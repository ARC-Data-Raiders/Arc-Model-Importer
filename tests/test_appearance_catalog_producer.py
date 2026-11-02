"""Appearance producer uses typed exports and records source/build provenance."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("appearance_producer", ROOT / "scripts/build_appearance_catalog.py")
PRODUCER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PRODUCER)
SCRATCH = Path("Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/ui-revision-implementation/appearance") if os.name == "nt" else Path(tempfile.gettempdir())


class AppearanceProducerTests(unittest.TestCase):
    def test_typed_colorways_localization_and_provenance(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as temporary:
            root = Path(temporary)
            source_root = root / "source"
            source_root.mkdir()
            source = {"build": "fixture-build", "root": str(source_root), "records": {}}
            folder = source_root / "Items/Characters/Skins/BackpackContainer/Fixture"
            folder.mkdir(parents=True)
            stem = "DA_OI_BackpackContainer_Fixture_Color_Red"
            package = "/Game/Pioneer/Items/Characters/Skins/BackpackContainer/Fixture/" + stem
            record = {"Type": "CharacterVisualPartOnlineItemDataAsset", "Name": stem, "Package": package}
            (folder / (stem + ".json")).write_text(json.dumps([record]), encoding="utf-8")
            (folder / (stem + "_Persistence.json")).write_text(json.dumps([record]), encoding="utf-8")
            (folder / "DA_OI_BackpackContainer_Fixture_Color_Invented.json").write_text(json.dumps([{"Type": "UnrelatedAsset"}]), encoding="utf-8")
            (folder / "DA_UIMetaData_BackpackContainer_Fixture_Color_Red.json").write_text(json.dumps([{"Properties": {"DisplayName": {"LocalizedString": "Authored red"}}}]), encoding="utf-8")
            table = source_root / "UI/Localization/ST_Customization_Backpacks.json"
            table.parent.mkdir(parents=True)
            table.write_text(json.dumps([{"StringTable": {"KeysToEntries": {"ID_CUSTOMIZATION_BACKPACK_FIXTURE": "Authored pack"}}}]), encoding="utf-8")
            csv_path = root / "appearance.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=["id", "kind", "folder", "icon_stem", "segment"])
                writer.writeheader()
                writer.writerow({"id": "fixture-id", "kind": "backpack", "folder": "Fixture", "icon_stem": "Fixture", "segment": "BackpackContainer"})
            output = root / "output"
            coverage = PRODUCER.build_catalog(csv_path, [source], [], output)
            manifest = json.loads((output / "appearance_browse.json").read_text(encoding="utf-8"))
            self.assertEqual(coverage["backpack_colorways"], 1)
            self.assertEqual(manifest["backpack_colorways"]["Fixture"], [{"id": "Red", "label": "Authored red", "package": "PioneerGame/Content/" + package[len("/Game/"):]}])
            self.assertEqual(manifest["flavour_tables"]["backpack"]["FIXTURE"], "Authored pack")
            self.assertEqual(manifest["sources"][0]["build"], "fixture-build")
            self.assertTrue(manifest["sources"][0]["files"])
            self.assertEqual(coverage["missing_icon_ids"], ["fixture-id"])
            first = (output / "appearance_browse.json").read_bytes()
            PRODUCER.build_catalog(csv_path, [source], [], output)
            self.assertEqual(first, (output / "appearance_browse.json").read_bytes())

    def test_icon_matching_preserves_variant_suffix_and_word_ending(self):
        self.assertEqual(PRODUCER.token("Bandana"), "BANDANA")
        self.assertEqual(PRODUCER.token("Bucket_01_A"), "BUCKET01")
        row = {"icon_stem": "Fixture_01", "folder": "Fixture_01"}
        self.assertEqual(PRODUCER.choose_icon(row, [("FIXTURE01", "authored.png")]), "authored.png")
        self.assertIsNone(PRODUCER.choose_icon(row, [("UNRELATED", "wrong.png")]))
        self.assertEqual(PRODUCER.choose_icon({"folder": "HoseBunde_01", "icon_stem": "HoseBunde_01"}, [("HOSEBUNDLE", "authored.png")]), "authored.png")


if __name__ == "__main__":
    unittest.main()
