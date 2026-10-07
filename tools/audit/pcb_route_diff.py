"""Offline PCB snapshot differences; standard library only, with no EDA access.

The report describes object changes. It does not decide whether a split, an ID
replacement, a connection, or a native-snapping candidate is electrically valid.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

MM_PER_MIL = 0.0254
GEOMETRY_TOLERANCE_MIL = 0.001
CATEGORIES = ("lines", "vias", "components", "pads")
TYPE_TO_CATEGORY = {"Line": "lines", "Via": "vias", "Component": "components", "Pad": "pads"}
REQUIRED = {
    "lines": ("net", "layer", "startX", "startY", "endX", "endY", "lineWidth"),
    "vias": ("net", "x", "y", "diameter", "holeDiameter"),
    "components": ("x", "y", "rotation", "layer"),
    "pads": ("x", "y", "rotation", "layer", "net", "padNumber", "pad"),
}
MIL_FIELDS = {"x", "y", "startX", "startY", "endX", "endY", "lineWidth", "diameter", "holeDiameter", "pad"}


class SnapshotError(ValueError):
    """The input is incomplete or cannot be compared safely."""


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_object(obj: Any, category: str, where: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise SnapshotError(f"{where}: expected an object")
    if not isinstance(obj.get("primitiveId"), str) or not obj["primitiveId"]:
        raise SnapshotError(f"{where}: missing/non-string primitiveId")
    for field in REQUIRED[category]:
        if field not in obj:
            raise SnapshotError(f"{where}: missing {field}")
    numbers = set(REQUIRED[category]) - {"net", "padNumber", "pad"}
    for field in numbers:
        if not _number(obj[field]):
            raise SnapshotError(f"{where}.{field}: expected a finite number")
    if "layer" in numbers and int(obj["layer"]) != obj["layer"]:
        raise SnapshotError(f"{where}.layer: expected an integer layer ID")
    if "net" in REQUIRED[category] and not isinstance(obj["net"], str):
        raise SnapshotError(f"{where}.net: expected a string, including empty for no net")
    if category == "pads" and not isinstance(obj["pad"], list):
        raise SnapshotError(f"{where}.pad: expected the native pad-shape array")
    if obj.get("primitiveType") not in (None, next(t for t, c in TYPE_TO_CATEGORY.items() if c == category)):
        raise SnapshotError(f"{where}: primitiveType disagrees with category")
    return obj


def _snapshot(data: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(data, dict):
        raise SnapshotError(f"{label}: expected a snapshot object")
    maps: dict[str, dict[str, Any]] = {}
    global_ids: set[str] = set()
    for category in CATEGORIES:
        if category not in data or not isinstance(data[category], list):
            raise SnapshotError(f"{label}: required {category} array is missing or invalid")
        maps[category] = {}
        for index, item in enumerate(data[category]):
            obj = _validate_object(item, category, f"{label}.{category}[{index}]")
            ident = obj["primitiveId"]
            if ident in global_ids:
                raise SnapshotError(f"{label}: duplicate primitiveId {ident}")
            global_ids.add(ident)
            maps[category][ident] = obj
    return maps


def _equal(a: Any, b: Any, tolerance: float = 1e-9) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if _number(a) and _number(b):
        return abs(a - b) <= tolerance
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k], tolerance) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_equal(x, y, tolerance) for x, y in zip(a, b))
    return a == b


def _angle_delta(a: float, b: float, period: float) -> float:
    return (b - a + period / 2) % period - period / 2


def _vector(a: tuple[float, float], b: tuple[float, float]) -> dict[str, Any]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    distance = math.hypot(dx, dy)
    return {
        "dx_mil": dx, "dy_mil": dy, "distance_mil": distance,
        "dx_mm": dx * MM_PER_MIL, "dy_mm": dy * MM_PER_MIL,
        "distance_mm": distance * MM_PER_MIL,
    }


def _aligned_line(a: dict[str, Any], b: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """LINE direction is not physical: compare the best endpoint pairing."""
    aa = [(a["startX"], a["startY"]), (a["endX"], a["endY"])]
    bb = [(b["startX"], b["startY"]), (b["endX"], b["endY"])]
    direct = sum(math.dist(x, y) for x, y in zip(aa, bb))
    reverse = sum(math.dist(x, y) for x, y in zip(aa, bb[::-1]))
    if reverse < direct:
        b = dict(b)
        b["startX"], b["endX"] = b["endX"], b["startX"]
        b["startY"], b["endY"] = b["endY"], b["startY"]
        return b, True
    return b, False


def _difference(a: dict[str, Any], original_b: dict[str, Any], category: str) -> dict[str, Any] | None:
    b, reversed_order = _aligned_line(a, original_b) if category == "lines" else (original_b, False)
    fields: dict[str, Any] = {}
    for key in sorted(a.keys() | b.keys()):
        if key == "async":  # wrapper execution state, not design data
            continue
        if key in a and key in b:
            if key == "rotation" and _number(a[key]) and _number(b[key]):
                period = math.tau if category == "pads" else 360.0
                equal = abs(_angle_delta(a[key], b[key], period)) <= 1e-6
            else:
                equal = _equal(a[key], b[key], GEOMETRY_TOLERANCE_MIL if key in MIL_FIELDS else 1e-9)
            if equal:
                continue
        fields[key] = {"before": a.get(key), "after": b.get(key)}
        if key not in a or key not in b:
            fields[key].update(before_present=key in a, after_present=key in b)
    if not fields:
        return None
    geometry: dict[str, Any] = {}
    if category == "lines":
        start = _vector((a["startX"], a["startY"]), (b["startX"], b["startY"]))
        end = _vector((a["endX"], a["endY"]), (b["endX"], b["endY"]))
        geometry.update(start_displacement=start, end_displacement=end,
                        max_displacement_mm=max(start["distance_mm"], end["distance_mm"]),
                        endpoint_order_reversed_for_comparison=reversed_order)
    else:
        vector = _vector((a["x"], a["y"]), (b["x"], b["y"]))
        geometry.update(position_displacement=vector, max_displacement_mm=vector["distance_mm"])
    sizes = {}
    for key in ("lineWidth", "diameter", "holeDiameter"):
        if key in fields and _number(a.get(key)) and _number(b.get(key)):
            sizes[key] = {"before_mil": a[key], "after_mil": b[key],
                          "before_mm": a[key] * MM_PER_MIL, "after_mm": b[key] * MM_PER_MIL}
    if sizes:
        geometry["size_changes"] = sizes
    if "rotation" in fields and _number(a.get("rotation")) and _number(b.get("rotation")):
        delta = _angle_delta(a["rotation"], b["rotation"], math.tau if category == "pads" else 360.0)
        geometry["rotation_delta_deg"] = math.degrees(delta) if category == "pads" else delta
    return {"primitiveId": a["primitiveId"], "fields": fields, "geometry": geometry}


def _brief(obj: dict[str, Any], category: str) -> dict[str, Any]:
    keys = ("primitiveId", "designator", "primitiveType") + REQUIRED[category]
    return {key: obj[key] for key in keys if key in obj}


def _copper_layers(snapshot: dict[str, Any]) -> set[int] | None:
    """Classify only from this snapshot's optional native layer description."""
    layers = snapshot.get("layers")
    if not isinstance(layers, list):
        return None
    copper = set()
    for layer in layers:
        if (not isinstance(layer, dict) or not _number(layer.get("id"))
                or int(layer["id"]) != layer["id"]):
            raise SnapshotError("layers: each optional layer record needs an integer id")
        if layer.get("type") == "SIGNAL" and layer.get("layerStatus") in (1, 2):
            copper.add(int(layer["id"]))
    return copper or None


