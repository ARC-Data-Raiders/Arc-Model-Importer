"""Shared body skin and the traced UV1 region coverage shader."""
from .heads import rnm_numerator
from .masked import vlerp,saturate_color

REGIONS=['Chest','Hips','UpperArm','LowerArm','Thigh','Calf','Foot','Hand','Thumb','Index','Middle','Ring','Pinky']


def region_name(index,right=False):
    name=REGIONS[index]
    return name if index<2 else ('R_' if right else 'L_')+name


def coverage_interval(value):
    split,below,above=value[:3]
    return split*(1-below),split+(1-split)*above


def body_coverage(g,colors,hidden=False):
    if hidden:return 0
    with g.block('Body coverage · outfit regions and cut boundaries') as b:
        # Graph.uv has already restored Unreal's V orientation.
        u,v,_=b.split(b.uv(1))
        region=b.sub(b.math('CEIL',u),1)
        side=b.math('GREATER_THAN',b.math('FLOOR',v),.5)
        position=b.math('FRACT',v);total=0
        for index in range(len(REGIONS)):
            chosen=b.math('COMPARE',region,index,1e-5)
            for right in ([False,True] if index>1 else [False]):
                name=region_name(index,right);value=colors.get(name,(1,1,1,0))
                control=b.parameter(name+' · split / below / above',value,'Color')
                split,below,above=b.split(control)
                low=b.mul(split,b.sub(1,below))
                high=b.add(split,b.mul(b.sub(1,split),above))
                inside=b.mul(b.sub(1,b.math('LESS_THAN',position,low)),b.sub(1,b.math('GREATER_THAN',position,high)))
                if index>1:inside=b.mul(inside,side if right else b.sub(1,side))
                total=b.add(total,b.mul(chosen,inside))
        return b.export(b.clamp(total),'Visible body')


def build_body_skin(g,r):
    s,c,t=r['scalars'],r['colors'],r['textures']
    uv=g.uv(0)
    base=g.image(t.get('BodyColorMap',''),uv,label='Body skin colour')
    macro=g.image(t.get('BodyNormalMap',''),uv,srgb=False,label='Body normal RGB · roughness A')
    micro=g.image(t.get('MicroNormal',''),g.transform_uv(0,s.get('MicroNormal_Tiling_Body',13)),srgb=False,label='Skin micro normal')
    color=base['color'] if base else (.5,.3,.2,1)
    bw=g.vector('DOT_PRODUCT',color,(.3,.59,.11))
    color=vlerp(g,color,g.combine(bw,bw,bw),g.parameter('SkinDesaturation',s.get('SkinDesaturation',0)))
    tint=g.parameter('ColorMultiply',c.get('ColorMultiply',(.5,.5,.5,1)),'Color')
    color=g.vector('MULTIPLY',color,g.vector('SCALE',tint,scale=2))
    normal=g.vector('SUBTRACT',g.vector('SCALE',macro['color'],scale=2),(1,1,1)) if macro else (0,0,1)
    if micro:
        detail=vlerp(g,g.decode_normal(micro),(0,0,1),g.parameter('MicroNormal Flatness',s.get('MicroNormal Flatness',0)))
        normal=rnm_numerator(g,normal,g.vector('NORMALIZE',detail))
    normal=g.vector('NORMALIZE',normal)
    geo=g.node('ShaderNodeNewGeometry')
    facing=g.sub(1,g.math('MAXIMUM',g.vector('DOT_PRODUCT',g.normal_to_world(normal),geo.outputs['Incoming']),0))
    fresnel=g.add(g.mul(g.math('POWER',facing,3),.96),.04)
    rough_base=macro['a'] if macro else .5
    rough=g.clamp(g.mul(g.add(rough_base,g.mul(fresnel,g.sub(.8,rough_base))),g.parameter('Roughness',s.get('Roughness',1))))
    alpha=body_coverage(g,r.get('body_masks',c),r.get('body_hidden',False))
    g.mat.surface_render_method='DITHERED';g.mat.use_backface_culling=False
    g.report['body_mask_uv_channel']=1
    g.report['body_masks']=r.get('body_masks',{})
    g.report['body_mask_sources']=r.get('body_mask_sources',[])
    g.report['implemented_stages']=['authored gender skin instance','body colour and desaturation','RGB macro normal',
        'micro normal RNM','normal-alpha roughness and static fresnel','UV1 region coverage from active outfit parameters']
    g.report['warnings'].append('Body skin omits runtime wet/snow/body-paint branches; Blender subsurface and view response differ from Unreal')
    g.finish(saturate_color(g,color),rough,normal=normal,specular=g.parameter('Specular',s.get('Specular',.35)),alpha=alpha,
             response={'Subsurface Weight':g.parameter('Blender skin scatter',.08),
                       'Subsurface Radius':tuple(max(v,1e-4) for v in c.get('SubsurfaceColor',(.177,.043,.033,1))[:3])})
