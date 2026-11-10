"""Repository-relative test imports and tiny fixture builders; no game cache needed."""
import ast,importlib,importlib.util,json,os,re,sys,tempfile,types
from pathlib import Path
ROOT=Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY',Path(__file__).resolve().parents[2])).resolve()
if not (ROOT/'Shared/materials/native/core.py').is_file():raise RuntimeError('Run from installed repository tests, or set DATARAIDERS_TEST_REPOSITORY for staged verification')
sys.path.insert(0,str(ROOT))
PACKAGE='_native_runtime_regression_addon'
def namespace():
    for name,path in ((PACKAGE,ROOT),(PACKAGE+'.Shared',ROOT/'Shared'),(PACKAGE+'.Weapons',ROOT/'Weapons'),(PACKAGE+'.Shared.materials',ROOT/'Shared/materials'),(PACKAGE+'.Weapons.material_runtime',ROOT/'Weapons/material_runtime')):
        if name not in sys.modules:
            module=types.ModuleType(name);module.__path__=[str(path)];sys.modules[name]=module
    return importlib.import_module(PACKAGE+'.Shared.materials.native.native_families')
def functions_module(name,names,bpy=None):
    path=ROOT/'Shared/materials'/(name+'.py');tree=ast.parse(path.read_text(encoding='utf-8'));selected=[x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name in names]
    if set(x.name for x in selected)!=set(names):raise AssertionError('canonical function set changed')
    module=types.ModuleType(PACKAGE+'.Shared.materials.'+name);module.__package__=PACKAGE+'.Shared.materials';module.__dict__.update(bpy=bpy,json=json,os=os,re=re,FAMILY_ENVIRONMENT='environment')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+selected,type_ignores=[])),str(path),'exec'),module.__dict__);return module

def temp_fixture(prefix='native-runtime-'):
    # The repository has no fixed scratch/cache drive dependency. CI can redirect
    # TEMP/TMP normally; tests never create files in the source checkout.
    return tempfile.TemporaryDirectory(prefix=prefix,dir=os.environ.get('DATARAIDERS_TEST_TEMP'))

def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');return path

def create_shell_fixture(root):
    assets=Path(root)/'export';content=assets/'PioneerGame/Content';master='/Game/Pioneer/MaterialLibrary/M_Presets/M_EnemyPreset_Masked_NAO+Detail';package='/Game/Pioneer/Enemies/Fixture/MI_Shell'
    def material(ref,kind,props):return write_json(content/(ref.removeprefix('/Game/')+'.arc-material.json'),{'Type':kind,'Name':ref.rsplit('/',1)[-1],'Package':ref,'Properties':props})
    switches=[{'ParameterInfo':{'Name':key},'Value':value} for key,value in {'Parallax':True,'V0_HideWhenDamaged':True,'V1_HideWhenDamaged':True,'V2_HideWhenDamaged':True,'Use Frost':False,'Use Snow':False}.items()]
    refs={'DecalTrimsheet':'/Game/Test/NAO','Height/HX':'/Game/Test/HX','DetailTexture':'/Game/Test/Detail'}
    params=[{'ParameterInfo':{'Name':key},'ParameterValue':{'ObjectPath':ref}} for key,ref in refs.items()]
    material(master,'Material',{'BlendMode':'BLEND_Masked','StaticParameters':{'StaticSwitchParameters':switches},'TextureParameterValues':params})
    source=material(package,'MaterialInstanceConstant',{'Parent':{'ObjectPath':master}})
    for ref in refs.values():
        path=content/(ref.removeprefix('/Game/')+'.png');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture image bytes, never decoded')
        write_json(content/(ref.removeprefix('/Game/')+'.arc-texture.json'),{'Type':'Texture2D','Name':ref.rsplit('/',1)[-1],'Package':ref,'Properties':{'SRGB':False},'Mips':[{'SizeX':1,'SizeY':1,'SizeZ':1}]})
    return assets,source

def real_package(name='Shared.materials.native'):
    for key,path in ((PACKAGE,ROOT),(PACKAGE+'.Shared',ROOT/'Shared'),(PACKAGE+'.Weapons',ROOT/'Weapons'),(PACKAGE+'.Shared.materials',ROOT/'Shared/materials'),(PACKAGE+'.Weapons.material_runtime',ROOT/'Weapons/material_runtime')):
        if key not in sys.modules:
            module=types.ModuleType(key);module.__path__=[str(path)];sys.modules[key]=module
    return importlib.import_module(PACKAGE+'.'+name)
