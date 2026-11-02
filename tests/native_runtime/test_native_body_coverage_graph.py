"""Pure graph/slot regressions. Actual Blender rendering is a root-only fixture."""
import ast,json,math,pathlib,types,unittest
try:
 from .native_test_support import ROOT
except ImportError:
 from native_test_support import ROOT
source=ast.parse((ROOT/'body_mask.py').read_text(encoding='utf8'));names={'region_name','coverage_interval','coverage_at_uv','_surface_outputs','_coverage_mode','_coverage_connected','_external_surface_coverage','_flat_alpha_coverage','apply_body_masks'}
namespace={'json':json,'math':math,'_is_body':lambda body:body.type=='MESH','bpy':types.SimpleNamespace(context=types.SimpleNamespace(scene=object()))}
selected=[node for node in source.body if isinstance(node,ast.FunctionDef) and node.name in names or isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('REGIONS','REGION_NAMES') for t in node.targets)]
exec(compile(ast.fix_missing_locations(ast.Module(body=selected,type_ignores=[])),str(ROOT/'body_mask.py'),'exec'),namespace);namespace['_coverage_group']=lambda:GROUP
GROUP=object()
class Sockets(list):
 def __getitem__(self,key):return next(s for s in self if s.name==key) if isinstance(key,str) else super().__getitem__(key)
 def get(self,key):return next((s for s in self if s.name==key),None)
class Socket:
 def __init__(self,node,name,default=0.):self.node=node;self.name=name;self.default_value=default;self.links=[]
class Node(dict):
 def __bool__(self):return True
 def __eq__(self,other):return self is other
 def __ne__(self,other):return self is not other
 __hash__=object.__hash__
 def __init__(self,kind):
  super().__init__();self.kind=kind;self.type={'ShaderNodeOutputMaterial':'OUTPUT_MATERIAL','ShaderNodeGroup':'GROUP','ShaderNodeMath':'MATH','ShaderNodeMixShader':'MIX_SHADER','ShaderNodeBsdfTransparent':'BSDF_TRANSPARENT','ShaderNodeBsdfPrincipled':'BSDF_PRINCIPLED','ShaderNodeUVMap':'UVMAP','ShaderNodeValue':'VALUE'}[kind];self.name=kind;self.label='';self.location=(0,0);self.node_tree=None;self.is_active_output=True;self.operation='';self.uv_map=''
  ins,outs={'OUTPUT_MATERIAL':(['Surface'],[]),'GROUP':(['UV1','Hidden']+list(namespace['REGION_NAMES']),['Shader','Visible']),'MATH':(['Value','Value1','Value2'],['Value']),'MIX_SHADER':(['Fac','Shader1','Shader2'],['Shader']),'BSDF_TRANSPARENT':(['Color'],['BSDF']),'BSDF_PRINCIPLED':(['Alpha'],['BSDF']),'UVMAP':([],['UV']),'VALUE':([],['Value'])}[self.type];self.inputs=Sockets(Socket(self,name,1. if name=='Alpha' else 0.) for name in ins);self.outputs=Sockets(Socket(self,name) for name in outs)
 def as_pointer(self):return id(self)
class Nodes(list):
 def new(self,kind):node=Node(kind);self.append(node);return node
 def get(self,name):return next((node for node in self if node.name==name),None)
class Link:
 def __init__(self,source,target):self.from_socket=source;self.to_socket=target;self.from_node=source.node;self.to_node=target.node
class Links(list):
 def new(self,source,target):
  for link in list(target.links):self.remove(link)
  link=Link(source,target);self.append(link);source.links.append(link);target.links.append(link);return link
 def remove(self,link):super().remove(link);link.from_socket.links.remove(link);link.to_socket.links.remove(link)
class Material(dict):
 def __bool__(self):return True
 def __eq__(self,other):return self is other
 def __ne__(self,other):return self is not other
 __hash__=object.__hash__
 def __init__(self,private=True):
  super().__init__();self.name='body';self.use_nodes=True;self.users=1;self.surface_render_method='DITHERED';self.node_tree=types.SimpleNamespace(nodes=Nodes(),links=Links());source=self.node_tree.nodes.new('ShaderNodeGroup' if private else 'ShaderNodeBsdfPrincipled');source.name='DR.Material' if private else 'Skin';source.node_tree=GROUP if private else None;output=self.node_tree.nodes.new('ShaderNodeOutputMaterial');output.name='DR.Output';self.node_tree.links.new(source.outputs[0],output.inputs['Surface'])
 def copy(self):
  other=Material();other.clear();other.update(self);other.node_tree.nodes.clear();other.node_tree.links.clear();mapping={}
  for original in self.node_tree.nodes:
   node=other.node_tree.nodes.new(original.kind);mapping[id(original)]=node;node.update(original)
   for attr in ('name','label','location','node_tree','is_active_output','operation','uv_map'):setattr(node,attr,getattr(original,attr))
   for left,right in zip(original.inputs,node.inputs):right.default_value=left.default_value
   for left,right in zip(original.outputs,node.outputs):right.default_value=left.default_value
  for link in self.node_tree.links:
   a=mapping[id(link.from_node)];b=mapping[id(link.to_node)];other.node_tree.links.new(a.outputs[link.from_node.outputs.index(link.from_socket)],b.inputs[link.to_node.inputs.index(link.to_socket)])
  return other
