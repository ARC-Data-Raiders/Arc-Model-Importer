"""Generic legacy families: routing by texture contract or master base, and approximate builder output."""
try:
    from .fake_graph import FakeGraph, rebuild
except ImportError:
    from fake_graph import FakeGraph, rebuild
import unittest

LIB = '/Game/Pioneer/MaterialLibrary/Masters/'
PRESETS = '/Game/Pioneer/MaterialLibrary/M_Presets/'


def rec(master, textures=(), scalars=None, colors=None, switches=None, settings=None):
    return {'master': master, 'name': 'MI_Test', 'textures': dict.fromkeys(textures, '/Game/T/X'),
            'scalars': scalars or {}, 'colors': colors or {}, 'switches': switches or {}, 'settings': settings or {}}


ROUTES = {
    'vt_blend': (rec('/Game/M_A', ['CR_1', 'NOH_1', 'CR_2', 'NOH_2']), rec(LIB + 'M_Tarmac_02', ['0. CR 1', '0. NOH 1']), rec(LIB + 'M_CR_NOH_Parallax_VTblend_01', ['Foo'])),
    'nao_decal': (rec('/Game/M_A', ['DecalTrimsheet']), rec(LIB + 'M_EnemyPreset_Masked_NAO+Detail', ['Foo'])),
    'canr_decal': (rec('/Game/M_A', ['ColorAlpa', 'NormalRoughness']), rec(LIB + 'M_Decal_CANR', ['Foo'])),
    'masked_na': (rec('/Game/M_A', ['BC', 'NA']), rec(LIB + 'M_PropPreset_Masked+NA+Detail', ['Foo'])),
    'detail_ncr_trim': (rec('/Game/M_A', ['Detail NCR', 'Overlay']), rec(LIB + 'M_Res_ArchitectureTrim_01', ['Foo'])),
    'parameterless': (rec(LIB + 'M_DebugGrid'), rec(LIB + 'M_SplineThicken')),
    'broken_glass': (rec('/Game/M_A', ['NC', 'NDD']), rec(PRESETS + 'M_BrokenGlassSDF'), rec(PRESETS + 'M_BrokenGlassSDF_NoRandom')),
    'concrete_trim': (rec('/Game/M_A', ['Trim sheet', 'CH']), rec(PRESETS + 'M_ConcreteTrim_UXR')),
    'rusty_trim': (rec('/Game/M_A', ['Base Texture', 'HolesNXX']), rec(PRESETS + 'M_TrimPreset_Rusty', ['Foo'])),
    'graphic_atlas': (rec('/Game/M_A', ['Graphic Atlas', 'NX']), rec(PRESETS + 'M_Graphic_Atlas_01')),
    'blockout': (rec(PRESETS + 'M_BlockoutPreset'), rec(PRESETS + 'M_BlockoutPreset_Glow', ['Foo'])),
    'snow_pile_basic': (rec('/Game/M_A', ['BaseTextrue']), rec(PRESETS + 'M_SnowPile_01')),
    'nr_vpo_detail': (rec(PRESETS + 'M_PropPreset_NR+VPO+Detail', ['Foo']), rec('/Game/M_A', ['Variation Mask', 'LargeScaleVariation'])),
    'engine_default': (rec('Engine/Content/EngineMaterials/WorldGridMaterial'), rec('/Engine/EngineMaterials/DefaultMaterial', ['Foo'])),
}
# Round 3 root-master routes (base names from the prop audit's unrouted list).
R3_ROUTES = {
    'masked_na': [rec('/Game/Env/M_' + b, ['CA', 'NN']) for b in ('South_Swamp_Tiling_01', 'CableSimplePlane_01', 'GroundDebrisPreset_CR+NOH+A',
                                                                  'Wires_01_Lod', 'Rrs_Win_01', 'TBG_Grass_01', 'Rsr_Ceiling_01_Pads_A')]
                 + [rec('/Game/Env/MM_Cables_Raider_flags', ['CXA'])],
    'vt_blend': (rec('/Game/Env/M_FZT_Nature_01', ['0. CS', '0. NOH']), rec('/Game/Env/M_ScalingPipePreset', ['Base CR', 'Base NOH'])),
    'parameterless': (rec('/Game/Env/M_TarmacPatches_01', ['Mask']), rec('/Game/Env/M_Water_01', ['Normal'])),
    'graphic_atlas': (rec('/Game/Env/M_Hme_MonitorImageSeq_01', ['Flipbook']),),
    'decal': (rec('/Game/Env/M_RaiderMarks_01_A', ['Raider Mark Texture']), rec('/Game/Env/M_DeferedDecal_X_01', ['Decal Mask']),
              rec('/Game/Env/M_MeshDecal_ColorSimple_Deffered_01', ['CA Texture'])),
}


