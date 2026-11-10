"""Independent enemy surface/state plans. Unknown native stages fail visibly.
Portable animations are opt-in and are not claimed to be native shader reconstructions.
"""
from .core import expr,literal
from .charfx_common import *

# Object custom property read by native enemy display/surface emission (OBJECT attribute).
ALERTNESS_PROPERTY='arc_alertness_state'

def compile_portable_enemy_display(record,context=None):
    context=context or {};diagnostics=[]
    # Runtime state colors must be decoded from native data, never fabricated presets.
    colors=context.get('enemy_state_colors')
    alert=context.get('enemy_alertness',scalar(record,'AlertnessState',0.))
    if colors:
        if not context.get('enemy_state_evidence'):raise ValueError('enemy state palette requires native evidence')
        statecolor=literal(tuple(colors[0])[:3])
        for i,c in enumerate(colors[1:],1):statecolor=lerp(statecolor,literal(tuple(c)[:3]),expr('greater_than',alert,float(i)-.5))
    else:
        statecolor=literal((1.,1.,1.));diagnostics.append('unsupported_native_stage:display AlertnessState palette')
    if context.get('portable_display_animation',False):
        t=expr('time');v=ch(expr('uv',index=0),1)
        phase=expr('fract',mul(t,scalar(record,'ScanSpeed',.5)))
        loop=record.switches.get('Loop',False);blink=record.switches.get('Blink',False)
        mode='blink' if blink else ('loop' if loop else 'scan')
        if blink:mask=expr('less_than',phase,.5)
        elif loop:mask=saturate(sub(1.,mul(expr('abs',sub(expr('fract',add(mul(v,scalar(record,'Tiling',64.)),phase)),.5)),2.)))
        else:mask=expr('less_than',expr('abs',sub(v,phase)),.05)
        diagnostics.append('portable_display_animation:'+mode+'; independent preview, native transfer curve unproven')
    else:
        mode='blink' if record.switches.get('Blink',False) else ('loop' if record.switches.get('Loop',False) else 'scan')
        mask=context.get('enemy_display_mask',literal(1.))
        if 'enemy_display_mask' in context and not context.get('enemy_display_evidence'):raise ValueError('display mask requires source evidence')
        diagnostics.append('unsupported_native_stage:'+mode+' native display transfer function')
    if record.switches.get('Glitch Effect',False):diagnostics.append('unsupported_native_stage:Glitch Effect native noise/mapping')
    if record.scalars.get('DamagePercent',0.)!=0.:diagnostics.append('unsupported_native_stage:display DamagePercent transfer function')
    return plan(record,'enemy_display',{'base_color':literal((0.,0.,0.)),'emission':mul(statecolor,mask),'emission_strength':literal(1.),'alpha':literal(1.),'roughness':literal(1.)},diagnostics,enemy_display_mode=mode)


def native_alert_state(alert):
    """443BC5...3676 native comparison window and exact linear palette."""
    amber=sub(1.,expr('greater_than',expr('abs',sub(alert,.5)),.33))
    red=sub(1.,expr('greater_than',expr('abs',sub(alert,1.)),.33))
    palette=lerp(lerp(literal((0.,.325897,1.)),literal((1.,.406525,.006859)),amber),literal((1.,0.,0.)),red)
    return palette,amber,red

def native_display_ridge(value,loop=False):
    narrow=saturate(mul(sub(.1,value),9.999998)) if loop else expr('max',mul(sub(.1,value),9.999998),0.)
    wide=expr('max',mul(sub(.4,value),.125),0.)
    return add(narrow,wide)

def _native_damage_gate(record,context,diagnostics):
    bone=context.get('enemy_bone_damage')
    if bone is None:
        bone=literal(0.);diagnostics.append('unsupported_transport:per-bone CUSTOM_BONE_DATA; using global DamagePercent')
    x=add(bone,scalar(record,'DamagePercent',.9))
    wrapped=saturate(add(x,mul(expr('less_than',x,0.),2.)))
    return expr('greater_than',sub(1.,wrapped),.00001)

