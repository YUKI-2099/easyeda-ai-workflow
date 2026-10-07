"""Independent incremental and rail audit; consumes Codex live capture only.

    python codex_increment_check.py <旧冻结.enet.txt> [--expect 预期.json]

拿现场网表（<audit_dir>/parsed.json，codex_parse_netlist.py 生成）和旧冻结比：增删的位号 / 网、同名件的属性和逐脚变化；
电容按"跨在哪两个网之间"分组，分成电源去耦和非去耦两类；顺带列出项目网表目录里各冻结文件的件数 / 网数。

project.json 可选 audit.increment（逐项说明见 tools/audit/README.md）：
  non_bypass_nets   电容的脚全落在这些网（加上地）上就不算电源去耦，如复位 / 使能脚上的延时电容
  non_bypass_refs   直接点名不算去耦的电容位号
  expect            本轮预期，都可选，给了才核对：ref_delta / net_delta（新增减删除的位号数 / 网数）、
                    bypass_caps / non_bypass_caps（两类电容个数）、nets {网名: [成员, …]}（按集合比）、pins {位号: {脚号: 网名}}；
                    以 _ 开头的键算注释
--expect 给一个同结构的 JSON 文件时，优先于 project.json 里的 expect。没有预期就只出报告、不核对。
这些配置先核对类型（列表必须是字符串列表、计数必须是整数），不对就报错退出，不会读到一半才崩。
退出码：0 报告写好且预期都对上（或没配预期）；1 有预期没对上（看输出里的 expectations.failed）；2 参数 / 配置 / 输入缺。
输出 increment-check.json。
"""
from pathlib import Path
import argparse, collections, hashlib, json, re
from codex_parse_netlist import parse
from _paths import audit, cfg, fail, ground_net, live_dir, read_json, str_list, utf8_stdio

COUNT_KEYS = ('ref_delta', 'net_delta', 'bypass_caps', 'non_bypass_caps')
EXPECT_KEYS = COUNT_KEYS + ('nets', 'pins')


def validate_expect(expect, where):
    """核对预期的结构和类型，去掉以 _ 开头的注释键后返回；不对就 fail()。没给（None）返回 None。"""
    if expect is None:
        return None
    if not isinstance(expect, dict):
        fail('%s 要写成对象 {...}，现在是 %r' % (where, expect))
    expect = {k: v for k, v in expect.items() if not k.startswith('_')}
    unknown = sorted(set(expect) - set(EXPECT_KEYS))
    if unknown:
        fail('%s 里有不认识的键 %s（可用：%s；以 _ 开头的键算注释）' % (where, unknown, '、'.join(EXPECT_KEYS)))
    for key in COUNT_KEYS:
        if key in expect and (not isinstance(expect[key], int) or isinstance(expect[key], bool)):
            fail('%s.%s 要写成整数，现在是 %r' % (where, key, expect[key]))
    nets = expect.get('nets', {})
    if not isinstance(nets, dict):
        fail('%s.nets 要写成 {"网名": ["位号-脚号", …]}，现在是 %r' % (where, nets))
    for net, members in nets.items():
        if members is None:
            fail('%s.nets.%s 要写成字符串列表 ["位号-脚号", …]，现在是 None' % (where, net))
        str_list(members, '%s.nets.%s' % (where, net))
    pins = expect.get('pins', {})
    if not isinstance(pins, dict) or not all(isinstance(m, dict) and all(isinstance(n, str) for n in m.values())
                                             for m in pins.values()):
        fail('%s.pins 要写成 {"位号": {"脚号": "网名"}}，现在是 %r' % (where, pins))
    return expect


def classify_caps(cur, ground, non_bypass_nets=(), non_bypass_refs=()):
    """电容 → (按网对分组的去耦电容 {"网A / 网B": [...]}, 非去耦电容列表)。"""
    signal = {ground} | set(non_bypass_nets)
    rails = collections.defaultdict(list)
    non_power = []
    for ref, c in {c['DESIGNATOR']: c for c in cur['components']}.items():
        if not re.fullmatch(r'C\d+', ref):
            continue
        pair = cur['pins'].get(ref, {})
        if set(pair.values()) <= signal or ref in non_bypass_refs:
            non_power.append({'ref': ref, 'pins': pair, 'value': c.get('Value')})
            continue
        rails[' / '.join(sorted(pair.values()))].append({'ref': ref, 'value': c.get('Value')})
    return dict(rails), non_power


