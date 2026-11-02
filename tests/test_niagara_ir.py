"""Regression tests for cooked evidence identity, bytes and version semantics."""
import base64
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("niagara_ir", Path(__file__).resolve().parents[1] / "niagara_ir.py")
ir = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ir)


def outer(name):
    return {"ObjectName": f"Object'{name}'", "ObjectPath": "/Game/Test/NS_Test.0"}


class EvidenceTests(unittest.TestCase):
    def test_repeated_script_and_interface_names_do_not_collide(self):
        exports = [{"Type": "NiagaraSystem", "Name": "NS_Test", "Properties": {}}]
        for emitter in ("A", "B"):
            exports.extend([
                {"Type": "NiagaraEmitter", "Name": emitter, "Outer": outer("NS_Test"), "Properties": {}},
                {"Type": "NiagaraScript", "Name": "UpdateScript", "Outer": outer(f"NS_Test:{emitter}"),
                 "Properties": {"Usage": 5, "CachedScriptVM": {"ByteCode": {"Data": [0]}, "Unrecognized": [9]}}},
                {"Type": "NiagaraDataInterfaceCurve", "Name": "Curve_0", "Outer": outer(f"NS_Test:{emitter}.UpdateScript"), "Properties": {"ShaderLUT": [0, 1]}},
            ])
        result = ir.extract_document(exports)
        self.assertEqual(len({s["id"] for s in result["scripts"]}), 2)
        self.assertEqual(len({s["emitterId"] for s in result["scripts"]}), 2)
        self.assertNotIn(None, {s["emitterId"] for s in result["dataInterfaces"]})
        self.assertEqual(result["scripts"][0]["rawExport"]["Properties"]["CachedScriptVM"]["Unrecognized"], [9])

    def test_missing_empty_invalid_and_present_are_distinct(self):
        for container, state in [({}, "missing"), ({"ByteCode": {"Data": []}}, "empty"),
                                 ({"ByteCode": {"Data": [256]}}, "invalid"),
                                 ({"ByteCode": {"Data": [0, 255]}}, "present")]:
            self.assertEqual(ir.payload_state(container, "ByteCode")["state"], state)
        self.assertEqual(ir.byte_payload(base64.b64encode(b"abc").decode()), b"abc")
        with self.assertRaises(ValueError):
            ir.byte_payload([-1])

    def test_version_guid_wins_over_array_order(self):
        props = {"ExposedVersion": "wanted", "VersionData": [
            {"Version": {"VersionGuid": "old"}, "SimTarget": 0},
            {"Version": {"VersionGuid": "wanted"}, "SimTarget": 1}]}
        self.assertEqual(ir.select_emitter_version(props), (1, "exposed-guid"))
        props["ExposedVersion"] = "missing"
        self.assertEqual(ir.select_emitter_version(props), (None, "ambiguous"))

    def test_numeric_and_named_enums_agree(self):
        self.assertEqual(ir.normalize_enum(4, ir.SCRIPT_USAGES), "ParticleSpawnScriptInterpolated")
        self.assertEqual(ir.normalize_enum("ENiagaraScriptUsage::ParticleSpawnScriptInterpolated", ir.SCRIPT_USAGES), "ParticleSpawnScriptInterpolated")
        self.assertEqual(ir.normalize_enum("ENiagaraSimTarget::CPUSim", ir.SIM_TARGETS), "CPUSim")
        self.assertEqual(ir.normalize_enum(777, ir.SCRIPT_USAGES), 777)

    def test_preserves_all_exports_in_both_root_shapes(self):
        exports = [{"Type": "UnknownFutureType", "Name": "Object", "NativeBytes": [3, 4]}]
        for document in (exports, {"Exports": exports, "Metadata": {"Engine": "test"}}):
            result = ir.extract_document(document)
            self.assertEqual(result["otherExports"][0]["rawExport"], exports[0])
        with self.assertRaises(ValueError):
            ir.extract_document({"Exports": [None]})


if __name__ == "__main__":
    unittest.main()
