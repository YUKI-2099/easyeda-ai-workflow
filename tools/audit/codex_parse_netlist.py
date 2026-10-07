"""Independent line-state Protel2 parser and evidence comparison."""
from pathlib import Path
import json, re, collections

def parse(text):
    components=[]; nets={}; state=None; block=[]
    for no,line in enumerate(text.splitlines(),1):
        token=line.strip()
        if state is None:
            if token in ('[','('): state=token; block=[]
            elif token and token!='PROTEL NETLIST 2.0': raise ValueError(('outside block',no,line))
        elif token==(']' if state=='[' else ')'):
            if state=='[':
                attrs={}; i=0
                while i<len(block):
                    key=block[i].strip()
                    if not key or key=='*': i+=1; continue
                    assert i+1<len(block),(no,key)
                    assert key not in attrs,('duplicate property',key)
                    attrs[key]=block[i+1].strip(); i+=2
                assert attrs['DESIGNATOR']==attrs.get('Designator',attrs['DESIGNATOR'])
                components.append(attrs)
            else:
                name=block[0].strip(); assert name not in nets
                nets[name]=[x.strip().split()[0] for x in block[1:] if x.strip()]
            state=None
        else: block.append(line)
    assert state is None,'unclosed block'
    pins=collections.defaultdict(dict)
    for net,members in nets.items():
        for member in members:
            ref,pin=member.rsplit('-',1)
            assert pin not in pins[ref],('pin appears twice',member)
            pins[ref][pin]=net
    refs=[x['DESIGNATOR'] for x in components]
    assert set(pins)<=set(refs),'net references unknown component'
    return {'components':components,'nets':nets,'pins':dict(pins),'duplicate_refs':[k for k,v in collections.Counter(refs).items() if v>1]}

def compare(parsed,evidence):
    """执行方证据文件（Markdown）逐项比对：§1 器件表、§3 上下拉表、§4 网络表，格式见 tools/audit/README.md。"""
    comps={x['DESIGNATOR']:x for x in parsed['components']}; diffs=[]; counts=collections.Counter(); seen=set(); seen_nets=set()
    section=''; page=None; pages={}
    for no,line in enumerate(evidence.splitlines(),1):
        if line.startswith('## '): section=line
        if line.startswith('### p'): page=line.split()[1]
        if not line.startswith('| `'): continue
        cells=[s.strip().strip('`') for s in line.strip('|').split('|')]
        key=cells[0]
        if section.startswith('## 1.'):
            counts['component_rows']+=1; seen.add(key); pages[key]=page
            c=comps.get(key,{})
            for label,actual,expected in [('supplier',c.get('Supplier Part'),cells[1]),('value',c.get('Value'),cells[2]),('pins',parsed['pins'].get(key,{}),dict(x.split('=',1) for x in cells[4].split()))]:
                if actual!=expected: diffs.append({'line':no,'ref':key,'field':label,'live':actual,'claim':expected})
        elif section.startswith('## 3.'):
            counts['pull_rows']+=1
            actual=parsed['pins'].get(key,{})
            if actual!={'1':cells[3],'2':cells[4]} or comps.get(key,{}).get('Value')!=cells[1]: diffs.append({'line':no,'ref':key,'field':'pull','live':actual,'claim':cells})
        elif section.startswith('## 4.'):
            counts['net_rows']+=1; seen_nets.add(key)
            actual=parsed['nets'].get(key,[])
            if collections.Counter(actual)!=collections.Counter(cells[2].split()) or len(actual)!=int(cells[1]): diffs.append({'line':no,'net':key,'live':actual,'claim':cells})
    if seen!=set(comps): diffs.append({'component_set_difference':sorted(seen^set(comps))})
    if seen_nets!=set(parsed['nets']): diffs.append({'net_set_difference':sorted(seen_nets^set(parsed['nets']))})
    return {'checked':dict(counts),'differences':diffs,'pages':pages}

if __name__=='__main__':
    # 用法: codex_parse_netlist.py [执行方的证据文件.md]
    #   不给证据文件 → 只独立解析 <audit_dir>/live.enet.txt 并写 parsed.json
    #   给了        → 再与证据文件逐项比对，写 netlist-comparison.json（"审查方自拉 vs 执行方导出"对照）
    import sys
    from _paths import fail, live_dir, utf8_stdio
    utf8_stdio()
    live=live_dir(create=False)
    src=live/'live.enet.txt'
    if not src.is_file(): fail('缺 %s：先跑 codex_independent_audit.py（冻结门禁会现场导出这份网表）'%src)
    if len(sys.argv)>1 and not Path(sys.argv[1]).is_file(): fail('找不到执行方的证据文件：%s'%sys.argv[1])
    p=parse(src.read_text(encoding='utf-8'))
    (live/'parsed.json').write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
    out={'components':len(p['components']),'nets':len(p['nets']),'connected_pins':sum(map(len,p['nets'].values())),'duplicate_refs':p['duplicate_refs']}
    if len(sys.argv)>1:
        r=compare(p,Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
        (live/'netlist-comparison.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
        out.update({'checked':r['checked'],'differences':r['differences']})
    print(json.dumps(out,ensure_ascii=False,indent=2))
