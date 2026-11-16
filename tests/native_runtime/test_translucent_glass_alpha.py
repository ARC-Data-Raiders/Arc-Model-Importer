import importlib,unittest
import bpy
from .native_test_support import real_package
materials=real_package('Shared.materials.rebuild.materials')


def _wrapped(name):
    """Material whose Principled sits inside a group wrapper, as Graph.finish commits it."""
    mat=bpy.data.materials.new(name); mat.use_nodes=True
    tree=bpy.data.node_groups.new(name+'.grp','ShaderNodeTree')
    shader=tree.nodes.new('ShaderNodeBsdfPrincipled'); shader.name='Physical surface'
    wrapper=mat.node_tree.nodes.new('ShaderNodeGroup'); wrapper.node_tree=tree
    return mat,shader


def _rec(name,parent,family,scalars):
    return {'name':name,'parent':parent,'package':'/Game/'+name,'family':family,'scalars':scalars,
            'settings':{'BlendMode':'EBlendMode::BLEND_TranslucentGreyTransmittance'}}


class TranslucentGlassAlpha(unittest.TestCase):
    def test_authored_opacity_multiply(self):
        # MI_WorkshopGlass_01_A (M_ChambersGlass_01): OpacityMultiply 0.456.
        mat,shader=_wrapped('MI_WorkshopGlass_01_A')
        report={'warnings':[]}
        materials.translucent_glass_alpha(mat,_rec('MI_WorkshopGlass_01_A','M_ChambersGlass_01','surface',{'OpacityMultiply':0.456,'OpacityPower':1.741347}),report)
        self.assertAlmostEqual(shader.inputs['Alpha'].default_value,0.456,places=4)
        self.assertEqual(mat.surface_render_method,'BLENDED')
        self.assertFalse(any('placeholder' in w for w in report['warnings']))

    def test_fresnel_opacity_placeholder(self):
        # MI_the_scout_visor (M_Visor): Opacity 1.0 with OpacityExponent 2.0; edge math unknown.
        mat,shader=_wrapped('MI_the_scout_visor')
        report={'warnings':[]}
        materials.translucent_glass_alpha(mat,_rec('MI_the_scout_visor','M_Visor','fallback',{'Opacity':1.0,'OpacityExponent':2.0}),report)
        self.assertLess(shader.inputs['Alpha'].default_value,.85)
        self.assertTrue(any('placeholder' in w for w in report['warnings']))

    def test_authored_constant_one_link(self):
        # Fallback/simple builders bind Opacity 1.0 through a Value parameter node.
        mat,shader=_wrapped('MI_Bilguun_CloakOrange_Visor')
        value=shader.id_data.nodes.new('ShaderNodeValue'); value.outputs[0].default_value=1.
        shader.id_data.links.new(value.outputs[0],shader.inputs['Alpha'])
        materials.translucent_glass_alpha(mat,_rec('MI_Bilguun_CloakOrange_Visor','M_Visor_Tinted','simple',{'Opacity':1.0}),{'warnings':[]})
        self.assertFalse(shader.inputs['Alpha'].is_linked)
        self.assertLess(shader.inputs['Alpha'].default_value,.85)

    def test_keeps_transmissive_and_non_glass(self):
        mat,shader=_wrapped('M_Head_Eye'); shader.inputs['Transmission Weight'].default_value=1.
        materials.translucent_glass_alpha(mat,_rec('MI_Eye_Lens','M_Lens','surface',{}),{'warnings':[]})
        self.assertEqual(shader.inputs['Alpha'].default_value,1.)
        mat,shader=_wrapped('MI_Smoke')
        materials.translucent_glass_alpha(mat,_rec('MI_Smoke','M_Smoke','surface',{}),{'warnings':[]})
        self.assertEqual(shader.inputs['Alpha'].default_value,1.)


if __name__=='__main__':unittest.main()
