# -*- coding: utf-8 -*-
"""templates/ 下样板的回归：能导入、导入无副作用、找工具仓库和比对逻辑对。不连桥接、不联网。

    python -X utf8 -m unittest tools/test_templates.py
"""
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, 'templates', 'project_script.py')


def load_template():
    spec = importlib.util.spec_from_file_location('project_script_template', TEMPLATE)
    mod = importlib.util.module_from_spec(spec)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        spec.loader.exec_module(mod)
    return mod, out.getvalue()


class ProjectScriptTemplate(unittest.TestCase):
    def setUp(self):
        self.mod, self.import_output = load_template()
        self.old_env = os.environ.pop('EDA_WORKFLOW', None)

    def tearDown(self):
        if self.old_env is not None:
            os.environ['EDA_WORKFLOW'] = self.old_env
        else:
            os.environ.pop('EDA_WORKFLOW', None)

    def test_import_has_no_side_effects(self):
        self.assertEqual(self.import_output, '')

    def test_find_eda_workflow(self):
        os.environ['EDA_WORKFLOW'] = ROOT
        self.assertEqual(os.path.normcase(self.mod.find_eda_workflow()), os.path.normcase(ROOT))
        with tempfile.TemporaryDirectory() as tmp:
            os.environ['EDA_WORKFLOW'] = tmp                       # 指错了：明确退出，不瞎找
            with self.assertRaises(SystemExit):
                self.mod.find_eda_workflow()
            del os.environ['EDA_WORKFLOW']
            for name in ('easyeda-ai-workflow', 'eda-workflow'):     # GitHub 上的名字和作者本机的目录名都要认
                with tempfile.TemporaryDirectory() as ws:
                    tools = os.path.join(ws, name, 'tools')
                    os.makedirs(tools)
                    open(os.path.join(tools, 'bridge.py'), 'w').close()
                    deep = os.path.join(ws, 'my-board', 'eda')
                    os.makedirs(deep)
                    self.assertEqual(os.path.normcase(self.mod.find_eda_workflow(deep)),
                                     os.path.normcase(os.path.join(ws, name)))

    def test_compare(self):
        cfg = {'project_uuid': 'p-1', 'pages': [('p1 电源', 'u1'), ('p2 主控', 'u2')]}
        live = [{'uuid': 'u1', 'name': 'P1 电源'}, {'uuid': 'u2', 'name': '主控与接口'}]
        errors, notes = self.mod.compare(cfg, 'p-1', live)
        self.assertEqual(errors, [])
        self.assertEqual(len(notes), 1)                                  # 大小写不同不算，名字不同只提示
        errors, _ = self.mod.compare(cfg, 'p-2', live[:1])
        self.assertEqual(len(errors), 2)                                 # 工程不对 + 少一页
        self.mod.ONLY_PAGES = ['p1 电源']
        try:
            self.assertEqual(self.mod.compare(cfg, 'p-1', live[:1])[0], [])
            self.mod.ONLY_PAGES = ['不存在的页']
            self.assertEqual(len(self.mod.compare(cfg, 'p-1', live)[0]), 1)
        finally:
            self.mod.ONLY_PAGES = []


if __name__ == '__main__':
    unittest.main()
