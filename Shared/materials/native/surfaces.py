"""Independent surface dispatch and explicit packed-input graph construction.

A packed profile is a native-master-specific binding, never a filename rule.
"""
from .surfaces_common import *

def compile_emission(record,profile,context=None):
    """Build only emission, using a source-backed master/permutation profile.

    Supported source modes preserve authored RGB exactly: authored_color,
    cr_color, surface_color, texture_color. Mask identity/channel is explicit.
    Native UV enum-to-set bindings belong to the profile that proved them.
    """
    context=context or {}
    if not profile.get('evidence'):
        return unfinished(record,'generic_emission',('native emission profile lacks source evidence',))
    enable=profile.get('enable_switch','Use Emissive')
    if enable and not value(record,'switches',enable,False):
        return plan(record,'generic_emission',{},(),profile['evidence'])
    mode=value(record,'scalars',profile.get('uv_mode_parameter','UV Mode'),0.)
    uv_modes=profile.get('uv_modes',{0:profile.get('uv',0)})
    if mode not in uv_modes:
        return unfinished(record,'generic_emission',('unproven native UV mode '+str(mode),))
    coords=uv(uv_modes[mode])
    source=profile.get('source','authored_color')
    if profile.get('cr_switch','Use CR as Emissive') and value(record,'switches',profile.get('cr_switch','Use CR as Emissive'),False):
        source='cr_color'
    try:
        if source=='authored_color': result=rgb(color(record,profile.get('color_parameter','EmissiveColor')))
        elif source=='cr_color': result=rgb(texture(record,profile.get('cr_parameter','CR'),'color',coords))
        elif source=='surface_color':
            if 'base_color' not in context: raise ValueError('surface-color emission needs retained native base_color expression')
            result=context['base_color']
        elif source=='texture_color': result=rgb(texture(record,profile['color_texture'],'color',coords))
        else: raise ValueError('Unproven emission source '+str(source))
        mask=profile.get('mask')
        if mask:
            result=mul(result,channel(texture(record,mask['parameter'],'data',coords),mask['channel']))
        intensity=scalar(record,profile.get('intensity_parameter','EmissiveIntensity'),1.)
        return plan(record,'generic_emission',{'emission':staged(result,'DR.Light','Native emissive color and mask'),
                    'emission_strength':staged(intensity,'DR.Light','Authored emissive intensity')},(),profile['evidence'])
    except (ValueError,KeyError) as error:
        return plan(record,'generic_emission',{},('unsupported: '+str(error),),profile['evidence'])

def compile_packed_surface(record,profile,context=None):
    if not profile.get('evidence'):
        return unfinished(record,'packed_surface',('native packed channel profile lacks source evidence',))
    diagnostics=[]; coords=uv(profile.get('uv',0))
    if value(record,'switches','Turn on basic tiling',False):
        coords=mul(coords,expr('combine',scalar(record,'Tiling U',1.),scalar(record,'Tiling V',1.)))
    outputs={}
    try:
        for target,binding in profile.get('channels',{}).items():
            if isinstance(binding,(int,float,tuple,list)):
                outputs[target]=literal(binding); continue
            sample=texture(record,binding['parameter'],binding.get('role','data'),coords)
            if 'channels' in binding:
                sample=expr('combine',*(channel(sample,c) for c in binding['channels']))
            elif 'channel' in binding: sample=channel(sample,binding['channel'])
            if target=='normal': sample=normal(sample)
            if 'multiplier' in binding: sample=mul(sample,scalar(record,binding['multiplier'],1.))
            if 'bias' in binding: sample=add(sample,scalar(record,binding['bias']))
            outputs[target]=sample
        if value(record,'switches','Use Tinting',False):
            if profile.get('tint_mode')=='multiply':outputs['base_color']=mul(outputs['base_color'],rgb(color(record,'Tint')))
            else:diagnostics.append('unsupported: native tint mask/swatch branch')
        if value(record,'switches','Use Emissive',False):
            binding=profile.get('emission')
            if binding is None: diagnostics.append('unsupported: native emission channel mapping absent')
            else:
                emission_profile=dict(binding); emission_profile['evidence']=profile['evidence']
                if 'parameter' in binding: emission_profile['mask']={'parameter':binding['parameter'],'channel':binding['channel']}
                emission=compile_emission(record,emission_profile,{**(context or {}),**outputs})
                outputs.update(emission.outputs); diagnostics.extend(emission.diagnostics)
                if value(record,'switches','Pulsate',False):diagnostics.append('unsupported: native pulse phase/range requires permutation evidence')
        for feature in ('Use Detail','UseOverlay','UseDamage','Use Dust','Use Swatch Colors','Use Pattern Overlay','RGB Tint Mask','Use Packed Tint Mask','SubsurfaceScattering','AlphaHoles','LargeHoles','UseSpinning','bUseGlobalWind','Use Perma Snow'):
            if value(record,'switches',feature,False):diagnostics.append('unsupported: prop '+feature)
        return plan(record,profile.get('family','packed_surface'),outputs,diagnostics,profile['evidence'])
    except (ValueError,KeyError) as error:return plan(record,'packed_surface',{},('unsupported: '+str(error),),profile['evidence'])

