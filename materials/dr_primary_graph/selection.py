"""Balanced one-hot selection, with typed Mix semantics and retained fallback.

This is a graph-composition change. Source values, mask factors, texture data,
normal math, wear, authored defaults, and working decal operations stay intact.
"""
from dataclasses import dataclass
import json
import math
import re
from .identity import identify,add_alias,output_socket,assign,write_locations

@dataclass(frozen=True)
class MaskProof:
    selector: object
    center: float
    width: float

def disjoint_one_hot(proofs):
    values=list(proofs)
    if not values:return False
    if any(v.selector!=values[0].selector or not math.isfinite(v.center) or not math.isfinite(v.width) or v.width<0 for v in values):return False
    ordered=sorted(values,key=lambda v:v.center)
    return all(b.center-a.center>a.width+b.width for a,b in zip(ordered,ordered[1:]))

def balanced_value(zones,values,weights,default):
    """Pure model for tests; accepts only exact zero/one disjoint weights."""
    zones=tuple(z for z in sorted(zones) if z in values)
    if any(weights[z] not in (0.,1.) for z in zones) or sum(weights[z] for z in zones)>1:
        raise ValueError('Balanced selection requires proven one-hot weights')
    def lerp(a,b,t):
        if isinstance(a,(tuple,list)):
            if not isinstance(b,(tuple,list)) or len(a)!=len(b):raise ValueError('Selection dimensions differ')
            return tuple(lerp(x,y,t) for x,y in zip(a,b))
        return (1-t)*a+t*b
    def visit(part):
        if not part:return default
        if len(part)==1:return values[part[0]]
        middle=len(part)//2;left,right=part[:middle],part[middle:]
        return lerp(visit(left),visit(right),sum(weights[z] for z in right))
    return lerp(default,visit(zones),sum(weights[z] for z in zones))

def _socket_key(socket):
    try:return socket.as_pointer()
    except AttributeError:return id(socket)

class IncomingLinks:
    """Input-socket links per tree for proofs over a graph that is not edited meanwhile.

    NodeSocket.links rescans every link of the tree on each access.
    """
    def __init__(self):self.trees={}
    def __call__(self,socket):
        if socket.is_output:return tuple(socket.links)
        tree=socket.id_data;index=self.trees.get(tree.as_pointer())
        if index is None:
            index=self.trees[tree.as_pointer()]={}
            for link in tree.links:index.setdefault(link.to_socket.as_pointer(),[]).append(link)
        return tuple(index.get(socket.as_pointer(),()))

def _linked_source(socket,links_of=None):
    links=list(socket.links) if links_of is None else list(links_of(socket))
    return links[0].from_socket if len(links)==1 else None

def _unrouted(socket,links_of=None):
    seen=set()
    while socket is not None and socket.node.bl_idname=='NodeReroute':
        key=_socket_key(socket)
        if key in seen:return None
        seen.add(key);socket=_linked_source(socket.node.inputs[0],links_of)
    return socket

def _constant(socket):
    if socket.is_linked:return None
    # Unlinked does not imply immutable: animated/driven compare constants
    # would invalidate the one-hot proof later in the same graph.
    owner=getattr(socket,'id_data',None);animation=getattr(owner,'animation_data',None)
    if animation is not None:
        try:path=socket.path_from_id('default_value')
        except (AttributeError,TypeError):return None
        if any(curve.data_path==path for curve in animation.drivers):return None
        action=animation.action
        if action is not None:
            curves=getattr(action,'fcurves',None)
            if curves is None or any(curve.data_path==path for curve in curves):return None
    try:return float(socket.default_value)
    except (TypeError,ValueError,AttributeError):return None

def socket_proof(socket,links_of=None):
    """Prove direct COMPARE or the inspected single-COMPARE group structure.

    Names/labels alone never establish one-hot eligibility. Match/epsilon inputs
    must be unlinked constants and all masks must share the same selector.
    """
    socket=_unrouted(socket,links_of)
    if socket is None:return None
    node=socket.node
    if node.bl_idname=='ShaderNodeMath' and node.operation=='COMPARE':
        source=_unrouted(_linked_source(node.inputs[0],links_of),links_of);center=_constant(node.inputs[1]);width=_constant(node.inputs[2])
        if source is not None and center is not None and width is not None:return MaskProof(_socket_key(source),center,width)
    if node.bl_idname!='ShaderNodeGroup' or node.node_tree is None:return None
    tree=node.node_tree
    outputs=[n for n in tree.nodes if n.bl_idname=='NodeGroupOutput' and getattr(n,'is_active_output',True)]
    if len(outputs)!=1:return None
    output_input=outputs[0].inputs.get(socket.name)
    if output_input is None:return None
    comparison=_linked_source(output_input,links_of)
    if comparison is None or comparison.node.bl_idname!='ShaderNodeMath' or comparison.node.operation!='COMPARE':return None
    cmp=comparison.node;a=_linked_source(cmp.inputs[0],links_of);b=_linked_source(cmp.inputs[1],links_of);width=_constant(cmp.inputs[2])
    if a is None or b is None or width is None:return None
    if a.node.bl_idname!='NodeGroupInput' or b.node!=a.node:return None
    selector_input=node.inputs.get(a.name);match_input=node.inputs.get(b.name)
    if selector_input is None or match_input is None:return None
    source=_unrouted(_linked_source(selector_input,links_of),links_of);center=_constant(match_input)
    if source is None or center is None:return None
    return MaskProof(_socket_key(source),center,width)

