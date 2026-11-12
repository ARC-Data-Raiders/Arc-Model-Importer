"""Original general clamped-mask selection via suffix contribution weights.

The sequential interpolation CONTRACT is credited to the retained selector;
its implementation is replaced by shared suffix products and balanced sums.
"""
import json
from .identity import identify,add_alias,find_node,output_socket
from .selection import semantic_channel,_mix_sockets,socket_reference
from ..common import set_node_loc

def contribution_value(zones,values,factors,default):
    active=tuple(z for z in range(1,9) if z in zones and values.get(z) is not None)
    if not active:return default
    def scale(value,weight):return tuple(v*weight for v in value) if isinstance(value,(tuple,list)) else value*weight
    def add(a,b):
        if isinstance(a,(tuple,list)):
            if not isinstance(b,(tuple,list)) or len(a)!=len(b):raise ValueError('Selection dimensions differ')
            return tuple(x+y for x,y in zip(a,b))
        return a+b
    tail=1.;terms=[]
    for z in reversed(active):
        factor=factors.get(z)
        if factor is None:factor=1. if z==active[0] else 0.
        factor=max(0.,min(1.,float(factor)));terms.append(scale(values[z],factor*tail));tail*=1.-factor
    terms.append(scale(default,tail))
    while len(terms)>1:terms=[add(terms[i],terms[i+1]) if i+1<len(terms) else terms[i] for i in range(0,len(terms),2)]
    return terms[0]

