# -*- coding: utf-8 -*-
"""给这份克隆装两个本地 git 钩子，每次提交自动跑公开自查（带本地禁词表），有命中就拦下这次提交：
  - pre-commit：`check_public.py --staged`，扫暂存区里这次要提交的文件；
  - commit-msg：`check_public.py --message <文件>`，扫提交信息。

钩子不进仓库、不随 clone 走：每台机器、每份克隆装一次（同一个仓库的各个 git worktree 共用一套钩子）。

    python -X utf8 tools/install_hooks.py              # 安装；已经装过就更新
    python -X utf8 tools/install_hooks.py --check      # 只看装没装，不改
    python -X utf8 tools/install_hooks.py --uninstall  # 卸掉本脚本装的钩子

已经有别的同名钩子（不是本脚本装的）时不覆盖，除非加 --force：先把原来的备份成 <钩子名>.bak-<时间戳>。
仓库设了 core.hooksPath 时，装到那个目录。
退出码：0 成功；1 --check 发现没装全；2 出错（不是 git 仓库、遇到别的钩子又没给 --force…）。
"""
import os
import stat
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = '# installed by tools/install_hooks.py (public-repo self-check)'

# 钩子在 git 自带的 sh 里跑（Windows 上是 Git for Windows 的 sh），用当前克隆里的 tools/check_public.py
_FIND_PY = '''ROOT=$(git rev-parse --show-toplevel) || exit 1
for PY in python python3 py; do
  if command -v "$PY" >/dev/null 2>&1; then
    exec "$PY" -X utf8 "$ROOT/tools/check_public.py" %s
  fi
done
echo "check_public: 找不到 Python，没法做公开自查，这次提交先拦下（装好 Python 再提交）" >&2
exit 1
'''
HOOKS = {
    'pre-commit': '#!/bin/sh\n%s\n# 公开仓库：提交前带本地禁词表扫暂存区，有命中就拦下这次提交\n%s' % (MARK, _FIND_PY % '--staged'),
    'commit-msg': '#!/bin/sh\n%s\n# 公开仓库：扫提交信息，有命中就拦下这次提交\n%s' % (MARK, _FIND_PY % '--message "$1"'),
}


class HookError(Exception):
    pass


def _git(*args):
    r = subprocess.run(['git', '-C', ROOT] + list(args), capture_output=True)
    if r.returncode != 0:
        raise HookError('git %s 失败：%s' % (' '.join(args), r.stderr.decode('utf-8', 'replace').strip()))
    return r.stdout.decode('utf-8', 'replace').strip()


def hooks_dir():
    """钩子目录：core.hooksPath（相对路径按仓库根算）> 公共 git 目录下的 hooks（worktree 共用）。"""
    try:
        custom = _git('config', '--get', 'core.hooksPath')
    except HookError:
        custom = ''
    if custom:
        return os.path.normpath(custom if os.path.isabs(custom) else os.path.join(ROOT, custom))
    common = _git('rev-parse', '--git-common-dir')
    if not os.path.isabs(common):
        common = os.path.join(ROOT, common)
    return os.path.normpath(os.path.join(common, 'hooks'))


def _read(path):
    with open(path, 'rb') as f:
        return f.read().decode('utf-8', 'replace')


def status():
    """返回 {钩子名: '已装' / '未装' / '别的钩子' / '旧版'}。"""
    d = hooks_dir()
    out = {}
    for name, body in HOOKS.items():
        p = os.path.join(d, name)
        if not os.path.exists(p):
            out[name] = '未装'
        elif MARK not in _read(p):
            out[name] = '别的钩子'
        else:
            out[name] = '已装' if _read(p) == body else '旧版'
    return out


def install(force=False):
    d = hooks_dir()
    os.makedirs(d, exist_ok=True)
    st = status()
    foreign = [n for n, s in st.items() if s == '别的钩子']
    if foreign and not force:
        raise HookError('%s 下已有别的钩子：%s。不覆盖；确定要换就加 --force（会先备份）' % (d, '、'.join(foreign)))
    done = []
    for name, body in HOOKS.items():
        p = os.path.join(d, name)
        if st[name] == '已装':
            continue
        if st[name] == '别的钩子':
            bak = '%s.bak-%s' % (p, time.strftime('%Y%m%d-%H%M%S'))
            os.replace(p, bak)
            done.append('%s（原来的备份为 %s）' % (name, os.path.basename(bak)))
        else:
            done.append(name)
        with open(p, 'wb') as f:                       # 钩子是 sh 脚本，必须是 LF 换行
            f.write(body.encode('utf-8'))
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return d, done


def uninstall():
    d = hooks_dir()
    removed = []
    for name in HOOKS:
        p = os.path.join(d, name)
        if os.path.exists(p) and MARK in _read(p):
            os.remove(p)
            removed.append(name)
    return d, removed


def main(argv):
    try:
        if '--check' in argv:
            st = status()
            for name, s in st.items():
                print('%-10s %s' % (name, s))
            return 0 if all(s == '已装' for s in st.values()) else 1
        if '--uninstall' in argv:
            d, removed = uninstall()
            print('已卸掉：%s（%s）' % ('、'.join(removed) or '没有本脚本装的钩子', d))
            return 0
        d, done = install(force='--force' in argv)
        print('✅ 钩子已就位（%s）：%s' % (d, '、'.join(done) if done else '原来就是最新的，没有改动'))
        if not os.path.isfile(os.path.join(ROOT, '.public-denylist.txt')) and not os.environ.get('EDA_PUBLIC_DENYLIST'):
            print('⚠️ 这份克隆还没有本地禁词表 .public-denylist.txt：钩子只跑内置规则。项目名、客户名这类词请写进这个文件（它不进仓库）。')
        return 0
    except HookError as e:
        print('❌ %s' % e)
        return 2


if __name__ == '__main__':
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    sys.exit(main(sys.argv[1:]))
