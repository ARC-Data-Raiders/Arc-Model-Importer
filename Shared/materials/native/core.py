"""Independent immutable material records and a pure expression evaluator."""
from dataclasses import dataclass, field
from types import MappingProxyType
from collections.abc import Mapping
import math


# Exact JSON types skip the ABC isinstance checks (catalog load walks ~3M values).
_SCALARS = (str, int, float, bool, type(None))

def freeze(value):
    kind = type(value)
    if kind in _SCALARS:
        return value
    if kind is dict or isinstance(value, Mapping):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple([freeze(v) for v in value])
    return value

def preview_parameter_definition(attrs):
    key=attrs.get('key')
    if not isinstance(key,str) or not key.strip(): raise ValueError('Preview parameter requires a nonempty semantic key')
    value=attrs.get('default',1.)
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value): raise ValueError('Preview parameter value must be a finite scalar number')
    return key,float(value)

@dataclass(frozen=True)
class Expr:
    op: str
    args: tuple = ()
    attrs: Mapping = field(default_factory=dict)
    def __post_init__(self):
        if self.op=='preview_parameter':
            if self.args: raise ValueError('Preview parameter uses key/default attributes only')
            preview_parameter_definition(self.attrs)
        object.__setattr__(self, 'args', freeze(self.args))
        object.__setattr__(self, 'attrs', freeze(self.attrs))
    def to_dict(self):
        return serialize(self)

def expr(op, *args, **attrs):
    return Expr(op, args, attrs)

def literal(value):
    return expr('literal', value)

def serialize(value):
    kind = type(value)
    if kind in _SCALARS:
        return value
    if kind is MappingProxyType or kind is dict:
        return {str(k): serialize(v) for k, v in value.items()}
    if kind is tuple or kind is list:
        return [serialize(v) for v in value]
    if isinstance(value, Expr):
        return {'op': value.op, 'args': serialize(value.args), 'attrs': serialize(value.attrs)}
    if isinstance(value, Mapping):
        return {str(k): serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(v) for v in value]
    return value

@dataclass(frozen=True)
class MaterialRecord:
    package: str
    master: str = ''
    scalars: Mapping = field(default_factory=dict)
    colors: Mapping = field(default_factory=dict)
    textures: Mapping = field(default_factory=dict)
    switches: Mapping = field(default_factory=dict)
    settings: Mapping = field(default_factory=dict)
    scopes: Mapping = field(default_factory=dict)
    provenance: tuple = ()
    def __post_init__(self):
        for key in ('scalars','colors','textures','switches','settings','scopes','provenance'):
            object.__setattr__(self, key, freeze(getattr(self,key)))

@dataclass(frozen=True)
class MaterialPlan:
    package: str
    family: str
    outputs: Mapping
    settings: Mapping = field(default_factory=dict)
    diagnostics: tuple = ()
    evidence: tuple = ()
    def __post_init__(self):
        for key in ('outputs','settings','diagnostics','evidence'):
            object.__setattr__(self,key,freeze(getattr(self,key)))

def _vec(x):
    return isinstance(x,(tuple,list))

def _map(fn, *values):
    vectors=[x for x in values if _vec(x)]
    if not vectors:
        return fn(*values)
    n=len(vectors[0])
    if any(len(x)!=n for x in vectors):
        raise ValueError('vector dimensions differ')
    return tuple(fn(*(x[i] if _vec(x) else x for x in values)) for i in range(n))

def _unit(x):
    length=math.sqrt(sum(v*v for v in x))
    return tuple(v/length for v in x) if length else tuple(0.0 for _ in x)

def _coordinate_expr(node,memo):
    if not isinstance(node,Expr): return False
    prior=memo.get(id(node))
    if prior is not None and prior[0] is node: return prior[1]
    if node.op=='uv': result=True
    elif node.op in ('texture','normal_decode','rnm','geometry_normal','incoming','object_position','object_normal','attribute','dot','cross'): result=False
    else: result=any(_coordinate_expr(value,memo) for value in node.args)
    memo[id(node)]=(node,result); return result

