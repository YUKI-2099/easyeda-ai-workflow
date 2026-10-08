# -*- coding: utf-8 -*-
"""check_public.py 的回归。不碰网络和 EDA，只用内存字符串、临时文件和临时 git 仓库。

    python -X utf8 -m unittest tools/test_check_public.py

样本都用拼接写出来，而且全是编的：本文件自己也要能过 check_public（不能真的含一条用户路径、邮箱或板子 id）。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_public as cp  # noqa: E402

BS = '\\'
USER_PATH = 'C:' + BS + 'Users' + BS + 'alice' + BS + 'bin' + BS + 'x.exe'
DATA_DRIVE = 'D:' + BS + 'work' + BS + 'board'
EMAIL = 'alice' + '@' + 'mail.test'
TOKEN = 'ghp' + '_' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8'
PAGE_ID = '0123' + '4567' + '89ab' + 'cdef'          # 编的 16 位十六进制，不是任何真实工程的 id
SHA256 = 'ab12' * 16
HAS_GIT = shutil.which('git') is not None


def rules_hit(line, deny=None):
    return [name for _, name, _ in cp.scan_text(line, deny)]


def quiet(fn, *a):
    with redirect_stdout(io.StringIO()):
        return fn(*a)


class Paths(unittest.TestCase):
    def test_user_paths(self):
        for bad in (USER_PATH,
                    '/c/' + 'Users/' + 'alice/AppData',
                    '/mnt/c/' + 'Users/' + 'alice/x',
                    '/c/' + 'Users/' + '张三/x',
                    'cd /home/' + 'bob/x'):
            self.assertIn('本机用户路径', rules_hit(bad), bad)

    def test_unc_and_data_drives(self):
        self.assertIn('网络共享路径', rules_hit(BS * 2 + 'wsl$' + BS + 'Ubuntu' + BS + 'home'))
        self.assertIn('网络共享路径', rules_hit(BS * 2 + 'NAS' + BS + 'share'))
        self.assertIn('非系统盘绝对路径', rules_hit('set EDA_PROJECT=' + DATA_DRIVE))
        self.assertIn('非系统盘绝对路径', rules_hit('cd "/d/' + 'work/board/"'))
        self.assertIn('非系统盘绝对路径', rules_hit('ls /mnt/d/' + 'work/'))

    def test_placeholders_and_env_vars_pass(self):
        for ok in ('%LOCALAPPDATA%' + BS + 'OpenAI' + BS + 'codex.exe',
                   'C:' + BS + 'Users' + BS + '<你>' + BS + 'x',
                   'D:' + BS + '某板子',
                   '~/.claude/skills/easyeda-api',
                   '<项目目录>/netlist',
                   "Python 源码里的 'C:" + BS * 4 + "work'",
                   'https://example.com/a:b/c',
                   'https://github.com/d/foo/'):
            self.assertEqual(rules_hit(ok), [], ok)


class Network(unittest.TestCase):
    def test_ipv4(self):
        self.assertIn('IP 地址', rules_hit('ssh 到 10.' + '0.0.' + '7'))
        self.assertIn('IP 地址', rules_hit('地址是 10.' + '0.0.' + '7。'))
        self.assertIn('IP 地址', rules_hit('地址是 10.' + '0.0.' + '7.'))           # 句号紧跟着也要抓
        self.assertEqual(rules_hit('bridge http://127.0.0.1:49620'), [])
        self.assertEqual(rules_hit('示例 192.0.2.' + '10'), [])                     # 文档专用网段
        self.assertEqual(rules_hit('EDA 3.2.149.88089769'), [])                    # 某段超过 255：版本号
        self.assertEqual(rules_hit('固件 v2.2.' + '40.5'), [])

    def test_ipv6(self):
        self.assertIn('IP 地址', rules_hit('连 fe80::1ff:' + 'fe23:4567:890a'))
        for ok in ('::1 是回环', '2001:db8::1 是文档地址', 'x[1::2]', '会议 12:30 开始', 'a::b'):
            self.assertEqual(rules_hit(ok), [], ok)

    def test_mac(self):
        self.assertIn('MAC 地址', rules_hit('MAC ' + 'aa:bb:cc:' + 'dd:ee:01'))
        self.assertEqual(rules_hit('MAC 00:00:00:00:00:00'), [])

    def test_email_and_ssh(self):
        self.assertIn('邮箱', rules_hit('联系 ' + EMAIL))
        self.assertIn('ssh 登录目标', rules_hit('ssh ' + 'alice' + '@build-box'))
        self.assertEqual(rules_hit('Co-Authored-By: Claude <noreply' + '@anthropic.com>'), [])
        self.assertEqual(rules_hit('x <someone' + '@users.noreply.github.com>'), [])
        self.assertEqual(rules_hit('git clone git' + '@github.com:owner/repo.git'), [])


class Secrets(unittest.TestCase):
    def test_prefixed_tokens(self):
        for bad in ('token: ' + TOKEN,
                    'sk' + '-ant-api03-' + 'A1b2C3d4E5f6G7h8I9j0',
                    'gh' + 's_' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8'):
            self.assertIn('密钥样式', rules_hit(bad), bad)

    def test_assignments(self):
        for bad in ('pass' + 'word = hunter2xyz', '"api' + '_key": "abcdef123"', 'client' + '_secret=abcdef123'):
            self.assertIn('密钥样式', rules_hit(bad), bad)
        self.assertEqual(rules_hit('        token=line.strip()'), [])


class Ids(unittest.TestCase):
    def test_easyeda_ids_and_sha(self):
        self.assertIn('EasyEDA 16 位 id', rules_hit('页 uuid ' + PAGE_ID))
        self.assertIn('完整 SHA-256', rules_hit('SHA ' + SHA256))
        self.assertEqual(rules_hit("const LIB='" + '0819f05c4eef' + '4c71ace90d82' + "2a990e87';"), [])   # 系统库公共 uuid
        self.assertEqual(rules_hit('页 uuid `<16位页uuid>`'), [])
        self.assertEqual(rules_hit('1234567890123456'), [])                # 纯数字不是 id


class Denylist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.deny = self.write('deny.txt', '# 注释行\n(?-i:ZQX)\n秘密工程\n')
        self.old_env = os.environ.pop('EDA_PUBLIC_DENYLIST', None)

    def tearDown(self):
        if self.old_env is not None:
            os.environ['EDA_PUBLIC_DENYLIST'] = self.old_env
        else:
            os.environ.pop('EDA_PUBLIC_DENYLIST', None)
        self.tmp.cleanup()

    def write(self, name, text, encoding='utf-8'):
        p = os.path.join(self.tmp.name, name)
        with open(p, 'w', encoding=encoding) as f:
            f.write(text)
        return p

    def test_words_and_case(self):
        deny, _, warns = cp.load_denylist(self.deny)
        self.assertEqual((len(deny), warns), (2, []))
        self.assertEqual(rules_hit('ZQX 驱动板', deny), ['禁词'])
        self.assertEqual(rules_hit('zqx 小写不算', deny), [])
        self.assertEqual(rules_hit('来自秘密工程的实证', deny), ['禁词'])

    def test_allow_mark_only_exempts_builtin_rules(self):
        deny, _, _ = cp.load_denylist(self.deny)
        self.assertEqual(rules_hit('联系 ' + EMAIL + ' <!-- check_public: allow -->', deny), [])
        self.assertEqual(rules_hit('秘密工程 <!-- check_public: allow -->', deny), ['禁词'])

    def test_broken_entries_are_reported_with_line_numbers(self):
        with self.assertRaisesRegex(cp.ConfigError, r'bad\.txt:2'):
            cp.load_denylist(self.write('bad.txt', '# x\n(未闭合\n'))
        with self.assertRaisesRegex(cp.ConfigError, '空串'):
            cp.load_denylist(self.write('empty.txt', 'foo|\n'))
        with self.assertRaisesRegex(cp.ConfigError, 'UTF-8'):
            cp.load_denylist(self.write('gbk.txt', '秘密工程\n', encoding='gbk'))
        _, _, warns = cp.load_denylist(self.write('inline.txt', '秘密 # 行内注释\n'))
        self.assertEqual(len(warns), 1)

    def test_missing_denylist(self):
        deny, _, _ = cp.load_denylist(os.path.join(self.tmp.name, 'nope.txt'))
        self.assertIsNone(deny)                                              # 默认位置没有：只跑内置规则
        os.environ['EDA_PUBLIC_DENYLIST'] = os.path.join(self.tmp.name, 'nope.txt')
        self.assertEqual(quiet(cp.main, [self.write('a.md', '干净\n')]), 2)   # 显式指了却没有：报错

    def test_main_exit_codes_and_file_handling(self):
        os.environ['EDA_PUBLIC_DENYLIST'] = self.deny
        dirty = self.write('dirty.md', '# 标题\n证据在秘密工程里\n')
        clean = self.write('clean.md', '# 标题\n通用说法\n')
        utf16 = os.path.join(self.tmp.name, 'u16.txt')
        with open(utf16, 'wb') as f:
            f.write('第二行有 ZQX\n'.encode('utf-16'))
        binary = os.path.join(self.tmp.name, 'blob.json')
        with open(binary, 'wb') as f:
            f.write(b'\x00\x01ZQX\x00')
        self.assertEqual(quiet(cp.main, [dirty]), 1)
        self.assertEqual(quiet(cp.main, [clean]), 0)
        self.assertEqual(quiet(cp.main, [utf16]), 1)                         # UTF-16 也要扫到
        self.assertEqual(quiet(cp.main, [binary, clean]), 0)                 # 二进制跳过，不报错
        self.assertEqual(quiet(cp.main, [os.path.join(self.tmp.name, 'nope.md')]), 2)

    def test_path_on_another_drive_does_not_crash(self):
        old = cp.ROOT
        cp.ROOT = 'Q:' + BS + 'elsewhere' if os.name == 'nt' else '/nonexistent-root'
        try:
            hits, scanned = cp.scan_files([self.write('x.md', '干净\n')], None)
            self.assertEqual((hits, scanned), ([], 1))
        finally:
            cp.ROOT = old


@unittest.skipUnless(HAS_GIT, '没有 git')
class History(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.old_root, self.old_env = cp.ROOT, os.environ.pop('EDA_PUBLIC_DENYLIST', None)
        cp.ROOT = self.repo
        self.git('init', '-q')

    def tearDown(self):
        cp.ROOT = self.old_root
        if self.old_env is not None:
            os.environ['EDA_PUBLIC_DENYLIST'] = self.old_env
        self.tmp.cleanup()

    def git(self, *args, email='bot' + '@' + 'example.com'):
        subprocess.run(['git', '-C', self.repo, '-c', 'user.name=bot', '-c', 'user.email=' + email,
                        '-c', 'commit.gpgsign=false'] + list(args), check=True, capture_output=True)

    def commit(self, name, text, msg, email='bot' + '@' + 'example.com'):
        with open(os.path.join(self.repo, name), 'w', encoding='utf-8') as f:
            f.write(text)
        self.git('add', name)
        self.git('commit', '-q', '-m', msg, email=email)

    def test_clean_history_passes(self):
        self.commit('a.md', '通用说法\n', '初始')
        self.assertEqual(quiet(cp.main, ['--history']), 0)

    def test_old_versions_emails_and_messages_are_caught(self):
        self.commit('a.md', '路径 ' + DATA_DRIVE + '\n', '初始')
        self.commit('a.md', '已改成占位 <项目目录>\n', '去掉路径')          # 当前版本干净，旧版本不干净
        self.assertEqual(quiet(cp.main, []), 0)
        self.assertEqual(quiet(cp.main, ['--history']), 1)
        out = io.StringIO()
        with redirect_stdout(out):
            cp.main(['--history'])
        self.assertIn('非系统盘绝对路径', out.getvalue())

    def test_other_remotes_are_skipped_unless_all_refs(self):
        # 本机克隆里还挂着只读存档的旧私有仓库时（remote 叫 private），默认不该把它的旧历史算进来
        self.commit('a.md', '通用说法\n', '初始')
        self.git('checkout', '-q', '-b', 'tmp')
        self.commit('b.md', '路径 ' + DATA_DRIVE + '\n', '存档里的旧提交')
        self.git('update-ref', 'refs/remotes/private/main', 'tmp')
        self.git('checkout', '-q', '-')
        self.git('branch', '-q', '-D', 'tmp')
        self.assertEqual(quiet(cp.main, ['--history']), 0)
        self.assertEqual(quiet(cp.main, ['--history', '--all-refs']), 1)

    def test_message_mode_skips_git_comment_lines(self):
        msg = os.path.join(self.repo, 'MSG')
        with open(msg, 'w', encoding='utf-8') as f:
            f.write('修一处说明\n\n# 请输入提交信息\n# 路径 ' + DATA_DRIVE + ' 在注释里不算\n')
        self.assertEqual(quiet(cp.main, ['--message', msg]), 0)
        with open(msg, 'w', encoding='utf-8') as f:
            f.write('修一处说明，见 ' + DATA_DRIVE + '\n')
        self.assertEqual(quiet(cp.main, ['--message', msg]), 1)
        self.assertEqual(quiet(cp.main, ['--message']), 2)

    def test_staged_mode_reads_the_index_not_the_worktree(self):
        self.commit('a.md', '通用说法\n', '初始')
        with open(os.path.join(self.repo, 'b.md'), 'w', encoding='utf-8') as f:
            f.write('路径 ' + DATA_DRIVE + '\n')
        self.git('add', 'b.md')
        self.assertEqual(quiet(cp.main, ['--staged']), 1)
        with open(os.path.join(self.repo, 'b.md'), 'w', encoding='utf-8') as f:
            f.write('已改成 <项目目录>\n')                                  # 只改工作区、没 add：暂存区里还是旧的
        self.assertEqual(quiet(cp.main, ['--staged']), 1)
        self.git('add', 'b.md')
        self.assertEqual(quiet(cp.main, ['--staged']), 0)

    def test_commit_metadata(self):
        self.commit('a.md', '通用说法\n', '提到 ' + DATA_DRIVE, email=EMAIL)
        hits = cp.history_hits(None)
        kinds = {name for _, _, name, _ in hits}
        self.assertIn('作者邮箱', kinds)
        self.assertIn('非系统盘绝对路径', kinds)


if __name__ == '__main__':
    unittest.main()