def compile_enemy_display(record,context=None):
    context=context or {}
    if context.get('portable_display_animation',False):return compile_portable_enemy_display(record,context)
    diagnostics=[]
    palette,amber,red=native_alert_state(context.get('enemy_alertness',scalar(record,'AlertnessState',0.)))
    palette=tag(palette,'DR.LayerInputs','Native alertness state palette')
    coords=context.get('enemy_display_uv',expr('uv',index=0));u=ch(coords,0)
    time=context.get('enemy_display_time',expr('time'));speed=scalar(record,'ScanSpeed',.5)
    loop=record.switches.get('Loop',False);blink=record.switches.get('Blink',False)
    if blink:
        clock=mul(mul(time,speed),2.)
        pulse=mul(expr('sin',mul(clock,4.712401)),.8)
        square=sub(0.,expr('floor',sub(0.,expr('sin',mul(clock,6.283185)))))
        blink_value=lerp(lerp(pulse,square,amber),1.,red)
        ridge_input=literal(0.);mode='blink'
    elif loop:
        value=add(u,mul(time,speed))
        # SM5 fmod uses signed fractional part rather than GLSL/Blender fract.
        signed=mul(expr('fract',expr('abs',value)),sub(1.,mul(expr('less_than',value,0.),2.)))
        triangle=expr('abs',sub(mul(signed,2.),1.))
        wave=sub(expr('sin',mul(expr('sin',mul(time,6.283185)),6.283185)),.9)
        ridge_input=mul(lerp(triangle,wave,amber),sub(1.,red))
        mode='loop'
    else:
        frequency=mul(lerp(speed,mul(speed,2.),amber),sub(1.,red))
        phase=expr('fract',add(mul(expr('sin',mul(mul(frequency,time),6.283185)),.5),.5))
        ridge_input=expr('abs',mul(add(sub(u,1.),phase),2.))
        mode='scan'
    ridge=tag(native_display_ridge(ridge_input,loop),'DR.Surface','Native '+mode+' dual-width ridge')
    depth=context.get('enemy_view_depth')
    if depth is None:
        depth=mul(expr('camera_depth'),float(context.get('unreal_units_per_scene_unit',100.)))
    falloff=scalar(record,'DistanceFalloff',2048.)
    distance=saturate(expr('divide',depth,expr('max',falloff,1e-12)))
    growth=mul(distance,scalar(record,'DistanceSize',1.))
    ridge=saturate(mul(add(growth,1.),ridge))
    tiles=mul(coords,scalar(record,'Tiling',64.))
    cell=sub(expr('fract',tiles),literal((.5,.5,.5)))
    radius=expr('sqrt',add(mul(ch(cell,0),ch(cell,0)),mul(ch(cell,1),ch(cell,1))))
    circle_radius=add(mul(saturate(mul(growth,.0625)),.6),.4)
    circles=saturate(mul(sub(1.,expr('divide',radius,circle_radius)),100000.))
    mask=tag(lerp(distance,1.,circles),'DR.Coverage','Native distance-scaled display cells')
    exposure=context.get('enemy_eye_adaptation')
    if exposure is None:
        exposure=literal(1.);diagnostics.append('unsupported_transport:Unreal eye adaptation; using neutral exposure')
    luminance=saturate(mul(blink_value,add(mask,add(1.,growth)))) if blink else mul(mask,ridge)
    emission=expr('divide',mul(mul(palette,luminance),16.),expr('max',exposure,1e-12))
    emission=mul(emission,_native_damage_gate(record,context,diagnostics))
    if record.switches.get('Glitch Effect',False):
        noise=context.get('enemy_glitch_noise_sum')
        if noise is None:diagnostics.append('unsupported_native_stage:Glitch Effect requires engine six-octave 3Dnoise sum')
        else:
            if not context.get('enemy_glitch_evidence'):raise ValueError('native glitch noise requires resource evidence')
            amount=scalar(record,'GlitchAmount',1.)
            multiplier=add(mul(amount,sub(mul(noise,.972),.972)),1.)
            emission=lerp(mul(emission,multiplier),mul(palette,multiplier),amount)
            mode='glitch'
    emission=tag(expr('max',emission,0.),'DR.Light','Native display emission and damage gate')
    return plan(record,'enemy_display',{'base_color':literal((0.,0.,0.)),'emission':emission,'emission_strength':literal(1.),'alpha':literal(1.),'roughness':sub(.6,mul(mask,.2))},diagnostics,enemy_display_mode=mode,native_shader_mapping='fresh-query scan443BC5/loopB46B/glitchDE49;fresh-query-2 blink8617E5...0505')