class GenericRouting(unittest.TestCase):
    def setUp(self): self.r = rebuild('records')

    def test_routes_by_texture_and_by_master(self):
        for family, recs in ROUTES.items():
            for r in recs:
                with self.subTest(family=family, master=r['master']):
                    self.assertEqual(self.r.classify(r), family)

    def test_engine_default_record_only_for_engine_masters(self):
        r = self.r.engine_default_record('/Engine/EngineMaterials/WorldGridMaterial.WorldGridMaterial')
        self.assertEqual(r['master'], 'Engine/Content/EngineMaterials/WorldGridMaterial')
        self.assertEqual(self.r.classify(r), 'engine_default')
        self.assertIsNone(self.r.engine_default_record('/Game/Props/M_WorldGridMaterial'))

    def test_round3_root_masters_route(self):
        for family, recs in R3_ROUTES.items():
            for r in recs:
                with self.subTest(family=family, master=r['master']):
                    self.assertEqual(self.r.classify(r), family)

    def test_unrelated_textured_master_stays_fallback(self):
        self.assertEqual(self.r.classify(rec('/Game/M_Unknown', ['Weird Param'])), 'fallback')


def build(fn, r):
    g = FakeGraph()
    getattr(rebuild('generic_families'), fn)(g, r)
    return g


