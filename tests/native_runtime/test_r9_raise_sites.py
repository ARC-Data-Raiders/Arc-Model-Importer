"""R9 prop raise sites: static normals, empty texture refs and ownerless enemy decals never fail an import."""
try:
    from .native_test_support import ROOT, functions_module
    from .test_native_normal_transport import host, Mesh
except ImportError:
    from native_test_support import ROOT, functions_module
    from test_native_normal_transport import host, Mesh
import importlib.util,sys,types,unittest
from types import SimpleNamespace
from unittest.mock import patch
from Shared.materials.native import deformation
from Shared.materials.native.deformation import write_corner_normals, NormalTransportUnavailable, _FLOAT_NORMAL_CAPABILITIES
from Shared.materials.native.interop import Graph


class Corners(list):
    def foreach_get(self,field,out):
        out[:]=[c for item in self for c in item.vector]
class FMesh(Mesh):
    # Real Blender corner_normals supports foreach_get (deformation._corner_normal_array).
    @property
    def corner_normals(self):return Corners(Mesh.corner_normals.fget(self))
class Polys(list):
    def foreach_get(self,field,out):
        out[:]=[getattr(p,field,1.) for p in self]
def mesh():
    result=FMesh();result.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)]);result.polygons=Polys(result.polygons);return result


def skewed(m):
    # Readback returns a normal 90 degrees off the request: over CORNER_NORMAL_TOLERANCE_DEGREES.
    orig=m.normals_split_custom_set
    m.normals_split_custom_set=lambda values:orig([(0.,1.,0.)]*len(values))
    return m


class StaticCornerNormals(unittest.TestCase):
    def setUp(self):_FLOAT_NORMAL_CAPABILITIES.clear();self.host=host()
    def test_strict_default_still_raises(self):
        with self.assertRaises(NormalTransportUnavailable):write_corner_normals(skewed(mesh()),[(1,0,0)]*3,bpy_module=self.host)
    def test_approximate_ok_keeps_set_and_flags(self):
        result=write_corner_normals(skewed(mesh()),[(1,0,0)]*3,bpy_module=self.host,approximate_ok=True)
        self.assertTrue(result['normals_approximate'])
    def test_exact_write_not_flagged(self):
        result=write_corner_normals(mesh(),[(1,0,0)]*3,bpy_module=self.host,approximate_ok=True)
        self.assertFalse(result['normals_approximate'])


class Flat(list):
    def foreach_get(self,field,out):
        out[:]=[c for item in self for c in (getattr(item,field) if isinstance(getattr(item,field),tuple) else (getattr(item,field),))]
    def foreach_set(self,field,values):
        for item,v in zip(self,values):setattr(item,field,bool(v))
class FanMesh(FMesh):
    """Quad (0,1,2)+(0,2,3); vertex 0's fan space reads back (0,1,0) until an edge at vertex 0 is sharp."""
    def __init__(self):
        super().__init__();self.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0)],[],[(0,1,2),(0,2,3)])
        self.polygons=Polys(self.polygons);self.loops=Flat(self.loops)
        self.edges=Flat(SimpleNamespace(vertices=e) for e in ((0,1),(1,2),(2,0),(2,3),(3,0)))
        new=self.attributes.new
        def attr_new(name,kind,domain):
            if domain!='EDGE':return new(name,kind,domain)
            a=SimpleNamespace(name=name,data_type=kind,domain=domain,data=Flat(SimpleNamespace(value=False) for _ in self.edges));self.attributes[name]=a;return a
        self.attributes.new=attr_new
    def normals_split_custom_set(self,values):
        sharp=self.attributes.get('sharp_edge')
        fixed=sharp is not None and any(item.value and 0 in e.vertices for item,e in zip(sharp.data,self.edges))
        values=[(0.,1.,0.) if (l.vertex_index==0 and not fixed) else tuple(v) for l,v in zip(self.loops,values)]
        FMesh.normals_split_custom_set(self,values)


