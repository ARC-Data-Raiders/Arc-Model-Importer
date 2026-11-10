"""Authored texture SRGB metadata decides sampling colour space over a builder's linear-mask hint (no Blender)."""
import sys
import tempfile
import types
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'/'native_runtime'));__import__('native_test_support').namespace()
from _native_runtime_regression_addon.Shared.materials.native.interop import Graph

class Image(dict):
    pass

class Repo:
    def __init__(self,path,info):self.path,self.info,self.mount_aliases=path,info,None
    def texture_info(self,reference):return self.info
    def image_path(self,reference,role='data'):return self.path

class AuthoredSrgbTests(unittest.TestCase):
    def sample(self,info,srgb):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'T_Swirly_Noise.png';path.write_bytes(b'png')
            loaded=[]
            def load(filepath,check_existing=False):
                image=Image();image.colorspace_settings=types.SimpleNamespace(name='');image.filepath=filepath;image.alpha_mode='STRAIGHT'
                loaded.append(image);return image
            graph=object.__new__(Graph)
            graph.index=Repo(path,{**info,'source_path':str(path)} if info is not None else {})
            graph.report={};graph._state={'images':{},'samples':{}}
            graph.bpy=types.SimpleNamespace(data=types.SimpleNamespace(images=type('Images',(),{'load':staticmethod(load),'__iter__':lambda self:iter(())})()))
            graph.sample_image=lambda image,uv,label,info:{'image':image,'info':info}
            result=graph.image('/Game/Pioneer/MaterialLibrary/Textures/BlendMask/T_Swirly_Noise',srgb=srgb)
            return result['info']['role'],loaded[0].colorspace_settings.name,graph.report
    def test_sidecar_default_srgb_beats_linear_mask_hint(self):
        role,space,report=self.sample({'LODGroup':'TextureGroup::TEXTUREGROUP_Character'},False)
        self.assertEqual((role,space),('color','sRGB'));self.assertIn('Authored sRGB texture decoded as colour',report['notes'][0])
    def test_authored_linear_texture_stays_data(self):
        self.assertEqual(self.sample({'SRGB':False},False)[:2],('data','Non-Color'))
    def test_no_native_sidecar_keeps_builder_hint(self):
        self.assertEqual(self.sample(None,False)[:2],('data','Non-Color'))
    def test_normal_role_is_not_overridden(self):
        self.assertEqual(self.sample({},'normal')[:2],('normal','Non-Color'))
    def test_every_index_provenance_shape_counts(self):
        # materials.rebuild reports {'source': {...}}; the native catalog tracker only Type.
        decode=__import__('_native_runtime_regression_addon.Shared.materials.native.interop',fromlist=['x']).authored_srgb_decode
        self.assertTrue(decode({'Type':'Texture2D','source':{'path':'x'}}))
        self.assertTrue(decode({'Type':'Texture2D','CompressionSettings':'TextureCompressionSettings::TC_BC7'}))
        self.assertFalse(decode({}))
    def test_formats_without_srgb_variant_stay_data(self):
        # T_MechanicalDebris_01_Mask is TC_Alpha/BC4 with the default flag; CA_ atlases are float.
        decode=__import__('_native_runtime_regression_addon.Shared.materials.native.interop',fromlist=['x']).authored_srgb_decode
        self.assertFalse(decode({'Type':'Texture2D','CompressionSettings':'TextureCompressionSettings::TC_Alpha'}))
        self.assertFalse(decode({'Type':'CurveLinearColorAtlas','source_path':'x'}))
        self.assertEqual(self.sample({'CompressionSettings':'TextureCompressionSettings::TC_Normalmap'},False)[:2],('data','Non-Color'))

class FurShellSidecarTests(unittest.TestCase):
    def test_classic_samplers_follow_the_png_sidecar(self):
        # M_FurShells and the simple hero RM sampler load PNGs directly; Bells' T_FurMask_04_A
        # and Bilguun's tassel RM are DXT with the default flag.
        import ast,json
        tree=ast.parse((ROOT/'Shared/textures.py').read_text(encoding='utf-8'))
        ns={'os':__import__('os'),'json':json,'authored_srgb_decode':__import__('_native_runtime_regression_addon.Shared.materials.native.interop',fromlist=['x']).authored_srgb_decode}
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='authored_srgb_sidecar'],type_ignores=[]),'<textures>','exec'),ns)
        with tempfile.TemporaryDirectory() as folder:
            def check(name,properties):
                png=Path(folder)/(name+'.png');png.write_bytes(b'png')
                if properties is not None:(Path(folder)/(name+'.arc-texture.json')).write_text(json.dumps({'Type':'Texture2D','Properties':properties}),encoding='utf-8')
                return ns['authored_srgb_sidecar'](str(png))
            self.assertTrue(check('T_FurMask_04_A',{}))
            self.assertTrue(check('T_Bilguun_CloakOrange_Tassels_Roughnessmetal',{'LODGroup':'TextureGroup::TEXTUREGROUP_CharacterSpecular'}))
            self.assertFalse(check('T_UV_Noise_M',{'SRGB':False}))
            self.assertFalse(check('T_WindMask_01_A',{'SRGB':False,'CompressionSettings':'TextureCompressionSettings::TC_Alpha'}))
            self.assertFalse(check('T_NoSidecar',None))
class NativeCompilerRoleTests(unittest.TestCase):
    def test_recipe_roles_follow_authored_sampling(self):
        # T_Door_02_A_XEM (native prop recipe 'data') keeps the default flag on BC7; the sampler decodes it.
        from _native_runtime_regression_addon.Weapons.material_runtime.compiler import authored_role
        class Repo:
            def __init__(self,info):self.info=info
            def texture_info(self,reference):return self.info
        texture={'Type':'Texture2D','source_path':'x'}
        self.assertEqual(authored_role(Repo(texture),'/Game/T','data'),'color')
        self.assertEqual(authored_role(Repo({**texture,'SRGB':False}),'/Game/T','color'),'data')
        self.assertEqual(authored_role(Repo(texture),'/Game/T','normal'),'normal')
        self.assertEqual(authored_role(Repo({**texture,'CompressionSettings':'TextureCompressionSettings::TC_Alpha'}),'/Game/T','data'),'data')
        self.assertEqual(authored_role(Repo({}),'/Game/T','data'),'data')
        self.assertEqual(authored_role(object(),'/Game/T','data'),'data')
if __name__=='__main__':unittest.main()
