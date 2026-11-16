"""Background Blender check for Outfit_Accessories.gear_cache.

Run:
  blender --background --factory-startup --python tests/test_gear_cache_blender.py
Set ARC_GEAR_CACHE_DIR to a scratch folder first; the test wipes the role files in it.
"""

import hashlib
import importlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root.parent))
FAILS = []


def diff(a, b):
    out = []
    for ra, rb in zip(a, b):
        for k in ra:
            if ra.get(k) != rb.get(k):
                out.append(f"{ra['base']}.{k}: {str(ra.get(k))[:160]} != {str(rb.get(k))[:160]}")
    if len(a) != len(b):
        out.append(f"len {len(a)} != {len(b)}")
    return "; ".join(out)


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'} {name} {detail}".rstrip())
    if not cond:
        FAILS.append(name)


def portable_value(value):
    # backpack_instances._value keys IDs by pointer, which differs between sessions.
    if isinstance(value, bpy.types.ID):
        path = getattr(value, "filepath", "") or ""
        return ("ID", type(value).__name__, value.name.split(".")[0], os.path.normcase(bpy.path.abspath(path)) if path else "")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return list(value)
    except TypeError:
        return repr(value)


def node_records(mat, bi):
    rows = {}
    for node in mat.node_tree.nodes:
        props = bi._rna_values(node, {'inputs', 'outputs', 'internal_links', 'parent'})
        ins = [(sk.identifier, bi._value(sk.default_value)) for sk in node.inputs if hasattr(sk, 'default_value')]
        rows[node.name] = json.dumps([props, ins, sorted((str(k), bi._value(v)) for k, v in node.items())], default=repr)
    rows["__mat__"] = json.dumps(bi._rna_values(mat, {'name', 'name_full', 'node_tree', 'preview', 'users', 'is_updated',
                                    'is_updated_data', 'is_updated_viewport', 'original', 'session_uid',
                                    'library_weak_reference'}), default=repr)
    return rows


RECORDS = {}


def explain(a_key, b_key):
    a, b = RECORDS.get(a_key, {}), RECORDS.get(b_key, {})
    for name in sorted(set(a) | set(b)):
        if a.get(name) != b.get(name):
            ja, jb = a.get(name, ""), b.get(name, "")
            i = next((k for k in range(min(len(ja), len(jb))) if ja[k] != jb[k]), 0)
            print(f"DIFF {name}: ...{ja[max(0, i-120):i+80]} || ...{jb[max(0, i-120):i+80]}")
            break


def summary(objs, bi, key=None):
    bi._value = portable_value
    out = []
    for obj in sorted(objs, key=lambda o: (o.type, o.name.split(".")[0])):
        row = {"type": obj.type, "base": obj.name.split(".")[0]}
        if obj.type == "MESH":
            row["verts"] = len(obj.data.vertices)
            row["polys"] = len(obj.data.polygons)
            row["mesh_sig"] = bi.mesh_signature(obj)
            mats = []
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None:
                    mats.append(None)
                    continue
                nodes = len(mat.node_tree.nodes) if mat.node_tree else 0
                links = len(mat.node_tree.links) if mat.node_tree else 0
                # Graph hash: backpack_instances.material_signature fields, minus the
                # append provenance (library_weak_reference) that a library load adds.
                graph = hashlib.sha256(json.dumps(sorted(node_records(mat, bi).items())).encode()).hexdigest()
                mats.append((mat.name.split(".")[0], nodes, links, graph))
                if key:
                    RECORDS[key] = node_records(mat, bi)
            row["mats"] = mats
            row["groups"] = sorted(g.name for g in obj.vertex_groups)[:3] + [len(obj.vertex_groups)]
            row["parent"] = obj.parent.type if obj.parent else None
        elif obj.type == "ARMATURE":
            row["bones"] = len(obj.data.bones)
        out.append(row)
    return out


