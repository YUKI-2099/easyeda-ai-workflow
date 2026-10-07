# -*- coding: utf-8 -*-
"""安全改名：板子 / 原理图 / 页 / PCB 改名前后各拍一次全工程快照，只允许目标那一项变化，别的项变了立刻停。

编码的坑：#59（modifyBoardName / modifySchematicName 返回 true 却改到工程树上的相邻项）。
  2026-09-15 某工程：createBoard 后马上 modifyBoardName('Board1', …) → 改错板、改不回来。
  2026-09-26 另一工程：createBoard 后几秒内 modifyBoardName('<新板名>', '<目标名>') → 改到了树上紧挨着的
  下一块板；同一次调用里 modifySchematicName(新原理图 uuid, …) → 改到了那块邻板的原理图。
  约 10 分钟后同样的 modifyBoardName('<新板名>', …) → 改对了。两个接口都只是把请求转给 EDA 主程序
  （扩展侧源码就一行 rpcCall），主程序怎么定位看不到；详见工具手册 #59。
  ⑨：写后立刻回读可能慢一拍 → 改名后等 settle 秒再拍快照。

板子的身份用「原理图 uuid + PCB uuid」认，不认名字（名字正是会被改错的东西）。
不做"自动改回去"：错位时再调同一个接口，可能又落到别的项上（#59 "改不回来"）。

用法：
  python safe_rename.py show
  python safe_rename.py board <原板名> <新板名>
  python safe_rename.py sch <原理图uuid> <新名>
  python safe_rename.py page <页uuid> <新页名>      （快照含全工程所有页名；建页后先等 settle，坑⑪）
  python safe_rename.py pcb <PCB uuid> <新名>        （createPcb 给的默认名是 PCB1/PCB2…；建完隔开再改，#59）
退出码：0 成功；2 前置条件不满足（没改任何东西）；3 改名后发现不止目标一项变了（已停，需人工处理）。
"""
import json
import sys
import time

SETTLE = 2.0

_JS_SNAPSHOT = r'''
const b = (await eda.dmt_Board.getAllBoardsInfo()).map(x => ({name: x.name, sch: x.schematic ? x.schematic.uuid : null, pcb: x.pcb ? x.pcb.uuid : null}));
const s = (await eda.dmt_Schematic.getAllSchematicsInfo()).map(x => ({uuid: x.uuid, name: x.name, board: x.parentBoardName || null}));
const p = (await eda.dmt_Pcb.getAllPcbsInfo()).map(x => ({uuid: x.uuid, name: x.name, board: x.parentBoardName || null}));
const pj = await eda.dmt_Project.getCurrentProjectInfo();
const tree = {}, pages = {};
for (const it of pj.data) if (it.schematic) {
  tree[it.schematic.uuid] = it.schematic.name;   // 工程树里的原名（getAllSchematicsInfo / getSchematicPageInfo 会小写化，坑⑪）
  for (const pg of (it.schematic.page || [])) pages[pg.uuid] = it.schematic.uuid + '/' + pg.name;
}
return {project: pj.uuid, boards: b, schematics: s, pcbs: p, schTreeNames: tree, pages: pages};
'''


def snapshot(ex):
    r = ex(_JS_SNAPSHOT, timeout=60)
    if not isinstance(r, dict) or 'boards' not in r:
        raise RuntimeError('快照失败: %r' % (r,))
    return r


def board_key(b):
    return 'sch=%s|pcb=%s' % (b['sch'], b['pcb'])


def flatten(snap):
    """把快照压成 {稳定键: 名字}：板用 sch+pcb uuid，原理图/PCB 用自身 uuid。"""
    out = {}
    for b in snap['boards']:
        out['board ' + board_key(b)] = b['name']
    for uuid, name in snap['schTreeNames'].items():
        out['sch ' + uuid] = name
    for p in snap['pcbs']:
        out['pcb ' + p['uuid']] = p['name']
    for uuid, name in snap.get('pages', {}).items():
        out['page ' + uuid] = name
    return out


def diff(a, b):
    fa, fb = flatten(a), flatten(b)
    changes = []
    for k in sorted(set(fa) | set(fb)):
        if fa.get(k) != fb.get(k):
            changes.append((k, fa.get(k), fb.get(k)))
    return changes


