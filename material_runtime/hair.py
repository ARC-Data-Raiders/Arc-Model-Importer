"""Independent card hair/eyelash graphs with explicit unverified attribute channels."""
from .core import expr,literal
from .charfx_common import *

def compile_hair(record,context=None):
    diagnostics=['portable_hair:Principled anisotropy does not reproduce Unreal Hair scattering']
    strand=mapped_channel(record,context,'hair_strand_id')
    root=mapped_channel(record,context,'hair_root')
    if strand is None:
        strand=literal(.5);diagnostics.append('unsupported_native_stage:AttributeMap strand-id channel not proven')
    if root is None:
        root=literal(1.);diagnostics.append('unsupported_native_stage:AttributeMap root channel not proven')
    variation=tag(saturate(mul(strand,scalar(record,'ColorVariationInfluence',1.))),'DR.LayerInputs','Strand color variation')
    c=lerp(color(record,'ColorA',(.0625,.025636,.014969)),color(record,'ColorB',(.03125,.025803,.024187)),variation)
    if record.switches.get('UseSecondaryColor',bool(record.scalars.get('UseSecondaryColor',0.))):
        secondary=mapped_channel(record,context,'hair_secondary')
        if secondary is None:diagnostics.append('unsupported_native_stage:secondary hair mask')
        else:c=lerp(c,lerp(color(record,'SecondaryColor'),color(record,'SecondaryColorB'),variation),secondary)
    c=mul(c,lerp(color(record,'Root Multiply',(.380208,.323353,.272108)),literal((1.,1.,1.)),saturate(root)))
    if 'GlobalHairColor' in record.textures:
        coords=uv(scalar(record,'ColorMap_Tiling',1.))
        c=lerp(c,mul(c,rgb(texture(record,'GlobalHairColor','color',coords))),scalar(record,'ColorMapBlend',1.))
    coverage=ch(texture(record,'Coverage',default=(1.,1.,1.,1.)),0)
    alpha=saturate(mul(expr('power',saturate(coverage),scalar(record,'DitherPower',1.)),scalar(record,'Opacity amount',.896)))
    c=tag(c,'DR.Surface','Root and strand palette')
    alpha=tag(alpha,'DR.Coverage','Authored card coverage')
    outputs={'base_color':c,'roughness':lerp(scalar(record,'Roughness min',.29981),scalar(record,'Roughness max',.824),saturate(strand)),'specular':scalar(record,'Spec1',.75),'metallic':literal(0.),'alpha':alpha}
    if 'Tangent' in record.textures:outputs['tangent']=expr('normalize',sub(mul(rgb(texture(record,'Tangent')),2.),1.))
    diagnostics+=list(unresolved(record,('Depth','PixelDepthOffset','Scatter','RootScatter','Scraggle','SpecEdge','SpecFront','Transluscent','AO Amount')))
    return plan(record,'hair',outputs,diagnostics,two_sided=True)

def compile_eyelashes(record,context=None):
    # Fresh native SM5 program1FE23...3796: t3.x drives both authored
    # color interpolation and coverage. Nonpositive source texels produce zero
    # coverage even when OpacityPower is zero (the log/exp path is guarded).
    mask=ch(texture(record,'Eyelash Texture',default=(1.,1.,1.,1.)),0)
    palette=saturate(lerp(color(record,'Eyelash Color',(.036458,.024128,.01709)),color(record,'Eyelash Color 2',(0.,0.,0.)),mask))
    powered=expr('power',expr('max',mask,1e-30),scalar(record,'OpacityPower',1.))
    alpha=mul(saturate(mul(mul(powered,scalar(record,'Lash Opacity',1.)),scalar(record,'Visibility',1.))),expr('greater_than',mask,0.))
    alpha=tag(alpha,'DR.Coverage','Native lash texture power, opacity and visibility')
    outputs={'base_color':tag(palette,'DR.Surface','Native dual eyelash color'),'alpha':alpha,'roughness':scalar(record,'Roughness',.5),'specular':scalar(record,'Specular',0.),'metallic':literal(0.)}
    return plan(record,'eyelashes',outputs,('unsupported_transport:Unreal Hair scattering and screen-space proximity fade','unsupported_transport:character snow/wet gameplay effects'),two_sided=True,native_shader_mapping='fresh-query1FE23...3796 sample t3.x; cb3[2]/[3]/[4].w/[5].xy')

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
