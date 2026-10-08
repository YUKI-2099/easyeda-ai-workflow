# -*- coding: utf-8 -*-
"""install_hooks.py 的端到端回归：在临时 git 仓库里装上钩子，真的去提交，看该拦的拦没拦住。
不连 EDA、不联网；用户级 / 系统级 git 配置都隔离掉，不受本机设置影响。

    python -X utf8 -m unittest tools/test_install_hooks.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
HAS_GIT = shutil.which('git') is not None
BS = '\\'


@unittest.skipUnless(HAS_GIT, '没有 git')
class InstallHooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, 'repo')
        os.makedirs(os.path.join(self.repo, 'tools'))
        for name in ('check_public.py', 'install_hooks.py'):
            shutil.copy(os.path.join(TOOLS, name), os.path.join(self.repo, 'tools', name))
        empty_cfg = os.path.join(self.tmp.name, 'gitconfig')
        open(empty_cfg, 'w').close()
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=empty_cfg, GIT_CONFIG_NOSYSTEM='1',
                        PYTHONDONTWRITEBYTECODE='1')
        self.env.pop('EDA_PUBLIC_DENYLIST', None)
        self.git('init', '-q')
        self.write('.gitignore', '.public-denylist.txt\n__pycache__/\n')
        self.write('.public-denylist.txt', '秘密工程\n')
        self.git('add', '-A')
        self.assertEqual(self.commit('初始').returncode, 0)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        with open(os.path.join(self.repo, rel), 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)

    def run_(self, args):
        return subprocess.run(args, cwd=self.repo, env=self.env, capture_output=True, text=True,
                              encoding='utf-8', errors='replace')

    def git(self, *args):
        r = self.run_(['git', '-c', 'user.name=t', '-c', 'user.email=t' + '@example.com',
                       '-c', 'commit.gpgsign=false'] + list(args))
        return r

    def commit(self, msg):
        return self.git('commit', '-q', '-m', msg)

    def hooks(self, *args):
        return self.run_([sys.executable, '-X', 'utf8', os.path.join('tools', 'install_hooks.py')] + list(args))

    def count(self):
        return int(self.git('rev-list', '--count', 'HEAD').stdout.strip())

    def test_blocks_denied_words_and_bad_messages_and_lets_clean_commits_through(self):
        r = self.hooks()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hooks('--check').returncode, 0)

        self.write('a.md', '通用说法\n')
        self.git('add', 'a.md')
        r = self.commit('加一份通用说明')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        n = self.count()

        self.write('b.md', '实证来自秘密工程\n')
        self.git('add', 'b.md')
        r = self.commit('加实证')
        self.assertNotEqual(r.returncode, 0)                                  # 禁词：pre-commit 拦下
        self.assertIn('拦下', r.stdout + r.stderr)
        self.assertEqual(self.count(), n)

        self.write('b.md', '实证来自某板\n')
        self.git('add', 'b.md')
        r = self.commit('见 ' + 'C:' + BS + 'Users' + BS + 'alice' + BS + 'x')
        self.assertNotEqual(r.returncode, 0)                                  # 提交信息里的本机路径：commit-msg 拦下
        self.assertEqual(self.count(), n)

        r = self.commit('补一条实证')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.count(), n + 1)

    def test_only_staged_content_counts(self):
        self.hooks()
        self.write('c.md', '干净的版本\n')
        self.git('add', 'c.md')
        self.write('c.md', '工作区里又写了秘密工程，但没 add\n')          # 没进暂存区的改动不算
        r = self.commit('加 c')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_foreign_hook_is_kept_unless_forced_and_uninstall_only_removes_ours(self):
        hooks_dir = os.path.join(self.repo, '.git', 'hooks')
        os.makedirs(hooks_dir, exist_ok=True)
        with open(os.path.join(hooks_dir, 'pre-commit'), 'w', newline='\n') as f:
            f.write('#!/bin/sh\nexit 0\n')
        self.assertEqual(self.hooks().returncode, 2)                         # 有别的钩子：不覆盖
        self.assertEqual(self.hooks('--check').returncode, 1)
        r = self.hooks('--force')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue([n for n in os.listdir(hooks_dir) if n.startswith('pre-commit.bak-')])
        self.assertEqual(self.hooks('--check').returncode, 0)
        self.assertEqual(self.hooks().returncode, 0)                          # 再装一次：已是最新
        self.assertEqual(self.hooks('--uninstall').returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(hooks_dir, 'pre-commit')))
        self.assertFalse(os.path.exists(os.path.join(hooks_dir, 'commit-msg')))


if __name__ == '__main__':
    unittest.main()
