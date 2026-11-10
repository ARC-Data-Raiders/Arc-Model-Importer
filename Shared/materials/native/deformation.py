"""Normal-aware independently implemented delta deformation, separate from scene mutation.
Decoded deltas require source-backed units/basis metadata supplied by the caller.
"""
import math
import struct

_MORPH_NORMAL_CACHE_LIMIT=65536

def _vectors(values,name):
    out=tuple(tuple(float(v) for v in row) for row in values)
    if any(len(row)!=3 or not all(math.isfinite(v) for v in row) for row in out):
        raise ValueError(name+' must contain finite 3-vectors')
    return out

def deform_vertices(positions,normals,position_deltas,normal_deltas,weight,*,position_scale=1.,evidence=None):
    """Blend position and normal deltas in one declared coordinate basis.
    Normals are renormalized; singular results fail before any Blender writes.
    """
    if not evidence:raise ValueError('native delta decode evidence is required')
    if not math.isfinite(weight) or not math.isfinite(position_scale):raise ValueError('finite deformation parameters required')
    p,n,dp,dn=(_vectors(v,k) for v,k in ((positions,'positions'),(normals,'normals'),(position_deltas,'position deltas'),(normal_deltas,'normal deltas')))
    if not len(p)==len(n)==len(dp)==len(dn):raise ValueError('deformation vertex counts differ')
    output_p=[];output_n=[]
    for point,base,delta,ndelta in zip(p,n,dp,dn):
        output_p.append(tuple(a+weight*position_scale*b for a,b in zip(point,delta)))
        candidate=tuple(a+weight*b for a,b in zip(base,ndelta));length=math.hypot(*candidate)
        if length<1e-12:raise ValueError('deformation produced a singular normal')
        output_n.append(tuple(v/length for v in candidate))
    return tuple(output_p),tuple(output_n)

def decode_delta_texel(texel,*,minimum,extent,encoding,evidence=None):
    """Decode explicit source-derived bounds; no guessed signedness or unit conversion."""
    if not evidence:raise ValueError('native delta decode evidence is required')
    t=_vectors((texel[:3],),'texel')[0];lo=_vectors((minimum,),'minimum')[0];ex=_vectors((extent,),'extent')[0]
    if encoding=='unorm':return tuple(a+b*c for a,b,c in zip(lo,ex,t))
    if encoding=='snorm':return tuple(a+b*(2*c-1) for a,b,c in zip(lo,ex,t))
    if encoding=='float':return t
    raise ValueError('unsupported delta encoding '+str(encoding))


def transform_normal(vector,matrix):
    """Transport a source normal through an affine 3x3 linear transform.

    Uses inverse transpose, so imported nonuniform scale cannot skew delta normals.
    Translation is deliberately outside this API.
    """
    v=_vectors((vector,),"normal")[0]
    m=_vectors(matrix,"matrix")
    if len(m)!=3:raise ValueError("normal transform requires a 3x3 matrix")
    a,b,c=m[0];d,e,f=m[1];g,h,i=m[2]
    cof=((e*i-f*h,f*g-d*i,d*h-e*g),(c*h-b*i,a*i-c*g,b*g-a*h),(b*f-c*e,c*d-a*f,a*e-b*d))
    determinant=a*cof[0][0]+b*cof[0][1]+c*cof[0][2]
    if abs(determinant)<1e-12:raise ValueError("singular normal transform")
    transformed=tuple(sum(row[k]*v[k] for k in range(3))/determinant for row in cof)
    length=math.hypot(*transformed)
    if length<1e-12:raise ValueError("singular transformed normal")
    return tuple(x/length for x in transformed)


def deform_corner_normals(vertex_normals,normal_deltas,corner_vertices,weight,*,basis=None,evidence=None):
    """Build normalized split normals without changing polygons or vertex indexing.

    The explicit corner map expands the normal arrays without seam averaging.
    Hard-edge callers provide duplicated base-normal entries and matching deltas.
    """
    if not evidence:raise ValueError("native delta decode evidence is required")
    base=_vectors(vertex_normals,"base normals");delta=_vectors(normal_deltas,"normal deltas")
    indices=tuple(corner_vertices)
    if any(not isinstance(index,int) or index<0 or index>=len(base) for index in indices):raise ValueError("invalid corner vertex index")
    if len(base)!=len(delta):raise ValueError("normal delta counts differ")
    # Position zero vectors allow reuse of the validated normal interpolation.
    zeros=((0.,0.,0.),)*len(base)
    normals=deform_vertices(zeros,base,zeros,delta,weight,evidence=evidence)[1]
    if basis is not None:normals=tuple(transform_normal(n,basis) for n in normals)
    return tuple(normals[index] for index in indices)


