# -*- coding: utf-8 -*-
"""给满页里的空闲引脚接线：先算路径、**用线段相交判据排除**，再落笔。

🔴 为什么要这个（2026-09-11 连续两次把网短接的教训）：
EasyEDA 里**两根导线只要碰上就连通** —— 不限于端点对端点，**交叉也算**。
往一张排满扇出的页里横拉一条线，会把沿途所有竖线串成一个网。
我第一次查"端点是否和现有顶点重合"、第二次也只查端点，**都查错了对象**；
正确判据是 **新线段 vs 现有线段的相交（含仅仅接触）**。

用法: python tools/v2_route_pin.py <spec.json>
spec.json: {"page":"<页uuid>",
            "routes":[{"des":"U1","pin":"12","net":"NET_A"}, ...]}
每根脚自动搜索候选路径（直出短桩 / 出一段再折），取第一条与现有线段**零相交**的。
新画的线段会加入占用集，后续脚不会撞到前面刚画的。
"""
import json, sys, os, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v2_rows, v2_refreeze

EPS = 0.6


def seg_rect(s):
    return (min(s[0], s[2]) - EPS, min(s[1], s[3]) - EPS,
            max(s[0], s[2]) + EPS, max(s[1], s[3]) + EPS)


def rect_hit(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def seg_hit(s1, s2):
    """轴对齐线段：退化矩形相交 == 交叉/重叠/接触，正是我们要禁止的全部情况。"""
    return rect_hit(seg_rect(s1), seg_rect(s2))


READ = '''await eda.dmt_EditorControl.openDocument('%s');await new Promise(r=>setTimeout(r,1700));
const ws=await eda.sch_PrimitiveWire.getAll();const segs=[];
for(const w of ws){const L=w.getState_Line();
  for(let i=0;i+3<L.length;i+=4)segs.push([L[i],L[i+1],L[i+2],L[i+3]]);}
const all=await eda.sch_PrimitiveComponent.getAll();
const syms=all.filter(c=>['netflag','netport'].includes(c.getState_ComponentType()))
  .map(c=>[c.getState_X(),c.getState_Y(),c.getState_Rotation()]);
const pins={};const boxes=[];
for(const c of all.filter(c=>c.getState_ComponentType()==='part')){
  const d=c.getState_Designator&&c.getState_Designator();
  const ps=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(c.getState_PrimitiveId());
  if(!ps.length)continue;
  const xs=ps.map(p=>p.x),ys=ps.map(p=>p.y);
  boxes.push([Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)]);
  for(const p of ps)pins[d+'-'+p.pinNumber]=[p.x,p.y,p.pinName,
      (p.getState_NoConnected?p.getState_NoConnected():p.noConnected),
      Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)];}
return {segs,syms,pins,boxes};'''

MAKE = '''await eda.dmt_EditorControl.openDocument('%(page)s');await new Promise(r=>setTimeout(r,1500));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));const J=%(job)s;
const all=await eda.sch_PrimitiveComponent.getAll();
const c=all.find(q=>q.getState_Designator&&q.getState_Designator()===J.des);
const pid=c.getState_PrimitiveId();
let ps=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(pid);
let p=ps.find(q=>String(q.pinNumber)===J.pin);
const nc=p.getState_NoConnected?p.getState_NoConnected():p.noConnected;
if(nc){const a=p.toAsync();a.setState_NoConnected(false);await a.done();await sleep(1300);}
for(const s of J.segs){try{await eda.sch_PrimitiveWire.create(s);}catch(e){return {ABORT:'线段 '+JSON.stringify(s)+' '+e.message};}}
try{await eda.sch_PrimitiveComponent.createNetPort('BI',J.net,J.port[0],J.port[1],J.rot,false);}
catch(e){return {ABORT:'端口 '+e.message};}
return {ok:1,clearedNC:!!nc};'''


def sym_box(x, y, rot):
    if rot in (90, 270):
        return (x - 14, y - 78, x + 14, y + 78)
    return (x - 78, y - 14, x + 78, y + 14)


def plan(px, py, x0, y0, x1, y1, segs, symboxes, partboxes):
    """返回 (线段列表, 端口xy, 端口rot)；找不到返回 None。"""
    if abs(px - x0) < 0.5:
        out = 'left'
    elif abs(px - x1) < 0.5:
        out = 'right'
    elif abs(py - y0) < 0.5:
        out = 'down'
    else:
        out = 'up'
    D = {'left': (-1, 0), 'right': (1, 0), 'down': (0, -1), 'up': (0, 1)}[out]
    PR = {'left': 180, 'right': 0, 'down': 270, 'up': 90}[out]
    cands = []
    # ① 直出短桩
    for d in range(10, 90, 10):
        ex, ey = px + D[0] * d, py + D[1] * d
        cands.append(([[px, py, ex, ey]], (ex, ey), PR, d))
    # ② 出一小段再折（沿外侧走廊），两个折向都试
    for d1 in range(10, 50, 10):
        mx, my = px + D[0] * d1, py + D[1] * d1
        for sgn in (1, -1):
            perp = (0, sgn) if D[1] == 0 else (sgn, 0)
            for d2 in range(30, 220, 20):
                ex, ey = mx + perp[0] * d2, my + perp[1] * d2
                rot = (180 if perp[0] < 0 else 0) if perp[0] else (270 if perp[1] < 0 else 90)
                cands.append(([[px, py, mx, my], [mx, my, ex, ey]], (ex, ey), rot, d1 + d2))
    cands.sort(key=lambda c: c[3])
    for ss, port, rot, _ in cands:
        ss = [s for s in ss if not (s[0] == s[2] and s[1] == s[3])]
        if not ss:
            continue
        if any(seg_hit(s, e) for s in ss for e in segs):
            continue
        pb = sym_box(port[0], port[1], rot)
        if any(rect_hit(pb, b) for b in symboxes):
            continue
        if any(rect_hit(pb, (b[0] - 6, b[1] - 6, b[2] + 6, b[3] + 6)) for b in partboxes):
            continue
        return ss, port, rot
    return None


def netmap():
    n = v2_rows.ex("const f=await eda.sch_ManufactureData.getNetlistFile('x','Protel2');"
                   "return {s:f?await f.text():''};", timeout=200)
    s = (n or {}).get('s', '')
    if not s:
        raise RuntimeError('网表导不出来')
    return v2_refreeze.parse(s)[1]


def main():
    spec = json.load(open(sys.argv[1], encoding='utf-8'))
    page, routes = spec['page'], spec['routes']
    info = v2_rows.ex(READ % page, timeout=300)
    if not info or 'HTTP' in info:
        print('读页失败', info); return 1
    segs = [list(map(float, s)) for s in info['segs']]
    symboxes = [sym_box(*s) for s in info['syms']]
    partboxes = [list(map(float, b)) for b in info['boxes']]
    pins = info['pins']
    print('现有线段 %d、符号 %d、器件 %d' % (len(segs), len(symboxes), len(partboxes)))

    plans = []
    for r in routes:
        key = '%s-%s' % (r['des'], r['pin'])
        if key not in pins:
            print('  🔴 %s 找不到' % key); return 1
        px, py, pname, nc, x0, y0, x1, y1 = pins[key]
        got = plan(px, py, x0, y0, x1, y1, segs, symboxes, partboxes)
        if not got:
            print('  🔴 %s (%s) 找不到零相交路径' % (key, pname)); return 1
        ss, port, rot = got
        plans.append(dict(des=r['des'], pin=r['pin'], net=r['net'], segs=ss,
                          port=list(port), rot=rot, name=pname))
        segs.extend(ss)
        symboxes.append(sym_box(port[0], port[1], rot))
        print('  规划 %-7s %-8s → %-8s 段数%d 端口(%.0f,%.0f) rot%d'
              % (key, pname, r['net'], len(ss), port[0], port[1], rot))

    before = netmap()
    expect = {}
    for pl in plans:
        expect.setdefault(pl['net'], set()).add('%s-%s' % (pl['des'], pl['pin']))

    print('\n落笔:')
    for pl in plans:
        rr = v2_rows.ex(MAKE % dict(page=page, job=json.dumps(pl)), timeout=240)
        if isinstance(rr, dict) and rr.get('HTTP'):
            print('  ⚠️ %s-%s HTTP 超时，只回读' % (pl['des'], pl['pin'])); time.sleep(8); continue
        if not rr or rr.get('ABORT'):
            print('  🔴 %s-%s %s' % (pl['des'], pl['pin'], json.dumps(rr, ensure_ascii=False)[:200])); continue
        print('  ✅ %s-%s %s' % (pl['des'], pl['pin'], '清了NC' if rr['clearedNC'] else ''))

    time.sleep(2)
    after = netmap()
    print('\n=== 护栏 ===')
    bad = 0
    for net in sorted(set(before) | set(after)):
        b, a = set(before.get(net, [])), set(after.get(net, []))
        if b == a:
            continue
        added, removed = a - b, b - a
        good = (added == expect.get(net, set())) and not removed
        print('  %s %-10s 新增%s 删除%s' % ('✅' if good else '🔴', net, sorted(added), sorted(removed) or '无'))
        if not good:
            bad += 1
    for net, want in expect.items():
        got = set(after.get(net, [])) - set(before.get(net, []))
        if got != want:
            print('  🔴 %-10s 期望 %s 实得 %s' % (net, sorted(want), sorted(got))); bad += 1
    print('\n' + ('✅ 差异精确符合预期' if bad == 0 else '🔴 有意外差异'))
    if bad == 0:
        print('保存:', v2_rows.ex("const ok=await eda.sch_Document.save();return {saved:ok};", timeout=180))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
