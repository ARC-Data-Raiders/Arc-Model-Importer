"""R13 item 5: seed a fault into one builder stage and require the audit to flag every material of that family.

The Interior_Floors family is built by rebuild/surfaces.build_surface on a fake graph; graph facts
(images, links, output) are derived from what the builder sampled and wired into finish()."""
import unittest
from unittest import mock

from .test_material_audit import G, IMG, MI, audit
from .test_surface_layer_stages import FakeGraph, load_surfaces, record

MASTER = 'M_ArchitecturePreset_Interior_Floors'
FAMILY = [{'CR': f'/Game/T/T_Floor{i}_CR', 'NOH': f'/Game/T/T_Floor{i}_NOH'} for i in range(4)]
MESH = {'uv_layers': ['UV0'], 'color_attributes': ['Col']}


def audited(textures):
    """Build one family MI on a fake graph and audit graph facts derived from the build."""
    surfaces = load_surfaces()
    g = FakeGraph()
    r = record(MASTER, textures)
    surfaces.build_surface(g, r)
    wired = str(g.finished)
    images = [IMG(ref.rsplit('/', 1)[-1], linked=ref in wired) for ref in dict.fromkeys(g.sampled)]
    graph = G(images=images, output_linked=g.finished is not None)
    mi = MI(package='/Game/MI_Floor', master=r['master'], textures=r['textures'], switches=r['switches'])
    return audit.audit_material_facts(graph, mi, g.report, MESH, None, ())['flags']


def unlinked(f):
    return sorted(k for k in f if k.startswith('tex_param_unlinked:'))


def constant_stage(g, r, surf, *a, **k):
    """Seeded fault: a weathering stage that replaces every surface channel with a constant."""
    return {**surf, 'color': (.5, .5, .5, 1), 'rough': .5, 'metal': 0, 'normal': None}


class SeededBuilderFault(unittest.TestCase):
    def test_healthy_family_has_no_unlinked_textures(self):
        for tex in FAMILY:
            with self.subTest(tex=tex['CR']):
                self.assertEqual(unlinked(audited(tex)), [])

    def test_broken_stage_flags_every_family_material(self):
        surfaces = load_surfaces()
        with mock.patch.object(surfaces, 'weathering_layers', constant_stage):
            for tex in FAMILY:
                with self.subTest(tex=tex['CR']):
                    self.assertTrue(unlinked(audited(tex)), 'audit missed the seeded fault')


if __name__ == '__main__':
    unittest.main()
