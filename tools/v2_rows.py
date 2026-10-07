"""行式建图 helper(easyeda-api 桥接版, 2026-09-10)
用法: python tools/v2_rows.py <rows.json> [--no-verify]
rows.json: {"page":"<页uuid>","lib":"<libraryUuid>","skipWire":["R1"],
            "rows":[{"des":"R2","uuid":"<deviceUuid>","x":300,"y":600,"nets":{"1":"NET_A","2":"GND"},"chip":false}, ...]}
两脚件: 放件→settle→位号→读脚→竖脚转90→两侧非零短桩(10)→端口/标志 ; chip:true 只放件+位号并返回引脚表
规矩: 🔴 导线一律**不传网名**(传了 EDA 会把名字画在线上,和符号的名字重复,图面文字翻倍;已验证的旧板上百根线全不带网名)
      🔴 短桩端点必须落 5 网格(坑⑯ 符号会吸附到 5 网格,端点不在格上就和符号错开 1~2 单位 → 静默断路)
      🔴 旋转后要 sleep 1600ms 再回读引脚(坑⑨ 慢一拍,500ms 会拿到旋转前坐标 → 线画在空处)
      活动页断言 / 坐标 5 倍数且在可用区 / 按位号幂等 / 每次 /execute ≤6 行(30s 硬超时) /
      HTTP 500 = 桥接超时但 EDA 仍在执行 → 等 6s 按位号幂等重跑 / 校验 = 网表与 DRC 分两次调用
电源网(用 Power 标志而不是网络端口的网)只来自 project.json 的 power_nets;没有 project.json 时用 tools/project.py 的默认表。
"""
import json, sys, io, os, time, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge import ex                    # 桥接层：127.0.0.1、保存错误正文（坑㊺ ㊾）
from project import DEFAULTS, load_project

CHUNK = 6


def power_nets():
    """哪些网用 Power 标志：project.json 的 power_nets（没有 project.json 就用默认表）。用到时才读配置。"""
    cfg = load_project(required=False)
    return set((cfg or DEFAULTS)['power_nets'])


def __getattr__(name):                   # 兼容旧写法 v2_rows.POWER：导入时不读配置，取值时才读
    if name == 'POWER':
        return power_nets()
    raise AttributeError('module %r has no attribute %r' % (__name__, name))


JS = r'''
const PAGE=%(page)s, LIB=%(lib)s, ROWS=%(rows)s, POWER=new Set(%(power)s), SKIPW=new Set(%(skipw)s);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const doc=await eda.dmt_SelectControl.getCurrentDocumentInfo();
if(!doc||doc.uuid!==PAGE)return {ABORT:'active doc '+(doc&&doc.uuid)};
const byDes=async()=>{const m={};for(const c of await eda.sch_PrimitiveComponent.getAll()){const d=c.getState_Designator&&c.getState_Designator();if(d)m[d]=c.getState_PrimitiveId();}return m;};
let have=await byDes(); const made=[],skipped=[],err=[];
for(const r of ROWS){ if(have[r.des]){skipped.push(r.des);continue;}
  try{const c=await eda.sch_PrimitiveComponent.create({libraryUuid:LIB,uuid:r.uuid},r.x,r.y,'',0,false,true,true);made.push([r.des,c.primitiveId]);}catch(e){err.push([r.des,'create '+e.message]);} }
if(made.length){await sleep(900);
for(const [d,pid] of made){try{const o=await eda.sch_PrimitiveComponent.get(pid);const a=o.toAsync();a.setState_Designator(d);await a.done();}catch(e){err.push([d,'des '+e.message]);}}
await sleep(700); have=await byDes();
for(const [d,pid] of made){try{const o=await eda.sch_PrimitiveComponent.get(pid);const op=o.getState_OtherProperty()||{};
  if(!op.Value&&!Object.keys(op).some(k=>/^(Footprint|Symbol|3D Model|Device)$/i.test(k))){const v=o.getState_ManufacturerId()||'';if(v){const a=o.toAsync();a.setState_OtherProperty({...op,Value:v});await a.done();}}
 }catch(e){err.push([d,'value '+e.message]);}}
}
const rotL={port:180,pwr:270,gnd:90}, rotR={port:0,pwr:90,gnd:270};
const kind=n=>n==='GND'?'gnd':(POWER.has(n)?'pwr':'port');
const wired=[],chips={},rotated=[];
for(const r of ROWS){ const pid=have[r.des]; if(!pid){err.push([r.des,'no pid']);continue;}
  let pins=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(pid);
  if(r.chip||pins.length!==2){chips[r.des]=pins.map(p=>[p.pinNumber,p.pinName,p.x,p.y]);continue;}
  if(skipped.includes(r.des)){wired.push(r.des+'(exist)');continue;}
  if(pins[0].x===pins[1].x){const o=await eda.sch_PrimitiveComponent.get(pid);const a=o.toAsync();a.setState_Rotation(90);await a.done();await sleep(1600);pins=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(pid);rotated.push(r.des);}
  if(SKIPW.has(r.des)){wired.push(r.des+'(skip)');continue;}
  pins.sort((a,b)=>a.x-b.x);
  for(let i=0;i<2;i++){const p=pins[i];const net=r.nets[String(p.pinNumber)];if(!net){err.push([r.des,'pin '+p.pinNumber+' no net']);continue;}
    const left=(i===0);const g=v=>Math.round(v/5)*5;
    let ex=left?g(p.x-10):g(p.x+10); if(ex===g(p.x))ex=left?ex-5:ex+5;
    const ey=g(p.y);
    if(ey!==p.y){err.push([r.des,'pin '+p.pinNumber+' y='+p.y+' 不在5网格,短桩改走折线']);}
    const k=kind(net);const rot=(left?rotL:rotR)[k];
    const pts=(ey===p.y)?[p.x,p.y,ex,p.y]:[p.x,p.y,p.x,ey,ex,ey];
    try{await eda.sch_PrimitiveWire.create(pts);
      if(k==='gnd')await eda.sch_PrimitiveComponent.createNetFlag('Ground',net,ex,ey,rot,false);
      else if(k==='pwr')await eda.sch_PrimitiveComponent.createNetFlag('Power',net,ex,ey,rot,false);
      else await eda.sch_PrimitiveComponent.createNetPort('BI',net,ex,ey,rot,false);
    }catch(e){err.push([r.des,'wire '+e.message]);} }
  wired.push(r.des); }
return {made:made.length,skipped,rotated,wired,chips,err};
'''


