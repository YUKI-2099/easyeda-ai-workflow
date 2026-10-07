# -*- coding: utf-8 -*-
"""按立创编号批量加件并接线。

用法: python tools/v2_add_parts.py <spec.json>
spec.json: {"page":"<页uuid>",
            "stub":20,                       # 可选，短桩长度，默认 10；要跟本页现有风格一致
            "parts":[{"des":"C1","lcsc":"C1591","x":300,"y":600,"value":"100nF",
                      "nets":{"1":"+3V3","2":"GND"}}, ...]}
  nets 的键是**新器件的引脚号**；不给的脚留空（会在结果里报出来）。
  只想放件不接线：nets 给 {}。

做法：查重(按位号) → 放件 → 位号/Value → 读引脚 → 两脚件竖放则转 90° →
      端点落 5 网格的短桩(不带网名) → 地用 Ground 标志、电源网用 Power 标志，其余用 netPort。
      电源网只认 project.json 的 power_nets（见 v2_rows.power_nets）。
护栏：改前后导出网表，**只允许 spec 里出现过的网发生变化，且只能是"新增本次器件的脚"**。
"""
import json, sys, os, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v2_rows, v2_refreeze

JS = '''await eda.dmt_EditorControl.openDocument('%(page)s');await new Promise(r=>setTimeout(r,1600));
const LIB='0819f05c4eef4c71ace90d822a990e87';const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const DES=%(des)s,LCSC=%(lcsc)s,NETS=%(nets)s,POS=%(pos)s,VAL=%(value)s,POWER=new Set(%(power)s),STUB=%(stub)s;
const all0=await eda.sch_PrimitiveComponent.getAll();
if(all0.some(c=>c.getState_Designator&&c.getState_Designator()===DES))return {SKIP:DES+' 已存在'};
const a=await eda.lib_Device.getByLcscIds([LCSC]);const d=Array.isArray(a)?a[0]:a;
if(!d)return {ABORT:'库件解析失败 '+LCSC};
const c=await eda.sch_PrimitiveComponent.create({libraryUuid:LIB,uuid:d.uuid},POS.x,POS.y,'',0,false,true,true);
await sleep(1100);
{const o=await eda.sch_PrimitiveComponent.get(c.primitiveId);const x=o.toAsync();x.setState_Designator(DES);await x.done();}
await sleep(800);
let LIBVAL='';
{const o=await eda.sch_PrimitiveComponent.get(c.primitiveId);const op=o.getState_OtherProperty()||{};LIBVAL=op.Value||'';
 if(!Object.keys(op).some(k=>/^(Footprint|Symbol|3D Model|Device)$/i.test(k))){
   // 库条目已给标准 Value 就不拿厂家型号覆盖（工具手册 #63 后 #56 说明：否则 DRC 报"元件的属性与供应商编号不匹配"）
   const v=VAL||(op.Value?'':(o.getState_ManufacturerId()||''));if(v&&v!==op.Value){const x=o.toAsync();x.setState_OtherProperty({...op,Value:v});await x.done();}}}
await sleep(600);
let pins=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(c.primitiveId);
if(pins.length===2&&pins[0].x===pins[1].x){const x=(await eda.sch_PrimitiveComponent.get(c.primitiveId)).toAsync();
  x.setState_Rotation(90);await x.done();await sleep(1600);
  pins=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(c.primitiveId);}
const g=v=>Math.round(v/5)*5;
const ROT=%(rot)s;const rotL=ROT.left,rotR=ROT.right;
const kind=n=>n==='GND'?'gnd':(POWER.has(n)?'pwr':'port');
const xs=pins.map(p=>p.x);const cx=(Math.min(...xs)+Math.max(...xs))/2;
const done=[],skip=[],err=[];
for(const p of pins){const net=NETS[String(p.pinNumber)];
  if(!net){skip.push(String(p.pinNumber)+'('+p.pinName+')');continue;}
  const left=p.x<=cx;let ex=left?g(p.x-STUB):g(p.x+STUB);if(ex===g(p.x))ex=left?ex-5:ex+5;
  const ey=g(p.y);const k=kind(net);const rot=(left?rotL:rotR)[k];
  const pts=(ey===p.y)?[p.x,p.y,ex,p.y]:[p.x,p.y,p.x,ey,ex,ey];
  try{await eda.sch_PrimitiveWire.create(pts);
    if(k==='gnd')await eda.sch_PrimitiveComponent.createNetFlag('Ground',net,ex,ey,rot,false);
    else if(k==='pwr')await eda.sch_PrimitiveComponent.createNetFlag('Power',net,ex,ey,rot,false);
    else await eda.sch_PrimitiveComponent.createNetPort('BI',net,ex,ey,rot,false);
    done.push([String(p.pinNumber),p.pinName,net]);}catch(e){err.push([String(p.pinNumber),e.message]);}}
return {pins:pins.map(p=>[String(p.pinNumber),p.pinName,p.x,p.y]),done,skip,err,libValue:LIBVAL};'''



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
        raise RuntimeError('网表导不出来')
    return v2_refreeze.parse(s)[1]