_CHANNELS={'GT_mux_col':'DR.Surface.BaseColor','GT_mux_rough':'DR.Surface.Roughness','GT_mux_metal':'DR.Surface.Metallic',
'GT_mux_nBase':'DR.Details.BaseNormal','GT_mux_nCrease':'DR.Details.CreaseNormal','GT_mux_nEdge':'DR.Details.EdgeNormal',
'GT_mux_Cloth':'DR.Light.Cloth','GT_mux_HueStrength':'DR.Surface.HueVariation','GT_mux_HueTile':'DR.Coordinates.HueTiling',
'GT_mux_nMedium':'DR.Details.MediumNormal','GT_mux_MediumFactor':'DR.Details.MediumMask','GT_mux_crease':'DR.Wear.Crease','GT_mux_edge':'DR.Wear.Edge'}

def semantic_channel(prefix):
    match=re.fullmatch(r'GT_mux_(Base|Crease|Edge)(Specular|Metallicity)',prefix)
    if match:return ('DR.Light.Specular.' if match.group(2)=='Specular' else 'DR.Surface.Metallic.')+match.group(1)
    return _CHANNELS.get(prefix,'DR.Surface.'+re.sub('[^A-Za-z0-9]+','.',prefix.removeprefix('GT_mux_').removeprefix('GT_')).strip('.'))

def _mix_sockets(node,dtype):
    kind={'FLOAT':'VALUE','VECTOR':'VECTOR','RGBA':'RGBA'}[dtype]
    def pick(collection,name,target):
        matches=[s for s in collection if s.name==name and (s.type==target or target=='RGBA' and s.type=='COLOR')]
        if len(matches)!=1:raise RuntimeError('Ambiguous '+dtype+' '+name+' socket')
        return matches[0]
    return pick(node.inputs,'Factor','VALUE'),pick(node.inputs,'A',kind),pick(node.inputs,'B',kind),pick(node.outputs,'Result',kind)

def socket_reference(socket):
    return {'node':socket.node.name,'semantic':socket.node.get('dr.semantic_id',''),
            'identifier':socket.identifier,'index':list(socket.node.outputs).index(socket)}

def _shared_weights(nodes):
    """Per-tree weight cache for the current operation build, or None outside one."""
    from ..dr_primary_operations import _BUILD
    context=_BUILD.get()
    return None if context is None else context.state(nodes).setdefault('shared_weights',{})

