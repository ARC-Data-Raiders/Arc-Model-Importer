"""A material texcoord index past the mesh's UV count reads the last channel, as local vertex fetch does (no Blender)."""
import sys
import types
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from Shared.materials.native.interop import Graph

class UvClampTests(unittest.TestCase):
    def graph(self,layers):
        g=object.__new__(Graph);made=[]
        g.obj=types.SimpleNamespace(data=types.SimpleNamespace(uv_layers=[types.SimpleNamespace(name=n) for n in layers]))
        g.report={};g._state={'uv':{}};g._stage='DR.Surface'
        def node(kind,label=''):
            n=types.SimpleNamespace(uv_map='',outputs={'UV':'uv'},label=label);made.append(n);return n
        g.node=node;g.split=lambda v:(v,v,v);g.combine=lambda x,y,z=0:(x,y,z);g.sub=lambda a,b:(a,b)
        return g,made
    def test_one_uv_mesh_reads_uv0_for_uv1(self):
        # SM_Mdr_WindTurbine_01_BrokenBase_A cooks NumTexCoords=1; M_TrimMap_01 tiles with TexCoord[1].
        g,made=self.graph(['UV0'])
        g.uv(1)
        self.assertEqual(made[0].uv_map,'UV0');self.assertIn('UV1 clamped',g.report['notes'][0])
    def test_existing_channel_is_used_and_meshless_still_refuses(self):
        g,made=self.graph(['UV0','UV1']);g.uv(1)
        self.assertEqual(made[0].uv_map,'UV1');self.assertNotIn('notes',g.report)
        g,_=self.graph([])
        with self.assertRaises(IndexError):g.uv(1)
if __name__=='__main__':unittest.main()
