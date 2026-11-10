"""Original native teeth stage graph from independently bound shipping SM5 facts.

Native material response only: renderer BRDF/temporal coverage are explicit.
Pre-skin TangentZ is an input, never substituted with a shading/world normal.
"""
import math
from .core import Expr,expr,literal
from .charfx_common import scalar,color,ch,rgb,saturate,lerp,mul,add,sub,tag,plan

MASTER='/Game/Pioneer/Characters/Heads/Shared/Mouth/Materials/M_Teeth'
COLOR='/Game/Pioneer/Characters/Heads/Shared/Mouth/Textures/teeth_color_map'
NORMAL='/Game/Pioneer/Characters/Heads/Shared/Mouth/Textures/teeth_normal_map'
SNOW='/Game/Pioneer/MaterialLibrary/Character/CustomMaterials/Textures/T_CharacterSnow_M'
DEFAULT_PRIMITIVE=(0.,0.,0.)  # Native cached preview CPD0/1/2, not live gameplay.


def _value(value):
    if isinstance(value,Expr):return value
    if not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('Native teeth context values must be finite numbers or Expr')
    return literal(float(value))


def _sample(reference,role,coordinates,label):
    return expr('texture',reference=reference,role=role,uv=coordinates,stage='DR.Source',name=label)


def native_coverage(record,primitive_fade,context):
    visibility=float(record.scalars.get('Visibility',1.))
    if visibility==0.:return literal(0.),()
    if visibility==1. and isinstance(primitive_fade,(int,float)) and primitive_fade==0.:return literal(1.),()
    if 'screen_radius' not in context or 'blue_noise' not in context:
        return None,('unsupported_native_transport:teeth Visibility/proximity dither requires native viewport radius and blue-noise inputs',)
    outer=_value(context.get('fade_outer_diameter',.719121));inner=_value(context.get('fade_inner_diameter',.452))
    fade=saturate(expr('divide',sub(outer,_value(context['screen_radius'])),sub(outer,inner)))
    opacity=mul(literal(visibility),sub(literal(1.),mul(fade,_value(primitive_fade))))
    threshold=sub(add(opacity,mul(literal(.83),_value(context['blue_noise']))),literal(.8333))
    return tag(sub(literal(1.),expr('less_than',threshold,0.)),'DR.Coverage','Native supplied proximity and blue-noise coverage'),('external_native_transport:viewport radius and blue-noise sequence supplied explicitly; Blender does not reproduce Unreal temporal generation',)


def compile_teeth(record,context=None):
    context=dict(context or {})
    if record.master!=MASTER and record.package!=MASTER:
        return plan(record,'teeth',{},('unsupported_master:exact M_Teeth required',))
    if 'pre_skin_normal_z' not in context:
        return plan(record,'teeth',{},('missing_native_source:raw pre-skin TangentZ.z required; world/shading normal is not equivalent',))
    primitive=context.get('primitive_data',DEFAULT_PRIMITIVE)
    if not isinstance(primitive,(tuple,list)) or len(primitive)!=3:raise ValueError('Native teeth primitive_data must contain CPD0 fade, CPD1 snow, CPD2 wet')
    cp=[_value(value) for value in primitive]
    alpha,coverage_diagnostics=native_coverage(record,primitive[0],context)
    if alpha is None:return plan(record,'teeth',{},coverage_diagnostics)
    coordinates=expr('uv',index=0)
    packed=_sample(NORMAL,'normal',coordinates,'Native teeth packed RG')
    color_sample=_sample(COLOR,'color',coordinates,'Native teeth authored RGB')
    snow=_sample(SNOW,'data',expr('vector_scale',coordinates,scalar(record,'SnowMaskTiling',10.)),'Native character snow RGB')
    x=sub(mul(ch(packed,0),2.),1.);y=sub(mul(ch(packed,1),2.),1.)
    decoded=expr('combine',x,y,expr('sqrt',expr('max',sub(literal(1.),add(mul(x,x),mul(y,y))),0.)))
    z01=add(mul(_value(context['pre_skin_normal_z']),.5),.5)
    h=tag(add(mul(ch(snow,1),scalar(record,'Snow_Overspray',.2)),z01),'DR.Details','Native tangent elevation and overspray')
    height_squared=mul(h,h)
    snow_limit=sub(literal(1.),mul(scalar(record,'MaxSnowAmount',.5),cp[1]))
    snow_mask=tag(saturate(mul(sub(add(height_squared,mul(h,ch(snow,0))),mul(mul(snow_limit,snow_limit),2.)),12.5)),'DR.LayerInputs','Native teeth snow coverage')
    wet_limit=sub(literal(1.),mul(scalar(record,'MaxWetAmount',.6),cp[2]))
    wet=tag(saturate(mul(sub(height_squared,mul(mul(wet_limit,wet_limit),1.15)),3.333333)),'DR.LayerInputs','Native teeth wet coverage')
    perturbation=add(sub(ch(snow,0),.5),mul(sub(ch(snow,2),.5),.6))
    snow_normal=expr('combine',perturbation,mul(perturbation,-1.),1.)
    final_normal=tag(expr('normalize',lerp(decoded,snow_normal,mul(snow_mask,.95))),'DR.Details','Native teeth normal and snow perturbation')
    base=tag(saturate(lerp(rgb(color_sample),color(record,'SnowColor',(.8125,.8125,.8125)),snow_mask)),'DR.Surface','Native teeth RGB and snow color')
    wet_rough=saturate(sub(literal(.5),mul(wet,scalar(record,'WetGloss',.7))))
    rough=tag(lerp(wet_rough,literal(1.),snow_mask),'DR.Surface','Native teeth wet and snow roughness')
    selection=tuple(record.colors.get('SelectionColor',(0.,0.,0.,0.)))
    if len(selection)!=4:raise ValueError('Native SelectionColor requires its authored alpha')
    emission=tag(mul(literal(selection[:3]),selection[3]),'DR.Light','Native editor selection emission')
    diagnostics=list(coverage_diagnostics)
    if 'primitive_data' not in context:diagnostics.append('external_runtime_state:native cached preview CPD fade/snow/wet=(0,0,0); live game primitive values are not imported')
    diagnostics.extend(('renderer_difference:native clearcoat GBuffer controls mapped to Blender Principled; Unreal BRDF parity not claimed','native_encoding:raw material roughness retained; Unreal deferred GBuffer254/255 cap is a separate transport encoding'))
    return plan(record,'teeth',{'base_color':base,'roughness':rough,'metallic':literal(0.),'specular':literal(.5),'normal':final_normal,'alpha':alpha,'coat':literal(1.),'coat_roughness':literal(.1),'emission':emission,'emission_strength':literal(1.)},diagnostics,blend='masked',cutoff=.3333,two_sided=False,native_teeth_revision='shipping-sm5-v1')

# Disk replacements must not masquerade as executing new Python bodies.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
