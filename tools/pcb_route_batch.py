"""Additive PCB routing batches: offline compile by default, explicit live steps.

Only standard-library dependencies. Importing this module performs no I/O.
No delete/modify, automatic replay, bridge startup, document/window switching,
or close/reopen APIs are provided. See PCB_ROUTING.md for operating limits.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

from project import load_project

VERSION = 1
GROUPS = ('components', 'pads', 'lines', 'vias')
MODULES = dict(zip(GROUPS, ('pcb_PrimitiveComponent', 'pcb_PrimitivePad',
                          'pcb_PrimitiveLine', 'pcb_PrimitiveVia')))
REQUIRED = {
    'components': ('primitiveId', 'x', 'y', 'rotation', 'layer'),
    'pads': ('primitiveId', 'x', 'y', 'net', 'layer', 'padNumber'),
    'lines': ('primitiveId', 'net', 'layer', 'startX', 'startY', 'endX', 'endY', 'lineWidth'),
    'vias': ('primitiveId', 'net', 'x', 'y', 'holeDiameter', 'diameter'),
}
LINE_KEYS = ('net', 'layer', 'startX', 'startY', 'endX', 'endY', 'lineWidth')
VIA_KEYS = ('net', 'x', 'y', 'holeDiameter', 'diameter')
NEW_TOL_MIL = 0.001  # getAll round-trip geometry, not a DRC clearance allowance.


class RouteError(RuntimeError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_new(path, value):
    """Every evidence file is exclusive-create; never replace previous evidence."""
    with Path(path).open('x', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frozen_nets(value):
    """Keep the raw return type and body; canonicalize only an outer list."""
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list) and value and all(isinstance(v, str) and v for v in value):
        return sorted(value)  # Never trim, rewrite, or sort characters in a body.
    raise RouteError('Frozen PCB Protel2 must be a nonempty raw string or nonempty JSON list of nonempty raw strings')


def snapshot_ok(value):
    if not isinstance(value, dict):
        raise RouteError('Snapshot must be an object')
    for group in GROUPS:
        rows = value.get(group)
        if not isinstance(rows, list) or (group in ('components', 'pads') and not rows):
            raise RouteError('Missing or empty snapshot array: ' + group)
        ids = []
        for row in rows:
            if not isinstance(row, dict) or any(k not in row for k in REQUIRED[group]):
                raise RouteError('Incomplete primitive fields: ' + group)
            ident = row['primitiveId']
            if not isinstance(ident, str) or not ident:
                raise RouteError('Invalid primitiveId: ' + group)
            ids.append(ident)
        if len(ids) != len(set(ids)):
            raise RouteError('Duplicate primitiveId: ' + group)
    return value


def canonical_groups(snapshot):
    snapshot_ok(snapshot)
    # All serialized fields, including footprint references and pad shape,
    # not only x/y/net. Array order of getAll is immaterial; nested order is not.
    return {k: sorted(snapshot[k], key=lambda row: row['primitiveId']) for k in GROUPS}


def finite_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_plan(plan, baseline, batch_size):
    snapshot_ok(baseline)
    if not 1 <= batch_size <= 20:
        raise RouteError('Batch size must be 1..20 total primitives')
    if plan.get('coordinate_units', 'mil') != 'mil':
        raise RouteError('Plan coordinates and widths must be mil')
    if any(k in plan for k in ('delete', 'modify', 'deletes', 'modifications')):
        raise RouteError('Only additive lines/vias are supported')
    layers = {v['id'] for v in baseline.get('layers', [])
              if v.get('type') == 'SIGNAL' and v.get('layerStatus') in (1, 2)}
    if not layers:
        raise RouteError('Snapshot requires active SIGNAL copper layers from getAllLayers')
    nets = {v['net'] for key in ('pads', 'lines', 'vias') for v in baseline[key] if v.get('net')}
    operations, seen = [], set()
    for kind, keys in (('line', LINE_KEYS), ('via', VIA_KEYS)):
        rows = plan.get(kind + 's', [])
        if not isinstance(rows, list):
            raise RouteError(kind + 's must be an array')
        for row in rows:
            if any(k not in row for k in keys):
                raise RouteError('Missing planned ' + kind + ' fields')
            obj = {k: row[k] for k in keys}
            if not isinstance(obj['net'], str) or obj['net'] not in nets:
                raise RouteError('Plan net absent from existing copper/pads')
            if any(not finite_number(obj[k]) for k in keys if k != 'net'):
                raise RouteError('Non-finite or nonnumeric primitive coordinate')
            if kind == 'line':
                if obj['layer'] not in layers or int(obj['layer']) != obj['layer']:
                    raise RouteError('Plan line layer is not an enabled signal copper layer')
                dx, dy = abs(obj['endX']-obj['startX']), abs(obj['endY']-obj['startY'])
                if max(dx, dy) < 1e-6 or obj['lineWidth'] <= 0:
                    raise RouteError('Zero-length line or nonpositive width')
                if min(dx, dy) > 1e-4 and abs(dx-dy) > 1e-4:
                    raise RouteError('Line must be horizontal, vertical or 45 degrees')
                ends = sorted([(obj['startX'], obj['startY']), (obj['endX'], obj['endY'])])
                signature = (kind, obj['net'], obj['layer'], tuple(ends[0]), tuple(ends[1]), obj['lineWidth'])
            else:
                if not 0 < obj['holeDiameter'] < obj['diameter']:
                    raise RouteError('Via requires 0 < holeDiameter < diameter')
                signature = (kind, *(obj[k] for k in keys))
            if signature in seen:
                raise RouteError('Duplicate planned primitive')
            seen.add(signature)
            operations.append({'kind': kind, 'params': obj, 'label': row.get('label', '')})
    if not operations:
        raise RouteError('Plan has no additive primitives')
    return [operations[i:i+batch_size] for i in range(0, len(operations), batch_size)]


def scope_js(project_uuid, pcb_uuid):
    return ('const project=await eda.dmt_Project.getCurrentProjectInfo();'
            'const document=await eda.dmt_SelectControl.getCurrentDocumentInfo();'
            'if(!project||!document||project.uuid!==' + json.dumps(project_uuid) +
            '||document.uuid!==' + json.dumps(pcb_uuid) + ')throw Error("scope drift");')


def guard_js(manifest, expected, nets):
    payload = '__EXPECTED_FROM_PREVIOUS_VERIFIED_READBACK__' if expected is None else json.dumps(canonical_groups(expected), ensure_ascii=True, allow_nan=False)
    code = scope_js(manifest['project_uuid'], manifest['pcb_uuid'])
    code += 'const expected=' + payload + ';const frozen=' + json.dumps(frozen_nets(nets), ensure_ascii=True) + ';'
    code += ('const canon=v=>Array.isArray(v)?v.map(canon):v&&typeof v==="object"?'
             'Object.fromEntries(Object.keys(v).sort().map(k=>[k,canon(v[k])])):v;'
             'const stable=v=>JSON.stringify(canon(JSON.parse(JSON.stringify(v))));'
             'const net=await eda.pcb_Net.getNetlist("Protel2");'
             'const sameNet=typeof frozen==="string"?'
             'typeof net==="string"&&net.length>0&&net===frozen:'
             'Array.isArray(net)&&net.length>0&&net.every(v=>typeof v==="string"&&v.length>0)&&'
             'stable([...net].sort())===stable([...frozen].sort());'
             'if(!sameNet)throw Error("netlist drift");')
    for name, module in MODULES.items():
        code += ('const ' + name + '=await eda.' + module + '.getAll();'
                 'if(!Array.isArray(' + name + '))throw Error("invalid getAll");'
                 'const ordered_' + name + '=[...' + name + '].sort((a,b)=>a.primitiveId<b.primitiveId?-1:a.primitiveId>b.primitiveId?1:0);'
                 'if(stable(ordered_' + name + ')!==stable(expected.' + name + '))throw Error("geometry drift: ' + name + '");')
    # Scope is checked again after relatively expensive guard reads.
    code += ('const p2=await eda.dmt_Project.getCurrentProjectInfo();'
             'const d2=await eda.dmt_SelectControl.getCurrentDocumentInfo();'
             'if(p2.uuid!==project.uuid||d2.uuid!==document.uuid)throw Error("scope drift after guard");')
    return code


def batch_js(manifest, expected, nets, operations):
    code = guard_js(manifest, expected, nets) + 'const created=[];'
    for op in operations:
        keys = LINE_KEYS if op['kind'] == 'line' else VIA_KEYS
        args = [op['params'][k] for k in keys]
        args += [False] if op['kind'] == 'line' else [0, None, None, False]
        module = 'pcb_PrimitiveLine' if op['kind'] == 'line' else 'pcb_PrimitiveVia'
        code += ('{const r=await eda.' + module + '.create(...' + json.dumps(args, ensure_ascii=True) +
                 ');if(!r||typeof r.primitiveId!=="string")throw Error("empty create result");created.push(r);}')
    return code + 'return created;'


def readback_js(manifest):
    code = scope_js(manifest['project_uuid'], manifest['pcb_uuid']) + 'const snapshot={};'
    modules = dict(MODULES, arcs='pcb_PrimitiveArc', pours='pcb_PrimitivePour', regions='pcb_PrimitiveRegion')
    for key, module in modules.items():
        code += 'snapshot.' + key + '=await eda.' + module + '.getAll();'
    code += ('snapshot.layers=await eda.pcb_Layer.getAllLayers();'
             'const netlist=await eda.pcb_Net.getNetlist("Protel2");'
             'const p2=await eda.dmt_Project.getCurrentProjectInfo();'
             'const d2=await eda.dmt_SelectControl.getCurrentDocumentInfo();'
             'if(p2.uuid!==project.uuid||d2.uuid!==document.uuid)throw Error("scope drift during readback");'
             'return {snapshot,netlist};')
    return code


def validate_readback(before, after, operations, created, actual_nets, expected_nets):
    snapshot_ok(before)
    snapshot_ok(after)
    if frozen_nets(actual_nets) != frozen_nets(expected_nets):
        raise RouteError('Netlist changed after operation')
    if not isinstance(created, list) or len(created) != len(operations):
        raise RouteError('Create result count mismatch')
    allowed = {'lines': {}, 'vias': {}}
    for op, obj in zip(operations, created):
        if not isinstance(obj, dict) or not obj.get('primitiveId'):
            raise RouteError('Create result missing primitiveId')
        group = op['kind'] + 's'
        if obj['primitiveId'] in allowed[group]:
            raise RouteError('Duplicate created primitiveId')
        allowed[group][obj['primitiveId']] = op['params']
    for group in GROUPS:
        old = {r['primitiveId']: r for r in before[group]}
        live = {r['primitiveId']: r for r in after[group]}
        if any(ident not in live or live[ident] != row for ident, row in old.items()):
            raise RouteError('Existing serialized fields changed: ' + group)
        added = set(live) - set(old)
        if added != set(allowed.get(group, {})):
            raise RouteError('Unexpected added or missing primitives: ' + group)
        for ident in added:
            for key, expected in allowed[group][ident].items():
                actual = live[ident].get(key)
                match = abs(actual-expected) <= NEW_TOL_MIL if finite_number(expected) and finite_number(actual) else actual == expected
                if key == 'layer':
                    match = actual == expected
                if not match:
                    raise RouteError('New primitive readback mismatch: ' + group + '/' + key)
            if live[ident].get('primitiveLock') is not False:
                raise RouteError('Unexpected primitive lock state')
            if group == 'vias' and live[ident].get('viaType') != 0:
                raise RouteError('Unexpected via type')


def validate_bridge(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'http' or p.hostname not in ('127.0.0.1', 'localhost', '::1') or not p.port or p.path not in ('', '/') or p.query or p.fragment or p.username:
        raise RouteError('Use an explicit existing local HTTP bridge URL and port')
    return url.rstrip('/')


def compile_plan(args):
    cfg = load_project(args.project)
    if not cfg.get('project_uuid') or not args.pcb_uuid or not args.owner:
        raise RouteError('Explicit project UUID, PCB UUID and owner required')
    baseline = snapshot_ok(read_json(args.baseline))
    nets = frozen_nets(read_json(args.frozen_netlist))
    plan = read_json(args.plan)
    batches = validate_plan(plan, baseline, args.batch_size)
    out = Path(args.evidence).resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out/'compiled').mkdir()
    (out/'live').mkdir()
    write_new(out/'baseline.json', baseline)
    write_new(out/'frozen-netlist.json', nets)
    write_new(out/'plan.json', plan)
    manifest = {'version': VERSION, 'compiled_at': now(), 'mode': 'dry_run_not_applied',
                'project_uuid': cfg['project_uuid'], 'pcb_uuid': args.pcb_uuid,
                'project_config': cfg['file'], 'bridge': validate_bridge(args.bridge),
                'lock_file': str(Path(args.lock_file).resolve()), 'owner': args.owner,
                'batch_size': args.batch_size, 'batches': batches,
                'files_sha256': {f: sha(out/f) for f in ('baseline.json', 'frozen-netlist.json', 'plan.json')},
                'validation': {'angles_and_input_schema': True, 'clearance_checked': False, 'native_drc_run': False},
                'later_batch_templates': 'Runtime expected snapshot comes only from preceding verified readback; placeholders must not be run manually.'}
    write_new(out/'manifest.json', manifest)
    for i, batch in enumerate(batches):
        with (out/'compiled'/('batch-%03d.js' % i)).open('x', encoding='utf-8', newline='\n') as f:
            f.write(batch_js(manifest, baseline if i == 0 else None, nets, batch))
    write_new(out/'manifest-sha256.json', {'sha256': sha(out/'manifest.json')})
    return {'status': 'DRY_RUN', 'batches': len(batches), 'primitives': sum(map(len, batches)), 'evidence': str(out)}


class HttpTransport:
    """One request only; urllib errors propagate. No retry loop or bridge startup."""
    def __init__(self, base, timeout=45):
        self.base, self.timeout = validate_bridge(base), timeout

    def request(self, method, endpoint, payload):
        raw = None if payload is None else json.dumps(payload, ensure_ascii=True).encode('utf-8')
        req = urllib.request.Request(self.base+endpoint, data=raw, method=method,
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                body = response.read().decode('utf-8')
        except urllib.error.HTTPError as exc:
            error = RouteError('HTTP %s; execution may be partial' % exc.code)
            error.response_body = exc.read().decode('utf-8', errors='replace')
            raise error from exc
        try:
            return json.loads(body)
        except ValueError as exc:
            error = RouteError('Non-JSON bridge response; execution state uncertain')
            error.response_body = body
            raise error from exc


class Journal:
    def __init__(self, folder, transport):
        self.folder, self.transport, self.counter = folder, transport, 0

    def request(self, method, endpoint, payload):
        self.counter += 1
        stem = '%03d' % self.counter
        write_new(self.folder/(stem+'-request.json'), {'at': now(), 'method': method, 'endpoint': endpoint, 'payload': payload})
        try:
            response = self.transport.request(method, endpoint, payload)
        except Exception as exc:
            write_new(self.folder/(stem+'-error.json'), {'at': now(), 'type': type(exc).__name__, 'error': str(exc), 'response_body': getattr(exc, 'response_body', None)})
            raise
        write_new(self.folder/(stem+'-response.json'), response)
        return response

    def execute(self, code):
        reply = self.request('POST', '/execute', {'code': code})
        if not isinstance(reply, dict) or reply.get('success') is False or 'result' not in reply:
            raise RouteError('Bridge execution failed; inspect full response evidence')
        result = reply['result']
        if isinstance(result, dict) and ('ABORT' in result or 'HTTP' in result):
            raise RouteError('EDA operation aborted; inspect full response evidence')
        return result


def check_lock(manifest, owner):
    if not owner or owner != manifest['owner']:
        raise RouteError('Explicit owner must match compiled manifest')
    value = read_json(manifest['lock_file'])
    if value.get('owner') != owner:
        raise RouteError('EDA lock belongs to another owner; not acquiring or replacing it')
    if value.get('scope') != manifest['project_uuid']+'/'+manifest['pcb_uuid']:
        raise RouteError('Lock scope does not match explicit project/PCB')
    stamp = value.get('heartbeat')
    try:
        heartbeat = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        age = (dt.datetime.now(dt.timezone.utc)-heartbeat).total_seconds()
    except (AttributeError, TypeError, ValueError):
        raise RouteError('Lock heartbeat must be timezone-aware ISO8601') from None
    if not -30 <= age <= 300:
        raise RouteError('Lock heartbeat stale; owner must refresh it explicitly')


def _live_step(evidence, owner, bridge, apply_one=None, checkpoint=False, transport=None):
    out = Path(evidence).resolve()
    manifest = read_json(out/'manifest.json')
    if sha(out/'manifest.json') != read_json(out/'manifest-sha256.json')['sha256']:
        raise RouteError('Compiled manifest changed')
    if manifest.get('version') != VERSION:
        raise RouteError('Unsupported manifest version')
    if (out/'BLOCKED.json').exists():
        raise RouteError('Evidence run blocked/uncertain; never replay it. Inspect live state and compile a new residual plan.')
    if any(d.is_dir() and not (d/'verified.json').exists() for d in (out/'live').iterdir()):
        raise RouteError('An earlier live command is incomplete; inspect it, never replay or bypass it')
    for f, expected in manifest['files_sha256'].items():
        if sha(out/f) != expected:
            raise RouteError('Compiled input changed: ' + f)
    check_lock(manifest, owner)
    batches = manifest['batches']
    completed = 0
    while completed < len(batches) and (out/'live'/('apply-%03d' % completed)/'verified.json').exists():
        completed += 1
    if not checkpoint and apply_one != completed:
        raise RouteError('Apply exactly the next unexecuted batch; completed batches cannot be replayed')
    if not checkpoint and (apply_one is None or not 0 <= apply_one < len(batches)):
        raise RouteError('Batch index out of range')
    before = read_json(out/'baseline.json' if completed == 0 else out/'live'/('apply-%03d' % (completed-1))/'snapshot.json')
    if completed:
        previous = out/'live'/('apply-%03d' % (completed-1))
        if sha(previous/'snapshot.json') != read_json(previous/'verified.json')['snapshot_sha256']:
            raise RouteError('Previous verified readback changed')
    nets = read_json(out/'frozen-netlist.json')
    name = ('checkpoint-after-%03d' % completed) if checkpoint else ('apply-%03d' % apply_one)
    attempt = out/'live'/name
    attempt.mkdir(exist_ok=False)  # Started, failed, interrupted and completed all block replay.
    write_new(attempt/'started.json', {'at': now(), 'owner': owner, 'bridge': validate_bridge(bridge), 'completed_batches_before': completed, 'checkpoint': checkpoint})
    journal = Journal(attempt, transport or HttpTransport(bridge))
    try:
        health = journal.request('GET', '/health', None)
        if not isinstance(health, dict) or health.get('service') != 'easyeda-bridge' or health.get('edaConnected') is not True or health.get('edaWindowCount', 0) < 1:
            raise RouteError('Existing bridge identity/connection not verified')
        check_lock(manifest, owner)
        if checkpoint:
            saved = journal.execute(guard_js(manifest, before, nets)+'return await eda.pcb_Document.save('+json.dumps(manifest['pcb_uuid'])+');')
            if saved is not True:
                raise RouteError('Save did not return true')
            write_new(attempt/'save.json', saved)
            operations, created = [], []
        else:
            operations = batches[apply_one]
            created = journal.execute(batch_js(manifest, before, nets, operations))
            write_new(attempt/'created.json', created)
        # A separate /execute call is mandatory after mutation/save.
        readback = journal.execute(readback_js(manifest))
        if not isinstance(readback, dict) or 'snapshot' not in readback or 'netlist' not in readback:
            raise RouteError('Invalid independent readback')
        write_new(attempt/'snapshot.json', readback['snapshot'])
        write_new(attempt/'netlist.json', readback['netlist'])
        validate_readback(before, readback['snapshot'], operations, created, readback['netlist'], nets)
        if checkpoint:
            check_lock(manifest, owner)
            drc = journal.execute(scope_js(manifest['project_uuid'], manifest['pcb_uuid'])+'return await eda.pcb_Drc.check(true,false,true);')
            write_new(attempt/'drc.json', drc)
            if not isinstance(drc, list):
                raise RouteError('Unexpected native DRC shape; false/null are not a clean result')
        summary = {'status': 'CHECKPOINT_CAPTURED' if checkpoint else 'BATCH_READBACK_VERIFIED',
                   'at': now(), 'batch': apply_one, 'created': len(created),
                   'counts': {k: len(readback['snapshot'][k]) for k in GROUPS},
                   'saved': checkpoint, 'native_drc_run': checkpoint,
                   'snapshot_sha256': sha(attempt/'snapshot.json'),
                   'remaining_batches': len(batches)-completed-(0 if checkpoint else 1)}
        if checkpoint:
            summary['drc_groups'] = len(drc)
            summary['drc_is_clean'] = not drc
        write_new(attempt/'verified.json', summary)
        return summary
    except BaseException as exc:
        problem = {'at': now(), 'attempt': name, 'type': type(exc).__name__, 'error': str(exc),
                   'state': 'UNCERTAIN_DO_NOT_REPLAY', 'instruction': 'Inspect live geometry/netlist independently; compile a new plan containing only verified missing objects.'}
        write_new(attempt/'failed.json', problem)
        if not (out/'BLOCKED.json').exists():
            write_new(out/'BLOCKED.json', problem)
        raise


def live_step(evidence, owner, bridge, apply_one=None, checkpoint=False, transport=None):
    """Serialize commands sharing one evidence run; never acquire the EDA lock."""
    active = Path(evidence).resolve()/'ACTIVE.json'
    lease = {'token': str(uuid.uuid4()), 'at': now(), 'owner': owner}
    write_new(active, lease)
    try:
        return _live_step(evidence, owner, bridge, apply_one, checkpoint, transport)
    finally:
        if active.exists() and read_json(active) == lease:
            active.unlink()  # Only our transient local lease, never the EDA lock.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Default: compile only, zero bridge calls')
    mode.add_argument('--apply-one', type=int, metavar='INDEX')
    mode.add_argument('--checkpoint', action='store_true')
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--bridge', required=True, help='Existing local bridge, explicitly verified by operator')
    parser.add_argument('--project')
    parser.add_argument('--pcb-uuid')
    parser.add_argument('--lock-file')
    parser.add_argument('--baseline')
    parser.add_argument('--frozen-netlist')
    parser.add_argument('--plan')
    parser.add_argument('--batch-size', type=int, default=20)
    args = parser.parse_args(argv)
    try:
        if args.apply_one is not None or args.checkpoint:
            result = live_step(args.evidence, args.owner, args.bridge, args.apply_one, args.checkpoint)
        else:
            for field in ('pcb_uuid', 'lock_file', 'baseline', 'frozen_netlist', 'plan'):
                if not getattr(args, field):
                    raise RouteError('Dry run requires --'+field.replace('_', '-'))
            result = compile_plan(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'STOPPED', 'error_type': type(exc).__name__, 'message': str(exc)[:240]}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
