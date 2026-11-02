"""Portable Tileable Metal / PropTrim regressions against an instruction replay of the cooked SM5 programs."""
try:
    from .native_test_support import ROOT,namespace
except ImportError:
    from native_test_support import ROOT,namespace
import math,unittest
from material_runtime.core import MaterialRecord,evaluate,expr
from material_runtime import tileable_trim as tt

LUMA=(.2126,.7152,.0722)
TEX={'NXX/NMX Texture':'/Game/T/NMX','CR Texture':'/Game/T/CR','Overlay':'/Game/T/OV','CR Blend':'/Game/T/BCR','NOH Blend':'/Game/T/BNOH'}
HULL_01={'Tiling':1.5,'Normal Strength':1.,'Detail Tiling':4.,'2:1 Ratio':0.,'Detail Normal Intensity':.1,'Desaturate':0.,'Color Contrast':0.,
 'Tint Range Low':0.,'Tint Range High':1.,'MaskTintFromMetalness':0.,'OverlayTiling':4.5,'Use UV0 as Overlay UVs':0.,'Use UV1 as Overlay UVs':0.,
 'UVOffset':.456,'Rotation Angle':0.,"Use Rotate UV's":1.,'OverlayDesaturation':.7,'OverlayRange Min':0.,'OverlayRange Max':.096,'OverlayRange Invert':1.,
 'Overlay_BaseColor_Strength':.35,'Detail Color Shift':.25,'Detail Color Intensity':.1,'TintMetalness':0.,'Master Metalness Multiply':.75,
 'Overlay_Metalness':.308,'Roughness Tint':1.26963,'Overlay_Roughness_Strength':.459586,'Detail Roughness Multiplier':1.,'Detail Roughness Influence':.456}
HULL_COLORS={'Tint Color 01':(.273,.302,.288,1.),'Overlay Color':(.5,.5,.5,1.)}
TILE_ON={'Turn on basic tiling':True,'Use Detail':True,'Use Tint':True,'Use NMX':True,'UseOverlay':True,'ProjectionObject':True,'Use Snow':True,'Use Slice Mask':True,'Use Prim Data Weathering Index':True}
TRIM_HULL_02={'Scale Axis':0.,'Mask on UVs':0.,'UV Mode':0.,'Tiling U':1.,'Tiling V':1.,'ConcaveNormal':0.,'BlendTextureTile':3.6,'BlendTextureOffset':.711551,
 'Blend Texture Normal Strength':1.,'OverlayTiling':3.31375,'Use UV0 as Overlay UVs':0.,'Use UV1 as Overlay UVs':0.,'UVOffset':1.95939,'Rotation Angle':0.,
 "Use Rotate UV's":1.,'Blend Mask Range Low':0.,'Blend Mask Range High':1.,'Detail Tiling':3.,'2:1 Ratio':0.,'Detail Normal Intensity':.2,'DesaturateTrim':0.,
 'Color Contrast':0.,'Tint Range Low':0.,'Tint Range High':1.,'OverlayDesaturation':.7,'OverlayRange Min':.293236,'OverlayRange Max':.706037,'OverlayRange Invert':1.,
 'Overlay_BaseColor_Strength':1.5,'Blend Texture Desaturation':0.,'Detail Color Shift':1.,'Detail Color Intensity':1.,'Master Metalness Multiply':.7,
 'Overlay_Metalness':.432,'Fresnel Exponent':5.,'Roughness Tint':.724,'Overlay_Roughness_Strength':.624,'BlendTextureAffectRoughness':1.,
 'Detail Roughness Multiplier':1.,'Detail Roughness Influence':.65}
TRIM_COLORS={'Tint Color 01':(.342,.399,.453,1.),'Overlay Color':(.707,.85,.87,1.),'Blend Tint':(.268,.268,.271,1.)}
TRIM_ON={'Use NMX':True,'Use Slice Mask':True,'Use Tint':True,'Align - UV or World':True,'BlendTextureUV0':True,'Enable Blend Textures':True,'Use Detail':True,
 'Turn on basic tiling':True,'UseOverlay':True,'RGB Tint Mask':True,'ProjectionObject':True}
POINTS=((.137,.412,.71,.29),(.583,.071,.33,.95),(.902,.665,.18,.44),(.256,.938,.62,.07),(.47,.53,.5,.5))

