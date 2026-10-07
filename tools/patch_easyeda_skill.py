# -*- coding: utf-8 -*-
"""给 easyeda-api 的 SKILL.md 打「本机入口补丁」：让 AI 开工先读本仓库的手册、先扫端口找现成 Bridge。

    python tools/patch_easyeda_skill.py                     # 预览默认三份：会改哪些文件、改了什么，不写
    python tools/patch_easyeda_skill.py --apply             # 真写；写之前把原文件备份成 SKILL.md.bak-<时间戳>
    python tools/patch_easyeda_skill.py --skill <SKILL.md 或它所在的目录> [--skill …] [--apply]   # 只处理指定的文件

默认三份（Claude Code / OpenCode / Codex 各一份拷贝，只改一份就漂移；哪份没装就跳过并说明）：
    $CLAUDE_CONFIG_DIR（没设就是 ~/.claude）/skills/easyeda-api/SKILL.md
    $XDG_CONFIG_HOME（没设就是 ~/.config）/opencode/skills/easyeda-api/SKILL.md
    $CODEX_HOME（没设就是 ~/.codex）/skills/easyeda-api/SKILL.md
--skill 显式给的路径必须存在，不存在直接报错。同一个文件（包括经软链指过去的）只处理一次；
软链不会被换成普通文件：写的是它指向的真实文件，备份也放在真实文件旁边。

为什么是补丁而不是新 skill：新 skill 会和厂家 skill 抢同一批触发词（EDA / 原理图 / PCB），调用时冲突。
补丁只做一件事：告诉 AI 手册在哪、先扫端口找现成 Bridge。**坑记录一律不进 skill**（工具手册 §0.6）：skill 目录是厂家包，升级整包覆盖。
所以升级 skill 之后要重跑；本仓库挪了位置也要重跑（补丁里的手册路径按本仓库所在位置现算）。
本仓库在系统临时目录下时（比如一份临时工作副本），预览照常但会警告，--apply 默认拒绝：临时目录一清，
skill 里的路径就指空了；确实要写就加 --allow-temp-repo。
不用这个 skill 的 AI 工具（或想双保险）：在你自己的 AGENTS.md / CLAUDE.md 里加一行指向本手册。

幂等：认补丁只认开头标记的前缀，打过（不管哪一版）就整段换成当前版本，再跑一次没有变化。
保留原文件的换行风格（补丁按文件里占多数的 CRLF / LF 写，补丁以外的字节不动）和 UTF-8 BOM。
不改的情况：不是 UTF-8、文件只读、锚点没命中、打完后首尾标记不是各 1 处。几份文件现有的补丁段彼此不一样时打一条警告（漂移）。
写入：先把原文件原样备份，再写临时文件、改名替换；写后回读不对就用备份还原并报错。
退出码：0 正常（预览或写完）；1 有文件处理不了，或一份 SKILL.md 都没找到；
        2 参数不对（--skill 给的路径不存在；仓库在临时目录下却 --apply 又没加 --allow-temp-repo）。
"""
import argparse
import datetime
import difflib
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL_REL = Path('skills') / 'easyeda-api' / 'SKILL.md'
ANCHOR = 'Then proceed with the setup steps below.'
BEGIN_PREFIX = '<!-- BEGIN local-patch easyeda-api-manual'          # 认旧补丁只认前缀：版本号、路径变了也能整段替换
BEGIN = BEGIN_PREFIX + ' v3 (eda-workflow: tools/patch_easyeda_skill.py) -->'
END = '<!-- END local-patch -->'
BOM = b'\xef\xbb\xbf'
MANUAL = 'EDA工具手册-easyeda-api官方桥接-跨项目版.md'
PCB_MANUAL = 'PCB操作手册-跨项目版.md'

