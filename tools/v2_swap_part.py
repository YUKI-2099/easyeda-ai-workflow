"""换件：原地把某个位号换成另一个立创料号，并按新引脚重画短桩与网络符号。

用法: python tools/v2_swap_part.py <swap.json>
swap.json: {"page":"<页uuid>",
            "stub":20,                       # 可选，短桩长度，默认 10；要跟本页现有风格一致
            "swaps":[{"des":"D1","lcsc":"C132591","nets":{"1":"VBUS","2":"GND"},"value":"SMF5.0A"}, ...]}
  nets 的键是**新器件的引脚号**；只给要接的脚，其余脚留空会被报出来。
  想只删不换：给 "lcsc": null，并可给 "mergeInto" 说明该位号删除后由谁承接（仅记录用）。

做法：删旧件 → 删挂在它引脚上的短桩 → 删这些短桩另一端的网络符号 → 原坐标放新件 →
      设位号/Value → 读新引脚 → 两侧短桩(端点落 5 网格，不带网名) → 网络符号
      （地用 Ground 标志，project.json 的 power_nets 用 Power 标志，其余用网络端口）。
护栏：活动页断言；改前后导出网表，只允许**本次涉及的网**发生变化，其余网零差异，否则报警。
"""
import json, sys, io, time, re
sys.path.insert(0, __file__.replace('\\', '/').rsplit('/', 1)[0])
import v2_rows

JS_DEL = '''await eda.dmt_EditorControl.openDocument('%(page)s');await new Promise(r=>setTimeout(r,1300));
const DES=%(des)s;const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const all=await eda.sch_PrimitiveComponent.getAll();
const c=all.find(x=>x.getState_Designator&&x.getState_Designator()===DES);
if(!c)return {ABORT:'找不到 '+DES};
const pos={x:c.getState_X(),y:c.getState_Y(),rot:c.getState_Rotation()};
const ps=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(c.getState_PrimitiveId());
const R=v=>Math.round(v*100)/100;   // 坐标带浮点噪声(799.9999999999998)，一律 round 2 位再比，否则符号漏删(2026-09-26 实证：漏删的成了浮空符号)
const K=(x,y)=>R(x)+','+R(y);
const pinKeys=new Set(ps.map(p=>K(p.x,p.y)));
const ws=await eda.sch_PrimitiveWire.getAll();
const killW=[],ends=[];
for(const w of ws){const L=w.getState_Line();const pts=[];for(let i=0;i<L.length;i+=2)pts.push([L[i],L[i+1]]);
  if(pts.some(p=>pinKeys.has(K(p[0],p[1])))){killW.push(w.getState_PrimitiveId());
    for(const p of pts)if(!pinKeys.has(K(p[0],p[1])))ends.push(K(p[0],p[1]));}}
const killS=all.filter(s=>['netflag','netport'].includes(s.getState_ComponentType())&&ends.includes(K(s.getState_X(),s.getState_Y())));
const killedNets=killS.map(s=>s.getState_Net());
if(killW.length)await eda.sch_PrimitiveWire.delete(killW);
if(killS.length)await eda.sch_PrimitiveComponent.delete(killS.map(s=>s.getState_PrimitiveId()));
await eda.sch_PrimitiveComponent.delete([c.getState_PrimitiveId()]);
await sleep(900);
return {pos,oldPins:ps.length,killedWires:killW.length,killedSyms:killS.length,killedNets};'''

