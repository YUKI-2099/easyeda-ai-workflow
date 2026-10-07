"""几何连通性审计（两侧）：每页画完必跑。

为什么需要：给导线起了网络名时，即使导线**没碰到**引脚，网表也会显示"连上了"
（通用手册 §4.1 一致性≠连通性 的几何版本）。本脚本只看坐标，不看网名，
是这类静默断路的唯一硬判据。2026-09-10 靠它抓到过一颗两脚件转 90° 后线画在旧坐标的断路。

三项检查（全 0 才算过）：
  1. 引脚侧 —— 每个非 NC 引脚上必须有导线端点
  2. 符号侧 —— 每个 netFlag/netPort 必须落在导线端点上（坑⑯：符号会吸附 5 网格）
  3. 网名侧 —— 导线不许带网名（坑㊴：会画出重复文字，并掩盖第 1、2 项）

🔴 坐标一律 round 到 2 位小数再比：2026-09-11 EDA 重启后 getState_X() 开始返回
   150.00000000000003 这类浮点噪声，字符串比对会让每一页都误报"浮空符号"。

用法: python tools/v2_geom_audit.py [页uuid ...]     （不给就查 project.json 的全部页）
"""
import sys, json
sys.path.insert(0, __file__.replace('\\', '/').rsplit('/', 1)[0])
import v2_rows, bridge

from project import load_project


def pages():
    """project.json 的页表 [(页名, 页uuid), ...]；用到时才读配置（导入本模块不读文件）。"""
    return load_project()['pages']


def __getattr__(name):                   # 兼容旧写法 v2_geom_audit.PAGES：取值时才读 project.json
    if name == 'PAGES':
        return pages()
    raise AttributeError('module %r has no attribute %r' % (__name__, name))

JS = '''await eda.dmt_EditorControl.openDocument('%s');await new Promise(r=>setTimeout(r,1400));
const all=await eda.sch_PrimitiveComponent.getAll();
const parts=all.filter(c=>c.getState_ComponentType()==='part');
const syms=all.filter(c=>['netflag','netport'].includes(c.getState_ComponentType()));
const ws=await eda.sch_PrimitiveWire.getAll();
const pts=new Set();
const K=(x,y)=>(Math.round(x*100)/100)+','+(Math.round(y*100)/100);for(const w of ws){const L=w.getState_Line();for(let i=0;i<L.length;i+=2)pts.add(K(L[i],L[i+1]));}
const badPin=[];
for(const c of parts){const des=c.getState_Designator();
  const ps=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(c.getState_PrimitiveId());
  for(const p of ps){
    if(p.getState_NoConnected?p.getState_NoConnected():p.noConnected)continue;
    if(!pts.has(K(p.x,p.y)))badPin.push([des,String(p.pinNumber),p.x,p.y]);}}
const badSym=syms.filter(s=>!pts.has(K(s.getState_X(),s.getState_Y())))
  .map(s=>[s.getState_Net(),s.getState_X(),s.getState_Y()]);
const named=ws.filter(w=>{const n=w.getState_Net();return n&&n.length;})
  .map(w=>[w.getState_Net(),w.getState_Line()]).slice(0,8);
return {parts:parts.length,wires:ws.length,syms:syms.length,badPin,badSym,namedCount:ws.length-ws.filter(w=>{const n=w.getState_Net();return !n||!n.length;}).length,named};'''

if __name__ == '__main__':
    only = sys.argv[1:]
    todo = [p for p in pages() if not only or p[1] in only]
    unknown = sorted(set(only) - {uid for _, uid in todo})
    if unknown or not todo:
        print('🔴 没有可查的页：%s' % ('这些页 uuid 不在 project.json 的 pages 里 %s' % unknown if unknown
                                     else 'project.json 的 pages 是空的'))
        sys.exit(1)
    bad = 0
    bridge.remember_doc()                 # #51：记住用户前台文档，退出时切回
    for name, uid in todo:
        r = v2_rows.ex(JS % uid, timeout=180)
        if not r or 'HTTP' in r:
            print('🔴', name, '读取失败', r); bad += 1; continue
        n = len(r['badPin']) + len(r['badSym']) + r['namedCount']
        bad += n
        print('%s %-14s 件%3d 线%3d 符号%3d ｜ 悬空引脚 %d ｜ 浮空符号 %d ｜ 带网名导线 %d'
              % ('✅' if n == 0 else '🔴', name, r['parts'], r['wires'], r['syms'],
                 len(r['badPin']), len(r['badSym']), r['namedCount']))
        for k in ('badPin', 'badSym', 'named'):
            if r[k]:
                print('    %s: %s' % (k, json.dumps(r[k], ensure_ascii=False)[:400]))
    print('\n合计异常:', bad, '✅ 三项全 0，几何连通性过关' if bad == 0 else '🔴 必须修完再往下走')
    sys.exit(1 if bad else 0)
