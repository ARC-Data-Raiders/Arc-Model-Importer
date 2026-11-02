"""Constant layout/availability fixtures; values are explicitly authored."""
import struct
import unittest
from niagara_vm import OPCODES
from niagara_context import ContextError, build_constant_context, build_dataset_inputs
from niagara_runtime import Dataset, execute
IDS={name:index for index,name in OPCODES.items()}
def op(name,*words,mask=None):
    return bytes([IDS[name]])+(bytes([mask]) if mask is not None else b'')+struct.pack('<'+'H'*len(words),*words)
def script(usage,raw,size=0,data=b'',literals=b'',offsets=()):
    return {'Properties':{'Usage':usage,'CachedScriptVM':{'ByteCode':{'Data':list(raw+b'\0')},
        'ScriptLiterals':list(literals)},'ScriptExecutionParamStore':{'ParameterSize':size,
        'ParameterData':list(data),'SortedParameterOffsets':list(offsets)}}}
def build(exp,**kwargs):
    return build_constant_context(exp,layout_profile='ue5.3.2',**kwargs)

class NiagaraConstantContextTests(unittest.TestCase):
    def test_interpolated_particle_spawn_layout_and_execution(self):
        # Handler sequence/constant offsets observed in LocalFogSphere spawn.
        raw=(op('enter_stat_scope',1320)+op('exec_index',0)+op('addi',612,0,1,mask=1)
            +op('acquireindex',0,1324,0,mask=1)+op('outputdata_int32',0,0,1,0,mask=0)
            +op('exit_stat_scope'))
        exp=script(4,raw,size=20,data=bytes(40),literals=struct.pack('<ii',1,-1))
        with self.assertRaisesRegex(ContextError,'emitter.EmitterTotalSpawnedParticles'):
            build(exp)
        context=build(exp,runtime_values={'emitter.EmitterTotalSpawnedParticles':100})
        self.assertEqual([len(t) for t in context.constant_tables],[32,64,512,32,20,32,64,512,32,20,8])
        self.assertEqual([r['constantOffset'] for r in context.required_constants],[612,1320,1324])
        self.assertEqual(context.constant_tables[0],bytes([0xcd])*32)
        self.assertEqual(context.table_manifest[0]['unavailableBytes'],32)
        result=execute(context.bytecode,num_instances=3,constant_tables=context.constant_tables,
            datasets={0:Dataset(output_capacities={('int32',0):3})})
        self.assertEqual(result.outputs[0,('int32',0)],(100,101,102))

    def test_system_order_uses_unpadded_parameter_size(self):
        exp=script('ENiagaraScriptUsage::SystemSpawnScript',
            op('addi',32,36,0,mask=3),size=4,data=struct.pack('<i',7),literals=struct.pack('<i',5))
        exp['Properties']['ScriptExecutionParamStore']['PaddedParameterSize']=64
        context=build(exp)
        self.assertEqual([len(t) for t in context.constant_tables],[32,4,4])
        self.assertEqual(context.table_manifest[2]['byteOffset'],36)
        result=execute(context.bytecode,num_instances=1,constant_tables=context.constant_tables)
        self.assertEqual(result.registers[0],(12,))

    def test_runtime_delta_is_required_and_no_defaults_invented(self):
        exp=script(12,op('mul',4,32,0,mask=3),literals=struct.pack('<f',10))
        with self.assertRaisesRegex(ContextError,'global.EngineDeltaTime'):build(exp)
        ctx=build(exp,runtime_values={'global.EngineDeltaTime':.25})
        result=execute(ctx.bytecode,num_instances=1,constant_tables=ctx.constant_tables)
        self.assertEqual(result.registers[0],(0x40200000,))

    def test_explicit_previous_frame_and_owner_component(self):
        # Previous global starts660 for interpolated external20.
        exp=script(4,op('add',664,528,0,mask=3),size=20,data=bytes(40))
        with self.assertRaisesRegex(ContextError,'owner.EngineXAxis'):build(exp)
        ctx=build(exp,runtime_values={'previous.global.EngineDeltaTime':.5,
            'owner.EngineXAxis':[2,3,4]})
        self.assertEqual([r['table'] for r in ctx.required_constants],['owner','previous.global'])

    def test_parameter_override_and_serialized_default_provenance(self):
        exp=script(11,op('addi',32,36,0,mask=3),size=4,data=struct.pack('<i',7),
            literals=struct.pack('<i',5),offsets=[{'Name':'Emitter.RandomSeed','Offset':0}])
        ctx=build(exp,parameter_overrides={'Emitter.RandomSeed':struct.pack('<i',10)})
        self.assertEqual(ctx.required_constants[0]['provenance'],('parameter override Emitter.RandomSeed',))
        result=execute(ctx.bytecode,num_instances=1,constant_tables=ctx.constant_tables)
        self.assertEqual(result.registers[0],(15,))
        with self.assertRaisesRegex(ContextError,'must contain 4 bytes'):
            build(exp,parameter_overrides={'Emitter.RandomSeed':b'\x01'})

    def test_missing_bytes_bad_usage_bad_profile_and_unknown_fields(self):
        with self.assertRaisesRegex(ContextError,'ParameterData size'):
            build(script(4,op('exec_index',0),size=20,data=bytes(20)))
        with self.assertRaisesRegex(ContextError,'unsupported script usage'):
            build(script(8,op('exec_index',0)))
        with self.assertRaisesRegex(ContextError,'unsupported constant layout profile'):
            build_constant_context(script(11,op('exec_index',0)),layout_profile='guessed')
        with self.assertRaisesRegex(ContextError,'unknown or irrelevant'):
            build(script(11,op('exec_index',0)),runtime_values={'emitter.EmitterAge':0})
        with self.assertRaisesRegex(ContextError,'exceeds assembled'):
            build(script(11,op('neg',1000,0,mask=1)))
        with self.assertRaisesRegex(ContextError,'padding-or-unknown'):
            build(script(11,op('negi',24,0,mask=1)))