JS_ADD = '''await eda.dmt_EditorControl.openDocument('%(page)s');await new Promise(r=>setTimeout(r,1200));
const LIB='0819f05c4eef4c71ace90d822a990e87';const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const DES=%(des)s,LCSC=%(lcsc)s,NETS=%(nets)s,POS=%(pos)s,VAL=%(value)s,POWER=new Set(%(power)s),STUB=%(stub)s;
const a=await eda.lib_Device.getByLcscIds([LCSC]);const d=Array.isArray(a)?a[0]:a;
if(!d)return {ABORT:'库件解析失败 '+LCSC};
const c=await eda.sch_PrimitiveComponent.create({libraryUuid:LIB,uuid:d.uuid},POS.x,POS.y,'',0,false,true,true);
await sleep(1100);
const o=await eda.sch_PrimitiveComponent.get(c.primitiveId);
{const x=o.toAsync();x.setState_Designator(DES);await x.done();}
await sleep(800);
let LIBVAL='';
{const o2=await eda.sch_PrimitiveComponent.get(c.primitiveId);const op=o2.getState_OtherProperty()||{};LIBVAL=op.Value||'';
 if(!Object.keys(op).some(k=>/^(Footprint|Symbol|3D Model|Device)$/i.test(k))){
   // 库条目已给标准 Value 就不拿厂家型号覆盖（工具手册 #63 后 #56 说明：否则 DRC 报"元件的属性与供应商编号不匹配"）
   const v=VAL||(op.Value?'':(o2.getState_ManufacturerId()||''));if(v&&v!==op.Value){const x=o2.toAsync();x.setState_OtherProperty({...op,Value:v});await x.done();}}}
await sleep(600);
let pins=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(c.primitiveId);
if(pins.length===2&&pins[0].x===pins[1].x){const x=(await eda.sch_PrimitiveComponent.get(c.primitiveId)).toAsync();
  x.setState_Rotation(90);await x.done();await sleep(1600);
  pins=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(c.primitiveId);}
const g=v=>Math.round(v/5)*5;
const ROT=%(rot)s;const rotL=ROT.left,rotR=ROT.right;
const kind=n=>n==='GND'?'gnd':(POWER.has(n)?'pwr':'port');
const sorted=[...pins].sort((p,q)=>p.x-q.x);const cx=(sorted[0].x+sorted[sorted.length-1].x)/2;
const done=[],skip=[],err=[];
for(const p of pins){const net=NETS[String(p.pinNumber)];
  if(!net){skip.push(String(p.pinNumber));continue;}
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
    """导网表并解析成 {网名: [成员]}。

    🔴 解析一律走 v2_refreeze.parse()（逐行线性扫描）。
    原来这里内联了一份块正则，和 v2_refreeze 里那份是同一个坑：
    网表长到一百多 KB 后灾难性回溯，跑十分钟不出结果、看起来像桥接卡死。
    """
    import v2_refreeze
    n = v2_rows.ex("const f=await eda.sch_ManufactureData.getNetlistFile('x','Protel2');"
                   "return {s:f?await f.text():''};", timeout=150)
    s = (n or {}).get('s', '')
    if not s:
        raise RuntimeError('网表导不出来')
    return v2_refreeze.parse(s)[1]


def run(spec):
    page = spec['page']
    power = json.dumps(sorted(v2_rows.power_nets()))   # 电源网只认 project.json 的 power_nets
    before = netmap()
    touched = set()
    for sw in spec['swaps']:
        des = sw['des']
        r = v2_rows.ex(JS_DEL % dict(page=page, des=json.dumps(des)), timeout=180)
        if isinstance(r, dict) and r.get('HTTP'):
            time.sleep(6); r = v2_rows.ex(JS_DEL % dict(page=page, des=json.dumps(des)), timeout=180)
        if not r or r.get('ABORT'):
            print('🔴 删除失败', des, r); return False
        print('  删 %-5s 线%d 符号%d 原网 %s @(%s,%s)' % (des, r['killedWires'], r['killedSyms'],
              ','.join(r['killedNets']), r['pos']['x'], r['pos']['y']))
        # 预期受影响的网 = ① 被删符号上的网 ② spec 里要接的网
        #   ③ 🔴 改前网表里**含这个位号任何一脚**的所有网。
        # 少了 ③ 会误报：2026-09-14 换一颗多脚芯片时，有 4 根脚的导线另一端不是
        # 自己的私有符号（共用线段），这些网没进 killedNets，于是这 4 个网
        # 被判成「意外变化」，害得 run() 返回 False、**没保存**。它们其实只是各自少了旧件的一只脚。
        touched |= set(r['killedNets']) | set((sw.get('nets') or {}).values())
        # 被删件自己各脚所在的网也是"本次涉及的网"：短桩另一端连的是导线而不是符号时，killedNets 里没有它，
        # 护栏会把"只是少了这颗件的脚"误报成意外变化（2026-09-26 删一个板间接口座时实证）
        touched |= {n for n, mem in before.items() if any(m.rsplit('-', 1)[0] == des for m in mem)}
        if not sw.get('lcsc'):
            print('    （只删不换）'); continue
        a = v2_rows.ex(JS_ADD % dict(page=page, des=json.dumps(des), lcsc=json.dumps(sw['lcsc']),
                                     nets=json.dumps(sw.get('nets') or {}), pos=json.dumps(r['pos']),
                                     value=json.dumps(sw.get('value')), power=power,
                                     stub=int(spec.get('stub') or 10),
                                     rot=json.dumps(flag_rot(spec))), timeout=180)
        if isinstance(a, dict) and a.get('HTTP'):
            time.sleep(6); print('    HTTP 500，请手工复核'); return False
        if not a or a.get('ABORT') or a.get('err'):
            print('🔴 放件失败', des, a); return False
        print('    换 %-5s %s  引脚%s  接线%s  未接%s' % (des, sw['lcsc'],
              [p[:2] for p in a['pins']], [d[0] + '(' + str(d[1]) + ')→' + d[2] for d in a['done']], a['skip']))
        if sw.get('value') and a.get('libValue') and sw['value'] != a['libValue']:
            print('    ⚠️ Value 用了 spec 的 %r，库里的标准写法是 %r：DRC 会报器件标准化告警（手册 #56），'
                  '要保留这个写法就按 #56 加 Display Value' % (sw['value'], a['libValue']))
    after = netmap()
    diff = {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)}
    stray = {k: v for k, v in diff.items() if k not in touched}
    print('\n受影响网(预期):', sorted(touched & set(diff)))
    print('意外变化的网 :', stray if stray else '✅ 无')
    auto = [k for k in after if k.startswith('$')]
    single = [k for k, m in after.items() if len(m) == 1]
    print('自动网:', auto or '✅ 无', ' 单脚网:', single or '✅ 无')
    return not stray and not auto


if __name__ == '__main__':
    ok = run(json.load(io.open(sys.argv[1], encoding='utf-8')))
    if ok:
        for uid in {json.load(io.open(sys.argv[1], encoding='utf-8'))['page']}:
            print('保存:', v2_rows.ex("return {saved:await eda.sch_Document.save()};"))
    sys.exit(0 if ok else 1)