PATCH_TEMPLATE = BEGIN + '''
### 0. 🔴 本机专用：先读病历，再动手

本 skill 是厂家的只读说明书；**本机的坑、标准流程、脚本工具链全在
`{manual}`**。

1. 开工先读手册 **§0 + §1 + §4**；PCB 侧再读 `{pcb_manual}`。
2. 🚨 **先扫端口找现成 Bridge**（手册 §0.5），扫到就用；扫不到才按下面 §3 起，且**先 Bridge 后扩展**（§0.3）。
3. 画原理图按手册 **§1.7 标准流程** + **§2.14 脚本索引**（`{tools}`，项目根放 `project.json`），不要即兴调 API。
4. 任何写操作前做 §1.6 防线 1 的 scope 断言；**用户在 GUI 里时连只读切页都不许跑**（#51）；每批写完 **DRC → 网表 → SHA**。
5. 审查默认按同目录《EDA作业通用手册》§9.7 开子代理红队 + 对辩；另拉 Codex 等第二个 AI 交叉审查时按手册 §6.4：审查方自己拉数据、执行方同时段不碰桥接。
''' + END + '\n'

LABEL = {'new': '新打补丁', 'refresh': '刷新旧补丁', 'same': '已是最新', 'missing': '跳过', 'error': '❌ 不处理'}


def render_patch(repo=None, nl='\n'):
    """补丁全文（含首尾标记和末尾换行）：手册路径按 repo（默认本仓库）现算，换行用 nl。"""
    repo = Path(REPO if repo is None else repo)
    text = PATCH_TEMPLATE.format(manual=repo / 'docs' / MANUAL, pcb_manual=repo / 'docs' / PCB_MANUAL,
                                 tools=str(repo / 'tools') + os.sep)
    return text.replace('\n', nl)


def default_copies():
    """默认三份的路径：CLAUDE_CONFIG_DIR / XDG_CONFIG_HOME / CODEX_HOME 设了就用它们（调用时才读环境变量）。"""
    home = Path(os.path.expanduser('~'))

    def base(var, fallback):
        value = os.environ.get(var)
        return Path(os.path.expanduser(value)) if value else home / fallback

    return [base('CLAUDE_CONFIG_DIR', '.claude') / SKILL_REL,
            base('XDG_CONFIG_HOME', '.config') / 'opencode' / SKILL_REL,
            base('CODEX_HOME', '.codex') / SKILL_REL]


def in_temp_dir(path):
    """path 在不在系统临时目录（tempfile.gettempdir()）之下。"""
    temp = Path(tempfile.gettempdir()).resolve()
    p = Path(path).resolve()
    return p == temp or temp in p.parents


def skill_file(path):
    """给的是目录就取里面的 SKILL.md。"""
    p = Path(os.path.expanduser(str(path)))
    return p / 'SKILL.md' if p.is_dir() else p


def newline_of(text):
    """文件里占多数的换行：CRLF 不少于单独的 LF 就算 CRLF。"""
    crlf = text.count('\r\n')
    return '\r\n' if crlf and crlf >= text.count('\n') - crlf else '\n'


def plan(path, repo=None):
    """算出一份 SKILL.md（真实文件路径）打完补丁的样子，不写。返回 dict：path、state（new/refresh/same/missing/error），
    能处理的还有 raw（原字节）、bom、nl、old、new（文本）、old_block（现有补丁段，没有为 None）和 data（要写的字节）；
    处理不了的有 why。"""
    p = Path(path)
    r = {'path': p}
    if not p.is_file():
        return dict(r, state='missing', why='文件不存在')
    raw = p.read_bytes()
    bom = raw.startswith(BOM)
    try:
        old = raw[len(BOM):].decode('utf-8') if bom else raw.decode('utf-8')
    except UnicodeDecodeError:
        return dict(r, state='error', why='不是 UTF-8 编码，不改（补丁里有中文和 emoji，只能写进 UTF-8 文件）')
    nl = newline_of(old)
    patch = render_patch(repo, nl)
    n = old.count(BEGIN_PREFIX)
    old_block = None
    if n > 1:
        return dict(r, state='error', why='文件里有 %d 段补丁，先人工清理成一段' % n)
    if n == 1:
        a = old.index(BEGIN_PREFIX)
        e = old.find(END, a)
        if e < 0:
            return dict(r, state='error', why='有补丁开头标记、没有结尾标记，先人工看')
        b = e + len(END)
        b += 2 if old.startswith('\r\n', b) else (1 if old.startswith('\n', b) else 0)
        old_block = old[a:b]
        new = old[:a] + patch + old[b:]
        state = 'same' if new == old else 'refresh'
    elif ANCHOR in old:
        new = old.replace(ANCHOR, ANCHOR + nl + nl + patch, 1)
        state = 'new'
    else:
        return dict(r, state='error', why='锚点未命中（skill 版本变了？先人工看 SKILL.md 再改 ANCHOR）')
    if new.count(BEGIN_PREFIX) != 1 or new.count(END) != 1:
        return dict(r, state='error', why='打完后首尾标记不是各 1 处（文件里另有补丁标记？先人工看），不改')
    if state != 'same' and not os.access(p, os.W_OK):
        return dict(r, state='error', why='文件只读，不改')
    return dict(r, state=state, raw=raw, bom=bom, nl=nl, old=old, new=new, old_block=old_block,
                data=(BOM if bom else b'') + new.encode('utf-8'))


