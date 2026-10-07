"""Offline regression checks for the 2026-09-27 local/remote tool merge."""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


TOOLS = Path(__file__).resolve().parent


class MergeRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.project_file = cls.root / "project.json"
        cls.project_file.write_text(json.dumps({
            "name": "merge-regression",
            "project_uuid": "project-test",
            "pages": [],
            "netlist_dir": "netlist",
            "freeze_format": "FREEZE-{tag}.enet",
            "nonelectrical_refs": ["LOGO1"],
        }), encoding="utf-8")
        (cls.root / "netlist").mkdir()
        cls.old_project = os.environ.get("EDA_PROJECT")
        os.environ["EDA_PROJECT"] = str(cls.project_file)
        sys.path.insert(0, str(TOOLS))
        for name in ("project", "bridge", "v2_rows", "v2_geom_audit", "v2_refreeze",
                     "v2_add_parts", "v2_swap_part", "v2_wire_pins"):
            sys.modules.pop(name, None)
        cls.project = importlib.import_module("project")
        cls.refreeze = importlib.import_module("v2_refreeze")
        cls.add = importlib.import_module("v2_add_parts")
        cls.swap = importlib.import_module("v2_swap_part")
        cls.wire = importlib.import_module("v2_wire_pins")

    @classmethod
    def tearDownClass(cls):
        if cls.old_project is None:
            os.environ.pop("EDA_PROJECT", None)
        else:
            os.environ["EDA_PROJECT"] = cls.old_project
        if sys.path and sys.path[0] == str(TOOLS):
            sys.path.pop(0)
        cls.temp.cleanup()

    def test_project_merge_keeps_nonelectrical_and_relative_netlist(self):
        cfg = self.project.load_project(self.project_file)
        self.assertEqual(cfg["nonelectrical_refs"], ["LOGO1"])
        self.assertEqual(cfg["netlist_dir"], str(self.root / "netlist"))

    def test_standard_library_value_is_preserved_without_explicit_override(self):
        for source in (self.add.JS, self.swap.JS_ADD):
            self.assertIn("LIBVAL=op.Value||''", source.replace("o2.getState", "o.getState"))
            self.assertRegex(source, r"const v=VAL\|\|\(op\.Value\?'':\(o2?\.getState_ManufacturerId\(\)\|\|''\)\)")
            self.assertIn("if(v&&v!==op.Value)", source)

    def test_swap_rounds_coordinates_and_all_old_designator_nets_are_touched(self):
        self.assertIn("Math.round(v*100)/100", self.swap.JS_DEL)
        before = {"SHARED": ["U2-1", "R1-1"], "UNCHANGED": ["R2-1", "R3-1"]}
        after = {"SHARED": ["R1-1"], "UNCHANGED": ["R2-1", "R3-1"]}
        deleted = {"pos": {"x": 10, "y": 20}, "oldPins": 1, "killedWires": 1,
                   "killedSyms": 0, "killedNets": []}
        calls = []

        def fake_ex(code, timeout=0):
            calls.append(code)
            if code == self.swap.JS_DEL % {"page": "page-test", "des": json.dumps("U2")}:
                return deleted
            self.fail("unexpected bridge wrapper call in offline test")

        # run() 会打印 ✅；不加 -X utf8 时 Windows 管道是 GBK，直接打到 stdout 会 UnicodeEncodeError，所以这里收进内存
        with mock.patch.object(self.swap, "netmap", side_effect=[before, after]), \
             mock.patch.object(self.swap.v2_rows, "ex", side_effect=fake_ex), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(self.swap.run({"page": "page-test", "swaps": [{"des": "U2", "lcsc": None}]}))
        self.assertEqual(len(calls), 1)

    def test_stub_and_flag_rotation_merge_is_consistent_across_three_tools(self):
        modules = (self.add, self.swap, self.wire)
        defaults = [module.flag_rot({}) for module in modules]
        self.assertEqual(defaults[0], defaults[1])
        self.assertEqual(defaults[1], defaults[2])
        custom = {"flag_rot": {"left": {"gnd": 123}}}
        for module in modules:
            value = module.flag_rot(custom)
            self.assertEqual(value["left"]["gnd"], 123)
            self.assertEqual(value["right"], module.DEFAULT_FLAG_ROT["right"])
            self.assertIn("STUB=%(stub)s", module.JS if module is not self.swap else module.JS_ADD)
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertIn("DEFAULT_FLAG_ROT 和 spec 的 flag_rot 都是 createNetFlag/createNetPort", source)
            self.assertIn("create_input = (360 - getState_Rotation()) % 360", source)

    def test_freeze_sha_name_cannot_alias_enet_or_enet_txt(self):
        source = (TOOLS / "v2_refreeze.py").read_text(encoding="utf-8")
        self.assertIn("re.sub(r'\\.enet(\\.txt)?$|\\.txt$', '', fn) + '-sha256.txt'", source)
        self.assertIn("os.path.abspath(shafn) == os.path.abspath(fn)", source)
        self.assertNotIn("fn.replace('.enet.txt', '-sha256.txt')", source)


if __name__ == "__main__":
    unittest.main()
