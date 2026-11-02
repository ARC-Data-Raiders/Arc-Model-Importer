"""Native M_Tileable_Metal_CR+NXX and M_PropTrimPreset_CR+NOM+AO surfaces from cooked SM5 base-pass programs.

Equations are the frigate hull and trim permutations read from their shader maps (evidence below). Engine inputs
stay at their outdoor defaults: MPC wetness 0, no editor selection, no view-mode override. The LOD dither discard and
the trim specular dither are omitted. Object-planar overlay/detail project the owner's object-space position in UE local
units (UEModel imports UE cm as 0.01 m with Y reflected).
"""
import math
from .core import Expr
from .surfaces_common import *
from .surfaces import native_prop_coordinates

TILEABLE_MASTER='/Game/Pioneer/MaterialLibrary/M_Presets/M_Tileable_Metal_CR+NXX'
TRIM_MASTER='/Game/Pioneer/MaterialLibrary/M_Presets/M_PropTrimPreset_CR+NOM+AO'
# Non-parameter textures: CachedExpressionData.ReferencedTextures[7] (both masters) and [22] (trim).
DETAIL_ARRAY='/Game/Pioneer/MaterialLibrary/Textures/Props/Detail/TA_Detail_Props_02'
SLICE_MASK='/Game/Pioneer/MaterialLibrary/Textures/Trims/T_PropTrimSheet_Mask'
DETAIL_LAYERS=8
LUMA=(.2126,.7152,.0722)
TILEABLE_EVIDENCE=('sm5-map:C9073E65255862CEB833B628B7047E2935A7FB5C pixel:32AC2F40C558DC695238A8464C7C4499F37996779766790CB9C8819008FB994B (UV overlay, NMX)',
 'sm5-map:5C304D94CDE373962A89A74AED777AFE7D61BC01 pixel:2CE93005284BF860938C91B207B22CE046B06578D93D9AEF842D4879D57CD098 (no overlay, NMX)',
 'sm5-map:68D7EE333B293660155C4F6DAEB8D2B8913A17F0 pixel:8AE4C4FEABEF0BB3C215A92E20A95381B79347927EE466806A9575C822E747A4 (no overlay, NXX)',
 'sm5-map:4BA6B3D6D5AEAFF9F6503870352C1E4F07603AB0 pixel:C9BC95D33BEF74F80CE0795597F2D8A28532E8DCBA4B550010127469F8D38408 (TriPlanar overlay, NMX)')
TRIM_EVIDENCE=('sm5-map:843A774F64572B27FBBB458EAF22DD9FFB89B3F6 pixel:FC760B02F4534FC42777803EEF44CD52E60B8FEF02D143087CD8F78B1A2A006B (overlay)',
 'sm5-map:5A8E3BECC527792AE78B9D94C8DA31646B3AA4A4 pixel:8035D001C6F618B58C332D236128160A3A8B2329834C50DDE77CAC011BD464AC (overlay, blend, detail)',
 'sm5-map:3B0B978617B236FE9615177513624F2F353BAC5D pixel:45CDF3A13ED19A4F65D87F446F7E3142278C8C281D600EE4F4D9380A0B157D24 (overlay, blend)',
 'sm5-map:6CC3E2BD0EE72A24D68E83456373CD431598A870 pixel:2FC605DEBFCB0538B6C1638DE09C320CB5BFDFDF3A860C7CE8BFBE6B7AA83D3F (blend)',
 'sm5-map:56CA22DEE42ACAA9C5B235C5F21C3327EDD6C815 pixel:FC9E7803BFECA6BD75EAC677A32D614A2B4C8C69EF20FC68539BC428063760EC (overlay, blend, object-planar detail)')
ENGINE_DEFAULTS='engine inputs held at outdoor defaults: MPC wetness 0, no selection or view-mode override'
PLANAR_NORMAL='object-planar axis test reads the unit Blender object-space shading normal; native WorldToLocal*VertexNormalWS is unnormalized'
UE_PER_BLENDER=100.

