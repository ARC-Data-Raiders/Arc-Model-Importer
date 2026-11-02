"""Independent decal coordinate and channel operations.

Game layer decal placement may be supplied as evaluated native preshader rows;
this keeps camera/projector transforms separate from the surface material DAG.
"""
import math
from .surfaces_common import *

def atlas_coordinates(coords,columns,rows,position):
    if columns<=0 or rows<=0: raise ValueError('Atlas dimensions must be positive')
    column=position%columns; row=math.floor(position/columns)
    return div(add(coords,literal((column,row))),literal((columns,rows)))

def placed_coordinates(coords,scale,offset,rotation,aspect=1.):
    centered=mul(sub(coords,literal((.5,.5))),literal((scale,scale*aspect)))
    centered=add(centered,literal(offset))
    x=channel(centered,0); y=channel(centered,1)
    cs=math.cos(rotation); sn=math.sin(rotation)
    rotated=expr('combine',sub(mul(x,literal(cs)),mul(y,literal(sn))),add(mul(x,literal(sn)),mul(y,literal(cs))))
    return add(rotated,literal((.5,.5)))

def rectangle_mask(coords):
    x=channel(coords,0); y=channel(coords,1)
    return mul(mul(expr('greater_than',x,literal(0.)),expr('less_than',x,literal(1.))),mul(expr('greater_than',y,literal(0.)),expr('less_than',y,literal(1.))))

def apply_layer_decal(record,index,outputs,binding):
    """Apply an explicit native channel binding to a garment's shared DAG."""
    if not binding.get('evidence'): raise ValueError('Layer decal binding requires native evidence')
    prefix=str(index)+'_'; coords=binding['uv']
    sample=texture(record,prefix+'DecalColor','color',coords)
    mask=mul(rectangle_mask(coords),channel(texture(record,prefix+'DecalColor','data',coords),3))
    mask=mul(mask,binding.get('layer_mask',literal(1.)))
    out=dict(outputs)
    if 'base_color' in out:out['base_color']=mix(out['base_color'],rgb(sample),mul(mask,scalar(record,prefix+'ColorOverride',1.)))
    for target,channel_id in binding.get('data_channels',{}).items():
        data=texture(record,prefix+'DecalData','data',coords)
        if target=='normal':
            v=normal(data)
            out[target]=expr('rnm',out.get(target,literal((0.,0.,1.))),mix(literal((0.,0.,1.)),v,mul(mask,scalar(record,prefix+'DetailNormalOverride'))))
        else:out[target]=mix(out.get(target,literal(0.)),channel(data,channel_id),mul(mask,scalar(record,prefix+'SurfaceOverride')))
    return out

def compile_material(record,context=None):
    context=context or {}; diagnostics=[]
    master=field(record,'master','').split('/')[-1].split('.')[0]
    try:
        coords=uv(0,scalar(record,'Tiling',1.))
        if value(record,'switches','UseAtlas',False):
            coords=atlas_coordinates(coords,value(record,'scalars','Columns',1.),value(record,'scalars','Rows',1.),value(record,'scalars','Position',0.))
        if master in ('M_DecalPreset_Masked_CA+Detail','M_DecalPreset_Translucent_CA+Detail'):
            ca=texture(record,'CA','color',coords)
            col=rgb(ca)
            gray=expr('dot',col,literal((.212639,.715169,.072192)))
            col=mix(expr('combine',gray,gray,gray),col,scalar(record,'Saturation',1.))
            if value(record,'switches','Tint Color',False):col=mul(col,rgb(color(record,'Tint Color')))
            alpha=expr('power',channel(texture(record,'CA','data',coords),3),scalar(record,'Opacity Power',1.))
            outputs={'base_color':col,'alpha':alpha,'roughness':scalar(record,'Roughness',.5),'metallic':scalar(record,'Metalness')}
            for feature in ('Grunge opacity breakup','GrungColor','UseDithering'):
                if value(record,'switches',feature,False):diagnostics.append('unsupported: decal '+feature)
            diagnostics.append('unsupported: decal atlas orientation/opacity arithmetic needs matched native shader verification')
            return plan(record,'decal_ca',outputs,diagnostics,('MaterialLibrary/M_Presets/M_DecalPreset_Masked_CA+Detail.json',))
        profile=context.get('packed_surface_profile')
        if profile:
            from .surfaces import compile_packed_surface
            return compile_packed_surface(record,profile,context)
        return unfinished(record,'decal',('no native decal channel/placement profile for '+master,))
    except (ValueError,KeyError) as error:return plan(record,'decal',{},('unsupported: '+str(error),))

