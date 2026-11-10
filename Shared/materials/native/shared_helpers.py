"""Original procedural helper boundary registry; no binary libraries.
NormalFlipper is a legacy compatibility encoding, not native DX reconstruction.
Its RG pass-through and radial Z match isolated public-helper probes064/069.
"""
import hashlib,math,sys,threading
from pathlib import Path
from .loaded_identity import RestartRequired,require_loaded_identity
_SCHEMA_PATH=Path(__file__).with_name('shared_helper_schema.json')
_SCHEMA_BYTES=_SCHEMA_PATH.read_bytes()
_SCHEMAS=__import__('json').loads(_SCHEMA_BYTES)
_SCHEMA_SHA=hashlib.sha256(_SCHEMA_BYTES).hexdigest()
RECIPE='shared-original-v2-'+hashlib.sha256(Path(__file__).read_bytes()+_SCHEMA_BYTES).hexdigest()[:16]
SUPPORTED=('NormalFlipper',)
class UnsupportedHelperGroup(RuntimeError):pass

def evaluate_normal_flipper(color):
 r,g=float(color[0]),float(color[1]);radial=min(math.hypot(r-.5,g-.5),.5)
 return (r,g,math.sqrt(max(1.-radial,0.)),1.)

def find_group(name,bpy_module=None):
 name='ArcTexturer' if name=='Arc Texturer' else name
 if name not in SUPPORTED:return None
 if bpy_module is None:import bpy as bpy_module
 candidates=[tree for tree in bpy_module.data.node_groups if tree.get('dr.shared_helper.key')==name and tree.get('dr.shared_helper.recipe')==RECIPE]
 return max(candidates,key=lambda tree:tree.get('dr.shared_helper.epoch',0),default=None)

def ensure_group(name,bpy_module=None,*,force=False):
 if name not in SUPPORTED:raise UnsupportedHelperGroup('No original shared helper registered: '+str(name))
 if threading.current_thread() is not threading.main_thread():raise RuntimeError('Shared shader helpers require the Blender main thread')
 require_loaded_identity(sys.modules[__name__])
 if bpy_module is None:import bpy as bpy_module
 old=find_group(name,bpy_module)
 if old is not None and not force:return old
 epoch=max((tree.get('dr.shared_helper.epoch',0) for tree in bpy_module.data.node_groups if tree.get('dr.shared_helper.key')==name),default=0)+1
 tree=bpy_module.data.node_groups.new('DR.Details.NormalEncoding.'+RECIPE.rsplit('-',1)[-1],'ShaderNodeTree')
 try:
  incoming=tree.interface.new_socket(name='Color',in_out='INPUT',socket_type='NodeSocketColor');incoming.default_value=(.8,.8,.8,1.)
  tree.interface.new_socket(name='Color',in_out='OUTPUT',socket_type='NodeSocketColor')
  nodes,links=tree.nodes,tree.links;depths={}
  def node(kind,role,depth):
   n=nodes.new(kind);n.name='DR.Details.'+role;n.label=role.replace('.',' ');n['dr.semantic_id']=n.name;n['dr.stage']='DR.Details';depths[n.as_pointer()]=depth;return n
  def scalar(operation,a,b=None,*,role,depth):
   n=node('ShaderNodeMath',role,depth);n.operation=operation
   for index,value in enumerate((a,b)):
    if value is None:continue
    if isinstance(value,(int,float)):n.inputs[index].default_value=value
    else:links.new(value,n.inputs[index])
   return n.outputs[0]
  source=node('NodeGroupInput','Normal.Source',0);split=node('ShaderNodeSeparateXYZ','Normal.Channels',1);links.new(source.outputs['Color'],split.inputs[0]);r,g=split.outputs[0],split.outputs[1]
  x=scalar('SUBTRACT',r,.5,role='Normal.CenteredR',depth=2);y=scalar('SUBTRACT',g,.5,role='Normal.CenteredG',depth=2)
  combine=node('ShaderNodeCombineXYZ','Normal.RadialVector',3);links.new(x,combine.inputs[0]);links.new(y,combine.inputs[1]);combine.inputs[2].default_value=0
  length=node('ShaderNodeVectorMath','Normal.RadialLength',4);length.operation='LENGTH';links.new(combine.outputs[0],length.inputs[0]);limited=scalar('MINIMUM',length.outputs['Value'],.5,role='Normal.RadialLimit',depth=5)
  remaining=scalar('SUBTRACT',1.,limited,role='Normal.HeightSquared',depth=6);height=scalar('SQRT',remaining,role='Normal.Height',depth=7)
  output_color=node('ShaderNodeCombineXYZ','Normal.EncodedCompatibility',8);links.new(r,output_color.inputs[0]);links.new(g,output_color.inputs[1]);links.new(height,output_color.inputs[2]);end=node('NodeGroupOutput','Normal.Output',9);links.new(output_color.outputs[0],end.inputs['Color'])
  bins={}
  for n in nodes:bins.setdefault(depths[n.as_pointer()],[]).append(n)
  for column,members in sorted(bins.items()):
   for row,n in enumerate(sorted(members,key=lambda item:item.name)):n.location=(column*210.,-row*160.)
  tree['dr.shared_helper.key']=name;tree['dr.shared_helper.recipe']=RECIPE;tree['dr.shared_helper.epoch']=epoch;tree['dr.legacy_group_key']=name
  tree['dr.provenance']='Original construction from public boundary064/069; no retained topology or binary loader'
  tree['dr.compatibility.encoding']='RG unchanged; Z=sqrt(1-min(length(RG-.5),.5)); not native DX decode'
  return tree
 except BaseException:
  bpy_module.data.node_groups.remove(tree);raise


