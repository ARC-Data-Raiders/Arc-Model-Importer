"""Original native-informed compatibility composition for legacy garment ports.

This is an explicit approximate compatibility route, NOT exact retained closure
parity. The primary native GT route remains separately proved. Only the retained
PUBLIC interface is reused; no private NodeTree, shader source or layout copied.
"""
from pathlib import Path
import hashlib,json
from .native.core import expr,literal
from .dr_layered_composer import StageEmitter,e,binding,clamp,decode_circuit,rnm_circuit
from . import dr_primary_operations as ops
from . import dr_layered_composer as _stage

INTERFACE=Path(__file__).with_name('data')/'legacy_arc_public_interface.json'
BAND_CONTRACT=INTERFACE.with_name('legacy_arc_band_contract.json')
BANDS=tuple(tuple(value) for value in json.loads(BAND_CONTRACT.read_text(encoding='utf-8'))['bands'])
RECIPE=hashlib.sha256(Path(__file__).read_bytes()+INTERFACE.read_bytes()+BAND_CONTRACT.read_bytes()+Path(_stage.__file__).read_bytes()).hexdigest()[:16]
LIMITS=(
 'Original native-equation compatibility approximation; not exact legacy closure or native-game appearance parity.',
 'OCM uses calibrated legacy ID bands and known no-region gaps (root072); boundaries beyond sampled precision are not native-game claims.',
 'Legacy Base Color port has ambiguous packed-mask meaning and is reserved; use primary native GT for its authored packed semantics.',
 'Legacy crease/edge color preview uses curvature halves and nonblack activation; authored black overlays lack a separate enable port.',
 'Procedural dirt/variation use original preview noise; retained/private game noise parameters are unavailable.',
 'DN ports carry no authored UV rotation vector; normal placement rotation is approximate.',
 'Legacy layer9 is supported as a compatibility extension; native layered evidence establishes layers1..8.',
)

def instantiate(item,replacements,prefix):
    memo={}
    def visit(value):
        if not hasattr(value,'op'):return value
        old=memo.get(id(value))
        if old is not None:return old
        if value.op=='attribute' and value.attrs['name'] in replacements:return replacements[value.attrs['name']]
        attrs=dict(value.attrs)
        if 'name' in attrs:attrs['name']=prefix+'.'+attrs['name']
        result=expr(value.op,*(visit(arg) for arg in value.args),**attrs);memo[id(value)]=result;return result
    return visit(item)

def balanced(terms,prefix):
    if not terms:return literal(0.)
    level=0
    while len(terms)>1:
        terms=[e('add',terms[i],terms[i+1],name=prefix+'.'+str(level)+'.'+str(i)) if i+1<len(terms) else terms[i] for i in range(0,len(terms),2)];level+=1
    return terms[0]

