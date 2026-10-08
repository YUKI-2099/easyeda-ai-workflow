# -*- coding: utf-8 -*-
"""导入零副作用的回归：干净环境（没设 EDA_PROJECT、当前目录是空的临时目录）里导入 tools/ 下的脚本模块，
不许有任何输出、不许因为找不到 project.json 而退出、不许在当前目录留下文件。

配置（project.json、项目根的 notes.py）都只在脚本真正运行时才读；导入不读、不连桥接、不执行项目代码。
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parent
MODULES = ('v2_rows', 'v2_refreeze', 'v2_notes_dump', 'v2_geom_audit', 'v2_add_parts', 'v2_swap_part',
           'v2_wire_pins', 'v2_route_pin', 'v2_strip_wire_nets', 'v2_free_spot', 'v2_add_note', 'v2_notes',
           'v2_notes_place', 'pcb_geom', 'project', 'bridge', 'safe_rename', 'pcb_route_batch',
           'patch_easyeda_skill', 'manual_index', 'check_public', 'install_hooks')


class ImportCleanTest(unittest.TestCase):
    def test_modules_import_silently_without_project(self):
        env = {k: v for k, v in os.environ.items() if k not in ('EDA_PROJECT', 'EDA_BRIDGE')}
        code = ('import sys; sys.dont_write_bytecode = True; sys.path.insert(0, %r); import %s'
                % (str(TOOLS), ', '.join(MODULES)))
        with tempfile.TemporaryDirectory() as d:
            run = subprocess.run([sys.executable, '-B', '-c', code], cwd=d, env=env, timeout=120,
                                 capture_output=True, encoding='utf-8', errors='replace')
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(run.stdout + run.stderr, '')
            self.assertEqual(os.listdir(d), [])

    def test_power_nets_and_pages_are_read_when_used(self):
        sys.path.insert(0, str(TOOLS))
        self.addCleanup(sys.path.remove, str(TOOLS))
        import project
        import v2_geom_audit
        import v2_rows
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / 'project.json'
            cfg.write_text('{"power_nets": ["VBUS", "+3V3"], "pages": [["p1", "page-a"]]}', encoding='utf-8')
            with mock.patch.dict(os.environ, {'EDA_PROJECT': str(cfg)}):
                self.assertEqual(v2_rows.power_nets(), {'VBUS', '+3V3'})      # 只认 project.json，脚本不另加电源网
                self.assertEqual(v2_rows.POWER, {'VBUS', '+3V3'})             # 旧写法照样能用（取值时才读）
                self.assertEqual(v2_geom_audit.PAGES, [('p1', 'page-a')])
            empty = Path(d) / 'no-project'
            empty.mkdir()
            with mock.patch.dict(os.environ, {'EDA_PROJECT': str(empty)}):     # 没有 project.json：用默认表
                self.assertEqual(v2_rows.power_nets(), set(project.DEFAULTS['power_nets']))


if __name__ == '__main__':
    unittest.main()
