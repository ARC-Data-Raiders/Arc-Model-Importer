"""Breakup / overlay / Concrete blend stages in rebuild/surfaces.py, driven by a fake Graph (no Blender)."""
import importlib.util
import sys
import types
import unittest

from native_runtime.native_test_support import PACKAGE, ROOT, namespace


def load_surfaces(name='surfaces'):
    """Load a rebuild module inside the stub package so relative imports resolve; rebuild/__init__ (bpy) is skipped."""
    namespace()
    pkg = PACKAGE + '.Shared.materials.rebuild'
    if pkg not in sys.modules:
        stub = types.ModuleType(pkg)
        stub.__path__ = [str(ROOT / 'Shared/materials/rebuild')]
        sys.modules[pkg] = stub
    full = pkg + '.' + name
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, ROOT / f'Shared/materials/rebuild/{name}.py')
    module = importlib.util.module_from_spec(spec)
    module.__package__ = pkg
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


class Tex(dict):
    pass


class FakeGraph:
    """Every op returns an opaque token; image() records sampled refs; uv() logs the real clamp note."""

    def __init__(self, uv_layers=1):
        self.report = {'warnings': [], 'notes': []}
        self.sampled = []
        self.uv_layers = uv_layers
        self.obj = None
        self.finished = None

    def _tok(self, *args):
        return ('op',) + tuple(str(a) for a in args)

    def section(self, *a): pass
    def math(self, op, a, b=0): return self._tok(op, a, b)
    def add(self, a, b): return self._tok('add', a, b)
    def sub(self, a, b): return self._tok('sub', a, b)
    def mul(self, a, b): return self._tok('mul', a, b)
    def div(self, a, b): return self._tok('div', a, b)
    def clamp(self, a): return self._tok('clamp', a)
    def mix(self, a, b, f, label='lerp'): return self._tok('mix', a, b, f, label)
    def cmul(self, a, b, label='multiply'): return self._tok('cmul', a, b)
    def split(self, v): return self._tok('x', v), self._tok('y', v), self._tok('z', v)
    def combine(self, x, y, z=0): return self._tok('combine', x, y, z)
    def vector(self, op, a, b=(0, 0, 0), scale=1): return self._tok('vec', op, a, b, scale)
    def decode_normal(self, tex, strength=1): return self._tok('normal', tex['ref'], strength)
    def rnm(self, a, b): return self._tok('rnm', a, b)

    def uv(self, channel=0):
        if channel >= self.uv_layers:
            self.report.setdefault('notes', []).append(f'UV{channel} clamped to last mesh channel UV{self.uv_layers - 1}')
        return self._tok('uv', channel)

    def transform_uv(self, channel=0, scale=1, offset=(0, 0), rotation=0):
        return self._tok('tuv', self.uv(channel), scale)

    def image(self, ref, uv=None, layer=None, srgb=None, label='', required=True):
        if not ref:
            return None
        self.sampled.append(ref)
        return Tex({k: self._tok('tex', ref, k) for k in ('color', 'r', 'g', 'b', 'a', 'normal')}, ref=ref)

    def node(self, *a): raise AssertionError('no node needed')

    def finish(self, color, rough=None, metal=None, normal=None, spec=None, emission=None, **kw):
        self.finished = dict(color=color, rough=rough, metal=metal, normal=normal)


def record(master, textures, switches=(), scalars=None, colors=None):
    return {'master': '/Game/Pioneer/MaterialLibrary/M_Presets/' + master, 'textures': dict(textures),
            'switches': dict(switches), 'scalars': dict(scalars or {}), 'colors': dict(colors or {}), 'settings': {}}


FLOOR_TEXTURES = {'CR': '/Game/T/T_Floor_CR', 'NOH': '/Game/T/T_Floor_NOH',
                  'Breakup Mask - Linear Grayscale': '/Game/T/T_Noise_L',
                  'Breakup_Material_CR': '/Game/T/T_Tile_CR', 'Breakup_Material_NOH': '/Game/T/T_Tile_NOH'}


