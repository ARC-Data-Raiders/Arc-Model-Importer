import hashlib
import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from .test_native_gzip_catalog import catalog, fixture


class CatalogHashRevisionTests(unittest.TestCase):
    def test_repeated_hashes_reuse_the_encoder(self):
        value = fixture()["records"]["/Game/M"]["facts"]
        expected = catalog._json_hash(value)
        with patch.object(json, "JSONEncoder", wraps=json.JSONEncoder) as encoder:
            for repeat in range(5):
                self.assertEqual(catalog._json_hash(value), expected)
            self.assertEqual(encoder.call_count, 0)

    def test_hash_matches_the_existing_canonical_json_contract(self):
        values = [
            fixture(),
            {"unicode": "Raider \u00e9 \u96ea", "controls": "\n\t\u0000", "values": [0, -0.0, True, None]},
            {"nested": [{"z": 2, "a": 1}], "float": 1.25e-12},
        ]
        for value in values:
            expected = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            self.assertEqual(catalog._json_hash(value), expected)
            self.assertEqual(catalog._json_hash(value, decoded_tree=True), expected)

    def test_nonfinite_values_are_still_refused(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                catalog._json_hash({"value": value})
            with self.assertRaises(ValueError):
                catalog._json_hash({"value": value}, decoded_tree=True)

    def test_reused_encoder_is_safe_across_threads(self):
        values = [{"package": "/Game/M", "value": index} for index in range(32)]
        expected = [catalog._json_hash(value) for value in values]
        with ThreadPoolExecutor(max_workers=4) as workers:
            self.assertEqual(list(workers.map(catalog._json_hash, values)), expected)

    def test_cyclic_caller_input_is_still_refused(self):
        value = {}
        value["cycle"] = value
        with self.assertRaises(ValueError):
            catalog._json_hash(value)

    def test_every_owned_record_is_still_hashed(self):
        payload = fixture()
        unused = copy.deepcopy(payload["records"]["/Game/M"])
        unused["facts"]["package"] = unused["facts"]["parent"] = "/Game/Unused"
        unused["facts_sha256"] = catalog._json_hash(unused["facts"])
        payload["records"]["/Game/Unused"] = unused
        with patch.object(catalog, "_json_hash", wraps=catalog._json_hash) as hashing:
            catalog.NativeCatalog(payload, _owned=True)
            self.assertEqual(hashing.call_count, 2)
            self.assertTrue(all(call.kwargs["decoded_tree"] for call in hashing.call_args_list))
        unused["facts"]["scalars"]["Zero"] = 10
        with self.assertRaisesRegex(ValueError, "digest mismatch: /Game/Unused"):
            catalog.NativeCatalog(payload, _owned=True)

    def test_owned_record_identity_and_path_checks_remain_active(self):
        payload = fixture()
        payload["records"]["/Game/M"]["facts"]["package"] = "/Game/Other"
        payload["records"]["/Game/M"]["facts_sha256"] = catalog._json_hash(payload["records"]["/Game/M"]["facts"])
        with self.assertRaisesRegex(ValueError, "package mismatch"):
            catalog.NativeCatalog(payload, _owned=True)
        payload = fixture()
        payload["records"]["/Game/M"]["source"]["relative_path"] = "../outside.json"
        with self.assertRaisesRegex(ValueError, "Unsafe catalog source path"):
            catalog.NativeCatalog(payload, _owned=True)

    def test_caller_owned_payload_keeps_cycle_detection(self):
        payload = fixture()
        with patch.object(catalog._DECODED_JSON_ENCODER, "encode", wraps=catalog._DECODED_JSON_ENCODER.encode) as encoding:
            catalog.NativeCatalog(payload)
            self.assertEqual(encoding.call_count, 0)
            catalog.NativeCatalog(payload, _owned=True)
            self.assertEqual(encoding.call_count, 1)


if __name__ == "__main__":
    unittest.main()