def circuits():
    main=binding('Main Texture');r=e('channel',main,index=0,name='Source.OCM.Occlusion');g=e('channel',main,index=1,name='Source.OCM.Curvature');b=e('channel',main,index=2,name='Source.OCM.Layer')
    masks={zone:e('multiply',e('greater_than',b,literal(bounds[0]),name='Layer'+str(zone)+'.AboveLower'),e('less_than',b,literal(bounds[1]),name='Layer'+str(zone)+'.BelowUpper'),name='Layer.Match'+str(zone)) for zone,bounds in enumerate(BANDS,1)}
    active=balanced(list(masks.values()),'Layer.Active');absent=e('subtract',literal(1.),active,name='Layer.DefaultCoverage')
    def select(values,default,prefix):return balanced([e('multiply',literal(default),absent,name=prefix+'.Default')]+[e('multiply',values[z],masks[z],name=prefix+'.Layer'+str(z)) for z in range(1,10)],prefix+'.Sum')
    colors={z:e('mix',binding('Colour '+str(z)),binding('Overlay '+str(z)),clamp(binding('Overlay Fac '+str(z)),'Layer'+str(z)+'.OverlayWeight'),name='Layer'+str(z)+'.Overlay') for z in range(1,10)}
    albedo=select(colors,(1.,1.,1.),'Surface.Palette')
    crease=select({z:binding('Crease '+str(z)) for z in range(1,10)},(0.,0.,0.),'Wear.CreaseColor');edge=select({z:binding('Edge '+str(z)) for z in range(1,10)},(0.,0.,0.),'Wear.EdgeColor')
    def nonblack(color,prefix):return e('greater_than',balanced([e('channel',color,index=i,name=prefix+'.'+str(i)) for i in range(3)],prefix+'.Sum'),literal(1e-8),name=prefix+'.Active')
    crease_weight=e('multiply',clamp(e('subtract',literal(1.),e('multiply',g,literal(2.),name='Wear.CurvatureDouble'),name='Wear.CreaseHalf'),'Wear.CreaseClamp'),nonblack(crease,'Wear.Crease'),name='Wear.CreaseWeight')
    edge_weight=e('multiply',clamp(e('subtract',e('multiply',g,literal(2.),name='Wear.EdgeDouble'),literal(1.),name='Wear.EdgeHalf'),'Wear.EdgeClamp'),nonblack(edge,'Wear.Edge'),name='Wear.EdgeWeight')
    albedo=e('mix',e('mix',albedo,crease,crease_weight,name='Wear.ApplyCrease'),edge,edge_weight,name='Wear.ApplyEdge')
    roughness=select({z:binding('Roughness '+str(z)) for z in range(1,10)},literal(.5).args[0],'Surface.Roughness');metallic=select({z:binding('Metallic '+str(z)) for z in range(1,10)},0.,'Surface.Metallic')
    base=instantiate(decode_circuit(),{'Color':binding('Base Normal')},'Normal.Base')
    flat=literal((0.,0.,1.));details={}
    for z in range(1,10):
        decoded=instantiate(decode_circuit(),{'Color':binding('Mix '+str(z))},'Normal.Layer'+str(z))
        details[z]=e('mix',flat,decoded,clamp(binding('Enable '+str(z)),'Normal.Layer'+str(z)+'.Enable'),name='Normal.Layer'+str(z)+'.Enabled')
    detail=select(details,(0.,0.,1.),'Normal.Detail');normal=instantiate(rnm_circuit(),{'Base':base,'Detail':detail},'Normal.MeshDetail')
    # Decal order, public placement alpha and independent DN enables stay active.
    for slot in range(1,10):
        tag='Decal.Slot'+str(slot);color=binding('Decal '+str(slot));mean=e('divide',balanced([e('channel',color,index=i,name=tag+'.RGB'+str(i)) for i in range(3)],tag+'.RGBSum'),literal(3.),name=tag+'.Mean')
        dark=e('less_than',mean,literal(.075),name=tag+'.TintThreshold');tint=e('mix',binding('Decal '+str(slot)+' ColorA'),binding('Decal '+str(slot)+' ColorB'),dark,name=tag+'.TintAB')
        color=e('mix',color,tint,clamp(binding('Decal '+str(slot)+' ColorOverride'),tag+'.Override'),name=tag+'.AuthoredColor')
        alpha=e('multiply',e('multiply',clamp(binding('Decal Enable '+str(slot)),tag+'.Enable'),clamp(binding('Decal Alpha '+str(slot)),tag+'.Coverage'),name=tag+'.EnabledCoverage'),active,name=tag+'.Alpha')
        albedo=e('mix',albedo,color,alpha,name=tag+'.Albedo')
        dn_alpha=e('multiply',alpha,clamp(binding('DN Enable '+str(slot)),tag+'.DataEnable'),name=tag+'.DataCoverage');data=binding('DN '+str(slot))
        roughness=e('mix',roughness,e('channel',data,index=2,name=tag+'.DataRoughness'),dn_alpha,name=tag+'.Roughness')
        metallic=e('mix',metallic,binding('DN Alpha '+str(slot)),dn_alpha,name=tag+'.Metallic')
        dn=instantiate(decode_circuit(),{'Color':data},tag+'.NormalDecode');weighted=e('mix',flat,dn,dn_alpha,name=tag+'.NormalCoverage');normal=instantiate(rnm_circuit(),{'Base':normal,'Detail':weighted},tag+'.NormalBlend')
    noise=binding('PreviewNoise');dirt=e('multiply',clamp(binding('Dirt Toggle'),'Preview.Dirt.Toggle'),e('multiply',noise,literal(.3),name='Preview.Dirt.Amount'),name='Preview.Dirt.Coverage')
    albedo=e('mix',albedo,literal((.4,.32,.18)),dirt,name='Preview.Dirt.Color')
    variation=e('add',literal(.85),e('multiply',noise,literal(.3),name='Preview.Variation.Range'),name='Preview.Variation.Gain')
    varied=e('multiply',albedo,variation,name='Preview.Variation.Color');albedo=e('mix',albedo,varied,clamp(binding('Variation'),'Preview.Variation.Toggle'),name='Preview.Variation.Apply')
    albedo=e('multiply',albedo,r,name='Surface.Occlusion')
    n=[e('channel',normal,index=i,name='Normal.Final'+str(i)) for i in range(3)]
    encoded=e('combine',e('add',literal(.5),e('multiply',n[0],literal(.5),name='Normal.EncodeXScale'),name='Normal.EncodeX'),e('subtract',literal(.5),e('multiply',n[1],literal(.5),name='Normal.EncodeYScale'),name='Normal.EncodeY'),e('add',literal(.5),e('multiply',n[2],literal(.5),name='Normal.EncodeZScale'),name='Normal.EncodeZ'),name='Normal.BlenderEncoded',stage='Details')
    return {'base_color':albedo,'roughness':clamp(roughness,'Surface.FinalRoughness'),'metallic':clamp(metallic,'Surface.FinalMetallic'),'normal':encoded,'alpha':clamp(binding('Main Texture_Alpha'),'Coverage.MainAlpha'),'assigned_region':active}

