"""Dedicated face/eye/mouth profiles, with renderer differences stated in each report."""
from .masked import lerp,vlerp,saturate_color


def smooth(g,value):
    value=g.clamp(value)
    return g.mul(g.mul(value,value),g.sub(3,g.mul(2,value)))


def rnm_numerator(g,base,detail):
    base=g.vector('ADD',base,(0,0,1));detail=g.vector('MULTIPLY',detail,(-1,-1,1))
    return g.vector('SUBTRACT',g.vector('SCALE',base,scale=g.vector('DOT_PRODUCT',base,detail)),
                    g.vector('SCALE',detail,scale=g.split(base)[2]))


def face(g,r):
    s,c,t=r['scalars'],r['colors'],r['textures']
    def S(k,d=0):return g.parameter(k,s.get(k,d))
    def C(k,d=(1,1,1,1)):return g.parameter(k,c.get(k,d),'Color')
    uv=g.uv(0)
    base=g.image(t.get('BaseColor') or t.get('PM_Diffuse',''),uv,label='Face colour and cavity alpha')
    macro=g.image(t.get('Normal') or t.get('PM_Normals',''),uv,srgb=False,label='Face normal · RG normal / B response')
    controls=g.image(t.get('Mask Texture',''),uv,srgb=False,label='Shared face surface masks')
    micro=g.image(t.get('Micro Normal',''),g.transform_uv(0,S('Tile_Size',40)),srgb=False,label='Shared micro normal')
    zones=g.image(t.get('Zone Masks',''),uv,srgb=False,label='Shared face zones')
    if not base:g.finish((.5,.25,.18,1));return
    with g.block('Skin cavity colour and surface response') as b:
        cavity=base['a'];mask=controls['b'] if controls else 1
        micro_cavity=b.math('MAXIMUM',b.sub(1,mask),micro['b'] if micro else 0)
        amount=b.mul(b.add(1,b.mul(b.sub(cavity,1),b.parameter('Cavity Color Intensity',s.get('Cavity Color Intensity',.2)))),
                     b.add(1,b.mul(b.sub(micro_cavity,1),b.parameter('MicroCavity Color Intensity',s.get('MicroCavity Color Intensity',0)))))
        color=vlerp(b,b.parameter('Cavity Color',c.get('Cavity Color',(.04,.01,0,1)),'Color'),base['color'],amount)
        rough=macro['b'] if macro else .5
        zone_rough=lerp(b,b.parameter('Zone_Roughness',s.get('Zone_Roughness',.54)),1,zones['g'] if zones else 0)
        rough=b.math('MINIMUM',rough,zone_rough)
        rough=b.math('MINIMUM',rough,b.math('MAXIMUM',b.sub(1,b.parameter('Lip Roughness',s.get('Lip Roughness',0))),mask))
        rough=b.add(b.mul(rough,b.parameter('RoughnessMultiplier',s.get('RoughnessMultiplier',1))),
                    b.mul(b.sub(1,cavity),b.parameter('Cavity Roughness',s.get('Cavity Roughness',.8))))
        rough=b.add(rough,b.mul(b.sub(1,micro_cavity),b.parameter('Micro Cavity Roughness',s.get('Micro Cavity Roughness',.1))))
        spec=lerp(b,b.parameter('LipSpecular',s.get('LipSpecular',.35)),b.parameter('Specular',s.get('Specular',.25)),mask)
        spec=b.mul(spec,b.add(1,b.mul(b.sub(cavity,1),b.parameter('Specular Cavity Amount',s.get('Specular Cavity Amount',1)))))
        spec=b.mul(spec,b.add(1,b.mul(b.sub(micro_cavity,1),b.parameter('Specular MicroCavity Amount',s.get('Specular MicroCavity Amount',.1)))))
        color=b.export(color,'Skin colour','Color');rough=b.export(rough,'Skin roughness');spec=b.export(spec,'Skin specular')
    normal=vlerp(g,g.decode_normal(macro),(0,0,1),S('NormalFlatness',0))
    if micro:
        mn=g.decode_normal(micro);mx,my,mz=g.split(mn)
        mn=g.combine(g.mul(mx,S('MicroNormal_Intensity',.8)),g.mul(my,S('MicroNormal_Intensity',.8)),mz)
        normal=rnm_numerator(g,normal,vlerp(g,(0,0,1),mn,controls['b'] if controls else 1))
    if r['switches'].get('UseHair',True):
        with g.block('Stubble and facial hair · colour masks, never skin opacity') as b:
            for ref,amount,root,tip,rough_name,spec_name in [
                ('Hair NXA','Hair Mask Amount','HairCap Root Color','HairCap Tip Color','Hair Roughness','Hair Specular'),
                ('FacialHairNXA','FacialHair Mask Amount','FacialHair Root Color','FacialHair Tip Color','FacialHair Roughness','FacialHair Specular')]:
                if s.get(amount,1 if ref=='Hair NXA' else 0)<=0:continue
                hair=b.image(t.get(ref,''),b.uv(0),srgb=False,label=ref+' · RG normal / B tip / A coverage')
                if not hair:continue
                coverage=b.clamp(b.mul(hair['a'],b.parameter(amount,s.get(amount,1))))
                tint=vlerp(b,b.parameter(root,c.get(root,(0,0,0,1)),'Color'),b.parameter(tip,c.get(tip,(.04,.025,.02,1)),'Color'),hair['b'])
                color=vlerp(b,color,tint,coverage)
                rough=lerp(b,rough,b.parameter(rough_name,s.get(rough_name,.8)),coverage)
                spec=lerp(b,spec,b.parameter(spec_name,s.get(spec_name,.1)),coverage)
                normal=vlerp(b,normal,b.decode_normal(hair),coverage)
            color=b.export(color,'Face colour','Color');rough=b.export(rough,'Face roughness')
            spec=b.export(spec,'Face specular');normal=b.export(normal,'Face normal','Vector')
    # The compiled skin's alpha is its fixed neck-fade texture, not either hair map.
    neck=g.image(t.get('Neck Fade',''),uv,srgb=False,label='Skin opacity · fixed neck fade')
    alpha=1
    if neck:
        fade=g.clamp(g.mul(neck['r'],S('FadeAmount',1.16)))
        alpha=g.clamp(g.add(g.mul(g.math('FLOOR',g.add(neck['r'],S('s',.5))),g.sub(1,fade)),fade))
    radius=tuple(max(v,1e-4) for v in c.get('SubsurfaceColor',(.177,.043,.033,1))[:3])
    g.report['skin_opacity_source']=t.get('Neck Fade','opaque')
    g.report['implemented_stages']=['face-specific shared bindings','base colour and cavity alpha','normal B surface response',
        'shared zones and lip response','micro normal RNM numerator','stubble/facial hair colour, roughness, specular and normals',
        'separate neck-fade skin opacity','editable Blender subsurface approximation']
    g.report['warnings'].append('Face profile omits runtime body paint/weather and view-dependent face fresnel; Blender subsurface is an approximation')
    g.finish(saturate_color(g,color),g.clamp(rough),normal=g.vector('NORMALIZE',normal),specular=g.clamp(spec),alpha=alpha,
             response={'Subsurface Weight':g.parameter('Blender skin scatter',.08),'Subsurface Radius':radius})


