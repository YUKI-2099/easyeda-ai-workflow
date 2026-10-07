# -*- coding: utf-8 -*-
"""在某一页上找**真正空**的落点，用于放新器件。

为什么单独做这个（2026-09-11 用户反馈"摆得太靠近别的元件"）：
只按引脚包络找空位是不够的，一个器件在图上实际占的地方还包括
  · 位号/值两行属性文字
  · 短桩 + 网络端口/标志符号（端口文字会向外伸出几十单位）
  · **注释文本图元**（给每个器件都加了注释的图上，每个器件旁都有一条）
少算任何一项都会挤到邻居。

用法: python tools/v2_free_spot.py <页uuid> <要几个> [列宽w] [行高h] [间距clear]
输出若干 (x,y) 候选，按"离已有东西最远"排序。
"""
import sys, os, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v2_rows

JS = '''await eda.dmt_EditorControl.openDocument('%s');await new Promise(r=>setTimeout(r,1700));
const all=await eda.sch_PrimitiveComponent.getAll();
const parts=all.filter(c=>c.getState_ComponentType()==='part');
const boxes=[];
for(const c of parts){
  const ps=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(c.getState_PrimitiveId());
  if(!ps.length)continue;
  const xs=ps.map(p=>p.x),ys=ps.map(p=>p.y);
  boxes.push([Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys),'part:'+c.getState_Designator()]);
  const atts=await eda.sch_PrimitiveAttribute.getAll(c.getState_PrimitiveId());
  for(const a of atts){ if(a.getState_ValueVisible&&a.getState_ValueVisible()&&a.getState_X&&a.getState_X()!=null)
    boxes.push([a.getState_X()-45,a.getState_Y()-9,a.getState_X()+45,a.getState_Y()+9,'attr']); }
}
for(const s of all.filter(c=>['netflag','netport'].includes(c.getState_ComponentType())))
  boxes.push([s.getState_X()-75,s.getState_Y()-13,s.getState_X()+75,s.getState_Y()+13,'sym:'+s.getState_Net()]);
for(const t of await eda.sch_PrimitiveText.getAll()){
  const s=t.getState_Content()||'';const w=s.length*6+10;
  boxes.push([t.getState_X()-4,t.getState_Y()-9,t.getState_X()+w,t.getState_Y()+9,'text']); }
for(const w of await eda.sch_PrimitiveWire.getAll()){
  const L=w.getState_Line();
  for(let i=0;i<L.length-2;i+=4){
    boxes.push([Math.min(L[i],L[i+2])-3,Math.min(L[i+1],L[i+3])-3,
                Math.max(L[i],L[i+2])+3,Math.max(L[i+1],L[i+3])+3,'wire']); } }
let mnx=9e9,mny=9e9,mxx=-9e9,mxy=-9e9;
for(const b of boxes){mnx=Math.min(mnx,b[0]);mny=Math.min(mny,b[1]);mxx=Math.max(mxx,b[2]);mxy=Math.max(mxy,b[3]);}
return {boxes,bounds:[mnx,mny,mxx,mxy]};'''


def overlap(a, b, pad):
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def find(page, n, w=120, h=40, clear=30):
    r = v2_rows.ex(JS % page, timeout=280)
    if not r or 'HTTP' in r:
        raise RuntimeError('读页面失败 %s' % r)
    boxes, (mnx, mny, mxx, mxy) = r['boxes'], r['bounds']
    got = []
    # 在图纸包络内按 20 网格扫，优先靠上、靠左
    for y in range(int(mxy) // 20 * 20, int(mny) - 1, -20):
        for x in range(int(mnx) // 20 * 20, int(mxx) + 1, 20):
            cand = (x - w / 2.0, y - h / 2.0, x + w / 2.0, y + h / 2.0)
            if any(overlap(cand, b, clear) for b in boxes):
                continue
            if any(abs(x - gx) < w + clear and abs(y - gy) < h + clear for gx, gy in got):
                continue
            got.append((x, y))
            if len(got) >= n:
                return got, boxes
    return got, boxes


if __name__ == '__main__':
    page = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 120
    h = int(sys.argv[4]) if len(sys.argv) > 4 else 40
    clear = int(sys.argv[5]) if len(sys.argv) > 5 else 30
    spots, boxes = find(page, n, w, h, clear)
    print('占位物 %d 个；找到 %d 个空位（器件框 %dx%d，四周留 %d）：' % (len(boxes), len(spots), w, h, clear))
    for x, y in spots:
        print('  (%d, %d)' % (x, y))