# Boundaries reconstructed from isolated public-helper range072. They describe
# the historical preview contract, not Unreal Color N decoding. Resolution1/64.
ID_BANDS=((.0078125,.0234375),(.0234375,.1015625),(.1015625,.1796875),(.1796875,.3046875),(.3046875,.4453125),(.4453125,.5984375),(.6015625,.7421875),(.7578125,.8828125),(.8828125,1.0078125))
_NORMAL_BUILD=ensure_group
SUPPORTED=('NormalFlipper','ColorMask_XYZ','Decal Data','Decal LayerMask Gate','Decal LayerMask','CurvatureID_Override','Visor','ArcTexturer','WeaponTexturer')

def evaluate_id_mask(blue,color_n,softness=0.):
 index=int(color_n)-1
 if not 0<=index<len(ID_BANDS):return 0.
 lower,upper=ID_BANDS[index];width=min(max(float(softness),0.),1.)/32.
 return float(lower-width<float(blue)<upper+width)

def evaluate_layer_gate(mid,mask):
 mask=max(0,min(255,int(mask)))
 return float(any(mask&(1<<i) and evaluate_id_mask(mid,i+1) for i in range(8)))

def evaluate_colormask(mask,x,y,z,rough=(1.,1.,1.),metal=(0.,0.,0.)):
 g,b=float(mask[1]),float(mask[2]);gc=min(max(g,0),1);bc=min(max(b,0),1)
 color=tuple(((1-gc)*a+gc*c)*(1-bc)+bc*d for a,c,d in zip(x[:3],y[:3],z[:3]))
 basis=lambda v:float(v[0])+g*(float(v[1])-float(v[0]))+b*(float(v[2])-float(v[0]))
 return {'Mask_Color':color,'Mask_Roughness':basis(rough),'Mask_Metal':basis(metal)}