def diff_lines(r):
    """统一差异（换行先归一，只看内容），返回 (差异行, 加了几行, 删了几行)。"""
    a = r['old'].replace('\r\n', '\n').split('\n')
    b = r['new'].replace('\r\n', '\n').split('\n')
    lines = list(difflib.unified_diff(a, b, 'SKILL.md（现在）', 'SKILL.md（打补丁后）', n=1, lineterm=''))
    body = lines[2:]
    return lines, sum(l.startswith('+') for l in body), sum(l.startswith('-') for l in body)


def backup_path(p, stamp):
    base = p.with_name(p.name + '.bak-' + stamp)
    cand, i = base, 1
    while cand.exists():
        cand, i = Path('%s-%d' % (base, i)), i + 1
    return cand


def apply(r, stamp):
    """先把原文件原样备份，再写临时文件、改名替换（临时文件无论成败都清掉）；写后回读核对，不对就用备份还原。
    返回备份路径；失败抛 OSError / RuntimeError。"""
    p = r['path']
    bak = backup_path(p, stamp)
    with open(bak, 'xb') as f:
        f.write(r['raw'])
    tmp = p.with_name(p.name + '.patch-tmp')
    try:
        with open(tmp, 'wb') as f:
            f.write(r['data'])
        shutil.copymode(p, tmp)
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)
    back = p.read_bytes()
    text = back[len(BOM):].decode('utf-8', 'replace') if r['bom'] else back.decode('utf-8', 'replace')
    if back != r['data'] or text.count(BEGIN_PREFIX) != 1 or text.count(END) != 1:
        try:
            shutil.copyfile(bak, p)
        except OSError as e:
            raise RuntimeError('写后回读不对，用备份还原也失败了（%s）：请手工把 %s 拷回 %s' % (e, bak, p)) from e
        raise RuntimeError('写后回读不对，已用备份 %s 还原原文件' % bak)
    return bak


