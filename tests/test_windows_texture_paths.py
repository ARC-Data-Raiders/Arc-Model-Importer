"""Pure Win32 MAX_PATH simulation for exact native texture resolvers."""
import ast
import ntpath
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "\\\\?\\"

def extended(path):
    path = ntpath.normpath(path)
    return path if path.startswith(PREFIX) else PREFIX + path

def function(path, name, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]

class WindowsTexturePathTests(unittest.TestCase):
    def setUp(self):
        self.root = "Z:\\" + "long-fixture-" * 20
        self.reference = "/Game/Pioneer/Characters/Treasure/T_Treasure_OcclusionCurvatureMaterialID.T_Treasure_OcclusionCurvatureMaterialID"
        self.tail = "Pioneer\\Characters\\Treasure\\T_Treasure_OcclusionCurvatureMaterialID.png"
        self.files = {}
        self.directories = set()
        self.reads = []
        def checked(path, directory=False):
            self.reads.append(path)
            if len(path) >= 248 and not path.startswith(PREFIX):
                raise AssertionError("Unprefixed Windows long path reached IO: " + path)
            return path in (self.directories if directory else self.files)
        path = types.SimpleNamespace(join=ntpath.join, isfile=lambda path: checked(path),
                                     isdir=lambda path: checked(path, True),
                                     getmtime=lambda path: self.files[path], basename=ntpath.basename,
                                     dirname=ntpath.dirname, normpath=ntpath.normpath)
        self.utils = types.SimpleNamespace(invalidate_dir_caches_if_root_changed=lambda: None,
            _pak_cache_roots=[self.root], get_content_dirs=lambda: [],
            get_pioneer_root=lambda: "", find_content_dir=lambda root: "", find_relative_dir=lambda *args: "")
        self.namespace = {"os": types.SimpleNamespace(path=path, sep="\\"), "utils": self.utils,
                          "_fs_abspath": extended, "_object_path_is_null_stub": lambda path: False,
                          "_OBJECT_PATH_ASSET_CACHE": {}}
        self.resolve = function(ROOT / "textures.py", "find_asset_from_object_path", self.namespace)
        self.os = self.namespace["os"]

    def test_current_cache_long_mask_and_stat_use_extended_paths(self):
        target = extended(ntpath.join(self.root, "RemappedPlugins", "Content", self.tail))
        old = extended(ntpath.join(self.root, "PioneerGame", "Content", self.tail))
        self.files.update({target: 20, old: 10})
        self.assertEqual(self.resolve(self.reference, ".png"), target)
        self.assertTrue(all(path.startswith(PREFIX) for path in self.reads))

    def test_long_content_root_and_legacy_exact_path(self):
        self.utils._pak_cache_roots = []
        content = ntpath.join(self.root, "PioneerGame", "Content")
        self.utils.get_content_dirs = lambda: [content]
        target = extended(ntpath.join(content, self.tail))
        self.files[target] = 1
        self.assertEqual(self.resolve(self.reference, ".png"), target)
        self.namespace["_OBJECT_PATH_ASSET_CACHE"].clear()
        self.utils.get_content_dirs = lambda: []
        self.utils.get_pioneer_root = lambda: self.root
        folder = ntpath.dirname(target)
        self.utils.find_relative_dir = lambda *args: folder
        self.assertEqual(self.resolve(self.reference, ".png"), target)

    def test_cached_raw_success_is_normalized_before_return(self):
        clean = self.reference.rsplit(".", 1)[0]
        target = ntpath.join(self.root, "RemappedPlugins", "Content", self.tail)
        self.namespace["_OBJECT_PATH_ASSET_CACHE"][(clean.lower(), ".png")] = target
        self.assertEqual(self.resolve(self.reference, ".png"), extended(target))

    def test_plugin_direct_mount_and_content_alias_choose_newest(self):
        reference = "/EmbarkScript/MaterialLibrary/Character/Templates/Textures/T_HueNoise_01_C.T_HueNoise_01_C"
        tail = "MaterialLibrary\\Character\\Templates\\Textures\\T_HueNoise_01_C.png"
        direct = extended(ntpath.join(self.root, "EmbarkScript", tail))
        alias = extended(ntpath.join(self.root, "EmbarkScript", "Content", tail))
        self.files.update({direct: 10, alias: 20})
        self.assertEqual(self.resolve(reference, ".png"), alias)
        self.files[direct] = 30
        self.namespace["_OBJECT_PATH_ASSET_CACHE"].clear()
        self.assertEqual(self.resolve(reference, ".png"), direct)

    def test_engine_content_and_remapped_plugin_layouts(self):
        engine = extended(ntpath.join(self.root, "Engine", "Content", "EngineResources", "DefaultTexture.png"))
        plugin = extended(ntpath.join(self.root, "RemappedPlugins", "ExamplePlugin", "Content", "Textures", "T_Test.png"))
        self.files.update({engine: 10, plugin: 10})
        self.assertEqual(self.resolve("/Engine/EngineResources/DefaultTexture.DefaultTexture", ".png"), engine)
        self.assertEqual(self.resolve("/ExamplePlugin/Textures/T_Test.T_Test", ".png"), plugin)

    def test_secondary_fmodel_plugin_mount_uses_exact_export_sibling(self):
        self.utils._pak_cache_roots = []
        self.utils.get_content_dirs = lambda: [ntpath.join(self.root, "PioneerGame", "Content")]
        target = extended(ntpath.join(self.root, "EmbarkScript", "MaterialLibrary", "T_HueNoise.png"))
        self.files[target] = 1
        self.assertEqual(self.resolve("/EmbarkScript/MaterialLibrary/T_HueNoise.T_HueNoise", ".png"), target)

    def test_missing_plugin_never_uses_game_bfs_or_traversal(self):
        def forbidden(*args):
            raise AssertionError("Plugin resolution must not walk or guess a Game folder")
        self.utils.find_relative_dir = forbidden
        self.assertEqual(self.resolve("/EmbarkScript/Missing/T_Absent.T_Absent", ".png"), "")
        self.reads.clear()
        for path in ("/EmbarkScript/../Outside/T_Test.T_Test", "C:/Outside/T_Test.png", "/EmbarkScript//T_Test"):
            self.assertEqual(self.resolve(path, ".png"), "")
        self.assertEqual(self.reads, [])

    def test_local_fallback_directory_and_file_checks_are_prefixed(self):
        folder = ntpath.join(self.root, "Materials")
        self.directories.add(extended(folder))
        leaf = self.reference.rsplit("/", 1)[-1].split(".")[0]
        target = extended(ntpath.join(folder, leaf + ".png"))
        self.files[target] = 1
        textures = types.SimpleNamespace(find_asset_from_object_path=lambda *args: "", _fs_abspath=extended)
        resolve = function(ROOT / "materials/common.py", "_resolve_mi_texture_path", {"os": self.os, "textures": textures})
        self.assertEqual(resolve(self.reference, [folder]), target)
        self.assertTrue(all(path.startswith(PREFIX) for path in self.reads))

if __name__ == "__main__":
    unittest.main(verbosity=2)
