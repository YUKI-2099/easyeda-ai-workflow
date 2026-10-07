"""清除原理图导线上的网络名，让网只由末端的 netFlag/netPort 定义（= 已验证旧板的画法）。

背景：`sch_PrimitiveWire.create(pts, net)` 传了 net，EasyEDA 会把网名画在导线上，
于是每个连接点的名字出现两次（导线一次 + 符号一次），图面文字翻倍。
实测一块已验证的旧板上百根导线全部无网名，连通性完全靠末端符号。

两个坑（2026-09-10 实证）：
  A. `setState_Net('')` **只对两点直线有效**；多段折线一律 `modify failed!`。
  B. 折线的 `getState_Line()` 返回的是**乱序的线段端点表**（每 4 个数一段），不是有序折线。
     → 折线的处理办法：读线段 → 删折线 → 按段重建为两点线（不带网名），几何完全一致。

安全性：改前后导出网表逐网比对，必须零差异，否则中止（不保存）。
用法: python tools/v2_strip_wire_nets.py     （逐页处理 project.json 的 pages；运行时才读配置）
"""
import sys, json, re, time
sys.path.insert(0, __file__.replace('\\', '/').rsplit('/', 1)[0])
import v2_rows

from project import load_project


def netmap():
    n = v2_rows.ex("const f=await eda.sch_ManufactureData.getNetlistFile('x','Protel2');return {s:f?await f.text():''};")
    s = (n or {}).get('s', '').replace('\r', '')
    d = {}
    for nm, b in re.findall(r'\(\n([^\n]+)\n((?:[^()]*\n)*?)\)', s[s.rfind(']') + 1:]):
        d[nm.strip()] = sorted(l.split()[0] for l in b.strip().split('\n') if l.strip())
    return d


PHASE_A = '''await eda.dmt_EditorControl.openDocument('%(uid)s');await new Promise(r=>setTimeout(r,1300));
const ws=await eda.sch_PrimitiveWire.getAll();
const todo=ws.filter(w=>{const n=w.getState_Net();return n&&n.length&&w.getState_Line().length===4;}).slice(0,%(n)d);
let done=0;const err=[];
for(const w of todo){try{const a=w.toAsync();a.setState_Net('');await a.done();done++;}catch(e){err.push(e.message);}}
await new Promise(r=>setTimeout(r,600));
const rest=(await eda.sch_PrimitiveWire.getAll()).filter(w=>{const n=w.getState_Net();return n&&n.length;});
return {done,left2:rest.filter(w=>w.getState_Line().length===4).length,leftPoly:rest.filter(w=>w.getState_Line().length>4).length,err:err.slice(0,2)};'''

PHASE_B = '''await eda.dmt_EditorControl.openDocument('%(uid)s');await new Promise(r=>setTimeout(r,1300));
const ws=await eda.sch_PrimitiveWire.getAll();
const todo=ws.filter(w=>{const n=w.getState_Net();return n&&n.length&&w.getState_Line().length>4;}).slice(0,%(n)d);
const segs=[],ids=[];
for(const w of todo){const L=w.getState_Line();ids.push(w.getState_PrimitiveId());
  for(let i=0;i+3<L.length;i+=4)segs.push([L[i],L[i+1],L[i+2],L[i+3]]);}
if(ids.length)await eda.sch_PrimitiveWire.delete(ids);
await new Promise(r=>setTimeout(r,700));
let made=0;const err=[];
for(const s of segs){try{await eda.sch_PrimitiveWire.create(s);made++;}catch(e){err.push(e.message);}}
await new Promise(r=>setTimeout(r,600));
const rest=(await eda.sch_PrimitiveWire.getAll()).filter(w=>{const n=w.getState_Net();return n&&n.length;});
return {polyRemoved:ids.length,segsMade:made,leftNamed:rest.length,err:err.slice(0,2)};'''


def loop(page_uid, js, n, label, rounds=15):
    for _ in range(rounds):
        t = time.time()
        r = v2_rows.ex(js % dict(uid=page_uid, n=n), timeout=180)
        if isinstance(r, dict) and r.get('HTTP'):
            print('    HTTP %s -> 等 6s 幂等重跑' % r['HTTP']); time.sleep(6); continue
        moved = r.get('done', 0) or r.get('polyRemoved', 0)
        print('    %s 本轮 %-3d  剩带名 %s%s' % (label, moved,
              r.get('leftNamed', '%s直/%s折' % (r.get('left2'), r.get('leftPoly'))),
              ('  ERR ' + str(r['err'])) if r.get('err') else ''))
        if moved == 0:
            return r
    return r


if __name__ == '__main__':
    PAGES = load_project()['pages']        # 来自项目根 project.json，不写死
    if not PAGES:
        print('🔴 project.json 的 pages 是空的：不知道要清哪几页'); sys.exit(1)
    before = netmap()
    print('改前 网数', len(before))
    for name, uid in PAGES:
        print('  %s' % name)
        loop(uid, PHASE_A, 40, 'A 两点线')
        loop(uid, PHASE_B, 12, 'B 折线重建')
    after = netmap()
    diff = [(k, before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)]
    if diff:
        print('🔴 网表比对 FAIL，未保存:', diff[:10]); sys.exit(1)
    print('✅ 网表零差异，连通性未变')
    for name, uid in PAGES:
        s = v2_rows.ex("await eda.dmt_EditorControl.openDocument('%s');await new Promise(r=>setTimeout(r,1200));return {saved:await eda.sch_Document.save()};" % uid)
        print('  保存', name, s)