# The following wire layout was supplied as native UEFormat interoperability
# facts by the coordinator. No previous loader or shader implementation is used.
from dataclasses import dataclass
import struct

@dataclass(frozen=True)
class MorphDelta:
    position_delta: tuple
    normal_delta: tuple
    source_vertex_index: int

@dataclass(frozen=True)
class MorphTarget:
    name: str
    deltas: tuple

@dataclass(frozen=True)
class MorphResult:
    positions: tuple
    normals: tuple
    corner_normals: tuple = ()


def decode_morph_targets(payload,source_vertex_count):
    """Decode a complete little-endian UEFormat MORPHTARGETS payload.

    Native UEFormat v10 string byte length excludes NUL (verified on actual Yeti). Every delta is
    position float3, dimensionless normal float3, and source-vertex uint32.
    Counts, lengths, float finiteness, source bounds and trailing bytes are checked
    before decoded targets are returned. Nothing mutates a Blender mesh.
    """
    if not isinstance(source_vertex_count,int) or source_vertex_count<0:
        raise ValueError('source vertex count must be a nonnegative integer')
    view=memoryview(payload).cast('B');offset=0
    def take(count):
        nonlocal offset
        if count<0 or offset+count>len(view):raise ValueError('truncated morph payload at byte '+str(offset))
        value=view[offset:offset+count];offset+=count;return value
    def int32():return struct.unpack('<i',take(4))[0]
    count=int32()
    if count<0 or count>(len(view)-offset)//9:raise ValueError('invalid morph target count')
    targets=[]
    for _ in range(count):
        length=int32()
        if length<=0:raise ValueError('morph target requires positive UTF-8 byte length')
        encoded=bytes(take(length))
        if b'\0' in encoded:raise ValueError('native morph target name contains an unexpected NUL')
        try:name=encoded.decode('utf-8')
        except UnicodeDecodeError as error:raise ValueError('invalid UTF-8 morph target name') from error
        delta_count=int32()
        if delta_count<0 or delta_count>(len(view)-offset)//28:raise ValueError('invalid morph delta count')
        deltas=[]
        for _ in range(delta_count):
            values=struct.unpack('<6fI',take(28));position=tuple(values[:3]);normal=tuple(values[3:6]);source=values[6]
            if not all(math.isfinite(v) for v in position+normal):raise ValueError('nonfinite morph delta')
            if source>=source_vertex_count:raise ValueError('morph source vertex index out of bounds: '+str(source))
            deltas.append(MorphDelta(position,normal,source))
        targets.append(MorphTarget(name,tuple(deltas)))
    if offset!=len(view):raise ValueError('unexpected trailing bytes in morph payload')
    return tuple(targets)


def _inverse_transpose(matrix):
    m=_vectors(matrix,'basis')
    if len(m)!=3:raise ValueError('basis must be a 3x3 matrix')
    a,b,c=m[0];d,e,f=m[1];g,h,i=m[2]
    cof=((e*i-f*h,f*g-d*i,d*h-e*g),(c*h-b*i,a*i-c*g,b*g-a*h),(b*f-c*e,c*d-a*f,a*e-b*d))
    determinant=a*cof[0][0]+b*cof[0][1]+c*cof[0][2]
    if abs(determinant)<1e-12:raise ValueError('singular morph basis')
    return tuple(tuple(v/determinant for v in row) for row in cof)

def _matvec(matrix,vector):return tuple(sum(row[i]*vector[i] for i in range(3)) for row in matrix)