class _Graph:
 def __init__(self,bpy,key):
  self.bpy=bpy;self.key=key;self.tree=bpy.data.node_groups.new('DR.Shared.'+key.replace(' ','').replace('_','')+'.'+RECIPE.rsplit('-',1)[-1],'ShaderNodeTree');self.nodes=self.tree.nodes;self.links=self.tree.links;self.counts={};self.stages={};self.mask_cache={}
  contract=_SCHEMAS[key]
  for direction,items in (('INPUT',contract['inputs']),('OUTPUT',contract['outputs'])):
   for item in items:
    typ=item['type'];typ='NodeSocketFloat' if typ.startswith('NodeSocketFloat') else typ
    sock=self.tree.interface.new_socket(name=item['name'],in_out=direction,socket_type=typ)
    if item.get('default_value') is not None:sock.default_value=item['default_value']
    if typ=='NodeSocketFloat':
     sock.min_value=0. if item['type'].endswith(('Factor','Percentage')) else -3.402823466e38;sock.max_value=1. if item['type'].endswith(('Factor','Percentage')) else 3.402823466e38
  self.source=self.node('NodeGroupInput','Source','Controls');self.end=self.node('NodeGroupOutput','Output','Response')
 def node(self,kind,stage,role):
  ordinal=self.counts.get((stage,role),0);self.counts[(stage,role)]=ordinal+1;n=self.nodes.new(kind);n.name='DR.'+stage+'.'+role+(('.'+str(ordinal)) if ordinal else '');n.label=role.replace('.',' ');n['dr.semantic_id']=n.name;n['dr.stage']='DR.'+stage;self.stages[n.as_pointer()]=stage;return n
 def put(self,sock,value):
  if isinstance(value,(int,float,bool,tuple,list)):sock.default_value=value
  else:self.links.new(value,sock)
 def input(self,key):return self.source.outputs[key]
 def math(self,op,*values,stage='Surface',role=None):
  n=self.node('ShaderNodeMath',stage,role or op.title()) ;n.operation=op
  for index,value in enumerate(values):self.put(n.inputs[index],value)
  return n.outputs[0]
 def split(self,value,stage='Source'):
  n=self.node('ShaderNodeSeparateXYZ',stage,'Channels');self.put(n.inputs[0],value);return tuple(n.outputs)
 def combine(self,x,y,z,stage='Coordinates'):
  n=self.node('ShaderNodeCombineXYZ',stage,'Vector')
  for sock,value in zip(n.inputs,(x,y,z)):self.put(sock,value)
  return n.outputs[0]
 def vector(self,op,a,b=None,stage='Details'):
  n=self.node('ShaderNodeVectorMath',stage,op.title());n.operation=op;self.put(n.inputs[0],a)
  if b is not None:self.put(n.inputs[1],b)
  return n.outputs['Value' if op in ('LENGTH','DOT_PRODUCT','DISTANCE') else 'Vector']
 def mix_color(self,a,b,factor,stage='Surface',role='ColorBlend'):
  n=self.node('ShaderNodeMixRGB',stage,role);n.blend_type='MIX';n.use_clamp=False
  for sock,value in zip(n.inputs,(factor,a,b)):self.put(sock,value)
  return n.outputs[0]
 def mix_shader(self,a,b,factor,role='ShaderBlend'):
  n=self.node('ShaderNodeMixShader','Coverage',role)
  for sock,value in zip(n.inputs,(factor,a,b)):self.put(sock,value)
  return n.outputs[0]
 def sat(self,x):return self.math('MINIMUM',self.math('MAXIMUM',x,0),1)
 def emit(self,color):
  n=self.node('ShaderNodeEmission','Light','Preview');self.put(n.inputs['Color'],color);n.inputs['Strength'].default_value=1.;return n.outputs[0]
 def id_mask(self,mid,color_n,softness=0.):
  token=lambda value:('socket',value.as_pointer()) if hasattr(value,'as_pointer') else ('value',value)
  key=(token(mid),token(color_n),token(softness))
  if key in self.mask_cache:return self.mask_cache[key]
  result=0.;extension=self.math('DIVIDE',softness,32.,stage='Coverage',role='Mask.Softness')
  entries=list(enumerate(ID_BANDS))
  if isinstance(color_n,(int,float)):
   index=int(color_n)-1
   entries=[] if not 0<=index<len(ID_BANDS) else [(index,ID_BANDS[index])]
  for index,(lower,upper) in entries:
   selected=1. if isinstance(color_n,(int,float)) else self.math('COMPARE',color_n,index+1,.1,stage='Coverage',role='Mask.Select'+str(index+1))
   above=self.math('GREATER_THAN',mid,self.math('SUBTRACT',lower,extension),stage='Coverage',role='Mask.Above'+str(index+1));below=self.math('LESS_THAN',mid,self.math('ADD',upper,extension),stage='Coverage',role='Mask.Below'+str(index+1));part=self.math('MULTIPLY',selected,self.math('MULTIPLY',above,below),stage='Coverage');result=self.math('ADD',result,part,stage='Coverage')
  self.mask_cache[key]=self.sat(result);return self.mask_cache[key]
 def gate(self,mid,mask):
  result=0.
  for index,(lower,upper) in enumerate(ID_BANDS[:8]):
   bit=self.math('FLOORED_MODULO',self.math('FLOOR',self.math('DIVIDE',mask,2**index)),2,stage='Coverage',role='Mask.Bit'+str(index));window=self.math('MULTIPLY',self.math('GREATER_THAN',mid,lower),self.math('LESS_THAN',mid,upper),stage='Coverage',role='Mask.Window'+str(index));result=self.math('ADD',result,self.math('MULTIPLY',window,bit),stage='Coverage')
  return self.sat(result)
 def checker(self,scale):
  uv=self.node('ShaderNodeTexCoord','Coordinates','PreviewCoordinates').outputs['UV'];u,v,_=self.split(uv);return self.math('FLOORED_MODULO',self.math('ADD',self.math('FLOOR',self.math('MULTIPLY',u,scale)),self.math('FLOOR',self.math('MULTIPLY',v,scale))),2,stage='Coverage',role='Preview.Checker')
 def finish(self,outputs,diagnostics=()):
  for key,value in outputs.items():self.put(self.end.inputs[key],value)
  incoming={node.as_pointer():[] for node in self.nodes}
  for link in self.links:incoming[link.to_node.as_pointer()].append(link.from_node)
  depth={}
  def level(node):
   key=node.as_pointer()
   if key not in depth:depth[key]=max((level(parent)+1 for parent in incoming[key]),default=0)
   return depth[key]
  x=0.
  for stage in ('Source','Coordinates','Wear','Details','Surface','Light','Coverage','Output'):
   members=[n for n in self.nodes if self.stages.get(n.as_pointer())==stage]
   if not members:continue
   columns={}
   for n in members:columns.setdefault(level(n),[]).append(n)
   frame=self.nodes.new('NodeFrame');frame.name='DR.'+stage;frame.label=stage;frame.location=(x,0.)
   for col,(_,rows) in enumerate(sorted(columns.items())):
    for row,n in enumerate(sorted(rows,key=lambda item:item.name)):n.parent=frame;n.location=(col*220.,-row*160.)
   x+=len(columns)*220.+300.
  self.tree['dr.shared_helper.key']=self.key;self.tree['dr.shared_helper.recipe']=RECIPE;self.tree['dr.legacy_group_key']=self.key;self.tree['dr.shared_helper.epoch']=max((t.get('dr.shared_helper.epoch',0) for t in self.bpy.data.node_groups if t!=self.tree and t.get('dr.shared_helper.key')==self.key),default=0)+1
  self.tree['dr.provenance']='Original semantic construction; public input/output contracts064/069/072, native packeddata evidence where declared; retained author credits remain in addon'
  self.tree['dr.compatibility.diagnostics']=__import__('json').dumps(list(diagnostics));return self.tree

