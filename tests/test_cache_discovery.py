"""Run actual cache/parity source against a read-only in-memory filesystem."""

import ast
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "cache_contract"
PARITY = PACKAGE + ".Shared.materials.native.shader_parity"
CANONICAL = "PioneerGame/Content/Props/MI_Window"


def functions(relative, names, namespace):
    path = ROOT / relative
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)


@contextlib.contextmanager
def source_seam(selected="", build="2.2.0"):
    names = [PACKAGE, PACKAGE + ".Shared", PACKAGE + ".Shared.materials",
             PACKAGE + ".Shared.materials.native", PARITY, PACKAGE + ".Outfit_Accessories",
             PACKAGE + ".Outfit_Accessories.pak_catalog", PACKAGE + ".Shared.pak_extract",
             PACKAGE + ".Shared.uemodel_import", PACKAGE + ".Shared.materials.dispatch",
             PARITY + ".props", "bpy"]
    modules = {name: types.ModuleType(name) for name in names}
    for module in modules.values():
        module.__path__ = []
    with mock.patch.dict(sys.modules, modules):
        pak = modules[PACKAGE + ".Shared.pak_extract"]
        pak.__file__ = str(ROOT / "Shared/pak_extract.py")
        pak.os = os
        functions("Shared/pak_extract.py", {"_addon_dir", "cache_path", "cache_dir"}, vars(pak))
        pak.saved_material_package = lambda path: CANONICAL
        catalog = modules[PACKAGE + ".Outfit_Accessories.pak_catalog"]
        catalog.installed_build = lambda unused: build
        catalog.canonical_path = lambda path: path
        bpy = modules["bpy"]
        bpy.context = types.SimpleNamespace(scene=types.SimpleNamespace(dr_parity_root=selected, arc_pak_dir=""))
        bpy.path = types.SimpleNamespace(abspath=os.path.abspath)
        bpy.data = types.SimpleNamespace(images=types.SimpleNamespace(load=mock.Mock()))
        plans = {"__name__": PARITY + ".plans", "__package__": PARITY}
        path = ROOT / "Shared/materials/native/shader_parity/plans.py"
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), plans)
        slots = vars(modules[PARITY])
        slots["__package__"] = PARITY
        functions("Shared/materials/native/shader_parity/__init__.py", {"apply_uemodel_slots"}, slots)
        images = {"__name__": PARITY + ".build", "__package__": PARITY, "Path": Path, "bpy": bpy}
        functions("Shared/materials/native/shader_parity/build.py", {"images"}, images)
        yield types.SimpleNamespace(pak=pak, plans=plans, slots=slots, images=images, modules=modules, bpy=bpy)


@contextlib.contextmanager
def read_only_files(files):
    def read_bytes(path):
        try:
            return files[str(path)]
        except KeyError:
            raise FileNotFoundError(str(path)) from None

    def stat(path, **kwargs):
        return types.SimpleNamespace(st_mtime_ns=1, st_size=len(read_bytes(path)))

    with contextlib.ExitStack() as stack:
        for target in ("os.makedirs", "os.mkdir", "pathlib.Path.mkdir", "pathlib.Path.write_bytes", "pathlib.Path.write_text"):
            stack.enter_context(mock.patch(target, side_effect=AssertionError("Read discovery attempted a filesystem mutation: " + target)))
        stack.enter_context(mock.patch.object(Path, "read_bytes", read_bytes))
        stack.enter_context(mock.patch.object(Path, "read_text", lambda path, **kwargs: read_bytes(path).decode("utf-8")))
        stack.enter_context(mock.patch.object(Path, "stat", stat))
        stack.enter_context(mock.patch.object(Path, "is_file", lambda path: str(path) in files))
        stack.enter_context(mock.patch("os.path.isfile", lambda path: str(Path(path)) in files))
        yield


def valid_bundle(root):
    plan = json.dumps({"dag": "material.dag.json", "textures": [], "params": []}).encode()
    dag = b'{"nodes": []}'
    entry = {"dir": "window", "plan_sha256": hashlib.sha256(plan).hexdigest().upper(),
             "dag_sha256": hashlib.sha256(dag).hexdigest().upper()}
    index = {"runtime": "parity-1", "build_id": "2.2.0", "plans": {CANONICAL: entry}}
    return {str(root / "index.json"): json.dumps(index).encode(),
            str(root / "window/plan.json"): plan, str(root / "window/material.dag.json"): dag}


