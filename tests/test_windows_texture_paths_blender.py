"""Actual long native Treasure texture/decal proof; isolated Blender process only.

--python this.py -- --blend source.blend --assets current-native-root
--proof treasure-longpath-diagnostic.json --output scratch/result.json
"""
from pathlib import Path

def main():
    import argparse, importlib, json, os, sys, traceback
    import bpy
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", required=True)
    parser.add_argument("--assets", required=True)
    parser.add_argument("--proof", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    sys.pycache_prefix = str(Path(args.output).resolve().parent / "pycache")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parent))
    textures = importlib.import_module(root.name + ".Shared.textures")
    common = importlib.import_module(root.name + ".Shared.materials.common")
    utils = importlib.import_module(root.name + ".Shared.utils")
    pak = importlib.import_module(root.name + ".Shared.pak_extract")
    operators = importlib.import_module(root.name + ".Shared.operators")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "textures": [], "errors": []}
    try:
        proof = json.loads(Path(args.proof).read_text(encoding="utf-8"))
        utils._pak_cache_roots.clear()
        utils.set_pak_cache_root(args.assets)
        textures.clear_clothing_path_caches()
        if hasattr(bpy.context.scene, "arc_pak_dir"):
            bpy.context.scene.arc_pak_dir = ""
        bpy.context.scene["arc_pak_dir_user_cleared"] = True
        for row in proof["texture_proof"]:
            resolved = textures.find_asset_from_object_path(row["reference"], ".png")
            assert resolved, ("Unresolved exact native texture", row["parameter"])
            if os.name == "nt" and len(row["native_path"]) >= 248:
                assert resolved.startswith("\\\\?\\"), resolved
            image = common._load_image_cached(resolved, non_color=row["parameter"] != "BaseColor")
            common._force_image_ready(image)
            assert image and image.has_data and min(image.size) > 0, row["parameter"]
            report["textures"].append({"parameter": row["parameter"], "resolved": resolved,
                                       "loaded": True, "size": list(image.size)})
        with bpy.data.libraries.load(args.blend, link=False) as (available, selected):
            names = [name for name in available.objects if name == proof["object"]]
            assert len(names) == 1, ("Treasure object identity not found", names)
            selected.objects = names
        obj = selected.objects[0]
        bpy.context.scene.collection.objects.link(obj)
        before = (len(obj.data.vertices), len(obj.data.polygons), tuple(v for row in obj.matrix_world for v in row))
        materials = [slot.material for slot in obj.material_slots if slot.material and slot.material.use_nodes]
        original_mi = next(str(mat.get("arc_skin_json") or mat.get("arc_mi_path")) for mat in materials
                           if mat.get("arc_skin_json") or mat.get("arc_mi_path"))
        original_mesh = str(obj.get("arc_source_mesh") or obj.get("arc_psk_path") or "")
        mesh = pak.recover_saved_material_source(original_mesh, [args.assets], native_pak=True)
        mi = pak.recover_saved_material_source(original_mi, [args.assets], native_pak=True)
        assert mesh and mi, "Missing exact current source fixture"
        native_properties = textures.load_mi_properties(mi["path"])
        native_scalars = {item.get("ParameterInfo", {}).get("Name"): item.get("ParameterValue")
                          for item in native_properties.get("ScalarParameterValues", [])}
        assert native_scalars.get("2_LayerMask") == 8, native_scalars.get("2_LayerMask")
        status = operators.apply_materials_to_object(obj, mesh["path"], skin_json=mi["path"],
            manual_skins_folder=os.path.dirname(mi["path"]), skin_choice=mi["path"])
        assert status in ("clothing", "visor"), status
        after = (len(obj.data.vertices), len(obj.data.polygons), tuple(v for row in obj.matrix_world for v in row))
        assert before == after, "Geometry or transform changed during material-only update"
        gates = [slot.material.node_tree.nodes.get("GT_decal2_layer") for slot in obj.material_slots
                 if slot.material and slot.material.use_nodes]
        gates = [node for node in gates if node]
        assert gates, "GT_decal2_layer disappeared"
        assert any(abs(node.inputs["Allow4"].default_value - 1) < 1e-8 for node in gates), "Native LayerMask8 Allow4 lost"
        inventory = [node.image.filepath for slot in obj.material_slots if slot.material and slot.material.use_nodes
                     for node in slot.material.node_tree.nodes if node.type == "TEX_IMAGE" and node.image]
        for token in ("_BaseColor", "_ColorMask", "_OcclusionCurvatureMaterialID"):
            assert any(token.lower() in path.lower() for path in inventory), ("Missing native shader binding", token)
        report.update(passed=True, status=status, decal2_allow4=1, geometry_unchanged=True, texture_inventory=inventory)
    except Exception:
        report["errors"].append(traceback.format_exc())
        raise
    finally:
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