class GenericBuilders(unittest.TestCase):
    def approx(self, g):
        self.assertTrue(any(w.startswith('approximate:') for w in g.report['warnings']))
        self.assertTrue(g.report['implemented_stages'])

    def test_vt_blend_blends_two_layers(self):
        g = build('build_vt_blend', rec('/Game/M', ['CR_1', 'NOH_1', 'CR_2', 'NOH_2', 'Breakup Mask'], {'Tiling': 4},
                                        {'Tint Color': (1, 0, 0, 1)}, {'Use Breakup Texture': True, 'Tint': True}))
        self.approx(g)
        self.assertEqual(set(g.images), {'CR_1', 'NOH_1', 'CR_2', 'NOH_2', 'Breakup Mask'})
        self.assertIn('T[CR_2]', g.out['color']); self.assertIn('Tint Color', g.out['color']); self.assertIn('T[Breakup Mask]', g.out['color'])

    def test_vt_blend_absent_mask_uses_vertex_color_without_missing_image_request(self):
        graph, requested = FakeGraph(), []
        original = graph.image
        def image(reference, *args, **kwargs):
            requested.append((reference, kwargs.get('label')))
            return original(reference, *args, **kwargs)
        graph.image = image
        record = rec('/Game/M', ['CR_1', 'NOH_1', 'CR_2', 'NOH_2'])
        rebuild('generic_families').build_vt_blend(graph, record)
        self.assertNotIn((None, '4. Mask'), requested)
        reference = build('build_vt_blend', {**record, 'switches': {'VCMask': True}})
        self.assertEqual(graph.out, reference.out)

    def test_vt_blend_missing_core_and_authored_mask_requests_are_preserved(self):
        graph, requested = FakeGraph(), []
        original = graph.image
        def image(reference, *args, **kwargs):
            requested.append((reference, kwargs.get('label')))
            return original(reference, *args, **kwargs)
        graph.image = image
        record = rec('/Game/M', ['NOH_1', 'CR_2', 'NOH_2', '4. Mask'])
        rebuild('generic_families').build_vt_blend(graph, record)
        self.assertIn((None, 'CR_1'), requested)
        self.assertIn(('/Game/T/X', '4. Mask'), requested)

    def test_nao_decal_is_masked_with_zones(self):
        g = build('build_nao_decal', rec('/Game/M', ['DecalTrimsheet'], {'V1_Roughness': .2}, {'V2_Color': (1, 1, 0, 1)},
                                         settings={'OpacityMaskClipValue': .5}))
        self.approx(g)
        self.assertIn('GREATER_THAN(T[DecalTrimsheet].b,0.5)', g.out['alpha'])
        self.assertIn('P[V2_Color]', g.out['color']); self.assertIn('P[V1_Roughness]', g.out['roughness'])

    def test_canr_decal_alpha_from_color_alpha(self):
        g = build('build_canr_decal', rec('/Game/M', ['ColorAlpa', 'NormalRoughness'], {'Opacity': .8}))
        self.approx(g)
        self.assertIn('T[ColorAlpha].a', g.out['alpha']); self.assertEqual(g.out['roughness'], 'T[NormalRoughness].b')

    def test_canr_decal_reads_authored_nrh_normal_without_guessing_roughness(self):
        graph = build('build_canr_decal', rec(PRESETS + 'M_Decals_Masked_Contact',
                                            ['ColorAlpha', 'NormalRoughnessHeight']))
        self.approx(graph)
        self.assertIn('T[NormalRoughnessHeight].r', graph.out['normal'])
        self.assertIn('T[NormalRoughnessHeight].g', graph.out['normal'])
        self.assertEqual(graph.out['roughness'], .5)
        self.assertIn('T[ColorAlpha].a', graph.out['alpha'])

    def test_canr_decal_keeps_existing_normal_roughness_precedence(self):
        graph = build('build_canr_decal', rec('/Game/M',
                                            ['ColorAlpha', 'NormalRoughness', 'NormalRoughnessHeight']))
        self.assertEqual(graph.images, ['ColorAlpha', 'NormalRoughness'])
        self.assertEqual(graph.out['roughness'], 'T[NormalRoughness].b')

    def test_masked_na_clips_na_alpha(self):
        g = build('build_masked_na', rec('/Game/M', ['BC', 'NA'], {'Roughness': .7}))
        self.approx(g)
        self.assertIn('T[NA].a', g.out['alpha']); self.assertEqual(g.params['Roughness'], .7)

    def test_detail_ncr_trim_uses_ncr_roughness(self):
        g = build('build_detail_ncr_trim', rec('/Game/M', ['Detail NCR', 'Overlay'], colors={'Color Tint': (.3, .3, .3, 1)}))
        self.approx(g)
        self.assertIn('T[Detail NCR].a', g.out['roughness']); self.assertIn('T[Overlay]', g.out['color'])

    def test_parameterless_constant_surface(self):
        g = build('build_parameterless', rec('/Game/M', [], {'Roughness': .3, 'Opacity': .4}, {'Base Color': (.2, .4, .6, 1)},
                                             settings={'BlendMode': 'BLEND_Translucent'}))
        self.assertTrue(any(w.startswith('no_texture_master') for w in g.report['warnings']))
        self.assertEqual(g.out['color'], 'P[Base Color]'); self.assertEqual(g.out['roughness'], 'P[Roughness]')
        self.assertEqual(g.out['alpha'], 'P[Opacity]'); self.assertEqual(g.images, [])

    def test_vt_blend_tarmac_mask_and_tint(self):
        g = build('build_vt_blend', rec('/Game/M', ['0. CR 1', '0. NOH 1', '0. CR 2', '0. NOH 2', '4. Mask'], {'Tiling 2': 3, 'Roughness Range High': .8},
                                        {'1. Tint': (1, 0, 0, 1)}, {'Use Tint': True}))
        self.approx(g)
        self.assertEqual(set(g.images), {'CR_1', 'NOH_1', 'CR_2', 'NOH_2', '4. Mask'})
        self.assertIn('T[4. Mask]', g.out['color']); self.assertIn('1. Tint', g.out['color'])
        g = build('build_vt_blend', rec('/Game/M', ['0. CR 1', '0. CR 2'], switches={'Use Second Texture Set': False}))
        self.assertNotIn('T[CR_2]', str(g.out['color']))

    def test_rusty_trim_rust_and_holes(self):
        g = build('build_rusty_trim', rec('/Game/M', ['Base Texture', 'Base Normal', 'Overlay', 'HolesNXX'], {'Rust Range 1 Low': .2},
                                          {'TintColor': (.5, .5, 1, 1), 'Rust Tint': (.5, .2, .1, 1)},
                                          {'UseOpacityMask': True, 'Is Metallic': True}, {'BlendMode': 'BLEND_Masked'}))
        self.approx(g)
        self.assertIn('T[Overlay]', g.out['color']); self.assertIn('TintColor', g.out['color'])
        self.assertIn('T[HolesNXX].b', g.out['alpha']); self.assertIsNotNone(g.out['normal'])

    def test_rusty_trim_sign_usealpha_does_not_enable_holes(self):
        # Sign MIs set EnableSigns+UseAlpha (sign-layer param); UseOpacityMask stays master default False.
        g = build('build_rusty_trim', rec('/Game/M', ['Base Texture', 'HolesNXX'], switches={'EnableSigns': True, 'UseAlpha': True}))
        self.assertNotIn('HolesNXX', g.images); self.assertIsNone(g.out.get('alpha'))

    def test_rusty_trim_holes_nxx_normal(self):
        # MI_TrimPreset_Rusty_Yellow_01_A: UseOpacityMask+UseOpacityMaskNormal, HolesNXX RG normal.
        g = build('build_rusty_trim', rec('/Game/M', ['Base Texture', 'HolesNXX'], {'NormalStrengthHoles': 1.0},
                                          switches={'UseOpacityMask': True, 'UseOpacityMaskNormal': True}))
        self.assertIn('T[HolesNXX]', str(g.out['normal']))

    def test_graphic_atlas_cell_uv(self):
        g, uvs = FakeGraph(), []
        orig = g.transform_uv
        g.transform_uv = lambda ch=0, scale=1, offset=(0, 0), rotation=0: uvs.append((scale, offset)) or orig(ch, scale, offset, rotation)
        rebuild('generic_families').build_graphic_atlas(g, rec('/Game/M', ['Graphic Atlas', 'NX'], {'AtlasPosition': 5, 'Number of columns': 4, 'Number of rows': 2},
                                                             switches={'Use Opacity': True}, settings={'BlendMode': 'BLEND_Masked'}))
        self.approx(g)
        self.assertIn('T[Graphic Atlas]', g.out['color']); self.assertIn('T[Graphic Atlas].a', g.out['alpha'])
        self.assertEqual(uvs[0], ((.25, .5), (.25, .5)))

    def test_masked_ca_alpha_and_tint(self):
        g = build('build_masked_na', rec('/Game/M', ['CA', 'NN'], {'Roughness': .4}, {'Tint': (1, 0, 0, 1)}, settings={'BlendMode': 'BLEND_Masked'}))
        self.approx(g)
        self.assertIn('T[CA]', g.out['color']); self.assertIn('P[Tint]', g.out['color'])
        self.assertIn('T[CA].a', g.out['alpha']); self.assertIsNotNone(g.out['normal'])

    def test_color_only_masked_master_does_not_request_an_absent_normal(self):
        graph, requested = FakeGraph(), []
        original = graph.image
        def image(reference, *args, **kwargs):
            requested.append((reference, kwargs.get('label')))
            return original(reference, *args, **kwargs)
        graph.image = image
        rebuild('generic_families').build_masked_na(graph, rec('/Game/M_SimpleDecalFrontEnd', ['Texture']))
        self.assertEqual(requested, [('/Game/T/X', 'CA')])
        self.assertIn('T[CA]', graph.out['color'])
        self.assertIsNone(graph.out['normal'])

    def test_masked_master_still_requests_its_missing_color_and_authored_normal(self):
        graph, requested = FakeGraph(), []
        original = graph.image
        def image(reference, *args, **kwargs):
            requested.append((reference, kwargs.get('label')))
            return original(reference, *args, **kwargs)
        graph.image = image
        rebuild('generic_families').build_masked_na(graph, rec('/Game/M', ['NormalMap']))
        self.assertEqual(requested, [(None, 'CA'), ('/Game/T/X', 'Normal')])
        self.assertIsNotNone(graph.out['normal'])

    def test_vt_blend_base_cr_alias_single_layer(self):
        g = build('build_vt_blend', rec('/Game/M', ['Base CR', 'Base NOH'], colors={'1.  Material Color Tint': (0, 1, 0, 1)}))
        self.approx(g)
        self.assertEqual(set(g.images), {'CR_1', 'NOH_1'}); self.assertIn('Material Color Tint', g.out['color'])

    def test_graphic_atlas_flipbook_first_frame(self):
        g = build('build_graphic_atlas', rec('/Game/M', ['Flipbook'], {'Rows': 4, 'Emissive': 3}, {'Tint': (0, 1, 0, 1)}))
        self.approx(g)
        self.assertIn('T[Graphic Atlas]', g.out['color']); self.assertIn('P[Tint]', g.out['color']); self.assertIn('P[Emissive]', g.out['emission'])

    def test_parameterless_prefers_water_color(self):
        g = build('build_parameterless', rec('/Game/M', ['Normal'], colors={'Base Tint': (1, 1, 1, 1), 'Deep Water Color': (0, 0, 1, 1)}))
        self.approx(g); self.assertEqual(g.out['color'], 'P[Deep Water Color]')

    def test_round3_decal_masters(self):
        d = rebuild('decals')
        g = FakeGraph(); d.build_decal(g, rec('/Game/M_RaiderMarks_01_A', ['Raider Mark Texture'], colors={'Raider Mark Color': (1, 0, 0, 1)}))
        self.assertIn('T[Raider mark colour and alpha]', g.out['color']); self.assertIn('T[Raider mark colour and alpha].a', g.out['alpha'])
        g = FakeGraph(); d.build_decal(g, rec('/Game/M_MeshDecal_ColorSimple_Deffered_01', ['CA Texture'], {'Opacity': .5}))
        self.assertIn('T[Decal colour and alpha].a', g.out['alpha']); self.assertIn('P[Opacity]', g.out['alpha'])
        g = FakeGraph(); d.build_decal(g, rec('/Game/M_DeferedDecal_X_01', ['Decal Mask'], {'Global Opacity': .7}))
        self.assertIn('P[Global Opacity]', g.out['alpha'])
        self.assertTrue(any(w.startswith('approximate:') for w in g.report['warnings']))

    def test_blockout_authored_colour(self):
        g = build('build_blockout', rec('/Game/M', [], colors={'BaseColor': (.2, .4, .6, 1)}))
        self.approx(g); self.assertIn('P[BaseColor]', str(g.out['color'])); self.assertEqual(g.images, [])
        g = build('build_blockout', rec('/Game/M', ['DamageMask'], colors={'Color': (1, 0, 0, 1)}, switches={'EnableDamage': True}))
        self.assertIn('T[DamageMask]', g.out['color'])

    def test_snow_pile_basic_two_colours(self):
        g = build('build_snow_pile_basic', rec('/Game/M', ['BaseTextrue'], colors={'ColorA': (1, 1, 1, 1)}, switches={'Use SnowPatch Alpha': True}, settings={'BlendMode': 'BLEND_Masked'}))
        self.approx(g); self.assertIn('P[ColorA]', g.out['color']); self.assertIn('T[BaseTextrue]', g.out['alpha'])

    def test_snow_pile_decodes_nrh_normal_and_height_mask(self):
        g = build('build_snow_pile_basic', rec(PRESETS + 'M_SnowPile_01', ['BaseTextrue'], {'Tilling Base': 1.41, 'Normal Strength': 1, 'MaskLow': 0, 'Mask High': 1, 'BaseRougness': .92}))
        self.approx(g); self.assertIn('NORMAL(T[BaseTextrue].r,T[BaseTextrue].g)', str(g.out['normal']))
        self.assertNotIn('T[BaseTextrue].r', str(g.out['color'])); self.assertIn('T[BaseTextrue].a', str(g.out['color']))
        self.assertNotIn('alpha', g.out); self.assertEqual(g.out['roughness'], .92)

    def test_tarp_construction_binds_fixed_normals(self):
        g = build('build_parameterless', rec('/Game/Pioneer/Environment/Props/_Generic/TarpConstruction_01/M_TarpConstruction_01', ['Wind Folds N'],
                                             {'Tile U': 1, 'Base Normal Strenght': .5, 'Large Wind Tiling': .35, 'Detail Tiling': 8}, {'Color': (.38, .4, .44, 1)}))
        self.approx(g); n = str(g.out['normal'])
        for label in ('T_TarpConstruction_01_NXR', 'Wind Folds N', 'T_TarpDetail_01_NX'):
            self.assertIn('T[' + label + ']', n)
        self.assertIn('P[Color]', str(g.out['color']))

    def test_nr_vpo_detail_variation(self):
        g = build('build_nr_vpo_detail', rec('/Game/M', ['Variation Mask', 'Decal'], colors={'Base Color': (.3, .3, .3, 1), 'Base Color Variation': (.6, .5, .4, 1)},
                                             switches={'EnableDecal': True}))
        self.approx(g); self.assertIn('T[Variation Mask]', g.out['color']); self.assertIn('T[Decal]', g.out['color'])

    def test_nr_vpo_detail_master_fabric_cr_and_mask_channel_drive_base_colour(self):
        # Both masters sample hard-coded T_Tarp_03_CR (ReferencedTextures, UV0); MI picks mask channel by vector.
        for master in (PRESETS + 'M_PropPreset_NR+VPO+Detail',
                       '/Game/Pioneer/Environment/POI/FrozenTrail/Extras/NomadCamps/M_NomadTarp_NR+VPO+Detail'):
            with self.subTest(master=master):
                g = build('build_nr_vpo_detail', rec(master, ['Variation Mask'],
                          colors={'Base Color': (.82, .22, 0, 1), 'Base Color Variation': (.82, .3, .1, 1),
                                  'Variation Mask Channel': (0, 0, 1, 0)}, settings={'BlendMode': 'BLEND_Masked'}))
                self.approx(g)
                self.assertIn('T[Tarp CR]', g.out['color'])
                self.assertIn('T[Variation Mask].b', g.out['color'])
                self.assertNotIn('T[Variation Mask].r', g.out['color'])

    def test_nr_vpo_detail_absent_optional_maps_do_not_request_missing_images(self):
        graph, requested = FakeGraph(), []
        original = graph.image
        def image(reference, *args, **kwargs):
            requested.append((reference, kwargs.get('label')))
            return original(reference, *args, **kwargs)
        graph.image = image
        record = rec('/Game/M', colors={'Base Color': (.3, .3, .3, 1)})
        rebuild('generic_families').build_nr_vpo_detail(graph, record)
        self.assertEqual(requested, [])
        self.assertEqual(graph.out['color'], 'P[Base Color]')
        self.assertIsNone(graph.out['normal'])

    def test_nr_vpo_detail_authored_maps_and_enabled_missing_decal_still_request_images(self):
        graph, requested = FakeGraph(), []
        original = graph.image
        def image(reference, *args, **kwargs):
            requested.append((reference, kwargs.get('label')))
            return original(reference, *args, **kwargs)
        graph.image = image
        record = rec('/Game/M', ['Base Texture', 'Variation Mask', 'Folds Normal Texture'],
                     switches={'EnableDecal': True})
        rebuild('generic_families').build_nr_vpo_detail(graph, record)
        self.assertEqual(requested, [('/Game/T/X', 'Base Texture'), ('/Game/T/X', 'Variation Mask'),
                                     (None, 'Decal'), ('/Game/T/X', 'Folds Normal Texture')])
        self.assertIn('T[Base Texture]', graph.out['color'])
        self.assertIn('T[Folds Normal Texture]', graph.out['normal'])

    def test_parameterless_opaque_ignores_opacity(self):
        g = build('build_parameterless', rec('/Game/M', [], {'Opacity': .4}))
        self.assertEqual(g.out['alpha'], 1); self.assertEqual(g.out['color'], (.5, .5, .5, 1))

    def test_broken_glass_masks_sdf_cutout(self):
        g = build('build_broken_glass', rec('/Game/M', ['NC', 'NDD'], {'CutoutLow': .2, 'CutoutHigh': .4, 'tile': 2},
                                            {'ager': (.1, .2, .3, 1)}, settings={'BlendMode': 'BLEND_Masked'}))
        self.approx(g)
        self.assertEqual(set(g.images), {'NC', 'NDD'})
        self.assertIn('T[NDD]', g.out['alpha']); self.assertIn('P[CutoutLow]', g.out['alpha'])
        self.assertIn('T[NC]', g.out['color']); self.assertIsNotNone(g.out['normal'])

    def test_broken_glass_nc_is_normal_plus_mask_not_colour(self):
        # T_BrokenGlassOverlay_01_NC: RGB mean (.50, .49, .99) normal, crack mask in alpha.
        g = build('build_broken_glass', rec('/Game/M', ['NC', 'NDD']))
        self.assertIn('T[NC].a', g.out['color'])
        self.assertNotIn('T[NC]', g.out['color'].replace('T[NC].a', ''))

    def test_broken_glass_without_maps_stays_constant_glass(self):
        g = build('build_broken_glass', rec('/Game/M'))
        self.approx(g); self.assertEqual(g.images, [])

    def test_broken_glass_unknown_selector_vectors_do_not_tint_base_color(self):
        reference = build('build_broken_glass', rec('/Game/M', ['NC', 'NDD']))
        for colors in ({'Param': (0, 0, 1, 1), 'ager': (1, 0, 1, 1)},
                       {'ager': (1, 0, 1, 1), 'Param': (0, 0, 1, 1)}):
            with self.subTest(colors=colors):
                graph = build('build_broken_glass', rec('/Game/M', ['NC', 'NDD'], colors=colors))
                self.assertEqual(graph.out, reference.out)
                self.assertFalse(set(colors) & set(graph.params))

    def test_concrete_trim_trimsheet_alpha_switch(self):
        g = build('build_concrete_trim', rec('/Game/M', ['Trim sheet', 'CH', 'Detail Normal'], {'Global opacity': .9, 'Detail Normal Tile': 4},
                                             {'Color': (.4, .4, .4, 1)}, {'Use Alpha mask': True}))
        self.approx(g)
        self.assertEqual(set(g.images), {'Trim sheet', 'CH', 'Detail Normal'})
        self.assertIn('T[CH]', g.out['color']); self.assertIn('P[Color]', g.out['color'])
        self.assertIn('T[Trim sheet].a', g.out['alpha']); self.assertIn('P[Global opacity]', g.out['alpha'])
        g = build('build_concrete_trim', rec('/Game/M', ['Trim sheet', 'CH']))
        self.assertEqual(g.out.get('alpha', 1), 1)

    def test_engine_default_neutral_grey_with_info(self):
        g = build('build_engine_default', rec('Engine/Content/EngineMaterials/WorldGridMaterial'))
        self.assertTrue(any(w.startswith('engine_default:') for w in g.report['warnings']))
        self.assertEqual(g.out['color'], (.5, .5, .5, 1)); self.assertEqual(g.out['roughness'], .5); self.assertEqual(g.images, [])

    # W5: masters whose maps are fixed samples (master ReferencedTextures), not texture parameters.
    def test_fixed_map_masters_route_to_parameterless(self):
        for base, tex in (('M_RailGunCables', []), ('M_DebrisPile_07_GroundPlane', ['CR']), ('M_Wires_01', ['Overlay']),
                          ('M_MCP_FloorLines_01_A', ['Puddles Mask - Linear Grayscale'])):
            with self.subTest(base=base):
                self.assertEqual(rebuild('records').classify(rec('/Game/Pioneer/Env/' + base, tex)), 'parameterless')

    def test_fixed_map_cables_sample_bc_and_n(self):
        g = build('build_parameterless', rec('/Game/Env/M_RailGunCables', [], {'Tiling': 2}, settings={'BlendMode': 1}))
        self.assertIn('T[T_Cables_BC]', str(g.out['color'])); self.assertIn('T[T_Cables_N]', str(g.out['normal']))
        self.approx(g)

    def test_fixed_map_debris_ground_plane_cr_noh_with_tint(self):
        g = build('build_parameterless', rec('/Game/Env/M_DebrisPile_07_GroundPlane', ['CR'], {'Tilling Base': 2.1255, 'Normal Strength': 1.349},
                                             {'Tint': (1, 1, 1, 1)}, settings={'BlendMode': 1}))
        self.assertIn('T[T_Interior_FloorDust_01_CR]', str(g.out['color'])); self.assertIn('P[Tint]', str(g.out['color']))
        self.assertIn('T[T_Interior_FloorDust_01_NOH]', str(g.out['normal']))

    def test_fixed_map_wires_cr_noh_roughness_range(self):
        g = build('build_parameterless', rec('/Game/Env/M_Wires_01', ['Overlay'], {'RoughnessLow': .6, 'RoughnessHigh': .8},
                                             {'Tint': (.5, .5, .5, 1)}))
        self.assertIn('T[T_Wires_01_CR]', str(g.out['color'])); self.assertIn('T[T_Wires_01_NOH]', str(g.out['normal']))
        self.assertIn('P[RoughnessHigh]', str(g.out['roughness']))

    def test_fixed_map_floorlines_authored_color_nxx_normal_and_clip(self):
        g = build('build_parameterless', rec('/Game/Env/M_MCP_FloorLines_01_A', [], colors={'Color': (.796, .294, .0955, 1)},
                                             settings={'BlendMode': 1, 'OpacityMaskClipValue': .8}))
        self.assertEqual(g.out['color'], 'P[Color]')
        self.assertIn('T[MCP_FloorLines_MCP_FloorLines_NXX]', str(g.out['normal']))
        self.assertIn('T[MCP_FloorLines_MCP_FloorLines_NXX].b', str(g.out['alpha'])); self.assertIn('0.8', str(g.out['alpha']))

    # W10: M_SwampGrass_01 samples fixed T_Grass_01_C (alpha = opacity mask, clip 0.3333) and T_Grass_01_N.
    def test_fixed_map_swamp_grass_color_alpha_clip(self):
        r = rec('/Game/Env/M_SwampGrass_01', ['PM_Diffuse', 'T_Grass_01_C'], settings={'BlendMode': 1, 'OpacityMaskClipValue': .3333})
        self.assertEqual(rebuild('records').classify(r), 'parameterless')
        g = build('build_parameterless', r)
        self.assertIn('T[T_Grass_01_C]', str(g.out['color'])); self.assertIn('T[T_Grass_01_N]', str(g.out['normal']))
        self.assertIn('T[T_Grass_01_C].a', str(g.out['alpha']))

    def test_base_a_col_parameter_is_base_colour(self):
        # W10 MISSING_ALBEDO: M_base_A ReferencedTextures = cross_col via MI param 'col' (MI_cross_A_Inst, rough .8).
        r = rec('/Game/Pioneer/Environment/Props/FrontEnd/NPCRooms_mockup/M_base_A', ['col'], {'rough': .8}, {'tint': (1, 1, 1, 1)})
        r['textures']['col'] = '/Game/T/cross_col'
        self.assertEqual(rebuild('records').classify(r), 'masked_na')
        g = build('build_masked_na', r)
        self.assertIn('T[CA]', g.out['color']); self.assertEqual(g.params['Roughness'], .8)

    def test_fake_interior_window_frame_bc(self):
        # W10 MISSING_ALBEDO: M_FakeInterior_01 MI params 'WIndow Frame ' = T_FakeInterior_WindowFrame_BC_01, 'Window Frame NOM '.
        r = rec(PRESETS + 'M_FakeInterior_01', ['WIndow Frame ', 'Window Frame NOM ', 'Atlas', 'Prop Layer'], {'Window Frame Rough ': 1.0})
        self.assertEqual(rebuild('records').classify(r), 'masked_na')
        g = build('build_masked_na', r)
        self.assertIn('T[CA]', g.out['color']); self.assertIn('T[Normal]', g.out['normal']); self.assertEqual(g.params['Roughness'], 1.0)

    def test_builders_registered(self):
        src = (rebuild('records').__file__.replace('records.py', 'materials.py'))
        text = open(src, encoding='utf-8').read()
        for f in ROUTES:
            with self.subTest(family=f): self.assertIn(f"'{f}'", text)


