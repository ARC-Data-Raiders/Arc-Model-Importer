"""R24: engine helper note, engine default textures, texture-array slice refs and flags. Pure facts, no Blender."""
import ast
import importlib
import types
import unittest

from .native_test_support import PACKAGE, ROOT, namespace
from .test_material_audit import G, IMG, MI, audit

namespace()
rules = importlib.import_module(PACKAGE + '.Shared.materials.audit_param_rules')
interop = importlib.import_module(PACKAGE + '.Shared.materials.native.interop')
MESH = {'uv_layers': ['UV0'], 'color_attributes': ['Col']}


def _env_slice_ref():
    path = ROOT / 'Shared/materials/environment.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    body = [x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'texture_array_slice_ref']
    assert len(body) == 1
    mod = types.ModuleType('r24_env')
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(path), 'exec'), mod.__dict__)
    return mod.texture_array_slice_ref


def flags(graph, mi):
    return set(audit.audit_material_facts(graph, mi, None, MESH, None, ())['flags'])


class EngineHelper(unittest.TestCase):
    def test_resolution_note_flags_info(self):
        mi = MI(resolution_note='engine_helper_material: basename BasicShapeMaterial picked X by object path order of 2')
        self.assertIn('engine_helper_material', flags(G(), mi))
        self.assertEqual(rules.severity('engine_helper_material'), rules.INFO)

    def test_no_note_no_flag(self):
        self.assertNotIn('engine_helper_material', flags(G(), MI()))


class EngineDefaultTexture(unittest.TestCase):
    def test_engine_default_normal(self):
        self.assertTrue(interop.engine_default_texture_ref('/Engine/EngineMaterials/DefaultNormal.DefaultNormal'))

    def test_game_texture_is_not_default(self):
        self.assertFalse(interop.engine_default_texture_ref('/Game/T/DefaultNormal.DefaultNormal'))
        self.assertFalse(interop.engine_default_texture_ref('/Engine/T/T_Rock_N.T_Rock_N'))


class ArraySlice(unittest.TestCase):
    def test_slice_ref(self):
        ref = _env_slice_ref()
        self.assertEqual(ref('/Game/T/TA_TBG_DetailRocks_01.TA_TBG_DetailRocks_01', 0),
                         '/Game/T/TA_TBG_DetailRocks_01_LAYER0.TA_TBG_DetailRocks_01_LAYER0')
        self.assertEqual(ref('', 0), '')

    def test_linked_slice_links_param_and_flags_approximate(self):
        mi = MI(textures={'2. Detail Array': '/Game/T/TA_TBG_DetailRocks_01'})
        got = flags(G(images=[IMG('TA_TBG_DetailRocks_01_LAYER0')]), mi)
        self.assertNotIn('tex_param_unlinked:2. Detail Array', got)
        self.assertIn('array_slice_approximate:2. Detail Array', got)
        self.assertEqual(rules.severity('array_slice_approximate:2. Detail Array'), rules.INCONSISTENCY)

    def test_unlinked_array_stays_unlinked(self):
        mi = MI(textures={'2. Detail Array': '/Game/T/TA_TBG_DetailRocks_01'})
        got = flags(G(images=[IMG('T_X_BC')]), mi)
        self.assertNotIn('array_slice_approximate:2. Detail Array', got)
        self.assertTrue(any(f.endswith(':2. Detail Array') for f in got))