def main(argv=None):
    ap = argparse.ArgumentParser(description='给 easyeda-api 的 SKILL.md 打本机入口补丁（默认只预览，--apply 才写）')
    ap.add_argument('--apply', action='store_true', help='真写；写之前把原文件备份成 SKILL.md.bak-<时间戳>')
    ap.add_argument('--skill', action='append', metavar='路径',
                    help='只处理这份 SKILL.md（也可以给它所在的目录），可重复；不给就处理默认三份')
    ap.add_argument('--allow-temp-repo', action='store_true',
                    help='本仓库在系统临时目录下也照样 --apply（默认拒绝，免得把临时路径写进 skill）')
    args = ap.parse_args(argv)

    explicit = bool(args.skill)
    entries, missing, seen, dup_notes = [], [], {}, []
    for given in (args.skill or default_copies()):
        f = skill_file(given)
        if not f.is_file():
            if explicit:
                missing.append(f)
            else:
                entries.append((f, None))                       # 默认副本没装：跳过并说明
            continue
        real = f.resolve()
        key = os.path.normcase(str(real))
        if key in seen:
            dup_notes.append('  （%s 和 %s 是同一个文件，只处理一次）' % (f, seen[key]))
            continue
        seen[key] = f
        entries.append((f, real))
    if missing:
        for f in missing:
            print('❌ 找不到 %s（--skill 要给 SKILL.md 或它所在的目录）' % f, file=sys.stderr)
        return 2

    repo = REPO
    if in_temp_dir(repo):
        print('⚠️ 本仓库在系统临时目录下（%s）：补丁会把这个临时路径写进 skill，临时目录一清就指空了。' % repo)
        if args.apply and not args.allow_temp_repo:
            print('❌ 拒绝 --apply：请在长期存放的仓库里跑；确实要用这份临时副本，加 --allow-temp-repo', file=sys.stderr)
            return 2

    print(('写入（先备份原文件）' if args.apply else '预览（不写任何文件）') + '，补丁里的手册在 %s：' % (repo / 'docs'))
    for note in dup_notes:
        print(note)
    plans = []
    for shown, real in entries:
        r = plan(real, repo) if real else {'path': shown, 'state': 'missing', 'why': '文件不存在'}
        linked = real and os.path.normcase(os.path.abspath(shown)) != os.path.normcase(str(real))
        name = '%s（→ %s）' % (shown, real) if linked else str(shown)     # 经软链 / 联接过去的，标出真实文件
        plans.append((name, r))

    blocks = {}
    for name, r in plans:
        if r.get('old_block'):
            blocks.setdefault(r['old_block'].replace('\r\n', '\n'), []).append(name)
    if len(blocks) > 1:
        print('⚠️ 各份现有的补丁段不一致（漂移了），这次会统一成当前版本：')
        for block, names in blocks.items():
            print('    %s [%s] ← %s' % (block.split('\n', 1)[0], hashlib.sha256(block.encode('utf-8')).hexdigest()[:8],
                                       '、'.join(names)))

    bad, shown_diffs, changed = 0, [], 0
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    for name, r in plans:
        st = r['state']
        if st in ('missing', 'error'):
            print('  %s  %s：%s' % (LABEL[st], name, r['why']))
            bad += st == 'error'
            continue
        lines, plus, minus = diff_lines(r)
        info = '+%d -%d 行，%s%s' % (plus, minus, 'CRLF' if r['nl'] == '\r\n' else 'LF', '，带 BOM' if r['bom'] else '')
        note = ''
        if st != 'same':
            changed += 1
            if args.apply:
                try:
                    note = '  已写，原文件备份为 %s' % apply(r, stamp)
                except (OSError, RuntimeError) as e:
                    print('  ❌ 写失败  %s：%s' % (name, e))
                    bad += 1
                    continue
        print('  %s  %s（%s）%s' % (LABEL[st], name, info, note))
        if st != 'same' and not args.apply:
            if lines[2:] in shown_diffs:
                print('      （差异同上）')
            else:
                shown_diffs.append(lines[2:])
                for line in lines:
                    print('      ' + line)
    found = [r for _, r in plans if r['state'] != 'missing']
    if not found:
        print('\n❌ 一份 SKILL.md 都没找到：先装 easyeda-api skill，或用 --skill 指给我')
        return 1
    if bad:
        print('\n❌ 有 %d 份处理不了，看上面' % bad)
        return 1
    same_bytes = len({r['data'] for r in found}) == 1
    tail = '%d 份 SKILL.md%s' % (len(found), '，字节一致' if same_bytes and len(found) > 1 else '')
    if args.apply:
        print('\n✅ 补丁在位：%s' % tail)
    elif changed:
        print('\n%d 份要改（%s）；确认无误后加 --apply 写入' % (changed, tail))
    else:
        print('\n✅ 都已是最新：%s' % tail)
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):        # 管道默认是本机编码（如 GBK），一遇到 ✅ 就崩——写完了却报失败
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
