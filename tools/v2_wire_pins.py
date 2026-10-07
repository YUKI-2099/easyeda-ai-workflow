# -*- coding: utf-8 -*-
"""给**已存在**器件的指定引脚接线（短桩 + 网络符号），自动处理 NC 标记。

用法: python tools/v2_wire_pins.py <spec.json>
spec.json: {"page":"<页uuid>",
            "stub":20,                       # 可选，短桩长度，默认 10；要跟本页现有风格一致
            "wires":[{"des":"U1","pin":"12","net":"NET_A"}, ...]}

做法（每根脚）：
  1. 若 getState_NoConnected() 为 true → toAsync().setState_NoConnected(false) → done() → 回读确认
     🔴 不清 NC 的话 sch_PrimitiveWire.create 只回一句 `create failed!`，不说原因（手册坑 ㊻）
  2. 按该脚在"引脚包络"上的位置判断朝外方向，画落 5 网格的短桩（**不传网名**）
  3. GND→Ground 标志，电源网（只认 project.json 的 power_nets）→Power 标志，其余→BI 网络端口

护栏：改前后导出网表，只允许 spec 里的网各自新增对应引脚，其余零差异。
"""
import json, sys, os, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v2_rows, v2_refreeze

JS = '''await eda.dmt_EditorControl.openDocument('%(page)s');await new Promise(r=>setTimeout(r,1700));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const JOBS=%(jobs)s, POWER=new Set(%(power)s), STUB=%(stub)s;
const all=await eda.sch_PrimitiveComponent.getAll();
const byDes={};for(const c of all){const d=c.getState_Designator&&c.getState_Designator();if(d)byDes[d]=c;}
const g=v=>Math.round(v/5)*5;
const rot=%(rot)s;
const kind=n=>n==='GND'?'gnd':(POWER.has(n)?'pwr':'port');
const out=[],err=[];
for(const j of JOBS){
  const c=byDes[j.des]; if(!c){err.push([j.des,'找不到器件']);continue;}
  const pid=c.getState_PrimitiveId();
  let ps=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(pid);
  const xs=ps.map(p=>p.x),ys=ps.map(p=>p.y);
  const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
  let p=ps.find(q=>String(q.pinNumber)===String(j.pin));
  if(!p){err.push([j.des+'-'+j.pin,'找不到引脚']);continue;}
  const wasNC=p.getState_NoConnected?p.getState_NoConnected():p.noConnected;
  if(wasNC){try{const a=p.toAsync();a.setState_NoConnected(false);await a.done();await sleep(1300);
      ps=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(pid);
      p=ps.find(q=>String(q.pinNumber)===String(j.pin));
      const still=p.getState_NoConnected?p.getState_NoConnected():p.noConnected;
      if(still){err.push([j.des+'-'+j.pin,'NC 清不掉']);continue;}
    }catch(e){err.push([j.des+'-'+j.pin,'清 NC 失败 '+e.message]);continue;}}
  let dir;
  if(Math.abs(p.x-x0)<0.5)dir='left'; else if(Math.abs(p.x-x1)<0.5)dir='right';
  else if(Math.abs(p.y-y0)<0.5)dir='down'; else if(Math.abs(p.y-y1)<0.5)dir='up';
  else {err.push([j.des+'-'+j.pin,'判不出朝外方向']);continue;}
  let ex=p.x,ey=p.y;
  if(dir==='left'){ex=g(p.x-STUB);if(ex===g(p.x))ex-=5;}
  else if(dir==='right'){ex=g(p.x+STUB);if(ex===g(p.x))ex+=5;}
  else if(dir==='down'){ey=g(p.y-STUB);if(ey===g(p.y))ey-=5;}
  else {ey=g(p.y+STUB);if(ey===g(p.y))ey+=5;}
  if(dir==='left'||dir==='right')ey=g(p.y); else ex=g(p.x);
  const pts=(dir==='left'||dir==='right')
      ? ((ey===p.y)?[p.x,p.y,ex,p.y]:[p.x,p.y,p.x,ey,ex,ey])
      : ((ex===p.x)?[p.x,p.y,p.x,ey]:[p.x,p.y,ex,p.y,ex,ey]);
  const k=kind(j.net), r=rot[dir][k];
  try{
    if(pts.length===4){await eda.sch_PrimitiveWire.create(pts);}
    else{await eda.sch_PrimitiveWire.create([pts[0],pts[1],pts[2],pts[3]]);
         await eda.sch_PrimitiveWire.create([pts[2],pts[3],pts[4],pts[5]]);}
    if(k==='gnd')await eda.sch_PrimitiveComponent.createNetFlag('Ground',j.net,ex,ey,r,false);
    else if(k==='pwr')await eda.sch_PrimitiveComponent.createNetFlag('Power',j.net,ex,ey,r,false);
    else await eda.sch_PrimitiveComponent.createNetPort('BI',j.net,ex,ey,r,false);
    out.push([j.des+'-'+j.pin,p.pinName,j.net,dir,wasNC?'清了NC':'']);
  }catch(e){err.push([j.des+'-'+j.pin,'画线失败 '+e.message]);}
}
return {out,err};'''



