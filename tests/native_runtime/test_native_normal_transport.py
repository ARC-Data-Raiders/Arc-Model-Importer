try:
    from .native_test_support import ROOT
except ImportError:
    from native_test_support import ROOT
import unittest,math
from types import SimpleNamespace
from Shared.materials.native.deformation import write_corner_normals, preserve_normal_source, read_normal_source, apply_normal_deltas, MorphTarget, MorphDelta, NormalTransportUnavailable, _FLOAT_NORMAL_CAPABILITIES, NORMAL_BASE_CORNER

class Data(list):
    def foreach_set(self,field,values):
        values=list(values)
        if field=='vector':
            if len(values)!=len(self)*3:raise ValueError('wrong vector buffer')
            for i,item in enumerate(self):item.vector=tuple(values[i*3:i*3+3])
        else:
            if len(values)!=len(self):raise ValueError('wrong int buffer')
            for item,value in zip(self,values):item.value=value
class Attributes(dict):
    def __init__(self,mesh):super().__init__();self.mesh=mesh
    def new(self,name,kind,domain):
        if name in self:raise ValueError('attribute already exists')
        size=len(self.mesh.vertices) if domain=='POINT' else len(self.mesh.loops)
        attr=SimpleNamespace(name=name,data_type=kind,domain=domain,data=Data(SimpleNamespace(vector=(0.,0.,0.),value=0) for _ in range(size)))
        self[name]=attr;return attr
    def remove(self,attr):del self[attr.name]
class Mesh(dict):
    def __init__(self):
        super().__init__();self.vertices=[];self.loops=[];self.polygons=[];self.attributes=Attributes(self);self.fail_updates=0
    def from_pydata(self,verts,edges,faces):
        self.vertices=[SimpleNamespace(co=tuple(v)) for v in verts];self.loops=[];self.polygons=[]
        for face in faces:
            start=len(self.loops)
            self.loops.extend(SimpleNamespace(vertex_index=v) for v in face)
            self.polygons.append(SimpleNamespace(loop_start=start,loop_total=len(face),material_index=7,use_smooth=True))
    def normals_split_custom_set(self,values):
        values=[tuple(v) for v in values]
        if len(values)!=len(self.loops):raise ValueError('wrong corner count')
        old=self.attributes.get('custom_normal')
        if old is not None:self.attributes.remove(old)
        attr=self.attributes.new('custom_normal','INT16_2D','CORNER')
        for item,v in zip(attr.data,values):item.vector=v
    def update(self):
        if self.fail_updates:self.fail_updates-=1;raise RuntimeError('simulated Blender update failure')
    @property
    def corner_normals(self):
        attr=self.attributes.get('custom_normal')
        return [SimpleNamespace(vector=tuple(v.vector)) for v in attr.data] if attr else [SimpleNamespace(vector=(0.,0.,1.)) for _ in self.loops]
class Meshes:
    def __init__(self):self.active=[]
    def new(self,name):m=Mesh();self.active.append(m);return m
    def remove(self,mesh):self.active.remove(mesh)

def host(version=(5,1,2)):return SimpleNamespace(app=SimpleNamespace(version=version,build_hash=b'fixture'),data=SimpleNamespace(meshes=Meshes()))
def mesh():
    result=Mesh();result.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)]);return result

