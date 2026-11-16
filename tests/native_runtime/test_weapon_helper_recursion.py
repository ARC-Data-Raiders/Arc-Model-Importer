"""Actual lookup/registry/builder functions with bounded in-memory bpy fixtures.

This does not run Blender, evaluate shaders, or validate RNA socket semantics.
"""
import ast
from contextlib import contextmanager
import hashlib
import importlib
import sys
import types
import unittest
from unittest import mock

try:
    from .native_test_support import ROOT, real_package
except ImportError:
    from native_test_support import ROOT, real_package


NATIVE = real_package()
HELPERS = importlib.import_module(NATIVE.__name__ + ".shared_helpers")
MATERIAL_PACKAGE = NATIVE.__name__.rsplit(".", 1)[0]
WEAPON_PATH = ROOT / "Shared/materials/weapon.py"
UTILS_PATH = ROOT / "Shared/utils.py"
FUNCTIONS = {
    "_new_iface_float", "_new_iface_color", "_weapon_texturer_is_current",
    "_mix_rgba", "ensure_weapon_texturer_node_group",
}
RECIPE_SHA256 = "3fff771a14ae5d9092a315b4538ca2358c56f8fa2b3b1fbada49760e91a2438e"
SUPPORT_SHA256 = {
    "_new_iface_float": "b9f86dd61646d5afc3feb4bffcb7ded8bed867e64ebf7ff0ecdbebc3e0fe01f1",
    "_new_iface_color": "b3663d55e6d9e49981eca906bfdbab4d2052606d0c186d503f948f97db24fef4",
    "_weapon_texturer_is_current": "df06aefde0d81518779db3b9cb86498806776974146e9cea511094328c6a5633",
    "_mix_rgba": "c8df2a434ad9aa783c51b003e8f1b249bc83700f061b79119587f894052786e2",
}


class Socket:
    def __init__(self, name, in_out="", socket_type=""):
        self.name = name
        self.in_out = in_out
        self.socket_type = socket_type
        self.default_value = None


class Sockets(dict):
    def __missing__(self, name):
        socket = Socket(name)
        self[name] = socket
        return socket


class Node(dict):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.inputs = Sockets()
        self.outputs = Sockets()
        self.name = kind

    def as_pointer(self):
        return id(self)


class Nodes(list):
    def new(self, kind):
        node = Node(kind)
        self.append(node)
        return node


class Links(list):
    def new(self, source, target):
        self.append((source, target))


class Interface:
    def __init__(self):
        self.items_tree = []

    def new_socket(self, *, name, in_out, socket_type):
        socket = Socket(name, in_out, socket_type)
        self.items_tree.append(socket)
        return socket


class Tree(dict):
    def __init__(self, name):
        super().__init__()
        self.name = name
        self.nodes = Nodes()
        self.links = Links()
        self.interface = Interface()


class Groups(list):
    def __init__(self):
        super().__init__()
        self.created = 0

    def new(self, name, kind):
        if kind != "ShaderNodeTree":
            raise AssertionError(kind)
        names = {tree.name for tree in self}
        actual = name
        suffix = 1
        while actual in names:
            actual = f"{name}.{suffix:03}"
            suffix += 1
        tree = Tree(actual)
        self.append(tree)
        self.created += 1
        return tree

    def get(self, name):
        return next((tree for tree in self if tree.name == name), None)


def load_functions(path, names, package, **namespace):
    parsed = ast.parse(path.read_text(encoding="utf-8"))
    selected = [node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in selected} != set(names):
        raise AssertionError("Actual function selection changed")
    selected += [node for node in parsed.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id in {
                     "_WEAPON_TEXTURER", "_WEAPON_TEXTURER_VER",
                 } for target in node.targets)]
    module = types.ModuleType(package + "." + path.stem)
    module.__package__ = package
    module.__file__ = str(path)
    module.__dict__.update(namespace)
    compiled = compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec", dont_inherit=True)
    exec(compiled, module.__dict__)
    return module


