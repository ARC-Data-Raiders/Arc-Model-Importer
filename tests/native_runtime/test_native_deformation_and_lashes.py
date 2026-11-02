try:
    from .native_test_support import ROOT
except ImportError:
    from native_test_support import ROOT
import unittest,math
from material_runtime.core import MaterialRecord,evaluate,literal
from material_runtime.hair import compile_eyelashes
from material_runtime.deformation import deform_vertices,decode_delta_texel,transform_normal,deform_corner_normals
def sample(inputs=None,uv=(.5,.5,0.),time=0.):
    data=inputs or {}
    return {'uv':uv,'time':time,'texture':lambda reference,coords,attrs:data[reference]}
class TestCharFXMath(unittest.TestCase):

    def test_normal_deformation_unit_length_endpoints_and_scale(self):
        p, n = deform_vertices([(1, 2, 3)], [(0, 0, 1)], [(2, -2, 4)], [(0, 1, -1)], 0.5, position_scale=0.01, evidence='fixture decoded delta bounds')
        self.assertEqual(p, ((1.01, 1.99, 3.02),))
        self.assertAlmostEqual(sum((x * x for x in n[0])), 1.0)
        self.assertAlmostEqual(n[0][1], math.sqrt(0.5))
        self.assertEqual(deform_vertices([(1, 2, 3)], [(0, 0, 1)], [(2, -2, 4)], [(0, 1, -1)], 0, evidence='fixture')[0], ((1.0, 2.0, 3.0),))

    def test_inverse_transpose_normal_transport_and_corner_mapping(self):
        n = transform_normal((1.0, 1.0, 0.0), ((2.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.5)))
        self.assertAlmostEqual(n[0], 1 / math.sqrt(5))
        self.assertAlmostEqual(n[1], 2 / math.sqrt(5))
        corners = deform_corner_normals([(0, 0, 1), (0, 1, 0)], [(0, 0, 0), (0, 0, 0)], [1, 0, 1], 0.5, evidence='fixture')
        self.assertEqual(corners, ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)))
        with self.assertRaises(ValueError):
            transform_normal((0, 0, 1), ((0, 0, 0), (0, 1, 0), (0, 0, 1)))

    def test_delta_validation_is_atomic(self):
        p = [(1, 2, 3)]
        with self.assertRaises(ValueError):
            deform_vertices(p, [(0, 0, 1)], [(0, 0, 0)], [(0, 0, -1)], 1, evidence='fixture')
        self.assertEqual(p, [(1, 2, 3)])
        with self.assertRaises(ValueError):
            deform_vertices(p, [(0, 0, 1)], [], [], 1, evidence='fixture')
        with self.assertRaises(ValueError):
            decode_delta_texel((0, 0, 0), minimum=(0, 0, 0), extent=(1, 1, 1), encoding='unorm')

    def test_signed_decode_preserves_axis_extents(self):
        self.assertEqual(decode_delta_texel((0, 0.5, 1), minimum=(1, 2, 3), extent=(4, 5, 6), encoding='snorm', evidence='fixture'), (-3.0, 2.0, 9.0))

    def test_eyelash_authored_controls_and_zero(self):
        r = MaterialRecord('/Game/lash', scalars={'OpacityPower': 2.0, 'Lash Opacity': 0.8, 'Visibility': 0.5, 'Specular': 0}, textures={'Eyelash Texture': '/Game/mask'})
        plan = compile_eyelashes(r)
        self.assertAlmostEqual(evaluate(plan.outputs['alpha'], sample({'/Game/mask': (0.5, 0.1, 0.9, 1.0)})), 0.1)
        self.assertEqual(evaluate(plan.outputs['specular']), 0.0)
        colored = MaterialRecord('/Game/lash', textures={'Eyelash Texture': '/Game/mask'}, colors={'Eyelash Color': (1, 0, 0, 1), 'Eyelash Color 2': (0, 0, 1, 1)}, scalars={'OpacityPower': 0.0})
        cp = compile_eyelashes(colored)
        self.assertEqual(evaluate(cp.outputs['base_color'], sample({'/Game/mask': (0.25, 0, 0, 1)})), (0.75, 0.0, 0.25))
        self.assertEqual(evaluate(cp.outputs['alpha'], sample({'/Game/mask': (0, 0, 0, 1)})), 0.0)
