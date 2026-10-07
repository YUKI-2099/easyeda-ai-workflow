# -*- coding: utf-8 -*-
"""patch_easyeda_skill 的离线回归：只在临时目录里造 SKILL.md，不碰真实家目录。

每个用例里 HOME / USERPROFILE 都指到临时目录，CLAUDE_CONFIG_DIR / XDG_CONFIG_HOME / CODEX_HOME 都先删掉；
用到默认三份路径的用例先确认它们落在临时目录里，否则跳过，而且只跑预览，不带 --apply。
补丁里写的仓库路径（P.REPO）换成一个不在临时目录下的假路径，"临时目录仓库"那条规则单独测。
"""
import io
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import patch_easyeda_skill as P  # noqa: E402

SAMPLE = '# easyeda-api\n\nIntro line.\n\n' + P.ANCHOR + '\n\n## 1. Setup\n\nsteps\n'
FAKE_REPO = Path(os.path.abspath(os.sep)) / 'opt' / 'eda-workflow'        # 不在临时目录下、也不需要存在


def with_old_block(body, version='v2 (old/path/patch.py)'):
    block = P.BEGIN_PREFIX + ' ' + version + ' -->\n' + body + '\n' + P.END + '\n'
    return SAMPLE.replace(P.ANCHOR, P.ANCHOR + '\n\n' + block)


class PatchSkillTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.home = self.dir / 'home'
        env = mock.patch.dict(os.environ, {'HOME': str(self.home), 'USERPROFILE': str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        for var in ('CLAUDE_CONFIG_DIR', 'XDG_CONFIG_HOME', 'CODEX_HOME'):
            os.environ.pop(var, None)                                      # patch.dict 结束时会还原
        repo = mock.patch.object(P, 'REPO', FAKE_REPO)
        repo.start()
        self.addCleanup(repo.stop)

    def make(self, rel='skill/SKILL.md', text=SAMPLE, newline='\n', bom=False):
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((P.BOM if bom else b'') + text.replace('\n', newline).encode('utf-8'))
        return p

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = P.main(list(args))
        return rc, out.getvalue() + err.getvalue()

    def backups(self, p):
        return sorted(p.parent.glob(p.name + '.bak-*'))

    def leftovers(self):
        return sorted(self.dir.rglob('*.patch-tmp'))

    def test_preview_writes_nothing(self):
        p = self.make()
        before = p.read_bytes()
        rc, out = self.run_main('--skill', str(p))
        self.assertEqual(rc, 0, out)
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(sorted(x.name for x in p.parent.iterdir()), ['SKILL.md'])     # 没有备份、没有临时文件
        self.assertIn('新打补丁', out)
        self.assertIn('--apply', out)
        self.assertIn('+' + P.BEGIN, out)                                               # 差异里看得到要加的补丁

    def test_apply_once_then_nothing_changes(self):
        p = self.make()
        rc, out = self.run_main('--skill', str(p), '--apply')
        self.assertEqual(rc, 0, out)
        once = p.read_bytes()
        text = once.decode('utf-8')
        self.assertEqual(text.count(P.BEGIN_PREFIX), 1)
        self.assertEqual(text.count(P.END), 1)
        self.assertIn(P.ANCHOR + '\n\n' + P.render_patch(), text)
        self.assertEqual(len(self.backups(p)), 1)
        rc, out = self.run_main('--skill', str(p), '--apply')
        self.assertEqual(rc, 0, out)
        self.assertEqual(p.read_bytes(), once)
        self.assertEqual(len(self.backups(p)), 1)                                        # 没变化就不备份
        self.assertIn('已是最新', out)
        self.assertEqual(self.leftovers(), [])

    def test_old_patch_block_is_replaced_whole(self):
        p = self.make(text=with_old_block('### 0. 旧正文\nold body'))
        rc, out = self.run_main('--skill', str(p), '--apply')
        self.assertEqual(rc, 0, out)
        self.assertIn('刷新旧补丁', out)
        text = p.read_text(encoding='utf-8')
        self.assertNotIn('old body', text)
        self.assertEqual(text, SAMPLE.replace(P.ANCHOR, P.ANCHOR + '\n\n' + P.render_patch()))

    def test_crlf_and_bom_are_kept(self):
        p = self.make(newline='\r\n', bom=True)
        rc, out = self.run_main('--skill', str(p), '--apply')
        self.assertEqual(rc, 0, out)
        raw = p.read_bytes()
        self.assertTrue(raw.startswith(P.BOM))
        body = raw[len(P.BOM):]
        self.assertEqual(body.count(b'\n'), body.count(b'\r\n'))                           # 没有落单的 LF
        self.assertIn(P.render_patch(nl='\r\n').encode('utf-8'), body)
        rc, out = self.run_main('--skill', str(p), '--apply')                             # CRLF 文件同样幂等
        self.assertEqual(rc, 0, out)
        self.assertEqual(p.read_bytes(), raw)

    def test_backup_holds_the_original_bytes(self):
        p = self.make(newline='\r\n')
        before = p.read_bytes()
        rc, out = self.run_main('--skill', str(p), '--apply')
        self.assertEqual(rc, 0, out)
        backups = self.backups(p)
        self.assertEqual(len(backups), 1)
        self.assertRegex(backups[0].name, r'^SKILL\.md\.bak-\d{8}-\d{6}$')
        self.assertEqual(backups[0].read_bytes(), before)
        self.assertEqual(self.leftovers(), [])

    def test_patch_text_has_no_private_paths(self):
        self.assertIsNone(re.search(r'[A-Za-z]:[\\/]', P.PATCH_TEMPLATE))                 # 正文模板里没有任何盘符路径
        repo = self.dir / 'somewhere' / 'eda-workflow'
        text = P.render_patch(repo)
        self.assertIn(str(repo / 'docs' / P.MANUAL), text)
        self.assertIn(str(repo / 'tools'), text)
        self.assertEqual(text.count('\n'), P.PATCH_TEMPLATE.count('\n'))

    def test_default_paths_follow_env_vars(self):
        rel = Path('skills') / 'easyeda-api' / 'SKILL.md'
        self.assertEqual(P.default_copies(), [self.home / '.claude' / rel, self.home / '.config' / 'opencode' / rel,
                                              self.home / '.codex' / rel])
        cc, xdg, cx = self.dir / 'cc', self.dir / 'xdg', self.dir / 'cx'
        with mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(cc), 'XDG_CONFIG_HOME': str(xdg), 'CODEX_HOME': str(cx)}):
            self.assertEqual(P.default_copies(), [cc / rel, xdg / 'opencode' / rel, cx / rel])

    def test_missing_default_copies_are_skipped(self):
        defaults = P.default_copies()
        if not all(str(d).startswith(str(self.dir)) for d in defaults):
            self.skipTest('默认路径没落到临时家目录里，不冒险')
        rc, out = self.run_main()                                                          # 三份都没装
        self.assertEqual(rc, 1, out)
        self.assertEqual(out.count('文件不存在'), 3)
        codex = defaults[2]
        codex.parent.mkdir(parents=True)
        codex.write_text(SAMPLE, encoding='utf-8')
        rc, out = self.run_main()                                                          # 只装了一份：照常预览，另两份跳过
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count('文件不存在'), 2)
        self.assertIn('新打补丁', out)
        self.assertEqual(codex.read_text(encoding='utf-8'), SAMPLE)                        # 预览不写

    def test_explicit_missing_path_is_an_error(self):
        good = self.make()
        before = good.read_bytes()
        rc, out = self.run_main('--skill', str(good), '--skill', str(self.dir / 'nope'), '--apply')
        self.assertEqual(rc, 2, out)
        self.assertIn('找不到', out)
        self.assertEqual(good.read_bytes(), before)                                        # 参数有错就一份都不写
        self.assertEqual(self.backups(good), [])

    def test_skill_dir_and_duplicates_are_processed_once(self):
        p = self.make()
        rc, out = self.run_main('--skill', str(p.parent), '--skill', str(p),
                                '--skill', str(p.parent / '..' / p.parent.name / 'SKILL.md'), '--apply')
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count('同一个文件'), 2)
        self.assertEqual(out.count('新打补丁'), 1)
        self.assertEqual(len(self.backups(p)), 1)
        self.assertEqual(p.read_text(encoding='utf-8').count(P.BEGIN_PREFIX), 1)

    def test_symlink_is_kept_and_its_target_is_patched(self):
        target = self.make('real/SKILL.md')
        link = self.dir / 'linked' / 'SKILL.md'
        link.parent.mkdir()
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest('这台机器建不了软链（Windows 要开发者模式或管理员）')
        rc, out = self.run_main('--skill', str(link), '--skill', str(target), '--apply')
        self.assertEqual(rc, 0, out)
        self.assertTrue(link.is_symlink())
        self.assertIn(P.render_patch(), target.read_text(encoding='utf-8'))
        self.assertEqual(len(self.backups(target)), 1)                                     # 备份在真实文件旁边，只一份
        self.assertEqual(self.backups(link), [])

    def test_linked_skill_dir_is_deduplicated_and_kept(self):
        target = self.make('real/SKILL.md')
        link_dir = self.dir / 'linked'
        try:
            os.symlink(target.parent, link_dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            if os.name != 'nt':
                self.skipTest('这台机器建不了目录软链')
            import _winapi                                     # Windows 上不要权限的"目录联接"
            _winapi.CreateJunction(str(target.parent), str(link_dir))
        rc, out = self.run_main('--skill', str(link_dir), '--skill', str(target), '--apply')
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count('同一个文件'), 1)
        self.assertNotEqual(os.path.normcase(os.path.realpath(link_dir)), os.path.normcase(str(link_dir)))   # 链接还在
        self.assertIn(P.render_patch(), target.read_text(encoding='utf-8'))
        self.assertEqual(len(self.backups(target)), 1)

    def test_unpatchable_files_are_refused_untouched(self):
        no_anchor = self.make('a/SKILL.md', text='# 别的 skill\n')
        stray_end = self.make('c/SKILL.md', text=SAMPLE.replace('Intro line.', 'Intro line. ' + P.END))
        gbk = self.dir / 'b' / 'SKILL.md'
        gbk.parent.mkdir()
        gbk.write_bytes('中文说明'.encode('gbk'))
        for p, why in ((no_anchor, '锚点未命中'), (gbk, 'UTF-8'), (stray_end, '标记不是各 1 处')):
            before = p.read_bytes()
            rc, out = self.run_main('--skill', str(p), '--apply')
            self.assertEqual(rc, 1, out)
            self.assertIn(why, out)
            self.assertEqual(p.read_bytes(), before)
            self.assertEqual(self.backups(p), [])

    def test_read_only_file_is_refused(self):
        p = self.make()
        before = p.read_bytes()
        os.chmod(p, stat.S_IREAD)
        self.addCleanup(os.chmod, p, stat.S_IREAD | stat.S_IWRITE)
        if os.access(p, os.W_OK):
            self.skipTest('以管理员 / root 身份运行，只读属性挡不住')
        rc, out = self.run_main('--skill', str(p), '--apply')
        self.assertEqual(rc, 1, out)
        self.assertIn('只读', out)
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(self.backups(p), [])

    def test_bad_readback_restores_original(self):
        p = self.make()
        before = p.read_bytes()
        real_replace = os.replace

        def replace_then_tamper(src, dst):
            real_replace(src, dst)
            Path(dst).write_bytes(b'tampered by someone else')

        with mock.patch.object(P.os, 'replace', side_effect=replace_then_tamper):
            rc, out = self.run_main('--skill', str(p), '--apply')
        self.assertEqual(rc, 1, out)
        self.assertIn('还原', out)
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(len(self.backups(p)), 1)
        self.assertEqual(self.leftovers(), [])

    def test_failed_replace_leaves_no_temp_file(self):
        p = self.make()
        before = p.read_bytes()
        with mock.patch.object(P.os, 'replace', side_effect=PermissionError('file is locked')):
            rc, out = self.run_main('--skill', str(p), '--apply')
        self.assertEqual(rc, 1, out)
        self.assertIn('写失败', out)
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(self.leftovers(), [])

    def test_temp_dir_repo_needs_explicit_permission_to_apply(self):
        p = self.make()
        before = p.read_bytes()
        temp_repo = self.dir / 'eda-workflow'
        self.assertTrue(P.in_temp_dir(temp_repo))
        self.assertFalse(P.in_temp_dir(FAKE_REPO))
        with mock.patch.object(P, 'REPO', temp_repo):
            rc, out = self.run_main('--skill', str(p))                                     # 预览照常，但有警告
            self.assertEqual(rc, 0, out)
            self.assertIn('临时目录', out)
            rc, out = self.run_main('--skill', str(p), '--apply')                          # 默认拒绝
            self.assertEqual(rc, 2, out)
            self.assertIn('--allow-temp-repo', out)
            self.assertEqual(p.read_bytes(), before)
            rc, out = self.run_main('--skill', str(p), '--apply', '--allow-temp-repo')
            self.assertEqual(rc, 0, out)
        self.assertIn(str(temp_repo / 'docs'), p.read_text(encoding='utf-8'))

    def test_drift_between_copies_is_warned(self):
        a = self.make('a/SKILL.md', text=with_old_block('body A'))
        b = self.make('b/SKILL.md', text=with_old_block('body B'))
        rc, out = self.run_main('--skill', str(a), '--skill', str(b))
        self.assertEqual(rc, 0, out)
        self.assertIn('不一致', out)
        b.write_text(with_old_block('body A'), encoding='utf-8')
        rc, out = self.run_main('--skill', str(a), '--skill', str(b))
        self.assertEqual(rc, 0, out)
        self.assertNotIn('不一致', out)

    def test_script_survives_a_non_utf8_pipe(self):
        p = self.make()
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONUTF8', 'PYTHONIOENCODING')}
        run = subprocess.run([sys.executable, '-B', P.__file__, '--skill', str(p), '--apply', '--allow-temp-repo'],
                             env=env, capture_output=True, timeout=120)
        out = run.stdout.decode('utf-8')
        self.assertEqual(run.returncode, 0, out + run.stderr.decode('utf-8', 'replace'))
        self.assertIn('✅ 补丁在位', out)
        self.assertIn(P.BEGIN, p.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
