"""codex_* 审查脚本的离线回归：几何 / 解析的纯函数，加上"配置用到时才读、缺了就说人话退出"。

不连桥接、不联网：要项目配置的用例在临时目录里造一份假的 project.json（EDA_PROJECT 指过去），用完还原；
采集脚本用一个假桥接（替换 urllib.request.urlopen）跑通"门禁 → 逐页拉 → 几何"，time.sleep 和 atexit.register 也换掉。
命令行用例起子进程，检查退出码、stderr 里有没有一句人话、有没有 Traceback；子进程不设 PYTHONUTF8，走本机编码的管道。
"""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error

import _paths
import codex_distances
import codex_geometry
import codex_increment_check
import codex_independent_audit
import codex_properties
from codex_geometry import on,intersect,pt,audit_page,segments
from codex_parse_netlist import parse

HERE = Path(__file__).resolve().parent


class IndependentAuditTests(unittest.TestCase):
    def test_crossing_and_t_junction(self):
        self.assertTrue(intersect((0,1),(2,1),(1,0),(1,2)))
        self.assertTrue(intersect((0,1),(2,1),(1,1),(1,2)))
        self.assertTrue(intersect((0,0),(2,0),(1,0),(3,0)))
        self.assertFalse(intersect((0,0),(2,0),(0,1),(2,1)))
    def test_noise_and_segment_interior(self):
        self.assertEqual(pt({'x':150.00000000003,'y':149.99999999999}),(150,150))
        self.assertTrue(on((1,0),(0,0),(2,0)))
    def test_live_flat_segment_encoding(self):
        # 合成样本：三段独立线段（中间那段是反着记的）。要是误当成连续折线，(110,200)→(100,300) 那段斜线会经过 (105,250)。
        w={'line':[100,200,110,200,100,300,100,200,50,300,100,300]}
        s=list(segments(w));self.assertEqual(len(s),3)
        self.assertTrue(all(a[0]==b[0] or a[1]==b[1] for a,b in s))
        self.assertTrue(on((105,250),(110,200),(100,300)))
        self.assertFalse(any(on((105,250),a,b) for a,b in s))
    def test_named_wire_connected_through_crossing(self):
        d={'wires':[{'id':'a','net':'V','line':[0,1,2,1]},{'id':'b','net':'','line':[1,0,1,2]}], 'components':[{'ref':'U1','pins':[{'number':'1','name':'V','x':1,'y':0,'nc':False}]}]}
        r=audit_page(d,{'U1':{'1':'V'}})
        self.assertEqual(r['named_wires_no_pin'],[])
        d['components'][0]['pins'][0]['x']=5
        r=audit_page(d,{'U1':{'1':'V'}})
        self.assertEqual(len(r['named_wires_no_pin']),1)
        self.assertEqual(len(r['dangling_pins']),1)
    def test_parser_whitespace_and_empty_property(self):
        t='PROTEL NETLIST 2.0\n[\nDESIGNATOR\nU1\nValue\n\n\n*\n]\n(\n +3V3 \nU1-A1 foo Passive\n)\n'
        p=parse(t);self.assertEqual(p['pins'],{'U1':{'A1':'+3V3'}});self.assertEqual(p['components'][0]['Value'],'')
    def test_parser_rejects_double_connection_and_truncation(self):
        t='PROTEL NETLIST 2.0\n[\nDESIGNATOR\nU1\n]\n(\nV\nU1-1 x\n)\n(\nGND\nU1-1 x\n)'
        with self.assertRaises(AssertionError):parse(t)
        with self.assertRaises(AssertionError):parse('[\nDESIGNATOR\nU1')


# ── 下面的用例要项目配置：临时目录里造假项目 ─────────────────────────────────