def deform_morph_targets(positions,normals,render_source_indices,targets,weights,*,source_vertex_count,position_scale=1.,basis=None,corner_normals=None,corner_vertex_indices=None):
    """Blend decoded targets with source-preserving render/corner mappings.

    Base geometry is already in destination space. Only position deltas receive
    the scene unit scale. Normal deltas use inverse-transpose basis without unit
    scale, stay unnormalized while summing targets, and normalize with base normals
    once at the end. Repeated source indices expand to every matching render vertex.
    Per-corner base normals preserve hard edges instead of averaging seam vertices.
    """
    p=_vectors(positions,'positions');n=_vectors(normals,'normals');indices=tuple(render_source_indices)
    if not isinstance(source_vertex_count,int) or source_vertex_count<0:raise ValueError('invalid source vertex count')
    if not len(p)==len(n)==len(indices):raise ValueError('render vertex array lengths differ')
    if any(not isinstance(i,int) or i<0 or i>=source_vertex_count for i in indices):raise ValueError('render source vertex index out of bounds')
    if not math.isfinite(position_scale):raise ValueError('position scale must be finite')
    basis=_vectors(basis if basis is not None else ((1,0,0),(0,1,0),(0,0,1)),'basis')
    if len(basis)!=3:raise ValueError('basis must be a 3x3 matrix')
    normal_basis=_inverse_transpose(basis)
    accumulated={}
    target_names=set()
    for target in targets:
        if target.name in target_names:raise ValueError('ambiguous duplicate morph target name: '+target.name)
        target_names.add(target.name)
        weight=float(weights.get(target.name,0.))
        if not math.isfinite(weight):raise ValueError('morph target weights must be finite')
        for delta in target.deltas:
            source=delta.source_vertex_index
            if not isinstance(source,int) or source<0 or source>=source_vertex_count:raise ValueError('morph source vertex index out of bounds')
            dp=_vectors((delta.position_delta,),'position delta')[0];dn=_vectors((delta.normal_delta,),'normal delta')[0]
            if weight==0.:continue
            dp=_matvec(basis,tuple(v*position_scale*weight for v in dp))
            dn=_matvec(normal_basis,tuple(v*weight for v in dn))
            if not all(math.isfinite(v) for v in dp+dn):raise ValueError('morph delta transform overflow')
            oldp,oldn=accumulated.get(source,((0.,0.,0.),(0.,0.,0.)))
            accumulated[source]=(tuple(a+b for a,b in zip(oldp,dp)),tuple(a+b for a,b in zip(oldn,dn)))
    unknown=set(weights)-target_names
    if unknown:raise ValueError('unknown morph target weights: '+', '.join(sorted(unknown)))
    normal_cache={};pack_normal=struct.Struct('<3d').pack
    def moved_normal(base,source):
        key=(source,pack_normal(*base)) if corner_normals is not None else None
        if key is not None:
            cached=normal_cache.get(key)
            if cached is not None:return cached
        dn=accumulated.get(source,((0.,0.,0.),(0.,0.,0.)))[1]
        out=tuple(a+b for a,b in zip(base,dn))
        if not all(math.isfinite(v) for v in out):raise ValueError('morph normal overflow')
        length=math.hypot(*out)
        if length<1e-12:raise ValueError('morph produced singular normal')
        result=tuple(v/length for v in out)
        if key is not None and len(normal_cache)<_MORPH_NORMAL_CACHE_LIMIT:normal_cache[key]=result
        return result
    outp=tuple(tuple(a+b for a,b in zip(point,accumulated.get(source,((0.,0.,0.),(0.,0.,0.)))[0])) for point,source in zip(p,indices))
    if any(not all(math.isfinite(v) for v in point) for point in outp):raise ValueError('morph position overflow')
    outn=tuple(moved_normal(base,source) for base,source in zip(n,indices))
    outcorners=()
    if (corner_normals is None)!=(corner_vertex_indices is None):raise ValueError('corner normals and vertex indices are required together')
    if corner_normals is not None:
        cn=_vectors(corner_normals,'corner normals');ci=tuple(corner_vertex_indices)
        if len(cn)!=len(ci):raise ValueError('corner array lengths differ')
        if any(not isinstance(i,int) or i<0 or i>=len(p) for i in ci):raise ValueError('corner vertex index out of bounds')
        outcorners=tuple(moved_normal(base,indices[index]) for base,index in zip(cn,ci))
    return MorphResult(outp,outn,outcorners)