def _build_colormask(g):
 _,green,blue=g.split(g.input('ColorMask'));colors=[g.input(n) for n in ('X_Green','Y_Blue','Z_Pink')];color=g.mix_color(g.mix_color(colors[0],colors[1],green),colors[2],blue)
 def basis(names):
  x,y,z=[g.input(n) for n in names];return g.math('ADD',g.math('ADD',x,g.math('MULTIPLY',green,g.math('SUBTRACT',y,x))),g.math('MULTIPLY',blue,g.math('SUBTRACT',z,x)))
 return g.finish({'Mask_Color':color,'Mask_Roughness':basis(('Roughness_X','Roughness_Y','Roughness_Z')),'Mask_Metal':basis(('Metal_X','Metal_Y','Metal_Z'))})

def _build_curvature(g):
 blue=g.split(g.input('Material ID Map'))[2];mask=g.id_mask(blue,g.input('Color N'),g.input('Softness'));enabled=g.math('MULTIPLY',mask,g.input('Enable'),stage='Coverage');base=g.mix_shader(g.input('Shader'),g.input('Override'),enabled);check=g.checker(g.input('Grid Scale'))
 debug1=g.mix_shader(g.input('Shader'),g.emit(g.mix_color((1,1,1,1),(.02,.02,.02,1),check)),enabled)
 palette=((.95,.2,.2),(.95,.9,.15),(.25,.85,.25),(.15,.85,.85),(.2,.4,.95),(.55,.25,.95),(.95,.55,.15),(.95,.25,.75),(.85,.85,.85));color=(0.,0.,0.)
 for n,entry in enumerate(palette,1):
  weight=g.id_mask(blue,n);term=g.combine(*(g.math('MULTIPLY',channel*.25,weight) for channel in entry));color=g.vector('ADD',color,term,stage='Light')
 debug2=g.mix_shader(g.input('Shader'),g.emit(color),g.input('Enable'));debug3=g.mix_shader(g.input('Shader'),g.emit(g.mix_color(g.input('ColorMask'),(.05,.05,.05,1),check)),g.input('Enable'))
 for index,value in enumerate((debug1,debug2,debug3),1):base=g.mix_shader(base,value,g.math('COMPARE',g.input('Debug'),index,.1),role='Preview.Mode'+str(index))
 return g.finish({'Shader':base},('Legacy ID windows calibrated at1/64 resolution; Softness widens each edge bySoftness/32. This is a compatibility preview, not native ColorN parity.','Debug palettes/checker overlays are original diagnostic visualization; two unobserved palette colors use explicit preview colors.'))

