"""Independent appearance palettes and exact packaged outfit identities."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import patch

from test_appearance_packaged_browse import load_modules, ROOT


class AppearanceFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.addon, self.modules = load_modules()
        self.appearance = self.addon.appearance

    def test_independent_palette_selection(self):
        scene = SimpleNamespace(arc_appearance_hair_color="normal", arc_appearance_facial_hair_color="beard")
        self.assertEqual(self.appearance.hair_color_choice(scene, "Hair"), "normal")
        self.assertEqual(self.appearance.hair_color_choice(scene, "FacialHair"), "beard")
        self.assertEqual(self.appearance.hair_color_choice(SimpleNamespace(arc_appearance_hair_color="normal"), "FacialHair"), "NONE")

    def test_role_identity_precedes_names(self):
        class Mesh(dict):
            type = "MESH"
            name = "Facial Hair misleading name"
        normal = Mesh(arc_assembly_role="Hair")
        beard = Mesh(arc_model_type="hair", arc_psk_path="X:/Characters/FacialHairs/Beard/SK_Beard.psk")
        self.assertEqual(self.appearance.hair_object_role(normal), "Hair")
        self.assertEqual(self.appearance.hair_object_role(beard), "FacialHair")
        self.assertEqual(self.appearance.hair_object_role(Mesh(arc_assembly_role="Head")), "")

    def test_color_update_copies_shared_mesh_and_materials(self):
        class Material:
            users = 2
            def copy(self):
                return Material()
        shared = Material()
        class MeshData:
            users = 2
            def __init__(self):
                self.materials = [shared]
            def copy(self):
                copied = MeshData()
                copied.users = 1
                copied.materials = list(self.materials)
                return copied
        original = MeshData()
        obj = SimpleNamespace(data=original)
        self.appearance.isolate_appearance_materials(obj)
        self.assertIsNot(obj.data, original)
        self.assertIs(original.materials[0], shared)
        self.assertIsNot(obj.data.materials[0], shared)

    def test_banshee_exact_packaged_preview(self):
        with (ROOT / "Data/outfit_reference.csv").open(encoding="utf-8-sig") as stream:
            row = next(row for row in csv.DictReader(stream) if row["ST"] == "ID_PLAYERSKIN_BANSHEE")
        self.assertEqual(self.appearance._outfit_preview_stems(row), ["Banshee"])
        self.assertTrue(self.appearance._shipped_outfit_preview_path("Banshee"))

    def test_facial_color_registered_and_cleaned_up(self):
        source = (ROOT / "Outfit_Accessories/properties.py").read_text(encoding="utf-8-sig")
        self.assertEqual(source.count('"arc_appearance_facial_hair_color"'), 4)  # register, cleanup, sync, tooltip

    def test_missing_selected_preview_queues_without_discovery(self):
        bpy = self.modules["bpy"]
        timers = SimpleNamespace(register=lambda callback, **kwargs: callbacks.append(callback))
        bpy.app = SimpleNamespace(timers=timers)
        callbacks = []
        context = SimpleNamespace(scene=SimpleNamespace(arc_pioneer_root="forbidden-root"))
        row = {"ST": "ID_PLAYERSKIN_BANSHEE", "Model Folder Name": "Banshee"}
        with patch.object(Path, "is_file", side_effect=AssertionError("discovery during draw")), patch.object(self.appearance.os, "walk", side_effect=AssertionError("scan")):
            self.assertEqual(self.appearance._queue_missing_outfit_preview(context, row), "")
            self.assertEqual(self.appearance._queue_missing_outfit_preview(context, row), "")
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(self.appearance._OUTFIT_PREVIEW_JOBS), 1)
        self.assertIsNone(next(iter(self.appearance._OUTFIT_PREVIEW_JOBS.values()))[1])

    def test_deferred_preview_extraction_requires_an_explicit_action(self):
        callbacks = []
        self.modules["bpy"].app = SimpleNamespace(timers=SimpleNamespace(register=lambda callback, **kwargs: callbacks.append(callback)))
        package_name = self.addon.__name__
        pak = self.modules[package_name + ".Shared.pak_extract"]
        pak.extraction_command = unittest.mock.Mock(return_value=["exporter", "extract"])
        submitted = unittest.mock.Mock(return_value=SimpleNamespace())
        context = SimpleNamespace(scene=SimpleNamespace(arc_pak_dir="configured-game", arc_usmap_path="mapping.usmap"))
        with patch.dict(__import__("sys").modules, self.modules), patch("concurrent.futures.ThreadPoolExecutor", return_value=SimpleNamespace(submit=submitted)):
            self.appearance._queue_missing_outfit_preview(context, {"ST": "ID_PLAYERSKIN_BANSHEE", "Model Folder Name": "Banshee"})
            callbacks[0]()
        pak.extraction_command.assert_not_called()
        self.assertEqual(submitted.call_args.args[-1], ())
        with patch.dict(__import__("sys").modules, self.modules):
            self.appearance._queue_missing_outfit_preview(context, {"ST": "ID_PLAYERSKIN_BANSHEE", "Model Folder Name": "Banshee"}, allow_extract=True)
            self.assertEqual(len(callbacks), 2)
            callbacks[1]()
        pak.extraction_command.assert_called_once()
        self.assertEqual(submitted.call_args.args[-1], ("exporter", "extract"))

    def test_missing_icon_worker_extracts_only_authored_selected_paths(self):
        spec = importlib.util.spec_from_file_location("selected_preview_worker", ROOT / "Outfit_Accessories/outfit_preview.py")
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        scratch = Path("Z:/Datamining/Arc Raiders/CodexScratch/DataRaiders-BlenderImporter/.local/ui-feedback-20261101/appearance") if self.appearance.os.name == "nt" else Path(tempfile.gettempdir())
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            cache = Path(directory)
            base = "Items/Characters/Skins/Outfit/Banshee/"
            metadata = base + "DA_UIMetaData_Outfit_Banshee"
            icon = base + "T_UI_Icon_Outfit_Banshee"
            commands = []
            def run(command, **kwargs):
                commands.append(command)
                relative = command[-1].removeprefix("PioneerGame/Content/Pioneer/")
                destination = cache / (relative + (".json" if relative == metadata else ".png"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                if relative == metadata:
                    destination.write_text(json.dumps([{"Type": "UICharacterVisualSkinMetaDataItem", "Properties": {
                        "DisplayName": {"Key": "ID_PLAYERSKIN_BANSHEE"},
                        "Image": {"AssetPathName": "/Game/Pioneer/" + icon + ".T_UI_Icon_Outfit_Banshee"}}}]))
                else:
                    self.assertEqual(relative, icon)
                    destination.write_bytes(b"fixture icon")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch("subprocess.run", side_effect=run), patch.object(self.appearance.os, "walk", side_effect=AssertionError("scan")), patch.object(self.appearance.os, "listdir", side_effect=AssertionError("discovery")):
                result = worker.selected_game_icon(("Banshee",), "ID_PLAYERSKIN_BANSHEE", (str(cache),), str(cache), ("exporter", "extract", "--game-dir", str(cache)))
            self.assertEqual(result, str(cache / (icon + ".png")))
            self.assertEqual([command[-1] for command in commands], ["PioneerGame/Content/Pioneer/" + metadata, "PioneerGame/Content/Pioneer/" + icon])


if __name__ == "__main__":
    unittest.main()
