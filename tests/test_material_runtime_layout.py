import pathlib,sys,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'implementation'))
from material_runtime.layout import arrange
class Node:
    def __init__(self,name,parent=None,kind='MATH'): self.name=name; self.parent=parent; self.type=kind; self.location=(0,0); self.width=0; self.inputs=[]; self.outputs=[]
class Link:
    def __init__(self,a,b): self.from_node=a; self.to_node=b
class Tree(dict):
    def __init__(self,nodes,links): super().__init__(); self.nodes=nodes; self.links=links
class LayoutTests(unittest.TestCase):
    def test_typed_vector_parameter_never_routes_through_color(self):
        from types import SimpleNamespace
        from material_runtime.interop import Graph
        graph=Graph.__new__(Graph); captured=[]
        graph.combine=lambda *values:(captured.append(values) or SimpleNamespace(node=SimpleNamespace(label='')))
        graph.color=lambda *_:self.fail('Typed vector must not become a color')
        vector=graph.parameter('Native vector',(-.7,2.4,-3.2),'Vector')
        self.assertEqual(captured,[(-.7,2.4,-3.2)]); self.assertEqual(vector.node.label,'Native vector')
        with self.assertRaises(ValueError): graph.parameter('UV',(1,2),'Vector')
        with self.assertRaises(ValueError): graph.parameter('UV',(1,2),'float2')
    def test_optional_normal_none_and_empty_map_use_shared_flat_native_vector(self):
        from contextlib import nullcontext
        from material_runtime.interop import Graph
        graph=Graph.__new__(Graph); graph._state={}; calls=[]
        graph.block=lambda _:nullcontext(graph)
        graph.combine=lambda *values:(calls.append(values) or values)
        graph.split=lambda _:self.fail('Optional absent normal must not split a missing socket')
        self.assertEqual(graph.decode_normal(None),(0.,0.,1.))
        self.assertEqual(graph.decode_normal({}),(0.,0.,1.))
        self.assertEqual(calls,[(0.,0.,1.)])
    def test_shared_dag_semantic_frames_and_stable_positions(self):
        source=Node('DR.Source',kind='FRAME'); surface=Node('DR.Surface',kind='FRAME')
        a=Node('a',source); b=Node('b',surface); c=Node('c',surface); d=Node('d',surface)
        tree=Tree([surface,source,a,b,c,d],[Link(a,b),Link(b,c),Link(b,d),Link(c,d)])
        before=[(n.name,n.parent) for n in tree.nodes]; links=tuple(tree.links)
        self.assertIsNone(arrange(tree)); positions=[n.location for n in tree.nodes]
        self.assertLess(source.location[0],surface.location[0]); self.assertLess(b.location[0],c.location[0]); self.assertLess(c.location[0],d.location[0])
        arrange(tree); self.assertEqual(positions,[n.location for n in tree.nodes]); self.assertEqual(before,[(n.name,n.parent) for n in tree.nodes]); self.assertEqual(tuple(tree.links),links)
    def test_cycles_are_components_and_downstream_still_flows(self):
        a,b,c,d=[Node(name) for name in 'abcd']
        tree=Tree([a,b,c,d],[Link(a,b),Link(b,a),Link(b,c),Link(c,d)])
        arrange(tree); self.assertEqual(a.location[0],b.location[0]); self.assertNotEqual(a.location[1],b.location[1]); self.assertLess(b.location[0],c.location[0]); self.assertLess(c.location[0],d.location[0]); self.assertEqual(tree['dr_layout_cycle_components'],1)
    def test_deep_linear_graph_is_iterative(self):
        nodes=[Node(str(i)) for i in range(2500)]; tree=Tree(nodes,[Link(a,b) for a,b in zip(nodes,nodes[1:])])
        arrange(tree); self.assertEqual(nodes[-1].location[0],2499*190.)
if __name__=='__main__': unittest.main()