def _build_visor(g):
 # The established native opaque glass compiler is intentionally not used here.
 # This independent transparent fallback is explicitly an original preview.
 uv=g.node('ShaderNodeTexCoord','Coordinates','Visor.UV').outputs['UV'];u,v,_=g.split(uv);native_v=g.math('SUBTRACT',1,v);geometry=g.node('ShaderNodeNewGeometry','Source','Visor.Geometry');normal=geometry.outputs['Normal']
 def world_normal(color):
  r,green,b=g.split(color);encoded=g.combine(r,g.math('SUBTRACT',1,green),b);n=g.node('ShaderNodeNormalMap','Details','Visor.TangentNormal');g.put(n.inputs['Color'],encoded);n.inputs['Strength'].default_value=1.;return n.outputs['Normal']
 supplied=world_normal(g.input('Normal'));normal=g.vector('NORMALIZE',g.mix_color(normal,supplied,g.input('Use Normal'),stage='Details'))
 facing=g.sat(g.vector('DOT_PRODUCT',normal,geometry.outputs['Incoming']));width=g.math('MAXIMUM',g.math('ABSOLUTE',g.math('SUBTRACT',g.input('FresnelHigh'),g.input('FresnelLow'))),1e-6);fresnel=g.sat(g.math('DIVIDE',g.math('SUBTRACT',g.input('FresnelHigh'),facing),width));vertical=g.sat(g.math('DIVIDE',g.math('SUBTRACT',native_v,g.input('VerticalEnd')),g.math('MAXIMUM',g.math('ABSOLUTE',g.math('SUBTRACT',g.input('VerticalStart'),g.input('VerticalEnd'))),1e-6)));t=g.sat(g.math('MULTIPLY',fresnel,vertical));ab=g.mix_color(g.input('ColorA'),g.input('ColorB'),t);bc=g.mix_color(g.input('ColorB'),g.input('ColorC'),t);color=g.mix_color(ab,bc,t);color=g.mix_color(color,g.input('Base Color'),g.input('Use Base Color'))
 curve=g.split(g.input('Curvature ID'))[1];color=g.mix_color(color,g.mix_color(color,g.input('ColorC'),curve),g.input('Use Curvature'))
 du=g.math('DIVIDE',g.math('SUBTRACT',u,g.input('CircularPositionU')),g.math('MAXIMUM',g.math('ABSOLUTE',g.input('CircularScaleU')),1e-6));dv=g.math('DIVIDE',g.math('SUBTRACT',native_v,g.input('CircularPositionV')),g.math('MAXIMUM',g.math('ABSOLUTE',g.input('CircularScaleV')),1e-6));radial=g.vector('LENGTH',g.combine(du,dv,0));soft=g.math('MAXIMUM',g.input('DirtSoftness'),1e-6);circle=g.sat(g.math('DIVIDE',g.math('SUBTRACT',g.input('CircularSize'),radial),soft));gate=g.math('SUBTRACT',1,g.math('MULTIPLY',circle,g.input('MaskDirt')));dirt=g.sat(g.math('DIVIDE',g.math('SUBTRACT',g.math('MULTIPLY',g.input('Dirt Mask'),gate),g.math('SUBTRACT',1,g.input('DirtAmount'))),soft));dirt=g.math('MULTIPLY',dirt,g.input('Use Dirt'));color=g.mix_color(color,g.input('DirtColor'),dirt,stage='Wear');rough=g.math('ADD',g.input('RoughnessModifier'),g.math('MULTIPLY',g.input('Smudge'),g.input('Use Smudge')));rough=g.math('ADD',rough,g.math('MULTIPLY',dirt,g.math('SUBTRACT',g.input('DirtRoughness'),rough)));dirt_normal=world_normal(g.input('Dirt Normal'));normal=g.vector('NORMALIZE',g.mix_color(normal,dirt_normal,g.math('MULTIPLY',dirt,g.input('Use Dirt Normal')),stage='Details'))
 p=g.node('ShaderNodeBsdfPrincipled','Surface','Visor.TransparentPreview')
 for key,value in {'Base Color':color,'Roughness':g.sat(rough),'Normal':normal,'Metallic':g.input('Metallic'),'IOR':g.input('IOR'),'Transmission Weight':g.input('Transmission'),'Coat Weight':g.input('Coat Weight'),'Coat Roughness':g.input('Coat Roughness'),'Coat IOR':g.input('Coat IOR'),'Sheen Weight':g.input('Sheen Weight'),'Sheen Roughness':g.input('Sheen Roughness')}.items():g.put(p.inputs[key],value)
 p.inputs['Alpha'].default_value=1.;transparent=g.node('ShaderNodeBsdfTransparent','Coverage','Visor.Clear');shader=g.mix_shader(transparent.outputs[0],p.outputs[0],g.sat(g.input('Alpha')),role='Visor.IndependentAlpha')
 return g.finish({'BSDF':shader},('Original three-color transparent Visor preview; unavailable legacy shading/lighting transport is not reproduced or labelled native parity.','Opacity is retained as a compatibility input but specifically unsupported; measured Alpha is independent and is the only coverage control.','Fresnel/vertical palette, optional curvature/dirt/smudge/normal stages use declared original preview transfer functions; exact native opaque glass route remains separate.'))