class LegacyEmitter(StageEmitter):
    def emit(self,item):
        if item.op=='mix':
            prior=self.memo.get(id(item))
            if prior is not None and prior[0] is item:return prior[1]
            a,b=[self.emit(arg) for arg in item.args[:2]]
            vector=any(getattr(value,'type',None) in ('RGBA','VECTOR') or isinstance(value,(tuple,list)) for value in (a,b))
            if not vector:
                factor=self.emit(item.args[2]);node=self.ops.new_operation(self.tree.nodes,'ShaderNodeMix',stage=item.attrs.get('stage','DR.Surface').removeprefix('DR.'),role=item.attrs['name']);node.data_type='FLOAT';node.clamp_factor=True
                for value,target in [(factor,node.inputs[0]),(a,node.inputs[2]),(b,node.inputs[3])]:self.wire(value,target)
                result=node.outputs[0];self.memo[id(item)]=(item,result);return result
        if item.op not in ('floor','compare','greater_than'):return super().emit(item)
        prior=self.memo.get(id(item))
        if prior is not None and prior[0] is item:return prior[1]
        values=[self.emit(arg) for arg in item.args];node=self.ops.new_operation(self.tree.nodes,'ShaderNodeMath',stage=item.attrs.get('stage','DR.Surface').removeprefix('DR.'),role=item.attrs['name']);node.operation={'floor':'FLOOR','compare':'COMPARE','greater_than':'GREATER_THAN'}[item.op];node.use_clamp=False
        for value,target in zip(values,node.inputs):self.wire(value,target)
        if item.op=='compare':node.inputs[2].default_value=item.attrs.get('epsilon',1e-5)
        result=node.outputs[0];self.memo[id(item)]=(item,result);return result