compile_decal=compile_material

def compile_enemy_shell(record,context=None):
    """Build shell-decal channels from a proven native permutation binding.

    The NAO master is distinct from garment decals. Native V0/V1/V2 masks,
    coverage/normal swizzles and damage mask must be supplied from game evidence.
    No missing channel is substituted with a suffix convention.
    """
    context=context or {}; binding=context.get('enemy_shell_profile')
    master=field(record,'master','').split('/')[-1].split('.')[0]
    evidence=('MaterialLibrary/M_Presets/M_EnemyPreset_Masked_NAO+Detail.json',)
    if master!='M_EnemyPreset_Masked_NAO+Detail':
        return plan(record,'enemy_shell_decal',{},('unsupported: enemy-shell master '+master,),evidence)
    if not binding or not binding.get('evidence'):
        return plan(record,'enemy_shell_decal',{},('unsupported: native enemy-shell V0/V1/V2 masks and NAO channel proof required',),evidence)
    try:
        if set(binding['variant_masks'])!={'V0','V1','V2'}:raise ValueError('exact V0/V1/V2 mask expressions required')
        coords=binding.get('uv',uv())
        sample=texture(record,'DecalTrimsheet','data',coords)
        normal_channels=binding['normal_channels']
        encoded=expr('combine',*(channel(sample,c) for c in normal_channels))
        outputs={'normal':normal(encoded)}
        coverage=channel(sample,binding['coverage_channel'])
        color_sum=literal((0.,0.,0.)); rough_sum=literal(0.); metal_sum=literal(0.); visible=literal(0.)
        for variant,mask in binding['variant_masks'].items():
            mask=staged(mask,'DR.LayerInputs','Native shell '+variant+' mask')
            color_sum=add(color_sum,mul(rgb(color(record,variant+'_Color')),mask))
            rough_sum=add(rough_sum,mul(scalar(record,variant+'_Roughness'),mask))
            metal_sum=add(metal_sum,mul(scalar(record,variant+'_Metalness'),mask))
            if value(record,'switches',variant+'_HideWhenDamaged',False):
                if 'damage_mask' not in binding:raise ValueError('hide-when-damaged requires native damage mask expression')
                mask=mul(mask,sub(literal(1.),binding['damage_mask']))
            visible=add(visible,mask)
        outputs.update({'base_color':staged(color_sum,'DR.Surface','Authored shell variant color'),
                        'roughness':staged(rough_sum,'DR.Surface','Authored shell variant roughness'),
                        'metallic':staged(metal_sum,'DR.Surface','Authored shell variant metalness'),
                        'alpha':staged(mul(coverage,sat(visible)),'DR.Coverage','Shell cutout and native damage visibility')})
        diagnostics=['unsupported: native shell parallax/detail and snow/frost stages']
        if value(record,'scalars','UseSubSetMaterial',0.)!=0:
            diagnostics.append('unsupported: shell subset material mask binding')
        return plan(record,'enemy_shell_decal',outputs,diagnostics,evidence+tuple(binding['evidence']))
    except (KeyError,ValueError) as error:
        return plan(record,'enemy_shell_decal',{},('unsupported: '+str(error),),evidence)

compile_enemy_shell_explicit=compile_enemy_shell

ENEMY_SHELL_EVIDENCE=('fresh-query/M_EnemyPreset_Masked_NAO+Detail.json',
'fresh-query/disassembly/AECF394E5C20756DC5FBDAFDC1099BB7388EE8B4_14757_SF_Pixel_63D14DEADAC3606B8D866FCDAE04E522035736D0.asm',
'fresh-query-2/MI_ChonkMortar_01_Decals_01.json',
'fresh-query-2/disassembly/69D9E0A6F79CFD184208ED75B995CD18072F2A24_10357_SF_Pixel_3DD9680DC7598444E1A717175A04DC5723D81BCB.asm')

