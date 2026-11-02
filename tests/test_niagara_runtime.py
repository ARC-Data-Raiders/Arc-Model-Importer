"""Authored bytecode fixtures; not cooked-engine or Arc Raiders equivalence."""
import struct
import json
from pathlib import Path
import unittest
from niagara_vm import OPCODES
from niagara_runtime import Dataset, ExecutionError, execute, float_word, word_float, word_int
IDS = {name: value for value, name in OPCODES.items()}
def op(name, *words, mask=None):
    return bytes([IDS[name]]) + (bytes([mask]) if mask is not None else b'') + struct.pack('<' + 'H' * len(words), *words)
def program(*parts):
    return b''.join(parts) + b'\0'

class OfflineVectorVMTests(unittest.TestCase):
    def test_actual_ue53_engine_reference_fixture(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/niagara_vm_engine53.json').read_text(encoding='utf-8'))
        datasets={entry['index']:Dataset(
            inputs={(v['kind'],v['attribute']):v['values'] for v in entry['inputs']},
            output_capacities={(v['kind'],v['attribute']):v['capacity'] for v in entry['outputCapacities']},
            instance_offset=entry['instanceOffset'],access_index=entry['accessIndex'])
            for entry in fixture['datasets']}
        result=execute(bytes.fromhex(fixture['bytecodeHex']),num_instances=fixture['numInstances'],
            constant_tables=[bytes.fromhex(v) for v in fixture['constantTablesHex']],
            datasets=datasets,profile=fixture['bytecodeProfile'])
        expected=fixture['expected']
        actual=result.outputs[expected['dataset'],(expected['kind'],expected['attribute'])]
        # Engine buffers use an authored sentinel where the VM never wrote.
        words=[struct.pack('<f',expected['unwrittenSentinel'] if v is None else v).hex() for v in actual]
        self.assertEqual(words,expected['engineBinary32'])
        self.assertEqual(result.access_indices[expected['dataset']],expected['accessIndex'])

    def test_velocity_update_and_compacted_outputs(self):
        # Explicit authored Euler update x'=x+v*dt, with supplied alive mask.
        raw = program(op('inputdata_float', 0, 0, 0), op('inputdata_float', 0, 1, 1),
            op('inputdata_int32', 0, 0, 2), op('mul', 1, 0, 3, mask=2),
            op('add', 0, 3, 4, mask=0), op('acquireindex', 0, 2, 5, mask=0),
            op('outputdata_float', 0, 5, 4, 0, mask=0))
        ds = Dataset(inputs={('float', 0): [10,20,30,40,50], ('float', 1): [2,-4,6,8,-10],
            ('int32', 0): [-1,0,-1,-1,0]}, output_capacities={('float', 0): 5})
        result = execute(raw, num_instances=5, constant_tables=[struct.pack('<f', .25)], datasets={0: ds})
        self.assertEqual(result.outputs[0, ('float', 0)], (10.5,31.5,42.0,None,None))
        self.assertEqual(result.access_indices, {0: 2})
        self.assertEqual(ds.access_index, -1)
        self.assertEqual(ds.inputs['float', 0], [10,20,30,40,50])
        self.assertEqual(list(map(word_int, result.registers[5])), [0,-1,1,2,-1])

    def test_byte_offsets_table_resolution_and_word_alias(self):
        raw = program(op('add', 0, 4, 0, mask=3), op('fasi', 0, 1, mask=0),
            op('iasf', 1, 2, mask=0), op('addi', 1, 8, 3, mask=2))
        result = execute(raw, num_instances=3, constant_tables=[struct.pack('<f',1.25), struct.pack('<fi',2.5,1)])
        self.assertEqual(result.registers[0], (float_word(3.75),)*3)
        self.assertEqual(result.registers[1], result.registers[2])
        self.assertEqual(result.registers[3], (float_word(3.75)+1,)*3)

    def test_comparison_masks_and_bitwise_select(self):
        raw = program(op('cmplt', 0, 0, 1, mask=2), op('select', 1, 4, 8, 2, mask=6))
        result = execute(raw, num_instances=3, initial_registers={0:list(map(float_word,[-2,0,3]))},
            constant_tables=[struct.pack('<fff',0,10,20)])
        self.assertEqual(result.registers[1], (0xffffffff,0,0))
        self.assertEqual(list(map(word_float,result.registers[2])), [10,20,20])
        result = execute(program(op('select',0,1,2,3,mask=0)),num_instances=1,
            initial_registers={0:[0xff],1:[0x12345678],2:[0xaabbccdd]})
        self.assertEqual(result.registers[3], (0xaabbcc78,))

    def test_dataset_offset_noadvance_and_exec_index(self):
        raw = program(op('inputdata_noadvance_float',1,0,0), op('exec_index',1),
            op('acquireindex',1,0,2,mask=1), op('outputdata_float',1,2,0,0,mask=0),
            op('outputdata_int32',1,2,1,0,mask=0))
        ds = Dataset(inputs={('float',0):[99,7]}, output_capacities={('float',0):4,('int32',0):4},instance_offset=1)
        result = execute(raw,num_instances=3,start_instance=8,constant_tables=[struct.pack('<i',-1)],datasets={1:ds})
        self.assertEqual(result.outputs[1,('float',0)],(None,7,7,7))
        self.assertEqual(result.outputs[1,('int32',0)],(None,8,9,10))

    def test_safe_division_and_integer_wrap(self):
        raw = program(op('div',0,1,2,mask=0),op('divi',3,4,5,mask=0),op('addi',3,4,6,mask=0))
        result = execute(raw,num_instances=3,initial_registers={
            0:list(map(float_word,[10,10,10])),1:list(map(float_word,[0,1e-9,2])),
            3:[0x80000000,7,0x7fffffff],4:[0xffffffff,0xfffffffe,1]})
        self.assertEqual(list(map(word_float,result.registers[2])),[0,0,5])
        self.assertEqual(list(map(word_int,result.registers[5])),[0x7fffffff,-3,0x7fffffff])
        self.assertEqual(result.registers[6][2],0x80000000)

    def test_external_random_and_id_fail_closed(self):
        for raw in [program(bytes([IDS['external_func_call'],0])),
                program(op('random',0,1,mask=0)),program(op('acquire_id',0,1,2))]:
            with self.assertRaises(ExecutionError): execute(raw,num_instances=1,external_functions=[0])
        with self.assertRaisesRegex(ExecutionError,'unsupported operation random'):
            execute(program(op('add',4,5,6,mask=0),op('random',0,1,mask=0)),num_instances=1)

    def test_missing_inputs_indices_and_budgets(self):
        cases = [(program(op('add',0,1,2,mask=0)),{}),
            (program(op('add',0,8,2,mask=3)),{'constant_tables':[b'\0'*4]}),
            (program(op('outputdata_float',0,0,1,0,mask=0)),{'initial_registers':{0:[99],1:[0]},
                'datasets':{0:Dataset(output_capacities={('float',0):1})}}),
            (program(op('exec_index',0)),{'max_storage_words':0}),
            (program(op('exec_index',0)),{'max_lane_operations':0})]
        for raw,kwargs in cases:
            with self.subTest(kwargs=kwargs),self.assertRaises(ExecutionError): execute(raw,num_instances=1,**kwargs)
        with self.assertRaisesRegex(ExecutionError,'crosses table boundary'):
            execute(program(op('neg',2,0,mask=1)),num_instances=1,constant_tables=[b'\0'*4,b'\0'*4])

    def test_conversions_logic_sqrt_and_modulus(self):
        raw = program(op('f2i',0,1,mask=0), op('f2b',0,2,mask=0),
            op('b2i',2,3,mask=0), op('b2f',2,4,mask=0), op('i2b',1,5,mask=0),
            op('logic_and',2,5,6,mask=0), op('logic_not',6,7,mask=0),
            op('sqrt',0,8,mask=0), op('fmod',0,0,9,mask=2))
        result = execute(raw,num_instances=4,initial_registers={0:list(map(float_word,[-2.75,0,.25,4]))},
            constant_tables=[struct.pack('<f',2)])
        self.assertEqual(list(map(word_int,result.registers[1])),[-2,0,0,4])
        self.assertEqual(result.registers[2],(0,0,0xffffffff,0xffffffff))
        self.assertEqual(result.registers[3],(0,0,1,1))
        self.assertEqual(list(map(word_float,result.registers[4])),[0,0,1,1])
        self.assertEqual(result.registers[6],(0,0,0,0xffffffff))
        self.assertEqual(result.registers[7],(0xffffffff,0xffffffff,0xffffffff,0))
        self.assertEqual(list(map(word_float,result.registers[8])),[0,0,.5,2])
        self.assertEqual(list(map(word_float,result.registers[9])),[-.75,0,.25,0])
        with self.assertRaisesRegex(ExecutionError,'out-of-range f2i'):
            execute(program(op('f2i',0,1,mask=0)),num_instances=1,initial_registers={0:[float_word(2147483648)]})

    def test_mad_lerp_clamp_and_bitwise_integer_operations(self):
        raw = program(op('mad',0,4,8,0,mask=7), op('lerp',0,4,12,1,mask=7),
            op('clamp',0,16,4,2,mask=6), op('bit_not',20,3,mask=1),
            op('bit_xor',3,24,4,mask=2), op('muli',24,24,5,mask=3))
        constants = struct.pack('<fffffii',2,4,1,.25,0,0x1234,3)
        result = execute(raw,num_instances=1,constant_tables=[constants])
        self.assertEqual(list(map(word_float,[result.registers[i][0] for i in (0,1,2)])),[9,2.5,4])
        self.assertEqual(result.registers[3],((~0x1234)&0xffffffff,))
        self.assertEqual(result.registers[4],(((~0x1234)^3)&0xffffffff,))
        self.assertEqual(result.registers[5],(9,))

    def test_zero_instances_and_nonfinite(self):
        self.assertEqual(execute(program(op('exec_index',0)),num_instances=0).registers[0],())
        with self.assertRaisesRegex(ExecutionError,'nonfinite'):
            execute(program(op('add',0,0,1,mask=0)),num_instances=1,initial_registers={0:[float_word(float('nan'))]})

if __name__ == '__main__': unittest.main()

