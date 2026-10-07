"""去耦电容距离核对：指定的供电脚，同页上跨在"它的网"和地之间的电容各离它多远；可选再核对执行方证据文件里的坐标和"最近去耦电容"说法。

    python codex_distances.py [执行方证据.md] [--pin U1-3 --pin U2-1 …]

输入（都在 <audit_dir>）：parsed.json（codex_parse_netlist.py）、live-parts.json（codex_geometry.py）。
要查的引脚：--pin（可重复，写成 位号-脚号）或 project.json 的 audit.distances.pins；每个脚所在的网就是它的供电网。
执行方证据文件：命令行第一个参数，或 audit.distances.claims（相对项目根）；给了才比对（格式见 tools/audit/README.md）。
两样都没有就报错退出（退出码 2）。地网名默认 GND，可用 audit.ground_net 改。输出 distance-comparison.json。
"""
from pathlib import Path
import argparse, json, re, math, collections
from codex_geometry import pt, q
from _paths import audit, fail, ground_net, live_dir, read_json, root, utf8_stdio


def parse_pin(item):
    """'U1-3' 或 ['U1', '3'] → ('U1', '3')。"""
    if isinstance(item, str) and '-' in item:
        ref, pin = item.rsplit('-', 1)
    elif isinstance(item, (list, tuple)) and len(item) == 2:
        ref, pin = item
    else:
        fail('引脚要写成 "位号-脚号"（如 "U1-3"）或 ["U1", "3"]，收到 %r' % (item,))
    return str(ref), str(pin)


def value_of(part):
    return (part.get('properties') or {}).get('Value')


def compare_claims(text, parts, parsed, caps, ground):
    """执行方证据文件 §1 的坐标表、§2 的去耦说法 vs 现场数据。返回 (差异列表, 计数)。"""
    nets = lambda r: set(parsed['pins'].get(r, {}).values())
    diffs = []; checked = collections.Counter(); section = ''; page = ''
    for no, line in enumerate(text.splitlines(), 1):
        if line.startswith('## '): section = line
        if line.startswith('### p'): page = line.split()[1]
        if section.startswith('## 1.') and line.startswith('| `'):
            cells = [s.strip().strip('`') for s in line.strip('|').split('|')]; ref = cells[0]
            checked['coordinate_rows'] += 1
            try:
                xy = tuple(q(float(v)) for v in cells[3].strip('()').split(','))
            except (IndexError, ValueError):
                diffs.append({'line': no, 'field': 'unparsable coordinates', 'ref': ref}); continue
            c = parts.get(ref)
            if c is None:
                diffs.append({'line': no, 'field': 'unknown ref', 'ref': ref}); continue
            if pt(c) != xy or c['page'] != page:
                diffs.append({'line': no, 'field': 'coordinates/page', 'ref': ref, 'live': [pt(c), c['page']], 'claim': [xy, page]})
        if section.startswith('## 2.') and line.startswith('- **`'):
            ref = re.search(r'\*\*`([^`]+)`', line)[1]; m = re.search(r'接 `([^`]+)`', line)
            if ref not in parts or not m:
                diffs.append({'line': no, 'field': 'unknown ref or missing net', 'ref': ref}); continue
            net = m[1]
            claims = re.findall(r'`(C[0-9]+)`\(([^,]+),([0-9]+)\)', line)
            candidates = [(r, math.dist(pt(parts[ref]), pt(c))) for r, c in caps.items() if c['page'] == parts[ref]['page'] and nets(r) == {net, ground}]
            candidates.sort(key=lambda x: (x[1], int(x[0][1:])))
            checked['decoupling_rows'] += 1
            if not claims and candidates: diffs.append({'line': no, 'field': 'false no capacitors', 'ref': ref, 'actual': candidates})
            for r, val, dist in claims:
                checked['distances'] += 1
                if r not in parts:
                    diffs.append({'line': no, 'field': 'unknown cap', 'ref': ref, 'cap': r}); continue
                actual = math.dist(pt(parts[ref]), pt(parts[r]))
                if nets(r) != {net, ground} or round(actual) != int(dist): diffs.append({'line': no, 'field': 'distance/net', 'ref': ref, 'cap': r, 'live': actual, 'claim': dist})
                if value_of(parts[r]) != val: diffs.append({'line': no, 'field': 'cap value', 'cap': r})
            if {c[0] for c in claims} != {c[0] for c in candidates[:4]}:
                # Ties at fourth place are equally valid; compare distance multisets.
                if sorted(round(math.dist(pt(parts[ref]), pt(parts[c[0]])), 6) for c in claims if c[0] in parts) != sorted(round(c[1], 6) for c in candidates[:4]): diffs.append({'line': no, 'field': 'nearest ranking', 'ref': ref, 'claim': claims, 'actual': candidates[:4]})
    return diffs, checked


