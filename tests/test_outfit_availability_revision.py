import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("outfit_catalog_probe", ROOT / "Outfit_Accessories/outfit_catalog.py")
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class OutfitAvailabilityRevisionTests(unittest.TestCase):
    def test_live_index_keeps_explicit_meshes_outside_legacy_assets_folder(self):
        mesh = "PioneerGame/Content/Pioneer/Characters/Heroes/Lance/Resources/Boxer/Meshes/SK_Lance_Boxer_FullBody"
        unrelated = "PioneerGame/Content/Pioneer/Characters/Assets/Lancer/SK_Lancer"
        row = {"ST": "ID_LANCE_NAME", "Model Folder Name": "Lance", "Pak Mesh Paths": mesh}
        self.assertEqual(catalog.row_mesh_paths(row, [mesh, unrelated]), [mesh])
        self.assertEqual(catalog.row_mesh_paths(row, [unrelated]), [])

    def test_localization_only_rows_are_not_importable_outfits(self):
        is_option = getattr(catalog, "is_outfit_option", None)
        self.assertIsNotNone(is_option)
        self.assertFalse(is_option({"ST": "ID_PLAYERSKIN_SANDVEIL", "Flavour": "Sandveil", "Pak Status": "no cooked mesh"}))
        self.assertTrue(is_option({"ST": "ID_PLAYERSKIN_DASHWOOD", "Model Folder Name": "Dashwood"}))
        self.assertTrue(is_option({"ST": "ID_TIANWEN_NAME", "Pak Status": "no cooked mesh"}))

    def test_npc_catalog_uses_authored_meshes(self):
        import csv
        with (ROOT / "Data/outfit_reference.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = {row["ST"]: row for row in csv.DictReader(stream)}
        for ident in ("ID_APOLLO_NAME", "ID_CELESTE_NAME", "ID_JUANITO_NAME", "ID_LANCE_NAME", "ID_SHANI_NAME"):
            self.assertEqual(rows[ident]["Pak Status"], "available", ident)
            self.assertTrue(rows[ident]["Pak Mesh Paths"], ident)
        self.assertEqual(rows["ID_TIANWEN_NAME"]["Pak Status"], "no cooked mesh")
        self.assertIn("Characters/Heroes/Lance/", rows["ID_LANCE_NAME"]["Pak Mesh Paths"])
        self.assertIn("Characters/Assets/Pilot/", rows["ID_APOLLO_NAME"]["Pak Mesh Paths"])

    def test_all_npcs_have_packaged_trader_portraits(self):
        for stem in catalog.NPC_PREVIEW_STEMS.values():
            self.assertTrue((ROOT / "assets/ui_icons/appearance" / (stem + ".avif")).is_file(), stem)

    def test_live_visual_metadata_does_not_restore_stale_recorded_parts(self):
        own = "PioneerGame/Content/Pioneer/Characters/Assets/Shani/Jacket/SK_Shani_Jacket"
        old = "PioneerGame/Content/Pioneer/Characters/Assets/Old/Cap/SK_Old_Cap"
        current = "PioneerGame/Content/Pioneer/Characters/Assets/New/Cap/SK_New_Cap"
        row = {"Model Folder Name": "Shani", "Pak Mesh Paths": own + ";" + old}
        parts = ["PioneerGame/Content/Pioneer/Characters/Assets/New/Cap/DA_VP_New_Cap"]
        self.assertEqual(set(catalog.row_mesh_paths(row, [own, old, current], parts)), {own, current})


if __name__ == "__main__":
    unittest.main()
