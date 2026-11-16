"""Off decal shell slots keep their image nodes muted; a swap that enables the slot unmutes them."""
try:
    from .native_test_support import PACKAGE,namespace
except ImportError:
    from native_test_support import PACKAGE,namespace
import importlib,unittest
try:
    import bpy
except ImportError:
    bpy=None
namespace()


@unittest.skipIf(bpy is None,'needs Blender')
class DecalOffSlotMuteTests(unittest.TestCase):
    def setUp(self):
        self.gt=importlib.import_module(PACKAGE+'.Shared.materials.gt_outfit_pbr')
        self.mat=bpy.data.materials.new('DR_Test_DecalMute');self.mat.use_nodes=True
        nodes=self.mat.node_tree.nodes;self.nodes=nodes;self.tex={}
        for stage in ('color','data'):
            nd=nodes.new('ShaderNodeTexImage');nd['dr.decal_slot']=1;nd['dr.decal_role']=stage;self.tex[stage]=nd
        self._orig=(self.gt.decal_slot_values,self.gt._load_image_cached)

    def tearDown(self):
        self.gt.decal_slot_values,self.gt._load_image_cached=self._orig
        bpy.data.materials.remove(self.mat)

    def _values(self,on):
        z3=(0.0,0.0,0.0)
        return {'enabled':on,'texture':'','data':'','extension':'CLIP','label':'t','allow':0,
                'params':{'rot_location':z3,'rot_rotation':0.0,'rot_scale':(1.0,1.0,1.0),
                          'place_location':z3,'place_rotation':0.0,'place_scale':(1.0,1.0,1.0),'flip_v':False},
                'surface_override':0.0,'wear_override':0.0,'base_normal_override':0.0,'detail_normal_override':0.0,
                'tiling':1.0,'rotation':0.0,'data_blue':0.0,'color_a':(1.0,1.0,1.0,1.0),'color_b':(1.0,1.0,1.0,1.0),'override':0.0}

    def _write(self,on):
        self.gt.decal_slot_values=lambda *a,**k:self._values(on)
        self.gt._load_image_cached=lambda *a,**k:None
        return self.gt.write_decal_slot(None,self.nodes,1,{},'',[],'')

    def test_off_slot_mutes_then_swap_unmutes(self):
        self.assertFalse(self._write(False))
        for stage,nd in self.tex.items():
            with self.subTest(stage=stage):self.assertTrue(nd.mute,'off slot image node must be muted')
        self.assertTrue(self._write(True))
        for stage,nd in self.tex.items():
            with self.subTest(stage=stage):self.assertFalse(nd.mute,'enabled slot image node must be live')


if __name__=='__main__':
    unittest.main()