class TestFloatNormalTransport(unittest.TestCase):
    def setUp(self):_FLOAT_NORMAL_CAPABILITIES.clear();self.host=host()
    def test_direct_float_write_preserves_geometry_flags_and_unrelated_data(self):
        m=mesh();m.attributes.new('Color','FLOAT_VECTOR','POINT');before=[v.co for v in m.vertices]
        result=write_corner_normals(m,[(0,0,2),(0,3,4),(3,0,4)],bpy_module=self.host)
        self.assertEqual(result['storage'],'custom_normal:CORNER:INT16_2D');self.assertFalse(result['legacy_fallback'])
        self.assertEqual(m.corner_normals[1].vector,(0.,.6,.8));self.assertEqual([v.co for v in m.vertices],before)
        self.assertEqual(m.polygons[0].material_index,7);self.assertIn('Color',m.attributes);self.assertEqual(self.host.data.meshes.active,[])
    def test_unsupported_host_fails_before_mutating_original(self):
        m=mesh()
        with self.assertRaises(NormalTransportUnavailable):write_corner_normals(m,[(0,0,1)]*3,bpy_module=host((4,5,0)))
        self.assertNotIn('custom_normal',m.attributes)
    def test_free_float_layer_is_replaced_by_fan_space_storage(self):
        m=mesh();old=m.attributes.new('custom_normal','FLOAT_VECTOR','CORNER')
        write_corner_normals(m,[(1,0,0)]*3,bpy_module=self.host)
        self.assertIsNot(m.attributes['custom_normal'],old);self.assertEqual(m.attributes['custom_normal'].data_type,'INT16_2D')
        self.assertEqual([v.vector for v in m.corner_normals],[(1.,0.,0.)]*3)
    def test_zero_area_faces_are_skipped_by_readback(self):
        m=mesh();m.polygons[0].area=0.
        m.normals_split_custom_set_orig=m.normals_split_custom_set
        m.normals_split_custom_set=lambda values:m.normals_split_custom_set_orig([(0.,1.,0.)]*len(values))
        result=write_corner_normals(m,[(1,0,0)]*3,bpy_module=self.host)
        self.assertEqual(result['unchecked_degenerate_corners'],3)
        m.polygons[0].area=1.
        with self.assertRaises(NormalTransportUnavailable):write_corner_normals(m,[(1,0,0)]*3,bpy_module=self.host)
    def test_invalid_input_and_update_failure_are_atomic(self):
        m=mesh();write_corner_normals(m,[(0,0,1)]*3,bpy_module=self.host)
        for invalid in ([(0,0,1)],[(0,0,0)]*3,[(math.nan,0,1)]*3):
            with self.assertRaises(ValueError):write_corner_normals(m,invalid,bpy_module=self.host)
        m.fail_updates=1
        with self.assertRaises(RuntimeError):write_corner_normals(m,[(1,0,0)]*3,bpy_module=self.host)
        self.assertEqual([v.vector for v in m.corner_normals],[(0.,0.,1.)]*3)
    def test_pristine_base_source_map_and_corner_seams_do_not_accumulate(self):
        m=mesh();base=[(0,0,.99998),(0,1,0)];corners=[(0,0,.99998),(0,1,0),(1,0,0)]
        preserve_normal_source(m,base,[0,1,0],source_vertex_count=2,corner_normals=corners,bpy_module=self.host)
        target=MorphTarget('female',(MorphDelta((100,200,300),(0,1,0),0),))
        before=[v.co for v in m.vertices]
        apply_normal_deltas(m,[target],{'female':1.},bpy_module=self.host);first=[v.vector for v in m.corner_normals]
        apply_normal_deltas(m,[target],{'female':0.},bpy_module=self.host)
        self.assertEqual(m.corner_normals[0].vector,(0.,0.,1.));self.assertEqual(m.corner_normals[2].vector,(1.,0.,0.))
        apply_normal_deltas(m,[target],{'female':1.},bpy_module=self.host)
        self.assertEqual(first,[v.vector for v in m.corner_normals]);self.assertNotEqual(first[0],first[2])
        self.assertEqual([v.co for v in m.vertices],before)
        self.assertEqual(m.attributes[NORMAL_BASE_CORNER].data[0].vector,(0,0,.99998))
        self.assertEqual(read_normal_source(m)['render_source_indices'],(0,1,0))
        with self.assertRaises(ValueError):preserve_normal_source(m,base,[0,1,0],bpy_module=self.host)
    def test_topology_or_source_map_edit_blocks_delta_application(self):
        m=mesh();preserve_normal_source(m,[(0,0,1)]*3,[0,1,2],bpy_module=self.host)
        m.loops[0].vertex_index=2
        with self.assertRaises(ValueError):apply_normal_deltas(m,[],{},bpy_module=self.host)
        self.assertNotIn('custom_normal',m.attributes)

if __name__=='__main__':unittest.main()