# Switches whose verified state is fixed; others shown not to change the compiled program are ignored.
TILEABLE_REQUIRED={'Turn on basic tiling':True,'Use Detail':True,'Use Tint':True,'Use Tint 02':False,'Use Prop AO Dirt':False,'Use Dust':False,
 'Use Frost':False,'Use Dents':False,'Use Normal as NOH':False,'EnableSigns':False,'EnableQuest':False,'AtlasSign':False,'TintOffsetDetail':False,
 'EnableContactShadowsOnTexture':False,'Randomise Atlas Position':False,'RGB Tint Mask':False,'ProjectionLocal':False}
TRIM_REQUIRED={'Use NMX':True,'Use Slice Mask':True,'Use Tint':True,'Align - UV or World':True,'Use Dust':False,'Use Frost':False,'Use Snow':False,
 'Use Perma Snow':False,'Use Holes':False,'Use Tint 02':False,'Use Prop AO Dirt':False,'UseOpacityMask':False,'UseOpacityMaskNormal':False,
 'EnableSigns':False,'EnableQuest':False,'AtlasSign':False,'TintOffsetDetail':False,'Use as CeilingLamp':False,'WS WindSway':False,
 'bUseGlobalWind':False,'Sine B':False,'Sine C':False,'Randomise Atlas Position':False,'ProjectionLocal':False,'TriPlanar Overlay':False}

def _on(record,name):return bool(record.switches.get(name,False))
def _mismatch(record,required):return [k+'='+str(not v) for k,v in required.items() if _on(record,k)!=v]
def _s(record,name,default):
    v=record.scalars.get(name,default)
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ValueError('Invalid finite native scalar: '+name)
    return float(v)
def _c(record,name,default=(.5,.5,.5)):
    v=record.colors.get(name,default)
    if len(v)<3 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in v[:3]):raise ValueError('Invalid native color: '+name)
    return tuple(float(x) for x in v[:3])
def _k(x):return x if isinstance(x,Expr) else literal(tuple(float(v) for v in x) if isinstance(x,tuple) else float(x))
def _lerp(a,b,t):
    # Authored constants fold exactly; UE lerp(a,b,0)=a and lerp(a,b,1)=b.
    if not isinstance(t,Expr):
        if t==0.:return a
        if t==1.:return b
    return mix(a,b,_k(t))
def _rcp_range(low,high):
    # rcp(saturate(high-low)); a zero range is the GPU's infinite reciprocal (a step).
    span=min(max(high-low,0.),1.)
    return 1./span if span>0. else 1e30
def _uv(index=0):
    coords=expr('uv',index=index,stage='DR.Coordinates')
    return channel(coords,0),channel(coords,1)
def _luma(c):return expr('dot',c,literal(LUMA))
def _grade(c,desaturate,contrast):
    """saturate(lerp(c,luma,Desaturate)*(1+2C)-C), the shared colour pre-pass."""
    c=_lerp(c,_luma(c),desaturate)
    if contrast:c=add(mul(c,_k(1.+2.*contrast)),_k((-contrast,)*3))
    return sat(c)
def _range_mask(x,low,high):return sat(mul(sub(x,_k(low)),_k(_rcp_range(low,high))))
def _decode_xy(sample):
    return sub(mul(channel(sample,0),_k(2.)),_k(1.)),sub(mul(channel(sample,1),_k(2.)),_k(1.))
def _reconstruct_z(x,y):return expr('sqrt',expr('max',sub(_k(1.),add(mul(x,x),mul(y,y))),_k(0.)))

def object_planar(scale,threshold):
    """Dominant-axis projection of UE local position*scale: |n.z|>t -> (x,y), else |n.x|>t -> (y,-z), else (x,-z).

    UE local = (100x,-100y,100z) of the Blender object coordinates. The native axis test reads
    WorldToLocal*VertexNormalWS; it compares only |x| and |z|, so the Y reflection drops out.
    """
    p=expr('object_position',stage='DR.Coordinates',name='Owner object position')
    n=expr('object_normal',stage='DR.Coordinates',name='Owner object normal')
    x,y,neg_z=(mul(channel(p,i),_k(f*UE_PER_BLENDER*scale)) for i,f in enumerate((1.,-1.,-1.)))
    on_z=expr('greater_than',expr('abs',channel(n,2)),_k(threshold))
    on_x=expr('greater_than',expr('abs',channel(n,0)),_k(threshold))
    return mix(mix(x,y,on_x),x,on_z),mix(neg_z,y,on_z)

