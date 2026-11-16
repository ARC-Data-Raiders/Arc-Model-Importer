import importlib
import unittest

from .fake_graph import FakeGraph, rebuild
from .native_test_support import PACKAGE, namespace

namespace()
repository = importlib.import_module(PACKAGE + ".Shared.materials.native.repository")
records = rebuild("records")


class NullTextureInheritanceTests(unittest.TestCase):
    def document(self, name, texture, association="GlobalParameter", index=-1):
        return {"Type": "MaterialInstanceConstant", "Name": name, "Properties": {
            "TextureParameterValues": [{"ParameterInfo": {"Name": "PaintBreakup",
                "Association": association, "Index": index}, "ParameterValue": texture}]}}

    def test_native_null_override_keeps_parent_texture(self):
        parent = repository.parse_material(self.document("Parent", "/Game/T_Paint"), "/Game/Parent")
        child = repository.parse_material(self.document("Child", None), "/Game/Child")
        merged = repository.merge_records(parent, child)
        self.assertEqual(merged.textures["PaintBreakup"], "/Game/T_Paint")

    def test_native_scoped_null_override_keeps_same_scope(self):
        parent = repository.parse_material(self.document("Parent", "/Game/T_Paint", "LayerParameter", 2), "/Game/Parent")
        child = repository.parse_material(self.document("Child", None, "LayerParameter", 2), "/Game/Child")
        merged = repository.merge_records(parent, child)
        self.assertEqual(merged.scopes[("LayerParameter", 2)]["textures"]["PaintBreakup"], "/Game/T_Paint")
        self.assertNotIn("PaintBreakup", merged.textures)

    def test_compatibility_null_override_keeps_parent_texture(self):
        parent = records.raw_record(self.document("Parent", "/Game/T_Paint"), "/Game/Parent")
        child = records.raw_record(self.document("Child", None), "/Game/Child")
        merged = records.merge(parent, child)
        self.assertEqual(merged["textures"]["PaintBreakup"], "PioneerGame/Content/T_Paint")

    def test_valid_child_texture_still_overrides(self):
        parent = repository.parse_material(self.document("Parent", "/Game/T_Paint"), "/Game/Parent")
        child = repository.parse_material(self.document("Child", "/Game/T_Own"), "/Game/Child")
        self.assertEqual(repository.merge_records(parent, child).textures["PaintBreakup"], "/Game/T_Own")

    def test_no_valid_parent_texture_stays_missing(self):
        parent = repository.MaterialRecord("/Game/Parent", "/Game/Parent")
        child = repository.parse_material(self.document("Child", None), "/Game/Child")
        self.assertNotIn("PaintBreakup", repository.merge_records(parent, child).textures)

    def test_scoped_null_does_not_borrow_global_or_another_layer(self):
        parent = repository.MaterialRecord("/Game/Parent", "/Game/Parent", textures={"PaintBreakup": "/Game/T_Global"},
            scopes={("LayerParameter", 1): {"textures": {"PaintBreakup": "/Game/T_Layer"}}})
        child = repository.parse_material(self.document("Child", None, "LayerParameter", 2), "/Game/Child")
        merged = repository.merge_records(parent, child)
        self.assertNotIn("PaintBreakup", merged.scopes[("LayerParameter", 2)]["textures"])
        self.assertEqual(merged.scopes[("LayerParameter", 1)]["textures"]["PaintBreakup"], "/Game/T_Layer")
        self.assertEqual(merged.textures["PaintBreakup"], "/Game/T_Global")

    def test_null_texture_does_not_erase_scalar_zero_or_switch_false(self):
        parent = repository.MaterialRecord("/Game/Parent", "/Game/Parent", textures={"PaintBreakup": "/Game/T_Paint"},
            scalars={"Amount": 1}, switches={"Enabled": True})
        child = repository.MaterialRecord("/Game/Child", "/Game/Parent", textures={"PaintBreakup": None},
            scalars={"Amount": 0}, switches={"Enabled": False})
        merged = repository.merge_records(parent, child)
        self.assertEqual(merged.textures["PaintBreakup"], "/Game/T_Paint")
        self.assertEqual(merged.scalars["Amount"], 0)
        self.assertIs(merged.switches["Enabled"], False)
        self.assertIsNone(child.textures["PaintBreakup"])

    def test_concrete_paint_builder_receives_inherited_texture(self):
        parent = records.raw_record(self.document("Parent", "/Game/T_Paint"), "/Game/Parent")
        child = records.raw_record(self.document("Child", None), "/Game/Child")
        graph = FakeGraph()
        record = records.merge(parent, child)
        rebuild("surfaces")._concrete_paint(graph, record, (.5, .5, .5), .5, (0, 0, 1), "UV0", None, 0, 100)
        self.assertIn("Native paint normal / edge B / breakup A", graph.images)
        self.assertFalse(any("requires its authored PaintBreakup" in warning for warning in graph.report["warnings"]))


if __name__ == "__main__":
    unittest.main()