class SurfaceLayerStages(unittest.TestCase):
    surfaces = load_surfaces()

    def test_breakup_layer_sampled_when_gate_on(self):
        g = FakeGraph()
        self.surfaces.build_surface(g, record('M_ArchitecturePreset_Interior_Floors', FLOOR_TEXTURES, {'Enable Breakup Material': True}))
        for ref in ('/Game/T/T_Noise_L', '/Game/T/T_Tile_CR', '/Game/T/T_Tile_NOH'):
            self.assertIn(ref, g.sampled)
        self.assertIn('T_Tile_CR', str(g.finished['color']))
        self.assertIn('T_Noise_L', str(g.finished['color']))
        self.assertIn('T_Tile_NOH', str(g.finished['normal']))
        self.assertTrue(any(n.startswith('approximate:') for n in g.report['notes']))

    def test_two_layer_blend_basecolor_normal(self):
        g = FakeGraph()
        tex = {'BaseColor': '/Game/T/T_A_C', 'Breakup BaseColor': '/Game/T/T_B_C', 'Breakup NormalMap': '/Game/T/T_B_N',
               'Breakup Mask - Linear Grayscale': '/Game/T/T_M_L'}
        self.surfaces.build_surface(g, record('M_2LayerBlend_01', tex, {'Use Breakup Texture': True, 'Use Breakup Tint': True},
                                              colors={'Breakup Tint': (.5, .5, .5, 1)}))
        self.assertIn('T_B_C', str(g.finished['color']))
        self.assertIn('T_B_N', str(g.finished['normal']))
        self.assertIn('/Game/T/T_M_L', g.sampled)

    def test_breakup_gate_off_marks_skipped(self):
        g = FakeGraph()
        self.surfaces.build_surface(g, record('M_ArchitecturePreset_Interior_Floors', FLOOR_TEXTURES, {'Enable Breakup Material': False}))
        self.assertNotIn('/Game/T/T_Tile_CR', g.sampled)
        skipped = g.report.get('skipped_textures', {})
        self.assertEqual({'Breakup Mask - Linear Grayscale', 'Breakup_Material_CR', 'Breakup_Material_NOH'}, set(skipped))
        self.assertIn('off', skipped['Breakup_Material_CR'])

    def test_world_state_breakup_masks_not_breakup_layer(self):
        """Snow/frost 'Breakup' masks (PropTrim) belong to world-state layers, never to the breakup layer route."""
        tex = {'CR': '/Game/T/T_Trim_CR', 'Snow Breakup Mask': '/Game/T/T_Snow_M',
               'Frost Fresnel Mask Breakup': '/Game/T/T_Frost_01_A_NM'}
        g = FakeGraph()
        self.surfaces.build_surface(g, record('M_PropTrimPreset_CR+NOM+AO', tex, {'Enable Breakup Material': True},
                                              colors={'Breakup Tint': (.5, .5, .5, 1)}))
        self.assertNotIn('/Game/T/T_Snow_M', g.sampled)
        self.assertNotIn('/Game/T/T_Frost_01_A_NM', g.sampled)
        g = FakeGraph()
        self.surfaces.build_surface(g, record('M_PropTrimPreset_CR+NOM+AO', tex, {'Enable Breakup Material': False}))
        skipped = g.report.get('skipped_textures', {})
        self.assertFalse(any('breakup layer' in str(v) for k, v in skipped.items() if k != 'CR'), skipped)

    def test_overlay_built_and_not_unsupported(self):
        g = FakeGraph()
        tex = {'CR': '/Game/T/T_Metal_CR', 'Overlay': '/Game/T/T_Rust_C'}
        self.surfaces.build_surface(g, record('M_Tileable_Metal_CR+NXX', tex, {'UseOverlay': True}, {'OverlayRange Min': .2}))
        self.assertIn('/Game/T/T_Rust_C', g.sampled)
        self.assertIn('T_Rust_C', str(g.finished['color']))
        self.assertNotIn('UseOverlay', g.report.get('unsupported_features', []))
        self.assertFalse(any('UV1 clamped' in n for n in g.report['notes']))

    def test_concrete_unused_uv1_branch_and_blend_skips(self):
        g = FakeGraph(uv_layers=1)
        tex = {'CR': '/Game/T/T_C_CR', 'NOH': '/Game/T/T_C_NOH', 'Overlay': '/Game/T/T_O_C',
               'CR Blend': '/Game/T/T_B_CR', 'NOH Blend': '/Game/T/T_B_NOH', 'PaintBreakup': '/Game/T/T_P'}
        sw = {'UseOverlay': True, 'Enable Blend Textures': False, 'IntersectionBlending': True, 'Use Paint': False}
        try:
            self.surfaces.build_architecture(g, record('M_ArchitecturePreset_Concrete_01', tex, sw, {'Use UV1 as Overlay UVs': 0}))
        except AssertionError:
            pass  # geometry node after the stages under test
        self.assertFalse(any('UV1 clamped' in n for n in g.report['notes']), g.report['notes'])
        skipped = g.report.get('skipped_textures', {})
        self.assertIn('IntersectionBlending', skipped.get('CR Blend', ''))
        self.assertIn('NOH Blend', skipped)
        self.assertIn('PaintBreakup', skipped)


