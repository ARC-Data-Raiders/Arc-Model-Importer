import importlib.util,os,sys,types,unittest
from pathlib import Path
ROOT=Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY',Path(__file__).resolve().parents[1])).resolve()
SOURCE=Path(os.environ.get('DATARAIDERS_CHARACTER_STAGE',ROOT))
class Socket:
    def __init__(self,v):self.default_value=v
class Node:
    type="BSDF_PRINCIPLED"
    def __init__(self):self.inputs={"Subsurface Weight":Socket(.0),"Subsurface Radius":Socket((1.,.2,.1))}
class Nodes(list):
    def new(self,kind):
        n=Node();self.append(n);return n
class Tree(dict):
    def __init__(self,name):super().__init__();self.name=name;self.nodes=Nodes();self.users=1
    def copy(self):raise AssertionError("binary/tree copy prohibited")
class Groups(list):
    def new(self,name,kind):t=Tree(name);self.append(t);return t
class Bootstrap(unittest.TestCase):
    def setUp(self):
        self.saved={k:sys.modules.get(k) for k in ['bpy','_face_probe','_face_probe.character_face','_face_probe.character_bootstrap']}
        self.groups=Groups();sys.modules['bpy']=types.SimpleNamespace(data=types.SimpleNamespace(node_groups=self.groups))
        package=types.ModuleType('_face_probe');package.__path__=[];sys.modules['_face_probe']=package
        native=types.ModuleType('_face_probe.character_face');native.parameters=lambda path:{'source':path};self.received=[]
        def build(t,record):
            self.received.append((t,record,[(n.type,{k:s.default_value for k,s in n.inputs.items()}) for n in t.nodes]));t.nodes.clear();t['arc_face_native_revision']=1
        native.build_face_tree=build;sys.modules[native.__name__]=native;self.native=native
        spec=importlib.util.spec_from_file_location('_face_probe.character_bootstrap',ROOT/'Shared/materials/character_bootstrap.py');self.module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=self.module;spec.loader.exec_module(self.module)
        self.mat=types.SimpleNamespace(name='Face')
    def tearDown(self):
        for k,v in self.saved.items():
            if v is None:sys.modules.pop(k,None)
            else:sys.modules[k]=v
    def test_actual_fidelity_entry_does_not_touch_vendor_base(self):
        import ast
        source=ast.parse((SOURCE/'Shared/materials/character_fidelity.py').read_text(encoding='utf-8'))
        function=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='prepare_face_shader')
        namespace={'__package__':'_face_probe','os':types.SimpleNamespace(path=types.SimpleNamespace(isfile=lambda p:True))}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'<actual staged fidelity>','exec'),namespace)
        class Forbidden:
            def __getattribute__(self,name):raise AssertionError('vendor template was accessed: '+name)
        tree=namespace['prepare_face_shader'](self.mat,Forbidden(),mi_path='native.json')
        self.assertEqual(tree['dr_face_bootstrap'],'native-interface-v2')
    def test_actual_head_native_gate_has_no_binary_template_ensure(self):
        import ast
        source=ast.parse((SOURCE/'Shared/materials/character.py').read_text(encoding='utf-8'))
        function=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='_setup_head_slot_head')
        calls=[n for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('ensure_node_group','ensure_material')]
        self.assertEqual(calls,[])
        missing=next(n for n in function.body if isinstance(n,ast.If) and isinstance(n.test,ast.UnaryOp) and isinstance(n.test.operand,ast.Name) and n.test.operand.id=='native_face_source')
        self.assertTrue(any(isinstance(n,ast.Return) for n in missing.body))
    def test_initial_native_face_has_no_template_or_copy(self):
        tree=self.module.prepare_native_face(self.mat,'native.json');self.assertEqual(tree.name,'DR.Face.Face');self.assertEqual(self.received[0][2],[]);self.assertEqual(tree['arc_face_shader_owner'],'Face')
    def test_preserves_only_explicit_sss_values_without_copying_tree(self):
        old=Tree('vendor');n=old.nodes.new('ShaderNodeBsdfPrincipled');n.inputs['Subsurface Weight'].default_value=.37
        tree=self.module.prepare_native_face(self.mat,'native.json',old);self.assertIsNot(tree,old);self.assertEqual(self.received[0][2][0][1]['Subsurface Weight'],.37);self.assertEqual(len(old.nodes),1)
    def test_owned_native_tree_also_builds_fresh_before_commit(self):
        old=Tree('native');old.update(arc_face_native_revision=1,arc_face_shader_owner='Face');self.assertIsNot(self.module.prepare_native_face(self.mat,'native.json',old),old);self.assertEqual(len(self.groups),1)
    def test_shared_native_tree_remains_untouched(self):
        old=Tree('native');old.update(arc_face_native_revision=1,arc_face_shader_owner='Face');old.users=2;self.assertIsNot(self.module.prepare_native_face(self.mat,'native.json',old),old)
    def test_failed_builder_removes_only_new_datablock(self):
        def fail(*args):raise RuntimeError('fixture native failure')
        self.native.build_face_tree=fail
        with self.assertRaises(RuntimeError):self.module.prepare_native_face(self.mat,'native.json')
        self.assertEqual(self.groups,[])
    def test_failed_builder_never_clears_previous_owned_working_tree(self):
        old=Tree('native');old.update(arc_face_native_revision=1,arc_face_shader_owner='Face');old.nodes.new('ShaderNodeBsdfPrincipled');before=(dict(old),list(old.nodes))
        def fail(candidate,*args):candidate.nodes.clear();raise RuntimeError('failure after clear')
        self.native.build_face_tree=fail
        with self.assertRaises(RuntimeError):self.module.prepare_native_face(self.mat,'native.json',old)
        self.assertEqual((dict(old),list(old.nodes)),before);self.assertEqual(self.groups,[])
    def test_failure_reading_native_record_allocates_nothing(self):
        def fail(*args):raise ValueError('bad native source')
        self.native.parameters=fail
        with self.assertRaises(ValueError):self.module.prepare_native_face(self.mat,'native.json')
        self.assertEqual(self.groups,[])
if __name__=='__main__':unittest.main(verbosity=2)
