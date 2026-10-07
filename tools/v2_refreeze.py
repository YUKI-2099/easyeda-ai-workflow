"""改完原理图之后跑这一个 —— 审计 + 导网表 + 算 SHA + 与旧冻结逐项 diff。

用法:
    python tools/v2_refreeze.py                 # 只检查、只报告，不写新冻结文件
    python tools/v2_refreeze.py --freeze        # 检查通过后写新冻结文件并更新 SHA

它做四件事:
    1. 几何三查(project.json 的每一页) —— 悬空引脚 / 浮空符号 / 带网名导线，必须全 0
    2. 网表语义            —— 单脚网 / 自动网($开头) / Value 空 / supplierId 非 C 号
    3. 与当前冻结文件 diff  —— 分别列出「器件变化」(料号/封装/Value) 与「网络变化」
    4. DRC                 —— 致命/错误必须 0

为什么必须跑: 换料或换封装都会进 Protel 网表(每个器件带 FOOTPRINT 字段),
旧 SHA 立即作废。基准一废,后续任何审查都建立在错的东西上。
页表、网表目录、冻结文件名都来自 project.json（运行时才读，导入本模块不读文件）。
"""
import sys, io, os, re, glob, json, hashlib
sys.path.insert(0, __file__.replace('\\', '/').rsplit('/', 1)[0])
import v2_rows, v2_geom_audit, bridge

from project import load_project


def parse(txt):
    """Protel2 网表 -> (器件表, 网络表)。

    🔴 必须逐行线性扫描，不许用正则块匹配。
    原来的块正则在网络段变大后灾难性回溯：2026-09-11 实测一百多 KB 的网表
    跑 >10 分钟不出结果，表面看像"桥接卡死"，其实卡在本地正则。
    """
    lines = txt.replace('\r', '').split('\n')
    parts, nets = {}, {}
    i, n = 0, len(lines)
    FIELDS = ('DESIGNATOR', 'FOOTPRINT', 'PARTTYPE', 'Supplier Part', 'Value')
    while i < n and lines[i].strip() != '(':
        if lines[i].strip() == '[':
            d, i = {}, i + 1
            while i < n and lines[i].strip() != ']':
                if lines[i] in FIELDS and i + 1 < n:
                    d[lines[i]] = lines[i + 1].strip()
                i += 1
            if d.get('DESIGNATOR'):
                parts[d['DESIGNATOR']] = d
        i += 1
    while i < n:
        if lines[i].strip() == '(':
            i += 1
            nm = lines[i].strip() if i < n else ''
            i += 1
            mem = []
            while i < n and lines[i].strip() != ')':
                if lines[i].strip():
                    mem.append(lines[i].split()[0])
                i += 1
            if nm:
                nets[nm] = sorted(mem)
        i += 1
    return parts, nets


def cur_freeze(cfg=None):
    """现役冻结文件 = 网表目录里按 freeze_glob 排序的最后一个；没有返回 None。"""
    cfg = cfg or load_project()
    f = sorted(glob.glob(os.path.join(cfg['netlist_dir'], cfg['freeze_glob'])))
    return f[-1] if f else None


