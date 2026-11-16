import copy
import json
import unittest
from unittest.mock import patch
from .test_native_gzip_catalog import catalog, fixture
from .native_test_support import temp_fixture
from pathlib import Path


class LazyCatalogTests(unittest.TestCase):
    def test_read_validates_raw_facts_before_freezing_only_requested_records(self):
        payload = fixture()
        unused = copy.deepcopy(payload["records"]["/Game/M"])
        unused["facts"]["package"] = unused["facts"]["parent"] = "/Game/Unused"
        unused["facts_sha256"] = catalog._json_hash(unused["facts"])
        payload["records"]["/Game/Unused"] = unused
        with temp_fixture("lazy-catalog-") as directory:
            target = Path(directory) / "bank.json"
            target.write_text(json.dumps(payload))
            with patch.object(catalog, "freeze", wraps=catalog.freeze) as freezing:
                bank = catalog.NativeCatalog.read(target)
                self.assertFalse(any(call.args[0] is unused or call.args[0] == payload for call in freezing.call_args_list))
                self.assertEqual(bank.payload["records"]["/Game/M"]["facts"]["scalars"]["Zero"], 0)
                self.assertFalse(any(isinstance(call.args[0], dict) and "records" in call.args[0] for call in freezing.call_args_list))
                with self.assertRaises(TypeError):
                    bank.payload["records"]["/Game/M"]["facts"]["scalars"]["Zero"] = 1
                self.assertEqual(bank._records["/game/m"], bank.payload["records"]["/Game/M"])

    def test_constructor_snapshots_mutable_input(self):
        payload = fixture()
        bank = catalog.NativeCatalog(payload)
        payload["records"]["/Game/M"]["facts"]["scalars"]["Zero"] = 9
        self.assertEqual(bank.payload["records"]["/Game/M"]["facts"]["scalars"]["Zero"], 0)