class GeneralSelector:
    def __init__(self,nodes,links,sources,factors,default,*,dtype,prefix,origin=(0.,0.),hide=False,frame=None):
        self.nodes=nodes;self.links=links;self.sources=sources;self.factors=factors;self.default=default;self.dtype=dtype;self.prefix=prefix;self.channel=semantic_channel(prefix)+'.General';self.hide=hide;self.results={};self.clamps={};self.generated=[]
        self.frame=frame or self.make('NodeFrame',self.channel+'.Selection','General authored-mask contributions')
        if frame is None:
            set_node_loc(self.frame,*origin);self.frame['dr.selection.schema']=3;self.frame['dr.selection.prefix']=prefix;self.frame['dr.selection.dtype']=dtype;self.frame['dr.selection.contract']='clamped factors; shared suffix complements; balanced contribution sum'
    def make(self,kind,name,label):
        node=self.nodes.new(kind);identify(node,name,label=label);self.generated.append(node);node['arc_layout_lock']=1
        if getattr(self,'frame',None) is not None:node.parent=self.frame
        return node
    def math(self,operation,a,b,name):
        n=self.make('ShaderNodeMath',self.channel+'.'+name,operation.title());n.operation=operation;n.use_clamp=False
        for value,target in [(a,n.inputs[0]),(b,n.inputs[1])]:
            if hasattr(value,'node'):self.links.new(value,target)
            else:target.default_value=value
        return n.outputs[0]
    def factor(self,z):
        if z in self.clamps:return self.clamps[z]
        n=self.make('ShaderNodeClamp',self.channel+'.Factor.'+str(z),'Authored factor clamped');n.inputs['Min'].default_value=0.;n.inputs['Max'].default_value=1.;self.links.new(self.factors[z],n.inputs['Value']);n['dr.general.factor_zone']=z;self.clamps[z]=n.outputs[0];return n.outputs[0]
    def product(self,source,weight,tag):
        if self.dtype=='FLOAT':return self.math('MULTIPLY',source,weight,'Term.'+tag)
        if self.dtype=='VECTOR':
            n=self.make('ShaderNodeVectorMath',self.channel+'.Term.'+tag,'Weighted vector contribution');n.operation='SCALE';self.links.new(source,n.inputs[0]);self.links.new(weight,n.inputs['Scale']);return n.outputs['Vector']
        n=self.make('ShaderNodeMix',self.channel+'.Term.'+tag,'Weighted color contribution');n.data_type='RGBA';n.blend_type='MULTIPLY';n.clamp_factor=True;n.clamp_result=False;f,a,b,out=_mix_sockets(n,'RGBA');f.default_value=1.;self.links.new(source,a);self.links.new(weight,b);return out
    def sum(self,a,b,tag):
        if self.dtype=='FLOAT':return self.math('ADD',a,b,'Sum.'+tag)
        if self.dtype=='VECTOR':
            n=self.make('ShaderNodeVectorMath',self.channel+'.Sum.'+tag,'Balanced vector sum');n.operation='ADD';self.links.new(a,n.inputs[0]);self.links.new(b,n.inputs[1]);return n.outputs['Vector']
        n=self.make('ShaderNodeMix',self.channel+'.Sum.'+tag,'Balanced color sum');n.data_type='RGBA';n.blend_type='ADD';n.clamp_factor=True;n.clamp_result=False;f,aa,bb,out=_mix_sockets(n,'RGBA');f.default_value=1.;self.links.new(a,aa);self.links.new(b,bb);return out
    def result(self,zones):
        zones=tuple(z for z in sorted(zones) if z in self.sources)
        if not zones:return self.default
        if zones in self.results:return self.results[zones][0]
        tail=None;terms=[];tag='-'.join(map(str,zones))
        for z in reversed(zones):
            factor=self.factor(z);weight=factor if tail is None else self.math('MULTIPLY',factor,tail,'Weight.'+tag+'.'+str(z));terms.append(self.product(self.sources[z],weight,tag+'.'+str(z)))
            complement=self.math('SUBTRACT',1.,factor,'Remaining.'+tag+'.'+str(z));tail=complement if tail is None else self.math('MULTIPLY',complement,tail,'Tail.'+tag+'.'+str(z))
        terms.append(self.product(self.default,tail,tag+'.Default'));level=0
        while len(terms)>1:terms=[self.sum(terms[i],terms[i+1],tag+'.'+str(level)+'.'+str(i)) if i+1<len(terms) else terms[i] for i in range(0,len(terms),2)];level+=1
        out=terms[0];out.node['dr.general.result_zones']=json.dumps(zones);out.node['dr.result_identifier']=out.identifier;add_alias(out.node,self.prefix+'_z'+str(zones[-1]));self.results[zones]=(out,out.node)
        for index,node in enumerate(self.generated):
            if node!=self.frame:
                set_node_loc(node,220.*(index%8),-150.*(index//8))
                if node.hide!=self.hide:node.hide=self.hide
        return out
    def build(self):
        out=self.result(tuple(self.sources));self.frame['dr.selection.bindings']=json.dumps({'dtype':self.dtype,'default':socket_reference(self.default),'sources':{str(z):socket_reference(s) for z,s in self.sources.items()},'factors':{str(z):socket_reference(s) for z,s in self.factors.items()}});return out

def build_general(nodes,links,zone_idx,zone_socks,*,dtype,start,prefix,x_fn=None,y=None,col0=None,y_fn=None,hide_mix=True,zone_eq=None):
    if dtype not in ('FLOAT','VECTOR','RGBA'):raise ValueError('Unsupported general selection dtype '+str(dtype))
    sources={z:zone_socks[z] for z in range(1,9) if zone_socks.get(z) is not None}
    if not sources:return start
    if start is None:raise TypeError('Nonempty typed selection requires a start socket')
    first=min(sources);origin=(float(x_fn(first)) if x_fn else float(col0 or 0.),float(y) if y is not None else float(y_fn(first)) if y_fn else 0.)
    selector=GeneralSelector(nodes,links,sources,{},start,dtype=dtype,prefix=prefix,origin=origin,hide=hide_mix)
    for z in sources:
        factor=zone_eq.get(z) if zone_eq else None
        if not zone_eq and zone_idx is not None:
            n=selector.make('ShaderNodeMath',selector.channel+'.Compare.'+str(z),'Zone equality');n.operation='COMPARE';n.inputs[1].default_value=float(z);n.inputs[2].default_value=.45;links.new(zone_idx,n.inputs[0]);factor=n.outputs[0]
        if factor is None:
            n=selector.make('ShaderNodeValue',selector.channel+'.Implicit.'+str(z),'Implicit first source');n.outputs[0].default_value=1. if z==first else 0.;factor=n.outputs[0]
        selector.factors[z]=factor
    return selector.build()

def restore_general(nodes,prefix,frame):
    bindings=json.loads(frame['dr.selection.bindings'])
    def resolve(ref):
        node=find_node(nodes,ref.get('semantic') or ref['node'])
        if node is None:raise LookupError('Missing general selector source')
        for s in node.outputs:
            if s.identifier==ref['identifier']:return s
        raise LookupError('General selector socket changed')
    selector=GeneralSelector(nodes,nodes.id_data.links,{int(z):resolve(v) for z,v in bindings['sources'].items()},{int(z):resolve(v) for z,v in bindings['factors'].items()},resolve(bindings['default']),dtype=bindings['dtype'],prefix=prefix,frame=frame)
    for node in nodes:
        if node.parent!=frame:continue
        if node.get('dr.general.factor_zone'):selector.clamps[int(node['dr.general.factor_zone'])]=node.outputs[0]
        if node.get('dr.general.result_zones'):
            zones=tuple(json.loads(node['dr.general.result_zones']));selector.results[zones]=(output_socket(node),node)
    return selector
