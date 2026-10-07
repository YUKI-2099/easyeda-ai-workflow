"""Offline-only regressions; all transport is mocked and JS uses local mock EDA."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import pcb_route_batch as route


def fixture():
    return {
        'components': [{'primitiveId': 'C', 'x': 0, 'y': 0, 'rotation': 0, 'layer': 1,
                        'designator': 'J1', 'footprint': {'uuid': 'fp'}, 'extra': {'rating': 'A'}}],
        'pads': [{'primitiveId': 'Cp1', 'x': 0, 'y': 0, 'net': 'SIG', 'layer': 1,
                  'padNumber': '1', 'pad': ['RECT', 10, 10]},
                 {'primitiveId': 'Cp2', 'x': 100, 'y': 100, 'net': 'GND', 'layer': 1,
                  'padNumber': '2', 'pad': ['RECT', 10, 10]}],
        'lines': [], 'vias': [], 'arcs': [], 'pours': [], 'regions': [],
        'layers': [{'id': i, 'type': 'SIGNAL', 'layerStatus': 1 if i < 3 else 2} for i in (1, 2, 15, 16)]}


def plan_fixture(count=1, vias=0):
    return {'coordinate_units': 'mil', 'lines': [dict(net='SIG', layer=1, startX=i*20,
            startY=0, endX=i*20+10, endY=10, lineWidth=8) for i in range(count)],
            'vias': [dict(net='SIG', x=i*30, y=50, holeDiameter=12, diameter=24) for i in range(vias)]}


class FakeTransport:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def request(self, method, endpoint, payload):
        self.calls.append((method, endpoint, payload))
        result = self.replies.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


HEALTH = {'service': 'easyeda-bridge', 'edaConnected': True, 'edaWindowCount': 1}


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = fixture()
        self.nets = ['PROTEL A\r\n', 'PROTEL B\n']
        self.out = self.root/'evidence'
        self.lock = self.root/'eda-lock.json'
        self.project = self.root/'project.json'
        self.project.write_text(json.dumps({'project_uuid': 'project-test'}), encoding='utf-8')
        for name, value in [('base.json', self.base), ('nets.json', self.nets), ('plan.json', plan_fixture())]:
            (self.root/name).write_text(json.dumps(value), encoding='utf-8')
        route.write_new(self.lock, {'owner': 'unit-test', 'scope': 'project-test/pcb-test', 'heartbeat': route.now()})

    def tearDown(self):
        self.temp.cleanup()

    def compile(self, plan=None, batch_size=20):
        if plan is not None:
            (self.root/'plan.json').write_text(json.dumps(plan), encoding='utf-8')
        args = argparse.Namespace(project=str(self.project), pcb_uuid='pcb-test', owner='unit-test',
             baseline=str(self.root/'base.json'), frozen_netlist=str(self.root/'nets.json'),
             plan=str(self.root/'plan.json'), evidence=str(self.out), batch_size=batch_size,
             bridge='http://127.0.0.1:49620', lock_file=str(self.lock))
        with mock.patch('urllib.request.urlopen', side_effect=AssertionError('Dry run made network call')):
            result = route.compile_plan(args)
        return result

    def addition(self, index=0):
        manifest = route.read_json(self.out/'manifest.json')
        ops = manifest['batches'][index]
        after, created = copy.deepcopy(self.base), []
        for i, op in enumerate(ops):
            obj = dict(op['params'], primitiveId='new-'+str(i), primitiveLock=False,
                       primitiveType='Line' if op['kind']=='line' else 'Via')
            if op['kind']=='via': obj['viaType'] = 0
            created.append(copy.deepcopy(obj))
            after[op['kind']+'s'].append(obj)
        return ops, created, after

    def run_batch(self, transport):
        return route.live_step(self.out, 'unit-test', 'http://127.0.0.1:49620', apply_one=0, transport=transport)

    def test_dry_run_zero_network_and_mixed_total_batch_size(self):
        result = self.compile(plan_fixture(21, 22))
        manifest = route.read_json(self.out/'manifest.json')
        self.assertEqual([len(x) for x in manifest['batches']], [20, 20, 3])
        self.assertEqual(result['primitives'], 43)
        self.assertIn('__EXPECTED_FROM_PREVIOUS_VERIFIED_READBACK__', (self.out/'compiled/batch-001.js').read_text())

    def test_invalid_angle_layer_and_batch_size_fail_before_creating_evidence(self):
        for change in ({'endY': 7}, {'layer': 3}, {'layer': 17}, {'endX': 0, 'endY': 0}):
            plan = plan_fixture()
            plan['lines'][0].update(change)
            with self.assertRaises(route.RouteError):
                route.validate_plan(plan, self.base, 20)
        with self.assertRaises(route.RouteError): route.validate_plan(plan_fixture(), self.base, 21)
        self.assertFalse(self.out.exists())

    def test_duplicate_unknown_net_nan_and_noncopper_rejected(self):
        plan = plan_fixture(); plan['lines'].append(copy.deepcopy(plan['lines'][0]))
        with self.assertRaises(route.RouteError): route.validate_plan(plan, self.base, 20)
        for change in ({'net': 'UNKNOWN'}, {'startX': float('nan')}, {'lineWidth': -1}):
            plan = plan_fixture(); plan['lines'][0].update(change)
            with self.assertRaises(route.RouteError): route.validate_plan(plan, self.base, 20)
        with self.assertRaises(route.RouteError): route.frozen_nets('')

    def test_frozen_netlist_preserves_raw_type_body_and_list_members(self):
        raw = '  PROTEL BA\r\n中文\n '
        self.assertIsInstance(route.frozen_nets(raw), str)
        self.assertEqual(route.frozen_nets(raw), raw)
        values = ['B\r\n', 'A\n', 'A\n']
        original = values[:]
        self.assertIsInstance(route.frozen_nets(values), list)
        self.assertEqual(route.frozen_nets(values), ['A\n', 'A\n', 'B\r\n'])
        self.assertEqual(values, original)
        # Whitespace is body content, not a reason to trim or rewrite the export.
        self.assertEqual(route.frozen_nets(' \r\n'), ' \r\n')

    def test_string_compile_and_live_readback_preserve_raw_type(self):
        self.nets = '  PROTEL BA\r\n中文\n '
        (self.root/'nets.json').write_text(json.dumps(self.nets), encoding='utf-8')
        self.compile()
        frozen = route.read_json(self.out/'frozen-netlist.json')
        self.assertIsInstance(frozen, str)
        self.assertEqual(frozen, self.nets)
        _, created, after = self.addition()
        result = self.run_batch(FakeTransport([HEALTH, {'result': created},
                    {'result': {'snapshot': after, 'netlist': self.nets}}]))
        self.assertEqual(result['created'], 1)
        self.assertEqual(route.read_json(self.out/'live/apply-000/netlist.json'), self.nets)

    def test_invalid_frozen_netlist_rejected_before_compile_output(self):
        for value in ('', [], [''], ['valid', ''], ['valid', 1], ['valid', None],
                      ['valid', True], None, {}, 0, False):
            with self.subTest(value=value):
                (self.root/'nets.json').write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(route.RouteError): self.compile()
                self.assertFalse(self.out.exists())

    def test_readback_rejects_netlist_body_type_empty_and_subset_drift(self):
        self.compile()
        ops, created, after = self.addition()
        raw = '  BA\r\n '
        cases = [(raw, '  AB\r\n '), (raw, raw.strip()), (raw, raw.replace('\r\n', '\n')),
                 (raw, raw+'\n'), (raw, [raw]), ([raw], raw),
                 (['REFERENCE', raw], [raw]), (['REFERENCE', raw], raw),
                 (raw, ''), (raw, None), ([raw], []), ([raw], ['']),
                 ([raw], [raw, 1]), ([raw], [raw, None]), ([raw], [raw, True])]
        for expected, actual in cases:
            with self.subTest(expected=expected, actual=actual):
                with self.assertRaises(route.RouteError):
                    route.validate_readback(self.base, after, ops, created, actual, expected)

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable: JS semantics test skipped')
    def test_generated_javascript_preserves_string_and_list_netlist_contracts(self):
        raw = '  BA\r\n中文\n '
        cases = [(raw, raw, True), ([raw, 'REFERENCE'], ['REFERENCE', raw], True),
                 ([raw, raw], [raw, raw], True),
                 (raw, raw.replace('BA', 'AB'), False), (raw, raw.strip(), False),
                 (raw, raw.replace('\r\n', '\n'), False), (raw, raw+'\n', False),
                 (raw, [raw], False), ([raw], raw, False),
                 (['REFERENCE', raw], [raw], False), (['REFERENCE', raw], raw, False),
                 ([raw, raw], [raw], False),
                 (raw, '', False), (raw, None, False), (raw, 1, False),
                 ([raw], [], False), ([raw], [''], False), ([raw], [raw, ''], False),
                 ([raw], [raw, 1], False), ([raw], [raw, None], False),
                 ([raw], [raw, True], False), ([raw], {}, False)]
        self.nets = raw
        (self.root/'nets.json').write_text(json.dumps(raw), encoding='utf-8')
        self.compile()
        compiled_string = (self.out/'compiled/batch-000.js').read_text(encoding='utf-8')
        manifest = route.read_json(self.out/'manifest.json')
        payload = []
        for frozen, actual, expected_ok in cases:
            code = compiled_string if isinstance(frozen, str) else route.batch_js(
                manifest, self.base, frozen, manifest['batches'][0])
            payload.append(dict(code=code, nets=actual, expected_ok=expected_ok,
                                snapshot=self.base, modules=route.MODULES))
        harness = r'''
const fs=require('fs');const inputs=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{const results=[];for(const input of inputs){let writes=0;const eda={
 dmt_Project:{getCurrentProjectInfo:async()=>({uuid:'project-test'})},
 dmt_SelectControl:{getCurrentDocumentInfo:async()=>({uuid:'pcb-test'})},
 pcb_Net:{getNetlist:async()=>input.nets}};
 for(const [name,module] of Object.entries(input.modules))eda[module]={getAll:async()=>input.snapshot[name],create:async()=>{writes++;return {primitiveId:'created'}}};
 try{await new (Object.getPrototypeOf(async function(){}).constructor)('eda',input.code)(eda);results.push({ok:true,writes});}
 catch(e){results.push({ok:false,writes,error:String(e)});}
}process.stdout.write(JSON.stringify(results));})();'''
        run = subprocess.run([shutil.which('node'), '-e', harness], input=json.dumps(payload),
                             text=True, capture_output=True, check=True)
        results = json.loads(run.stdout)
        self.assertEqual(len(results), len(cases))
        for case, result in zip(cases, results):
            with self.subTest(frozen=case[0], actual=case[1]):
                self.assertEqual(result['ok'], case[2])
                self.assertEqual(result['writes'], 1 if case[2] else 0)
                if not case[2]: self.assertIn('netlist drift', result['error'])

    def test_compile_never_overwrites(self):
        self.compile()
        before = (self.out/'manifest.json').read_bytes()
        with self.assertRaises(FileExistsError): self.compile()
        self.assertEqual(before, (self.out/'manifest.json').read_bytes())

    def test_scope_netlist_and_all_fields_present_before_create(self):
        self.compile()
        code = (self.out/'compiled/batch-000.js').read_text()
        for token in ('scope drift', 'netlist drift', 'footprint', 'rating', 'padNumber', 'getNetlist("Protel2")'):
            self.assertIn(token, code)
        self.assertLess(code.index('netlist drift'), code.index('.create('))
        for name in route.GROUPS: self.assertIn('geometry drift: '+name, code)
        for forbidden in ('.modify(', '.delete(', 'openDocument', 'closeDocument', 'activateDocument', 'rebuild'):
            self.assertNotIn(forbidden, code)

    def test_success_has_separate_readback_and_replay_is_rejected(self):
        self.compile(plan_fixture(1, 1))
        _, created, after = self.addition()
        transport = FakeTransport([HEALTH, {'result': created}, {'result': {'snapshot': after, 'netlist': self.nets[::-1]}}])
        result = self.run_batch(transport)
        self.assertEqual(result['created'], 2)
        self.assertEqual(len(transport.calls), 3)
        self.assertIn('.create(', transport.calls[1][2]['code'])
        self.assertNotIn('.create(', transport.calls[2][2]['code'])
        before = (self.out/'live/apply-000/created.json').read_bytes()
        with self.assertRaises(route.RouteError): self.run_batch(transport)
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(before, (self.out/'live/apply-000/created.json').read_bytes())

    def test_second_batch_uses_actual_previous_ids_and_live_geometry(self):
        self.compile(plan_fixture(21))
        _, created, after = self.addition()
        self.run_batch(FakeTransport([HEALTH, {'result': created}, {'result': {'snapshot': after, 'netlist': self.nets}}]))
        manifest = route.read_json(self.out/'manifest.json')
        op = manifest['batches'][1][0]
        second = dict(op['params'], primitiveId='new-20', primitiveLock=False, primitiveType='Line')
        final = copy.deepcopy(after); final['lines'].append(second)
        transport = FakeTransport([HEALTH, {'result': [second]}, {'result': {'snapshot': final, 'netlist': self.nets}}])
        result = route.live_step(self.out, 'unit-test', 'http://127.0.0.1:49620', apply_one=1, transport=transport)
        self.assertEqual(result['remaining_batches'], 0)
        code = transport.calls[1][2]['code']
        self.assertIn('new-19', code)
        self.assertNotIn('__EXPECTED_FROM_PREVIOUS_VERIFIED_READBACK__', code)

    def test_local_command_lease_and_manifest_hash_prevent_conflict(self):
        self.compile()
        route.write_new(self.out/'ACTIVE.json', {'token': 'another-command'})
        with self.assertRaises(FileExistsError): self.run_batch(FakeTransport([]))
        self.assertEqual(route.read_json(self.out/'ACTIVE.json')['token'], 'another-command')
        (self.out/'ACTIVE.json').unlink()
        m = route.read_json(self.out/'manifest.json'); m['pcb_uuid'] = 'different'
        (self.out/'manifest.json').write_text(json.dumps(m), encoding='utf-8')
        with self.assertRaises(route.RouteError): self.run_batch(FakeTransport([]))

    def test_import_zero_stdout_and_no_http_retry(self):
        imported = subprocess.run([sys.executable, '-c', 'import pcb_route_batch'], cwd=Path(route.__file__).parent,
                                  text=True, capture_output=True, check=True)
        self.assertEqual(imported.stdout, '')
        self.assertEqual(imported.stderr, '')
        with mock.patch('urllib.request.urlopen', side_effect=TimeoutError('wire timeout')) as call:
            with self.assertRaises(TimeoutError):
                route.HttpTransport('http://127.0.0.1:49620').request('POST', '/execute', {'code': 'return true;'})
            self.assertEqual(call.call_count, 1)

    def test_timeout_never_retries_or_replays(self):
        self.compile()
        transport = FakeTransport([HEALTH, TimeoutError('test timeout')])
        with self.assertRaises(TimeoutError): self.run_batch(transport)
        self.assertEqual(len(transport.calls), 2)
        self.assertTrue((self.out/'BLOCKED.json').exists())
        self.assertTrue((self.out/'live/apply-000/002-error.json').exists())
        with self.assertRaises(route.RouteError): self.run_batch(transport)
        self.assertEqual(len(transport.calls), 2)

    def test_partial_bridge_error_retains_raw_response_and_blocks(self):
        self.compile()
        transport = FakeTransport([HEALTH, {'success': False, 'error': 'mock partial failure'}])
        with self.assertRaises(route.RouteError): self.run_batch(transport)
        raw = route.read_json(self.out/'live/apply-000/002-response.json')
        self.assertEqual(raw['error'], 'mock partial failure')
        self.assertTrue((self.out/'BLOCKED.json').exists())

    def test_other_owner_or_stale_lock_rejected_without_bridge_call(self):
        self.compile()
        transport = FakeTransport([])
        for owner, heartbeat in [('someone-else', route.now()), ('unit-test', '2000-01-01T00:00:00+00:00')]:
            self.lock.write_text(json.dumps({'owner': owner, 'scope': 'project-test/pcb-test', 'heartbeat': heartbeat}), encoding='utf-8')
            with self.assertRaises(route.RouteError): self.run_batch(transport)
        self.assertFalse(transport.calls)

    def test_readback_extra_field_geometry_or_net_change_blocks(self):
        self.compile()
        ops, created, after = self.addition()
        changed = copy.deepcopy(after); changed['components'][0]['extra']['rating'] = 'B'
        with self.assertRaises(route.RouteError): route.validate_readback(self.base, changed, ops, created, self.nets, self.nets)
        changed = copy.deepcopy(after); changed['pads'][0]['pad'][1] = 20
        with self.assertRaises(route.RouteError): route.validate_readback(self.base, changed, ops, created, self.nets, self.nets)
        changed = copy.deepcopy(after); changed['lines'][0]['endY'] += .2
        with self.assertRaises(route.RouteError): route.validate_readback(self.base, changed, ops, created, self.nets, self.nets)
        with self.assertRaises(route.RouteError): route.validate_readback(self.base, after, ops, created, ['changed'], self.nets)

    def test_incomplete_attempt_blocks_checkpoint(self):
        self.compile()
        (self.out/'live/apply-000').mkdir()
        with self.assertRaises(route.RouteError):
            route.live_step(self.out, 'unit-test', 'http://127.0.0.1:49620', checkpoint=True, transport=FakeTransport([]))

    def test_checkpoint_saves_then_reads_then_drc_without_reopen(self):
        self.compile()
        transport = FakeTransport([HEALTH, {'result': True}, {'result': {'snapshot': self.base, 'netlist': self.nets}}, {'result': []}])
        result = route.live_step(self.out, 'unit-test', 'http://127.0.0.1:49620', checkpoint=True, transport=transport)
        self.assertTrue(result['drc_is_clean'])
        self.assertEqual(len(transport.calls), 4)
        self.assertIn('pcb_Document.save', transport.calls[1][2]['code'])
        self.assertIn('const snapshot=', transport.calls[2][2]['code'])
        self.assertIn('pcb_Drc.check', transport.calls[3][2]['code'])
        self.assertFalse(any('openDocument' in str(c) or 'closeDocument' in str(c) for c in transport.calls))
        with self.assertRaises(FileExistsError):
            route.live_step(self.out, 'unit-test', 'http://127.0.0.1:49620', checkpoint=True, transport=transport)

    def test_false_drc_is_not_clean(self):
        self.compile()
        transport = FakeTransport([HEALTH, {'result': True}, {'result': {'snapshot': self.base, 'netlist': self.nets}}, {'result': False}])
        with self.assertRaises(route.RouteError):
            route.live_step(self.out, 'unit-test', 'http://127.0.0.1:49620', checkpoint=True, transport=transport)
        self.assertTrue((self.out/'BLOCKED.json').exists())

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable: JS semantics test skipped')
    def test_generated_javascript_rejects_scope_net_and_full_field_drift(self):
        self.compile()
        code = (self.out/'compiled/batch-000.js').read_text()
        harness = r'''
const fs=require('fs');const input=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{let writes=0;const eda={
 dmt_Project:{getCurrentProjectInfo:async()=>({uuid:input.scope?'wrong':'project-test'})},
 dmt_SelectControl:{getCurrentDocumentInfo:async()=>({uuid:'pcb-test'})},
 pcb_Net:{getNetlist:async()=>input.net?['different']:input.nets}};
 for(const [name,module] of Object.entries(input.modules))eda[module]={getAll:async()=>input.snapshot[name],create:async()=>{writes++;return {primitiveId:'created'}}};
 try{await new (Object.getPrototypeOf(async function(){}).constructor)('eda',input.code)(eda);process.stdout.write(JSON.stringify({ok:true,writes}));}
 catch(e){process.stdout.write(JSON.stringify({ok:false,writes,error:String(e)}));}
})();'''
        for scope, net, change in [(False, False, False), (True, False, False), (False, True, False), (False, False, True)]:
            snap = copy.deepcopy(self.base)
            if change: snap['components'][0]['extra']['rating'] = 'B'
            payload = dict(code=code, snapshot=snap, nets=self.nets, scope=scope, net=net, modules=route.MODULES)
            run = subprocess.run([shutil.which('node'), '-e', harness], input=json.dumps(payload), text=True, capture_output=True, check=True)
            result = json.loads(run.stdout)
            self.assertEqual(result['writes'], 0 if scope or net or change else 1)
            self.assertEqual(result['ok'], not(scope or net or change))


if __name__ == '__main__':
    unittest.main()