def run(claims=None, pins=None):
    cfg_d = audit('distances'); ground = ground_net()
    if claims is None and cfg_d.get('claims') is not None:
        if not isinstance(cfg_d['claims'], str):
            fail('project.json 的 audit.distances.claims 要写成路径字符串（相对项目根），现在是 %r' % (cfg_d['claims'],))
        claims = root() / cfg_d['claims']
    if not pins:
        pins = cfg_d.get('pins') or []
        if not isinstance(pins, list):
            fail('project.json 的 audit.distances.pins 要写成列表 ["U1-3", …]，现在是 %r' % (pins,))
    pins = [parse_pin(p) for p in pins]
    if claims is None and not pins:
        fail('没配要查的引脚：在 project.json 的 audit.distances.pins 写 ["U1-3", …]，或命令行 --pin U1-3（可重复）；'
             '要和执行方证据文件比对就再给它的路径（命令行第一个参数或 audit.distances.claims）。')
    if claims is not None and not Path(claims).is_file():
        fail('找不到执行方证据文件：%s' % claims)
    parsed = read_json('parsed.json', '先跑 codex_parse_netlist.py')
    parts = {c['ref']: c for c in read_json('live-parts.json', '先跑 codex_geometry.py')}
    caps = {r: c for r, c in parts.items() if re.fullmatch('C[0-9]+', r)}
    nets = lambda r: set(parsed['pins'].get(r, {}).values())
    diffs, checked = [], collections.Counter()
    if claims is not None:
        diffs, checked = compare_claims(Path(claims).read_text(encoding='utf-8-sig'), parts, parsed, caps, ground)
    distances = {}
    for ref, pin in pins:
        c = parts.get(ref)
        if c is None:
            fail('%s 不在 live-parts.json 里（位号写错了？）' % ref)
        p = next((p for p in c['pins'] if p['number'] == pin), None)
        net = parsed['pins'].get(ref, {}).get(pin)
        if p is None or not net or net == ground:
            fail('%s-%s 不是接在供电网上的脚（没有这只脚、没接网，或接的是地 %s）' % (ref, pin, ground))
        rows = []
        for r, cap in caps.items():
            if cap['page'] != c['page'] or nets(r) != {net, ground}: continue
            supply = next((s for s in cap['pins'] if parsed['pins'][r].get(s['number']) == net), None)
            if supply is None: continue                  # 页数据和网表的脚号对不上：交给 codex_geometry 的报告去查
            rows.append({'cap': r, 'value': value_of(cap), 'center_to_center': round(math.dist(pt(c), pt(cap)), 2), 'supply_pin_to_cap_center': round(math.dist(pt(p), pt(cap)), 2), 'supply_pin_to_cap_supply_pin': round(math.dist(pt(p), pt(supply)), 2)})
        distances['%s-%s' % (ref, pin)] = {'net': net, 'center': pt(c), 'supply_pin': pt(p), 'caps': sorted(rows, key=lambda r: r['center_to_center'])}
    rails = collections.defaultdict(list)
    for r in caps:
        ns = nets(r)
        if ground in ns and len(ns) == 2: rails[next(iter(ns - {ground}))].append(r)
    result = {'checked': dict(checked), 'differences': diffs, 'distances': distances, 'all_caps': len(caps), 'GND_cap_groups': dict(rails)}
    (live_dir() / 'distance-comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('claims', nargs='?', help='执行方证据文件（可选）')
    ap.add_argument('--pin', action='append', help='要查的供电脚，写成 位号-脚号，可重复；不给就用 audit.distances.pins')
    args = ap.parse_args(argv)
    run(args.claims, args.pin)
    return 0


if __name__ == '__main__':
    utf8_stdio()
    raise SystemExit(main())
