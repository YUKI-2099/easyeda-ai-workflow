# -*- coding: utf-8 -*-
"""把 plan.json 里的注释写成原理图文本图元（注释三步的最后一步：v2_notes_dump → v2_notes_place → 本脚本）。

🔴 会执行项目目录里的 notes.py（从里面取 NOTES = {位号: 注释}），只在自己的项目里用，别对来路不明的项目目录跑。
   项目根由 project.json 定位；plan.json（v2_notes_place.py 生成）也在项目根。导入本模块不读文件，main() 里才读。

规矩（都是踩出来的）：
  · 按内容幂等 —— HTTP 500 只是桥接超时，EDA 那边照跑完，重跑必须不重复
  · 每次 /execute 一小批 —— 30s 硬超时
  · 文本图元不进网表 —— 冻结 SHA 不会因此作废，这是选它而不选器件属性的原因
  · 写完回读比对，不是打印"✓"就算完

用法: python tools/v2_notes.py [--undo]     （在项目目录里跑；--undo 删掉 notes.py 里那些注释文本）
"""
import json, io, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from project import load_project
import v2_rows

COLOR = '#8896A6'
FONT = 6
BATCH = 10

OPEN = ("await eda.dmt_EditorControl.openDocument('%s');"
        "await new Promise(r=>setTimeout(r,1500));")


def load_notes(root):
    """执行 <项目根>/notes.py，取它的 NOTES（{位号: 注释}）。会执行项目目录里的代码——只在自己的项目里用。"""
    if root not in sys.path:
        sys.path.insert(0, root)
    from notes import NOTES
    return NOTES


def read_existing(uuid):
    js = OPEN % uuid + ("const ts=await eda.sch_PrimitiveText.getAll();"
                        "return ts.map(t=>[t.getState_Content(),t.getState_X(),t.getState_Y()]);")
    return v2_rows.ex(js, timeout=200)


def main():
    root = load_project()['root']
    ALL_NOTES = sorted(set(load_notes(root).values()))
    plan = json.load(io.open(os.path.join(root, 'plan.json'), encoding='utf-8'))
    undo = '--undo' in sys.argv
    mine = set(ALL_NOTES)

    for pg, v in plan.items():
        uuid = v['uuid']
        cur = read_existing(uuid)
        if cur is None or (isinstance(cur, dict) and 'HTTP' in cur):
            print('🔴 %s 读取失败 %s' % (pg, cur)); sys.exit(1)
        have = {'%s|%s|%s' % (c[0], c[1], c[2]) for c in cur}

        if undo:
            js = OPEN % uuid + (
                "const ts=await eda.sch_PrimitiveText.getAll();"
                "const M=new Set(%s);"
                "const kill=ts.filter(t=>M.has(t.getState_Content())).map(t=>t.getState_PrimitiveId());"
                "if(kill.length)await eda.sch_PrimitiveText.delete(kill);"
                "return {killed:kill.length};" % json.dumps(ALL_NOTES, ensure_ascii=False))
            r = v2_rows.ex(js, timeout=200)
            print('%-14s 删除 %s' % (pg, r))
            continue

        todo = [t for t in v['texts']
                if '%s|%s|%s' % (t['t'], t['x'], t['y']) not in have]
        print('%-14s 计划 %d，已有 %d，本次写 %d'
              % (pg, len(v['texts']), len(v['texts']) - len(todo), len(todo)))
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            payload = json.dumps([[c['x'], c['y'], c['t']] for c in chunk], ensure_ascii=False)
            js = OPEN % uuid + (
                "const T=%s;"
                "const ts=await eda.sch_PrimitiveText.getAll();"
                "const have=new Set(ts.map(t=>t.getState_Content()+'|'+t.getState_X()+'|'+t.getState_Y()));"
                "let n=0;"
                "for(const [x,y,s] of T){if(have.has(s+'|'+x+'|'+y))continue;"
                "await eda.sch_PrimitiveText.create(x,y,s,0,'%s',null,%d,false,false,false);n++;}"
                "return {made:n};" % (payload, COLOR, FONT))
            r = v2_rows.ex(js, timeout=200)
            if isinstance(r, dict) and 'HTTP' in r:
                print('   ⚠️ %s 批 %d 返回 %s —— 桥接超时不等于没执行，等 6s 后按内容幂等重跑'
                      % (pg, i // BATCH, r))
                time.sleep(6)
                r = v2_rows.ex(js, timeout=200)
            print('   批 %-2d %s' % (i // BATCH, r))

    # ---- 写完回读核对 ----
    print('\n=== 回读核对 ===')
    bad = 0
    for pg, v in plan.items():
        cur = read_existing(v['uuid'])
        have = {'%s|%s|%s' % (c[0], c[1], c[2]) for c in cur}
        want = {'%s|%s|%s' % (t['t'], t['x'], t['y']) for t in v['texts']}
        miss = want - have
        if undo:
            left = want & have
            print('%-14s 残留 %d %s' % (pg, len(left), sorted(left)[:5]))
            bad += len(left)
        else:
            print('%s %-14s 图上 %d 条，缺 %d %s'
                  % ('✅' if not miss else '🔴', pg, len(have & want), len(miss),
                     sorted(miss)[:5] if miss else ''))
            bad += len(miss)
    print('\n' + ('✅ 全部到位' if bad == 0 else '🔴 有 %d 条没落盘' % bad))
    sys.exit(0 if bad == 0 else 1)


if __name__ == '__main__':
    main()