class NiagaraDatasetInputsTests(unittest.TestCase):
    def fixture(self):
        exp=script(12,op('inputdata_float',1,0,0)+op('inputdata_float',1,1,1)+op('inputdata_int32',1,0,2))
        context=build(exp)
        compiled={'Variables':[{'Name':'User.Color'},{'Name':'Engine.Owner.ExecutionState'}],
            'VariableLayouts':[
                {'FloatComponentStart':0,'Int32ComponentStart':0,'LayoutInfo':{
                    'NumFloatComponents':4,'ComponentsOffsets':[0,4,8,12,0,1,2,3]}},
                {'FloatComponentStart':4,'Int32ComponentStart':0,'LayoutInfo':{
                    'NumInt32Components':1,'ComponentsOffsets':[0,0]}}]}
        store={'SortedParameterOffsets':[{'Name':'User.Color','Offset':0}],
            'ParameterData':list(struct.pack('<ffff',1,.5,.25,1))}
        return context,compiled,store

    def test_named_inputs_mix_recovered_defaults_and_explicit_values(self):
        ctx,compiled,store=self.fixture()
        result=build_dataset_inputs(ctx,compiled,1,num_instances=2,parameter_store=store,
            values={'Engine.Owner.ExecutionState':0})
        self.assertEqual(result.inputs,{('float',0):(1.,1.),('float',1):(.5,.5),('int32',0):(0,0)})
        self.assertEqual(result.provenance[0]['variable'],'User.Color')
        self.assertEqual(result.provenance[0]['provenance'],'serialized instance parameter default')
        self.assertEqual(result.provenance[-1]['provenance'],'explicit uniform input')
        evaluated=execute(ctx.bytecode,num_instances=2,constant_tables=ctx.constant_tables,
            datasets={1:Dataset(inputs=result.inputs)})
        self.assertEqual(evaluated.registers[1],(0x3f000000,0x3f000000))

    def test_explicit_user_value_overrides_serialized_default(self):
        ctx,compiled,store=self.fixture()
        result=build_dataset_inputs(ctx,compiled,1,num_instances=1,parameter_store=store,
            values={'Engine.Owner.ExecutionState':0,'User.Color':[.25,.75,0,1]})
        self.assertEqual(result.inputs['float',1],(.75,))
        self.assertTrue(all(p['provenance']=='explicit uniform input' for p in result.provenance))

    def test_missing_unknown_and_truncated_input_fail_closed(self):
        ctx,compiled,store=self.fixture()
        with self.assertRaisesRegex(ContextError,'missing explicit dataset input Engine.Owner.ExecutionState'):
            build_dataset_inputs(ctx,compiled,1,num_instances=1,parameter_store=store)
        with self.assertRaisesRegex(ContextError,'unknown or unread'):
            build_dataset_inputs(ctx,compiled,1,num_instances=1,parameter_store=store,
                values={'Engine.Owner.ExecutionState':0,'Typo':1})
        store['ParameterData']=[0]
        with self.assertRaisesRegex(ContextError,'missing parameter bytes'):
            build_dataset_inputs(ctx,compiled,1,num_instances=1,parameter_store=store,
                values={'Engine.Owner.ExecutionState':0})

    def test_missing_layout_and_mixed_layout_fail_closed(self):
        ctx,compiled,store=self.fixture()
        with self.assertRaisesRegex(ContextError,'incomplete compiled'):
            build_dataset_inputs(ctx,{},1,num_instances=1)
        compiled['VariableLayouts'][0]['LayoutInfo']['NumInt32Components']=1
        with self.assertRaisesRegex(ContextError,'unsupported mixed'):
            build_dataset_inputs(ctx,compiled,1,num_instances=1,parameter_store=store,
                values={'Engine.Owner.ExecutionState':0})

if __name__=='__main__':unittest.main()