class BalancedSelector:
    def __init__(self,nodes,links,sources,factors,default,*,dtype,prefix,origin=(0.,0.),hide=False):
        self.nodes=nodes;self.links=links;self.sources=sources;self.factors=factors;self.default=default
        self.dtype=dtype;self.prefix=prefix;self.channel=semantic_channel(prefix);self.hide=hide
        self.results={};self.raw_results={};self.weights={};self.generated=[]
        # Locations wait for build(): each write would re-run the whole tree update.
        self.placed={}
        self.frame=self.make('NodeFrame',self.channel+'.Selection','Select authored zone')
        self.place(self.frame,origin);self.frame.use_custom_color=True;self.frame.color=(.12,.22,.29)
        self.frame['dr.selection.schema']=2;self.frame['dr.selection.prefix']=prefix;self.frame['dr.selection.zones']=json.dumps(sorted(sources))
        self.frame['dr.selection.contract']='one-hot shared selector; raw balanced tree plus one unmatched/default guard'
    def make(self,kind,name,label):
        node=self.nodes.new(kind);self.generated.append(node);identify(node,name,label=label)
        node['arc_layout_lock']=1
        return node
    def place(self,node,location):
        # Selectors restored from a saved tree have no pending batch.
        placed=getattr(self,'placed',None)
        if placed is None:node.location=location
        else:placed[node.as_pointer()]=location
    def weight(self,zones):
        zones=tuple(zones)
        if len(zones)==1:return self.factors[zones[0]]
        if zones in self.weights:return self.weights[zones]
        # Selectors fed by the same zone masks share each weight sum: it depends
        # only on those factor sockets, and every node added slows later writes.
        shared=_shared_weights(self.nodes);key=tuple(_socket_key(self.factors[z]) for z in zones)
        if shared is not None and key in shared:
            try:shared[key].node
            except ReferenceError:del shared[key]
            else:self.weights[zones]=shared[key];return shared[key]
        middle=len(zones)//2;left=self.weight(zones[:middle]);right=self.weight(zones[middle:])
        tag='-'.join(map(str,zones));node=self.make('ShaderNodeMath',self.channel+'.Weight.'+tag,'One-hot subtree weight')
        node['dr.selection.weight_zones']=json.dumps(zones)
        assign(node,'operation','ADD');node.parent=self.frame;node.hide=True;self.place(node,(180.,-110.*(zones[0]-1)))
        self.links.new(left,node.inputs[0]);self.links.new(right,node.inputs[1]);self.weights[zones]=node.outputs[0]
        if shared is not None:shared[key]=node.outputs[0]
        return node.outputs[0]
    def mix(self,zones,a,b,factor,kind):
        tag='-'.join(map(str,zones));node=self.make('ShaderNodeMix',self.channel+'.'+kind.title()+'.'+tag,
            'Default unless zone matched' if kind=='guard' else 'Balanced zone selection')
        assign(node,'data_type',self.dtype);assign(node,'blend_type','MIX');assign(node,'clamp_factor',True);node.parent=self.frame;node.hide=self.hide
        depth=math.ceil(math.log2(len(zones)))+(1 if kind=='guard' else 0)
        self.place(node,(360.*depth,-110.*(sum(zones)/len(zones)-1)))
        f,aa,bb,out=_mix_sockets(node,self.dtype)
        self.links.new(factor,f);self.links.new(a,aa);self.links.new(b,bb)
        node['dr.result_identifier']=out.identifier;node['dr.selection.zone_set']=json.dumps(zones)
        node['dr.selection.prefix']=self.prefix;node['dr.selection.dtype']=self.dtype;node['dr.selection.kind']=kind
        return out,node
    def raw_result(self,zones):
        zones=tuple(zones)
        if len(zones)==1:return self.sources[zones[0]]
        if zones in self.raw_results:return self.raw_results[zones][0]
        middle=len(zones)//2;left,right=zones[:middle],zones[middle:]
        result=self.mix(zones,self.raw_result(left),self.raw_result(right),self.weight(right),'raw')
        self.raw_results[zones]=result
        return result[0]
    def result(self,zones):
        zones=tuple(zones)
        if not zones:return self.default
        if zones in self.results:return self.results[zones][0]
        result=self.mix(zones,self.default,self.raw_result(zones),self.weight(zones),'guard')
        self.results[zones]=result
        present=tuple(z for z in sorted(self.sources) if z<=zones[-1])
        if zones==present:add_alias(result[1],self.prefix+'_z'+str(zones[-1]))
        return result[0]
    def build(self):
        try:
            zones=tuple(sorted(self.sources));out=self.result(zones)
            self.frame['dr.selection.bindings']=json.dumps({'dtype':self.dtype,'default':socket_reference(self.default),
                'sources':{str(z):socket_reference(s) for z,s in self.sources.items()},
                'factors':{str(z):socket_reference(s) for z,s in self.factors.items()}})
            out.node['dr.selection.root']=True
            out.node['dr.selection.proof']='shared selector, non-overlapping COMPARE intervals'
            return out
        finally:
            placed,self.placed=self.placed,None
            write_locations(self.nodes,placed)

def build_balanced(nodes,links,zone_idx,zone_socks,*,dtype,start,prefix,x_fn=None,y=None,col0=None,y_fn=None,hide_mix=True,zone_eq=None):
    """Return (socket, reason). No graph mutation occurs for ineligible masks."""
    if dtype not in ('FLOAT','VECTOR','RGBA'):return None,'unsupported selection dtype'
    sources={z:zone_socks[z] for z in range(1,9) if zone_socks.get(z) is not None}
    if len(sources)<2:return None,'fewer than two eligible sources'
    factors={};proofs=[];links_of=IncomingLinks()
    if zone_eq:
        for z in sources:
            factor=zone_eq.get(z)
            if factor is None:return None,'partial shared mask map'
            proof=socket_proof(factor,links_of)
            if proof is None or proof.center!=float(z):return None,'unproven or modified shared mask'
            factors[z]=factor;proofs.append(proof)
        if not disjoint_one_hot(proofs):return None,'overlapping or unrelated selector masks'
    elif zone_idx is None:return None,'implicit first-source fallback retained'
    origin=(float(x_fn(min(sources))) if x_fn else float(col0 or 0.),float(y) if y is not None else float(y_fn(min(sources))) if y_fn else 0.)
    selector=BalancedSelector(nodes,links,sources,factors,start,dtype=dtype,prefix=prefix,origin=origin,hide=hide_mix)
    if not zone_eq:
        for z in sources:
            node=selector.make('ShaderNodeMath',selector.channel+'.Mask.'+str(z),'Native zone compare '+str(z))
            node.operation='COMPARE';node.inputs[1].default_value=float(z);node.inputs[2].default_value=.45
            node.parent=selector.frame;node.hide=True;selector.place(node,(-260.,-110.*(z-1)));links.new(zone_idx,node.inputs[0]);factors[z]=node.outputs[0]
    # Keep generated nodes accessible for explicit lazy debug-prefix resolution.
    return selector.build(),selector