# Native base values are deliberately separate from the posed Blender normals.
NORMAL_SOURCE_VERTEX='DR.NativeSourceVertex'
NORMAL_BASE_VERTEX='DR.NativeBaseVertexNormal'
NORMAL_BASE_CORNER='DR.NativeBaseCornerNormal'
NORMAL_SOURCE_COUNT='DR.NativeSourceVertexCount'
NORMAL_TOPOLOGY='DR.NativeNormalTopology'
NORMAL_SOURCE_VERSION='DR.NativeNormalSourceVersion'
_FLOAT_NORMAL_CAPABILITIES={}

class NormalTransportUnavailable(RuntimeError):
    """The host cannot preserve requested float corner normals losslessly."""


def normal_direction_error(expected,actual):
    a=_vectors(expected,'expected normals');b=_vectors(actual,'actual normals')
    if len(a)!=len(b):raise ValueError('corner normal counts differ')
    worst=0.
    for x,y in zip(a,b):
        lx=math.hypot(*x);ly=math.hypot(*y)
        if min(lx,ly)<1e-12:raise ValueError('normal comparison contains a zero vector')
        cosine=max(-1.,min(1.,sum(i*j for i,j in zip(x,y))/(lx*ly)))
        worst=max(worst,math.degrees(math.acos(cosine)))
    return worst


def _host_module(bpy_module):
    import threading
    if threading.current_thread() is not threading.main_thread():raise RuntimeError('native normal transport requires Blender main thread')
    if bpy_module is not None:return bpy_module
    try:import bpy
    except ImportError as error:raise NormalTransportUnavailable('Blender is required for float normal transport') from error
    return bpy


def _float_corner_attribute(mesh,values):
    attribute=mesh.attributes.new('custom_normal','FLOAT_VECTOR','CORNER')
    try:
        if attribute.name!='custom_normal' or attribute.data_type!='FLOAT_VECTOR' or attribute.domain!='CORNER':raise NormalTransportUnavailable('Blender did not create the native float custom_normal attribute')
        attribute.data.foreach_set('vector',[x for row in values for x in row]);mesh.update()
        return attribute
    except Exception:
        mesh.attributes.remove(attribute);mesh.update();raise


def _require_float_normal_capability(bpy_module):
    host=_host_module(bpy_module);version=tuple(host.app.version)
    if version<(5,1,0):raise NormalTransportUnavailable('direct FLOAT_VECTOR corner normal transport requires Blender5.1+; no legacy SHORT2 fallback')
    key=(version,str(getattr(host.app,'build_hash','')))
    if key in _FLOAT_NORMAL_CAPABILITIES:return host
    probe=host.data.meshes.new('DR.NativeNormalCapabilityProbe')
    try:
        probe.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)]);probe.update()
        expected=((0.,0.,1.),(0.,.6,.8),(.6,0.,.8))
        _float_corner_attribute(probe,expected)
        actual=tuple(tuple(n.vector) for n in probe.corner_normals)
        if normal_direction_error(expected,actual)>.05:raise NormalTransportUnavailable('host float custom_normal capability probe failed strict directional check')
        _FLOAT_NORMAL_CAPABILITIES[key]=True
    except NormalTransportUnavailable:raise
    except Exception as error:raise NormalTransportUnavailable('host FLOAT_VECTOR corner normal capability is unavailable: '+str(error)) from error
    finally:host.data.meshes.remove(probe)
    return host


CORNER_NORMAL_TOLERANCE_DEGREES=1.
# Faces smaller than this fraction of the mesh's mean face area have no stable
# smooth frame (measured: Cosmonaut straps, 118 bad corners, all area <= 8.5e-12
# against a 3e-05 median); their corners are excluded from the readback check.
DEGENERATE_FACE_AREA_RATIO=1e-6


def _corner_errors(expected,actual):
    out=[]
    for x,y in zip(expected,actual):
        lx=math.hypot(*x);ly=math.hypot(*y)
        if min(lx,ly)<1e-12:out.append(180.);continue
        out.append(math.degrees(math.acos(max(-1.,min(1.,sum(i*j for i,j in zip(x,y))/(lx*ly))))))
    return out


