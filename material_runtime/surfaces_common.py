"""Small graph construction helpers. Native parameter names remain exact."""
from collections.abc import Mapping
from .core import expr, literal, MaterialPlan

def field(record, name, default=None):
    return record.get(name, default) if isinstance(record, Mapping) else getattr(record, name, default)

def group(record, name):
    return field(record, name, {}) or {}

def value(record, kind, name, default):
    return group(record, kind).get(name, default)

def scalar(record, name, default=0.0):
    return literal(value(record, 'scalars', name, default))

def color(record, name, default=(1.,1.,1.,1.)):
    v=value(record,'colors',name,default)
    if isinstance(v, Mapping): v=tuple(v.get(k,0.0) for k in ('R','G','B','A'))
    return literal(tuple(v))

def rgb(v): return expr('combine', *(channel(v,i) for i in range(3)))
def channel(v,i): return expr('channel',v,index=i)
def add(a,b): return expr('add',a,b)
def sub(a,b): return expr('subtract',a,b)
def mul(a,b): return expr('multiply',a,b)
def div(a,b): return expr('divide',a,b)
def sat(a): return expr('clamp',a,literal(0.),literal(1.))
def mix(a,b,t): return expr('mix',a,b,t)
def uv(index=0,tiling=1.): return expr('multiply',expr('uv',index=index,stage='DR.Coordinates'), literal(tiling) if isinstance(tiling,(int,float,tuple)) else tiling,stage='DR.Coordinates',name='Authored coordinate scale')
def texture(record,name,role='data',coords=None,slice=None):
    ref=group(record,'textures').get(name)
    if not ref: raise ValueError('Missing native texture parameter '+name)
    attrs={'reference':ref,'role':role,'uv':coords if coords is not None else expr('uv',index=0)}
    if slice is not None: attrs['slice']=slice
    return expr('texture',**attrs)
def normal(sample): return expr('normal_decode',sample,stage='DR.Details',name='Decode authored tangent normal')
def staged(node,stage,name): return expr(node.op,*node.args,**{**node.attrs,'stage':stage,'name':name})
def layered_value(base,crease,edge,c,e): return mix(mix(base,crease,c),edge,e)
def plan(record,family,outputs,diagnostics=(),evidence=(),settings=None):
    # Tag the existing output operation; do not add numerical identity nodes.
    tags={'normal':'DR.Details','alpha':'DR.Coverage','emission':'DR.Light','emission_strength':'DR.Light'}
    outputs={key:node if node.attrs.get('stage') else staged(node,tags.get(key,'DR.Surface'),key.replace('_',' ').title()) for key,node in outputs.items()}
    return MaterialPlan(field(record,'package',''),family,outputs,settings if settings is not None else group(record,'settings'),tuple(diagnostics),tuple(evidence))
def unfinished(record, family, stages):
    return plan(record,family,{},tuple('unsupported: '+s for s in stages),('native_parameter_schema',))

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
