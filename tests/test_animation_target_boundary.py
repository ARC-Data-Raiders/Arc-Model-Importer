"""Animation/pose target = ui_targets.resolve(context, "character") only (Sol 3: no legacy scene-wide fallback)."""
import ast
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from typing import Any, Optional

ROOT = Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY', Path(__file__).resolve().parents[1])).resolve()
PKG = '_anim_boundary_pkg'


class Obj(dict):
    def __init__(self, name, type='MESH', parent=None, **props):
        super().__init__(props)
        self.name, self.type, self.parent = name, type, parent
        self.children = []
        self.material_slots = ()
        self.modifiers = ()
        if parent is not None:
            parent.children.append(self)

    __hash__ = object.__hash__
    __eq__ = object.__eq__

    @property
    def children_recursive(self):
        out = []
        for child in self.children:
            out.append(child)
            out.extend(child.children_recursive)
        return out

    def visible_get(self):
        return True

    def select_get(self):
        return False


class Objects(list):
    def get(self, name):
        return next((o for o in self if o.name == name), None)


class Context:
    def __init__(self, objects, active=None):
        self.scene = types.SimpleNamespace(name='Scene', objects=Objects(objects),
                                           arc_last_applied_anim_armature='')
        self.active_object = self.object = active
        self.selected_objects = [active] if active is not None else []
        self.view_layer = types.SimpleNamespace(objects=types.SimpleNamespace(active=active))


def _resolver():
    spec = importlib.util.spec_from_file_location(PKG + '.Shared.ui_targets', ROOT / 'Shared/ui_targets.py')
    targets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(targets)
    pkg, shared = types.ModuleType(PKG), types.ModuleType(PKG + '.Shared')
    pkg.__path__, shared.__path__ = [], []
    shared.ui_targets = targets
    sys.modules.update({PKG: pkg, PKG + '.Shared': shared, PKG + '.Shared.ui_targets': targets})
    tree = ast.parse((ROOT / 'Animations/animation_import.py').read_text(encoding='utf-8'))
    wanted = {'resolve_target_armature', '_legacy_target_armature'}
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    ns = {'__name__': PKG + '.Animations.animation_import', '__package__': PKG + '.Animations',
          'Optional': Optional, 'Any': Any, 'bpy': types.SimpleNamespace(data=types.SimpleNamespace(objects={}))}
    exec(compile(ast.Module(body=body, type_ignores=[]), 'animation_import', 'exec'), ns)
    return ns['resolve_target_armature'], targets


class AnimationTargetBoundary(unittest.TestCase):
    def tearDown(self):
        for name in [n for n in sys.modules if n.startswith(PKG)]:
            del sys.modules[name]

    def test_unstamped_lone_armature_is_not_a_target(self):
        resolve, _ = _resolver()
        rig = Obj('Mannequin_Rig', 'ARMATURE')
        mesh = Obj('Mannequin', parent=rig)
        self.assertIsNone(resolve(Context([rig, mesh])))
        self.assertIsNone(resolve(Context([rig, mesh], active=mesh)))

    def test_stamped_character_resolves_to_its_armature(self):
        resolve, targets = _resolver()
        rig = Obj('SK_Scout_Rig', 'ARMATURE', arc_assembly_role='Outfit')
        body = Obj('Scout_Body', parent=rig, arc_assembly_role='Outfit')
        targets.stamp([rig, body], targets.new_import_id(), 'character', 'Scout')
        self.assertIs(resolve(Context([rig, body], active=body)), rig)
        self.assertIs(resolve(Context([rig, body])), rig)

    def test_resolver_error_fails_closed(self):
        resolve, targets = _resolver()
        targets.resolve = lambda context, role: (_ for _ in ()).throw(RuntimeError('boom'))
        self.assertIsNone(resolve(Context([Obj('Rig', 'ARMATURE')])))


if __name__ == '__main__':
    unittest.main()