def frac(x):return x-math.floor(x)
def sample(ref,uv,attrs=None):
    layer=(attrs or {}).get('slice')
    seed=(sum(map(ord,ref))%97)/97.+(0. if layer is None else .131*layer)
    return tuple(frac(seed+.37*k+1.7*uv[0]+2.3*uv[1]+.11*k*uv[0]) for k in range(4))
def sat(x):return min(max(x,0.),1.)
def lerp(a,b,t):return a+(b-a)*t
def dot(a,b):return sum(x*y for x,y in zip(a,b))
def unit(v):
    length=math.sqrt(dot(v,v));return tuple(x/length for x in v)
def rcp_range(low,high):
    span=sat(high-low);return 1./span if span else 1e30
def planar(tri,scale,threshold):
    """4BA6B3D6 PS 11319 lines 94-119 / 56CA22DE PS 11382 lines 107-132 on UE local = (100x,-100y,100z)."""
    (px,py,pz),(nx,_,nz)=tri;x,y,z=px*100.*scale,-py*100.*scale,pz*100.*scale
    if abs(nz)>threshold:return x,y
    return (y,-z) if abs(nx)>threshold else (x,-z)
def detail(p,uv0,total,alpha,tri=None):
    if tri:
        du,dv=planar(tri,1./350.,.5)
    else:
        t=p['Detail Tiling'];du=uv0[0]*t;dv=uv0[1]*t;dv=dv+(dv*.5-dv)*p['2:1 Ratio']
    layer=min(max(round(alpha*8.),0),7)  # D3D array index: nearest even, clamped to depth
    return sample(tt.DETAIL_ARRAY,(du*total,dv*total),{'slice':layer})
def pow_guard(x,e):return 0. if x<=1.1920929e-07 else x**e
def overlay_uv(p,u,v,uv1,tri=None):
    if tri:
        ou,ov=planar(tri,1./p['Overlay Size'],.65);ou+=p['UVOffset'];ov+=p['UVOffset']
    else:
        ou,ov=u*.1*p['OverlayTiling'],v*.1*p['OverlayTiling']
        ou+=(u-ou)*p['Use UV0 as Overlay UVs'];ov+=(v-ov)*p['Use UV0 as Overlay UVs']
        ou+=(uv1[0]-ou)*p['Use UV1 as Overlay UVs'];ov+=(uv1[1]-ov)*p['Use UV1 as Overlay UVs']
        ou=ou*p['OverlayTiling']+p['UVOffset'];ov=ov*p['OverlayTiling']+p['UVOffset']
    a=p['Rotation Angle']/360.*6.28318548;du,dv=ou-.5,ov-.5
    ru,rv=du*math.cos(a)+dv*-math.sin(a)+.5,du*math.sin(a)+dv*math.cos(a)+.5
    return ou+(ru-ou)*p["Use Rotate UV's"],ov+(rv-ov)*p["Use Rotate UV's"]
def overlay(p,c,a,o):
    # Lines 93-110 (hull 8887): desaturate, x OverlayColor x2, x albedo x2, range mask, lerp by mi*strength.
    lum=dot(o[:3],LUMA);ovc=[(x+(lum-x)*p['OverlayDesaturation'])*k*2. for x,k in zip(o[:3],c['Overlay Color'])]
    blend=[x*y*2. for x,y in zip(a,ovc)]
    m=sat((o[0]-p['OverlayRange Min'])*rcp_range(p['OverlayRange Min'],p['OverlayRange Max']));mi=(m-(1.-m))*p['OverlayRange Invert']+(1.-m)
    w=sat(mi*p['Overlay_BaseColor_Strength']);return [x+(y-x)*w for x,y in zip(a,blend)],mi
def detail_rough(p,r,d):
    b=pow_guard(d[2],1.5);low=2.*r*b;high=1.-(2.-2.*r)*(1.-b)
    return r+((high if r>=.5 else low)*p['Detail Roughness Multiplier']-r)*p['Detail Roughness Influence']