class Slot:
 def __init__(self,body):self.body=body;self.link='DATA';self.override=None
 @property
 def material(self):return self.override if self.link=='OBJECT' else self.body.data.materials[0]
 @material.setter
 def material(self,value):
  if self.link=='OBJECT':self.override=value
  else:self.body.data.materials[0]=value
class Body(dict):
 def __init__(self,material,mesh=None):
  super().__init__();self.type='MESH';self.data=mesh or types.SimpleNamespace(users=1,materials=[material],uv_layers=[types.SimpleNamespace(name='TextureUV0'),types.SimpleNamespace(name='CoverageUV1')]);self.material_slots=[Slot(self)]
def state(hidden=False):return {'masks':{name:(1.,1.,1.) for name in namespace['REGION_NAMES']},'hidden':hidden,'sources':['native-fixture'],'warnings':[]}
class BodyMaskGraphTests(unittest.TestCase):
 def apply(self,body,value=None):return namespace['apply_body_masks'](body,state=state() if value is None else value)
 def test_stamped_but_unlinked_private_coverage_is_repaired(self):
  mat=Material();body=Body(mat);coverage=mat.node_tree.nodes.new('ShaderNodeGroup');coverage.name='ArcBodyCoverage';mat['arc_body_mask_signature']=json.dumps([state()['masks'],False,'CoverageUV1'],sort_keys=True)
  self.assertTrue(self.apply(body));self.assertTrue(namespace['_coverage_connected'](mat,coverage));mix=next(n for n in mat.node_tree.nodes if n.get('dr_body_coverage_wrapper'));self.assertIs(mix.inputs[2].links[0].from_node,mat.node_tree.nodes.get('DR.Material'));self.assertIs(mat.node_tree.nodes.get('DR.Material').node_tree,GROUP)
 def test_repeat_and_state_change_never_create_wrapper_chains(self):
  mat=Material();body=Body(mat);self.apply(body);count=len(mat.node_tree.nodes);mix=next(n for n in mat.node_tree.nodes if n.get('dr_body_coverage_wrapper'));self.assertFalse(self.apply(body));self.assertTrue(self.apply(body,state(True)));self.assertEqual(len(mat.node_tree.nodes),count);self.assertIs(mix.inputs[2].links[0].from_node,mat.node_tree.nodes.get('DR.Material'));self.assertEqual(mat.node_tree.nodes.get('ArcBodyCoverageUV').uv_map,'CoverageUV1')
 def test_shared_mesh_data_slot_is_isolated_to_object_and_group_is_shared_readonly(self):
  mat=Material();mesh=types.SimpleNamespace(users=2,materials=[mat],uv_layers=[types.SimpleNamespace(name='UV0'),types.SimpleNamespace(name='UV1')]);body=Body(mat,mesh);other=Body(mat,mesh);self.assertTrue(self.apply(body,state(True)));self.assertEqual(body.material_slots[0].link,'OBJECT');self.assertIs(other.material_slots[0].material,mat);self.assertIsNot(body.material_slots[0].material,mat);self.assertEqual(len(mat.node_tree.nodes),2);self.assertIs(body.material_slots[0].material.node_tree.nodes.get('DR.Material').node_tree,GROUP)
 def test_shared_material_on_independent_mesh_is_copied_without_group_mutation(self):
  mat=Material();mat.users=2;body=Body(mat);other=Body(mat);self.apply(body);self.assertEqual(body.material_slots[0].link,'DATA');self.assertIsNot(body.material_slots[0].material,mat);self.assertIs(other.material_slots[0].material,mat);self.assertEqual(len(mat.node_tree.nodes),2)
 def test_flat_principled_alpha_default_and_link_are_preserved(self):
  mat=Material(False);body=Body(mat);bsdf=mat.node_tree.nodes.get('Skin');bsdf.inputs['Alpha'].default_value=.4;self.apply(body);multiply=bsdf.inputs['Alpha'].links[0].from_node;self.assertEqual(multiply.inputs[0].default_value,.4);self.assertFalse(any(n.get('dr_body_coverage_wrapper') for n in mat.node_tree.nodes));replacement=mat.node_tree.nodes.new('ShaderNodeValue');mat.node_tree.links.new(replacement.outputs[0],bsdf.inputs['Alpha']);self.assertTrue(self.apply(body));self.assertIs(multiply.inputs[0].links[0].from_node,replacement)
 def test_user_replaced_native_surface_is_wrapped_without_stale_source_or_chain(self):
  mat=Material();body=Body(mat);self.apply(body);new=mat.node_tree.nodes.new('ShaderNodeGroup');new.name='NewNativeResponse';output=mat.node_tree.nodes.get('DR.Output');mat.node_tree.links.new(new.outputs[0],output.inputs['Surface']);self.assertTrue(self.apply(body));mix=output.inputs['Surface'].links[0].from_node;self.assertIs(mix.inputs[2].links[0].from_node,new);self.assertEqual(sum(bool(n.get('dr_body_coverage_wrapper')) for n in mat.node_tree.nodes),1)
 def test_missing_active_surface_is_not_stamped_as_success(self):
  mat=Material();body=Body(mat);mat.node_tree.links.remove(mat.node_tree.nodes.get('DR.Output').inputs['Surface'].links[0]);self.assertFalse(self.apply(body));self.assertNotIn('arc_body_mask_signature',mat);self.assertIsNone(mat.node_tree.nodes.get('ArcBodyCoverage'))
if __name__=='__main__':unittest.main(verbosity=2)