def iris_coordinates(g,r):
    s=r['scalars']
    with g.block('Eye coordinates · iris radius, pupil and limbus') as b:
        uv=b.uv(0);scale=b.math('MAXIMUM',b.parameter('ScaleByCenter',s.get('ScaleByCenter',1)),1e-5)
        centered=b.vector('SUBTRACT',uv,(.5,.5,0));centered=b.vector('SCALE',centered,scale=b.div(1,scale))
        sclera=b.vector('ADD',centered,(.5,.5,0))
        distance=b.vector('LENGTH',centered)
        radius=b.math('MAXIMUM',b.parameter('Iris UV Radius',s.get('Iris UV Radius',.17)),.001)
        width=b.math('MAXIMUM',b.parameter('LimbusUVWidthColor',s.get('LimbusUVWidthColor',.035)),1e-5)
        mask=smooth(b,b.div(b.sub(radius,distance),width))
        radial=b.div(distance,b.mul(radius,2))
        pupil=b.clamp(b.mul(b.parameter('PupilScale',s.get('PupilScale',.7)),b.sub(1,b.mul(radial,2))))
        direction=b.vector('SCALE',centered,scale=b.div(1,b.math('MAXIMUM',distance,1e-8)))
        offset=b.vector('SCALE',direction,scale=b.mul(.5,b.sub(1,pupil)))
        iris=b.vector('ADD',offset,(.5,.5,0))
        limbus=b.sub(1,b.math('POWER',b.vector('LENGTH',b.vector('SCALE',offset,scale=b.parameter('LimbusDarkScale',s.get('LimbusDarkScale',2.15)))),b.parameter('LimbusPow',s.get('LimbusPow',8))))
        return (b.export(sclera,'Sclera UV','Vector'),b.export(iris,'Iris UV','Vector'),b.export(mask,'Iris mask'),
                b.export(distance,'Eye radius'),b.export(limbus,'Limbus brightness'))