class Fixture:
    def __init__(self):
        self.groups = Groups()
        self.bpy = types.ModuleType("bpy")
        self.bpy.data = types.SimpleNamespace(node_groups=self.groups)
        self.utils = load_functions(UTILS_PATH, {"find_node_group"}, MATERIAL_PACKAGE.rsplit(".", 1)[0], bpy=self.bpy)
        self.weapon = load_functions(WEAPON_PATH, FUNCTIONS, MATERIAL_PACKAGE, bpy=self.bpy, utils=self.utils)
        self.original_builder = self.weapon.ensure_weapon_texturer_node_group
        self.builder_entries = 0
        self.active_builders = 0
        self.max_active_builders = 0
        self.weapon.ensure_weapon_texturer_node_group = self.tracked_builder

    def tracked_builder(self):
        self.builder_entries += 1
        self.active_builders += 1
        self.max_active_builders = max(self.max_active_builders, self.active_builders)
        try:
            if self.active_builders > 3:
                raise RecursionError("Actual helper constructor reentered; fixture stops at four active builders")
            return self.original_builder()
        finally:
            self.active_builders -= 1

    @contextmanager
    def installed(self):
        with mock.patch.dict(sys.modules, {MATERIAL_PACKAGE + ".weapon": self.weapon, "bpy": self.bpy}):
            yield self

    def seed(self, *, version=3, recipe=None, epoch=1, key="WeaponTexturer"):
        with mock.patch.object(self.utils, "find_node_group", return_value=None), \
                mock.patch.object(HELPERS, "find_group", return_value=None):
            tree = self.original_builder()
        tree["arc_weapon_texturer_ver"] = version
        tree["dr.shared_helper.key"] = key
        tree["dr.shared_helper.recipe"] = HELPERS.RECIPE if recipe is None else recipe
        tree["dr.shared_helper.epoch"] = epoch
        self.groups.created = 0
        return tree