def compile_enemy_shell(record,context=None):
    """Native NAO shell: negative-V zones, blue cutout, alpha detail control."""
    context=context or {}
    if context.get('enemy_shell_profile'):return compile_enemy_shell_explicit(record,context)
    master=field(record,'master','').split('/')[-1].split('.')[0]
    if master!='M_EnemyPreset_Masked_NAO+Detail':return unfinished(record,'enemy_shell_decal',('unproven shell master '+master,))
    try:
        coords=uv(); v=channel(coords,1)
        negative=expr('less_than',v,literal(0.))
        lower=expr('less_than',v,literal(-1.))
        lowest=expr('less_than',v,literal(-2.))
        v1=sub(negative,lower); v2=sub(lower,lowest)
        diagnostics=['unsupported: engine screen-space temporal alpha dithering; authored blue coverage retained']
        subset_amount=value(record,'scalars','UseSubSetMaterial',0.)
        use_parallax=value(record,'switches','Parallax',False)
        hx=texture(record,'Height/HX','data',coords) if subset_amount!=0 or use_parallax else None
        sample_coords=coords
        if use_parallax:
            tangent_view=context.get('tangent_view')
            if tangent_view is None:diagnostics.append('unsupported: native shell parallax needs tangent_view camera direction')
            else:
                height=mul(sub(channel(hx,0),literal(.5)),scalar(record,'HeightRatio'))
                offset=mul(expr('combine',channel(tangent_view,0),channel(tangent_view,1)),height)
                sample_coords=add(coords,offset)
        sample=texture(record,'DecalTrimsheet','data',sample_coords)
        detail=texture(record,'DetailTexture','data',mul(coords,literal(3.)))
        subset=mul(channel(hx,1),literal(subset_amount)) if subset_amount!=0 else literal(0.)
        def variants(kind):
            values=[rgb(color(record,'V'+str(i)+'_'+kind)) if kind=='Color' else scalar(record,'V'+str(i)+'_'+kind) for i in range(3)]
            result=mix(mix(values[0],values[1],v1),values[2],v2)
            target=rgb(color(record,'SubSet_Color')) if kind=='Color' else scalar(record,'SubSet_'+('Metal' if kind=='Metalness' else kind))
            return mix(result,target,subset)
        col=variants('Color')
        color_detail=mul(channel(detail,2),literal(2.))
        color_detail=mul(mix(color_detail,sat(mul(channel(sample,3),color_detail)),literal(.75)),literal(.5))
        col=sat(mul(col,color_detail))
        metal=sat(variants('Metalness'))
        rough_detail=mix(channel(detail,3),literal(.15),channel(sample,3))
        roughs=[sat(mul(rough_detail,scalar(record,'V'+str(i)+'_Roughness'))) for i in range(3)]
        rough=mix(mix(roughs[0],roughs[1],v1),roughs[2],v2)
        rough=sat(mix(rough,scalar(record,'SubSet_Roughness'),subset))
        alpha=channel(sample,2)
        # Actual Chonk MI bytecode multiplies coverage by 1-abs(DamagePercent).
        # Static zone switches select whether that authored uniform applies.
        zone0=sub(literal(1.),add(v1,v2))
        hidden=literal(0.)
        for i,zone in enumerate((zone0,v1,v2)):
            if value(record,'switches','V'+str(i)+'_HideWhenDamaged',False):hidden=add(hidden,zone)
        alpha=mul(alpha,sub(literal(1.),mul(expr('abs',scalar(record,'DamagePercent')),hidden)))
        if context.get('native_damage_visibility') is not None:
            alpha=mul(alpha,context['native_damage_visibility'])
        outputs={'base_color':col,'normal':normal(sample),'roughness':rough,'metallic':metal,'alpha':alpha}
        return plan(record,'enemy_shell_decal',outputs,diagnostics,ENEMY_SHELL_EVIDENCE)
    except (KeyError,ValueError) as error:return plan(record,'enemy_shell_decal',{},('unsupported: '+str(error),),ENEMY_SHELL_EVIDENCE)

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