def overlay_coordinates(record,u,v):
    """UV overlay: base*0.1*OT, UV0/UV1 lerps, *OT; TriPlanar (4BA6B3D6): object-planar local/Overlay Size.

    Both then add UVOffset and rotate about 0.5.
    """
    offset=_s(record,'UVOffset',0.)
    if _on(record,'TriPlanar Overlay'):
        size=_s(record,'Overlay Size',1024.)
        if size==0.:raise ValueError('TriPlanar Overlay needs a nonzero Overlay Size')
        ou,ov=object_planar(1./size,.65)
        ou,ov=add(ou,_k(offset)),add(ov,_k(offset))
    else:
        tiling=_s(record,'OverlayTiling',1.)
        ou,ov=mul(u,_k(.1*tiling)),mul(v,_k(.1*tiling))
        use0=_s(record,'Use UV0 as Overlay UVs',0.)
        ou,ov=_lerp(ou,u,use0),_lerp(ov,v,use0)
        use1=_s(record,'Use UV1 as Overlay UVs',0.)
        if use1:
            u1,v1=_uv(1);ou,ov=_lerp(ou,u1,use1),_lerp(ov,v1,use1)
        ou,ov=add(mul(ou,_k(tiling)),_k(offset)),add(mul(ov,_k(tiling)),_k(offset))
    rotate=_s(record,"Use Rotate UV's",0.)
    angle=_s(record,'Rotation Angle',0.)/360.*6.28318548
    c,s=math.cos(angle),math.sin(angle)
    if rotate and (c!=1. or s!=0.):
        du,dv=sub(ou,_k(.5)),sub(ov,_k(.5))
        ru=add(add(mul(du,_k(c)),mul(dv,_k(-s))),_k(.5));rv=add(add(mul(du,_k(s)),mul(dv,_k(c))),_k(.5))
        ou,ov=_lerp(ou,ru,rotate),_lerp(ov,rv,rotate)
    return expr('combine',ou,ov,stage='DR.Coordinates',name='Native overlay coordinates')

def overlay_layer(record,albedo,sample):
    """Returns (albedo, mi): multiply-by-two overlay over the range mask mi = lerp(1-m,m,Invert)."""
    o=rgb(sample)
    tinted=mul(_lerp(o,_luma(o),_s(record,'OverlayDesaturation',0.)),_k(tuple(2.*x for x in _c(record,'Overlay Color'))))
    blended=mul(mul(albedo,tinted),_k(2.))
    m=_range_mask(channel(sample,0),_s(record,'OverlayRange Min',0.),_s(record,'OverlayRange Max',1.))
    mi=_lerp(sub(_k(1.),m),m,_s(record,'OverlayRange Invert',0.))
    weight=sat(mul(mi,_k(_s(record,'Overlay_BaseColor_Strength',1.))))
    return mix(albedo,blended,weight),mi

