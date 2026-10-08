# -*- coding: utf-8 -*-
"""公开前自查：找不该公开的东西。提交前跑一遍，要零命中。

    python -X utf8 tools/check_public.py              # 扫工作区：已跟踪 + 未忽略的新文件，有命中就退出码 1
    python -X utf8 tools/check_public.py 文件 …        # 只扫指定文件
    python -X utf8 tools/check_public.py --history    # 扫会公开的 git 历史：所有提交里的每个文件版本，加作者 / 提交者邮箱和提交信息
    python -X utf8 tools/check_public.py --history --all-refs   # 连别的远程（如只读存档的旧仓库）和 stash 一起扫
    python -X utf8 tools/check_public.py --list-rules # 看有哪些规则
    python -X utf8 tools/check_public.py --staged     # 只扫暂存区里这次要提交的文件（pre-commit 钩子用）
    python -X utf8 tools/check_public.py --message 文件 # 扫一份提交信息（commit-msg 钩子用；# 开头的注释行不算）

本地钩子：`python -X utf8 tools/install_hooks.py` 给这份克隆装上 pre-commit / commit-msg 两个钩子，
每次提交自动跑上面两条，有命中就拦下这次提交。钩子不随 clone 走，每台机器、每份克隆装一次。

注意：默认只扫工作区里文件现在的内容。仓库要改成公开之前，必须再跑一次 --history——
历史版本和提交信息会跟着仓库一起公开，只改当前文件挡不住。

内置规则（与具体项目无关，随仓库公开）：
  - 带用户名的本机路径（C:\\Users\\某人、/c/Users/某人、/mnt/c/Users/某人、/home/某人）、网络共享路径（\\\\主机\\共享），
    以及非系统盘的绝对路径（D:\\…、/d/…）；
  - IP（IPv4 / IPv6；回环、0.0.0.0 和文档专用网段除外）、MAC 地址、邮箱（noreply 和 example 域名除外）、ssh 登录目标；
  - 密钥样式（sk-、ghp_ 一类前缀、私钥头、password / api_key = … 赋值）；
  - EasyEDA 工程 / 页 / 图元 id（16 位、32 位十六进制）和完整 SHA-256——它们只属于某一块板子，示例写成 `<16位页uuid>` 这类占位。

本地禁词表（不进仓库）：项目名、客户名、主机名、私有网名这类"说出来就是泄密"的词，本身也不能公开，
所以写在仓库根目录的 `.public-denylist.txt`（已在 .gitignore 里），或用环境变量 EDA_PUBLIC_DENYLIST 指一个文件。
在 git worktree 里没有这份文件时，自动用主工作目录根目录下的那份。
每行一个正则（不分大小写；要区分大小写写成 (?-i:…)）；# 开头是注释。没有禁词表时只跑内置规则，并提示一句。

确实要保留的行，在同一行写上 `check_public: allow`（Markdown 里可以写成 HTML 注释）：只豁免内置规则，禁词照样报。
退出码：0 干净；1 有命中；2 用法或配置错误（禁词表写坏了、指定的文件不存在、没装 git…）。
"""
import ipaddress
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DENYLIST_NAME = '.public-denylist.txt'
ALLOW_MARK = 'check_public: allow'
SELF_REL = 'tools/check_public.py'
TEXT_EXT = {'.md', '.py', '.js', '.mjs', '.cjs', '.ts', '.json', '.txt', '.toml', '.yml', '.yaml', '.ini', '.cfg',
            '.ps1', '.sh', '.bat', '.cmd', '.html', '.css', '.csv', ''}