def _evaluate_impl(node, inputs=None, walk=None):
    """Evaluate linear numeric IR. Textures require an explicit sampling callback."""
    inputs={} if inputs is None else inputs
    if not isinstance(node,Expr):
        return node
    op=node.op; a=[walk(v) for v in node.args]; at=node.attrs
    if walk.is_coordinate(node) and {len(v) for v in a if _vec(v)}=={2,3}:
        a=[tuple(v)+(0.,) if _vec(v) and len(v)==2 else v for v in a]
    if op=='literal': return a[0]
    if op=='preview_parameter':
        key,default=preview_parameter_definition(at)
        parameters=inputs.get('preview_parameters',{})
        if not isinstance(parameters,Mapping): raise TypeError('preview_parameters must be a mapping')
        return preview_parameter_definition({'key':key,'default':parameters.get(key,default)})[1]
    if op=='texture':
        sampler=inputs.get('texture')
        if sampler is None: raise KeyError('texture sampler required')
        uv=walk(at.get('uv', node.args[0] if node.args else expr('uv')))
        return sampler(at['reference'], uv, at)
    if op in ('uv','geometry_normal','incoming','tangent_view','backfacing','object_position','object_normal','object_axis_lengths','camera_distance','camera_depth','attribute','time'):
        key=at.get('name',at.get('index',0))
        value=inputs[op]
        return value(key) if callable(value) else (value[key] if isinstance(value,Mapping) else value)
    if op=='channel': return a[0][at.get('index',a[1] if len(a)>1 else 0)]
    if op=='combine': return tuple(a)
    if op in ('add','vector_add'): return _map(lambda x,y:x+y,*a)
    if op in ('subtract','vector_subtract'): return _map(lambda x,y:x-y,*a)
    if op in ('multiply','vector_scale'): return _map(lambda x,y:x*y,*a)
    if op=='divide': return _map(lambda x,y:x/y if y else 0.0,*a)
    if op=='power': return _map(lambda x,y: math.pow(max(x,0.0),y),*a)
    if op in ('min','max'): return _map(min if op=='min' else max,*a)
    if op=='clamp': return _map(lambda x,lo,hi:min(max(x,lo),hi),a[0],a[1] if len(a)>1 else at.get('min',0.0),a[2] if len(a)>2 else at.get('max',1.0))
    if op=='mix': return _map(lambda x,y,t:x*(1-t)+y*t,*a)
    if op=='less_than': return _map(lambda x,y:float(x<y),*a)
    if op=='greater_than': return _map(lambda x,y:float(x>y),*a)
    if op=='compare': return _map(lambda x,y:float(abs(x-y)<=at.get('epsilon',1e-5)),*a)
    unary={'abs':abs,'floor':math.floor,'fract':lambda x:x-math.floor(x),'sqrt':lambda x:math.sqrt(max(x,0)),'sin':math.sin,'cos':math.cos}
    if op in unary: return _map(unary[op],a[0])
    if op=='dot': return sum(x*y for x,y in zip(a[0],a[1],strict=True))
    if op=='cross':
        x,y=a
        return (x[1]*y[2]-x[2]*y[1],x[2]*y[0]-x[0]*y[2],x[0]*y[1]-x[1]*y[0])
    if op=='normalize': return _unit(a[0])
    if op=='normal_decode':
        x,y=2*a[0][0]-1,2*a[0][1]-1
        if at.get('flip_y',False): y=-y
        z=math.sqrt(max(0,1-x*x-y*y)) if at.get('reconstruct_z',True) else 2*a[0][2]-1
        return _unit((x,y,z))
    if op=='rnm':
        x,y=a; t=(x[0],x[1],x[2]+1); u=(-y[0],-y[1],y[2])
        d=sum(i*j for i,j in zip(t,u))
        if abs(t[2])<1e-12: return _unit(x)
        return _unit(tuple(t[i]*d/t[2]-u[i] for i in range(3)))
    raise NotImplementedError('Unsupported IR op: '+op)

def evaluate(node, inputs=None):
    """Evaluate each shared expression once for one immutable input context."""
    memo={}; coordinate_memo={}
    def walk(value):
        if not isinstance(value,Expr): return value
        previous=memo.get(id(value))
        if previous is not None and previous[0] is value: return previous[1]
        result=_evaluate_impl(value,inputs,walk)
        memo[id(value)]=(value,result)
        return result
    walk.is_coordinate=lambda value:_coordinate_expr(value,coordinate_memo)
    return walk(node)

def expression_fingerprint(node, memo=None):
    """Hash a shared expression DAG in linear graph work, without expanding it to a tree.

    A caller-owned memo retains expression objects alongside their digests, preventing
    object-id reuse and allowing all outputs of a plan to share hashing work.
    """
    import hashlib
    memo={} if memo is None else memo
    def token(value):
        if isinstance(value,Expr):
            prior=memo.get(id(value))
            if prior is not None and prior[0] is value: return prior[1]
            result=hashlib.sha256(b'E'+token(value.op)+token(value.args)+token(value.attrs)).digest()
            memo[id(value)]=(value,result)
            return result
        if isinstance(value,Mapping):
            return hashlib.sha256(b'M'+b''.join(token(k)+token(v) for k,v in sorted(value.items(),key=lambda item:repr(item[0])))).digest()
        if isinstance(value,(tuple,list)):
            return hashlib.sha256(b'L'+b''.join(token(v) for v in value)).digest()
        return hashlib.sha256((type(value).__name__+':'+repr(value)).encode('utf8')).digest()
    return token(node).hex()


