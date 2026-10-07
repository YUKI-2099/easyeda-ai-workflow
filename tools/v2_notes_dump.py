# -*- coding: utf-8 -*-
"""把 project.json 里每一页的器件坐标/引脚包络/属性文字位置/导线段全部抓下来存 JSON（项目根 geom.json），
后面的注释排版全在本地算，不再反复打桥接。

注释三步：v2_notes_dump.py（抓几何）→ v2_notes_place.py（本地算落点，写 plan.json）→ v2_notes.py（落笔并回读）。
用法: python tools/v2_notes_dump.py     （在项目目录里跑）
"""
import json, io, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from project import load_project
import v2_rows, bridge

JS_PARTS = '''await eda.dmt_EditorControl.openDocument('%s');await new Promise(r=>setTimeout(r,1500));
const all=await eda.sch_PrimitiveComponent.getAll();
const out=[];
for(const c of all){
  if(c.getState_ComponentType()!=='part')continue;
  const pid=c.getState_PrimitiveId();
  const ps=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(pid);
  const atts=await eda.sch_PrimitiveAttribute.getAll(pid);
  const vis=atts.filter(a=>a.getState_ValueVisible&&a.getState_ValueVisible()&&a.getState_X&&a.getState_X()!=null)
                .map(a=>[a.getState_Key(),a.getState_X(),a.getState_Y()]);
  out.push({d:c.getState_Designator(),x:c.getState_X(),y:c.getState_Y(),
            rot:c.getState_Rotation(),
            pins:ps.map(p=>[p.x,p.y]),vis:vis});
}
return out;'''

JS_WIRES = '''const ws=await eda.sch_PrimitiveWire.getAll();
const ts=await eda.sch_PrimitiveText.getAll();
return {w:ws.map(w=>w.getState_Line()),
        t:ts.map(t=>[t.getState_Content(),t.getState_X(),t.getState_Y()])};'''

def main():
    P = load_project()
    out = os.path.join(P['root'], 'geom.json')     # 写到项目根，不写进工具目录
    if not P['pages']:
        print('🔴 project.json 的 pages 是空的：不知道要抓哪几页'); sys.exit(1)
    bridge.remember_doc()                 # #51：记住用户前台文档，退出时切回
    data = {}
    for name, uid in P['pages']:
        p = v2_rows.ex(JS_PARTS % uid, timeout=240)
        if not p or (isinstance(p, dict) and 'HTTP' in p):
            print('FAIL parts', name, p); sys.exit(1)
        w = v2_rows.ex(JS_WIRES, timeout=180)
        if not w or 'HTTP' in w:
            print('FAIL wires', name, w); sys.exit(1)
        data[name] = {'uuid': uid, 'parts': p, 'wires': w['w'], 'texts': w['t']}
        print('%-14s 件%3d 线%3d 已有文本%d' % (name, len(p), len(w['w']), len(w['t'])))

    io.open(out, 'w', encoding='utf-8').write(json.dumps(data, ensure_ascii=False))
    print('-> ', out)


if __name__ == '__main__':      # 🔴 模块导入时绝不打桥接（手册 §1.8）：2026-09-11 迁库回归时 import 即执行，误写了 geom.json
    main()
