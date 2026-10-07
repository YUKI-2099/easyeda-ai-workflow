"""Offline regression tests for PCB object changes, not routing validity."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pcb_route_diff as diff


def line(ident="L1", **changes):
    obj = {"primitiveId": ident, "primitiveType": "Line", "net": "N", "layer": 1,
           "startX": 0.0, "startY": 0.0, "endX": 100.0, "endY": 0.0, "lineWidth": 10.0}
    obj.update(changes)
    return obj


def snapshot():
    return {
        "lines": [line()],
        "vias": [{"primitiveId": "V1", "primitiveType": "Via", "net": "N", "x": 100.0, "y": 0.0,
                  "diameter": 24.0, "holeDiameter": 12.0, "viaType": 0}],
        "components": [{"primitiveId": "C1", "primitiveType": "Component", "designator": "R1",
                        "x": 50.0, "y": 10.0, "rotation": 270.0, "layer": 1}],
        "pads": [{"primitiveId": "C1P1", "primitiveType": "Pad", "net": "N", "padNumber": "1",
                  "x": 50.0, "y": 0.0, "rotation": math.pi, "layer": 1, "pad": ["RECT", 20.0, 30.0, 0]}],
        "layers": [{"id": 1, "type": "SIGNAL", "layerStatus": 1},
                   {"id": 2, "type": "SIGNAL", "layerStatus": 1},
                   {"id": 3, "type": "SILKSCREEN", "layerStatus": 1},
                   {"id": 15, "type": "SIGNAL", "layerStatus": 1}],
    }


class RouteDiffTests(unittest.TestCase):
    def test_unchanged_and_storage_normalization(self):
        a, b = snapshot(), snapshot()
        b["lines"][0].update(startX=100.0, endX=0.0, async_state="unrelated")
        # An actual metadata change should remain visible.
        self.assertEqual(diff.compare_snapshots(a, b)["categories"]["lines"]["counts"]["changed"], 1)
        del b["lines"][0]["async_state"]
        b["lines"][0]["async"] = True
        b["components"][0]["rotation"] = -90.0
        b["pads"][0]["rotation"] = -math.pi
        report = diff.compare_snapshots(a, b)
        for category in diff.CATEGORIES:
            self.assertEqual(report["categories"][category]["counts"],
                             {"before": 1, "after": 1, "added": 0, "removed": 0, "changed": 0, "unchanged": 1})

    def test_split_line_is_an_observation_not_a_failure(self):
        a, b = snapshot(), snapshot()
        b["lines"] = [line("L2", endX=40.0), line("L3", startX=40.0)]
        report = diff.compare_snapshots(a, b)
        lines = report["categories"]["lines"]
        self.assertEqual(lines["counts"]["added"], 2)
        self.assertEqual(lines["counts"]["removed"], 1)
        self.assertEqual(lines["counts"]["changed"], 0)
        self.assertEqual([x["primitiveId"] for x in lines["added"]], ["L2", "L3"])
        self.assertEqual([x["primitiveId"] for x in lines["removed"]], ["L1"])
        self.assertNotIn("errors", report)
        self.assertIn("not failures", report["interpretation"])

    def test_create_return_shift_is_detected_in_mm(self):
        a, b = snapshot(), snapshot()
        # Synthetic values: the readback lands about 0.1 mm away from the create return.
        returned = line("new", startX=1000.0, endX=1000.0, startY=0.0, endY=40.0)
        actual = {**returned, "startX": 1003.937, "endX": 1003.937}
        b["lines"].append(actual)
        preserved = copy.deepcopy((a, b, returned))
        report = diff.compare_snapshots(a, b, [returned])
        created = report["created_readback"]
        self.assertEqual(created["counts"]["position_shift_candidates"], 1)
        delta = created["changed"][0]["geometry"]["start_displacement"]
        self.assertAlmostEqual(delta["dx_mm"], 0.0999998, places=10)
        self.assertEqual(delta["dy_mm"], 0)
        self.assertIn("not_determined", created["native_snapped_candidates"][0]["cause"])
        self.assertEqual((a, b, returned), preserved)

    def test_net_layer_and_width_changes(self):
        a, b = snapshot(), snapshot()
        b["lines"][0].update(net="OTHER", layer=15, lineWidth=20.0)
        item = diff.compare_snapshots(a, b)["categories"]["lines"]["changed"][0]
        self.assertEqual(item["fields"]["net"], {"before": "N", "after": "OTHER"})
        self.assertEqual(item["fields"]["layer"], {"before": 1, "after": 15})
        self.assertAlmostEqual(item["geometry"]["size_changes"]["lineWidth"]["after_mm"], 0.508)
        self.assertEqual(item["geometry"]["max_displacement_mm"], 0)

    def test_via_position_drill_and_diameter(self):
        a, b = snapshot(), snapshot()
        b["vias"][0].update(x=103.0, y=4.0, diameter=30.0, holeDiameter=14.0)
        item = diff.compare_snapshots(a, b)["categories"]["vias"]["changed"][0]
        self.assertEqual(item["geometry"]["position_displacement"]["distance_mil"], 5.0)
        self.assertAlmostEqual(item["geometry"]["max_displacement_mm"], 0.127)
        self.assertEqual(set(item["geometry"]["size_changes"]), {"diameter", "holeDiameter"})

    def test_component_and_pad_geometry(self):
        a, b = snapshot(), snapshot()
        b["components"][0].update(x=60.0, rotation=0.0)
        b["pads"][0].update(y=5.0, rotation=math.pi / 2, pad=["RECT", 22.0, 30.0, 0])
        report = diff.compare_snapshots(a, b)
        self.assertEqual(report["categories"]["components"]["changed"][0]["geometry"]["rotation_delta_deg"], 90.0)
        pad = report["categories"]["pads"]["changed"][0]
        self.assertAlmostEqual(pad["geometry"]["rotation_delta_deg"], -90.0)
        self.assertIn("pad", pad["fields"])

    def test_missing_arrays_fields_and_duplicate_ids_are_errors(self):
        invalid = []
        a = snapshot(); del a["pads"]; invalid.append(a)
        a = snapshot(); del a["lines"][0]["endX"]; invalid.append(a)
        a = snapshot(); a["vias"][0]["diameter"] = float("nan"); invalid.append(a)
        a = snapshot(); a["lines"].append(line()); invalid.append(a)
        a = snapshot(); a["components"][0]["primitiveId"] = "L1"; invalid.append(a)
        a = snapshot(); a["layers"][0]["id"] = 1.5; invalid.append(a)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(diff.SnapshotError):
                diff.compare_snapshots(value, snapshot())
        with self.assertRaises(diff.SnapshotError):
            diff.compare_snapshots(snapshot(), snapshot(), [{"primitiveId": "unknown"}])

    def test_copper_classification_includes_inner_and_excludes_silk(self):
        a, b = snapshot(), snapshot()
        b["lines"].extend([line("inner", layer=15), line("silk", layer=3)])
        report = diff.compare_snapshots(a, b)
        self.assertEqual(report["copper_lines"]["net_count_change"], 1)
        self.assertEqual(report["categories"]["lines"]["counts"]["added"], 2)
        b["layers"][-1]["layerStatus"] = 2  # HIDDEN remains an enabled copper layer.
        self.assertEqual(diff.compare_snapshots(a, b)["copper_lines"]["net_count_change"], 1)
        del b["layers"]
        self.assertEqual(diff.compare_snapshots(a, b)["copper_lines"]["status"], "unavailable")

    def test_created_missing_after_not_assumed_failed_and_reversal_not_snapped(self):
        a, b = snapshot(), snapshot()
        returned = line()
        b["lines"][0].update(startX=100.0, endX=0.0)
        report = diff.compare_snapshots(a, b, [returned, line("absorbed")])
        created = report["created_readback"]
        self.assertEqual(created["counts"]["missing_after"], 1)
        self.assertEqual(created["counts"]["position_shift_candidates"], 0)
        self.assertEqual(created["counts"]["changed"], 0)

    def test_cli_compact_output_reports_and_protects_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            before, after, report = p / "before.json", p / "after.json", p / "report.json"
            raw = json.dumps(snapshot()).encode("utf-8")
            before.write_bytes(raw); after.write_bytes(raw)
            stdout, stderr = io.StringIO(), io.StringIO()
            args = ["--before", str(before), "--after", str(after), "--output", str(report)]
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = diff.main(args)
            self.assertEqual(rc, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(len(stdout.getvalue().splitlines()), 1)
            self.assertNotIn("startX", stdout.getvalue())
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["sources"]["before"]["path"], str(before.resolve()))
            original_report = report.read_bytes()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(diff.main(args), 2)
            self.assertEqual(report.read_bytes(), original_report)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(diff.main(args[:-1] + [str(before)]), 2)
            self.assertEqual(before.read_bytes(), raw)
            after.write_text('{"lines":[]}', encoding="utf-8")
            report.unlink()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(diff.main(args), 2)
            self.assertFalse(report.exists())

    def test_import_has_no_stdout_or_report_side_effect(self):
        module_dir = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            code = f"import sys; sys.dont_write_bytecode=True; sys.path.insert(0,{str(module_dir)!r}); import pcb_route_diff"
            run = subprocess.run([sys.executable, "-B", "-c", code], cwd=directory, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout + run.stderr, "")
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