def main():
    cache = os.environ.get("ARC_GEAR_CACHE_DIR")
    if not cache:
        raise SystemExit("ARC_GEAR_CACHE_DIR not set")
    addon = importlib.import_module(root.name)
    addon.register()
    gc = importlib.import_module(root.name + ".Outfit_Accessories.gear_cache")
    app = importlib.import_module(root.name + ".Outfit_Accessories.appearance")
    bi = importlib.import_module(root.name + ".Outfit_Accessories.backpack_instances")
    base = root / "ArcPak/cache/PioneerGame/Content/Pioneer/Characters/Backpacks"
    parts = {
        "BackpackStrap": str(base / "Straps/Default/Strap/SK_Default_Strap.uemodel"),
        "BackpackFrame": str(base / "Frames/Default/SM_Backpack_Frame_01_A.uemodel"),
    }
    for role in parts:
        for ext in (".blend", ".json"):
            p = os.path.join(gc.cache_dir(), role + ext)
            if os.path.isfile(p):
                os.remove(p)

    def run(role, path, slot=None):
        t0 = time.perf_counter()
        ok, msg, objs = gc.import_cached(
            role, path, lambda: app.import_appearance_mesh(path), import_slot=slot,
        )
        dt = time.perf_counter() - t0
        return ok, objs, dt, dict(gc.LAST_EVENT)

    built = {}
    for role, path in parts.items():
        bpy.ops.wm.read_homefile(use_empty=True)
        ok, objs, dt, ev = run(role, path)
        print(f"TIME {role} cold {dt:.3f}s result={ev.get('result')}")
        check(f"{role} cold ok", ok and objs, str(len(objs or [])))
        check(f"{role} cold miss", ev.get("result") == "miss")
        check(f"{role} library written", ev.get("written") is True
              and os.path.isfile(os.path.join(gc.cache_dir(), role + ".blend")))
        built[role] = summary(objs, bi, "built")

        bpy.ops.wm.read_homefile(use_empty=True)
        ok, objs, dt, ev = run(role, path, slot=1)
        print(f"TIME {role} warm {dt:.3f}s result={ev.get('result')}")
        check(f"{role} warm library hit", ev.get("result") == "hit-library")
        warm = summary(objs, bi, "warm")
        explain("built", "warm")
        check(f"{role} built==cached", warm == built[role],
              diff(built[role], warm))
        check(f"{role} instances in scene", all(o.name in bpy.context.scene.objects for o in objs))
        check(f"{role} slot stamped", all(int(o.get("arc_import_slot") or -1) == 1 for o in objs))
        nmesh = len(bpy.data.meshes)

        ok, objs2, dt, ev = run(role, path, slot=2)
        print(f"TIME {role} in-file {dt:.3f}s result={ev.get('result')}")
        check(f"{role} in-file hit", ev.get("result") == "hit-file")
        check(f"{role} in-file same", summary(objs2, bi) == built[role], diff(built[role], summary(objs2, bi)))
        check(f"{role} in-file new data", len(bpy.data.meshes) > nmesh
              and not set(objs2) & set(objs))

        # Signature change: alter the stored stamp.
        meta = os.path.join(gc.cache_dir(), role + ".json")
        stamp = json.load(open(meta, encoding="utf-8"))
        stamp["sig"] = "stale"
        json.dump(stamp, open(meta, "w", encoding="utf-8"))
        bpy.ops.wm.read_homefile(use_empty=True)
        ok, objs, dt, ev = run(role, path)
        print(f"TIME {role} stale-stamp {dt:.3f}s result={ev.get('result')}")
        check(f"{role} stale stamp rebuild", ev.get("result") == "miss" and ev.get("written") is True)
        check(f"{role} stamp rewritten", gc.read_stamp(role).get("sig") == ev.get("sig"))
        check(f"{role} rebuilt same", summary(objs, bi) == built[role], diff(built[role], summary(objs, bi)))

    # Deferred material path: miss with pending materials, then capture_pending.
    role, path = "BackpackFrame", parts["BackpackFrame"]
    os.remove(os.path.join(gc.cache_dir(), role + ".json"))
    bpy.ops.wm.read_homefile(use_empty=True)
    queue = []
    ok, msg, objs = gc.import_cached(
        role, path, lambda: app.import_appearance_mesh(path, defer_materials=queue),
    )
    check("deferred miss not written", gc.LAST_EVENT.get("result") == "miss"
          and "written" not in gc.LAST_EVENT and queue)
    for obj, qpath, kwargs, _post in queue:
        app.finish_deferred_materials(obj, qpath, **kwargs)
        obj["arc_materials_pending"] = 0
    check("capture_pending wrote", gc.capture_pending(objs) == 1)
    deferred_built = summary(objs, bi)
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, objs, dt, ev = run(role, path)
    check("deferred library hit", ev.get("result") == "hit-library")
    check("deferred cached same", summary(objs, bi) == deferred_built,
          diff(deferred_built, summary(objs, bi)))


try:
    main()
except Exception:
    traceback.print_exc()
    FAILS.append("exception")
print("RESULT", "FAIL " + ",".join(FAILS) if FAILS else "PASS")
sys.stdout.flush()
os._exit(1 if FAILS else 0)