def show(ex):
    s = snapshot(ex)
    print('工程', s['project'])
    for b in s['boards']:
        print('  板  %-34s 原理图 %s「%s」  PCB %s' % (b['name'], b['sch'], s['schTreeNames'].get(b['sch']), b['pcb']))
    free = [u for u in s['schTreeNames'] if u not in {b['sch'] for b in s['boards']}]
    for u in free:
        print('  游离原理图 %s「%s」' % (u, s['schTreeNames'][u]))


def rename_board(ex, old, new):
    before = snapshot(ex)
    names = [b['name'] for b in before['boards']]
    if names.count(old) != 1:
        print('前置不满足：板名 %r 出现 %d 次（要求恰好 1 次）' % (old, names.count(old))); return 2
    if new in names:
        print('前置不满足：新板名 %r 已被占用' % new); return 2
    target = board_key(next(b for b in before['boards'] if b['name'] == old))
    r = ex('return await eda.dmt_Board.modifyBoardName(%s, %s);' % (json.dumps(old), json.dumps(new)), timeout=60)
    print('modifyBoardName 返回 %r（返回值不作证据，看快照）' % (r,))
    time.sleep(SETTLE)
    after = snapshot(ex)
    return _judge(diff(before, after), [('board ' + target, old, new)])


def rename_schematic(ex, uuid, new):
    before = snapshot(ex)
    if uuid not in before['schTreeNames']:
        print('前置不满足：原理图 %s 不存在' % uuid); return 2
    old = before['schTreeNames'][uuid]
    r = ex('return await eda.dmt_Schematic.modifySchematicName(%s, %s);' % (json.dumps(uuid), json.dumps(new)), timeout=60)
    print('modifySchematicName 返回 %r（返回值不作证据，看快照）' % (r,))
    time.sleep(SETTLE)
    after = snapshot(ex)
    return _judge(diff(before, after), [('sch ' + uuid, old, new)])


def rename_page(ex, uuid, new):
    """原理图页改名（坑⑪：建页后要等 settle；这里同样做全工程快照，页名也在快照里）。"""
    before = snapshot(ex)
    if uuid not in before['pages']:
        print('前置不满足：页 %s 不在任何板的原理图里' % uuid); return 2
    old = before['pages'][uuid]
    r = ex('return await eda.dmt_Schematic.modifySchematicPageName(%s, %s);' % (json.dumps(uuid), json.dumps(new)), timeout=60)
    print('modifySchematicPageName 返回 %r（返回值不作证据，看快照）' % (r,))
    time.sleep(SETTLE)
    after = snapshot(ex)
    return _judge(diff(before, after), [('page ' + uuid, old, old.rsplit('/', 1)[0] + '/' + new)])


def rename_pcb(ex, uuid, new):
    """PCB 改名（2026-09-27 实测：createPcb('<板名>') 建出来的 PCB 默认叫 PCB2）。同样全工程快照、只许目标一项变。"""
    before = snapshot(ex)
    pcbs = {p['uuid']: p['name'] for p in before['pcbs']}
    if uuid not in pcbs:
        print('前置不满足：PCB %s 不存在' % uuid); return 2
    if new in pcbs.values():
        print('前置不满足：新 PCB 名 %r 已被占用' % new); return 2
    old = pcbs[uuid]
    r = ex('return await eda.dmt_Pcb.modifyPcbName(%s, %s);' % (json.dumps(uuid), json.dumps(new)), timeout=60)
    print('modifyPcbName 返回 %r（返回值不作证据，看快照）' % (r,))
    time.sleep(SETTLE)
    after = snapshot(ex)
    return _judge(diff(before, after), [('pcb ' + uuid, old, new)])


def _judge(changes, expected):
    if changes == expected:
        k, a, b = expected[0]
        print('✓ 只改了目标：%s  %r → %r' % (k, a, b)); return 0
    print('✗ 改名结果不对，已停（不自动改回，见 #59）。期望：%s' % expected)
    for k, a, b in changes:
        print('   实际变化  %s  %r → %r' % (k, a, b))
    if not changes:
        print('   （什么都没变）')
    return 3


if __name__ == '__main__':
    sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
    from bridge import ex
    if len(sys.argv) >= 2 and sys.argv[1] == 'show':
        show(ex); sys.exit(0)
    if len(sys.argv) == 4 and sys.argv[1] == 'board':
        sys.exit(rename_board(ex, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == 'sch':
        sys.exit(rename_schematic(ex, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == 'page':
        sys.exit(rename_page(ex, sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == 'pcb':
        sys.exit(rename_pcb(ex, sys.argv[2], sys.argv[3]))
    print(__doc__); sys.exit(2)
