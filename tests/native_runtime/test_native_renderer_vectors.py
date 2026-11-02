try:
    from .native_test_support import ROOT
except ImportError:
    from native_test_support import ROOT
"""Graph contract tests; actual renderer basis is verified by root-only fixture."""
import unittest
from material_runtime.renderer_vectors import lower_native_tangent_view, lower_world_view

class Socket:
    def __init__(self,name): self.name=name;self.default_value=None
class Ports:
    def __init__(self): self.sockets={}
    def __getitem__(self,key): return self.sockets.setdefault(key,Socket(key))
class Node:
    def __init__(self,kind,label):self.kind=kind;self.label=label;self.inputs=Ports();self.outputs=Ports()
class RendererVectorTests(unittest.TestCase):
    def setUp(self):self.nodes=[];self.links=[]
    def factory(self,kind,label=''):
        node=Node(kind,label);self.nodes.append(node);return node
    def test_native_basis_uses_renderer_uv_handed_axes(self):
        outputs=lower_native_tangent_view(self.factory,lambda a,b:self.links.append((a,b)),'NativeUV')
        basis=[n for n in self.nodes if n.kind=='ShaderNodeNormalMap']
        self.assertEqual([n.inputs['Color'].default_value for n in basis],[(1,.5,.5,1),(.5,0,.5,1),(.5,.5,1,1)])
        self.assertTrue(all(n.space=='TANGENT' and n.uv_map=='NativeUV' for n in basis))
        dots=[n for n in self.nodes if n.kind=='ShaderNodeVectorMath']
        self.assertEqual(outputs,tuple(n.outputs['Value'] for n in dots));self.assertEqual(len(self.links),6)
        incoming=self.nodes[0].outputs['Incoming']
        self.assertTrue(all(self.links[i*2][0] is incoming for i in range(3)))
        self.assertTrue(all(n.operation=='DOT_PRODUCT' for n in dots))
        self.assertTrue(all(n.label.startswith('DR.View.') for n in self.nodes))
    def test_world_view_preserves_incoming_direction(self):
        out=lower_world_view(self.factory,lambda a,b:self.links.append((a,b)))
        self.assertEqual(out,tuple(self.nodes[1].outputs[a] for a in 'XYZ'))
        self.assertIs(self.links[0][0],self.nodes[0].outputs['Incoming'])
    def test_invalid_uv_name_fails_before_mutation(self):
        with self.assertRaises(TypeError):lower_native_tangent_view(self.factory,lambda a,b:None,42)
        self.assertEqual(self.nodes,[])

class RendererViewIRTests(unittest.TestCase):
    def test_explicit_native_tangent_and_world_inputs_stay_distinct(self):
        from material_runtime.core import expr,literal,evaluate
        tangent=expr('tangent_view',uv_map='NativeUV')
        incoming=expr('incoming')
        supplied={'tangent_view':(.2,-.3,.9),'incoming':(-.3,.2,.9)}
        encoded=expr('add',literal((.5,.5,.5)),expr('multiply',tangent,literal(.5)))
        self.assertEqual(evaluate(tangent,supplied),(.2,-.3,.9))
        self.assertEqual(evaluate(incoming,supplied),(-.3,.2,.9))
        for actual,expected in zip(evaluate(encoded,supplied),(.6,.35,.95)):
            self.assertAlmostEqual(actual,expected)
    def test_missing_view_context_fails_explicitly(self):
        from material_runtime.core import expr,evaluate
        for op in ('tangent_view','incoming'):
            with self.assertRaises(KeyError):evaluate(expr(op),{})
    def test_vector_view_input_can_be_resolved_explicitly_by_context(self):
        from material_runtime.core import expr,evaluate
        calls=[]
        def lookup(key):calls.append(key);return (.1,.4,.8)
        view=expr('tangent_view',uv_map='NativeUV')
        self.assertEqual(evaluate(view,{'tangent_view':lookup}),(.1,.4,.8))
        self.assertEqual(calls,[0])