# FMaterialInstanceBasePropertyOverrides: override flag suffix -> (value member, struct default).
BASE_PROPERTY_OVERRIDES={'OpacityMaskClipValue':('OpacityMaskClipValue',1./3.),'BlendMode':('BlendMode','EBlendMode::BLEND_Opaque'),
    'ShadingModel':('ShadingModel','EMaterialShadingModel::MSM_DefaultLit'),'DitheredLODTransition':('DitheredLODTransition',False),
    'CastDynamicShadowAsMasked':('bCastDynamicShadowAsMasked',False),'TwoSided':('TwoSided',False),'bIsThinSurface':('bIsThinSurface',False),
    'OutputTranslucentVelocity':('bOutputTranslucentVelocity',False),'bHasPixelAnimation':('bHasPixelAnimation',False),
    'bEnableTessellation':('bEnableTessellation',False),'MaxWorldPositionOffsetDisplacement':('MaxWorldPositionOffsetDisplacement',0.),
    'CompatibleWithLumenCardSharing':('bCompatibleWithLumenCardSharing',False)}


def base_property_overrides(overrides,snapshot=False):
    """The base properties a material instance overrides, as UE applies them.

    Only an enabled bOverride_ flag makes a value authoritative: UE copies the parent's current values into the
    struct for the disabled ones, and an enabled override equal to the struct default (TwoSided false, BLEND_Opaque)
    is not serialized at all, so it reads as the default rather than the parent's value. snapshot=True also keeps
    the copied parent values, for readers that never merge the parent material.
    """
    overrides=overrides if isinstance(overrides,Mapping) else {}
    result={k:v for k,v in overrides.items() if not str(k).startswith('bOverride_')} if snapshot else {}
    for flag,(name,default) in BASE_PROPERTY_OVERRIDES.items():
        if overrides.get('bOverride_'+flag): result[name]=overrides.get(name,default)
    return result


def normalize_material_settings(settings):
    """Normalize explicit plan and native flags without treating false/zero as absent.

    Lower-case plan keys win over native aliases. Unsupported blend physics are
    explicit; callers must not silently substitute opaque or alpha blending.
    """
    values=dict(settings); diagnostics=[]
    def first(names,default):
        for name in names:
            if name in settings: return settings[name]
        return default
    sided=first(('two_sided','TwoSided','bTwoSided'),False)
    if isinstance(sided,str):
        if sided.lower() not in ('true','false','0','1'): raise ValueError('Invalid two-sided flag: '+repr(sided))
        sided=sided.lower() in ('true','1')
    elif sided not in (False,True,0,1): raise ValueError('Invalid two-sided flag: '+repr(sided))
    values['two_sided']=bool(sided)
    raw=first(('blend','BlendMode','blend_mode'),0)
    names={0:'opaque',1:'masked',2:'translucent',3:'additive',4:'modulate',5:'alpha_composite',6:'alpha_holdout',7:'translucent_colored'}
    if isinstance(raw,bool): mode='unsupported'
    elif isinstance(raw,int): mode=names.get(raw,'unsupported')
    else:
        token=str(getattr(raw,'name',raw)).split('::')[-1].split('.')[-1].lower()
        if token.startswith('blend_'): token=token[6:]
        aliases={'opaque':'opaque','masked':'masked','mask':'masked','translucent':'translucent','blend':'translucent','alpha_blend':'translucent','transparent':'translucent','translucentgreytransmittance':'translucent','translucentcoloredtransmittance':'translucent_colored','coloredtransmittanceonly':'modulate','additive':'additive','modulate':'modulate','alphacomposite':'alpha_composite','alpha_composite':'alpha_composite','alphaholdout':'alpha_holdout','alpha_holdout':'alpha_holdout'}
        mode=aliases.get(token,'unsupported')
    values['blend']=mode
    values['blend_supported']=mode in ('opaque','masked','translucent','translucent_colored')
    values['cutoff']=float(first(('cutoff','OpacityMaskClipValue','alpha_threshold'),1./3.))
    if not math.isfinite(values['cutoff']): raise ValueError('Opacity cutoff must be finite')
    values['render_method']='DITHERED' if mode in ('masked','translucent','translucent_colored') else None
    if not values['blend_supported']: diagnostics.append('unsupported_blend_physics:'+str(raw))
    if mode=='translucent_colored': diagnostics.append('colored_transmittance_requires_authored_transmission; engine-specific scattering is not reconstructed')
    return freeze(values),tuple(diagnostics)

def ignores_alpha_output(settings):
    """Explicit native/plan opaque blending ignores opacity; unset generic mode stays compatible."""
    normalized,_=normalize_material_settings(settings)
    return any(key in settings for key in ('blend','BlendMode','blend_mode')) and normalized['blend']=='opaque'

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