def detail_sample(record,slice_alpha,context,planar=False):
    """TA_Detail_Props_02 at UV0*DetailTiling*(1,1-.5*Ratio)*sum|LocalToWorld axes|, slice round(alpha*8).

    planar (56CA22DE) replaces the UV0 coordinates with object_planar(1/350, .5); Detail Tiling and
    2:1 Ratio are then unused. D3D rounds the array index to nearest even and clamps it to the
    native depth; the dynamic index is expanded into static data-role samples. The axis-length sum
    is read per object in the shader, so a material shared by differently scaled owners stays
    exact; a context 'object_scale_sum' pins it to a constant.
    """
    total=context.get('object_scale_sum')
    if total is None:total=expr('dot',expr('object_axis_lengths',stage='DR.Coordinates',name='Owner world axis lengths'),literal((1.,1.,1.)))
    elif isinstance(total,bool) or not isinstance(total,(int,float)) or not math.isfinite(total) or total<=0:raise ValueError('invalid owner world-scale sum (UE LocalToWorld axis lengths)')
    count=int(context.get('detail_layer_count',DETAIL_LAYERS))
    if count!=DETAIL_LAYERS:raise ValueError('verified detail shader requires the native eight-layer TA_Detail_Props_02')
    u,v=_uv(0);tiling=_s(record,'Detail Tiling',1.);ratio=1.-.5*_s(record,'2:1 Ratio',0.)
    if planar:
        pu,pv=object_planar(1./350.,.5)
        coords=expr('combine',mul(pu,_k(total)),mul(pv,_k(total)),stage='DR.Coordinates',name='Native object-planar detail coordinates')
    elif isinstance(total,Expr):
        scale=total if tiling==1. else mul(total,_k(tiling))
        coords=expr('combine',mul(u,scale),mul(v,scale if ratio==1. else mul(scale,_k(ratio))),stage='DR.Coordinates',name='Native detail coordinates')
    else:
        tiling*=float(total)
        coords=expr('combine',mul(u,_k(tiling)),mul(v,_k(tiling*ratio)),stage='DR.Coordinates',name='Native detail coordinates')
    layers=[expr('texture',reference=DETAIL_ARRAY,role='data',uv=coords,slice=i,stage='DR.Source',name='Prop detail layer '+str(i)) for i in range(count)]
    index=mul(slice_alpha,_k(8.));selected=layers[0]
    for i in range(1,count):
        choose=expr('greater_than',index,_k(i-.5)) if i%2 else sub(_k(1.),expr('less_than',index,_k(i-.5)))
        selected=mix(selected,layers[i],choose)
    return selected

def detail_roughness(record,r,detail):
    """Overlay blend of roughness with detail.B^1.5, then lerp by Detail Roughness Influence."""
    d=expr('power',channel(detail,2),_k(1.5))
    low=mul(mul(r,d),_k(2.))
    high=sub(_k(1.),mul(mul(sub(_k(1.),r),sub(_k(1.),d)),_k(2.)))
    blended=mix(low,high,sub(_k(1.),expr('less_than',r,_k(.5))))
    return _lerp(r,mul(blended,_k(_s(record,'Detail Roughness Multiplier',1.))),_s(record,'Detail Roughness Influence',.65))

def detail_color(record,albedo,detail):
    shifted=mul(albedo,_k(_s(record,'Detail Color Shift',.25)))
    return mix(albedo,shifted,mul(sub(_k(1.),channel(detail,3)),_k(_s(record,'Detail Color Intensity',1.))))