@ops.operation_build
def build_group(bpy):
    name='DR.LegacyGarment.Compatibility.'+RECIPE;tree=bpy.data.node_groups.get(name)
    if tree is not None:
        if tree.get('dr.legacy_arc.recipe')!=RECIPE:raise ValueError('Legacy compatibility name collision')
        return tree
    contract=json.loads(INTERFACE.read_text(encoding='utf-8'));tree=bpy.data.node_groups.new(name,'ShaderNodeTree')
    try:
        identifiers={}
        for row in contract['sockets']:
            kind=row['socket_type']
            try:socket=tree.interface.new_socket(name=row['name'],in_out=row['direction'],socket_type=kind)
            except (TypeError,RuntimeError,ValueError):
                base='NodeSocketFloat' if kind.startswith('NodeSocketFloat') else 'NodeSocketVector' if kind.startswith('NodeSocketVector') else kind;socket=tree.interface.new_socket(name=row['name'],in_out=row['direction'],socket_type=base)
            if 'default_value' in row:socket.default_value=row['default_value']
            if kind in ('NodeSocketFloatFactor','NodeSocketFloatPercentage'):
                socket.min_value=0.;socket.max_value=1.
            identifiers[row['direction']+':'+row['name']]=row['identifier']
        gi=ops.new_operation(tree.nodes,'NodeGroupInput',stage='Source',role='LegacyGarment.Inputs');go=ops.new_operation(tree.nodes,'NodeGroupOutput',stage='Output',role='LegacyGarment.Outputs')
        source=ops.new_operation(tree.nodes,'ShaderNodeTexCoord',stage='Coordinates',role='Preview.ObjectCoordinates');noise=ops.new_operation(tree.nodes,'ShaderNodeTexNoise',stage='Details',role='Preview.DirtVariationNoise');noise.noise_dimensions='3D';noise.inputs['Scale'].default_value=4.;noise.inputs['Detail'].default_value=2.;tree.links.new(source.outputs['Object'],noise.inputs['Vector'])
        bindings={socket.name:socket for socket in gi.outputs if socket.name!=''};bindings['PreviewNoise']=noise.outputs['Fac'];emitter=LegacyEmitter(tree,bindings,ops);outputs={key:emitter.emit(value) for key,value in circuits().items()}
        normal=ops.new_operation(tree.nodes,'ShaderNodeNormalMap',stage='Details',role='LegacyGarment.NormalTransport');tree.links.new(outputs['normal'],normal.inputs['Color'])
        bsdf=ops.new_operation(tree.nodes,'ShaderNodeBsdfPrincipled',stage='Output',role='LegacyGarment.Surface');bsdf.inputs['IOR'].default_value=1.5;bsdf.inputs['Specular IOR Level'].default_value=.5
        for key,port in [('base_color','Base Color'),('roughness','Roughness'),('metallic','Metallic')]:tree.links.new(outputs[key],bsdf.inputs[port])
        tree.links.new(normal.outputs['Normal'],bsdf.inputs['Normal'])
        diffuse=ops.new_operation(tree.nodes,'ShaderNodeBsdfDiffuse',stage='Output',role='LegacyGarment.UnassignedDiffuse');diffuse.inputs['Roughness'].default_value=0.;tree.links.new(outputs['base_color'],diffuse.inputs['Color']);tree.links.new(normal.outputs['Normal'],diffuse.inputs['Normal'])
        region=ops.new_operation(tree.nodes,'ShaderNodeMixShader',stage='Coverage',role='LegacyGarment.AssignedRegion');tree.links.new(outputs['assigned_region'],region.inputs[0]);tree.links.new(diffuse.outputs[0],region.inputs[1]);tree.links.new(bsdf.outputs['BSDF'],region.inputs[2])
        transparent=ops.new_operation(tree.nodes,'ShaderNodeBsdfTransparent',stage='Coverage',role='LegacyGarment.Transparent');mix=ops.new_operation(tree.nodes,'ShaderNodeMixShader',stage='Coverage',role='LegacyGarment.AlphaCoverage');tree.links.new(outputs['alpha'],mix.inputs[0]);tree.links.new(transparent.outputs[0],mix.inputs[1]);tree.links.new(region.outputs[0],mix.inputs[2]);tree.links.new(mix.outputs[0],go.inputs['Character']);go.inputs['Displacement'].default_value=(0.,0.,0.)
        tree['dr.legacy_group_key']='ArcTexturer';tree['dr.legacy_arc.recipe']=RECIPE;tree['dr.original.implementation']='native-equation legacy compatibility';tree['dr.legacy_arc.parity']='calibrated legacy band compatibility, approximate private closure/noise/packed ports; primary GT native route independently verified';tree['dr.legacy_arc.limits']=json.dumps(LIMITS);tree['dr.interface.legacy_identifiers']=json.dumps(identifiers)
        note=ops.new_operation(tree.nodes,'NodeFrame',stage='Source',role='LegacyGarment.CompatibilityLimits');note.label='Legacy compatibility approximation: native GT is the verified route';note['dr.annotation']=1
        ops.organize(tree.nodes,tree.links);return tree
    except BaseException:
        bpy.data.node_groups.remove(tree);raise