class InvalidFanRepair(unittest.TestCase):
    def setUp(self):_FLOAT_NORMAL_CAPABILITIES.clear();self.host=host()
    def test_sharpen_bad_vertex_edges_repairs_readback(self):
        m=FanMesh();result=write_corner_normals(m,[(0,0,1)]*6,bpy_module=self.host)
        self.assertFalse(result['normals_approximate']);self.assertEqual(result['max_normal_angle_degrees'],0.)
        self.assertAlmostEqual(result['unrepaired_max_normal_angle_degrees'],90.,places=3)
        self.assertEqual(result['sharpened_edges'],3);self.assertEqual(result['repair_passes'],1)
        self.assertEqual([i.value for i in m.attributes['sharp_edge'].data],[True,False,True,False,True])
    def test_exact_mesh_untouched(self):
        m=mesh();result=write_corner_normals(m,[(1,0,0)]*3,bpy_module=self.host)
        self.assertEqual(result['sharpened_edges'],0);self.assertNotIn('sharp_edge',m.attributes)
    def test_failed_strict_repair_restores_sharp_edges(self):
        m=skewed(FanMesh())
        with self.assertRaises(NormalTransportUnavailable):write_corner_normals(m,[(1,0,0)]*6,bpy_module=self.host)
        self.assertNotIn('sharp_edge',m.attributes)


def load_uemodel(deformation_stub):
    pkg='_r9_uemodel_probe';name=pkg+'.Shared.uemodel_import'
    mods={'bpy':types.ModuleType('bpy')}
    for sub,path in ((pkg,ROOT),(pkg+'.Shared',ROOT/'Shared'),(pkg+'.Shared.materials',ROOT/'Shared/materials'),(pkg+'.Shared.materials.native',ROOT/'Shared/materials/native')):
        m=types.ModuleType(sub);m.__path__=[str(path)];mods[sub]=m
    mods[pkg+'.Shared.materials.native.deformation']=deformation_stub
    spec=importlib.util.spec_from_file_location(name,ROOT/'Shared/uemodel_import.py');module=importlib.util.module_from_spec(spec)
    mods[name]=module
    with patch.dict(sys.modules,mods):spec.loader.exec_module(module)
    return module,mods


class StaticImportNeverFails(unittest.TestCase):
    def run_with(self,writer):
        stub=types.ModuleType('deformation');stub.NormalTransportUnavailable=NormalTransportUnavailable;stub.write_corner_normals=writer
        u,mods=load_uemodel(stub);m={}
        with patch.dict(sys.modules,mods),patch('builtins.print'):result=u.write_static_corner_normals(m,[(0,0,1)])
        return result,m
    def test_capability_failure_falls_back_to_auto_normals(self):
        def boom(*a,**k):raise NormalTransportUnavailable('fan-space corner normal readback exceeds 1.0 degree')
        result,m=self.run_with(boom)
        self.assertTrue(result['normals_approximate']);self.assertEqual(result['storage'],'auto');self.assertTrue(m['normals_approximate'])
    def test_approximate_write_requested_and_stamped(self):
        seen={}
        def writer(mesh,normals,**k):seen.update(k);return {'normals_approximate':True}
        result,m=self.run_with(writer)
        self.assertTrue(seen.get('approximate_ok'));self.assertTrue(m['normals_approximate'])


class Img(dict):
    def __init__(self,name):super().__init__();self.name=name;self.pixels=[0.]*4;self.alpha_mode='STRAIGHT';self.colorspace_settings=SimpleNamespace(name='sRGB')
class Images(list):
    def new(self,name,w,h,alpha=False):
        image=Img(name);self.append(image);return image


class EmptyTextureReference(unittest.TestCase):
    def graph(self):
        g=object.__new__(Graph);g.bpy=SimpleNamespace(data=SimpleNamespace(images=Images()));g.report={};g._state={'images':{}}
        g.sample_image=lambda image,uv,label,meta:('sample',image.name,meta)
        return g
    def test_required_empty_ref_samples_unset_stand_in(self):
        for ref in ('',None):
            g=self.graph();out=g.image(ref,label='Decal Mask',required=True)
            self.assertEqual(out[0],'sample');self.assertTrue(out[2]['unset']);self.assertEqual(g.report['unset_textures'],['Decal Mask'])
            self.assertEqual(g._state['images'][out[1]].alpha_mode,'CHANNEL_PACKED')
    def test_stand_in_reused(self):
        g=self.graph();g.image('',label='A');g.image('',label='B');self.assertEqual(len(g.bpy.data.images),1)


class OwnerlessEnemySurface(unittest.TestCase):
    def test_no_owner_returns_none(self):
        bpy=SimpleNamespace(data=SimpleNamespace(objects=[]),context=SimpleNamespace(object=None))
        module=functions_module('enemy_surface',['_owner'],bpy=bpy)
        self.assertIsNone(module._owner(object(),'',None))
    def test_caller_flags_unresolved_owner(self):
        src=(ROOT/'Shared/materials/enemy_surface.py').read_text(encoding='utf-8')
        self.assertIn("report['uv_owner_unresolved'] = True",src)


if __name__=='__main__':unittest.main()