def compile_tileable_metal(record,context=None):
    context=context or {}
    def refuse(reason):return plan(record,'tileable_metal',{},('unsupported: '+reason,),TILEABLE_EVIDENCE)
    wrong=_mismatch(record,TILEABLE_REQUIRED)
    if wrong:return refuse('unverified Tileable Metal permutation '+','.join(wrong))
    overlay=_on(record,'UseOverlay')
    diagnostics=[ENGINE_DEFAULTS]+([PLANAR_NORMAL] if overlay and _on(record,'TriPlanar Overlay') else [])
    try:
        u,v=_uv(0);tiling=_s(record,'Tiling',1.)
        coords=expr('combine',mul(u,_k(tiling)),mul(v,_k(tiling)),stage='DR.Coordinates',name='Native base tiling')
        nmx=texture(record,'NXX/NMX Texture','data',coords)
        cr=texture(record,'CR Texture','color',coords)
        detail=detail_sample(record,channel(nmx,3),context)
        bx,by=_decode_xy(nmx);bz=_reconstruct_z(bx,by)
        strength=_k(_s(record,'Normal Strength',1.));dni=_k(_s(record,'Detail Normal Intensity',1.))
        dx,dy=_decode_xy(detail)
        normal_ts=expr('normalize',expr('combine',add(mul(bx,strength),mul(dx,dni)),add(mul(by,strength),mul(dy,dni)),bz),stage='DR.Details',name='Native base and detail normal')
        c0=_grade(rgb(cr),_s(record,'Desaturate',0.),_s(record,'Color Contrast',0.))
        b=channel(nmx,2)
        tint=_range_mask(b,_s(record,'Tint Range Low',0.),_s(record,'Tint Range High',1.))
        a=mix(c0,mul(c0,_k(tuple(2.*x for x in _c(record,'Tint Color 01')))),tint)
        # Use NMX: metal = B^2.2; the NXX program derives it from the detail slice as saturate(1-8A).
        metal=expr('power',b,_k(2.2)) if _on(record,'Use NMX') else sat(sub(_k(1.),mul(channel(nmx,3),_k(8.))))
        mask_tint=_s(record,'MaskTintFromMetalness',1.)
        if mask_tint:a=mix(a,c0,mul(metal,_k(mask_tint)))
        mi=None
        if overlay:
            a,mi=overlay_layer(record,a,texture(record,'Overlay','color',overlay_coordinates(record,u,v)))
        a=detail_color(record,a,detail)
        mm=mul(_lerp(metal,expr('min',add(metal,tint),_k(1.)),_s(record,'TintMetalness',0.)),_k(_s(record,'Master Metalness Multiply',1.)))
        metallic=sat(mm) if mi is None else sat(mix(mm,mul(mm,_k(2.*_s(record,'Overlay_Metalness',.5))),sat(mi)))
        r=channel(cr,3);r=mix(r,mul(r,_k(_s(record,'Roughness Tint',1.))),tint)
        if mi is not None:r=mix(r,mul(mul(r,mi),_k(2.*_s(record,'Overlay_Roughness_Strength',1.))),sat(mi))
        r=detail_roughness(record,r,detail)
        outputs={'base_color':staged(sat(a),'DR.Surface','Native tileable metal albedo'),'normal':normal_ts,'metallic':metallic,
                 'roughness':staged(sat(r),'DR.Surface','Native tileable metal roughness'),'specular':literal(.5)}
        return plan(record,'tileable_metal',outputs,diagnostics,TILEABLE_EVIDENCE)
    except (ValueError,KeyError) as error:return refuse(str(error))