def write_corner_normals(mesh,normals,*,bpy_module=None):
    """Write display corner normals in Blender's fan-space custom normal storage.

    Fan-space normals (mesh.normals_split_custom_set, an INT16_2D custom_normal
    layer) are stored relative to each corner's smooth frame, so the Armature
    modifier rotates them with the surface. Free FLOAT_VECTOR custom_normal
    layers stay in rest space under deformation and shade posed meshes faceted.
    Pristine float normals for morph recompute live in the DR.NativeBase*
    attributes (preserve_normal_source), not in this display layer.

    An existing free float layer is replaced. Corners of zero-area faces have no
    smooth frame and are excluded from the readback check. Previous normals are
    restored if the write or readback fails. Geometry, polygon material indices,
    smooth flags, UVs and unrelated attributes are untouched.
    """
    # Corners of near-zero-area faces (below DEGENERATE_FACE_AREA_RATIO of the mean) are not checked.
    values=_vectors(normals,'corner normals')
    if len(values)!=len(mesh.loops):raise ValueError('normal count differs from mesh corners')
    if any(math.hypot(*v)<1e-12 for v in values):raise ValueError('native corner normal cannot be zero')
    values=tuple(tuple(x/math.hypot(*v) for x in v) for v in values)
    _require_float_normal_capability(bpy_module)
    old=mesh.attributes.get('custom_normal')
    previous=tuple(tuple(v.vector) for v in mesh.corner_normals) if old else None
    try:
        if old is not None and old.data_type=='FLOAT_VECTOR':mesh.attributes.remove(old)
        mesh.normals_split_custom_set(values);mesh.update()
        stored=mesh.attributes.get('custom_normal')
        if stored is None or stored.data_type=='FLOAT_VECTOR':raise NormalTransportUnavailable('Blender did not store fan-space custom normals')
        errors=_corner_errors(values,tuple(tuple(v.vector) for v in mesh.corner_normals))
        areas=[getattr(face,'area',1.) for face in mesh.polygons]
        floor=DEGENERATE_FACE_AREA_RATIO*sum(areas)/max(len(areas),1)
        checked=[e for face,area in zip(mesh.polygons,areas) if area>floor for e in errors[face.loop_start:face.loop_start+face.loop_total]]
        error=max(checked,default=0.)
        if error>CORNER_NORMAL_TOLERANCE_DEGREES:raise NormalTransportUnavailable('fan-space corner normal readback exceeds %.1f degree: %s'%(CORNER_NORMAL_TOLERANCE_DEGREES,error))
    except Exception:
        current=mesh.attributes.get('custom_normal')
        if current is not None:mesh.attributes.remove(current)
        if previous is not None:mesh.normals_split_custom_set(previous)
        mesh.update();raise
    return {'storage':'custom_normal:CORNER:'+stored.data_type,'corner_count':len(values),'max_normal_angle_degrees':error,'unchecked_degenerate_corners':len(errors)-len(checked),'legacy_fallback':False}


def _normal_topology(mesh,indices):
    import hashlib,json
    corners=tuple(int(loop.vertex_index) for loop in mesh.loops)
    faces=tuple((int(face.loop_start),int(face.loop_total)) for face in mesh.polygons)
    return hashlib.sha256(json.dumps((len(mesh.vertices),corners,faces,tuple(indices)),separators=(',',':')).encode()).hexdigest()