def compile_enemy(record,context=None):
    context=context or {};name=record.master+' '+record.package
    if record.master.rsplit('/',1)[-1]=='M_EnemyPreset_Masked_NAO+Detail':
        from .decals import compile_enemy_shell
        return compile_enemy_shell(record,context)
    if 'ScanDisplay' in name:return compile_enemy_display(record,context)
    if 'Cloak' in name:
        return plan(record,'enemy_cloak',{'alpha':literal(0.)},('unsupported_native_stage:cloak scene-color refraction/Voronoi',))
    diagnostics=[];outputs={'alpha':literal(1.),'roughness':scalar(record,'Roughness',.5),'metallic':scalar(record,'Metallic',0.)}
    if 'CR' in record.textures:
        outputs['base_color']=rgb(texture(record,'CR','color'))
        if record.switches.get('Use BC Tint',False):outputs['base_color']=mul(outputs['base_color'],color(record,'BC Tint'))
        outputs['roughness']=ch(texture(record,'CR','data'),3)
    elif 'OX' in record.textures:
        outputs['base_color']=color(record,'Color',(.09375,)*3)
        diagnostics.append('unsupported_native_stage:OX edge/leak roles')
    else:outputs['base_color']=color(record,'Color',(.5,)*3)
    for parameter in ('NOM','N'):
        if parameter in record.textures:
            outputs['normal']=normal(record,parameter);break
    if 'NMR' in record.textures:
        nmr=texture(record,'NMR','data',uv(scalar(record,'NMRTile',4.)))
        outputs['normal']=expr('normal_decode',nmr)
        diagnostics.append('unsupported_native_stage:NMR roughness/metal channel binding')
    for role,out in (('enemy_roughness','roughness'),('enemy_metallic','metallic'),('enemy_alpha','alpha')):
        value=mapped_channel(record,context,role)
        if value is not None:outputs[out]=value
    if 'NOM' in record.textures and mapped_channel(record,context,'enemy_metallic') is None:diagnostics.append('unsupported_native_stage:NOM metal/occlusion channel binding')
    if 'Detail NXR' in record.textures and record.switches.get('Use Detail',False):
        detail=normal(record,'Detail NXR',uv(scalar(record,'Detail Tile',1.)))
        outputs['normal']=tag(expr('rnm',outputs.get('normal',literal((0.,0.,1.))),detail),'DR.Details','Enemy tiled detail normal')
    emissive=mapped_channel(record,context,'enemy_emission')
    if emissive is not None:
        outputs['emission']=tag(mul(color(record,'Emissive Color'),emissive),'DR.Light','Bound enemy emission')
        outputs['emission_strength']=scalar(record,'Emissive Power',record.scalars.get('EmissivePower',0.))
    elif any(k in record.textures for k in ('EX','EXX','EmissiveMask')):diagnostics.append('unsupported_native_stage:enemy emission mask/alertness channel binding')
    if record.scalars.get('DamagePercent',0.)!=0.:diagnostics.append('unsupported_native_stage:enemy shell native damage stages')
    for key in ('AlphaHoles','BulletHoles','LargeHoles','Dents','OnlySoot'):
        if record.switches.get(key,False):diagnostics.append('unsupported_native_stage:enemy shell '+key)
    if 'DecalTrimsheet' in record.textures:
        diagnostics.append('unsupported_native_stage:enemy NAO decal variant and cutout binding')
        value=mapped_channel(record,context,'enemy_decal_alpha')
        if value is not None:outputs['alpha']=value
    return plan(record,'enemy_surface',outputs,diagnostics)

compile_enemies=compile_enemy

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