def compile_prop_trim(record,context=None):
    context=context or {}
    def refuse(reason):return plan(record,'prop_trim',{},('unsupported: '+reason,),TRIM_EVIDENCE)
    wrong=_mismatch(record,TRIM_REQUIRED)
    blend=_on(record,'Enable Blend Textures');use_detail=_on(record,'Use Detail');overlay=_on(record,'UseOverlay')
    if blend and (not _on(record,'BlendTextureUV0') or _on(record,'Invert Blend Mask')):wrong.append('blend texture UV/invert')
    if wrong:return refuse('unverified PropTrim permutation '+','.join(wrong))
    planar=use_detail and not _on(record,'Turn on basic tiling')
    try:
        diagnostics=[ENGINE_DEFAULTS]+([PLANAR_NORMAL] if planar else [])
        tiled=native_prop_coordinates(record,context);tu,tv=channel(tiled,0),channel(tiled,1)
        coords=expr('combine',tu,tv,stage='DR.Coordinates',name='Native trim coordinates')
        nmx=texture(record,'NXX/NMX Texture','data',coords)
        cr=texture(record,'CR Texture','color',coords)
        nx,ny=channel(nmx,0),channel(nmx,1)
        concave=_s(record,'ConcaveNormal',0.)
        if concave:
            weight=context.get('vertex_color_green')
            if weight is None:
                weight=literal(1.);diagnostics.append('vertex color green assumed white (UE null color stream)')
            mask=expr('texture',reference=SLICE_MASK,role='data',uv=coords,stage='DR.Source',name='Trim slice mask')
            weight=mul(mul(sat(channel(mask,0)),_k(weight)),_k(concave))
            nx,ny=add(nx,mul(weight,sub(_k(1.),mul(nx,_k(2.))))),add(ny,mul(weight,sub(_k(1.),mul(ny,_k(2.)))))
        bx,by=sub(mul(nx,_k(2.)),_k(1.)),sub(mul(ny,_k(2.)),_k(1.))
        n=(bx,by,_reconstruct_z(bx,by))
        u,v=_uv(0)
        ov=texture(record,'Overlay','color',overlay_coordinates(record,tu,tv)) if overlay or blend else None
        if blend:
            btile,boffset=_s(record,'BlendTextureTile',1.),_s(record,'BlendTextureOffset',1.)
            bcoords=expr('combine',add(mul(u,_k(btile)),_k(boffset)),add(mul(v,_k(btile)),_k(boffset)),stage='DR.Coordinates',name='Native blend texture coordinates')
            bx2,by2=_decode_xy(texture(record,'NOH Blend','data',bcoords));bz2=_reconstruct_z(bx2,by2)
            bs=_s(record,'Blend Texture Normal Strength',1.)
            bn=(mul(bx2,_k(bs)),mul(by2,_k(bs)),add(mul(sub(bz2,_k(1.)),_k(bs)),_k(1.)))
            bmask=_range_mask(channel(ov,0),_s(record,'Blend Mask Range Low',0.),_s(record,'Blend Mask Range High',1.))
            n=tuple(mix(x,y,bmask) for x,y in zip(n,bn))
        detail=detail_sample(record,channel(nmx,3),context,planar) if use_detail else None
        if detail is not None:
            dx,dy=_decode_xy(detail);dni=_k(_s(record,'Detail Normal Intensity',1.))
            n=(add(n[0],mul(dx,dni)),add(n[1],mul(dy,dni)),n[2])
        normal_ts=expr('normalize',expr('combine',*n),stage='DR.Details',name='Native trim normal')
        c=_grade(rgb(cr),_s(record,'DesaturateTrim',0.),_s(record,'Color Contrast',0.))
        b=channel(nmx,2)
        tint=_range_mask(b,_s(record,'Tint Range Low',0.),_s(record,'Tint Range High',1.))
        a=mix(c,mul(c,_k(_c(record,'Tint Color 01'))),mul(tint,b))
        mi=None
        if overlay:a,mi=overlay_layer(record,a,ov)
        if blend:
            bc=texture(record,'CR Blend','color',bcoords);bcrgb=rgb(bc)
            bcol=mul(_lerp(bcrgb,_luma(bcrgb),_s(record,'Blend Texture Desaturation',0.)),_k(tuple(2.*x for x in _c(record,'Blend Tint'))))
            a=mix(a,bcol,bmask)
        if detail is not None:a=detail_color(record,a,detail)
        mm=mul(b,_k(_s(record,'Master Metalness Multiply',1.)))
        metallic=sat(mm) if mi is None else sat(mix(mm,mul(mm,_k(2.*_s(record,'Overlay_Metalness',.5))),sat(mi)))
        ndotv=context.get('vertex_ndotv')
        if ndotv is None:
            ndotv=expr('dot',expr('geometry_normal'),expr('incoming'),stage='DR.Coordinates',name='Blender vertex-normal view transport')
            diagnostics.append('Blender shading-normal/view transport; native unnormalized vertex interpolant may differ on curved meshes')
        fresnel=expr('power',expr('max',expr('abs',sub(_k(1.),expr('max',_k(ndotv),_k(0.)))),_k(.0001)),_k(_s(record,'Fresnel Exponent',5.)))
        specular=expr('min',add(expr('min',fresnel,_k(1.)),_k(.5)),_k(1.))
        r=channel(cr,3);r=mix(r,mul(r,_k(_s(record,'Roughness Tint',1.))),tint)
        if mi is not None:r=mix(r,mul(mul(r,mi),_k(2.*_s(record,'Overlay_Roughness_Strength',1.))),sat(mi))
        if blend:r=mix(r,channel(bc,3),mul(bmask,_k(_s(record,'BlendTextureAffectRoughness',1.))))
        if detail is not None:r=detail_roughness(record,r,detail)
        r=sat(sub(r,expr('min',fresnel,_k(.25))))
        outputs={'base_color':staged(sat(a),'DR.Surface','Native trim albedo'),'normal':normal_ts,'metallic':metallic,
                 'roughness':staged(r,'DR.Surface','Native trim fresnel roughness'),'specular':staged(specular,'DR.Surface','Native trim fresnel specular')}
        return plan(record,'prop_trim',outputs,diagnostics,TRIM_EVIDENCE)
    except (ValueError,KeyError) as error:return refuse(str(error))

COMPILERS={TILEABLE_MASTER:compile_tileable_metal,TRIM_MASTER:compile_prop_trim}

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