def main():
    P = load_project()
    ok = True
    bridge.remember_doc()                 # #51：记住用户前台文档，退出时切回
    print('=== 1. 几何三查 ===')
    bad = 0
    if not P['pages']:
        print('  🔴 project.json 的 pages 是空的，几何三查没法做（门禁不能空过）'); bad += 1
    for name, uid in P['pages']:
        r = v2_rows.ex(v2_geom_audit.JS % uid, timeout=180)
        if not r or 'HTTP' in r:
            print('  🔴', name, '读取失败', r); bad += 1; continue
        n = len(r['badPin']) + len(r['badSym']) + r['namedCount']
        bad += n
        print('  %s %-14s 件%3d 线%3d ｜ 悬空%d 浮空%d 带网名%d'
              % ('✅' if n == 0 else '🔴', name, r['parts'], r['wires'],
                 len(r['badPin']), len(r['badSym']), r['namedCount']))
        for k in ('badPin', 'badSym'):
            if r[k]:
                print('      ', k, json.dumps(r[k], ensure_ascii=False)[:300])
    if bad:
        ok = False

    print('\n=== 2. 网表语义 ===')
    n = v2_rows.ex("const f=await eda.sch_ManufactureData.getNetlistFile('x','Protel2');return {s:f?await f.text():''};", timeout=150)
    txt = (n or {}).get('s', '')
    if not txt:
        print('  🔴 网表导不出来 —— 多半是 DRC 有 fatal'); sys.exit(1)
    parts, nets = parse(txt)
    sha = hashlib.sha256(txt.encode('utf-8')).hexdigest()
    single = [k for k, m in nets.items() if len(m) == 1]
    auto = [k for k in nets if k.startswith('$')]
    nonel = set(P.get('nonelectrical_refs') or [])    # 丝印二维码这类非电气件：没有 Value / C 号属正常（project.json 登记）
    noval = [d for d, p in parts.items() if not p.get('Value') and d not in nonel]
    nolcsc = [d for d, p in parts.items() if not re.fullmatch(r'C\d+', str(p.get('Supplier Part', ''))) and d not in nonel]
    if nonel:
        print('  （跳过非电气件 %s，见 project.json 的 nonelectrical_refs）' % sorted(nonel & set(parts)))
    for label, lst in [('单脚网', single), ('自动网', auto), ('Value 空', noval), ('supplierId 非C号', nolcsc)]:
        print('  %s %-14s %s' % ('✅' if not lst else '🔴', label, lst or 0))
        if lst:
            ok = False
    print('  规模: %d 件 / %d 网    SHA-256 %s' % (len(parts), len(nets), sha))

    print('\n=== 3. 与当前冻结 diff ===')
    fz = cur_freeze(P)
    if not fz:
        print('  ⚠️ 找不到冻结文件')
    else:
        old = io.open(fz, encoding='utf-8', newline='').read()
        osha = hashlib.sha256(old.encode('utf-8')).hexdigest()
        print('  冻结文件:', os.path.basename(fz))
        if osha == sha:
            print('  ✅ 与冻结逐字节一致 —— 原理图没动过')
        else:
            op, on = parse(old)
            pd = []
            for d in sorted(set(op) | set(parts)):
                a, b = op.get(d), parts.get(d)
                if a is None:
                    pd.append(('新增', d, '', '%s / %s' % (b.get('Supplier Part'), b.get('FOOTPRINT')))); continue
                if b is None:
                    pd.append(('删除', d, '%s / %s' % (a.get('Supplier Part'), a.get('FOOTPRINT')), '')); continue
                for f in ('Supplier Part', 'FOOTPRINT', 'PARTTYPE', 'Value'):
                    if a.get(f) != b.get(f):
                        pd.append((f, d, a.get(f), b.get(f)))
            nd = [(k, on.get(k), nets.get(k)) for k in sorted(set(on) | set(nets)) if on.get(k) != nets.get(k)]
            print('  🔸 器件变化 %d 项:' % len(pd))
            for t, d, a, b in pd[:40]:
                print('      %-14s %-6s %s → %s' % (t, d, a, b))
            print('  🔸 网络变化 %d 项:' % len(nd))
            for k, a, b in nd[:25]:
                print('      %-12s %s → %s' % (k, a, b))
            if not nd:
                print('      （无 —— 只改了封装/料号，连接关系未变）')

    print('\n=== 4. DRC ===')
    d = v2_rows.ex("return {drc:JSON.stringify(await eda.sch_Drc.check(true,false,true)).slice(0,400)};")
    print('  ', (d or {}).get('drc'), ' （只要没有 fatal/error 就行；warn 需人工归类）')

    print('\n' + ('✅ 门禁全过' if ok else '🔴 有门禁未过，先修再冻结'))
    if '--freeze' in sys.argv:
        if not ok:
            print('拒绝写冻结文件：门禁未过'); sys.exit(1)
        import datetime
        tag = datetime.datetime.now().strftime('%Y%m%d-%H%M')
        fn = os.path.join(P['netlist_dir'], P['freeze_format'].format(tag=tag))
        # 🔴 SHA 文件名不许用 .replace('.enet.txt', ...) 硬凑。2026-09-14 差点炸：某个项目的
        # freeze_format 结尾是 .enet（没有 .txt），replace 不生效 → 两个 open 写的是同一个路径，
        # 后写的那一行 SHA 会把刚写好的整份冻结网表**整体覆盖掉**。
        shafn = re.sub(r'\.enet(\.txt)?$|\.txt$', '', fn) + '-sha256.txt'
        if os.path.abspath(shafn) == os.path.abspath(fn):
            print('🔴 SHA 文件名与冻结文件同名，拒绝写（freeze_format=%r）' % P['freeze_format'])
            sys.exit(1)
        io.open(fn, 'w', encoding='utf-8', newline='').write(txt)
        io.open(shafn, 'w', encoding='utf-8', newline='').write(
            '%s  %s\n' % (sha, os.path.basename(fn)))
        print('\n已写新冻结: %s\nSHA-256 %s' % (os.path.basename(fn), sha))
        print('🔴 记得把这个 SHA 更新到项目里记录冻结基准的地方（进度记录、PCB 交接说明等）')
    else:
        print('（本次只检查未写冻结文件；确认无误后加 --freeze）')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