# 网络符号朝向：DEFAULT_FLAG_ROT 和 spec 的 flag_rot 都是 createNetFlag/createNetPort
# 的【传入值】，不是 getState_Rotation() 读回的【存储值】。原生 API 存储时会取反：
# storage = (360 - create_input) % 360。因此按本页现有符号反推时必须先换算：
# create_input = (360 - getState_Rotation()) % 360；严禁把读回值直接填进 flag_rot。
# 默认表沿用本仓库一贯的朝向；页面确有不同风格时才在 spec 中按上述传入值覆盖。
DEFAULT_FLAG_ROT = {'left':  {'port': 180, 'pwr': 270, 'gnd': 90},
                    'right': {'port': 0,   'pwr': 90,  'gnd': 270},
                    'up':    {'port': 90,  'pwr': 0,   'gnd': 180},
                    'down':  {'port': 270, 'pwr': 180, 'gnd': 0}}


def flag_rot(spec):
    r = {k: dict(v) for k, v in DEFAULT_FLAG_ROT.items()}
    for side, tbl in (spec.get('flag_rot') or {}).items():
        if side not in r:
            raise SystemExit('flag_rot 的键只能是 left/right/up/down，收到 %r' % side)
        for k, v in tbl.items():
            if k not in ('port', 'pwr', 'gnd'):
                raise SystemExit('flag_rot[%s] 的键只能是 port/pwr/gnd，收到 %r' % (side, k))
            r[side][k] = int(v)
    return r

def netmap():
    n = v2_rows.ex("const f=await eda.sch_ManufactureData.getNetlistFile('x','Protel2');"
                   "return {s:f?await f.text():''};", timeout=150)
    s = (n or {}).get('s', '')
    if not s:
        raise RuntimeError('网表导不出来（活动文档是不是 PCB？）')
    return v2_refreeze.parse(s)[1]


def main():
    spec = json.load(open(sys.argv[1], encoding='utf-8'))
    page, jobs = spec['page'], spec['wires']
    v2_rows.ex("await eda.dmt_EditorControl.openDocument('%s');"
               "await new Promise(r=>setTimeout(r,1600));return {ok:1};" % page, timeout=200)
    before = netmap()
    expect = {}
    for j in jobs:
        expect.setdefault(j['net'], set()).add('%s-%s' % (j['des'], j['pin']))

    POWER = sorted(v2_rows.power_nets())
    CH = 6
    for i in range(0, len(jobs), CH):
        chunk = jobs[i:i + CH]
        js = JS % dict(page=page, jobs=json.dumps(chunk), power=json.dumps(POWER),
                       stub=int(spec.get('stub') or 10), rot=json.dumps(flag_rot(spec)))
        r = v2_rows.ex(js, timeout=260)
        if isinstance(r, dict) and r.get('HTTP'):
            print('  ⚠️ 批 %d HTTP %s —— 等 8s 后只回读，不盲目重跑（画线不幂等）' % (i // CH, r['HTTP']))
            time.sleep(8); r = {'out': [], 'err': [['batch%d' % (i // CH), 'HTTP 超时，见回读']]}
        for o in (r.get('out') or []):
            print('  ✅ %-8s %-10s → %-10s (%s) %s' % tuple(o))
        for e in (r.get('err') or []):
            print('  🔴 %-10s %s' % tuple(e))

    time.sleep(2)
    after = netmap()
    print('\n=== 护栏：网络差异必须精确等于新增这些脚 ===')
    bad = 0
    for net in sorted(set(before) | set(after)):
        b, a = set(before.get(net, [])), set(after.get(net, []))
        if b == a:
            continue
        added, removed = a - b, b - a
        ok = (added == expect.get(net, set())) and not removed
        print('  %s %-12s 新增%s 删除%s' % ('✅' if ok else '🔴', net, sorted(added), sorted(removed) or '无'))
        if not ok:
            bad += 1
    for net, want in expect.items():
        got = set(after.get(net, [])) - set(before.get(net, []))
        if got != want:
            print('  🔴 %-12s 期望新增 %s，实得 %s' % (net, sorted(want), sorted(got))); bad += 1
    print('\n' + ('✅ 差异精确符合预期' if bad == 0 else '🔴 有意外差异'))
    if bad == 0:
        print('保存:', v2_rows.ex("const ok=await eda.sch_Document.save();return {saved:ok};", timeout=150))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