def eyes(g,r):
    s,c,t=r['scalars'],r['colors'],r['textures']
    suv,iuv,mask,distance,limbus=iris_coordinates(g,r)
    sclera=g.image(t.get('ScleraColor',''),suv,label='Shared sclera');iris=g.image(t.get('IrisColor',''),iuv,label='Shared iris')
    with g.block('Eye normal coordinates') as b:
        centered=b.vector('SUBTRACT',b.uv(0),(.5,.5,0))
        scale=b.math('MAXIMUM',b.parameter('NormalUVScale',s.get('NormalUVScale',1)),1e-5)
        normal_uv=b.export(b.vector('ADD',b.vector('SCALE',centered,scale=b.div(1,scale)),(.5,.5,0)),'Normal UV','Vector')
    normal_tex=g.image(t.get('NormalMap') or t.get('Eye Normals',''),normal_uv,srgb=False,label='Wet eye normal')
    if not sclera or not iris:g.finish((.6,.6,.6,1));return
    with g.block('Iris and sclera colours, corner shadow and surface') as b:
        sc=b.vector('SCALE',b.cmul(sclera['color'],b.parameter('ScleraTint',c.get('ScleraTint',(1,1,1,1)),'Color')),scale=b.parameter('ScleraBrightness',s.get('ScleraBrightness',.9)))
        ic=iris['color']
        rotate=b.node('ShaderNodeVectorRotate','Iris hue rotation',rotation_type='AXIS_ANGLE')
        b.set(rotate.inputs['Vector'],ic);rotate.inputs['Axis'].default_value=(.577350269,)*3
        b.set(rotate.inputs['Angle'],b.mul(b.parameter('IrisHueShift',s.get('IrisHueShift',0)),6.283185307179586))
        ic=rotate.outputs[0]
        ic=b.vector('SCALE',b.cmul(ic,b.parameter('IrisHueTint',c.get('IrisHueTint',(1,1,1,1)),'Color')),scale=b.mul(limbus,b.parameter('IrisBrightness',s.get('IrisBrightness',1.2))))
        corner=b.clamp(b.div(b.sub(1,b.div(distance,b.math('MAXIMUM',b.parameter('ShadowRadius',s.get('ShadowRadius',.65)),1e-5))),
                              b.math('MAXIMUM',b.sub(1,b.parameter('ShadowHardness',s.get('ShadowHardness',.345))),1e-5)))
        shadow=vlerp(b,b.parameter('EyeCornerDarknessColor',c.get('EyeCornerDarknessColor',(.23,.083,.032,1)),'Color'),(1,1,1),corner)
        color=b.cmul(vlerp(b,sc,ic,mask),shadow)
        rough=lerp(b,b.parameter('ScleraRoughness',s.get('ScleraRoughness',.1)),b.parameter('IrisRoughness',s.get('IrisRoughness',.1)),mask)
        spec=lerp(b,b.parameter('SpecularityScelera',s.get('SpecularityScelera',1)),b.parameter('SpecularityIris',s.get('SpecularityIris',1)),mask)
        color=b.export(saturate_color(b,color),'Eye colour','Color');rough=b.export(rough,'Eye roughness');spec=b.export(spec,'Eye specular')
    flat=lerp(g,g.parameter('FlattenNormal',s.get('FlattenNormal',.95)),1,mask)
    normal=vlerp(g,g.decode_normal(normal_tex),(0,0,1),flat)
    g.report['implemented_stages']=['explicit shared iris/sclera maps','centered iris and pupil coordinates','smooth limbus','authored iris hue/tint/brightness',
        'sclera tint/brightness','radial corner darkness','iris/sclera roughness and specular','editable Blender corneal coat']
    g.report['warnings'].append('Eye view-dependent iris refraction, concavity, engine eye shading and runtime effects use a static Blender approximation')
    g.finish(color,g.clamp(rough),normal=g.vector('NORMALIZE',normal),specular=g.clamp(spec),
             response={'Coat Weight':g.parameter('Blender corneal coat',.35),'Coat Roughness':g.parameter('Blender coat roughness',.08)})


