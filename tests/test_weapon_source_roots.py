"""Weapon imports must add their source root without visiting lazy backups."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_weapon_source_roots"


class WeaponSourceRootsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.local = self.root / "selected" / "PioneerGame" / "Content" / "Pioneer"
        self.other = self.root / "configured" / "PioneerGame" / "Content" / "Pioneer"
        self.relative = Path("Items/Firearms/Probe/MI_Probe.json")
        for root in (self.local, self.other):
            (root / self.relative).parent.mkdir(parents=True)
            (root / self.relative).write_text("{}", encoding="utf-8")
        self.mesh = self.local / self.relative.with_name("SM_Probe.uemodel")
        self.mesh.touch()
        self.reference = "/Game/Pioneer/Items/Firearms/Probe/MI_Probe.MI_Probe"
        self.scene = SimpleNamespace(arc_pioneer_root=str(self.other))
        package = ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]
        self.utils = ModuleType(PACKAGE + ".Shared.utils")
        self.utils._pak_cache_roots = []
        self.utils.get_pioneer_root = Mock(return_value=str(self.other))
        self.utils.get_content_dirs = Mock(return_value=[])
        sys.modules[PACKAGE] = package
        sys.modules[self.utils.__name__] = self.utils
        self.addCleanup(self.remove_modules)
        spec = importlib.util.spec_from_file_location(PACKAGE + ".Weapons.weapon_assets", ROOT / "Weapons/weapon_assets.py")
        self.assets = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.assets
        spec.loader.exec_module(self.assets)
        self.assets.SUPPLIED_UNPACK = str(self.root / "old_unpack")

    def remove_modules(self):
        for name in list(sys.modules):
            if name == PACKAGE or name.startswith(PACKAGE + "."):
                del sys.modules[name]

    def test_material_call_uses_selected_root_without_list_mutation(self):
        materials = ModuleType(PACKAGE + ".Shared.materials")
        materials.weapon_surface = SimpleNamespace()
        materials.weapon_fur = SimpleNamespace(apply_shell_offsets=Mock())
        sys.modules[materials.__name__] = materials
        tree = ast.parse((ROOT / "Weapons/weapon_import.py").read_text(encoding="utf-8-sig"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_material")
        self.assets.mesh_slots = Mock(return_value=[("Body", self.reference)])
        namespace = {"__package__": PACKAGE + ".Weapons", "assets": self.assets, "bpy": SimpleNamespace(context=SimpleNamespace(scene=self.scene)), "Path": Path, "json": json}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "Weapons/weapon_import.py", "exec"), namespace)
        obj = type("MeshObject", (dict,), {})()
        obj.data = SimpleNamespace(materials=[None], uv_layers=[])
        obj.material_slots = [SimpleNamespace(link="", material=None)]
        material = object()
        cache = {(str(self.local / self.relative).casefold(), ()): material}
        self.assertEqual(namespace["_material"](obj, self.mesh, {}, cache=cache), 1)
        self.assertIs(obj.material_slots[0].material, material)
        self.utils.get_content_dirs.assert_not_called()

    def test_selected_source_precedes_configured_export_without_discovery(self):
        roots = self.assets.pioneer_roots(self.scene, source_path=self.mesh)
        self.utils.get_content_dirs.assert_not_called()
        self.assertEqual(self.assets.resolve(self.reference, roots), self.local / self.relative)
        self.utils.get_content_dirs.assert_not_called()

    def test_missing_primary_material_still_visits_fallback(self):
        (self.local / self.relative).unlink()
        self.scene.arc_pioneer_root = ""
        self.utils.get_content_dirs.return_value = [str(self.other.parent)]
        roots = self.assets.pioneer_roots(self.scene, source_path=self.mesh)
        self.assertEqual(self.assets.resolve(self.reference, roots), self.other / self.relative)
        self.utils.get_content_dirs.assert_called_once()

    def test_default_root_call_retains_configured_priority(self):
        roots = self.assets.pioneer_roots(self.scene)
        self.assertEqual(self.assets.resolve(self.reference, roots), self.other / self.relative)
        self.utils.get_content_dirs.assert_not_called()

    def test_current_roots_keeps_source_and_excludes_old_unpack(self):
        self.scene.arc_pioneer_root = self.assets.SUPPLIED_UNPACK
        roots = self.assets.current_roots(self.assets.pioneer_roots(self.scene, source_path=self.mesh))
        self.assertFalse(roots.include_old)
        self.assertEqual(self.assets.resolve(self.reference, roots), self.local / self.relative)
        self.utils.get_content_dirs.assert_not_called()

    def test_noncanonical_source_does_not_create_a_content_root(self):
        roots = self.assets.pioneer_roots(self.scene, source_path=self.root / "SM_Outside.uemodel")
        self.assertEqual(self.assets.resolve(self.reference, roots), self.other / self.relative)
        self.utils.get_content_dirs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
