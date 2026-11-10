import bpy,sys,types,json,os
from pathlib import Path
F=Path(os.environ['DATARAIDERS_TEST_ROOT'])
path=Path(__file__).resolve().parents[2]/'Shared/materials/param_bind.py'
for name in ['neutral_test','neutral_test.Shared.materials','neutral_test.Shared.textures']:
 mod=types.ModuleType(name);mod.__path__=[];sys.modules[name]=mod
mod=types.ModuleType('neutral_test.Shared.materials.param_bind');mod.__package__='neutral_test.Shared.materials';exec(compile(path.read_text(),str(path),'exec'),mod.__dict__)
bpy.ops.wm.read_factory_settings(use_empty=True)
for name,expected in mod._NEUTRAL.items():
 im=mod.neutral_image(name);im.use_fake_user=True
 actual=list(im.pixels[:4]);assert max(abs(a-b) for a,b in zip(actual,expected))<=1/255+.00001,(name,actual,expected)
p=F/'neutral_producer_test.blend';bpy.ops.wm.save_as_mainfile(filepath=str(p));bpy.ops.wm.open_mainfile(filepath=str(p))
for name,expected in mod._NEUTRAL.items():
 im=mod.neutral_image(name);actual=list(im.pixels[:4]);assert max(abs(a-b) for a,b in zip(actual,expected))<=1/255+.00001,(name,'reopened',actual,expected)
 # Existing broken fallback datablocks must be repaired, not silently reused.
 im.pixels[:]=(0,0,0,1);im.pack();im=mod.neutral_image(name)
 actual=list(im.pixels[:4]);assert max(abs(a-b) for a,b in zip(actual,expected))<=1/255+.00001,(name,'existing',actual,expected)
print('NEUTRAL_PRODUCER_ROUNDTRIP_PASS')