def preserve_normal_source(mesh,source_normals,render_source_indices,*,source_vertex_count=None,corner_normals=None,bpy_module=None):
    """Preserve pristine destination-basis source normals and original indexing.

    source_normals is indexed by native source vertex. Split render vertices may
    reference the same source index. Explicit corner_normals preserve hard edges;
    otherwise the native source normal expands through vertex/corner maps.
    Never recapture this source from a posed or compressed Blender normal layer.
    """
    source=_vectors(source_normals,'pristine native source normals');count=len(source) if source_vertex_count is None else source_vertex_count
    indices=tuple(render_source_indices)
    if not isinstance(count,int) or count!=len(source):raise ValueError('native source normal count differs')
    if len(indices)!=len(mesh.vertices) or any(not isinstance(i,int) or i<0 or i>=count for i in indices):raise ValueError('invalid original render/source vertex map')
    vertices=tuple(source[i] for i in indices);corners=tuple(int(loop.vertex_index) for loop in mesh.loops)
    if any(i<0 or i>=len(vertices) for i in corners):raise ValueError('corner vertex mapping out of bounds')
    base=tuple(vertices[i] for i in corners) if corner_normals is None else _vectors(corner_normals,'pristine corner normals')
    if len(base)!=len(corners) or any(math.hypot(*v)<1e-12 for v in vertices+base):raise ValueError('invalid pristine float normal data')
    names=(NORMAL_SOURCE_VERTEX,NORMAL_BASE_VERTEX,NORMAL_BASE_CORNER)
    if any(mesh.attributes.get(name) for name in names) or mesh.get(NORMAL_SOURCE_VERSION):raise ValueError('pristine native normal source already preserved; refusing posed recapture')
    _require_float_normal_capability(bpy_module)
    created=[]
    try:
        for name,kind,domain,values in ((NORMAL_SOURCE_VERTEX,'INT','POINT',indices),(NORMAL_BASE_VERTEX,'FLOAT_VECTOR','POINT',vertices),(NORMAL_BASE_CORNER,'FLOAT_VECTOR','CORNER',base)):
            attr=mesh.attributes.new(name,kind,domain);created.append(attr)
            if attr.name!=name:raise ValueError('native normal source attribute collision')
            attr.data.foreach_set('value' if kind=='INT' else 'vector',values if kind=='INT' else [x for row in values for x in row])
        mesh[NORMAL_SOURCE_COUNT]=count;mesh[NORMAL_TOPOLOGY]=_normal_topology(mesh,indices);mesh[NORMAL_SOURCE_VERSION]=1;mesh.update()
    except Exception:
        for attr in created:mesh.attributes.remove(attr)
        for key in (NORMAL_SOURCE_COUNT,NORMAL_TOPOLOGY,NORMAL_SOURCE_VERSION):
            if key in mesh:del mesh[key]
        raise
    return {'source_vertex_count':count,'render_vertices':len(indices),'corners':len(corners),'source_attributes':names,'topology_fingerprint':mesh[NORMAL_TOPOLOGY]}


def read_normal_source(mesh):
    if mesh.get(NORMAL_SOURCE_VERSION)!=1:raise ValueError('pristine native normal source is not present')
    expected=((NORMAL_SOURCE_VERTEX,'INT','POINT'),(NORMAL_BASE_VERTEX,'FLOAT_VECTOR','POINT'),(NORMAL_BASE_CORNER,'FLOAT_VECTOR','CORNER'))
    attrs=[]
    for name,kind,domain in expected:
        attr=mesh.attributes.get(name)
        if attr is None or attr.data_type!=kind or attr.domain!=domain:raise ValueError('native normal source attribute changed: '+name)
        attrs.append(attr)
    indices=tuple(int(v.value) for v in attrs[0].data)
    if _normal_topology(mesh,indices)!=mesh.get(NORMAL_TOPOLOGY):raise ValueError('mesh topology/source mapping changed since native normal capture')
    vertices=_vectors((tuple(v.vector) for v in attrs[1].data),'preserved vertex normals')
    corners=_vectors((tuple(v.vector) for v in attrs[2].data),'preserved corner normals')
    return {'source_vertex_count':int(mesh[NORMAL_SOURCE_COUNT]),'render_source_indices':indices,'base_vertex_normals':vertices,'base_corner_normals':corners,'corner_vertex_indices':tuple(int(loop.vertex_index) for loop in mesh.loops)}


def apply_normal_deltas(mesh,targets,weights,*,basis=None,bpy_module=None):
    """Recompute each pose from pristine native float normals, never previous pose.

    Only dimensionless normal deltas are applied. Shape-key/position changes stay
    with the caller. source IDs and corner identity survive duplicated seam verts.
    """
    state=read_normal_source(mesh);zeros=((0.,0.,0.),)*len(state['base_vertex_normals'])
    result=deform_morph_targets(zeros,state['base_vertex_normals'],state['render_source_indices'],targets,weights,source_vertex_count=state['source_vertex_count'],position_scale=0.,basis=basis,corner_normals=state['base_corner_normals'],corner_vertex_indices=state['corner_vertex_indices'])
    report=write_corner_normals(mesh,result.corner_normals,bpy_module=bpy_module)
    return {**report,'source_vertex_count':state['source_vertex_count'],'weights':dict(weights),'uses_pristine_base':True,'position_deltas_applied':False}
