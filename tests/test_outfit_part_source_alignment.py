"""Outfit parts must stay vertex-exact to their source after the import flow.

Run: blender --background --factory-startup --python tests/test_outfit_part_source_alignment.py

Imports Shroud outfit parts plus Body/Head/Hair (outfit-first and body-first)
at slot 2 and slot 0, then measures every mesh's armature-local vertices
against its source .uemodel LOD. Any rigid offset >= 0.01 mm fails.
Skips when the ArcPak character cache is missing.
"""
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "native_runtime"))
from native_test_support import namespace  # noqa: E402

namespace()
P = "_native_runtime_regression_addon"
uem = importlib.import_module(P + ".Shared.uemodel_import")
rig = importlib.import_module(P + ".Animations.rig")
layout = importlib.import_module(P + ".Outfit_Accessories.import_layout")
app = importlib.import_module(P + ".Outfit_Accessories.appearance")

CH = (Path(os.environ.get("APPDATA", "")) / "Blender Foundation/Blender/5.1/scripts/addons/"
      "Arc-Asset-Importer/ArcPak/cache/PioneerGame/Content/Pioneer/Characters")
PARTS = ["UpperBody", "LowerBody", "Accessories", "Bag", "Boots", "Bracelet", "Headgear", "Scarf"]
OUTFIT = [str(CH / "Assets/Shroud" / p / f"SK_Shroud_{p}.uemodel") for p in PARTS]
BODY = str(CH / "Shared/Bodies/SK_Body.uemodel")
HEAD = str(CH / "Heads/Face_08_Pio/Base/Head_Face_08_Pio_Base.uemodel")
HAIR = str(CH / "Hairs/CurlyShort/SK_CurlyShort.uemodel")
TOL_MM = 0.01

for _name in ("arc_appearance_head", "arc_appearance_hair"):
    if not hasattr(bpy.types.Scene, _name):
        setattr(bpy.types.Scene, _name, bpy.props.StringProperty(default="NONE"))

_LODS = {}


def _alive(o):
    try:
        return o is not None and o.name in bpy.data.objects
    except ReferenceError:
        return False


def _wipe():
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.armatures):
        for block in list(coll):
            coll.remove(block)


def _source_lod(path, obj):
    if path not in _LODS:
        _LODS[path] = uem.parse_uemodel(path)[2]
    lods = _LODS[path]
    return next((l for l in lods if l.name == obj.data.get("arc_uemodel_lod")), lods[0])


def _offsets():
    """Return {mesh name: (offset_mm xyz, residual_mm)} in armature space."""
    bpy.context.view_layer.update()
    from mathutils.kdtree import KDTree
    out = {}
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        path = o.get("arc_psk_path") or o.get("arc_source_path") or ""
        if not path or not os.path.isfile(path):
            continue
        src = np.asarray(_source_lod(path, o).vertices, dtype=float)
        mw = np.array(o.matrix_world)
        co = np.empty(len(o.data.vertices) * 3)
        o.data.vertices.foreach_get("co", co)
        w = co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
        arm = o.find_armature()
        if arm is not None:
            aw = np.array(arm.matrix_world.inverted())
            w = w @ aw[:3, :3].T + aw[:3, 3]
        if len(w) == len(src):
            d = w - src
        else:  # split meshes (boots L/R): nearest source vertex
            tree = KDTree(len(src))
            for i, p in enumerate(src):
                tree.insert(p, i)
            tree.balance()
            sample = w[:: max(1, len(w) // 400)]
            d = np.array([p - np.array(tree.find(p)[0]) for p in sample])
        d = d * 1000.0
        mean = d.mean(0)
        out[o.name] = (mean, float(np.abs(d - mean).max()))
    return out


def _import_outfit(slot):
    objs = []
    for path in OUTFIT:
        new = app._import_uemodel_outfit(path)
        for o in new:
            if o.type == "MESH":
                o["arc_psk_path"] = path
        objs.extend(new)
    rig.fix_rig_all(objs, merge=True, model_type="clothing")
    objs = [o for o in objs if _alive(o)]
    layout.place_imported(objs, "Outfit", scene=bpy.context.scene, prefer_slot=slot)
    return next(o for o in objs if _alive(o) and o.type == "ARMATURE")


def _import_appearance(slot, attach):
    sc = bpy.context.scene
    head_id = next(item[0] for item in app.make_head_enum_items(sc, bpy.context) if item[0] != "NONE") if sc.bl_rna.properties["arc_appearance_head"].type == "ENUM" else "H"
    hair_id = next(item[0] for item in app.make_hair_enum_items(sc, bpy.context) if item[0] != "NONE") if sc.bl_rna.properties["arc_appearance_hair"].type == "ENUM" else "R"
    sc.arc_appearance_head = head_id
    sc.arc_appearance_hair = hair_id
    paths = {head_id: HEAD, hair_id: HAIR}
    with patch.object(app, "list_body_mesh", return_value=BODY), \
         patch.object(app, "resolve_choice_path", side_effect=lambda i: paths.get(i, "")), \
         patch.object(app, "resolve_choice", side_effect=lambda i: {"path": paths.get(i, ""), "label": i}):
        app.import_appearance_parts(bpy.context, roles={"Body", "Head", "Hair"}, attach_to=attach,
                                    layout_slot=slot if attach is None else None, defer_materials=[])


@unittest.skipUnless(all(os.path.isfile(p) for p in OUTFIT + [BODY, HEAD, HAIR]),
                     "ArcPak character cache missing")
class OutfitPartSourceAlignment(unittest.TestCase):
    def _check(self, order, slot):
        _wipe()
        if order == "outfit_first":
            _import_appearance(slot, _import_outfit(slot))
        else:
            _import_appearance(slot, None)
            _import_outfit(slot)
        offsets = _offsets()
        bad = []
        for name, (mean, res) in sorted(offsets.items()):
            mag = float(np.abs(mean).max())
            print(f"ALIGN {order} slot{slot} {name[:34]:34s} off_mm=({mean[0]:.3f},{mean[1]:.3f},{mean[2]:.3f}) res={res:.4f}")
            if mag >= TOL_MM:
                bad.append((name, tuple(round(float(v), 3) for v in mean)))
        self.assertGreaterEqual(len(offsets), 12, f"too few meshes measured: {sorted(offsets)}")
        self.assertEqual(bad, [], f"{order} slot {slot}: parts off source")

    def test_outfit_first_slot2(self):
        self._check("outfit_first", 2)

    def test_outfit_first_slot0(self):
        self._check("outfit_first", 0)

    def test_body_first_slot2(self):
        self._check("body_first", 2)

    def test_body_first_slot0(self):
        self._check("body_first", 0)


if __name__ == "__main__":
    result = unittest.main(argv=[sys.argv[0]], exit=False, verbosity=2).result
    sys.exit(0 if result.wasSuccessful() else 1)
