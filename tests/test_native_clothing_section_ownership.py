"""Native section selection must not confuse a lantern with a layered shell."""
import ast
from pathlib import Path
import unittest


def selection():
    path=Path(__file__).resolve().parents[1]/'materials/clothing.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_select_native_clothing_owner')
    namespace={}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(path),'exec'),namespace)
    return namespace[node.name]


class NativeClothingSectionOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.select=selection()
        self.sections=[{'index':0,'slot':'light'},{'index':1,'slot':'props'}]
        self.paths={0:'native/MI_Lantern.json',1:'native/MI_Props.json'}
        self.identity=lambda path:path.casefold().replace('.arc-material.json','.json')
        self.layered=lambda path:'props' in path.casefold() or 'metal' in path.casefold()
    def test_selected_colorway_targets_unique_native_layered_owner(self):
        self.assertEqual(self.select(self.sections,self.paths,'Skins/Metal.json',self.identity,self.layered),1)
    def test_explicit_slot_override_resolves_multiple_layered_owners(self):
        sections=self.sections+[{'index':2,'slot':'other'}]
        paths=dict(self.paths,**{})
        paths.update({1:'chosen/Metal.json',2:'other/Props.json'})
        self.assertEqual(self.select(sections,paths,'chosen/Metal.arc-material.json',self.identity,self.layered),1)
    def test_ambiguous_layered_owner_is_not_guessed(self):
        sections=self.sections+[{'index':2,'slot':'other'}];paths=dict(self.paths);paths[2]='other/Props.json'
        with self.assertRaisesRegex(ValueError,'Multiple native layered'):
            self.select(sections,paths,'missing/Metal.json',self.identity,self.layered)
    def test_shared_selected_mi_targets_all_exact_authored_sections(self):
        paths={0:'shared/Props.json',1:'shared/Props.json'}
        self.assertEqual(self.select(self.sections,paths,'shared/Props.json',self.identity,self.layered),(0,1))
    def test_nonlayered_mesh_does_not_invent_owner(self):
        self.assertIsNone(self.select(self.sections,{0:'lantern',1:'glass'},'chosen',self.identity,lambda _:False))


if __name__=='__main__':unittest.main()
