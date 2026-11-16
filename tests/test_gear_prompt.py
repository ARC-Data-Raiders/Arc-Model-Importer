"""Duplicate-gear prompt: detection, keep/hide/remove finalize, orphan safety (needs bpy).

Run inside Blender: blender --background --factory-startup --python tests/test_gear_prompt.py
"""
import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import bpy
except ImportError:
    bpy = None

ROOT = Path(__file__).resolve().parents[1]


def _module(addon_name):
    return importlib.import_module(addon_name + ".Outfit_Accessories.icon_browser")


def _clear():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)


def _armature(name="Master"):
    arm = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(arm)
    return arm


def _gear(name, role, arm, *, mesh=None, via_modifier=False):
    mesh = mesh or bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["arc_assembly_role"] = role
    if via_modifier:
        mod = obj.modifiers.new("Armature", "ARMATURE")
        mod.object = arm
    else:
        obj.parent = arm
    return obj


def _names():
    return [o.name for o in bpy.data.objects]


def run(addon_name):
    ib = _module(addon_name)
    results = {}

    # Detection: item role only, visible only, on this armature only.
    _clear()
    arm, other = _armature("Master"), _armature("Other")
    charm = _gear("Charm_A", "Charm", arm)
    _gear("Connector_A", "CharmConnector", arm)
    _gear("Charm_Other", "Charm", other)
    bp = _gear("Backpack_A", "Backpack", arm, via_modifier=True)
    found = ib.existing_gear(arm, "CHARM")
    assert [o.name for o in found] == ["Charm_A"], found
    assert [o.name for o in ib.existing_gear(arm, "BACKPACK")] == ["Backpack_A"]
    assert ib.existing_gear(arm, "RAIDER_TOOL") == []
    charm.hide_viewport = True
    assert ib.existing_gear(arm, "CHARM") == []
    charm.hide_viewport = False
    helpers = {o.name for o in ib.gear_with_helpers(arm, "CHARM")}
    assert helpers == {"Charm_A", "Connector_A"}, helpers
    # An add-on parented under the backpack is not part of the backpack.
    addon = _gear("Attach_A", "Attachment", arm)
    addon.parent = bp
    assert "Attach_A" not in {o.name for o in ib.gear_with_helpers(arm, "BACKPACK")}
    results["detect"] = "ok"

    # Invoke detection: ADD mode with an armature and an existing item asks; otherwise not.
    plans = {
        "ask": {"armature": arm},
        "standalone": {"armature": None, "standalone": True},
        "replace": {"armature": arm, "replace": True},
        "error": {"armature": arm, "error": "busy"},
    }
    for label, plan in plans.items():
        with patch.object(ib, "_destination", lambda _ctx, plan=plan: plan):
            _arm, existing = ib.prompt_needed(bpy.context, "CHARM")
            assert bool(existing) == (label == "ask"), (label, existing)
    with patch.object(ib, "_destination", lambda _ctx: {"armature": arm}):
        assert ib.prompt_needed(bpy.context, "RAIDER_TOOL")[1] == []
    results["invoke_detect"] = "ok"

    def scenario(mode):
        _clear()
        arm = _armature("Master")
        shared = bpy.data.meshes.new("SharedCharmMesh")
        old = _gear("Charm_Old", "Charm", arm, mesh=shared)
        conn = _gear("Connector_Old", "CharmConnector", arm)
        old_names = sorted(o.name for o in ib.gear_with_helpers(arm, "CHARM"))
        before = _names()
        # Stub import: the new charm reuses the old mesh datablock (shared cache data).
        new = _gear("Charm_New", "Charm", arm, mesh=shared)
        result = ib.finalize_existing(arm.name, "CHARM", mode, old_names, before)
        return result, old, conn, new, shared

    result, old, conn, new, _ = scenario("KEEP")
    visible = [o.name for o in bpy.data.objects if o.get("arc_assembly_role") == "Charm" and ib._visible(o)]
    assert result == "skipped" and len(visible) == 2, (result, visible)
    results["keep"] = "%d visible" % len(visible)

    result, old, conn, new, _ = scenario("HIDE")
    assert old.hide_viewport and old.hide_render and conn.hide_render, result
    assert ib._visible(new) and not new.hide_render
    results["hide"] = result

    result, _old, _conn, new, shared = scenario("REMOVE")
    assert "Charm_Old" not in bpy.data.objects and "Connector_Old" not in bpy.data.objects, result
    assert new.name in bpy.data.objects and new.data == shared and shared.name in bpy.data.meshes
    assert "Connector_Old" not in bpy.data.meshes, "orphan mesh left behind"
    results["remove"] = result

    # Failed import (no new item): the old item must stay untouched.
    _clear()
    arm = _armature("Master")
    old = _gear("Charm_Old", "Charm", arm)
    res = ib.finalize_existing(arm.name, "CHARM", "REMOVE", [old.name], _names())
    assert res == "no-new-item" and old.name in bpy.data.objects
    results["no_new_item"] = res

    # Timer finalizer waits while the staged job runs, then applies.
    state = {"armature": arm.name, "part": "CHARM", "mode": "HIDE", "old": [old.name],
             "before": _names(), "deadline": 1e18}
    with patch.object(ib, "_job_busy", lambda: True):
        assert ib._finalize_timer(state) == 0.5
    _gear("Charm_New", "Charm", arm)
    with patch.object(ib, "_job_busy", lambda: False):
        assert ib._finalize_timer(state) is None
    assert old.hide_viewport
    results["timer"] = "ok"

    # Operator: registered, enum default KEEP, EXEC_DEFAULT takes existing_mode.
    rna = bpy.ops.arc_outfits.add_gear_part.get_rna_type()
    prop = rna.properties["existing_mode"]
    assert prop.default == "KEEP", prop.default
    assert [i.name for i in prop.enum_items] == [
        "Add and keep existing prop", "Add and hide existing prop", "Add and remove existing prop"]
    results["operator"] = "ok"
    _clear()
    return results


@unittest.skipIf(bpy is None, "needs Blender")
class GearPromptTest(unittest.TestCase):
    def test_gear_prompt(self):
        sys.path.insert(0, str(ROOT.parent))
        addon = importlib.import_module(ROOT.name)
        if not hasattr(bpy.types, "ARC_OUTFITS_OT_add_gear_part"):
            addon.register()
        print("GEAR_PROMPT", run(ROOT.name))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], exit=False)
