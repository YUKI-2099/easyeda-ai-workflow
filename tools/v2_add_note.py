# -*- coding: utf-8 -*-
"""给指定器件加一条注释文本，落点自动避开已有东西。

用法: python tools/v2_add_note.py <spec.json>
spec.json: {"page":"<页uuid>","notes":{"C1":"U1 电源脚就近去耦", ...}}

占位物同 v2_free_spot：引脚包络、属性文字、网络符号、已有注释、导线。
候选顺序：器件右侧同行 → 左侧同行 → 下方 → 上方 → 以器件为中心扩圈扫。
按内容+坐标幂等；文本图元不进网表，冻结 SHA 不受影响（手册坑 ㊸）。
"""
import json, sys, os, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v2_rows, v2_free_spot as FS

FONT = 6.0


def tw(s):
    return sum(FONT if ord(c) > 0x2E80 else FONT * 0.56 for c in s)


def main():
    spec = json.load(open(sys.argv[1], encoding='utf-8'))
    page, notes = spec['page'], spec['notes']
    r = v2_rows.ex(FS.JS % page, timeout=300)
    if not r or 'HTTP' in r:
        print('读页失败', r); return 1
    boxes = [list(map(float, b[:4])) + [b[4]] for b in r['boxes']]
    occ = [b[:4] for b in boxes]

    plans = []
    for des, txt in notes.items():
        pb = [b for b in boxes if b[4] == 'part:' + des]
        if not pb:
            print('  🔴 找不到 %s' % des); return 1
        x0, y0, x1, y1 = pb[0][:4]
        w, h = tw(txt), FONT * 1.25
        cy = (y0 + y1) / 2.0
        cx = (x0 + x1) / 2.0 - w / 2
        cands = [(x1 + 90, cy), (x1 + 120, cy), (x0 - 90 - w, cy),
                 (cx, y0 - 30), (cx, y1 + 26), (x1 + 160, cy)]
        for dy in (0, -30, 26, -50, 46, -70, 66):
            for dx in range(90, 320, 20):
                cands.append((x1 + dx, cy + dy))
                cands.append((x0 - dx - w, cy + dy))
        got = None
        for tx, ty in cands:
            rect = (tx - 3, ty - h / 2 - 2, tx + w + 3, ty + h / 2 + 2)
            if any(FS.overlap(rect, o, 3.0) for o in occ):
                continue
            got = (round(tx), round(ty), rect); break
        if not got:
            print('  🔴 %s 找不到空位' % des); return 1
        tx, ty, rect = got
        occ.append(rect)
        plans.append((des, txt, tx, ty))
        print('  规划 %-5s (%5d,%5d)  %s' % (des, tx, ty, txt))

    payload = json.dumps([[p[2], p[3], p[1]] for p in plans], ensure_ascii=False)
    js = ("await eda.dmt_EditorControl.openDocument('%s');await new Promise(r=>setTimeout(r,1600));"
          "const T=%s;const ts=await eda.sch_PrimitiveText.getAll();"
          "const have=new Set(ts.map(t=>t.getState_Content()+'|'+t.getState_X()+'|'+t.getState_Y()));"
          "let n=0;for(const [x,y,s] of T){if(have.has(s+'|'+x+'|'+y))continue;"
          "await eda.sch_PrimitiveText.create(x,y,s,0,'#8896A6',null,6,false,false,false);n++;}"
          "const ok=await eda.sch_Document.save();return {made:n,saved:ok};" % (page, payload))
    res = v2_rows.ex(js, timeout=240)
    if isinstance(res, dict) and res.get('HTTP'):
        time.sleep(6); res = v2_rows.ex(js, timeout=240)
    print('\n落笔:', res)

    chk = v2_rows.ex("const ts=await eda.sch_PrimitiveText.getAll();"
                     "return ts.map(t=>t.getState_Content()+'|'+t.getState_X()+'|'+t.getState_Y());",
                     timeout=200)
    have = set(chk or [])
    miss = [p[0] for p in plans if '%s|%s|%s' % (p[1], p[2], p[3]) not in have]
    print('回读: 缺 %d %s' % (len(miss), miss or ''))
    return 0 if not miss else 1


if __name__ == '__main__':
    sys.exit(main())