def supports(name):return ('ArcTexturer' if name=='Arc Texturer' else name) in SUPPORTED

def ensure_group(name,bpy_module=None,*,force=False):
 name='ArcTexturer' if name=='Arc Texturer' else name
 if name not in SUPPORTED:raise UnsupportedHelperGroup('No original shader helper registered: '+str(name))
 if threading.current_thread() is not threading.main_thread():raise RuntimeError('Shared shader helpers require Blender main thread')
 require_loaded_identity(sys.modules[__name__])
 from .repository import digest
 if digest(_SCHEMA_PATH)!=_SCHEMA_SHA:raise RestartRequired('Restart Blender: shared helper interface definitions changed')
 if bpy_module is None:import bpy as bpy_module
 if name=='NormalFlipper':return _NORMAL_BUILD(name,bpy_module,force=force)
 if name=='ArcTexturer':
  from importlib import import_module
  provider=import_module('..dr_legacy_arc',__package__);tree=provider.build_group(bpy_module)
  tree['dr.shared_helper.key']=name;tree['dr.shared_helper.recipe']=RECIPE;tree['dr.legacy_group_key']=name;return tree
 if name=='WeaponTexturer':
  from importlib import import_module
  tree=import_module('..weapon',__package__).ensure_weapon_texturer_node_group()
  tree.name='DR.Shared.WeaponPreview.'+RECIPE.rsplit('-',1)[-1];tree['dr.shared_helper.key']=name;tree['dr.shared_helper.recipe']=RECIPE;tree['dr.legacy_group_key']=name;tree['dr.compatibility.diagnostics']='Existing original repository weapon-preview composer retained; no library append';return tree
 if not force:
  old=find_group(name,bpy_module)
  if old is not None:return old
 if name=='Decal Data':
  from importlib import import_module
  tree=import_module('..dr_packed_decal',__package__).build_group(bpy_module);tree['dr.shared_helper.key']=name;tree['dr.shared_helper.recipe']=RECIPE;tree['dr.legacy_group_key']=name;return tree
 g=_Graph(bpy_module,name)
 try:
  if name=='ColorMask_XYZ':return _build_colormask(g)
  if name=='CurvatureID_Override':return _build_curvature(g)
  if name=='Visor':return _build_visor(g)
  gate=g.gate(g.input('Material ID'),g.input('LayerMask'))
  return g.finish({'Gate':gate} if name=='Decal LayerMask Gate' else {'Alpha':g.math('MULTIPLY',gate,g.input('Alpha'),stage='Coverage')},('Legacy ID-band gate calibrated to public072 boundary contract; not native material-ID decoding.',))
 except BaseException:
  bpy_module.data.node_groups.remove(g.tree);raise

from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