def compare_snapshots(before: Any, after: Any, created: Any = None) -> dict[str, Any]:
    """Return serializable observations. Does not read files or mutate inputs."""
    bm, am = _snapshot(before, "before"), _snapshot(after, "after")
    report: dict[str, Any] = {
        "schema_version": 1,
        "units": {"snapshot_lengths": "mil", "reported_displacements": "mil and mm",
                  "pad_rotation": "radians", "component_rotation": "degrees",
                  "geometry_tolerance_mil": GEOMETRY_TOLERANCE_MIL},
        "interpretation": "Object differences only; split/replaced IDs are not failures. Connectivity, DRC and snap causes are not evaluated.",
        "categories": {},
    }
    for category in CATEGORIES:
        old, new = bm[category], am[category]
        added, removed = sorted(new.keys() - old.keys()), sorted(old.keys() - new.keys())
        changed = [d for ident in sorted(old.keys() & new.keys()) if (d := _difference(old[ident], new[ident], category))]
        report["categories"][category] = {
            "counts": {"before": len(old), "after": len(new), "added": len(added), "removed": len(removed),
                       "changed": len(changed), "unchanged": len(old.keys() & new.keys()) - len(changed)},
            "added": [_brief(new[ident], category) for ident in added],
            "removed": [_brief(old[ident], category) for ident in removed],
            "changed": changed,
        }
    bl, al = _copper_layers(before), _copper_layers(after)
    if bl is not None and al is not None:
        bc = sum(obj["layer"] in bl for obj in bm["lines"].values())
        ac = sum(obj["layer"] in al for obj in am["lines"].values())
        report["copper_lines"] = {"status": "available", "before_layer_ids": sorted(bl), "after_layer_ids": sorted(al),
                                  "before": bc, "after": ac, "net_count_change": ac - bc,
                                  "classification": "optional native layers: enabled SIGNAL records only"}
    else:
        report["copper_lines"] = {"status": "unavailable", "reason": "Both snapshots need enabled SIGNAL layer records; no top/bottom-only assumption is made."}
    if created is not None:
        if not isinstance(created, list):
            raise SnapshotError("created: expected an array of native create-return objects")
        changed, missing, candidates = [], [], []
        seen = set()
        matched = 0
        for index, obj in enumerate(created):
            if not isinstance(obj, dict) or obj.get("primitiveType") not in TYPE_TO_CATEGORY:
                raise SnapshotError(f"created[{index}]: missing/unsupported primitiveType")
            category = TYPE_TO_CATEGORY[obj["primitiveType"]]
            _validate_object(obj, category, f"created[{index}]")
            ident = obj["primitiveId"]
            if ident in seen:
                raise SnapshotError(f"created: duplicate primitiveId {ident}")
            seen.add(ident)
            actual = am[category].get(ident)
            if actual is None:
                missing.append({"category": category, "primitiveId": ident})
                continue
            matched += 1
            delta = _difference(obj, actual, category)
            if delta:
                delta["category"] = category
                changed.append(delta)
                if delta["geometry"]["max_displacement_mm"] > GEOMETRY_TOLERANCE_MIL * MM_PER_MIL:
                    candidates.append({"category": category, "primitiveId": ident,
                                       "max_displacement_mm": delta["geometry"]["max_displacement_mm"],
                                       "cause": "not_determined; native endpoint/object snapping is only a candidate"})
        report["created_readback"] = {
            "counts": {"returned": len(created), "matched": matched, "missing_after": len(missing),
                       "changed": len(changed), "position_shift_candidates": len(candidates)},
            "changed": changed, "missing_after": missing,
            "native_snapped_candidates": candidates,
        }
    return report


