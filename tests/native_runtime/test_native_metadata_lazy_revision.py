import importlib
import unittest
import sys
import types
from unittest.mock import patch
from .native_test_support import PACKAGE, namespace
from .test_native_gzip_catalog import fixture

namespace()
rebuild_name = PACKAGE + ".Shared.materials.rebuild"
if rebuild_name not in sys.modules:
    rebuild = types.ModuleType(rebuild_name)
    from .native_test_support import ROOT
    rebuild.__path__ = [str(ROOT / "Shared/materials/rebuild")]
    sys.modules[rebuild_name] = rebuild
native_data = importlib.import_module(PACKAGE + ".Shared.materials.native.native_data")


class TrackingRecords(dict):
    def __getitem__(self, key):
        self.reads.append(key)
        return super().__getitem__(key)

    def items(self):
        return ((key, self[key]) for key in self)


class LazyMetadataTests(unittest.TestCase):
    def source(self):
        records = TrackingRecords(fixture()["records"])
        records.reads = []
        catalog = type("Catalog", (), {"payload": {"records": records, "textures": {}}})()
        return {"catalog": catalog, "limits": {}, "provenance": {}}, records

    def test_metadata_projects_only_requested_record(self):
        source, records = self.source()
        metadata = native_data.current_metadata(source)
        self.assertEqual(records.reads, [])
        self.assertEqual(metadata["materials"]["pioneergame/content/m"]["scalars"], {"Zero": 0})
        self.assertEqual(records.reads, ["/Game/M"])
        self.assertIs(metadata, native_data.current_metadata(source))
        self.assertIs(metadata["materials"]["pioneergame/content/m"], metadata["materials"].get("pioneergame/content/m"))

    def test_presets_skip_unrelated_records(self):
        source, records = self.source()
        with patch.object(native_data, "reviewed_source", return_value=source):
            self.assertEqual(native_data.current_presets(), {})
        self.assertEqual(records.reads, [])