def main():
    spec = json.load(open(sys.argv[1], encoding='utf-8'))
    page = spec['page']
    # 导网表要求当前活动文档是原理图；PCB 当活动文档时 getNetlistFile 返回空
    v2_rows.ex("await eda.dmt_EditorControl.openDocument('%s');"
               "await new Promise(r=>setTimeout(r,1600));return {ok:1};" % page, timeout=200)
    before = netmap()
    POWER = sorted(v2_rows.power_nets())
    expect = {}
    for p in spec['parts']:
        for num, net in (p.get('nets') or {}).items():
            expect.setdefault(net, set()).add('%s-%s' % (p['des'], num))

    for p in spec['parts']:
        js = JS % dict(page=page, des=json.dumps(p['des']), lcsc=json.dumps(p['lcsc']),
                       nets=json.dumps(p.get('nets') or {}),
                       pos=json.dumps({'x': p['x'], 'y': p['y']}),
                       value=json.dumps(p.get('value')), power=json.dumps(POWER),
                       stub=int(spec.get('stub') or 10), rot=json.dumps(flag_rot(spec)))
        r = v2_rows.ex(js, timeout=220)
        if isinstance(r, dict) and r.get('HTTP'):
            print('  ⚠️ %s HTTP %s —— 等 6s 按位号幂等重跑' % (p['des'], r['HTTP']))
            time.sleep(6); r = v2_rows.ex(js, timeout=220)
        if not r or r.get('ABORT') or r.get('err'):
            print('🔴 %s 失败: %s' % (p['des'], json.dumps(r, ensure_ascii=False)[:300])); return 1
        if r.get('SKIP'):
            print('  – %s' % r['SKIP']); continue
        print('  ✅ %-5s %-10s 引脚%s 接线%s 未接%s'
              % (p['des'], p['lcsc'], [q[:2] for q in r['pins']],
                 [d[0] + '→' + d[2] for d in r['done']], r['skip'] or '无'))
        if p.get('value') and r.get('libValue') and p['value'] != r['libValue']:
            print('    ⚠️ Value 用了 spec 的 %r，库里的标准写法是 %r：DRC 会报器件标准化告警（手册 #56），'
                  '要保留这个写法就按 #56 加 Display Value' % (p['value'], r['libValue']))

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
            print('  🔴 %s 期望新增 %s，实得 %s' % (net, sorted(want), sorted(got))); bad += 1
    print('\n' + ('✅ 差异精确符合预期' if bad == 0 else '🔴 有意外差异'))
    if bad == 0:
        print('保存:', v2_rows.ex("const ok=await eda.sch_Document.save();return {saved:ok};", timeout=150))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
