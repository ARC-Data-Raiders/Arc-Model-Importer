"""Pure-Python regressions for backpack per-slot material rebuild isolation."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import unittest


class Slots(list):
    def pop(self, index=-1):
        return super().pop(index)


class Slot:
    def __init__(self, materials, index):
        self.materials, self.index = materials, index
    @property
    def material(self):
        return self.materials[self.index]
    @material.setter
    def material(self, value):
        self.materials[self.index] = value


class Object(dict):
    def __init__(self, materials):
        super().__init__()
        self.data=SimpleNamespace(materials=Slots(materials),
                                  polygons=[SimpleNamespace(material_index=i) for i in (0,1,0)])
        self.active_material_index=1
    @property
    def material_slots(self):
        return [Slot(self.data.materials,i) for i in range(len(self.data.materials))]


def load_module(rebuild):
    # Isolate package dependencies: these tests never import or stub real bpy.
    package='_backpack_transaction_fixture'
    modules={package:ModuleType(package)}
    modules[package].__path__=[]
    for name in ('Shared','Outfit_Accessories'):
        modules[package+'.'+name]=ModuleType(package+'.'+name);modules[package+'.'+name].__path__=[]
    for name in ('Shared.utils','Shared.textures','Shared.pak_extract','Outfit_Accessories.pak_catalog','Shared.operators'):
        modules[package+'.'+name]=ModuleType(package+'.'+name)
    for name in ('utils','textures','pak_extract','operators'):setattr(modules[package+'.Shared'],name,modules[package+'.Shared.'+name])
    modules[package+'.Outfit_Accessories'].pak_catalog=modules[package+'.Outfit_Accessories.pak_catalog']
    modules[package+'.Shared.operators'].apply_materials_to_object=rebuild
    path=Path(__file__).resolve().parents[1]/'Outfit_Accessories/backpack_catalog.py'
    spec=importlib.util.spec_from_file_location(package+'.Outfit_Accessories.backpack_catalog',path)
    module=importlib.util.module_from_spec(spec)
    modules[spec.name]=module
    return module,spec,modules


class BackpackMaterialTransactionTests(unittest.TestCase):
    def exercise(self, outcome):
        originals=[{'name':'shell'}, {'name':'lamp'}]
        obj=Object(originals)
        obj['arc_skin_json']='original-selected-colorway'
        obj['unrelated_user_value']='preserve-me'
        target=str(Path(__file__).resolve().parent/'synthetic-colorway.json')
        selected={'arc_mi_path':target}
        def rebuild(obj,*args,**kwargs):
            obj.data.materials[:]=[{'name':'temporary'},selected if outcome=='success' else {'name':'unmatched'}, {'name':'extra'}]
            obj.active_material_index=2
            obj['arc_skin_json']='temporary-section-mi'
            obj['arc_skin_choice']='temporary-new-key'
            obj['arc_palette_resolved']='temporary-palette'
            for polygon in obj.data.polygons:polygon.material_index=2
            if outcome=='exception':raise RuntimeError('synthetic build error')
        module,spec,modules=load_module(rebuild)
        part={'mesh':'fixture.psk','materials':{1:target},'choice_id':'fixture','colorway':'red','part':'shell'}
        with patch.dict(sys.modules,modules):
            spec.loader.exec_module(module)
            if outcome=='exception':
                with self.assertRaisesRegex(RuntimeError,'synthetic build error'):
                    module.apply_material_bindings(obj,part)
            else:
                count=module.apply_material_bindings(obj,part)
                self.assertEqual(count,int(outcome=='success'))
        self.assertEqual(len(obj.data.materials),2)
        self.assertIs(obj.data.materials[0],originals[0])
        self.assertIs(obj.data.materials[1],selected if outcome=='success' else originals[1])
        self.assertEqual([p.material_index for p in obj.data.polygons],[0,1,0])
        self.assertEqual(obj.active_material_index,1)
        self.assertEqual(obj['arc_skin_json'],'original-selected-colorway')
        self.assertNotIn('arc_skin_choice',obj)
        self.assertNotIn('arc_palette_resolved',obj)
        self.assertEqual(obj['unrelated_user_value'],'preserve-me')
    def test_unmodified_layered_section_never_uses_generic_builder(self):
        original={'name':'old shell'};obj=Object([original,{'name':'lamp'}]);native={'arc_mi_path':'/native/layered.json'}
        calls=[]
        def rebuild(obj,*args,**kwargs):
            calls.append(kwargs['skin_json']);obj.data.materials[0]=native
        module,spec,modules=load_module(rebuild)
        package=spec.name.split('.',1)[0]
        material_package=ModuleType(package+'.Shared.materials');material_package.__path__=[]
        dispatch=ModuleType(package+'.Shared.materials.dispatch')
        dispatch._mi_uses_layered_character_profile=lambda path:True
        dispatch._dedicated_outfit_material_current=lambda *a,**k:False
        dispatch._get_or_build_shared_mi_material=lambda *a,**k: (_ for _ in ()).throw(AssertionError('generic builder reached'))
        ue=ModuleType(package+'.Shared.uemodel_import')
        ue.parse_uemodel=lambda path:('mesh',10,[SimpleNamespace(materials=[('shell','/Game/layered',0,1)])])
        modules.update({package+'.Shared.materials':material_package,package+'.Shared.materials.dispatch':dispatch,package+'.Shared.uemodel_import':ue})
        with patch.dict(sys.modules,modules):
            spec.loader.exec_module(module);module.cached_file=lambda ref:'/native/layered.json'
            module.apply_material_bindings(obj,{'mesh':'fixture.uemodel','materials':{},'choice_id':'fixture','colorway':'base','part':'shell'})
        self.assertEqual(calls,['/native/layered.json'])
        self.assertIs(obj.data.materials[0],native)
        self.assertEqual([p.material_index for p in obj.data.polygons],[0,1,0])
    def test_success_replaces_only_target(self):self.exercise('success')
    def test_missing_target_rolls_back_every_slot(self):self.exercise('missing')
    def test_exception_rolls_back_every_slot(self):self.exercise('exception')


if __name__=='__main__':unittest.main()
