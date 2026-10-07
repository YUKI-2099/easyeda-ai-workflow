"""Independent geometry graph: rounded coordinates, closed segment intersection.

    python codex_geometry.py

按 project.json 的 pages 逐页审（页数 = pages 的条数，第 i 页读 <audit_dir>/page-p<i>.json，
由 `codex_independent_audit.py pages` 拉下来，并核对里面记的页 uuid 就是 pages 第 i 条）；还要 parsed.json（codex_parse_netlist.py）。
输出 geometry.json（每页的悬空引脚 / 浮空符号 / 带网名导线 / 几何连通图）和 live-parts.json（给 distances / properties 用）。
"""
from decimal import Decimal, ROUND_HALF_UP
import json, collections, math, re

from _paths import cfg, fail, live_dir, read_json, utf8_stdio
def q(v): return float(Decimal(str(v)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
def pt(o): return (q(o['x']),q(o['y']))
def cross(a,b,c): return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
def on(p,a,b):
    return abs(cross(a,b,p))<1e-7 and min(a[0],b[0])-1e-7<=p[0]<=max(a[0],b[0])+1e-7 and min(a[1],b[1])-1e-7<=p[1]<=max(a[1],b[1])+1e-7
def intersect(a,b,c,d):
    if any((on(c,a,b),on(d,a,b),on(a,c,d),on(b,c,d))): return True
    return cross(a,b,c)*cross(a,b,d)<0 and cross(c,d,a)*cross(c,d,b)<0
def segments(w):
    paths=w['line']; paths=paths if paths and isinstance(paths[0],list) else [paths]
    for path in paths:
        # Live getState_Line returns concatenated independent x1,y1,x2,y2
        # segments (including reversed segments), NOT a chained point path.
        assert len(path)%4==0, ('unknown line encoding',path)
        for i in range(0,len(path),4):
            yield (q(path[i]),q(path[i+1])),(q(path[i+2]),q(path[i+3]))

def audit_page(data,netpins):
    ws=data['wires']; seg=[list(segments(w)) for w in ws]; roots=list(range(len(ws)))
    def root(i):
        while roots[i]!=i: roots[i]=roots[roots[i]]; i=roots[i]
        return i
    for i in range(len(ws)):
        for j in range(i):
            if any(intersect(a,b,c,d) for a,b in seg[i] for c,d in seg[j]): roots[root(i)]=root(j)
    vertices={p for ss in seg for ab in ss for p in ab}
    def hits(p): return [i for i,ss in enumerate(seg) if any(on(p,a,b) for a,b in ss)]
    pins=[]; symbols=[]; nc=[]; dangling=[]; floating=[]; vertex_pin=[]; vertex_symbol=[]; groups=collections.defaultdict(lambda:{'pins':[],'labels':[],'wire_ids':[],'names':set()})
    for i,w in enumerate(ws):
        g=groups[root(i)];g['wire_ids'].append(w['id'])
        if w['net']: g['names'].add(w['net'])
    for c in data['components']:
        if c.get('ref'):
            for p in c['pins']:
                item={'pin':c['ref']+'-'+p['number'],'name':p['name'],'xy':pt(p),'net':netpins.get(c['ref'],{}).get(p['number'])}
                ids=hits(pt(p)); item['wire_ids']=[ws[i]['id'] for i in ids]
                if p['nc']: nc.append(item)
                else:
                    pins.append(item)
                    if pt(p) not in vertices: vertex_pin.append(item)
                    if not ids: dangling.append(item)
                for i in ids:
                    g=groups[root(i)];g['pins'].append(item['pin'])
                    if item['net']:g['names'].add(item['net'])
        elif c['type'] in ('netflag','netport'):
            item={'id':c['id'],'net':c.get('net'),'xy':pt(c)};symbols.append(item); ids=hits(pt(c))
            if pt(c) not in vertices:vertex_symbol.append(item)
            if not ids:floating.append(item)
            for i in ids:
                g=groups[root(i)];g['labels'].append(c['id'])
                if c.get('net'):g['names'].add(c['net'])
    named=[{'id':w['id'],'net':w['net'],'component_pins':sorted(set(groups[root(i)]['pins']))} for i,w in enumerate(ws) if w['net']]
    graph=[]
    for g in groups.values():
        g['pins']=sorted(set(g['pins']));g['names']=sorted(g['names']);g['labels']=sorted(set(g['labels']));graph.append(g)
    return {'parts':sum(bool(c.get('ref')) for c in data['components']),'wires':len(ws),'active_pins':len(pins),'nc_count':len(nc),'symbols':len(symbols),'vertex_missing_pins':vertex_pin,'vertex_missing_symbols':vertex_symbol,'dangling_pins':dangling,'floating_symbols':floating,'named_wires':named,'named_wires_no_pin':[w for w in named if not w['component_pins']],'nc':nc,'nc_on_wire_or_net':[p for p in nc if p['wire_ids'] or p['net']],'geometric_net_conflicts':[g for g in graph if len(g['names'])>1],'unlabelled_pin_groups':[g for g in graph if g['pins'] and not g['names']],'graph':graph}

def load_page(i,uuid):
    """第 i 页（从 1 起）拉下来的页数据，并核对它就是 project.json 第 i 页（uuid）；没拉、拉失败、页对不上都 fail()。"""
    rec=read_json(f'page-p{i}.json','先跑 codex_independent_audit.py pages（按 project.json 的 pages 逐页拉）')
    resp=rec.get('response')
    if not isinstance(resp,dict) or not resp.get('success') or not isinstance(resp.get('result'),dict):
        why=rec.get('http_error') or rec.get('url_error') or (resp.get('error') if isinstance(resp,dict) else None)
        fail(f'page-p{i}.json 是一次失败的拉取（{why}）：重跑 codex_independent_audit.py pages')
    data=resp['result']
    got=(data.get('document') or {}).get('uuid')
    if got!=uuid:
        fail(f'page-p{i}.json 是页 {got} 的数据，project.json 第 {i} 页是 {uuid}（页序改过，或是旧数据）：重跑 codex_independent_audit.py pages')
    return data

def run():
    pages=cfg()['pages']
    if not pages:
        fail('project.json 的 pages 是空的：这里按它的页数逐页审（codex_independent_audit.py pages 也按它逐页拉）')
    parsed=read_json('parsed.json','先跑 codex_parse_netlist.py'); allparts=[];results={}
    for i,(name,uuid) in enumerate(pages,1):
        data=load_page(i,uuid)
        results[f'p{i}']=dict(audit_page(data,parsed['pins']),name=name)
        allparts += [dict(c,page=f'p{i}') for c in data['components'] if c.get('ref')]
    live=live_dir()
    (live/'geometry.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    (live/'live-parts.json').write_text(json.dumps(allparts,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({p:{k:(len(v) if isinstance(v,list) else v) for k,v in r.items() if k!='graph'} for p,r in results.items()},ensure_ascii=False,indent=2))
    print('NC',json.dumps({p:r['nc'] for p,r in results.items()},ensure_ascii=False))

if __name__=='__main__':
    utf8_stdio(); run()