def compile_material(record,context=None):
    context=context or {}; master=field(record,'master','').split('/')[-1].split('.')[0]
    if master in ('M_Character_Layered','MI_Character_Layered','MI_Character_Layered_4','MI_Character_Layered_8'):
        from .layered import compile_material as build
    elif master.startswith('M_ArchitecturePreset_'):
        from .architecture import compile_material as build
    elif master in ('M_FurShells','M_FurShells_LOD'):
        from .fur import compile_material as build
    elif master in ('M_Vehicle','M_VehicleStack'):
        from .vehicle import compile_material as build
    elif master=='M_EnemyPreset_Masked_NAO+Detail':
        from .decals import compile_enemy_shell as build
    elif master.startswith('M_Decal'):
        from .decals import compile_material as build
    elif master=='M_PropPreset_CR+NOX+XEM' and value(record,'switches','Use Masks',False):
        return compile_native_prop(record,context)
    else:
        profile=context.get('packed_surface_profile')
        if profile:return compile_packed_surface(record,profile,context)
        return unfinished(record,'unknown_surface',('no proven native profile for '+master,))
    return build(record,context)


NATIVE_PROP_EVIDENCE=('fresh-query/MI_Harvester_01_Weakpoints.json',
'fresh-query/disassembly/04DBB23E5298EF79D5E53B32CDD0395677D70CC6_2126_SF_Pixel_557114BC6F0E47E178CEB853515802F455641EB6.asm')

def native_prop_coordinates(record,context=None):
    """Observed generic prop UV Mode adjusts object scale on native UV0.

    Mode 0 retains coordinates; Mode 1 applies the selected object scale;
    the scalar is preserved rather than interpreted as a UV-set index.
    """
    context=context or {}; coords=uv()
    mode=value(record,'scalars','UV Mode',0.)
    if mode!=0:
        scale=context.get('object_scale')
        if scale is None:raise ValueError('UV Mode requires authored object_scale for native scale adjustment')
        axis=value(record,'scalars','Scale Axis',0.)
        axis_index=1 if abs(axis-1.)<=.1 else 2 if axis>=1. else 0
        axis_scale=abs(scale[axis_index])
        mask_axis=value(record,'scalars','Mask on UVs',0.)
        # Authored native selectors are integer enum values; no guess for fractions.
        if mask_axis not in (0.,1.,2.):raise ValueError('Unproven fractional native mask selector')
        component=channel(coords,0) if abs(mask_axis-1.)<=.1 else channel(coords,1) if mask_axis>=1 else literal(1.)
        allowed=sub(literal(1.),expr('greater_than',component,literal(1.)))
        u_scale=add(literal(1.),mul(allowed,literal((axis_scale-1.)*mode)))
        coords=mul(coords,expr('combine',u_scale,literal(1.)))
    return mul(coords,expr('combine',scalar(record,'Tiling U',1.),scalar(record,'Tiling V',1.)))