# EasyEDA 系统库的公共 uuid：所有人都一样，不属于任何项目
PUBLIC_IDS = {'0819f05c4eef4c71ace90d822a990e87'}
# RFC 5737 / RFC 3849 文档专用网段：写示例用
DOC_NETS = [ipaddress.ip_network(n) for n in ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24', '2001:db8::/32')]

_NOT_PLACEHOLDER = r'(?![<%$({]|你的|某)'
RULES = [
    ('本机用户路径', re.compile(r'(?i)\b[a-z]:[\\/]+users[\\/]+' + _NOT_PLACEHOLDER + r'(?!public\b|default\b)[^\\/\s"\'`)>|]+')),
    ('本机用户路径', re.compile(r'(?i)(?<![\w.~])(?:/mnt)?/(?:[a-z]/users|home|users)/' + _NOT_PLACEHOLDER + r'[^/\s"\'`)>|]+')),
    ('网络共享路径', re.compile(r'(?i)(?<![\w\\:])\\\\[a-z0-9][\w.$-]*\\[^\s"\'`)>|]+')),
    ('非系统盘绝对路径', re.compile(r'(?i)(?<![\w/\\])[d-z]:[\\/]+' + _NOT_PLACEHOLDER + r'[^\s"\'`)>|，。、]+')),
    ('非系统盘绝对路径', re.compile(r'(?i)(?<![\w.~/])(?:/mnt)?/[d-z]/' + _NOT_PLACEHOLDER + r'[^\s/"\'`)>|]+/')),
    ('IP 地址', re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)')),
    ('IP 地址', re.compile(r'(?i)(?<![\w:.])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?:%[\w.]+)?(?![\w:])')),
    ('MAC 地址', re.compile(r'(?i)(?<![0-9a-f:-])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f:-])')),
    ('邮箱', re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')),
    ('ssh 登录目标', re.compile(r'(?i)\b(?:ssh|scp|sftp|rsync|mosh)\b[^\n]*?(?<![\w.%+-])(?!git@)[a-z_][\w.-]*@[\w-]+(?:\.[\w-]+)*')),
    ('密钥样式', re.compile(r'\bsk-[A-Za-z0-9_\-]{20,}|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}'
                         r'|\bglpat-[\w\-]{20,}|\bAKIA[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_\-]{35}|\bxox[baprs]-[A-Za-z0-9-]{10,}'
                         r'|\btskey-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----')),
    ('密钥样式', re.compile(r'(?i)["\']?\b(?:\w+_)?(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)'
                         r'(?:_\w+)?["\']?\s*[:=]\s*["\']?[^\s"\'<>{}]{6,}')),
    ('完整 SHA-256', re.compile(r'(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])')),
    ('EasyEDA 32 位 id', re.compile(r'(?<![0-9a-fA-F])[0-9a-f]{32}(?![0-9a-fA-F])')),
    ('EasyEDA 16 位 id', re.compile(r'(?<![0-9a-fA-F])[0-9a-f]{16}(?![0-9a-fA-F])')),
]


class ConfigError(Exception):
    """配置或用法错误：退出码 2。"""


def _ip_public(addr):
    return not (addr.is_loopback or addr.is_unspecified or any(addr in n for n in DOC_NETS if n.version == addr.version))


def ipv4_flagged(s, line, start):
    if start > 0 and line[start - 1] in 'vV':                   # v2.2.40.5 这种版本号
        return False
    try:
        addr = ipaddress.ip_address(s)
    except ValueError:
        return False                                          # 某段超过 255：版本号、编号，不是 IP
    return _ip_public(addr) and s != '255.255.255.255'


def ipv6_flagged(s):
    core = s.split('%', 1)[0]
    if core.count(':') < 3 and not re.search(r'[0-9a-fA-F]{3}', core):
        return False                                          # x[1::2]、12:30 这类不是地址
    try:
        addr = ipaddress.ip_address(core)
    except ValueError:
        return False
    return addr.version == 6 and _ip_public(addr)


def mac_flagged(s):
    raw = re.sub(r'[:-]', '', s).lower()
    return raw not in ('000000000000', 'ffffffffffff')


def email_flagged(s):
    s = s.lower()
    host = s.rsplit('@', 1)[1]
    if s.endswith('@users.noreply.github.com') or s == 'noreply@anthropic.com' or s.startswith('git@'):
        return False
    return host not in ('example.com', 'example.org', 'example.net')


def hex_flagged(s):
    if s in PUBLIC_IDS:
        return False
    return bool(re.search(r'\d', s)) and bool(re.search(r'[a-f]', s))   # 纯数字或纯字母不是 id


def main_worktree_root():
    """当前目录是 git worktree 时，返回主工作目录的根；不是 git 仓库、或本身就是主工作目录时返回 None。"""
    try:
        common = subprocess.run(['git', '-C', ROOT, 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                                capture_output=True, check=True).stdout.decode('utf-8').strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    root = os.path.dirname(os.path.normpath(common))
    same = lambda a, b: os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    return None if same(root, ROOT) else root            # realpath：Windows 上把 RUNNER~1 这类短路径名展开再比


def load_denylist(path=None):
    """返回 (正则列表或 None, 路径, 警告列表)。表写坏了抛 ConfigError。"""
    env = os.environ.get('EDA_PUBLIC_DENYLIST')
    if path is None and env:
        path = env
        if not os.path.isfile(path):                      # 显式指了却找不到：多半是拼错了，不能静默放过
            raise ConfigError('环境变量 EDA_PUBLIC_DENYLIST 指向的禁词表不存在：%s' % path)
    if path is None:
        path = os.path.join(ROOT, DENYLIST_NAME)
        if not os.path.isfile(path):
            main_tree = main_worktree_root()               # 在 git worktree 里：用主工作目录那份
            if main_tree and os.path.isfile(os.path.join(main_tree, DENYLIST_NAME)):
                path = os.path.join(main_tree, DENYLIST_NAME)
    if not os.path.isfile(path):
        return None, path, []
    try:
        with open(path, encoding='utf-8-sig') as f:
            lines = f.read().splitlines()
    except UnicodeDecodeError:
        raise ConfigError('禁词表要存成 UTF-8：%s' % path)
    pats, warns = [], []
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if re.search(r'\s#', line):
            warns.append('%s:%d：行内的 " #" 会被当成正则的一部分，注释请单独占一行' % (path, n))
        try:
            rx = re.compile(line, re.I)
        except re.error as e:
            raise ConfigError('%s:%d：正则写错了（%s）：%s' % (path, n, e, line))
        if rx.search(''):
            raise ConfigError('%s:%d：这条能匹配空串，会让每一行都命中：%s' % (path, n, line))
        pats.append(rx)
    return pats, path, warns


def _git(args, **kw):
    try:
        return subprocess.run(['git', '-C', ROOT, '-c', 'core.quotepath=false'] + args,
                              capture_output=True, check=True, **kw).stdout
    except FileNotFoundError:
        raise ConfigError('没找到 git：默认扫描要用 git 列文件；可以改成把文件逐个写在命令行上')
    except subprocess.CalledProcessError as e:
        raise ConfigError('git %s 失败：%s' % (' '.join(args), e.stderr.decode('utf-8', 'replace').strip()))


def workspace_files():
    """已跟踪的文件 + 没被 .gitignore 忽略的新文件。"""
    out = _git(['ls-files', '--cached', '--others', '--exclude-standard', '-z']).decode('utf-8')
    return sorted({os.path.normpath(os.path.join(ROOT, p)) for p in out.split('\0') if p})


def decode(data):
    """按 BOM 解码；不像文本（有 NUL 又不是 UTF-16）返回 None。"""
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16', 'replace')
    if data.startswith(b'\xef\xbb\xbf'):
        return data[3:].decode('utf-8', 'replace')
    if b'\0' in data[:8192]:
        return None
    return data.decode('utf-8', 'replace')


def scan_text(text, deny, builtin=True):
    """返回 [(行号, 规则名, 命中片段)]。allow 标记只豁免内置规则。"""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if builtin and ALLOW_MARK not in line:
            for name, rx in RULES:
                for m in rx.finditer(line):
                    s = m.group(0)
                    if name == 'IP 地址':
                        ok = ipv4_flagged(s, line, m.start()) if '.' in s and ':' not in s else ipv6_flagged(s)
                        if not ok:
                            continue
                    elif name == 'MAC 地址' and not mac_flagged(s):
                        continue
                    elif name == '邮箱' and not email_flagged(s):
                        continue
                    elif name.startswith('EasyEDA') and not hex_flagged(s):
                        continue
                    hits.append((n, name, s))
        for rx in deny or ():
            for m in rx.finditer(line):
                hits.append((n, '禁词', m.group(0)))
    return hits


def is_self(rel):
    return rel.replace('\\', '/') == SELF_REL


def relpath(path):
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:                                    # 不在同一个盘上
        return path


def scan_files(files, deny):
    """返回 (命中列表 [(相对路径, 行号, 规则, 片段)], 实际扫了几个文件)。"""
    hits, scanned = [], 0
    for path in files:
        if os.path.splitext(path)[1].lower() not in TEXT_EXT:
            continue
        with open(path, 'rb') as f:
            text = decode(f.read())
        if text is None:
            continue
        scanned += 1
        rel = relpath(path)
        for n, name, s in scan_text(text, deny, builtin=not is_self(rel)):
            hits.append((rel, n, name, s))
    return hits, scanned


PUBLISHED_REFS = ['HEAD', '--branches', '--tags', '--remotes=origin']


def history_hits(deny, all_refs=False):
    """扫历史：每个提交里每个文件的每个版本（去重后的 blob），加每个提交的作者 / 提交者邮箱和提交信息。
    默认只扫会公开的那些提交：HEAD、本地分支、标签、origin 的远程分支。
    别的远程（比如留作只读存档的旧私有仓库）和 stash 不会被推出去，不扫；要连它们一起扫就给 all_refs=True。"""
    hits = []
    refs = ['--all'] if all_refs else PUBLISHED_REFS
    objs = _git(['rev-list', '--objects'] + refs).decode('utf-8', 'replace').splitlines()
    pairs = [l.split(' ', 1) for l in objs if ' ' in l]
    if pairs:
        kinds = _git(['cat-file', '--batch-check=%(objectname) %(objecttype)'],
                     input='\n'.join(o for o, _ in pairs).encode()).decode().split('\n')
        kind = dict(k.split() for k in kinds if k.strip())
        first_path = {}
        for oid, p in pairs:
            if kind.get(oid) == 'blob' and os.path.splitext(p)[1].lower() in TEXT_EXT:
                first_path.setdefault(oid, p)
        oids = list(first_path)
        data = _git(['cat-file', '--batch'], input='\n'.join(oids).encode()) if oids else b''
        pos = 0
        for _ in oids:
            nl = data.index(b'\n', pos)
            oid, _t, size = data[pos:nl].split()
            size = int(size)
            body = data[nl + 1: nl + 1 + size]
            pos = nl + 1 + size + 1
            p = first_path[oid.decode()]
            text = decode(body)
            if text is None:
                continue
            for n, name, s in scan_text(text, deny, builtin=not is_self(p)):
                hits.append(('历史 %s @%s' % (p, oid.decode()[:8]), n, name, s))
    log = _git(['log'] + refs + ['--format=%H%x00%ae%x00%ce%x00%B%x1e']).decode('utf-8', 'replace')
    for rec in log.split('\x1e'):
        rec = rec.strip('\n')
        if not rec:
            continue
        h, ae, ce, msg = (rec.split('\x00') + ['', '', '', ''])[:4]
        for role, mail in (('作者邮箱', ae), ('提交者邮箱', ce)):
            if mail and '@' in mail and email_flagged(mail):
                hits.append(('提交 %s' % h[:8], 0, role, mail))
        for n, name, s in scan_text(msg, deny):
            hits.append(('提交信息 %s' % h[:8], n, name, s))
    return hits


def staged_hits(deny):
    """只扫暂存区里这次要提交的文件（新增、修改、改名后的版本），读的是暂存区里的内容，不是工作区。
    返回 (命中列表, 扫了几个文件)。"""
    out = _git(['diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z']).decode('utf-8')
    hits, scanned = [], 0
    for rel in (p for p in out.split('\0') if p):
        if os.path.splitext(rel)[1].lower() not in TEXT_EXT:
            continue
        text = decode(_git(['show', ':' + rel]))
        if text is None:
            continue
        scanned += 1
        for n, name, s in scan_text(text, deny, builtin=not is_self(rel)):
            hits.append(('暂存区 %s' % rel, n, name, s))
    return hits, scanned


def message_hits(path, deny):
    """扫一份提交信息。git 编辑器模板里 # 开头的注释行不会进提交，跳过。"""
    if not os.path.isfile(path):
        raise ConfigError('提交信息文件不存在：%s' % path)
    with open(path, 'rb') as f:
        text = decode(f.read()) or ''
    kept = '\n'.join('' if l.startswith('#') else l for l in text.splitlines())
    return [('提交信息', n, name, s) for n, name, s in scan_text(kept, deny)]


def report(hits, limit=None):
    shown = hits if limit is None else hits[:limit]
    for where, n, name, s in shown:
        print('%s:%d: [%s] %s' % (where, n, name, s[:80]))
    if limit is not None and len(hits) > limit:
        print('… 另有 %d 处没列出' % (len(hits) - limit))


def main(argv):
    if '-h' in argv or '--help' in argv:
        print(__doc__)
        return 0
    if '--list-rules' in argv:
        for name, rx in RULES:
            print('%s: %s' % (name, rx.pattern))
        return 0
    try:
        deny, deny_path, warns = load_denylist()
        for w in warns:
            print('⚠️ ' + w)
        if deny is None:
            print('（没有本地禁词表 %s：只跑内置规则。项目名、客户名这类词请写进这个文件，它不进仓库）' % deny_path)
        if '--history' in argv:
            hits = history_hits(deny, all_refs='--all-refs' in argv)
            report(hits, limit=200)
            if hits:
                print('\n历史里共 %d 处。只改当前文件去不掉它们：改写历史，或者用不带历史的新仓库发布。' % len(hits))
                return 1
            print('✅ 历史里没有发现不该公开的内容%s' % ('' if deny is None else '（含本地禁词表 %d 条）' % len(deny)))
            return 0
        if '--message' in argv:
            i = argv.index('--message')
            if i + 1 >= len(argv):
                raise ConfigError('--message 后面要跟提交信息文件的路径')
            hits = message_hits(argv[i + 1], deny)
            report(hits)
            if hits:
                print('\n提交信息里有 %d 处不该公开的内容，这次提交已拦下：改写提交信息再提交。' % len(hits))
                return 1
            return 0
        if '--staged' in argv:
            hits, scanned = staged_hits(deny)
            report(hits)
            if hits:
                print('\n暂存区里共 %d 处，这次提交已拦下：改掉再 git add，'
                      '或确属公开信息就在那一行写 `%s`（只豁免内置规则）。' % (len(hits), ALLOW_MARK))
                return 1
            print('✅ check_public：暂存区 %d 个文本文件干净%s' % (scanned, '' if deny is None else '（含本地禁词表 %d 条）' % len(deny)))
            return 0
        named = [a for a in argv if not a.startswith('--')]
        missing = [a for a in named if not os.path.isfile(a)]
        if missing:
            raise ConfigError('文件不存在：%s' % '、'.join(missing))
        files = [os.path.normpath(os.path.abspath(a)) for a in named] or workspace_files()
        hits, scanned = scan_files(files, deny)
    except ConfigError as e:
        print('❌ %s' % e)
        return 2
    report(hits)
    if hits:
        print('\n共 %d 处。改掉，或确属公开信息就在那一行写 `%s`（只豁免内置规则）。' % (len(hits), ALLOW_MARK))
        return 1
    print('✅ 没有发现不该公开的内容（扫了 %d 个文本文件%s）' % (scanned, '' if deny is None else '，含本地禁词表 %d 条' % len(deny)))
    return 0


if __name__ == '__main__':
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    sys.exit(main(sys.argv[1:]))
