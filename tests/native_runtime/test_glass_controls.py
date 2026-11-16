"""Numeric checks for authored opacity, dirt and pulse boundaries."""
import math
import types
import unittest
from .fake_graph import FakeGraph, rebuild


class NumericGraph(FakeGraph):
    def __init__(self, *, back=False, mask=None, seconds=0.):
        super().__init__()
        self.back = back
        self.mask = mask
        self.seconds = seconds

    def parameter(self, label, value, typ='Float'):
        self.params[label] = value
        return value

    def time(self, label='Time (seconds)'):
        return self.seconds

    def uv(self, channel=0):
        return (0., 0., 0.)

    def node(self, kind, label='', **attrs):
        if kind != 'ShaderNodeNewGeometry':
            raise AssertionError(kind)
        return types.SimpleNamespace(outputs={'Backfacing': float(self.back)})

    def image(self, reference, *args, **kwargs):
        return self.mask if reference == 'mask' else None

    def clamp(self, value):
        return min(max(value, 0.), 1.)

    def mix(self, left, right, factor, **kwargs):
        return left * (1. - factor) + right * factor

    def math(self, operation, left, right=0, *args, **kwargs):
        return {'ADD': lambda: left + right, 'SUBTRACT': lambda: left - right,
                'MULTIPLY': lambda: left * right, 'DIVIDE': lambda: left / right,
                'MAXIMUM': lambda: max(left, right), 'SINE': lambda: math.sin(left)}[operation]()


def record(**scalars):
    return {'scalars': scalars, 'colors': {}, 'textures': {}, 'switches': {}, 'settings': {}}


class GlassControls(unittest.TestCase):
    def test_zero_opacity_and_roughness_are_not_truthiness_defaults(self):
        for function, scalars in (('build_window', {'OpacityOutside': 0., 'Roughness': 0., 'Specular': 0.}),
                                  ('build_translucent_glass', {'BaseOpacity': 0., 'DirtOpacity': 0., 'Roughness': 0., 'Specular': 0.})):
            graph = NumericGraph()
            getattr(rebuild('decals'), function)(graph, record(**scalars))
            self.assertEqual(graph.out['alpha'], 0.)
            self.assertEqual(graph.out['roughness'], 0.)
            self.assertEqual(graph.out['specular'], 0.)

    def test_window_does_not_impose_minimum_opacity(self):
        graph = NumericGraph()
        rebuild('decals').build_window(graph, record(OpacityOutside=.01))
        self.assertEqual(graph.out['alpha'], .01)

    def test_front_and_back_opacity_differ(self):
        for back, expected in ((False, .2), (True, .7)):
            graph = NumericGraph(back=back)
            rebuild('decals').build_window(graph, record(OpacityOutside=.2, OpacityInside=.7))
            self.assertAlmostEqual(graph.out['alpha'], expected)

    def test_authored_zero_dirt_does_not_roughen_clean_glass(self):
        graph = NumericGraph(mask={'r': 1.})
        data = record(Roughness=.1, DirtStrengthOutside=0.)
        data['textures']['MaskTexture'] = 'mask'
        rebuild('decals').build_window(graph, data)
        self.assertAlmostEqual(graph.out['roughness'], .1)

    def test_dirt_opacity_and_transmission_are_bounded(self):
        graph = NumericGraph(mask={'r': 2.})
        data = record(BaseOpacity=.7, DirtOpacity=.8, Transmission=2., IOR=.2)
        data['textures']['MaskTexture'] = 'mask'
        rebuild('decals').build_translucent_glass(graph, data)
        self.assertEqual(graph.out['alpha'], 1.)
        self.assertEqual(graph.out['response'], {'Transmission Weight': 1., 'IOR': 1.})


class PulseControls(unittest.TestCase):
    def test_zero_amount_is_static_and_zero_intensity_stays_off(self):
        pulse = rebuild('light_controls').pulse_intensity
        for seconds in (0., .25, .75, 10.):
            graph = NumericGraph(seconds=seconds)
            self.assertEqual(pulse(graph, 20., record(PulseAmount=0.)), 20.)
            self.assertEqual(pulse(graph, 0., record(PulseAmount=1.)), 0.)

    def test_pulse_cycles_and_disabled_negative_amount(self):
        pulse = rebuild('light_controls').pulse_intensity
        for seconds, expected in ((.25, 20.), (.75, 0.), (1.25, 20.)):
            self.assertAlmostEqual(pulse(NumericGraph(seconds=seconds), 20., record(PulseAmount=1., PulseSpeed=1.)), expected)
        self.assertEqual(pulse(NumericGraph(), 20., record(PulseAmount=-1.)), 20.)
