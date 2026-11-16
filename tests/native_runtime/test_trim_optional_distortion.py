import unittest

from .fake_graph import FakeGraph, rebuild


class RecordingGraph(FakeGraph):
    def __init__(self):
        super().__init__()
        self.optional_requests = []

    def image(self, ref, *args, **kwargs):
        if not kwargs.get('required', True):
            self.optional_requests.append((kwargs.get('label'), ref))
        return super().image(ref, *args, **kwargs)


class TrimOptionalDistortionTests(unittest.TestCase):
    def build(self, distortion):
        graph = RecordingGraph()
        record = {'master': '/Game/M_PropTrimPreset_CR+NOM+AO', 'name': 'MI_Trim',
                  'scalars': {}, 'colors': {}, 'switches': {'UseOverlay': True},
                  'textures': {'CR Texture': '/Game/T_CR', 'NXX/NMX Texture': '/Game/T_NXX',
                               'Overlay': '/Game/T_Overlay'}}
        if distortion is not False:
            record['textures']['OverlayDistortion'] = distortion
        rebuild('prop_trim').build_prop_trim(graph, record)
        return graph

    def test_absent_optional_distortion_never_requests_an_empty_reference(self):
        for value in (False, None, ''):
            with self.subTest(value=value):
                graph = self.build(value)
                self.assertNotIn('Overlay distortion', [label for label, _ in graph.optional_requests])
                self.assertIn('Trim overlay', graph.images)

    def test_authored_distortion_is_still_sampled(self):
        graph = self.build('/Game/T_Distortion')
        self.assertIn(('Overlay distortion', '/Game/T_Distortion'), graph.optional_requests)
        self.assertIn('Overlay distortion', graph.images)


if __name__ == '__main__':
    unittest.main()
