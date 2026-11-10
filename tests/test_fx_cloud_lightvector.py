"""M_FX_Cloud_LightVector graph equals the game's SM5 pixel shader on mirrored imports (no Blender).

The reference below is transcribed from MI_Cloud_Frozenday_01's translucent base-pass PS
(shader map A5FCE6E7, build 26159202) and evaluated in Unreal's frame; the builder runs
on the same card after the importer's Y mirror and V flip.
"""
import ast
import importlib.util
import math
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_fx_cloud', ROOT / 'Shared/materials/rebuild/clouds.py')
clouds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clouds)

FROZENDAY_01 = {
    'scalars': {'Ambient Scatter Curve': 1.413062, 'Ambient Scatter Offset': 0.0, 'Ambient Scatter Intensity Start': .283456,
                'Directional Scatter Intensity': 9.276782, 'Directional Scatter Curve': 3.161641,
                'Directional Scatter Offset': -.24762, 'Alpha Curve': 19.33533, 'ScatterBaseColor': 16.368952,
                'EmissiveMultiplier': .58831},
    'colors': {'BaseColor': (.380208, .380208, .380208, 0.), 'Ambient Scatter': (.775, .879049, 1., 1.),
               'Directional Scatter': (1., .833333, .75, 1.), 'Light World Position': (-1910704.1, 0., 7519190.5, 1.)},
}


def dot(a, b): return sum(x * y for x, y in zip(a, b))
def cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def scale(a, k): return tuple(x * k for x in a)
def sub(a, b): return tuple(x - y for x, y in zip(a, b))
def norm(a): return scale(a, 1 / math.sqrt(dot(a, a)))
def mirror(a): return (a[0], -a[1], a[2])


def shader_reference(T, N, sign, P, mdc1, mdc2, fc, scalars, colors):
    """Instructions 0-129 of the PS in UE's frame (cm): emissive RGB and opacity before depth fade."""
    B = scale(cross(N, T), sign)
    to_light = sub(colors['Light World Position'][:3], P)
    L = norm((dot(T, to_light), dot(B, to_light), dot(N, to_light)))
    L = (L[0], -L[1], L[2])
    k = 0.57735026
    corners = [(-k, -k, -k), (-k, -k, k), (-k, k, -k), (-k, k, k), (k, -k, -k), (k, -k, k), (k, k, -k), (k, k, k)]
    weights = [dot(L, c) * .75 + .25 for c in corners]
    m = min(max(sum(w * v for w, v in zip(weights, mdc1 + mdc2)) / sum(weights), 0), 1)
    directional = m ** scalars['Directional Scatter Curve'] if m > 2.98e-8 else 0.
    directional = [directional * c * scalars['Directional Scatter Intensity'] + scalars['Directional Scatter Offset']
                   for c in colors['Directional Scatter'][:3]]
    ambient = []
    for value, tint in zip(fc[:3], colors['Ambient Scatter'][:3]):
        x = value * scalars['ScatterBaseColor']
        x = x ** scalars['Ambient Scatter Curve'] if x > 2.98e-8 else 0.
        ambient.append(x * tint * scalars['Ambient Scatter Intensity Start'] + scalars['Ambient Scatter Offset'])
    emissive = [max((d + a) * scalars['EmissiveMultiplier'], 0.) for d, a in zip(directional, ambient)]
    alpha = min(fc[3] ** scalars['Alpha Curve'] if fc[3] > 2.98e-8 else 0., 1.)
    return emissive, alpha


class Outputs:
    def __init__(self, **values): self.outputs = values


class NumericGraph:
    """Eager stand-in for materials.native.interop.Graph: sockets are floats and 3-tuples."""
    def __init__(self, obj, images, position, normal, backfacing, tangent):
        self.report = {'warnings': []}
        self.obj, self.images = obj, images
        self.geometry = Outputs(Position=position, Normal=normal, Backfacing=backfacing)
        self.tangent, self.result = tangent, None

    @contextmanager
    def block(self, label): yield self
    def parameter(self, label, value, typ='Float'): return tuple(value) if isinstance(value, (tuple, list)) else float(value)
    def uv(self, channel=0): return 'UV%d' % channel
    def image(self, ref, uv=None, layer=None, srgb=None, label='', required=True): return self.images.get(ref)
    def node(self, typ, label='', **attrs):
        if typ == 'ShaderNodeNewGeometry': return self.geometry
        if typ == 'ShaderNodeTangent': return Outputs(Tangent=self.tangent)
        raise AssertionError('unexpected node ' + typ)
    def math(self, op, a, b=0, c=0, label=''):
        return {'ADD': lambda: a + b, 'SUBTRACT': lambda: a - b, 'MULTIPLY': lambda: a * b, 'DIVIDE': lambda: a / b,
                'MINIMUM': lambda: min(a, b), 'MAXIMUM': lambda: max(a, b),
                'POWER': lambda: a ** b if a > 0 else 0.}[op]()
    def add(self, a, b): return self.math('ADD', a, b)
    def sub(self, a, b): return self.math('SUBTRACT', a, b)
    def mul(self, a, b): return self.math('MULTIPLY', a, b)
    def clamp(self, a): return min(max(a, 0), 1)
    def combine(self, x, y, z=0): return (x, y, z)
    def cmul(self, a, b): return tuple(x * y for x, y in zip(a[:3], b[:3]))
    def vector(self, op, a, b=(0, 0, 0), scale=1):
        a, b = tuple(a[:3]), tuple(b[:3])
        return {'ADD': lambda: tuple(x + y for x, y in zip(a, b)), 'SUBTRACT': lambda: sub(a, b),
                'SCALE': lambda: tuple(x * scale for x in a), 'NORMALIZE': lambda: norm(a),
                'DOT_PRODUCT': lambda: dot(a, b), 'CROSS_PRODUCT': lambda: cross(a, b),
                'MAXIMUM': lambda: tuple(map(max, a, b)), 'MINIMUM': lambda: tuple(map(min, a, b))}[op]()
    def finish(self, color, roughness=.5, metallic=0, normal=None, specular=.5, emission=None, alpha=1, **_):
        self.result = {'color': color, 'roughness': roughness, 'specular': specular, 'emission': emission, 'alpha': alpha}