def build_head(g,r):
    kind=r['family'];s,c,t=r['scalars'],r['colors'],r['textures']
    if kind=='head_face':return face(g,r)
    if kind=='head_eyes':return eyes(g,r)
    if kind=='head_teeth':
        base=g.image(t.get('BaseColor',''),g.uv(0),label='Fixed shared teeth and mouth colour')
        normal=g.image(t.get('Normal',''),g.uv(0),srgb=False,label='Fixed shared teeth and mouth normal')
        g.report['implemented_stages']=['fixed master mouth textures','decoded mouth normals','compiled neutral roughness 0.5']
        return g.finish(base['color'] if base else (.8,.72,.58,1),.5,normal=g.decode_normal(normal))
    if kind=='head_eyelashes':
        tex=g.image(t.get('Eyelash Texture',''),g.uv(0),srgb=False,label='Shared eyelashes · R coverage')
        mask=tex['r'] if tex else 0
        color=vlerp(g,g.parameter('Eyelash Color',c.get('Eyelash Color',(.01,.005,.003,1)),'Color'),
                    g.parameter('Eyelash Color 2',c.get('Eyelash Color 2',(0,0,0,1)),'Color'),mask)
        alpha=g.clamp(g.mul(g.math('POWER',g.math('MAXIMUM',mask,0),g.parameter('OpacityPower',s.get('OpacityPower',1))),
                             g.mul(g.parameter('Lash Opacity',s.get('Lash Opacity',1)),g.parameter('Visibility',s.get('Visibility',1)))))
        g.report['implemented_stages']=['shared eyelash R coverage','authored two-colour palette','opacity power, lash opacity and visibility']
        return g.finish(saturate_color(g,color),s.get('Roughness',.5),specular=s.get('Specular',0),alpha=alpha)
    if kind=='head_occlusion':
        # Clear in every supplied base instance unless the authored shadow controls enable it.
        x,y,_=g.split(g.uv(0));coverage=0
        for edge,v in [('Outer',x),('Inner',g.sub(1,x)),('Bottom',y),('Top',g.sub(1,y))]:
            radius=g.parameter(edge+'Shadow_Radius',s.get(edge+'Shadow_Radius',0))
            hardness=g.parameter(edge+'Shadow_Hardness',s.get(edge+'Shadow_Hardness',0))
            edge_mask=g.clamp(g.div(g.sub(1,g.div(v,g.math('MAXIMUM',radius,1e-5))),g.math('MAXIMUM',g.sub(1,hardness),1e-5)))
            coverage=g.math('MAXIMUM',coverage,edge_mask)
        alpha=g.clamp(g.mul(coverage,g.parameter('ShadowMultiply',s.get('ShadowMultiply',0))))
        tint=g.parameter('ShadowTint',c.get('ShadowTint',(0,0,0,1)),'Color')
        g.report['implemented_stages']=['separate eye occlusion','authored edge shadow radii, hardness and opacity','clear base shell when ShadowMultiply is zero']
        g.report['warnings'].append('Eye occlusion scene-colour blur and runtime shadow overrides are not reconstructed')
        return g.finish((0,0,0,1),1,specular=0,emission=tint,alpha=alpha)
    normal=None
    if kind=='head_lacrimal':
        with g.block('Lacrimal detail coordinates') as b:
            u,v,_=b.split(b.uv(0))
            uv=b.export(b.combine(b.mul(u,b.parameter('DetailUTiling',s.get('DetailUTiling',3))),
                                   b.mul(v,b.parameter('DetailVTiling',s.get('DetailVTiling',1)))),'Detail UV','Vector')
        tex=g.image(t.get('Lacrimal Normal',''),uv,srgb=False,label='Fixed lacrimal normal')
        normal=g.decode_normal(tex,s.get('MicroNormalStrength',.15));alpha=s.get('Opacity',.005)
    else:alpha=s.get('o',.5)
    g.report['implemented_stages']=['separate transparent tear-line/lacrimal surface','authored opacity and roughness','fixed normal where present']
    g.report['warnings'].append('Tear-line scene-depth fade and refraction use a Blender approximation')
    return g.finish((1,1,1,1),s.get('Roughness',s.get('roughness',.2)),s.get('Metallic',0),normal,s.get('Specular',.5),alpha=alpha,
                    response={'Transmission Weight':1,'IOR':1.336})
