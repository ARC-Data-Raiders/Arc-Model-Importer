"""Native Blender regression for material-dispatch ownership.

Run in a private profile:
  blender --background --factory-startup -t 4 --python-exit-code 1 \
    --python tests/test_dedicated_material_postrepair_blender.py -- \
    --output <explicit-scratch-directory>/dedicated-material-results.json

DATARAIDERS_TEST_OUTPUT may provide the output JSON path instead. No user-specific
paths are embedded. Fixture directories are task-owned children of that path.
"""

def main():
    """Real Blender slots: generic repair must not overwrite dedicated shaders."""
    import importlib,json,sys,traceback
    from pathlib import Path
    import bpy
    import argparse,os,tempfile
    arguments=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    parser=argparse.ArgumentParser(description='Verify dedicated material ownership in native Blender')
    parser.add_argument('--output',default=os.environ.get('DATARAIDERS_TEST_OUTPUT') or (os.path.join(os.environ['DATARAIDERS_TEST_TEMP'],'dedicated_material_postrepair.json') if os.environ.get('DATARAIDERS_TEST_TEMP') else None),help='Result JSON; parent directory is the explicit scratch location')
    options=parser.parse_args(arguments)
    if not options.output:parser.error('--output or DATARAIDERS_TEST_OUTPUT is required')
    output_path=Path(options.output).expanduser().resolve();output_path.parent.mkdir(parents=True,exist_ok=True)
    ROOT=Path(__file__).resolve().parents[1];WORK=output_path.parent
    sys.pycache_prefix=str(WORK/'pycache');sys.path.insert(0,str(ROOT.parent))
    D=importlib.import_module(ROOT.name+'.Shared.materials.dispatch');C=importlib.import_module(ROOT.name+'.Shared.materials.common')
    fixture_directory=tempfile.TemporaryDirectory(prefix='dedicated-guard-',dir=WORK)
    fixture_root=Path(fixture_directory.name).resolve()
    assert fixture_root.is_relative_to(WORK.resolve())
    folder=fixture_root/'PioneerGame/Content/Pioneer/Characters/Assets/Test/Skins/Basic';folder.mkdir(parents=True,exist_ok=True)
    def fixture(name,parent='M_SimplePBR'):
     path=folder/(name+'.json');path.write_text(json.dumps([{'Type':'MaterialInstanceConstant','Name':name,'Properties':{'Parent':{'ObjectName':"Material'"+parent+"'"},'ScalarParameterValues':[]}}]));return str(path)
    primary=fixture('MI_Layered','M_Character_Layered_5');secondary=fixture('MI_Secondary');changed=fixture('MI_Layered_Blue','M_Character_Layered_5');fur=fixture('MI_Fur','M_FurShells')
    def material(name,path,kind='plain',connected=True):
     mat=bpy.data.materials.new(name);mat.use_nodes=True;mat.node_tree.nodes.clear();n=mat.node_tree.nodes;l=mat.node_tree.links;out=n.new('ShaderNodeOutputMaterial')
     if kind=='arc':
      ng=bpy.data.node_groups.new('ArcTexturer','ShaderNodeTree');ng.interface.new_socket(name='Character',in_out='OUTPUT',socket_type='NodeSocketShader');go=ng.nodes.new('NodeGroupOutput');p=ng.nodes.new('ShaderNodeBsdfPrincipled');ng.links.new(p.outputs[0],go.inputs['Character']);shader=n.new('ShaderNodeGroup');shader.node_tree=ng
     else:
      shader=n.new('ShaderNodeBsdfPrincipled')
      if kind=='gt':shader.name='GT_Principled';mat['arc_outfit_color_pipeline']='ground_truth';mat['arc_skin_json']=path;mat['arc_layered_surface_schema']='native-dry-layered-response-v3'
      if kind=='fur':mat['arc_fur_setup']='native-shells-v1';mat['arc_fur_fallback']=False
     if connected:l.new(shader.outputs[0],out.inputs['Surface'])
     mat['arc_mi_path']=path
     return mat
    mesh=bpy.data.meshes.new('Guard mesh');mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)]);obj=bpy.data.objects.new('Clothing guard object',mesh);bpy.context.scene.collection.objects.link(obj);obj['arc_model_type']='clothing';obj['arc_skin_json']=primary
    base=material('MI_Layered',primary,'gt');plain=material('MI_Secondary',secondary);mesh.materials.append(base);mesh.materials.append(plain)
    checks=[];calls=[]
    def check(name,value,**data):checks.append({'check':name,'passed':bool(value),**data});assert value,(name,data)
    original=D._get_or_build_shared_mi_material
    try:
     def generic(stem,path,*args,**kwargs):
      calls.append(path);return material(stem+' generic rebuilt',path)
     D._get_or_build_shared_mi_material=generic
     check('connected GT identified',D._active_dedicated_outfit_profile(base)=='layered')
     check('same requested MI accepted',D._dedicated_outfit_material_current(base,requested_mi_path=primary,obj=obj))
     check('changed requested MI rejected',not D._dedicated_outfit_material_current(base,requested_mi_path=changed,obj=obj))
     check('native sidecar alias keeps same material identity',D._dedicated_outfit_material_current(base,requested_mi_path=primary[:-5]+'.arc-material.json',obj=obj))
     stamp=base.node_tree.as_pointer();count=D.fix_object_materials_from_mi_slots(obj,str(folder),context=C.CTX_OUTFIT)
     check('postrepair preserves exact connected GT',obj.material_slots[0].material==base and base.node_tree.as_pointer()==stamp)
     check('unhandled secondary remains generically repairable',calls==[secondary] and count==1 and obj.material_slots[1].material!=plain,calls=calls)
     obj['arc_skin_json']=changed;obj['arc_materials_pending']=0;calls.clear();D.fix_object_materials_from_mi_slots(obj,str(folder),context=C.CTX_OUTFIT)
     check('stale requested MI leaves explicit pending state',obj.get('arc_materials_pending')==1)
     check('stale dedicated MI never enters generic pipeline',primary not in calls and changed not in calls,calls=calls)
     # The dedicated owner's rebuilt replacement can now be reused normally.
     newer=material('MI_Layered_Blue',changed,'gt');obj.material_slots[0].material=newer;obj['arc_materials_pending']=0;calls.clear();D.fix_object_materials_from_mi_slots(obj,str(folder),context=C.CTX_OUTFIT)
     check('newly selected dedicated colorway survives postrepair',obj.material_slots[0].material==newer and changed not in calls)
     orphan=material('MI_OrphanGT',primary,'gt',connected=False)
     check('orphan marker is not a successful dedicated shader',not D._dedicated_outfit_material_current(orphan,requested_mi_path=primary))
     arc=material('MI_ArcLegacy',primary,'arc')
     check('connected legacy ArcTexturer identified',D._active_dedicated_outfit_profile(arc)=='layered' and D._dedicated_outfit_material_current(arc,requested_mi_path=primary))
     furmat=material('MI_Fur',fur,'fur')
     check('embedded fur keeps its own source identity',D._dedicated_outfit_material_current(furmat,requested_mi_path=fur,obj=obj))
     check('fur requested source mismatch rejected',not D._dedicated_outfit_material_current(furmat,requested_mi_path=changed,obj=obj))
     check('current layered MI predicate establishes dedicated route',D._mi_uses_layered_character_profile(primary) and not D._mi_uses_layered_character_profile(secondary))
     # Distinct layered sections keep their own MI; the object's primary stamp is not global.
     independent=material('MI_LayeredIndependent',primary,'gt');obj.material_slots[1].material=independent
     obj['arc_skin_json']=changed;obj['arc_materials_pending']=0;calls.clear()
     count=D.fix_object_materials_from_mi_slots(obj,str(folder),context=C.CTX_OUTFIT)
     check('multiple layered sections preserve independent identities',count==0 and not calls and obj.get('arc_materials_pending')==0 and obj.material_slots[0].material==newer and obj.material_slots[1].material==independent)
     check('explicit per-slot MI remains strict with multiple layered owners',not D._dedicated_outfit_material_current(independent,requested_mi_path=changed,obj=obj))
     result={'passed':True,'checks':checks,'count':len(checks),'notes':'Only generic builder is instrumented; actual Blender node connectivity, material slots, MI files and source identity helpers execute normally.'}
    except Exception:result={'passed':False,'checks':checks,'error':traceback.format_exc()}
    finally:
     D._get_or_build_shared_mi_material=original
     assert fixture_root.is_relative_to(WORK.resolve())
     fixture_directory.cleanup()
    output_path.write_text(json.dumps(result,indent=2));print('DEDICATED_GUARD '+json.dumps(result))
    if not result['passed']:raise AssertionError(result.get('error','Dedicated material guard failed'))

if __name__ == '__main__':
    main()