class EnvGraph(FakeGraph):
    """Environment-layer graph: triplanar sampling and a mesh without vertex colours."""

    def __init__(self):
        super().__init__()
        self.obj = types.SimpleNamespace(data=types.SimpleNamespace(color_attributes=[]))

    def triplanar(self, ref, size=100, srgb=None, label=''):
        return self.image(ref)


SNOW_TEXTURES = {'1. CR': '/Game/T/T_Brick_CR', '1. NOH': '/Game/T/T_Brick_NOH', '2. CR': '/Game/T/T_Plaster_CR',
                 '2. NOH': '/Game/T/T_Plaster_NOH', '3. Blend Mask': '/Game/T/T_Blend_M', '4.. Overlay': '/Game/T/T_Dirt_O'}


class MaskOnlyAndEnvOverlay(unittest.TestCase):
    surfaces = load_surfaces()

    def test_breakup_mask_only_drives_tint(self):
        g = FakeGraph()
        tex = {'CR': '/Game/T/T_Roof_CR', 'NOH': '/Game/T/T_Roof_NOH', 'Breakup Mask': '/Game/T/T_Noise_M'}
        self.surfaces.build_surface(g, record('M_CR_NOH_Parallax_VTblend_01', tex, {'Use Breakup Texture': True},
                                              {'Mask Range Low': .2, 'Mask Range High': .8}, {'Tint Color 2': (.4, .3, .2, 1)}))
        self.assertIn('/Game/T/T_Noise_M', g.sampled)
        self.assertIn('T_Noise_M', str(g.finished['color']))
        self.assertIn('breakup mask variation (approximate)', g.report.get('implemented_stages', []))

    def test_breakup_mask_only_without_effect_param_skipped(self):
        g = FakeGraph()
        tex = {'CR': '/Game/T/T_Roof_CR', 'Breakup Mask': '/Game/T/T_Noise_M'}
        self.surfaces.build_surface(g, record('M_CR_NOH_Parallax_VTblend_01', tex, {'Use Breakup Texture': True}))
        self.assertNotIn('/Game/T/T_Noise_M', g.sampled)
        self.assertIn('Breakup Mask', g.report.get('skipped_textures', {}))

    def test_env_layers_overlay_built_when_switch_on(self):
        g = EnvGraph()
        self.surfaces.build_environment_layers(g, record('M_Res_Architecture_Snow_01', SNOW_TEXTURES,
                                                         {'0. Use Triplanar Overlay': True}, {'4. Overlay Strength': .5}))
        self.assertIn('/Game/T/T_Dirt_O', g.sampled)
        self.assertIn('T_Dirt_O', str(g.finished['color']))
        self.assertIn('environment overlay layer (approximate)', g.report['implemented_stages'])
        self.assertIn('two surface layers', g.report['implemented_stages'])

    def test_env_overlay_brightness_is_offset_not_gain(self):
        # RoadWall authors '4. Overlay Brightness' 0; a gain made the overlay multiply the wall to black.
        def ops(brightness):
            g = EnvGraph()
            calls = []
            base = g.vector
            g.vector = lambda op, a, b=(0, 0, 0), scale=1: calls.append((op, b, scale)) or base(op, a, b, scale)
            self.surfaces.build_environment_layers(g, record('M_Res_Architecture_Snow_01', SNOW_TEXTURES,
                                                             {'0. Use Triplanar Overlay': True}, {'4. Overlay Brightness': brightness}))
            return calls
        self.assertIn(('SCALE', (0, 0, 0), 2), ops(0.0))
        self.assertNotIn('ADD', [op for op, b, scale in ops(0.0)])
        self.assertIn(('ADD', (-0.07, -0.07, -0.07), 1), ops(-0.07))

    def test_env_layers_overlay_skipped_when_switch_off(self):
        g = EnvGraph()
        self.surfaces.build_environment_layers(g, record('M_Res_Architecture_Snow_01', SNOW_TEXTURES, {'0. Use Triplanar Overlay': False}))
        self.assertNotIn('/Game/T/T_Dirt_O', g.sampled)
        self.assertIn('off', g.report.get('skipped_textures', {}).get('4.. Overlay', ''))


