"""User-authored studio light rigs (assets/studio_lights/<id>.json) imported as exact copies.

Each rig JSON is written by .local/export_studio_rig.py from the blend the rig was authored in:
world-space light transforms, every simple Light / Light.cycles RNA value, light node inputs,
object ray visibility, the world background and the scene view transform. Import rebuilds the
lights in one collection ("Arc Studio Lights: <label>"), replacing an earlier import of the same rig.
"""
from __future__ import annotations

import json
import os
from typing import Any

import bpy
from mathutils import Matrix

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "studio_lights")
_CACHE: dict[str, dict[str, Any]] = {}
_ITEMS: list[tuple[str, str, str]] = []


def rigs() -> dict[str, dict[str, Any]]:
    if not _CACHE and os.path.isdir(_DIR):
        for name in sorted(os.listdir(_DIR)):
            if not name.lower().endswith(".json"):
                continue
            try:
                with open(os.path.join(_DIR, name), "r", encoding="utf-8") as fh:
                    rig = json.load(fh)
            except (OSError, ValueError) as exc:
                print(f"Arc Raiders: studio light rig {name} unreadable: {exc}")
                continue
            if rig.get("schema") == "arc-studio-lights-v1" and rig.get("lights"):
                _CACHE[str(rig.get("id") or name[:-5])] = rig
    if not _CACHE.get("outfit_inspect"):
        from . import inspect_lights  # code-built rig, placed against the active character
        _CACHE[inspect_lights.RIG_ID] = inspect_lights.rig_stub()
    return _CACHE


def make_rig_items(self, context):
    _ITEMS[:] = [(rid, str(r.get("label") or rid), f"{len(r['lights'])} lights, authored in {os.path.basename(str(r.get('source') or ''))}")
                 for rid, r in rigs().items()] or [("NONE", "No studio rigs", "assets/studio_lights is empty")]
    return _ITEMS


def _set_rna(target, values: dict[str, Any]) -> None:
    # type first: it decides which other properties exist
    for key in sorted(values, key=lambda k: k != "type"):
        if not hasattr(target, key):
            continue
        try:
            setattr(target, key, values[key])
        except (AttributeError, TypeError, ValueError):
            pass


def _apply_tree(tree, spec: dict[str, Any]) -> None:
    tree.nodes.clear()
    by_name = {}
    for nd in spec.get("nodes") or []:
        try:
            node = tree.nodes.new(nd["type"])
        except RuntimeError:
            continue
        node.name = nd["name"]
        by_name[nd["name"]] = node
        _set_rna(node, nd.get("props") or {})  # operation/data type first: they decide which inputs exist
        for ident, value in (nd.get("inputs") or {}).items():
            sock = next((s for s in node.inputs if s.identifier == ident), None)
            if sock is not None and hasattr(sock, "default_value"):
                try:
                    sock.default_value = value
                except (TypeError, ValueError):
                    pass
    for a, out_id, b, in_id in spec.get("links") or []:
        na, nb = by_name.get(a), by_name.get(b)
        if na is None or nb is None:
            continue
        so = next((s for s in na.outputs if s.identifier == out_id), None)
        si = next((s for s in nb.inputs if s.identifier == in_id), None)
        if so is not None and si is not None:
            tree.links.new(so, si)


def import_rig(scene, rig_id: str, *, world: bool = True, view: bool = True, camera: bool = True,
               context=None) -> tuple[bool, str]:
    rig = rigs().get(rig_id)
    if not rig:
        return False, f"Studio light rig '{rig_id}' not found in assets/studio_lights"
    if rig.get("builder") == "inspect":
        from . import inspect_lights
        return inspect_lights.build(scene, camera=camera, context=context)
    label = str(rig.get("label") or rig_id)
    cname = f"Arc Studio Lights: {label}"
    old = bpy.data.collections.get(cname)
    if old is not None:
        for ob in list(old.objects):
            data = ob.data
            bpy.data.objects.remove(ob, do_unlink=True)
            if data is not None and data.users == 0:
                bpy.data.lights.remove(data)
        bpy.data.collections.remove(old)
    coll = bpy.data.collections.new(cname)
    scene.collection.children.link(coll)
    for spec in rig["lights"]:
        light = bpy.data.lights.new(spec["name"], spec["type"])
        _set_rna(light, spec.get("light") or {})
        if hasattr(light, "cycles"):
            _set_rna(light.cycles, spec.get("cycles") or {})
        if spec.get("use_nodes") and spec.get("node_tree"):
            light.use_nodes = True
            _apply_tree(light.node_tree, spec["node_tree"])
        ob = bpy.data.objects.new(spec["name"], light)
        coll.objects.link(ob)
        ob.matrix_world = Matrix(spec["matrix_world"])
        ob.hide_render = bool(spec.get("hide_render"))
        _set_rna(ob, spec.get("object") or {})
        ob.hide_set(bool(spec.get("hide_viewport")))
        ob["arc_studio_rig"] = rig_id
    notes = [f"{len(rig['lights'])} lights"]
    w = rig.get("world")
    if world and w:
        wd = bpy.data.worlds.get(f"Arc Studio World: {label}") or bpy.data.worlds.new(f"Arc Studio World: {label}")
        wd.color = w.get("color") or wd.color
        if w.get("use_nodes") and w.get("node_tree"):
            wd.use_nodes = True
            _apply_tree(wd.node_tree, w["node_tree"])
        scene.world = wd
        notes.append("world")
    vs = rig.get("view_settings")
    if view and vs:
        if rig.get("display_device"):
            try:
                scene.display_settings.display_device = rig["display_device"]
            except TypeError:
                pass
        _set_rna(scene.view_settings, vs)
        notes.append(f"view {vs.get('view_transform')}")
    if camera and rig.get("camera"):
        from . import scenario_cameras
        note = scenario_cameras.create(scene, str(rig["camera"]), context)
        if note:
            notes.append(note)
    return True, f"Imported studio lights '{label}': " + ", ".join(notes)


