import unittest
from unittest.mock import patch
from .. import lower_dag


class Socket:
    def __init__(self,node):self.node=node;self.default_value=0


class Sockets:
    def __init__(self,node):self.values=[Socket(node) for _ in range(4)]
    def __getitem__(self,key):return self.values[key if isinstance(key,int) else 0]


class Node:
    def __init__(self,kind):
        self.kind=kind
        self.inputs=Sockets(self)
        self.outputs=Sockets(self)
        self.label=''


class Nodes(list):
    def new(self,kind):
        n=Node(kind)
        self.append(n)
        return n


class Links:
    def new(self,a,b):pass


class Tree:
    def __init__(self):self.nodes=Nodes();self.links=Links()


def dag(rows,outputs):
    return dict(nodes=[dict(id=i,op=o,args=a,type=t,src=i) for i,(o,a,t) in enumerate(rows)],outputs=outputs)


class LowerTests(unittest.TestCase):
    def test_l1_idiom(self):
        d=dag([('input',['v0','x'],'f32'),('const',[0x7FFFFFFF],'u32'),('and',[0,1],'u32'),('const',[1],'u32'),('umin',[2,3],'u32'),('utof',[4],'f32')],dict(v=5))
        tree=Tree()
        with patch.object(lower_dag.lb,'nonzero_idiom',return_value=1.0) as helper:
            lower_dag.lower(tree,d,{('v0','x'):2.0},{},['v'],'check')
            helper.assert_called_once_with(tree,tree.nodes[0].outputs[0])
        self.assertFalse(any(getattr(n,'operation',None)=='MULTIPLY' for n in tree.nodes))

    def test_l2_mask_and(self):
        d=dag([('const',[0xFFFFFFFF],'bool-mask'),('const',[0],'bool-mask'),('and',[0,1],'bool-mask')],dict(kill=2))
        tree=Tree()
        lower_dag.lower(tree,d,{},{},['kill'],'check')
        self.assertIn('MULTIPLY',[getattr(n,'operation',None) for n in tree.nodes])

    def test_l3_nonmask_and(self):
        d=dag([('input',['v0','x'],'u32'),('const',[123],'u32'),('and',[0,1],'u32')],dict(v=2))
        with self.assertRaises(lower_dag.Unsupported):lower_dag.lower(Tree(),d,{('v0','x'):3.0},{},['v'],'check')

    def test_l4_float_condition(self):
        d=dag([('input',['v0','x'],'f32'),('const',[0x3F800000],'f32'),('const',[0x40000000],'f32'),('movc',[0,1,2],'f32')],dict(v=3))
        with patch.object(lower_dag.lb,'nonzero_idiom',return_value=1.0) as helper:
            lower_dag.lower(Tree(),d,{('v0','x'):3.0},{},['v'],'check')
            self.assertEqual(helper.call_count,1)

    def test_l5_mask_constants(self):
        for raw,value in [(0,0.0),(0xFFFFFFFF,1.0)]:
            d=dag([('const',[raw],'bool-mask')],dict(kill=0))
            result=lower_dag.lower(Tree(),d,{},{},['kill'],'check')
            self.assertEqual(result['kill'].default_value,value)
        with self.assertRaises(lower_dag.Unsupported):
            lower_dag.lower(Tree(),dag([('const',[7],'bool-mask')],dict(kill=0)),{},{},['kill'],'check')


if __name__=='__main__':unittest.main()