class WeaponHelperRecursionTests(unittest.TestCase):
    def assert_built_once(self, fixture, tree):
        self.assertEqual(fixture.builder_entries, 1)
        self.assertEqual(fixture.max_active_builders, 1)
        self.assertEqual(fixture.groups.created, 1)
        self.assertTrue(fixture.weapon._weapon_texturer_is_current(tree))
        self.assertEqual([item.name for item in tree.interface.items_tree if item.in_out == "OUTPUT"],
                         ["Base Color", "Roughness", "Metallic", "Normal Color Out"])
        self.assertGreater(len(tree.nodes), 30)
        self.assertGreater(len(tree.links), 40)

    def test_missing_helper_through_actual_lazy_lookup_builds_once(self):
        with Fixture().installed() as fixture:
            tree = fixture.utils.find_node_group("WeaponTexturer")
            self.assert_built_once(fixture, tree)
            self.assertIs(tree, HELPERS.find_group("WeaponTexturer", fixture.bpy))
            self.assertIs(tree, fixture.utils.find_node_group("WeaponTexturer"))
            self.assertEqual(fixture.builder_entries, 1)

    def test_missing_helper_through_actual_direct_constructor_builds_once(self):
        with Fixture().installed() as fixture:
            self.assert_built_once(fixture, fixture.weapon.ensure_weapon_texturer_node_group())

    def test_current_tagged_group_is_reused(self):
        with Fixture().installed() as fixture:
            existing = fixture.seed()
            self.assertIs(fixture.weapon.ensure_weapon_texturer_node_group(), existing)
            self.assertEqual(fixture.groups.created, 0)

    def test_current_registry_epoch_wins_over_display_name(self):
        with Fixture().installed() as fixture:
            older = fixture.seed(epoch=1)
            older.name = "WeaponTexturer"
            newer = fixture.seed(epoch=2)
            newer.name = "DR.Shared.WeaponPreview.current"
            self.assertIsNot(older, newer)
            self.assertIs(fixture.weapon.ensure_weapon_texturer_node_group(), newer)
            self.assertEqual(fixture.groups.created, 0)

    def test_stale_tagged_version_is_renamed_and_rebuilt_without_mutating_recipe(self):
        with Fixture().installed() as fixture:
            existing = fixture.seed(version=2)
            old_nodes = tuple(existing.nodes)
            result = fixture.weapon.ensure_weapon_texturer_node_group()
            self.assertIsNot(result, existing)
            self.assertEqual(existing.name, "WeaponTexturer_stale_v2")
            self.assertEqual(tuple(existing.nodes), old_nodes)
            self.assert_built_once(fixture, result)

    def test_invalid_version_missing_socket_and_v1_socket_are_stale(self):
        for reason in ("invalid-version", "missing-input", "v1-input"):
            with self.subTest(reason=reason), Fixture().installed() as fixture:
                existing = fixture.seed()
                if reason == "invalid-version":
                    existing["arc_weapon_texturer_ver"] = "broken"
                elif reason == "missing-input":
                    existing.interface.items_tree.pop(0)
                else:
                    existing.interface.new_socket(name="Use Pattern", in_out="INPUT", socket_type="NodeSocketFloat")
                result = fixture.weapon.ensure_weapon_texturer_node_group()
                self.assertIsNot(result, existing)
                self.assertTrue(existing.name.startswith("WeaponTexturer_stale"))
                self.assert_built_once(fixture, result)

    def test_future_version_preserves_existing_currentness_policy(self):
        with Fixture().installed() as fixture:
            existing = fixture.seed(version=4)
            self.assertIs(fixture.weapon.ensure_weapon_texturer_node_group(), existing)
            self.assertEqual(fixture.groups.created, 0)

    def test_obsolete_registry_recipe_is_not_reused_or_renamed(self):
        with Fixture().installed() as fixture:
            existing = fixture.seed(recipe="obsolete-recipe")
            result = fixture.utils.find_node_group("WeaponTexturer")
            self.assertIsNot(result, existing)
            self.assertEqual(existing.name, "WeaponTexturer")
            self.assertEqual(existing["dr.shared_helper.recipe"], "obsolete-recipe")
            self.assert_built_once(fixture, result)

    def test_untagged_exact_name_is_not_treated_as_registered_helper(self):
        with Fixture().installed() as fixture:
            existing = Tree("WeaponTexturer")
            fixture.groups.append(existing)
            self.assert_built_once(fixture, fixture.utils.find_node_group("WeaponTexturer"))
            self.assertEqual(existing.name, "WeaponTexturer")
            self.assertEqual(dict(existing), {})

    def test_pure_lookup_never_creates_missing_helper(self):
        with Fixture().installed() as fixture:
            self.assertIsNone(HELPERS.find_group("WeaponTexturer", fixture.bpy))
            self.assertEqual(fixture.builder_entries, 0)
            self.assertEqual(fixture.groups.created, 0)

    def test_other_registered_helper_lookup_and_wrong_key_are_unchanged(self):
        with Fixture().installed() as fixture:
            normal = Tree("DR.Normal.registered")
            normal.update({"dr.shared_helper.key": "NormalFlipper", "dr.shared_helper.recipe": HELPERS.RECIPE})
            fixture.groups.append(normal)
            self.assertIs(fixture.utils.find_node_group("NormalFlipper"), normal)
            self.assertIsNone(HELPERS.find_group("WeaponTexturer", fixture.bpy))
            self.assertEqual(fixture.builder_entries, 0)
            self.assertEqual(fixture.groups.created, 0)

    def test_other_helper_actual_lazy_build_remains_independent(self):
        with Fixture().installed() as fixture:
            normal = fixture.utils.find_node_group("NormalFlipper")
            self.assertIs(normal, HELPERS.find_group("NormalFlipper", fixture.bpy))
            self.assertIs(normal, fixture.utils.find_node_group("NormalFlipper"))
            self.assertEqual(fixture.builder_entries, 0)
            self.assertEqual(fixture.groups.created, 1)
            self.assertIn("not native DX decode", normal["dr.compatibility.encoding"])

    def test_stale_rename_failure_retains_fallback_and_rebuild(self):
        class RenameFailure(Tree):
            def __setattr__(self, name, value):
                if name == "name" and hasattr(self, "name"):
                    raise RuntimeError("rename fixture failure")
                super().__setattr__(name, value)

        with Fixture().installed() as fixture:
            existing = RenameFailure("WeaponTexturer")
            existing.update({"arc_weapon_texturer_ver": 2, "dr.shared_helper.key": "WeaponTexturer",
                             "dr.shared_helper.recipe": HELPERS.RECIPE})
            fixture.groups.append(existing)
            result = fixture.weapon.ensure_weapon_texturer_node_group()
            self.assertIsNot(result, existing)
            self.assertEqual(existing.name, "WeaponTexturer")
            self.assert_built_once(fixture, result)

    def test_unregistered_helper_exact_lookup_and_scan_fallback_are_unchanged(self):
        with Fixture().installed() as fixture:
            other = Tree("UnregisteredHelper")
            fixture.groups.append(other)
            self.assertIs(fixture.utils.find_node_group(other.name), other)
            with mock.patch.object(fixture.groups, "get", side_effect=RuntimeError("bpy get unavailable")):
                self.assertIs(fixture.utils.find_node_group(other.name), other)
            self.assertIsNone(fixture.utils.find_node_group(""))
            self.assertIsNone(fixture.utils.find_node_group("MissingUnregisteredHelper"))
            self.assertEqual(fixture.builder_entries, 0)

    def test_registry_guard_errors_propagate_before_construction(self):
        with Fixture().installed() as fixture:
            with mock.patch.object(HELPERS, "require_loaded_identity", side_effect=HELPERS.RestartRequired("restart fixture")):
                with self.assertRaisesRegex(HELPERS.RestartRequired, "restart fixture"):
                    fixture.utils.find_node_group("WeaponTexturer")
            self.assertEqual(fixture.builder_entries, 0)
            self.assertEqual(fixture.groups.created, 0)
            with self.assertRaises(HELPERS.UnsupportedHelperGroup):
                HELPERS.ensure_group("MissingUnregisteredHelper", fixture.bpy)

    def test_registry_lookup_errors_are_not_silenced(self):
        with Fixture().installed() as fixture:
            with mock.patch.object(HELPERS, "find_group", side_effect=ValueError("registry fixture failure")):
                with self.assertRaisesRegex(ValueError, "registry fixture failure"):
                    fixture.weapon.ensure_weapon_texturer_node_group()
            self.assertEqual(fixture.groups.created, 0)

    def test_constructor_allocation_error_is_not_silenced(self):
        with Fixture().installed() as fixture:
            with mock.patch.object(fixture.groups, "new", side_effect=RuntimeError("allocation fixture failure")):
                with self.assertRaisesRegex(RuntimeError, "allocation fixture failure"):
                    fixture.utils.find_node_group("WeaponTexturer")
            self.assertEqual(fixture.builder_entries, 1)

    def test_unreadable_interface_is_rebuilt(self):
        with Fixture().installed() as fixture:
            existing = fixture.seed()
            existing.interface = None
            self.assertIsNot(fixture.weapon.ensure_weapon_texturer_node_group(), existing)
            self.assertEqual(fixture.groups.created, 1)

    def test_exact_existing_recipe_and_currentness_functions_are_preserved(self):
        parsed = ast.parse(WEAPON_PATH.read_text(encoding="utf-8"))
        functions = {node.name: node for node in parsed.body if isinstance(node, ast.FunctionDef)}
        builder = functions["ensure_weapon_texturer_node_group"]
        start = next(index for index, node in enumerate(builder.body)
                     if isinstance(node, ast.If) and isinstance(node.test, ast.Call)
                     and isinstance(node.test.func, ast.Name) and node.test.func.id == "_weapon_texturer_is_current")
        recipe = ast.dump(ast.Module(body=builder.body[start:], type_ignores=[]), include_attributes=False)
        self.assertEqual(hashlib.sha256(recipe.encode()).hexdigest(), RECIPE_SHA256)
        for name, expected in SUPPORT_SHA256.items():
            with self.subTest(function=name):
                actual = hashlib.sha256(ast.dump(functions[name], include_attributes=False).encode()).hexdigest()
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
