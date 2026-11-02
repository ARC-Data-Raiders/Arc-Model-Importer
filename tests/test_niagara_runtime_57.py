"""NN3-H: fixed supervisor vectors for opt-in VectorVM 5.7 semantics."""
import ctypes
import math
import os
import struct
import unittest
from niagara_vm import OPCODES
from niagara_runtime import (Dataset, ExecutionError, IdState, execute, float_word,
                            word_float, word_int, rng_seed_state)
IDS = {name: code for code, name in OPCODES.items()}
def op(name, *words, mask=None):
    return bytes([IDS[name]]) + (bytes([mask]) if mask is not None else b'') + struct.pack('<'+'H'*len(words), *words)
def prog(*parts): return b''.join(parts)+b'\0'
def run(name, *arrays, raw=False, **kwargs):
    n=len(arrays[0]); regs={i:[v & 0xffffffff if raw else float_word(v) for v in a] for i,a in enumerate(arrays)}
    return execute(prog(op(name,*range(len(arrays)),10,mask=0)),num_instances=n,initial_registers=regs,x86_semantics=True,**kwargs)
class VectorVM57Tests(unittest.TestCase):
    def floats(self,name,*arrays,**kw):return list(map(word_float,run(name,*arrays,**kw).registers[10]))
    def test_gate(self):
        with self.assertRaisesRegex(ExecutionError,'unsupported operation sin'):
            execute(prog(op('sin',0,1,mask=0)),num_instances=1)
        with self.assertRaisesRegex(ExecutionError,'requires x86_semantics'):
            execute(b'\0',num_instances=1,rng_seed=(1,3))
    def test_frac(self):self.assertEqual(self.floats('frac',[-1.25,2.75]),[-.25,.75])
    def test_sign(self):self.assertEqual(self.floats('sign',[0.,-0.,-3.,float('nan')]),[1.,1.,-1.,-1.])
    def test_round(self):self.assertEqual(run('round',[2.5,3.5,-2.5,-.4,.5]).registers[10],tuple(map(float_word,[2.,4.,-2.]))+(0x80000000,0))
    def test_trunc_floor_ceil(self):
        self.assertEqual(run('trunc',[-1.7,-.5]).registers[10],(float_word(-1.),0x80000000))
        self.assertEqual(self.floats('floor',[-1.2]),[-2.]);self.assertEqual(self.floats('ceil',[-1.2]),[-1.]);self.assertEqual(run('ceil',[-.5]).registers[10],(0x80000000,))
    def test_neg(self):self.assertEqual(run('neg',[0.,-0.,2.]).registers[10],(0,0,float_word(-2.)))
    def test_clamp(self):self.assertEqual(self.floats('clamp',[float('nan'),5.],[0.,0.],[1.,1.]),[0.,1.])
    def test_rcp_rsq(self):
        self.assertEqual(self.floats('rcp',[1e-9,4.]),[0.,.25]);self.assertEqual(self.floats('rsq',[0.,4.]),[0.,.5])
    def test_pow_acos(self):
        self.assertEqual(self.floats('pow',[-2.,2.],[2.,3.]),[0.,8.]);self.assertEqual(self.floats('acos',[1.,2.]),[0.,0.])
    def test_fmod(self):
        self.assertEqual(self.floats('fmod',[5.5,1.],[2.,0.]),[1.5,0.]);self.assertTrue(math.isnan(self.floats('fmod',[1.],[float('nan')])[0]))
    def test_shifts(self):
        self.assertEqual(run('bit_lshift',[1,1,1],[33,31,0xffffffff],raw=True).registers[10],(0,0x80000000,0));self.assertEqual(run('bit_rshift',[0x80000000,0xffffffff,0xffffffff],[31,28,32],raw=True).registers[10],(1,15,0))
    def test_f2i(self):self.assertEqual(run('f2i',[float('nan'),3e9,-1.9]).registers[10],(0x80000000,0x80000000,0xffffffff))
    def test_min_max(self):
        self.assertEqual(self.floats('min',[float('nan')],[1.]),[1.]);self.assertTrue(math.isnan(self.floats('min',[1.],[float('nan')])[0]));self.assertEqual(self.floats('max',[float('nan')],[1.]),[1.])
        self.assertEqual(run('min',[-0.,0.],[0.,-0.]).registers[10],(0,0x80000000))
    def test_acquireindex_sign_bit(self):
        r=execute(prog(op('acquireindex',0,0,1,mask=0)),num_instances=4,initial_registers={0:[0xffffffff,1,0x80000000,0]},datasets={0:Dataset()},x86_semantics=True)
        self.assertEqual(r.registers[1],(0,0xffffffff,1,0xffffffff));self.assertEqual(r.access_indices[0],1)
    def test_rng(self):
        r=run('random',[1.]*8,rng_seed=(1,3));self.assertEqual(r.registers[10],(0x3f401d7c,0x3ed9ccb0,0x3f2f15bc,0x3f1db6de,0x3f1e5bec,0x3e36ec50,0x3d72fee0,0x3ec363ec))
        s,c=rng_seed_state(1,3);self.assertEqual(s[0],[0,0xe4c14788,0xbe5caab4,0x29d191ca]);self.assertEqual(c,[3,0,0x5dcf1985,0x2cfe2c3e])
    def test_stub(self):
        signatures=[{'InputParamLocations':[],'NumOutputs':0} for _ in range(3)];signatures[2]={'InputParamLocations':[0,0],'NumOutputs':2}
        bytecode=prog(bytes([IDS['external_func_call'],2])+struct.pack('<HHHH',0,4,10,11))
        r=execute(bytecode,num_instances=1,constant_tables=[struct.pack('<II',0x3f800000,7)],external_functions=signatures,x86_semantics=True,external_handler='gate_v_stub')
        self.assertEqual((r.registers[10][0],r.registers[11][0]),(0x3dc5a370,0x3dada350))
        self.assertEqual(r.ext_calls[0]['inputs'],[0x3f800000,7])
        r=execute(prog(bytes([IDS['external_func_call'],0])+struct.pack('<H',10)),num_instances=1,external_functions=[{'InputParamLocations':[],'NumOutputs':1}],x86_semantics=True,external_handler='gate_v_stub')
        self.assertEqual(r.registers[10],(0x3f0e18ae,))
    def test_acquire_id(self):
        state=IdState([-1]*32,[10,11,12,13],4,80,-1,0,[],7)
        r=execute(prog(op('acquire_id',0,0,1)),num_instances=3,x86_semantics=True,id_states={0:state})
        self.assertEqual(r.registers[0],(13,12,11));self.assertEqual(r.registers[1],(7,7,7));s=r.id_states[0];self.assertEqual((s.free_ids,s.num_free_ids,s.num_spawned_ids,s.spawned_ids),([10],1,3,[13,12,11]));self.assertEqual(state.free_ids,[10,11,12,13])
    def test_update_id(self):
        r=execute(prog(op('update_id',0,0,1)),num_instances=4,initial_registers={0:[5,6,7,8],1:[0,0xffffffff,1,0xffffffff]},x86_semantics=True,id_states={0:IdState([-1]*16,[],0,80,-1,0,[],7)})
        s=r.id_states[0];self.assertEqual((s.id_table[5],s.id_table[7],s.free_ids,s.num_free_ids,s.max_used_id),(0,1,[6,8],2,6))
    def test_update_id_instance_offset(self):
        r=execute(prog(op('update_id',0,0,1)),num_instances=4,initial_registers={0:[5,6,7,8],1:[0,0xffffffff,1,0xffffffff]},datasets={0:Dataset(instance_offset=3)},x86_semantics=True,id_states={0:IdState([-1]*16,[],0,80,-1,0,[],7)})
        s=r.id_states[0];self.assertEqual((s.id_table[5],s.id_table[7],s.free_ids,s.num_free_ids,s.max_used_id),(3,4,[6,8],2,6))
    def test_taints(self):
        r=execute(prog(op('random',0,1,mask=0),op('add',1,0,2,mask=0),op('exec_index',3),op('outputdata_float',0,3,2,0,mask=0)),num_instances=1,initial_registers={0:[float_word(1.)]},datasets={0:Dataset(output_capacities={('float',0):1})},x86_semantics=True,rng_seed=(1,3))
        self.assertEqual(r.taints[2],frozenset({'rng'}));self.assertEqual(r.output_taints[0,('float',0)],frozenset({'rng'}));self.assertEqual(run('sin',[.5]).taints[10],frozenset({'svml'}));self.assertEqual(run('mad',[1.],[2.],[3.]).taints[10],frozenset({'fma'}))
    @unittest.skipUnless(os.environ.get('DR_SVML_SHIM'),'DR_SVML_SHIM unset')
    def test_svml_shim(self):
        import numpy as np
        path=os.environ['DR_SVML_SHIM'];dll=ctypes.CDLL(path);p=ctypes.POINTER(ctypes.c_float);dll.dr_sin.argtypes=[p,p,ctypes.c_int];dll.dr_sin.restype=None;a=np.array([.5],dtype=np.float32);out=np.zeros(1,dtype=np.float32);dll.dr_sin(a.ctypes.data_as(p),out.ctypes.data_as(p),1)
        self.assertEqual(run('sin',[.5],math_backend='svml_shim',svml_shim_path=path).registers[10],(float_word(float(out[0])),))