def _reject_json_constant(value: str) -> None:
    raise SnapshotError(f"Non-finite JSON number: {value}")


def _read_json(path: Path) -> tuple[Any, str]:
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_json_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"Cannot read {path}: {exc}") from exc
    return data, hashlib.sha256(raw).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--created", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        inputs = {"before": args.before, "after": args.after}
        if args.created is not None:
            inputs["created"] = args.created
        for path in inputs.values():
            if args.output.resolve() == path.resolve() or (args.output.exists() and path.exists() and args.output.samefile(path)):
                raise SnapshotError("--output must not overwrite an input snapshot")
        if args.output.exists():
            raise SnapshotError("--output already exists; refusing to overwrite audit evidence")
        loaded = {key: _read_json(path) for key, path in inputs.items()}
        report = compare_snapshots(loaded["before"][0], loaded["after"][0], loaded.get("created", (None,))[0])
        report["sources"] = {key: {"path": str(inputs[key].resolve()), "sha256": value[1]} for key, value in loaded.items()}
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        summary = {"counts": {key: report["categories"][key]["counts"] for key in CATEGORIES},
                   "created": report.get("created_readback", {}).get("counts"), "output": str(args.output)}
        print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (SnapshotError, OSError, ValueError) as exc:
        print(f"pcb_route_diff: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
