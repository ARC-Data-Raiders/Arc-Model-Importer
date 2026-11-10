import json
import pathlib
import unittest
from .. import opt, ssa

WAVE2=pathlib.Path(r'Z:\Datamining\Arc Raiders\CodexScratch\DataRaiders-BlenderImporter\.local\parity-v2\wave2')


class OptimizerTests(unittest.TestCase):
    def setUp(self):
        item=opt.load_inputs(WAVE2)[0]
        fixture=json.loads(pathlib.Path(item['fixture']).read_text())
        shader=json.loads(pathlib.Path(fixture['shader_json']).read_text())
        self.ev=ssa.Evaluator(shader,fixture)

    def dag(self,rows,outputs):
        return dict(nodes=[dict(id=i,op=op,args=args,type='u32',src=-1) for i,(op,args) in enumerate(rows)],outputs=outputs)

    def run_opt(self,dag):return opt.optimize(dag,self.ev)[0]

    def test_t1_ftz(self):
        meta=dict(read=True,float_mod=True,neg=False,abs=False)
        d=self.dag([('input',['v0','x']),('add',[0,0]),('mov',[1,meta]),('cbload',[dict(slot=0,component=0,offset=0,dynamic=False)]),('mov',[3,meta])],dict(a=2,b=4))
        result=self.run_opt(d)
        self.assertEqual([n['op'] for n in result['nodes']].count('mov'),1)
        self.assertEqual(result['nodes'][result['outputs']['a']]['op'],'add')

    def test_t2_fold(self):
        d=self.dag([('const',[0x3f800000]),('const',[0x40000000]),('add',[0,1])],dict(v=2))
        self.ev.values=[]
        expected=int(self.ev.evaluate(d)['v'][0,0])
        self.assertEqual(self.run_opt(d)['nodes'][0]['args'],[expected])

    def test_t3_select(self):
        d=self.dag([('input',['v0','x']),('input',['v0','y']),('const',[0]),('select',[0,1,1]),('select',[2,0,1])],dict(a=3,b=4))
        result=self.run_opt(d)
        self.assertEqual(result['outputs']['a'],result['outputs']['b'])
        self.assertNotIn('select',[n['op'] for n in result['nodes']])

    def test_t4_cse(self):
        d=self.dag([('input',['v0','x']),('mul',[0,0]),('mul',[0,0])],dict(a=1,b=2))
        result=self.run_opt(d)
        self.assertEqual(result['outputs']['a'],result['outputs']['b'])

    def test_t5_dce(self):
        d=self.dag([('input',['v0','x']),('const',[123])],dict(v=0))
        self.assertEqual(len(self.run_opt(d)['nodes']),1)

    def test_t6_idempotence(self):
        d=self.dag([('input',['v0','x']),('mul',[0,0]),('mul',[0,0]),('const',[123])],dict(a=1,b=2))
        first=self.run_opt(d)
        self.assertEqual(json.dumps(first),json.dumps(self.run_opt(first)))


if __name__=='__main__':unittest.main()
