"""Small independent IR builders; no global color conversion or mutable resources."""
from .core import expr, literal, MaterialPlan

def scalar(record,name,default=0.0):
    return literal(record.scalars[name] if name in record.scalars else default)
def color(record,name,default=(1.,1.,1.)):
    value=record.colors[name] if name in record.colors else default
    return literal(tuple(value)[:3])
def ch(value,index): return expr('channel',value,index=index)
def rgb(value): return expr('combine',*(ch(value,i) for i in range(3)))
def saturate(value): return expr('clamp',value,0.,1.)
def lerp(a,b,t): return expr('mix',a,b,t)
def mul(a,b): return expr('multiply',a,b)
def add(a,b): return expr('add',a,b)
def sub(a,b): return expr('subtract',a,b)
def uv(scale=1.,index=0,offset=(0.,0.,0.)):
    return expr('vector_add',expr('vector_scale',expr('uv',index=index),scale),literal(offset))
def texture(record,name,role='data',coords=None,default=(0.,0.,0.,1.)):
    reference=record.textures.get(name)
    if not reference:return literal(default)
    return expr('texture',reference=reference,role=role,uv=coords if coords is not None else expr('uv',index=0),parameter=name)
def normal(record,name,coords=None):
    return expr('normal_decode',texture(record,name,'normal',coords,default=(.5,.5,1.,1.)))
def plan(record,family,outputs,diagnostics=(),**settings):
    return MaterialPlan(record.package,family,outputs,{**dict(record.settings),**settings},tuple(diagnostics),tuple(record.provenance))
def mapped_channel(record,context,role):
    """Only verified binding tables may declare ambiguous packed-channel roles."""
    binding=(context or {}).get('channel_roles',{}).get(role)
    if binding is None:return None
    if not binding.get('evidence'):raise ValueError('channel role requires source evidence: '+role)
    return ch(texture(record,binding['parameter'],binding.get('role','data')),int(binding['channel']))
def unresolved(record,names):
    return tuple('unsupported_native_stage:'+n for n in names if (n in record.textures and bool(record.textures[n])) or (n in record.scalars and record.scalars[n]!=0.) or record.switches.get(n,False))

def tag(node,stage,name):
    """Attach original semantic graph organization without inserting shader math."""
    return expr(node.op,*node.args,**{**dict(node.attrs),'stage':stage,'name':name})

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