def run(spec):
    rows = spec['rows']
    for r in rows:
        x, y = r['x'], r['y']
        assert x % 5 == 0 and y % 5 == 0 and 60 <= x <= 1900 and 60 <= y <= 1120 and not (x > 1200 and y < 220), (r['des'], x, y)
    allchips = {}
    ok = True
    power = json.dumps(sorted(power_nets()))
    for i in range(0, len(rows), CHUNK):
        part = rows[i:i + CHUNK]
        code = JS % dict(page=json.dumps(spec['page']), lib=json.dumps(spec['lib']), rows=json.dumps(part),
                         power=power, skipw=json.dumps(spec.get('skipWire', [])))
        t = time.time()
        r = ex(code)
        if isinstance(r, dict) and r.get('HTTP'):
            print(f'  块{i // CHUNK + 1}: HTTP {r["HTTP"]} -> 桥接超时, 等 6s 后按位号幂等重跑')
            time.sleep(6)
            r = ex(code)
        summary = {k: v for k, v in (r or {}).items() if k != 'chips'}
        print(f'  块{i // CHUNK + 1}: {json.dumps(summary, ensure_ascii=False)} ({time.time() - t:.1f}s)')
        if not r or r.get('ABORT') or r.get('err'):
            ok = False
            break
        allchips.update(r.get('chips') or {})
    return ok, allchips


def verify():
    n = ex("const f=await eda.sch_ManufactureData.getNetlistFile('x','Protel2');if(!f)return {nets:'undefined'};"
           "const s=await f.text();const i=s.lastIndexOf(']');return {nets:s.slice(i+1).replace(/\\r/g,'').trim()};")
    d = ex("return {drc:JSON.stringify(await eda.sch_Drc.check(true,false,true)).slice(0,400)};")
    nets = {}
    for name, body in re.findall(r'\(\n([^\n]+)\n((?:[^()]*\n)*?)\)', (n or {}).get('nets', '') or ''):
        nets[name.strip()] = [l.split()[0] for l in body.strip().split('\n') if l.strip()]
    return nets, (d or {}).get('drc')


if __name__ == '__main__':
    spec = json.load(io.open(sys.argv[1], encoding='utf-8'))
    ok, chips = run(spec)
    if chips:
        print('[chips 引脚表]')
        for d, p in chips.items():
            print('  ', d, p)
    if not ok:
        sys.exit(1)
    if '--no-verify' not in sys.argv:
        nets, drc = verify()
        print('[drc]', drc)
        want = {}
        for r in spec['rows']:
            for pn, net in (r.get('nets') or {}).items():
                want.setdefault(net, set()).add(f"{r['des']}-{pn}")
        bad = [(net, sorted(m - set(nets.get(net, [])))) for net, m in want.items() if not m <= set(nets.get(net, []))]
        print('[verify]', 'PASS 规格里的全部引脚都在目标网' if not bad else f'FAIL {bad}')
        if bad:
            sys.exit(1)