def run_quiet(fn, *args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        result = fn(*args, **kwargs)
    return result, out.getvalue(), err.getvalue()


def part(ref, x, y, pins, page_props=None, supplier='C1'):
    return {'id': 'id-' + ref, 'type': 'part', 'ref': ref, 'x': x, 'y': y, 'net': None,
            'properties': page_props or {}, 'supplier': supplier,
            'pins': [{'number': n, 'name': n, 'x': px, 'y': py, 'nc': False} for n, px, py in pins]}


def page_record(*components, uuid, wires=()):
    """codex_independent_audit 记下的一页：桥接回话原样包在 response 里，document 是拉取时的活动页。"""
    return {'at': 'test', 'code': '', 'response': {'success': True, 'result': {
        'document': {'uuid': uuid}, 'components': list(components), 'wires': list(wires)}}}


OLD_NETLIST = ('PROTEL NETLIST 2.0\n[\nDESIGNATOR\nU1\n]\n[\nDESIGNATOR\nC1\nValue\n100nF\n]\n'
               '(\n+3V3\nU1-1\nC1-1\n)\n(\nGND\nU1-2\nC1-2\n)\n')


def current_parsed():
    """现场网表：比旧冻结多了 C2（NET_EN 上的延时电容）和 C3（VBUS 去耦）。"""
    nets = {'+3V3': ['U1-1', 'C1-1'], 'GND': ['U1-2', 'C1-2', 'C2-2', 'C3-2'], 'NET_EN': ['U1-3', 'C2-1'], 'VBUS': ['C3-1']}
    comps = [{'DESIGNATOR': 'U1'}, {'DESIGNATOR': 'C1', 'Value': '100nF'}, {'DESIGNATOR': 'C2', 'Value': '10nF'}, {'DESIGNATOR': 'C3', 'Value': '1uF'}]
    pins = {}
    for net, members in nets.items():
        for m in members:
            ref, pin = m.rsplit('-', 1)
            pins.setdefault(ref, {})[pin] = net
    return {'components': comps, 'nets': nets, 'pins': pins, 'duplicate_refs': []}


class ProjectCase(unittest.TestCase):
    def make_project(self, raw=None, **overrides):
        """临时项目：project.json（raw 给了就原样写进去）+ 空的 netlist 目录，EDA_PROJECT 指过去。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        conf = {'name': 'audit-test', 'pages': [['第一页', 'page-a'], ['第二页', 'page-b']],
                'netlist_dir': 'netlist', 'audit_dir': 'evidence'}
        conf.update(overrides)
        (root / 'project.json').write_text(raw if raw is not None else json.dumps(conf, ensure_ascii=False), encoding='utf-8')
        (root / 'netlist').mkdir()
        env = mock.patch.dict(os.environ, {'EDA_PROJECT': str(root / 'project.json')})
        env.start()
        self.addCleanup(env.stop)
        _paths.reset()
        self.addCleanup(_paths.reset)
        self.root, self.live = root, root / conf['audit_dir']
        return root

    def put(self, name, value):
        self.live.mkdir(exist_ok=True)
        (self.live / name).write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def load(self, name):
        return json.loads((self.live / name).read_text(encoding='utf-8'))

    def assertExits(self, fn, *args, code=2, says=None, **kwargs):
        """在本进程里调用：要以 SystemExit(code) 结束、stderr 里有 says。（有没有 Traceback 在 CliTests 里用子进程查。）"""
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            fn(*args, **kwargs)
        self.assertEqual(cm.exception.code, code, err.getvalue())
        if says:
            self.assertIn(says, err.getvalue())
        return err.getvalue()


class ConfigTests(ProjectCase):
    def test_imports_need_no_project_and_touch_nothing(self):
        env = {k: v for k, v in os.environ.items() if k != 'EDA_PROJECT'}
        code = ('import sys; sys.dont_write_bytecode=True; sys.path.insert(0, %r); import _paths, codex_geometry, '
                'codex_parse_netlist, codex_distances, codex_properties, codex_increment_check, '
                'codex_independent_audit, pcb_route_diff' % str(HERE))
        with tempfile.TemporaryDirectory() as d:
            run = subprocess.run([sys.executable, '-B', '-c', code], cwd=d, env=env,
                                 capture_output=True, encoding='utf-8', errors='replace')
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout + run.stderr, '')
            self.assertEqual(os.listdir(d), [])

    def test_missing_project_json_is_a_clear_exit(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {'EDA_PROJECT': os.path.join(d, 'nope', 'project.json')}):
            _paths.reset()
            self.addCleanup(_paths.reset)
            self.assertExits(_paths.cfg, says='project.json')

    def test_broken_project_json_is_a_clear_exit(self):
        self.make_project(raw='{"pages": [["p1", "page-a"],}')
        self.assertIn('Expecting', self.assertExits(_paths.cfg, says='project.json'))
        self.make_project(raw='[1, 2, 3]')                                         # 顶层不是对象
        self.assertExits(_paths.cfg, says='project.json')

    def test_live_dir_is_created_only_when_used(self):
        self.make_project()
        self.assertFalse(self.live.exists())
        self.assertExits(_paths.read_json, 'parsed.json', '先跑 codex_parse_netlist.py', says='codex_parse_netlist')
        self.assertFalse(self.live.exists())
        self.assertEqual(_paths.live_dir(), self.live)
        self.assertTrue(self.live.is_dir())

    def test_audit_section_and_value_types_are_checked(self):
        self.make_project(audit=['not', 'an', 'object'])
        self.assertExits(_paths.audit, says='audit')
        self.make_project(audit={'ground_net': ['GND']})
        self.assertExits(_paths.ground_net, says='ground_net')
        self.assertEqual(_paths.str_list(None, 'x'), [])
        self.assertExits(_paths.str_list, 'NET_EN', 'audit.increment.non_bypass_nets', says='non_bypass_nets')
        self.assertExits(_paths.str_list, ['A', 1], 'x', says='字符串列表')


class GeometryTests(ProjectCase):
    def test_walks_every_configured_page(self):
        self.make_project(pages=[['a', 'u1'], ['b', 'u2'], ['c', 'u3']])
        self.put('parsed.json', {'pins': {'R1': {'1': 'N1', '2': 'N2'}}})
        for i in (1, 2, 3):
            self.put('page-p%d.json' % i, page_record(part('R%d' % i, 0, 0, [('1', -10, 0), ('2', 10, 0)]), uuid='u%d' % i))
        run_quiet(codex_geometry.run)
        geometry = self.load('geometry.json')
        self.assertEqual(sorted(geometry), ['p1', 'p2', 'p3'])
        self.assertEqual([geometry[p]['name'] for p in ('p1', 'p2', 'p3')], ['a', 'b', 'c'])
        self.assertEqual([(c['ref'], c['page']) for c in self.load('live-parts.json')], [('R1', 'p1'), ('R2', 'p2'), ('R3', 'p3')])

    def test_missing_or_failed_page_and_empty_pages(self):
        self.make_project()
        self.put('parsed.json', {'pins': {}})
        self.put('page-p1.json', page_record(uuid='page-a'))
        self.assertExits(codex_geometry.run, says='page-p2.json')
        self.put('page-p2.json', {'at': 'test', 'code': '', 'http_error': 500, 'body': 'timeout'})
        self.assertExits(codex_geometry.run, says='失败的拉取')
        self.put('page-p2.json', {'at': 'test', 'code': '', 'url_error': 'connection refused'})
        self.assertExits(codex_geometry.run, says='connection refused')
        self.make_project(pages=[])
        self.assertExits(codex_geometry.run, says='pages')

    def test_page_data_must_belong_to_the_configured_page(self):
        self.make_project()
        self.put('parsed.json', {'pins': {}})
        self.put('page-p1.json', page_record(uuid='page-b'))                        # 页序改过 / 旧数据
        self.put('page-p2.json', page_record(uuid='page-a'))
        self.assertExits(codex_geometry.run, says='page-p1.json 是页 page-b')


class DistanceTests(ProjectCase):
    def setup_live(self, **overrides):
        self.make_project(**overrides)
        self.put('parsed.json', current_parsed())
        self.put('live-parts.json', [
            dict(part('U1', 0, 0, [('1', -10, 0), ('2', 10, 0), ('3', 0, 10)]), page='p1'),
            dict(part('C1', 30, 0, [('1', 20, 0), ('2', 40, 0)], {'Value': '100nF'}), page='p1'),
            dict(part('C3', 50, 0, [('1', 40, 0), ('2', 60, 0)], {'Value': '1uF'}), page='p1'),
        ])

    def test_needs_pins_or_claims(self):
        self.setup_live()
        self.assertExits(codex_distances.run, says='audit.distances.pins')

    def test_pins_from_config_and_command_line(self):
        self.setup_live(audit={'distances': {'pins': ['U1-1']}})
        result, _, _ = run_quiet(codex_distances.run)
        self.assertEqual(list(result['distances']), ['U1-1'])
        self.assertEqual(result['distances']['U1-1']['net'], '+3V3')
        self.assertEqual([c['cap'] for c in result['distances']['U1-1']['caps']], ['C1'])
        self.assertEqual(result['GND_cap_groups'], {'+3V3': ['C1'], 'VBUS': ['C3']})
        self.assertEqual(self.load('distance-comparison.json')['all_caps'], 2)
        self.assertEqual(run_quiet(codex_distances.main, ['--pin', 'U1-1'])[0], 0)
        self.assertExits(codex_distances.run, pins=['U1-2'], says='U1-2')          # 地脚不是供电脚
        self.assertExits(codex_distances.run, pins=['U9-1'], says='U9')

    def test_config_value_types(self):
        self.setup_live(audit={'distances': {'pins': 'U1-1'}})
        self.assertExits(codex_distances.run, says='audit.distances.pins')
        self.setup_live(audit={'distances': {'pins': [42]}})
        self.assertExits(codex_distances.run, says='位号-脚号')
        self.setup_live(audit={'distances': {'claims': ['a.md']}})
        self.assertExits(codex_distances.run, says='audit.distances.claims')

    def test_claims_file_is_compared(self):
        self.setup_live()
        claims = self.root / 'claims.md'
        good = ('## 1. 器件\n### p1 第一页\n| `U1` | C1 | X | (0, 0) | 1=+3V3 |\n| `C1` | C1 | 100nF | (30, 0) | 1=+3V3 2=GND |\n'
                '## 2. 去耦\n- **`U1`** 接 `+3V3`：`C1`(100nF,30)\n')
        claims.write_text(good, encoding='utf-8')
        result, _, _ = run_quiet(codex_distances.run, claims=str(claims))
        self.assertEqual(result['differences'], [])
        self.assertEqual(result['checked'], {'coordinate_rows': 2, 'decoupling_rows': 1, 'distances': 1})
        claims.write_text(good.replace('(30, 0)', '(35, 0)'), encoding='utf-8')
        result, _, _ = run_quiet(codex_distances.run, claims=str(claims))
        self.assertEqual([d['field'] for d in result['differences']], ['coordinates/page'])


class PropertyTests(ProjectCase):
    def setup_live(self, catalog, **overrides):
        self.make_project(**overrides)
        self.put('live-parts.json', [dict(part('R1', 0, 0, [], {'Value': '1k', 'Old Key': 'x'}, supplier='C9'), page='p1'),
                                     dict(part('LOGO1', 0, 0, [], {}, supplier=None), page='p1')])
        self.put('catalog.json', catalog)

    def test_renamed_keys_come_from_config(self):
        catalog = {'C9': [{'supplierId': 'C9', 'otherProperty': {'New Key': 'x'}}]}
        self.setup_live(catalog, nonelectrical_refs=['LOGO1'], audit={'renamed_catalog_keys': {'R1': {'Old Key': 'New Key'}}})
        result, _, _ = run_quiet(codex_properties.run)
        self.assertEqual(result['keys_not_in_current_catalog'], {'R1': {'Old Key': 'x'}})
        self.assertEqual(result['recognized_renamed_catalog_keys'], {'R1': {'Old Key': 'New Key'}})
        self.assertEqual(result['skipped_nonelectrical'], ['LOGO1'])

    def test_default_missing_entries_and_bad_types(self):
        catalog = {'C9': [{'supplierId': 'C9', 'otherProperty': {}}]}
        self.setup_live(catalog, nonelectrical_refs=['LOGO1'])
        self.assertEqual(run_quiet(codex_properties.run)[0]['recognized_renamed_catalog_keys'], {})
        self.setup_live(catalog)                                                    # LOGO1 没登记成非电气件
        self.assertExits(codex_properties.run, says='LOGO1')
        self.setup_live(catalog, nonelectrical_refs='LOGO1')
        self.assertExits(codex_properties.run, says='nonelectrical_refs')
        self.setup_live(catalog, nonelectrical_refs=['LOGO1'], audit={'renamed_catalog_keys': {'R1': 'New Key'}})
        self.assertExits(codex_properties.run, says='renamed_catalog_keys')


class IncrementTests(ProjectCase):
    def setup_live(self, **overrides):
        self.make_project(**overrides)
        self.put('parsed.json', current_parsed())
        old = self.root / 'netlist' / 'FREEZE-old.enet.txt'
        old.write_text(OLD_NETLIST, encoding='utf-8')
        return str(old)

    def test_report_only_without_expectations(self):
        old = self.setup_live()
        rc, _, _ = run_quiet(codex_increment_check.main, [old])
        self.assertEqual(rc, 0)
        out = self.load('increment-check.json')
        self.assertEqual(out['added_refs'], ['C2', 'C3'])
        self.assertEqual(out['expectations']['checked'], 0)
        self.assertEqual(out['snapshot_counts'], {'FREEZE-old.enet.txt': [2, 2]})
        self.assertEqual(sorted(out['power_capacitors_by_net_pair']), ['+3V3 / GND', 'GND / NET_EN', 'GND / VBUS'])

    def test_expectations_from_config(self):
        expect = {'_说明': '本轮新增 C2、C3', 'ref_delta': 2, 'net_delta': 2, 'bypass_caps': 2, 'non_bypass_caps': 1,
                  'nets': {'NET_EN': ['C2-1', 'U1-3']}, 'pins': {'C3': {'1': 'VBUS'}}}
        old = self.setup_live(audit={'increment': {'non_bypass_nets': ['NET_EN'], 'expect': expect}})
        rc, _, _ = run_quiet(codex_increment_check.main, [old])
        self.assertEqual(rc, 0)
        self.assertEqual(self.load('increment-check.json')['expectations'], {'checked': 6, 'failed': []})   # _说明 是注释，不算
        self.assertEqual([c['ref'] for c in self.load('increment-check.json')['non_bypass_capacitors']], ['C2'])

    def test_failed_expectation_and_command_line_override(self):
        old = self.setup_live(audit={'increment': {'non_bypass_refs': ['C3']}})
        wrong = self.root / 'expect.json'
        wrong.write_text(json.dumps({'ref_delta': 5, 'pins': {'C3': {'1': 'GND'}}}), encoding='utf-8')
        rc, _, _ = run_quiet(codex_increment_check.main, [old, '--expect', str(wrong)])
        self.assertEqual(rc, 1)
        failed = self.load('increment-check.json')['expectations']['failed']
        self.assertEqual([f['key'] for f in failed], ['ref_delta', 'pins.C3-1'])
        wrong.write_text(json.dumps({'typo_key': 1}), encoding='utf-8')
        self.assertExits(codex_increment_check.main, [old, '--expect', str(wrong)], says='typo_key')

    def test_config_value_types(self):
        cases = [({'non_bypass_nets': 'NET_EN'}, 'non_bypass_nets'),
                 ({'non_bypass_refs': ['C1', 3]}, 'non_bypass_refs'),
                 ({'expect': {'ref_delta': '2'}}, 'ref_delta'),
                 ({'expect': {'bypass_caps': True}}, 'bypass_caps'),
                 ({'expect': {'nets': {'NET_EN': 'C2-1'}}}, 'nets.NET_EN'),
                 ({'expect': {'nets': ['NET_EN']}}, 'expect.nets'),
                 ({'expect': {'pins': {'C3': 'VBUS'}}}, 'expect.pins'),
                 ({'expect': ['ref_delta']}, 'expect')]
        for increment, says in cases:
            with self.subTest(increment=increment):
                old = self.setup_live(audit={'increment': increment})
                self.assertExits(codex_increment_check.main, [old], says=says)
                self.assertFalse((self.live / 'increment-check.json').exists())   # 先查配置，没读到一半才崩

    def test_usage_errors(self):
        self.setup_live()
        self.assertExits(codex_increment_check.main, [], says='用法')
        self.assertExits(codex_increment_check.main, [str(self.root / 'missing.enet.txt')], says='missing.enet.txt')


# ── 采集脚本：假桥接，跑通门禁 → 逐页拉 → 几何 ───────────────────────────────

class Resp(io.BytesIO):
    """urlopen 的假响应：能 read()，能当上下文管理器。"""
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeBridge:
    """离线假桥接：按 JS 代码的特征回话；不碰网络。pages = {页 uuid: 这一页的元件列表}。"""
    def __init__(self, project_uuid, pages, netlist):
        self.project_uuid, self.pages, self.netlist = project_uuid, pages, netlist
        self.current, self.codes = 'user-doc', []

    def __call__(self, req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if url.endswith('/health'):
            return Resp(json.dumps({'service': 'easyeda-bridge', 'edaConnected': True, 'edaWindowCount': 1,
                                    'activeWindowId': 'w1'}).encode('utf-8'))
        code = json.loads(req.data.decode('utf-8'))['code']
        self.codes.append(code)
        opened = re.search(r"openDocument\('([^']+)'\)", code)
        if opened:
            self.current = opened.group(1)
            return self.ok(True)
        activated = re.search(r'activateDocument\("tab-([^"]+)"\)', code)
        if activated:
            self.current = activated.group(1)
            return self.ok(True)
        if 'TARGET_PAGE' in code:
            return self.ok({'document': {'uuid': self.current}, 'components': self.pages[self.current], 'wires': []})
        if 'getNetlistFile' in code:
            return self.ok({'s': self.netlist})
        if 'getCurrentProjectInfo' in code:
            return self.ok({'project': self.project_uuid, 'document': {'uuid': self.current}})
        if 'getCurrentDocumentInfo' in code:
            return self.ok({'uuid': self.current, 'tabId': 'tab-' + self.current, 'documentType': 1})
        raise AssertionError('假桥接没见过这段代码：' + code[:80])

    @staticmethod
    def ok(value):
        return Resp(json.dumps({'success': True, 'result': value}).encode('utf-8'))


class CollectorTests(ProjectCase):
    def setUp(self):
        for target in ('codex_independent_audit.time.sleep', 'codex_independent_audit.atexit.register'):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def setup_project(self):
        self.make_project(project_uuid='proj-1')
        (self.root / 'netlist' / 'FREEZE-20260101-0000.enet.txt').write_bytes(OLD_NETLIST.encode('utf-8'))
        return FakeBridge('proj-1', {'page-a': [part('U1', 0, 0, [('1', 0, 0), ('2', 10, 0)])],
                                     'page-b': [part('C1', 0, 0, [('1', 0, 0), ('2', 10, 0)], {'Value': '100nF'})]},
                          OLD_NETLIST)

    def gate_and_restore(self):
        """跑门禁，再手动执行它登记的"退出时切回原文档"（atexit 在测试里被换掉了）。"""
        run_quiet(codex_independent_audit.freeze_gate)
        fn, *args = codex_independent_audit.atexit.register.call_args.args
        run_quiet(fn, *args)

    def test_gate_pull_and_geometry_offline(self):
        bridge = self.setup_project()
        with mock.patch('urllib.request.urlopen', side_effect=bridge):
            self.gate_and_restore()
            self.assertEqual(bridge.current, 'user-doc')
            gate = self.load('freeze-gate.json')
            self.assertTrue(gate['pass'])
            self.assertEqual(gate['frozen_file'], 'FREEZE-20260101-0000.enet.txt')
            self.assertTrue(any("getNetlistFile('codex-independent','Protel2')" in c for c in bridge.codes))
            self.put('page-p3.json', page_record(uuid='page-x'))                    # 上一轮剩下的页：这一轮要清掉
            run_quiet(codex_independent_audit.pull_pages)
        self.assertEqual(bridge.current, 'user-doc')                                 # 切回了运行前的文档
        self.assertFalse((self.live / 'page-p3.json').exists())
        self.assertEqual([self.load('page-p%d.json' % i)['response']['result']['document']['uuid'] for i in (1, 2)],
                         ['page-a', 'page-b'])
        self.put('parsed.json', parse(OLD_NETLIST))
        run_quiet(codex_geometry.run)
        self.assertEqual([(c['ref'], c['page']) for c in self.load('live-parts.json')], [('U1', 'p1'), ('C1', 'p2')])

    def test_stale_gate_record_is_refused(self):
        bridge = self.setup_project()
        with mock.patch('urllib.request.urlopen', side_effect=bridge):
            self.gate_and_restore()
            (self.root / 'netlist' / 'FREEZE-20260102-0000.enet.txt').write_bytes(OLD_NETLIST.encode('utf-8'))
            self.assertExits(codex_independent_audit.pull_pages, says='重跑门禁')     # 有了更新的冻结（内容一样也不行）
        gate = self.load('freeze-gate.json')
        gate['frozen_file'] = 'FREEZE-20260102-0000.enet.txt'
        gate['frozen_sha256'] = hashlib.sha256(b'something else').hexdigest()        # 名字对、内容不对
        self.put('freeze-gate.json', gate)
        with mock.patch('urllib.request.urlopen', side_effect=AssertionError('不该连桥接')):
            self.assertExits(codex_independent_audit.pull_pages, says='重跑门禁')

    def test_unreachable_bridge_leaves_a_record_and_exits_2(self):
        self.setup_project()
        refused = urllib.error.URLError('connection refused')
        with mock.patch('urllib.request.urlopen', side_effect=refused):
            self.assertExits(codex_independent_audit.freeze_gate, says='连不上桥接')
            self.put('freeze-gate.json', {'pass': True, 'frozen_file': 'FREEZE-20260101-0000.enet.txt',
                                          'frozen_sha256': hashlib.sha256(OLD_NETLIST.encode('utf-8')).hexdigest()})
            self.assertExits(codex_independent_audit.pull_pages, says='连不上桥接')
        self.assertIn('connection refused', self.load('doc-before-pages.json')['url_error'])

    def test_http_error_on_a_page_is_recorded_and_geometry_refuses_it(self):
        bridge = self.setup_project()

        def flaky(req, timeout=None):
            if not isinstance(req, str) and b'TARGET_PAGE' in req.data and bridge.current == 'page-b':
                raise urllib.error.HTTPError(req.full_url, 500, 'Internal Server Error', {}, io.BytesIO(b'bridge timeout'))
            return bridge(req, timeout)

        with mock.patch('urllib.request.urlopen', side_effect=flaky):
            self.gate_and_restore()
            self.assertExits(codex_independent_audit.pull_pages, says='HTTP 500')
        self.assertEqual(self.load('page-p2.json')['http_error'], 500)
        self.assertEqual(bridge.current, 'user-doc')                                 # 失败了也切回原文档
        self.put('parsed.json', parse(OLD_NETLIST))
        self.assertExits(codex_geometry.run, says='page-p2.json')

    def test_config_problems_surface_before_any_bridge_call(self):
        with mock.patch('urllib.request.urlopen', side_effect=AssertionError('bridge called')):
            self.make_project()                                                     # 网表目录里还没有冻结文件
            self.assertExits(codex_independent_audit.freeze_gate, says='FREEZE-*.enet.txt')
            self.make_project(pages=[])                                             # 有冻结文件，但没配页
            (self.root / 'netlist' / 'FREEZE-a.enet.txt').write_text(OLD_NETLIST, encoding='utf-8')
            self.assertExits(codex_independent_audit.freeze_gate, says='pages')
            self.assertExits(codex_independent_audit.pull_pages, says='freeze-gate.json')


# ── 命令行：真起子进程，看退出码、stderr 和有没有 Traceback ─────────────────────

class CliTests(ProjectCase):
    def cli(self, script, *args):
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONUTF8', 'PYTHONIOENCODING')}
        run = subprocess.run([sys.executable, '-B', str(HERE / script), *args], cwd=self.root, env=env,
                             capture_output=True, timeout=120)
        return run.returncode, run.stdout.decode('utf-8', 'replace'), run.stderr.decode('utf-8', 'replace')

    def test_missing_config_or_inputs_exit_2_with_one_line_and_no_traceback(self):
        self.make_project(pages=[])
        cases = [('codex_geometry.py', (), 'pages 是空的'),
                 ('codex_distances.py', (), 'audit.distances.pins'),
                 ('codex_properties.py', (), 'live-parts.json'),
                 ('codex_increment_check.py', (), '用法'),
                 ('codex_parse_netlist.py', (), 'live.enet.txt'),
                 ('codex_independent_audit.py', (), 'FREEZE-*.enet.txt'),
                 ('codex_independent_audit.py', ('pages',), 'freeze-gate.json')]
        for script, args, says in cases:
            with self.subTest(script=script, args=args):
                rc, out, err = self.cli(script, *args)
                self.assertEqual(rc, 2, out + err)
                self.assertIn(says, err)
                self.assertNotIn('Traceback', err)

    def test_broken_project_json_exits_2_without_traceback(self):
        self.make_project(raw='{"pages": ')
        rc, out, err = self.cli('codex_geometry.py')
        self.assertEqual(rc, 2, out + err)
        self.assertIn('project.json', err)
        self.assertNotIn('Traceback', err)

    def test_output_survives_characters_outside_the_local_codepage(self):
        self.make_project(nonelectrical_refs=[])
        self.put('live-parts.json', [dict(part('C1', 0, 0, [], {'Value': '10µF', 'Note': '✅ 已核对'}, supplier='C9'), page='p1')])
        self.put('catalog.json', {'C9': [{'supplierId': 'C9', 'otherProperty': {}}]})
        rc, out, err = self.cli('codex_properties.py')
        self.assertEqual(rc, 0, out + err)
        self.assertIn('✅ 已核对', out)


if __name__=='__main__':unittest.main()
