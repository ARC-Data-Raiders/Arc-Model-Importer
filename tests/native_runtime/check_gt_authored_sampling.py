"""Run with Blender --background --python; DR_ADDON_PARENT selects a staged addon."""
import bpy,json,os,sys,tempfile
from pathlib import Path
root=Path(os.environ.get('DR_ADDON_PARENT',Path(__file__).resolve().parents[3]))
sys.path.insert(0,str(root))
if os.environ.get('DR_ADDON_PARENT'):
 from addon_snapshot.materials import gt_outfit_pbr as gt
else:
 import importlib
 gt=importlib.import_module('DataRaiders-BlenderImporter.Shared.materials.gt_outfit_pbr')
work=Path(os.environ['DATARAIDERS_TEST_ROOT'])/'sampling-test';work.mkdir(exist_ok=True)
m=bpy.data.materials.new('SamplingRegression');m.use_nodes=True
cases=[('default_srgb',{},True,'sRGB'),('explicit_linear',{'SRGB':False},False,'Non-Color'),('explicit_srgb',{'SRGB':True},True,'sRGB'),('unknown',None,True,'Non-Color')]
for name,props,hint,expected in cases:
 p=work/(name+'.png');im=bpy.data.images.new(name,width=1,height=1);im.pixels[:]=[.5,.5,.5,1];im.filepath_raw=str(p);im.file_format='PNG';im.save();bpy.data.images.remove(im)
 if props is not None:p.with_suffix('.arc-texture.json').write_text(json.dumps({'Type':'Texture2D','PixelFormat':'PF_BC7','Properties':props}))
 n=gt.make_tex(m.node_tree.nodes,str(p),name,(0,0),non_color=hint)
 assert n.image.colorspace_settings.name==expected,(name,n.image.colorspace_settings.name,expected)
 assert n.image.alpha_mode=='CHANNEL_PACKED'
print('GT_AUTHORED_SAMPLING_PASS: default, linear, explicit sRGB, absent metadata')