def replay_tileable(p,c,uv0,uv1,total,use_overlay=True,use_nmx=True,tri=None):
    """M_Tileable_Metal SM5 base pass: C9073E65 pixel 8887 / 5C304D94 / 68D7EE33 / 4BA6B3D6 register order, wetness 0."""
    bu,bv=uv0[0]*p['Tiling'],uv0[1]*p['Tiling'];n4=sample('/Game/T/NMX',(bu,bv));cr=sample('/Game/T/CR',(bu,bv))
    bx,by=(n4[0]-.5)*2.,(n4[1]-.5)*2.;bz=math.sqrt(max(1.-(bx*bx+by*by),0.))
    d=detail(p,uv0,total,n4[3]);dx,dy=(d[0]-.5)*2.*p['Detail Normal Intensity'],(d[1]-.5)*2.*p['Detail Normal Intensity']
    n=unit((bx*p['Normal Strength']+dx,by*p['Normal Strength']+dy,bz))
    lum=dot(cr[:3],LUMA);c0=[sat((x+(lum-x)*p['Desaturate'])*((p['Color Contrast']+1.)-(-p['Color Contrast']))+(-p['Color Contrast'])) for x in cr[:3]]
    tint=sat((n4[2]-p['Tint Range Low'])*rcp_range(p['Tint Range Low'],p['Tint Range High']))
    a=[x+(x*k*2.-x)*tint for x,k in zip(c0,c['Tint Color 01'])]
    m=pow_guard(n4[2],2.2) if use_nmx else sat(-8.*n4[3]+1.)
    a=[y+(x-y)*m*p['MaskTintFromMetalness'] for x,y in zip(c0,a)]
    mi=None
    if use_overlay:a,mi=overlay(p,c,a,sample('/Game/T/OV',overlay_uv(p,uv0[0],uv0[1],uv1,tri)))
    a=[x+(x*p['Detail Color Shift']-x)*(1.-d[3])*p['Detail Color Intensity'] for x in a];a=[sat(x) for x in a]
    mm=m+(min(m+tint,1.)-m)*p['TintMetalness'];mm*=p['Master Metalness Multiply']
    metal=sat(mm) if mi is None else sat(mm+(mm*p['Overlay_Metalness']*2.-mm)*sat(mi))
    r=cr[3];r=tint*(r*p['Roughness Tint']-r)+r
    if mi is not None:r=sat(mi)*(r*(mi*p['Overlay_Roughness_Strength'])*2.-r)+r
    r=sat(detail_rough(p,r,d))
    return {'base_color':tuple(a),'normal':n,'metallic':metal,'roughness':r,'specular':.5}

def replay_trim(p,c,uv0,uv1,total,ndotv,overlay_on=True,blend_on=True,detail_on=True,vcg=1.,tri=None):
    """M_PropTrimPreset SM5 base pass: 5A8E3BEC pixel 11374 (843A774F/3B0B9786/6CC3E2BD drop stages; 56CA22DE planar detail), wetness 0."""
    tu,tv=uv0[0]*p['Tiling U'],uv0[1]*p['Tiling V'];n4=sample('/Game/T/NMX',(tu,tv));cr=sample('/Game/T/CR',(tu,tv))
    mask=sat(sample(tt.SLICE_MASK,(tu,tv))[0])*vcg
    nx,ny=[p['ConcaveNormal']*(((1.-x)-x)*mask)+x for x in n4[:2]]
    bx,by=(nx-.5)*2.,(ny-.5)*2.;n=[bx,by,math.sqrt(max(1.-(bx*bx+by*by),0.))]
    ou=overlay_uv(p,tu,tv,uv1);o=sample('/Game/T/OV',ou)
    if blend_on:
        bu,bv=uv0[0]*p['BlendTextureTile']+p['BlendTextureOffset'],uv0[1]*p['BlendTextureTile']+p['BlendTextureOffset']
        h=sample('/Game/T/BNOH',(bu,bv));hx,hy=(h[0]-.5)*2.,(h[1]-.5)*2.;hz=math.sqrt(max(1.-(hx*hx+hy*hy),0.))
        s=p['Blend Texture Normal Strength'];bn=(s*hx,s*hy,s*(hz-1.)+1.)
        bmask=sat((o[0]-p['Blend Mask Range Low'])*rcp_range(p['Blend Mask Range Low'],p['Blend Mask Range High']))
        n=[x+(y-x)*bmask for x,y in zip(n,bn)]
    d=None
    if detail_on:
        d=detail(p,uv0,total,n4[3],tri);n[0]+=(d[0]-.5)*2.*p['Detail Normal Intensity'];n[1]+=(d[1]-.5)*2.*p['Detail Normal Intensity']
    n=unit(n)
    lum=dot(cr[:3],LUMA);col=[sat((x+(lum-x)*p['DesaturateTrim'])*(1.+2.*p['Color Contrast'])-p['Color Contrast']) for x in cr[:3]]
    tint=sat((n4[2]-p['Tint Range Low'])*rcp_range(p['Tint Range Low'],p['Tint Range High']))
    a=[x+(x*k-x)*tint*n4[2] for x,k in zip(col,c['Tint Color 01'])]
    mi=None
    if overlay_on:a,mi=overlay(p,c,a,o)
    if blend_on:
        bc=sample('/Game/T/BCR',(bu,bv));bl=dot(bc[:3],LUMA)
        a=[x+((y+(bl-y)*p['Blend Texture Desaturation'])*k*2.-x)*bmask for x,y,k in zip(a,bc[:3],c['Blend Tint'])]
    if d is not None:a=[x+(x*p['Detail Color Shift']-x)*(1.-d[3])*p['Detail Color Intensity'] for x in a]
    a=[sat(x) for x in a]
    mm=n4[2]*p['Master Metalness Multiply'];metal=sat(mm) if mi is None else sat(mm+(mm*p['Overlay_Metalness']*2.-mm)*sat(mi))
    f=max(abs(1.-max(ndotv,0.)),.0001)**p['Fresnel Exponent'];spec=min(min(f,1.)+.5,1.)
    r=cr[3];r=tint*(r*p['Roughness Tint']-r)+r
    if mi is not None:r=sat(mi)*(r*(mi*p['Overlay_Roughness_Strength'])*2.-r)+r
    if blend_on:r=p['BlendTextureAffectRoughness']*((bc[3]-r)*bmask)+r
    if d is not None:r=detail_rough(p,r,d)
    r=sat(sat(r-min(f,.25)))
    return {'base_color':tuple(a),'normal':n,'metallic':metal,'roughness':r,'specular':spec}

