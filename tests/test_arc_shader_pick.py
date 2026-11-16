# Plain Python: python -m unittest tests.test_arc_shader_pick
import importlib.util
import json
import os
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("arc_shader_pick", os.path.join(_ROOT, "shader_tools", "arc_shader_pick.py"))
pick = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pick)

LOCAL = 11475683181038621400
SKIN = 4115211957270979882
MAP = "AA" * 20


def _doc(types, indices, vf=LOCAL, quality="Num"):
    mesh_map = {
        "VertexFactoryTypeName": vf,
        "ShaderTypes": types,
        "ShaderPermutations": [0] * len(types),
        "Shaders": [{"Target": {"Frequency": "SF_Pixel"}, "ResourceIndex": i} for i in indices],
    }
    shader_map = {"ShaderPlatform": "SP_PCD3D_SM5", "ResourceHash": MAP,
                  "ShaderMapId": {"QualityLevel": quality, "FeatureLevel": "SM5"},
                  "Content": {"OrderedMeshShaderMaps": [mesh_map]}}
    parent = {"object_path": "/Game/M_Parent.M_Parent", "json": {"LoadedMaterialResources": [{"LoadedShaderMap": shader_map}]}}
    child = {"object_path": "/Game/MI_Child.MI_Child", "json": {"LoadedMaterialResources": []}}
    return {"requested": "Game/MI_Child", "build_id": "1", "chain": [child, parent]}


def _manifest(folder, archive, rows):
    """rows: (shader_index, shader_hash, frequency). Writes container files too."""
    records = []
    for index, shader_hash, frequency in rows:
        rel = f"{archive}/{MAP}/{index:04d}_{frequency}_{shader_hash}.dxbc"
        full = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "wb").write(b"DXBC")
        records.append({"archive_name": archive, "map_hash": MAP, "shader_index": index, "shader_hash": shader_hash,
                        "frequency": frequency, "container_magic": "DXBC", "container_file": rel})
    return records


class ArcShaderPickTest(unittest.TestCase):
    def test_vertex_factory_name_hashes(self):
        self.assertEqual(pick.hashed_name("FLocalVertexFactory"), LOCAL)
        self.assertEqual(pick.hashed_name("TGPUSkinVertexFactoryDefault"), SKIN)
        self.assertEqual(pick.hashed_name("FInstancedStaticMeshVertexFactory"), 9106933604177243835)
        self.assertEqual(pick.hashed_name("FSplineMeshVertexFactory"), 9739903087048706566)
        self.assertEqual(pick.vf_alias(1036904721468883472), "GPUSkinClothDefault")
        self.assertEqual(pick.hashed_name("TEmbarkClothVertexFactoryDefault"), 4358102381999477390)
        self.assertEqual(pick.vf_alias(4358102381999477390), "EmbarkClothDefault")
        self.assertEqual(pick.vf_alias(13168243933419104092), "NiagaraSprite")
        self.assertIsNone(pick.vf_alias(7880918185444700897))

    def test_resource_index_is_hash_order_within_one_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            # shader_index order differs from hash order, and the map sits in two archives.
            rows = [(7, "CC", "SF_Pixel"), (8, "AA", "SF_Vertex"), (9, "BB", "SF_Pixel")]
            records = _manifest(folder, "ArchiveA", rows) + _manifest(folder, "ArchiveB", [(i + 100, h, f) for i, h, f in rows])
            path = os.path.join(folder, "shader-records.manifest.json")
            json.dump({"records": records}, open(path, "w"))
            loaded = pick.load_records([path])
            self.assertEqual([r["shader_hash"] for r in loaded[MAP]], ["AA", "BB", "CC"])

            doc = _doc(["TBasePassVSFNoLightMapPolicy", "TBasePassPSFNoLightMapPolicySkylight", "TBasePassPSFNoLightMapPolicy"], [0, 2, 1])
            res = pick.pick_material(doc, loaded, "SP_PCD3D_SM5", ("Local", "GPUSkinDefault"))
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["resource_owner"], "/Game/M_Parent.M_Parent")
            self.assertEqual(len(res["picks"]), 2)
            self.assertEqual(res["best"]["shader_type"], "TBasePassPSFNoLightMapPolicy")
            self.assertEqual(res["best"]["shader_hash"], "BB")
            self.assertTrue(res["best"]["verified"])

    def test_wrong_frequency_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "shader-records.manifest.json")
            json.dump({"records": _manifest(folder, "A", [(0, "AA", "SF_Vertex"), (1, "BB", "SF_Pixel")])}, open(path, "w"))
            res = pick.pick_material(_doc(["TBasePassPSFNoLightMapPolicy"], [0]), pick.load_records([path]), "SP_PCD3D_SM5", None)
            self.assertEqual(res["status"], "unverified")
            self.assertEqual(res["best"]["problem"], "frequency_SF_Vertex")

    def test_vertex_factory_filter_and_unresolved(self):
        doc = _doc(["TBasePassPSFNoLightMapPolicy"], [0], vf=7880918185444700897)
        res = pick.pick_material(doc, None, "SP_PCD3D_SM5", ("Local",))
        self.assertEqual(res["status"], "no_base_pass_ps")
        self.assertEqual(res["unresolved_vertex_factories"], ["7880918185444700897"])
        res = pick.pick_material(doc, None, "SP_PCD3D_SM5", None)
        self.assertEqual(res["status"], "index_only")
        self.assertEqual(res["best"]["vertex_factory"], "hash:7880918185444700897")

    def test_skinned_preference(self):
        doc = _doc(["TBasePassPSFNoLightMapPolicy"], [0], vf=SKIN)
        doc["chain"][1]["json"]["LoadedMaterialResources"][0]["LoadedShaderMap"]["Content"]["OrderedMeshShaderMaps"].append(
            {"VertexFactoryTypeName": LOCAL, "ShaderTypes": ["TBasePassPSFNoLightMapPolicy"], "ShaderPermutations": [0],
             "Shaders": [{"Target": {"Frequency": "SF_Pixel"}, "ResourceIndex": 1}]})
        res = pick.pick_material(doc, None, "SP_PCD3D_SM5", ("GPUSkinDefault", "Local"))
        self.assertEqual(res["best"]["vertex_factory"], "GPUSkinDefault")
        self.assertEqual(set(res["best_by_vf"]), {"GPUSkinDefault", "Local"})


if __name__ == "__main__":
    unittest.main()