class PropGraph(FakeGraph):
    """FakeGraph plus the block/parameter/export API the decal and prop-trim builders use."""

    def __init__(self):
        super().__init__()
        self.obj = types.SimpleNamespace(matrix_world=types.SimpleNamespace(to_scale=lambda: (1, 1, 1)),
                                         data=types.SimpleNamespace(color_attributes=[]))

    def parameter(self, name, value, kind=None): return value
    def math(self, op, a, b=0, label=''): return self._tok(op, a, b)
    def export(self, value, *a): return value
    def decode_normal(self, tex, strength=1): return self._tok('normal', tex.get('ref'), strength)

    def block(self, *a):
        import contextlib
        return contextlib.nullcontext(self)


PUDDLE = 'Puddles Mask - Linear Grayscale'


class WaterlineAndFrostLayers(unittest.TestCase):
    surfaces = load_surfaces()

    def test_puddles_layer_built_when_switch_on(self):
        g = FakeGraph()
        tex = dict(FLOOR_TEXTURES, **{PUDDLE: '/Game/T/T_PuddlesMask_01_X'})
        self.surfaces.build_surface(g, record('M_ArchitecturePreset_Interior_Floors', tex, {'Puddles': True}, {'Puddles Tiling': 2}))
        self.assertIn('/Game/T/T_PuddlesMask_01_X', g.sampled)
        for key in ('color', 'rough', 'normal'):
            self.assertIn('T_PuddlesMask_01_X', str(g.finished[key]), key)
        self.assertIn('puddles waterline wet layer (approximate)', g.report['implemented_stages'])
        self.assertNotIn('Puddles', g.report.get('unsupported_features', []))
        self.assertTrue(any('puddles' in n for n in g.report['notes'] if n.startswith('approximate:')))

    def test_puddles_layer_skipped_when_switch_off(self):
        g = FakeGraph()
        tex = dict(FLOOR_TEXTURES, **{PUDDLE: '/Game/T/T_PuddlesMask_01_X'})
        self.surfaces.build_surface(g, record('M_ArchitecturePreset_Interior_Floors', tex, {'Puddles': False}))
        self.assertNotIn('/Game/T/T_PuddlesMask_01_X', g.sampled)
        self.assertIn('off', g.report.get('skipped_textures', {}).get(PUDDLE, ''))

    def test_decal_puddles_layer(self):
        decals = load_surfaces('decals')
        g = PropGraph()
        decals.build_decal(g, record('M_DecalPreset_Masked_Atlas+Detail_NoContact', {'Mask': '/Game/T/T_Dec_M', PUDDLE: '/Game/T/T_Pud_X'},
                                     {'Puddles': True}, colors={'BC Color': (.4, .4, .4, 1)}))
        self.assertIn('/Game/T/T_Pud_X', g.sampled)
        self.assertIn('T_Pud_X', str(g.finished['rough']))
        self.assertIn('puddles waterline wet layer (approximate)', g.report['implemented_stages'])

    def test_prop_trim_frost_layer(self):
        prop_trim = load_surfaces('prop_trim')
        tex = {'CR Texture': '/Game/T/T_M_CR', 'NXX/NMX Texture': '/Game/T/T_M_NXX', 'Prop Frost Mask': '/Game/T/T_Frost_M',
               'Frost Fresnel Mask Breakup': '/Game/T/T_Frost_01_A_NM'}
        g = PropGraph()
        prop_trim.build_prop_trim(g, record('M_Tileable_Metal_CR+NXX', tex, {'Use Frost': True}, {'Frost Roughness': .9}))
        self.assertIn('/Game/T/T_Frost_M', g.sampled)
        self.assertNotIn('/Game/T/T_Frost_01_A_NM', g.sampled)
        self.assertIn('T_Frost_M', str(g.finished['color']))
        self.assertIn('0.9', str(g.finished['rough']))
        self.assertIn('Frost Fresnel Mask Breakup', g.report.get('skipped_textures', {}))
        g = PropGraph()
        prop_trim.build_prop_trim(g, record('M_Tileable_Metal_CR+NXX', tex, {'Use Frost': False}))
        self.assertNotIn('/Game/T/T_Frost_M', g.sampled)
        self.assertIn('off', g.report.get('skipped_textures', {}).get('Prop Frost Mask', ''))

    def test_prop_trim_color_overlay_and_distortion(self):
        prop_trim = load_surfaces('prop_trim')
        tex = {'CR Texture': '/Game/T/T_M_CR', 'NXX/NMX Texture': '/Game/T/T_M_NXX', 'Overlay': '/Game/T/T_Ov',
               'OverlayDistortion': '/Game/T/T_OvD'}
        g = PropGraph()
        prop_trim.build_prop_trim(g, record('M_PropTrimPreset_CR+NOM+AO', tex, {'UseOverlay': False, 'Color Overlay': True},
                                            {'Overlay Distort': .2}))
        self.assertIn('/Game/T/T_Ov', g.sampled)
        self.assertIn('/Game/T/T_OvD', g.sampled)
        self.assertIn('T_Ov', str(g.finished['color']))
        g = PropGraph()
        prop_trim.build_prop_trim(g, record('M_PropTrimPreset_CR+NOM+AO', tex, {'UseOverlay': False, 'Color Overlay': False}))
        self.assertNotIn('/Game/T/T_Ov', g.sampled)
        self.assertIn('off', g.report.get('skipped_textures', {}).get('OverlayDistortion', ''))

    def test_snow_layer_prop_trim_and_packed_prop(self):
        prop_trim, surfaces = load_surfaces('prop_trim'), load_surfaces('surfaces')
        snow = {'Snow Base Color': '/Game/T/T_Snow_BC', 'Snow Breakup Mask': '/Game/T/T_Snow_M',
                'Snow Breakup Mask 2': '/Game/T/T_Snow_M2', 'Snow Normal': '/Game/T/T_Snow_N'}
        trim = {'CR Texture': '/Game/T/T_M_CR', 'NXX/NMX Texture': '/Game/T/T_M_NXX', **snow}
        g = PropGraph()
        prop_trim.build_prop_trim(g, record('M_PropTrim_CR+NOM+AO', trim, {'Use Perma Snow': True}, {'Snow Mask Max': .5}))
        for ref in snow.values():
            self.assertIn(ref, g.sampled)
        self.assertIn('T_Snow_BC', str(g.finished['color']))
        self.assertIn('T_Snow_N', str(g.finished['normal']))
        g = PropGraph()
        prop_trim.build_prop_trim(g, record('M_PropTrim_CR+NOM+AO', trim, {'Use Perma Snow': False}))
        self.assertNotIn('/Game/T/T_Snow_M', g.sampled)
        self.assertIn('off', g.report.get('skipped_textures', {}).get('Snow Base Color', ''))
        g = FakeGraph()
        surfaces.build_surface(g, record('M_PropPreset_CR+NOX+XEM', {'CR': '/Game/T/T_P_CR', **snow}, {'Use Snow': True}, {}))
        self.assertIn('/Game/T/T_Snow_BC', g.sampled)
        self.assertTrue(any('snow layer' in s for s in g.report['implemented_stages']))

    def test_prop_trim_use_dust_dirt_normal(self):
        prop_trim = load_surfaces('prop_trim')

        class GeoGraph(PropGraph):
            def node(self, *a, **k): return types.SimpleNamespace(outputs={'Normal': self._tok('geo_normal')})

        tex = {'CR Texture': '/Game/T/T_M_CR', 'NXX/NMX Texture': '/Game/T/T_M_NXX', 'Dirt Normal': '/Game/T/T_Dust_N'}
        g = GeoGraph()
        prop_trim.build_prop_trim(g, record('M_Tileable_Metal_CR+NXX', tex, {'Use Dust': True}, {'Top Down Dust Bias': .3}))
        self.assertIn('/Game/T/T_Dust_N', g.sampled)
        self.assertIn('T_Dust_N', str(g.finished['normal']))
        self.assertIn('top-down dust normal (approximate)', g.report['implemented_stages'])
        g = PropGraph()
        prop_trim.build_prop_trim(g, record('M_Tileable_Metal_CR+NXX', tex, {'Use Dust': False}))
        self.assertNotIn('/Game/T/T_Dust_N', g.sampled)
        self.assertIn('off', g.report.get('skipped_textures', {}).get('Dirt Normal', ''))


if __name__ == '__main__':
    unittest.main()