class CacheDiscoveryTests(unittest.TestCase):
    def test_actual_default_discovery_rejects_all_filesystem_writes(self):
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--probe"]
        result = subprocess.run(command, capture_output=True, text=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence["mutation_attempts"], [])
        self.assertEqual(evidence["root"], str(ROOT / "ArcPak/cache/shader_parity/2.2.0"))
        self.assertIsNone(evidence["plan"])
        self.assertTrue(evidence["reason"].startswith("index:"))

    def test_default_find_missing_index_is_read_only(self):
        with source_seam() as seam:
            expected = ROOT / "ArcPak/cache/shader_parity/2.2.0"
            with read_only_files({}):
                self.assertIsNone(seam.plans["find"]("missing.arc-material.json"))
                self.assertTrue(seam.plans["last_reason"].startswith("index:"))
                self.assertEqual(seam.plans["root"](), expected)

    def test_cache_path_preserves_original_path_without_creation(self):
        for addon in (str(ROOT), "X:/fixture/addon with spaces", "relative/addon"):
            with self.subTest(addon=addon), source_seam() as seam:
                seam.pak._addon_dir = lambda: addon
                with read_only_files({}):
                    self.assertEqual(seam.pak.cache_path(), os.path.join(addon, "ArcPak", "cache"))

    def test_writer_creates_original_directory_when_missing_or_existing(self):
        for exists in (False, True):
            with self.subTest(exists=exists), source_seam() as seam:
                expected = os.path.join(seam.pak._addon_dir(), "ArcPak", "cache")
                directories = {os.path.dirname(expected)}
                if exists:
                    directories.add(expected)

                def mkdir(path, mode=0o777, **kwargs):
                    if path in directories:
                        raise FileExistsError(path)
                    directories.add(path)

                with mock.patch("os.mkdir", side_effect=mkdir) as create, mock.patch("os.path.exists", side_effect=lambda path: path in directories), mock.patch("os.path.isdir", side_effect=lambda path: path in directories):
                    self.assertEqual(seam.pak.cache_dir(), expected)
                    self.assertIn(expected, directories)
                    create.assert_called_once_with(expected, 0o777)

    def test_missing_build_remains_unknown(self):
        with source_seam(build="") as seam, read_only_files({}):
            self.assertIsNone(seam.plans["find"]("missing.json"))
            self.assertEqual(seam.plans["last_reason"], "build_unknown")

    def test_invalid_index_and_missing_plan_remain_unresolved(self):
        root = ROOT / "ArcPak/cache/shader_parity/2.2.0"
        cases = [(b"{", "index:"), (b"{}", "index:"),
                 (b'{"runtime":"wrong"}', "runtime_mismatch"),
                 (b'{"runtime":"parity-1","build_id":"wrong"}', "build_mismatch"),
                 (b'{"runtime":"parity-1","build_id":"2.2.0","plans":{}}', "plan_missing")]
        for payload, reason in cases:
            with self.subTest(reason=reason), source_seam() as seam, read_only_files({str(root / "index.json"): payload}):
                self.assertIsNone(seam.plans["find"]("missing.json"))
                self.assertTrue(seam.plans["last_reason"].startswith(reason))

    def test_valid_default_and_selected_bundle_preserve_validation(self):
        for selected in ("", str(ROOT / "fixture/approved bundle")):
            with self.subTest(selected=selected), source_seam(selected) as seam:
                root = Path(selected) if selected else ROOT / "ArcPak/cache/shader_parity/2.2.0"
                files = valid_bundle(root)
                with read_only_files(files):
                    plan = seam.plans["find"]("missing.json")
                    self.assertEqual(plan.canonical, CANONICAL)
                    self.assertEqual(plan.root, root)
                    self.assertEqual(plan.dag(), {"nodes": []})
                    self.assertEqual(seam.plans["last_reason"], "")
                files[str(root / "window/plan.json")] = b"{}"
                seam.plans["_HASHES"].clear()
                with read_only_files(files):
                    self.assertIsNone(seam.plans["find"]("missing.json"))
                    self.assertEqual(seam.plans["last_reason"], "plan.json:hash_mismatch")

    def test_selected_missing_root_is_not_substituted(self):
        selected = str(ROOT / "fixture/missing selected")
        with source_seam(selected) as seam, read_only_files({}):
            self.assertEqual(seam.plans["root"](), Path(selected))
            self.assertIsNone(seam.plans["find"]("missing.json"))
            self.assertTrue(seam.plans["last_reason"].startswith("index:"))

    def test_slot_reads_keep_original_paths_and_assignment(self):
        for present, planned in ((False, False), (True, False), (True, True)):
            with self.subTest(present=present, planned=planned), source_seam() as seam:
                slot = types.SimpleNamespace(link="OBJECT", material=None)
                obj = types.SimpleNamespace(type="MESH", material_slots=[slot])
                lod = types.SimpleNamespace(materials=[("slot", CANONICAL, 0, 1)])
                seam.modules[PACKAGE + ".Shared.uemodel_import"].parse_uemodel = lambda *args, **kwargs: ("mesh", 1, [lod])
                seam.slots["planned"] = mock.Mock(return_value=planned)
                material = {"arc_mi_family": "shader_parity"}
                dispatch = seam.modules[PACKAGE + ".Shared.materials.dispatch"]
                dispatch._get_or_build_shared_mi_material = mock.Mock(return_value=material)
                props = seam.modules[PARITY + ".props"]
                props.ensure_object = mock.Mock()
                props.activate = mock.Mock()
                path = os.path.join(str(ROOT), "ArcPak", "cache", *CANONICAL.split("/")) + ".json"
                with read_only_files({str(Path(path)): b"{}"} if present else {}):
                    self.assertEqual(seam.slots["apply_uemodel_slots"](obj, "mesh.uemodel"), int(present and planned))
                if present:
                    seam.slots["planned"].assert_called_once_with(path)
                else:
                    seam.slots["planned"].assert_not_called()
                if present and planned:
                    self.assertIs(slot.material, material)
                    dispatch._get_or_build_shared_mi_material.assert_called_once_with("MI_Window", path, "mesh.uemodel", obj=obj)
                else:
                    dispatch._get_or_build_shared_mi_material.assert_not_called()

    def test_images_preserve_bundle_fallback_and_missing_errors(self):
        for bundled, fallback, srgb in ((True, False, True), (False, True, True), (False, True, False), (False, False, True)):
            with self.subTest(bundled=bundled, fallback=fallback, srgb=srgb), source_seam() as seam:
                plan = types.SimpleNamespace(directory=ROOT / "fixture/bundle")
                row = {"image": "image.exr", "object_path": CANONICAL, "slot": 3, "srgb": srgb}
                class ImagePlan(dict):
                    directory = plan.directory
                data = ImagePlan(textures=[row])
                original = data.directory / "image.exr"
                cached = ROOT / "ArcPak/cache" / (CANONICAL + ".png")
                files = {str(original): b"image"} if bundled else {str(cached): b"image"} if fallback else {}
                image = types.SimpleNamespace(colorspace_settings=types.SimpleNamespace(name=""), alpha_mode="")
                seam.bpy.data.images.load.return_value = image
                with read_only_files(files):
                    if not bundled and not fallback:
                        with self.assertRaises(FileNotFoundError) as failure:
                            seam.images["images"](data)
                        self.assertEqual(failure.exception.args[0], cached)
                        seam.bpy.data.images.load.assert_not_called()
                        continue
                    result, mode = seam.images["images"](data)
                self.assertEqual(result, {3: image})
                self.assertEqual(mode, "exr" if bundled else "png")
                seam.bpy.data.images.load.assert_called_once_with(str(original if bundled else cached), check_existing=True)
                self.assertEqual(image.colorspace_settings.name, "Non-Color" if bundled or not srgb else "sRGB")
                self.assertEqual(image.alpha_mode, "CHANNEL_PACKED")


def discovery_probe():
    attempts = []

    def reject_writes(event, arguments):
        if event in {"os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.system", "subprocess.Popen", "socket.bind", "socket.connect"}:
            attempts.append(event)
            raise AssertionError("Read discovery attempted mutation: " + event)
        if event == "open" and isinstance(arguments[2], int) and arguments[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            attempts.append(event)
            raise AssertionError("Read discovery attempted write-open")

    with source_seam() as seam:
        sys.addaudithook(reject_writes)
        plan = seam.plans["find"]("missing.arc-material.json")
        print(json.dumps({"root": str(seam.plans["root"]()), "plan": plan,
                          "reason": seam.plans["last_reason"], "mutation_attempts": attempts,
                          "limitations": "Actual root/cache/index filesystem reads; synthetic bpy, build and MI identity. No Blender or approved parity plan."}))


if __name__ == "__main__":
    if sys.argv[1:] == ["--probe"]:
        discovery_probe()
    else:
        unittest.main()
