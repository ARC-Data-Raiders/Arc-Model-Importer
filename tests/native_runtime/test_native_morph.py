try:
    from .native_test_support import ROOT
except ImportError:
    from native_test_support import ROOT
import math,struct,unittest
from Shared.materials.native.deformation import decode_morph_targets, deform_morph_targets, MorphTarget, MorphDelta

def payload(targets):
    out=bytearray(struct.pack('<i',len(targets)))
    for name,deltas in targets:
        name=name.encode('utf-8');out+=struct.pack('<i',len(name))+name+struct.pack('<i',len(deltas))
        for dp,dn,index in deltas:out+=struct.pack('<6fI',*dp,*dn,index)
    return bytes(out)

class TestNativeMorphPayload(unittest.TestCase):
    def test_complete_utf8_payload_keeps_normal_and_source_index(self):
        name='Gender_'+chr(0x5973)
        decoded=decode_morph_targets(payload([(name,[((1,2,3),(.25,.5,.75),7)])]),8)
        self.assertEqual(decoded,(MorphTarget(name,(MorphDelta((1.,2.,3.),(.25,.5,.75),7),)),))
        self.assertEqual(decode_morph_targets(struct.pack('<i',0),0),())
    def test_truncation_counts_terminators_and_trailing_data_fail(self):
        good=payload([('Gender',[((1,2,3),(0,0,1),0)])])
        for cut in range(len(good)):
            with self.subTest(cut=cut),self.assertRaises(ValueError):decode_morph_targets(good[:cut],1)
        for bad in (struct.pack('<i',-1),good+b'x',struct.pack('<ii',1,0),struct.pack('<ii',1,-10),struct.pack('<ii',1,1)+b'\0'+struct.pack('<i',0)):
            with self.assertRaises(ValueError):decode_morph_targets(bad,1)
    def test_invalid_source_and_nonfinite_deltas_fail_before_return(self):
        for data in (payload([('Gender',[((1,2,3),(0,0,1),1)])]),payload([('Gender',[((math.nan,2,3),(0,0,1),0)])])):
            with self.assertRaises(ValueError):decode_morph_targets(data,1)

class TestNativeMorphApplication(unittest.TestCase):
    def test_position_centimeters_do_not_scale_normal_deltas(self):
        targets=decode_morph_targets(payload([('Female',[((1,2,3),(1,0,0),0)])]),1)
        out=deform_morph_targets([(10,20,30)],[(0,0,1)],[0],targets,{'Female':1.},source_vertex_count=1,position_scale=.01,basis=((0,1,0),(1,0,0),(0,0,-1)))
        self.assertEqual(out.positions,((10.02,20.01,29.97),))
        self.assertAlmostEqual(out.normals[0][1],math.sqrt(.5));self.assertAlmostEqual(out.normals[0][2],math.sqrt(.5))
    def test_normal_delta_inverse_transpose_keeps_delta_magnitude(self):
        target=MorphTarget('Female',(MorphDelta((0,0,0),(1,0,0),0),))
        out=deform_morph_targets([(0,0,0)],[(0,0,1)],[0],[target],{'Female':1},source_vertex_count=1,basis=((2,0,0),(0,1,0),(0,0,1)))
        self.assertAlmostEqual(out.normals[0][0],1/math.sqrt(5));self.assertAlmostEqual(out.normals[0][2],2/math.sqrt(5))
    def test_targets_accumulate_before_single_normalization(self):
        targets=(MorphTarget('A',(MorphDelta((1,0,0),(1,0,0),0),)),MorphTarget('B',(MorphDelta((0,1,0),(0,1,0),0),)))
        args=([(0,0,0)],[(0,0,1)],[0])
        first=deform_morph_targets(*args,targets,{'A':1,'B':1},source_vertex_count=1)
        second=deform_morph_targets(*args,targets[::-1],{'A':1,'B':1},source_vertex_count=1)
        self.assertEqual(first,second);self.assertEqual(first.positions,((1.,1.,0.),))
        for value in first.normals[0]:self.assertAlmostEqual(value,1/math.sqrt(3))
    def test_source_mapping_expands_seams_without_averaging_corner_normals(self):
        target=MorphTarget('Female',(MorphDelta((1,0,0),(0,0,1),0),))
        out=deform_morph_targets([(0,0,0),(2,0,0),(0,0,0)],[(0,0,1)]*3,[0,1,0],[target],{'Female':1},source_vertex_count=2,corner_normals=[(1,0,0),(0,1,0),(0,0,1)],corner_vertex_indices=[0,2,1])
        self.assertEqual(out.positions,((1.,0.,0.),(2.,0.,0.),(1.,0.,0.)));self.assertNotEqual(out.corner_normals[0],out.corner_normals[1])
        self.assertAlmostEqual(out.corner_normals[0][0],math.sqrt(.5));self.assertAlmostEqual(out.corner_normals[1][1],math.sqrt(.5));self.assertEqual(out.corner_normals[2],(0.,0.,1.))
    def test_invalid_mappings_unknown_weights_and_singular_normal_are_atomic(self):
        positions=[(0,0,0)];normal=[(0,0,1)]
        t=MorphTarget('Female',(MorphDelta((0,0,0),(0,0,-1),0),))
        for indices,weights in (([1],{'Female':0}),([0],{'Female':1}),([0],{'Missing':1})):
            with self.assertRaises(ValueError):deform_morph_targets(positions,normal,indices,[t],weights,source_vertex_count=1)
        with self.assertRaises(ValueError):deform_morph_targets(positions,normal,[0],[MorphTarget('Huge',(MorphDelta((2,0,0),(0,0,0),0),))],{'Huge':1e308},source_vertex_count=1)
        self.assertEqual(positions,[(0,0,0)]);self.assertEqual(normal,[(0,0,1)])

if __name__=='__main__':unittest.main()
