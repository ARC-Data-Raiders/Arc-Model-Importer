"""Raw-word numpy VectorVM backend; scalar-compatible IDs, external ABI and taints."""
from dataclasses import dataclass,field
import math
import struct
import numpy as np
from niagara_vm import disassemble,Operand
from niagara_runtime import ExecutionError,IdState,SUPPORTED_OPS_57,float_word,word_float,word_int,rng_seed_state
from niagara_runtime import _SVML57,_backend57,_rng57_values,_fma57
@dataclass
class NpDataset:
    inputs:dict=field(default_factory=dict)
    output_capacities:dict=field(default_factory=dict)
    instance_offset:int=0
    access_index:int=-1
@dataclass
class NpExecutionResult:
    registers:dict
    outputs:dict
    written:dict
    access_indices:dict
    instruction_count:int
    taints:dict
    output_taints:dict
    dataset_taints:dict
    id_states:dict
    ext_calls:list

def _elementwise(name,sources,backend,shim,fma,rng,n):
    u=[np.asarray(s,dtype=np.uint32) for s in sources];f=[s.view(np.float32) for s in u];i=[s.view(np.int32) for s in u];a=u[0];fa=f[0];ia=i[0];b=u[1] if len(u)>1 else None;fb=f[1] if len(u)>1 else None;ib=i[1] if len(u)>1 else None;c=u[2] if len(u)>2 else None;fc=f[2] if len(u)>2 else None;eps=np.float32(1e-8);one=np.float32(1)
    if name in ('fasi','iasf'):return a.copy()
    if name=='select':return (a&b)|(~a&c)
    if name=='abs':return a&np.uint32(0x7fffffff)
    if name=='b2f':return a&np.uint32(0x3f800000)
    if name=='b2i':return a&np.uint32(1)
    if name in ('f2b','i2b'):return np.where((fa if name=='f2b' else ia)>0,np.uint32(0xffffffff),np.uint32(0))
    if name=='f2i':
        out=np.full(n,0x80000000,dtype=np.uint32);valid=np.isfinite(fa)&(fa.astype(np.float64)>=-2147483648)&(fa.astype(np.float64)<2147483648);out[valid]=np.trunc(fa[valid].astype(np.float64)).astype(np.int32).view(np.uint32);return out
    if name=='i2f':return ia.astype(np.float32).view(np.uint32)
    if name.startswith('cmp'):
        integer=name.endswith('i');base=name[:-1] if integer else name;x,y=(ia,ib) if integer else (fa,fb);fn={'cmplt':np.less,'cmple':np.less_equal,'cmpgt':np.greater,'cmpge':np.greater_equal,'cmpeq':np.equal,'cmpneq':np.not_equal}[base];return np.where(fn(x,y),np.uint32(0xffffffff),np.uint32(0))
    if name in ('bit_and','logic_and'):return a&b
    if name in ('bit_or','logic_or'):return a|b
    if name in ('bit_xor','logic_xor'):return a^b
    if name in ('bit_not','logic_not'):return ~a
    if name in ('bit_lshift','bit_rshift'):
        counts=np.where(b>31,np.uint32(0),b);v=a<<counts if name=='bit_lshift' else a>>counts;return np.where(b>31,np.uint32(0),v)
    if name in ('addi','subi','muli','negi'):return {'addi':lambda:a+b,'subi':lambda:a-b,'muli':lambda:a*b,'negi':lambda:np.uint32(0)-a}[name]()
    if name=='absi':return np.abs(ia).view(np.uint32)
    if name in ('mini','maxi','clampi'):
        return np.asarray(np.minimum(ia,ib) if name=='mini' else np.maximum(ia,ib) if name=='maxi' else np.minimum(np.maximum(ia,ib),i[2]),dtype=np.int32).view(np.uint32)
    if name=='divi':
        x=ia.astype(np.int64);y=ib.astype(np.int64);v=np.where(y==0,0,(np.abs(x)//np.where(y==0,1,np.abs(y)))*np.where((x<0)!=(y<0),-1,1));v=np.where((x==-2147483648)&(y==-1),2147483647,v);return v.astype(np.int32).view(np.uint32)
    if name in _SVML57 or name=='rcp':
        x,y=fa,fb;bn=name
        if name=='acos':
            p=(one-fa)*(one+fa);x=np.where(p>eps,np.sqrt(p),np.float32(0));y=fa;bn='atan2'
        v=np.asarray(_backend57(bn,x.tolist(),y.tolist() if y is not None else None,backend,shim),dtype=np.float32)
        if name=='rcp':v=np.where(np.abs(fa)>eps,v,np.float32(0))
        if name=='pow':v=np.where(fa>eps,v,np.float32(0))
        if name=='fmod':v=np.where(np.abs(fb)<=eps,np.float32(0),v)
    elif name=='random':v=np.asarray(_rng57_values(rng,n),dtype=np.float32)*fa
    elif name in ('add','sub','mul'):v={'add':lambda:fa+fb,'sub':lambda:fa-fb,'mul':lambda:fa*fb}[name]()
    elif name=='div':v=np.where(np.abs(fb)>eps,fa/fb,np.float32(0))
    elif name=='neg':v=np.float32(0)-fa
    elif name in ('min','max'):v=np.where(fa<fb,fa,fb) if name=='min' else np.where(fa>fb,fa,fb)
    elif name=='clamp':
        t=np.where(fa>fb,fa,fb);v=np.where(t<fc,t,fc)
    elif name in ('sqrt','rsq'):v=np.where(fa>eps,np.sqrt(fa) if name=='sqrt' else one/np.sqrt(fa),np.float32(0))
    elif name in ('floor','ceil','trunc','round'):v={'floor':np.floor,'ceil':np.ceil,'trunc':np.trunc,'round':np.rint}[name](fa)
    elif name=='frac':v=fa-np.trunc(fa)
    elif name=='sign':v=np.where(fa>=0,np.float32(1),np.float32(-1))
    elif name in ('mad','lerp'):
        x,y,z=(fa,fb,fc) if name=='mad' else (fb,fc,fa*(one-fc))
        v=np.asarray([_fma57(float(a),float(b),float(c)) for a,b,c in zip(x,y,z)],dtype=np.float32) if fma=='fused' else x*y+z
    else:raise ExecutionError('unimplemented operation '+name)
    return np.asarray(v,dtype=np.float32).view(np.uint32)

def execute_np(bytecode=None, *, program=None, num_instances, constant_tables=(), datasets=None,
             initial_registers=None, start_instance=0, external_functions=(), profile='pioneer-2.1.0',
             math_backend='reference', svml_shim_path=None, fma_mode='unfused', external_handler=None,
             id_states=None, rng_seed=None, max_instances=4_000_000, max_instructions=1_000_000):
    def bounded_int(value, minimum, maximum, label):
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ExecutionError(f'invalid {label}: {value!r}')
        return value

    bounded_int(num_instances, 0, max_instances, 'num_instances')
    bounded_int(start_instance, 0, 0x7fffffff - num_instances, 'start_instance')
    max_storage_words=10**12
    if len(bytecode or b'') > max_storage_words * 4:
        raise ExecutionError('bytecode storage budget exceeded')
    if program is None: program = disassemble(bytecode, external_functions, profile=profile)
    if len(program.instructions) > max_instructions:
        raise ExecutionError('instruction/lane work budget exceeded')
    for ins in program.instructions:
        if ins.name not in SUPPORTED_OPS_57:
            raise ExecutionError(f'byte {ins.offset}: unsupported operation {ins.name}')

    if math_backend not in ('reference', 'svml_shim') or fma_mode not in ('unfused', 'fused'):
        raise ExecutionError('invalid math backend or fma mode')
    if math_backend == 'svml_shim' and svml_shim_path is None:
        raise ExecutionError('svml_shim requires svml_shim_path')
    names = {i.name for i in program.instructions}
    if 'random' in names and rng_seed is None:
        raise ExecutionError('random requires rng_seed')
    if 'external_func_call' in names:
        if external_handler != 'gate_v_stub' and not callable(external_handler):
            raise ExecutionError('external_func_call requires gate_v_stub or a handler')
        if not isinstance(external_functions, list):
            raise ExecutionError('external_functions must be a list of signature dicts')
    for ins in program.instructions:
        if ins.name in ('acquire_id', 'update_id') and ins.operands[0].value not in (id_states or {}):
            raise ExecutionError('ID operation requires id_states')
    working_ids = {d: IdState(list(s.id_table), list(s.free_ids), s.num_free_ids,
        s.free_capacity, s.max_used_id, s.num_spawned_ids, list(s.spawned_ids), s.id_acquire_tag)
        for d, s in (id_states or {}).items()}
    taints, output_taints, dataset_taints, ext_calls = {}, {}, {}, []
    rng = rng_seed_state(*rng_seed) if rng_seed is not None else None
    tables = tuple(bytes(table) for table in constant_tables)
    if sum(map(len, tables)) > max_storage_words * 4:
        raise ExecutionError('constant storage budget exceeded')
    datasets = dict(datasets or {})
    registers = {}
    outputs = {}
    written = {}
    counters = {}
    storage = 0

    def allocate(words):
        nonlocal storage
        storage += words
        if storage > max_storage_words:
            raise ExecutionError('register/output storage budget exceeded')

    for index, ds in datasets.items():
        bounded_int(index, 0, 65535, 'dataset index')
        bounded_int(ds.instance_offset, 0, max_storage_words, 'instance offset')
        bounded_int(ds.access_index, -1, 0x7ffffffe, 'access index')
        counters[index] = ds.access_index
        for key, capacity in ds.output_capacities.items():
            if not isinstance(key, tuple) or len(key) != 2 or key[0] not in ('float', 'int32'):
                raise ExecutionError('invalid output attribute key')
            bounded_int(key[1], 0, 65535, 'output attribute')
            bounded_int(capacity, 0, max_storage_words, 'output capacity')
            allocate(capacity)
            outputs[index, key] = np.full(capacity,0xdeadbeef,dtype=np.uint32)
            written[index,key] = np.zeros(capacity,dtype=bool)
    for index, values in (initial_registers or {}).items():
        bounded_int(index, 0, 65535, 'register index')
        if len(values) != num_instances:
            raise ExecutionError('initial register lane count mismatch')
        allocate(num_instances)
        registers[index] = np.array([bounded_int(int(v), 0, 0xffffffff, 'raw register word') for v in values],dtype=np.uint32)

    def store(index, values, taint=frozenset()):
        if index not in registers:
            allocate(num_instances)
        if isinstance(values, np.ndarray) and values.ndim == 1 and values.dtype.kind in 'biu':
            registers[index] = (values.astype(np.int64) & 0xffffffff).astype(np.uint32)
        else:
            registers[index] = np.asarray([int(v) & 0xffffffff for v in values],dtype=np.uint32)
        taints[index] = frozenset(taint)

    def source(operand):
        if operand.kind == 'constant':
            offset = operand.value
            for table in tables:
                if offset < len(table):
                    if offset + 4 > len(table):
                        raise ExecutionError('constant crosses table boundary')
                    word = struct.unpack_from('<I', table, offset)[0]
                    return np.full(num_instances,word,dtype=np.uint32)
                offset -= len(table)
            raise ExecutionError(f'missing constant byte offset {operand.value}')
        if operand.value not in registers:
            raise ExecutionError(f'uninitialized register {operand.value}')
        return registers[operand.value]

    def source_taint(operand):
        return taints.get(operand.value, frozenset()) if operand.kind != 'constant' else frozenset()

    for ins in program.instructions:
        name = ins.name
        op = ins.operands
        if name == 'done':
            break
        if name in ('enter_stat_scope', 'exit_stat_scope'):
            continue  # Profiling only; no simulation state.
        try:
            if name == 'exec_index':
                store(op[0].value, np.arange(start_instance, start_instance + num_instances, dtype=np.int64))
            elif name.startswith('inputdata_'):
                ds = datasets[op[0].value]
                kind = 'int32' if name.endswith('int32') else 'float'
                values = ds.inputs[kind, op[1].value]
                noadvance = 'noadvance' in name
                if isinstance(values, np.ndarray) and values.ndim == 1 and values.dtype == np.uint32 and ds.instance_offset + (1 if noadvance else num_instances) <= len(values):
                    selected = np.full(num_instances, values[ds.instance_offset], dtype=np.uint32) if noadvance else values[ds.instance_offset:ds.instance_offset + num_instances]
                else:
                    selected = [values[ds.instance_offset + (0 if noadvance else lane)] for lane in range(num_instances)]
                store(op[2].value, selected)
            elif name == 'acquireindex':
                index = op[0].value
                if index not in counters:
                    raise ExecutionError(f'missing dataset {index}')
                keep = (source(op[1]) & 0x80000000) != 0
                kept = int(np.count_nonzero(keep))
                if kept and counters[index] + kept - 1 >= 0x7ffffffe:
                    raise ExecutionError('dataset counter overflow')
                acquired = np.where(keep, counters[index] + np.cumsum(keep, dtype=np.int64), 0xffffffff)
                counters[index] += kept
                condition_taint = source_taint(op[1])
                store(op[2].value, acquired, condition_taint)
                dataset_taints[index] = dataset_taints.get(index, frozenset()) | condition_taint
            elif name.startswith('outputdata_'):
                index = op[0].value
                ds = datasets[index]
                kind = 'int32' if name.endswith('int32') else 'float'
                target = outputs[index, (kind, op[3].value)]
                indices = source(op[1])
                values = source(op[2])
                dest = indices.view(np.int32).astype(np.int64)
                live = dest != -1
                dest = dest[live] + ds.instance_offset
                if dest.size and (int(dest.min()) < ds.instance_offset or int(dest.max()) >= len(target)):
                    raise ExecutionError('output index outside provided capacity')
                rows, last = np.unique(dest[::-1], return_index=True)
                target[rows] = values[live][dest.size - 1 - last]
                written[index,(kind,op[3].value)][rows] = True
                key = (index, (kind, op[3].value))
                output_taints[key] = output_taints.get(key, frozenset()) | source_taint(op[1]) | source_taint(op[2])
            elif name == 'acquire_id':
                d, dest, tag = (o.value for o in op)
                state = working_ids[d]
                if state.num_free_ids < num_instances:
                    raise ExecutionError('insufficient free IDs')
                acquired = [state.free_ids[state.num_free_ids - 1 - i] for i in range(num_instances)]
                state.num_free_ids -= num_instances
                state.free_ids = state.free_ids[:state.num_free_ids]
                state.num_spawned_ids += num_instances
                state.spawned_ids = acquired + state.spawned_ids
                store(dest, acquired, dataset_taints.get(d, frozenset()))
                store(tag, [state.id_acquire_tag] * num_instances, dataset_taints.get(d, frozenset()))
            elif name == 'update_id':
                d, id_reg, index_reg = (o.value for o in op)
                state = working_ids[d]
                ids, indices = registers[id_reg], registers[index_reg]
                surviving = [i for i, w in enumerate(indices) if w != 0xffffffff]
                offset = datasets[d].instance_offset if d in datasets else 0
                for i in surviving:
                    state.id_table[int(ids[i])] = word_int(int(indices[i])) + offset
                dead = [int(ids[i]) for i, w in enumerate(indices) if w == 0xffffffff]
                state.free_ids += dead
                state.num_free_ids += len(dead)
                if surviving:
                    state.max_used_id = max(state.max_used_id, max(word_int(int(w)) for w in ids[:len(surviving)]))
                dataset_taints[d] = dataset_taints.get(d, frozenset()) | taints.get(id_reg, frozenset()) | taints.get(index_reg, frozenset())
            elif name == 'external_func_call':
                fn = op[0].value
                signature = external_functions[fn]
                ni, no = len(signature['InputParamLocations']), signature['NumOutputs']
                args, arg_taints = [], []
                for operand in op[1:1 + ni]:
                    w = operand.value
                    if w == 0xffff:
                        raise ExecutionError('invalid external input operand')
                    arg = Operand('register', w & 0x7fff) if w & 0x8000 else Operand('constant', w)
                    args.append(source(arg)); arg_taints.append(source_taint(arg))
                destinations = [o.value for o in op[1 + ni:]]
                if any(w != 0xffff and w & 0x8000 for w in destinations):
                    raise ExecutionError('invalid external output operand')
                if callable(external_handler):
                    outs = external_handler(fn, [np.asarray(values, dtype=np.uint32) for values in args], num_instances, start_instance)
                    if len(outs) != no or any(len(values) != num_instances for values in outs):
                        raise ExecutionError('external handler output shape')
                    combined = frozenset().union(*arg_taints)
                    for dest, values in zip(destinations, outs):
                        if dest != 0xffff: store(dest, np.asarray(values, dtype=np.uint32), combined)
                    ext_calls.append({'fn': fn, 'start': start_instance, 'count': num_instances,
                        'input_taints': [sorted(t) for t in arg_taints]})
                    continue
                result = [[] for _ in range(no)]; inputs = []
                for lane in range(num_instances):
                    h = (0x811c9dc5 ^ fn) & 0xffffffff
                    for values in args:
                        w = int(values[lane]); inputs.append(w); h = ((h ^ w) * 0x01000193) & 0xffffffff
                    for out_index in range(no):
                        hk = ((h ^ ((0x9e3779b9 + out_index) & 0xffffffff)) * 0x01000193) & 0xffffffff
                        result[out_index].append(float_word(word_float((hk >> 9) | 0x3f800000) - 1.0))
                combined = frozenset().union(*arg_taints)
                for dest, values in zip(destinations, result):
                    if dest != 0xffff: store(dest, values, combined)
                ext_calls.append({'fn': fn, 'start': start_instance, 'count': num_instances,
                    'inputs': inputs, 'input_taints': [sorted(t) for t in arg_taints]})
            else:
                sources = [source(o) for o in op[:-1]]
                own = ({'rng'} if name == 'random' else {'svml'} if name in _SVML57 else
                    {'hwest'} if name == 'rcp' else {'fma'} if name in ('mad', 'lerp') else set())
                result_taint = frozenset(own).union(*(source_taint(o) for o in op[:-1]))
                with np.errstate(all='ignore'):
                    result = _elementwise(name,sources,math_backend,svml_shim_path,fma_mode,rng,num_instances)
                store(op[-1].value, result, result_taint)
        except (KeyError, IndexError, TypeError, OverflowError) as exc:
            raise ExecutionError(f'byte {ins.offset} ({name}): invalid or missing execution input: {exc}') from exc
    return NpExecutionResult(registers,outputs,written,counters,len(program.instructions),taints,output_taints,dataset_taints,working_ids,ext_calls)