class CardMesh:
    def __init__(self, sign):
        self.uv_layers = [SimpleNamespace(name='UV0')]
        self.loops, self.sign = [], sign
    def calc_tangents(self, uvmap):
        assert uvmap == 'UV0'
        self.loops = [SimpleNamespace(bitangent_sign=self.sign)] * 3
    def free_tangents(self): self.loops = []


def card_frames(normal_ue):
    """A vertical UE card: U along +X, UE V (texture down) along -Z. Returns UE and Blender frames."""
    T_ue, B_ue = (1., 0., 0.), (0., 0., -1.)
    sign_ue = 1. if dot(cross(normal_ue, T_ue), B_ue) > 0 else -1.
    # Import: positions (x, -y, z), V -> 1 - V, winding kept so normals are mirrored too.
    T_b, B_b, N_b = mirror(T_ue), scale(mirror(B_ue), -1), mirror(normal_ue)
    sign_b = 1. if dot(cross(N_b, T_b), B_b) > 0 else -1.
    return (T_ue, normal_ue, sign_ue), (T_b, N_b, sign_b)


class CloudShaderTests(unittest.TestCase):
    def run_case(self, normal_ue, P_ue, mdc1, mdc2, fc, backfacing=0.):
        (T_ue, N_ue, s_ue), (T_b, N_b, s_b) = card_frames(normal_ue)
        expected, expected_alpha = shader_reference(T_ue, N_ue, s_ue, P_ue, mdc1, mdc2, fc, FROZENDAY_01['scalars'], FROZENDAY_01['colors'])
        def sample(values): return {**dict(zip('rgba', values)), 'node': SimpleNamespace(image=SimpleNamespace(alpha_mode='STRAIGHT'))}
        images = {'/MDC1': sample(mdc1), '/MDC2': sample(mdc2), '/FC': sample(fc)}
        shown_normal = scale(N_b, -1) if backfacing else N_b   # Blender flips N on backfaces
        g = NumericGraph(SimpleNamespace(data=CardMesh(s_b)), images, scale(mirror(P_ue), .01), shown_normal, backfacing, T_b)
        record = {**FROZENDAY_01, 'switches': {'Depth Blend': True},
                  'textures': {'MDC1 Texture': '/MDC1', 'MDC2 Texture': '/MDC2', 'Final Color Texture': '/FC',
                               'Depth Texture': '/Depth', 'PM_Diffuse': '/Depth'}}
        clouds.build_fx_cloud(g, record)
        for got, want in zip(g.result['emission'], expected):
            self.assertAlmostEqual(got, want, places=5)
        self.assertAlmostEqual(g.result['alpha'], expected_alpha, places=6)
        self.assertEqual(g.result['specular'], 0)
        self.assertTrue(all(images[k]['node'].image.alpha_mode == 'CHANNEL_PACKED' for k in images))
        return g

    def test_sunlit_and_shadowed_texels_match_the_game_shader(self):
        mdc1, mdc2 = (.05, .3, .6, .9), (.1, .4, .2, .8)
        for normal in ((0., 1., 0.), (0., -1., 0.)):
            for P in ((0., 0., 0.), (250000., -120000., 40000.)):
                with self.subTest(normal=normal, position=P):
                    self.run_case(normal, P, mdc1, mdc2, (.4, .42, .45, .97))
        self.run_case((0., 1., 0.), (0., 0., 0.), (0., 0., 0., 0.), (0., 0., 0., 0.), (.02, .02, .02, .5))

    def test_backfaces_use_the_unflipped_vertex_basis(self):
        args = ((0., 1., 0.), (1000., 2000., 0.), (.2, .7, .1, .5), (.9, .3, .6, .4), (.3, .3, .3, .99))
        front = self.run_case(*args).result['emission']
        back = self.run_case(*args, backfacing=1.).result['emission']
        for a, b in zip(front, back):
            self.assertAlmostEqual(a, b, places=6)

    def test_depth_texture_is_never_colour(self):
        g = self.run_case((0., 1., 0.), (0., 0., 0.), (.1,) * 4, (.1,) * 4, (.5, .5, .5, .9))
        self.assertEqual(g.result['color'], (.380208, .380208, .380208))

    def test_master_routes_to_the_cloud_family(self):
        tree = ast.parse((ROOT / 'Shared/materials/rebuild/records.py').read_text(encoding='utf-8'))
        body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'classify']
        package = types.ModuleType('_cloud_pkg'); package.__path__ = []
        sys.modules['_cloud_pkg'] = package
        sys.modules['_cloud_pkg.head_records'] = SimpleNamespace(family=lambda record: None)
        try:
            ns = {'__name__': '_cloud_pkg.records', '__package__': '_cloud_pkg'}
            exec(compile(ast.Module(body=body, type_ignores=[]), '<records>', 'exec'), ns)
            record = {'master': '/Game/Pioneer/Lighting/Materials/Clouds/M_FX_Cloud_LightVector', 'colors': {},
                      'textures': {'Depth Texture': 'x', 'Final Color Texture': 'y', 'PM_Diffuse': 'x'}}
            self.assertEqual(ns['classify'](record), 'fx_cloud')
        finally:
            sys.modules.pop('_cloud_pkg.head_records', None); sys.modules.pop('_cloud_pkg', None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
