"""prop-audit R7: fallback roots with >=5 slots route by root base name; named textures feed the CA builder."""
try:
    from .fake_graph import FakeGraph, rebuild
    from .test_generic_families import rec
except ImportError:
    from fake_graph import FakeGraph, rebuild
    from test_generic_families import rec
import unittest

ENV = '/Game/Pioneer/Env/'
ROUTES = {
    'masked_na': [rec(ENV + 'BaseFlattenMaterial', ['DiffuseTexture', 'NormalTexture']),
                  rec(ENV + 'M_Res_Door_L_01', ['CM', 'NRH']), rec(ENV + 'M_RevealPostits', ['PostItTex', 'NormalMap']),
                  rec(ENV + 'M_MeshTerrain', ['Layer00_Color']), rec(ENV + 'M_MeshTerrain_02', ['Layer01_Color']),
                  rec(ENV + 'M_SimpleDecalFrontEnd', ['Texture']),
                  rec(ENV + 'M_TBG_BackdropMaterialPreset_01', ['Base Color', 'Normal Map']),
                  rec(ENV + 'M_Decal', ['Decal texture']), rec(ENV + 'M_FX_Decal_Parent', ['MainDecalTexture']),
                  rec(ENV + 'M_Moss_01', ['Texture']), rec(ENV + 'M_GroundDebrisPreset', ['Base_Material_CR'])],
    'parameterless': [rec(ENV + b, ['Mask']) for b in ('M_MCP_FloorLines_01_A', 'M_CableGenerator',
                                                       'M_TarpConstruction_01', 'M_Visor_Tinted')],
    'fx_invisible': [rec(ENV + 'M_FanLightFunction', ['Light Texture']),
                     rec(ENV + 'M_FX_VolumeScorchMarkDecal', ['NoiseTexture'])],
    'lightbulb': [rec(ENV + 'M_MasterLight_Lightbulb_Swing_01', ['Lightbulb'])],
    'sand_dune': [rec(ENV + 'M_SandDune_Floor', ['Param', 'Param_1'])],
    'snow_pile_basic': [rec(ENV + 'M_SnowSkirt_Decal_01', ['NX'])],
}


class R7RootRoutes(unittest.TestCase):
    def test_routes(self):
        r = rebuild('records')
        for family, recs in ROUTES.items():
            for x in recs:
                with self.subTest(master=x['master']):
                    self.assertEqual(r.classify(x), family)

    def test_named_ca_textures(self):
        gf = rebuild('generic_families')
        for colour, normal in (('DiffuseTexture', 'NormalTexture'), ('Base Color', 'Normal Map'), ('CM', 'NRH')):
            tex = {colour: 'C', normal: 'N'}
            self.assertEqual(gf._pick(tex, *gf.CA_COLOR), 'C')
            self.assertEqual(gf._pick(tex, *gf.CA_NORMAL), 'N')


if __name__ == '__main__':
    unittest.main()
