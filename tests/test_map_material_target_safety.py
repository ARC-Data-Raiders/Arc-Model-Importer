"""Exercise the production map collector in isolation, without importing bpy."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


class Mesh(dict):
    def __init__(self,name,**properties):
        super().__init__(properties);self.name=name;self.type='MESH'
        self.data=SimpleNamespace(as_pointer=lambda:id(self))


def collector(objects):
    source=Path(__file__).resolve().parents[1]/'Maps/map_placement.py'
    tree=ast.parse(source.read_text(encoding='utf-8'))
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='collect_map_mesh_targets')
    module=ast.Module(body=[function],type_ignores=[])
    namespace={'bpy':SimpleNamespace(data=SimpleNamespace(objects=objects))}
    exec(compile(ast.fix_missing_locations(module),str(source),'exec'),namespace)
    return namespace[function.name]


class MapMaterialTargetSafetyTests(unittest.TestCase):
    def setUp(self):
        self.character=Mesh('Face03',arc_psk_path='face.uemodel')
        self.accessory=Mesh('Charm',arc_mesh_file='charm.uemodel')
        self.map_a=Mesh('Rock',arc_map='Dam')
        self.map_b=Mesh('Chair',arc_map='City')
        self.instancer=Mesh('Points',arc_map='Dam',arc_placement_instancer=True)
        self.height=Mesh('Height',arc_map='Dam',arc_heightmap_plane=True)
        self.objects=[self.character,self.accessory,self.map_a,self.map_b,self.instancer,self.height]
        self.collect=collector(self.objects)
        self.context=SimpleNamespace(scene=SimpleNamespace(arc_placement_map='NONE',arc_placement_map_name='NONE'),selected_objects=[])
    def test_no_map_sentinel_only_accepts_tagged_geometry(self):
        result=self.collect(self.context,'NONE')
        self.assertEqual([o.name for o in result],['Rock','Chair'])
    def test_selected_other_map_does_not_fall_back(self):
        self.context.selected_objects=[self.map_b,self.character]
        self.assertEqual(self.collect(self.context,'Dam',only_selected=True),[])
    def test_empty_selection_does_not_scan_scene(self):
        self.assertEqual(self.collect(self.context,'Dam',only_selected=True),[])
    def test_selected_valid_map_deduplicates_linked_data(self):
        duplicate=Mesh('RockCopy',arc_map='Dam');duplicate.data=self.map_a.data
        self.context.selected_objects=[self.map_a,duplicate,self.map_b,self.height]
        self.assertEqual([o.name for o in self.collect(self.context,'Dam',only_selected=True)],['Rock'])
    def test_explicit_map_only_accepts_matching_tag(self):
        self.assertEqual([o.name for o in self.collect(self.context,'City')],['Chair'])


if __name__=='__main__':unittest.main()