def compile_native_prop_emission(record,context=None):
    context=context or {}
    if not value(record,'switches','Use Emissive',False):return plan(record,'generic_emission',{},(),NATIVE_PROP_EVIDENCE)
    if not value(record,'switches','Use Masks',False):return unfinished(record,'generic_emission',('native no-packed-mask emissive permutation not extracted',))
    try:
        coords=native_prop_coordinates(record,context)
        packed=texture(record,'Packed Mask','data',coords)
        mask=channel(packed,1)
        threshold=scalar(record,'EmissiveHistogramRange',.001)
        above=sub(literal(1.),expr('less_than',mask,threshold))
        histogram=sat(scalar(record,'Use Histogram (0=false | 1=true)'))
        mask=mix(mask,above,histogram)
        if value(record,'switches','Use CR as Emissive',False):source=rgb(texture(record,'CR','color',coords))
        else:source=rgb(color(record,'EmissiveColor'))
        strength=scalar(record,'EmissiveIntensity',1.)
        if value(record,'switches','Pulsate',False):
            speed=value(record,'scalars','PulsateSpeed',1.)
            phase=mul(expr('time'),literal(speed*6.283185307179586))
            pulse=expr('sin',phase)
            if value(record,'switches','ConstantPulsate',False):
                pulse=add(pulse,literal(1. if abs(speed)<=.0001 else 0.))
            else:
                # Only ConstantPulsate was present in the enabled native query.
                return unfinished(record,'generic_emission',('native non-constant pulse permutation not extracted',))
            strength=mul(strength,sat(mul(pulse,scalar(record,'PulsateAmount',1.))))
        return plan(record,'generic_emission',{'emission':mul(source,mask),'emission_strength':strength},(),NATIVE_PROP_EVIDENCE)
    except (ValueError,KeyError) as error:return plan(record,'generic_emission',{},('unsupported: '+str(error),),NATIVE_PROP_EVIDENCE)

def compile_native_prop(record,context=None):
    """Observed enabled CR+NOX+XEM packed-mask permutation."""
    context=context or {}
    try:
        coords=native_prop_coordinates(record,context)
        color_sample=texture(record,'CR','color',coords)
        rough_sample=texture(record,'CR','data',coords)
        packed=texture(record,'Packed Mask','data',coords)
        outputs={'base_color':rgb(color_sample),
                 'normal':normal(texture(record,'Normal','normal',coords)),
                 'roughness':expr('power',channel(rough_sample,3),scalar(record,'Roughness Power',1.)),
                 'metallic':sat(mul(channel(packed,2),scalar(record,'Metalness Value',1.)))}
        diagnostics=[]
        emission=compile_native_prop_emission(record,context)
        outputs.update(emission.outputs); diagnostics.extend(emission.diagnostics)
        for feature in ('Use Detail','Use Dust','UseOverlay','Use Tinting','Use Swatch Colors','EnableSigns','Use Frost','Use Snow','Use Pattern Overlay'):
            if value(record,'switches',feature,False):diagnostics.append('unsupported: native prop '+feature+' permutation requires retained native stage')
        diagnostics.append('unsupported: engine wetness, pre-exposure, selection and view-specular runtime inputs')
        return plan(record,'prop_cr_nox_xem',outputs,diagnostics,NATIVE_PROP_EVIDENCE)
    except (ValueError,KeyError) as error:return plan(record,'prop_cr_nox_xem',{},('unsupported: '+str(error),),NATIVE_PROP_EVIDENCE)

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
