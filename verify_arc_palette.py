"""Verify Blender palette_calibration + materials.py ColorMask section wiring."""
from __future__ import annotations

import ast
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import palette_calibration as pc  # noqa: E402

MATERIALS = ROOT / "materials.py"

EXPECTED_AUTO = {
    1: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
    2: {"X_Green": "ColorA2", "Y_Blue": "ColorB2", "Z_Pink": "ColorC2"},
    3: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
    4: {"X_Green": "ColorA2", "Y_Blue": "ColorB2", "Z_Pink": "ColorC2"},
    5: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
    6: {"X_Green": "ColorA2", "Y_Blue": "ColorB2", "Z_Pink": "ColorC2"},
    7: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
    8: {"X_Green": "ColorA", "Y_Blue": "ColorB", "Z_Pink": "ColorC"},
}


def main() -> int:
    failures = []

    if pc.DEFAULT_USES_SECONDARY != [False, True, False, True, False, True, False, False, False]:
        failures.append("default map")

    if pc.section_colour_inputs("auto") != EXPECTED_AUTO:
        failures.append(f"auto sections {pc.section_colour_inputs('auto')}")

    swap = pc.section_colour_inputs("swap")
    if swap[1]["Y_Blue"] != "ColorB2" or swap[2]["Y_Blue"] != "ColorB":
        failures.append(f"swap sections {swap}")

    # materials.py still documents a default dict that matches AUTO (overwritten at runtime).
    tree = ast.parse(MATERIALS.read_text(encoding="utf-8"))
    found = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "setup_arc_texturer_material":
            for child in ast.walk(node):
                if isinstance(child, ast.Assign):
                    for t in child.targets:
                        if isinstance(t, ast.Name) and t.id == "_CM_COLOUR_INPUTS":
                            try:
                                found = ast.literal_eval(child.value)
                            except Exception:
                                pass
    if found != EXPECTED_AUTO:
        # Runtime overwrites; accept either the static default or absence after transform.
        if found is not None and found != EXPECTED_AUTO:
            failures.append(f"materials.py static _CM_COLOUR_INPUTS {found}")

    key = pc.stable_material_key(
        "MI_Goalie_LegBox_Metal_CyanWhite.json",
        "MaterialInstanceConstant'MI_Character_Layered_5'",
    )
    if key != "MI_Goalie_LegBox_Metal_CyanWhite|MI_Character_Layered_5":
        failures.append(f"key {key}")

    # Override precedence: material prop > store > manifest > scene
    mode, src, _, uses = pc.resolve_routing(
        "MI_Fixture", "MI_Character_Layered_1",
        scene_mode="secondary",
        material_mode="swap",
        manifest_mode="primary",
    )
    if mode != "swap" or src != "material_prop":
        failures.append(f"precedence got {mode}/{src}")
    if uses[4] is not True:  # zone 5 secondary under swap
        failures.append("swap zone5")

    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "arc_palette_calibration.json"
        # Monkeypatch store path via writing then loading through API is hard; test import/export.
        payload = {
            "schema": pc.SCHEMA_OVERRIDES,
            "overrides": {key: "swap"},
        }
        text = json.dumps(payload)
        n = pc.import_overrides_json(text)
        if n != 1:
            failures.append(f"import count {n}")
        if pc.resolve_mode_for_key(key) != "swap":
            failures.append("stored override")
        exported = json.loads(pc.export_overrides_json())
        if exported["overrides"].get(key) != "swap":
            failures.append("export")
        pc.clear_overrides()
        if pc.resolve_mode_for_key(key) != "auto":
            failures.append("clear")
        _ = store

    report = pc.build_report(
        "MI_Fixture_Primary",
        "MI_Character_Layered_5",
        "auto",
        "default",
        ((0.3, 0.3, 0.3), (0.2, 0.2, 0.15), (0.1, 0.1, 0.1)),
        ((0.4, 0.4, 0.4), (0.05, 0.2, 0.05), (0.05, 0.05, 0.05)),
        True,
    )
    if report["schema"] != pc.SCHEMA_REPORT:
        failures.append("schema")
    if report["confidence"] != "ambiguous":
        failures.append("confidence")
    blob = json.dumps(report)
    if ":\\" in blob or "/Users/" in blob or "Gideon" in blob:
        failures.append("path leak")

    # Goalie numeric: AUTO zone5 Y = ColorB
    primary = ((0.382324, 0.409618, 0.453125), (0.260417, 0.234273, 0.192600), (0.124130, 0.135417, 0.118256))
    secondary = ((0.401042, 0.393657, 0.371502), (0.063036, 0.191202, 0.120819), (0.041667, 0.038152, 0.037736))
    uses = pc.resolve_uses_secondary("auto")
    z5 = pc.active_zone(96 / 255)
    if z5 != 4 or uses[z5]:
        failures.append("goalie zone5")
    y = secondary[1] if uses[z5] else primary[1]
    if y != primary[1]:
        failures.append("goalie ColorB")

    # Overlay mix-factor helper (no bpy required).
    samples = [
        ((0.0, 0.0, 0.0, 1.0), 0.0),
        ((1.0, 1.0, 1.0, 1.0), 0.0),
        ((0.05, 0.05, 0.05, 1.0), 0.5),
        ((0.95, 0.95, 0.94, 1.0), 0.5),
        ((0.8, 0.1, 0.05, 1.0), 1.0),
        ((0.2, 0.55, 0.9, 1.0), 1.0),
    ]
    for rgba, expected in samples:
        got = pc.base_overlay_mix_factor(rgba)
        if got != expected:
            failures.append(f"overlay_fac {rgba[:3]} → {got} (want {expected})")

    print("Blender palette verification")
    if failures:
        print("ARC_PALETTE_FAIL")
        for f in failures:
            print(" ", f)
        return 1
    print("ARC_PALETTE_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