def check_expect(expect, out, cur):
    """按（已经 validate_expect 过的）expect 核对；返回 {'checked': 条数, 'failed': [...]}。"""
    actual = {'ref_delta': len(out['added_refs']) - len(out['removed_refs']),
              'net_delta': len(out['added_nets']) - len(out['removed_nets']),
              'bypass_caps': sum(map(len, out['power_capacitors_by_net_pair'].values())),
              'non_bypass_caps': len(out['non_bypass_capacitors'])}
    checked, failed = 0, []
    for key, value in actual.items():
        if key in expect:
            checked += 1
            if expect[key] != value:
                failed.append({'key': key, 'expected': expect[key], 'actual': value})
    for net, members in (expect.get('nets') or {}).items():
        checked += 1
        got = cur['nets'].get(net)
        if got is None or sorted(got) != sorted(members):
            failed.append({'key': 'nets.' + net, 'expected': sorted(members), 'actual': None if got is None else sorted(got)})
    for ref, pins in (expect.get('pins') or {}).items():
        for pin, net in pins.items():
            checked += 1
            got = cur['pins'].get(ref, {}).get(str(pin))
            if got != net:
                failed.append({'key': 'pins.%s-%s' % (ref, pin), 'expected': net, 'actual': got})
    return {'checked': checked, 'failed': failed}


def run(old_path, expect=None, expect_from='--expect 给的预期文件'):
    old_path = Path(old_path)
    if not old_path.is_file():
        fail('找不到旧冻结文件：%s' % old_path)
    conf = audit('increment')
    non_nets = str_list(conf.get('non_bypass_nets'), 'project.json 的 audit.increment.non_bypass_nets')
    non_refs = str_list(conf.get('non_bypass_refs'), 'project.json 的 audit.increment.non_bypass_refs')
    if expect is None:
        expect, expect_from = conf.get('expect'), 'project.json 的 audit.increment.expect'
    expect = validate_expect(expect, expect_from)
    ground = ground_net()
    cur = read_json('parsed.json', '先跑 codex_parse_netlist.py')
    old = parse(old_path.read_text(encoding='utf-8-sig'))
    ac = {c['DESIGNATOR']: c for c in old['components']}
    bc = {c['DESIGNATOR']: c for c in cur['components']}
    rails, non_power = classify_caps(cur, ground, non_nets, non_refs)
    meta_keys = ['Supplier Part', 'Value', 'FOOTPRINT', 'PARTTYPE']
    out = {
        'old_sha256': hashlib.sha256(old_path.read_bytes()).hexdigest(),
        'old_counts': [len(ac), len(old['nets'])],
        'current_counts': [len(bc), len(cur['nets'])],
        'added_refs': sorted(bc.keys() - ac.keys()),
        'removed_refs': sorted(ac.keys() - bc.keys()),
        'added_nets': sorted(cur['nets'].keys() - old['nets'].keys()),
        'removed_nets': sorted(old['nets'].keys() - cur['nets'].keys()),
        'common_ref_metadata_changes': {r: {k: [ac[r].get(k), bc[r].get(k)] for k in meta_keys if ac[r].get(k) != bc[r].get(k)} for r in sorted(ac.keys() & bc.keys()) if any(ac[r].get(k) != bc[r].get(k) for k in meta_keys)},
        'common_ref_pin_changes': {r: {'old': old['pins'].get(r), 'new': cur['pins'].get(r)} for r in sorted(ac.keys() & bc.keys()) if old['pins'].get(r) != cur['pins'].get(r)},
        'power_capacitors_by_net_pair': rails,
        'non_bypass_capacitors': non_power,
        'snapshot_counts': {},
    }
    c = cfg()
    for p in sorted(Path(c['netlist_dir']).glob(c['freeze_glob'])):
        d = parse(p.read_text(encoding='utf-8-sig'))
        out['snapshot_counts'][p.name] = [len(d['components']), len(d['nets'])]
    out['expectations'] = check_expect(expect, out, cur) if expect else {'checked': 0, 'failed': [], 'note': '没配预期（audit.increment.expect 或 --expect）：只出报告，不核对'}
    (live_dir() / 'increment-check.json').write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='增量与电源轨审计：现场网表 vs 旧冻结')
    ap.add_argument('old', nargs='?', help='要对比的旧冻结文件（.enet.txt）')
    ap.add_argument('--expect', help='本轮预期 JSON 文件，结构同 project.json 的 audit.increment.expect')
    args = ap.parse_args(argv)
    if not args.old:
        fail('用法: codex_increment_check.py <旧冻结.enet.txt> [--expect 预期.json]')
    expect = None
    if args.expect:
        try:
            expect = json.loads(Path(args.expect).read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as e:
            fail('读不了预期文件 %s：%s' % (args.expect, e))
        if not isinstance(expect, dict):
            fail('预期文件 %s 要写成对象 {...}，现在是 %r' % (args.expect, expect))
    out = run(args.old, expect, '预期文件 %s' % args.expect)
    return 1 if out['expectations']['failed'] else 0


if __name__ == '__main__':
    utf8_stdio()
    raise SystemExit(main())