def record(master,scalars,colors,switches):return MaterialRecord('/Game/Tests/MI',master=master,scalars=scalars,colors=colors,textures=TEX,switches=switches)
def inputs(point,tri=None):
    base={'uv':{0:(point[0],point[1],0.),1:(point[2],point[3],0.)},'texture':sample}
    return {**base,'object_position':tri[0],'object_normal':tri[1]} if tri else base
# Blender object position (m) and object-space normal: z-dominant, x-dominant, y-dominant, and both sides of each threshold.
TRI=(((1.23,-4.56,7.89),(.1,.2,.974679)),((-3.1,2.2,.4),(.9,.3,.316228)),((.7,5.3,-2.6),(.2,.95,.239792)),
     ((2.5,1.5,-.5),(.0,.76,.649923)),((-1.9,-.8,3.3),(.651,.759,.0)),((4.4,-2.1,1.7),(.5,.0,.866025)),((.3,.9,-1.2),(.501,.865,0.)))

class NativeTileableTrimTests(unittest.TestCase):
    def check(self,plan,expected,point,tri=None):
        self.assertTrue(plan.outputs,plan.diagnostics)
        for key,value in expected.items():
            actual=evaluate(plan.outputs[key],inputs(point,tri))
            for left,right in zip(actual if isinstance(actual,tuple) else (actual,),value if isinstance(value,tuple) else (value,)):
                with self.subTest(output=key,point=point):self.assertAlmostEqual(left,right,delta=1e-9)
    def test_hull_overlay_permutation_matches_instruction_replay(self):
        plan=tt.compile_tileable_metal(record(tt.TILEABLE_MASTER,HULL_01,HULL_COLORS,TILE_ON),{'object_scale_sum':3.})
        for point in POINTS:self.check(plan,replay_tileable(HULL_01,HULL_COLORS,point[:2],point[2:],3.),point)
    def test_hull_no_overlay_and_nxx_permutations_match_replay(self):
        params={**HULL_01,'MaskTintFromMetalness':.6,'TintMetalness':.4,'Desaturate':.3,'Color Contrast':.2,'2:1 Ratio':1.}
        for use_nmx in (True,False):
            switches={**TILE_ON,'UseOverlay':False,'TriPlanar Overlay':True,'Use NMX':use_nmx}
            plan=tt.compile_tileable_metal(record(tt.TILEABLE_MASTER,params,HULL_COLORS,switches),{'object_scale_sum':2.5})
            for point in POINTS:self.check(plan,replay_tileable(params,HULL_COLORS,point[:2],point[2:],2.5,use_overlay=False,use_nmx=use_nmx),point)
    def test_overlay_uv_selectors_and_rotation_match_replay(self):
        params={**HULL_01,'Use UV0 as Overlay UVs':.25,'Use UV1 as Overlay UVs':.5,'Rotation Angle':33.,"Use Rotate UV's":.75,'OverlayRange Invert':.3}
        plan=tt.compile_tileable_metal(record(tt.TILEABLE_MASTER,params,HULL_COLORS,TILE_ON),{'object_scale_sum':3.})
        for point in POINTS:self.check(plan,replay_tileable(params,HULL_COLORS,point[:2],point[2:],3.),point)
    def test_trim_hull_blend_overlay_detail_matches_instruction_replay(self):
        plan=tt.compile_prop_trim(record(tt.TRIM_MASTER,TRIM_HULL_02,TRIM_COLORS,TRIM_ON),{'object_scale_sum':3.,'vertex_ndotv':.35})
        for point in POINTS:self.check(plan,replay_trim(TRIM_HULL_02,TRIM_COLORS,point[:2],point[2:],3.,.35),point)
    def test_trim_reduced_permutations_match_replay(self):
        params={**TRIM_HULL_02,'ConcaveNormal':.8,'Tiling U':2.,'Tiling V':.5,'Blend Texture Desaturation':.4,'BlendTextureAffectRoughness':.6}
        for overlay_on,blend_on,detail_on in ((True,False,False),(True,True,False),(False,True,False),(False,False,True)):
            switches={**TRIM_ON,'UseOverlay':overlay_on,'Enable Blend Textures':blend_on,'Use Detail':detail_on}
            plan=tt.compile_prop_trim(record(tt.TRIM_MASTER,params,TRIM_COLORS,switches),{'object_scale_sum':3.,'vertex_ndotv':.8,'vertex_color_green':.6})
            for point in POINTS:self.check(plan,replay_trim(params,TRIM_COLORS,point[:2],point[2:],3.,.8,overlay_on,blend_on,detail_on,vcg=.6),point)
    def test_detail_slice_rounds_to_nearest_even_and_clamps(self):
        alpha=lambda value:{'uv':{0:(.3,.6,0.)},'texture':lambda ref,uv,attrs:(.5,.5,0.,value) if ref=='/Game/T/NMX' else (float(attrs.get('slice',-1)),)*4}
        rec=record(tt.TILEABLE_MASTER,HULL_01,{},TILE_ON)
        selected=tt.detail_sample(rec,expr('channel',tt.texture(rec,'NXX/NMX Texture','data'),index=3),{'object_scale_sum':3.})
        for value,layer in ((0.,0),(.0625,0),(.1875,2),(.3125,2),(.5,4),(.93,7),(1.,7)):
            self.assertEqual(evaluate(selected,alpha(value))[0],float(layer),value)
    def test_triplanar_overlay_matches_instruction_replay(self):
        params={**HULL_01,'Overlay Size':512.,'Rotation Angle':25.,"Use Rotate UV's":.6}
        plan=tt.compile_tileable_metal(record(tt.TILEABLE_MASTER,params,HULL_COLORS,{**TILE_ON,'TriPlanar Overlay':True}),{'object_scale_sum':3.})
        self.assertIn(tt.PLANAR_NORMAL,plan.diagnostics)
        for tri in TRI:
            for point in POINTS[:2]:self.check(plan,replay_tileable(params,HULL_COLORS,point[:2],point[2:],3.,tri=tri),point,tri)
    def test_object_planar_trim_detail_matches_instruction_replay(self):
        plan=tt.compile_prop_trim(record(tt.TRIM_MASTER,TRIM_HULL_02,TRIM_COLORS,{**TRIM_ON,'Turn on basic tiling':False}),{'object_scale_sum':2.5,'vertex_ndotv':.35})
        self.assertIn(tt.PLANAR_NORMAL,plan.diagnostics)
        for tri in TRI:
            for point in POINTS[:2]:self.check(plan,replay_trim(TRIM_HULL_02,TRIM_COLORS,point[:2],point[2:],2.5,.35,tri=tri),point,tri)
    def test_object_planar_selects_ue_local_axes(self):
        u,v=tt.object_planar(1.,.5)
        for normal,expected in (((0.,0.,1.),(100.,-200.)),((1.,0.,0.),(-200.,-300.)),((0.,1.,0.),(100.,-300.)),((.6,0.,.6),(100.,-200.))):
            got=tuple(evaluate(x,{'object_position':(1.,2.,3.),'object_normal':normal}) for x in (u,v))
            self.assertEqual(got,expected,normal)
    def test_unverified_permutations_refuse(self):
        cases=((tt.compile_tileable_metal,tt.TILEABLE_MASTER,{**TILE_ON,'Use Dust':True},'Use Dust'),
               (tt.compile_tileable_metal,tt.TILEABLE_MASTER,{**TILE_ON,'Use Prop AO Dirt':True},'Use Prop AO Dirt'),
               (tt.compile_tileable_metal,tt.TILEABLE_MASTER,{**TILE_ON,'Turn on basic tiling':False},'Turn on basic tiling'),
               (tt.compile_prop_trim,tt.TRIM_MASTER,{**TRIM_ON,'Use Frost':True},'Use Frost'),
               (tt.compile_prop_trim,tt.TRIM_MASTER,{**TRIM_ON,'Invert Blend Mask':True},'blend texture'),
               (tt.compile_prop_trim,tt.TRIM_MASTER,{**TRIM_ON,'Use Tint':False},'Use Tint'),
               (tt.compile_prop_trim,tt.TRIM_MASTER,{**TRIM_ON,'TriPlanar Overlay':True},'TriPlanar'))
        for compile_fn,master,switches,reason in cases:
            plan=compile_fn(record(master,HULL_01,HULL_COLORS,switches),{'object_scale_sum':3.})
            with self.subTest(reason=reason):self.assertFalse(plan.outputs);self.assertIn(reason,plan.diagnostics[0])
        self.assertFalse(tt.compile_tileable_metal(record(tt.TILEABLE_MASTER,HULL_01,HULL_COLORS,TILE_ON),{'object_scale_sum':0.}).outputs)
    def test_detail_reads_owner_axis_lengths_per_object(self):
        params={**HULL_01,'2:1 Ratio':1.}
        plan=tt.compile_tileable_metal(record(tt.TILEABLE_MASTER,params,HULL_COLORS,TILE_ON))
        for axes in ((1.,1.,1.),(1.5,.75,2.25)):
            for point in POINTS:
                expected=replay_tileable(params,HULL_COLORS,point[:2],point[2:],sum(axes))
                for key,value in expected.items():
                    actual=evaluate(plan.outputs[key],{**inputs(point),'object_axis_lengths':axes})
                    for left,right in zip(actual if isinstance(actual,tuple) else (actual,),value if isinstance(value,tuple) else (value,)):
                        with self.subTest(axes=axes,output=key,point=point):self.assertAlmostEqual(left,right,delta=1e-9)
    def test_fixed_textures_and_registration(self):
        plan=tt.compile_prop_trim(record(tt.TRIM_MASTER,{**TRIM_HULL_02,'ConcaveNormal':1.},TRIM_COLORS,TRIM_ON),{'object_scale_sum':3.,'vertex_ndotv':.5})
        refs=set()
        def visit(node):
            if hasattr(node,'op'):
                if node.op=='texture':refs.add((node.attrs['reference'],node.attrs.get('slice')))
                for child in node.args:visit(child)
                visit(node.attrs.get('uv'))
        for node in plan.outputs.values():visit(node)
        self.assertIn((tt.SLICE_MASK,None),refs);self.assertEqual({s for r,s in refs if r==tt.DETAIL_ARRAY},set(range(8)))
        self.assertNotIn('/Game/Pioneer/Environment/Props/_Generic/GasStationPump_01/T_GasstationPump_01_AO',{r for r,_ in refs})
        nf=namespace()
        for master,subtype in ((tt.TILEABLE_MASTER,'tileable_metal'),(tt.TRIM_MASTER,'prop_trim')):
            self.assertEqual(nf.MASTERS[master],'prop');self.assertEqual(nf.PROP_SUBTYPES[master],subtype);self.assertIn(subtype,nf.PROP_SETUPS)

if __name__=='__main__':unittest.main(verbosity=2)