class ConcretePaintOverlay(unittest.TestCase):
    """W3 SUSPECT_TINT_GRAPH: MI_SperanzaSquare_Trim_01_A has ColorOverlayAmount 0, so the Overlay RGB canvas has zero weight."""
    OVL = {'color': 'OVL.rgb', 'r': 'OVL.r'}

    def paint(self, scalars):
        r = rec('/Game/M_ArchitecturePreset_Concrete_01', ['PaintBreakup'], scalars, {'PaintColor1': (.72, .73, .71, 1)})
        g = FakeGraph()
        color, _, _ = rebuild('surfaces')._concrete_paint(g, r, 'BASE', .5, 'N', 'UV', self.OVL, 'H', 'CAM')
        return str(color)

    def test_zero_color_overlay_amount_skips_overlay_rgb(self):
        self.assertNotIn('OVL.rgb', self.paint({'ColorOverlayAmount': 0.0, 'BreakupOverlayAmount': 0.0}))

    def test_nonzero_color_overlay_amount_uses_overlay_rgb(self):
        self.assertIn('OVL.rgb', self.paint({'ColorOverlayAmount': .5}))

    def test_distance_fade_keeps_overlay_rgb(self):
        self.assertIn('OVL.rgb', self.paint({'ColorOverlayAmount': 0.0, 'Enable_ColorOverlayDistanceFade': 1}))


if __name__ == '__main__': unittest.main()
