"""Bounded scalar evaluator for a deliberately small legacy VectorVM subset.

Semantics checked against UE 5.3.2 VectorVM.cpp / VectorVMLegacy.h. This is
not a Niagara scheduler or a validated replacement for Unreal execution.
Registers are raw 32-bit words (float/int views share bits); constants are byte
offsets across ordered constant tables. Float arithmetic rounds to binary32,
but SIMD/FMA platform equivalence is not claimed. Random/noise, external
interfaces, persistent IDs, GPU programs and unimplemented math fail closed.
No Blender dependency, implicit parameters, fabricated motion, or I/O.
"""
from dataclasses import dataclass, field
import math
import struct

try:
    from .niagara_vm import disassemble, Operand
except ImportError:
    from niagara_vm import disassemble, Operand


class ExecutionError(ValueError):
    pass


def float_word(value):
    """Encode a binary32 float as an unsigned register word."""
    try:
        return struct.unpack('<I', struct.pack('<f', value))[0]
    except OverflowError:
        return 0xff800000 if value < 0 else 0x7f800000


def word_float(value):
    return struct.unpack('<f', struct.pack('<I', value & 0xffffffff))[0]


def word_int(value):
    value &= 0xffffffff
    return value - 0x100000000 if value & 0x80000000 else value


@dataclass
class Dataset:
    """Typed attributes indexed within each type, not a flattened register list.

    inputs: {('float'|'int32', attribute): sequence of numeric values}.
    output_capacities: same keys with explicit maximum output lengths.
    instance_offset applies to both input reads and output writes, like UE's
    FDataSetMeta. access_index is the last acquired index (initially -1).
    Half storage is intentionally unsupported until engine comparison exists.
    """
    inputs: dict = field(default_factory=dict)
    output_capacities: dict = field(default_factory=dict)
    instance_offset: int = 0
    access_index: int = -1


@dataclass(frozen=True)
class ExecutionResult:
    registers: dict
    outputs: dict
    access_indices: dict
    instruction_count: int
    taints: dict = field(default_factory=dict)
    output_taints: dict = field(default_factory=dict)
    dataset_taints: dict = field(default_factory=dict)
    id_states: dict = field(default_factory=dict)
    ext_calls: list = field(default_factory=list)


_FLOAT = set('add sub mul div mad lerp neg abs clamp min max sqrt fmod'.split())
_INT = set('addi subi muli divi clampi mini maxi absi negi'.split())
_COMPARE = set('cmplt cmple cmpgt cmpge cmpeq cmpneq cmplti cmplei cmpgti cmpgei cmpeqi cmpneqi'.split())
_BITS = set('bit_and bit_or bit_xor bit_not logic_and logic_or logic_xor logic_not'.split())
_CONVERT = set('i2f f2i f2b b2f i2b b2i fasi iasf'.split())
SUPPORTED_OPS = frozenset(_FLOAT | _INT | _COMPARE | _BITS | _CONVERT | {
    'done', 'select', 'inputdata_float', 'inputdata_int32',
    'inputdata_noadvance_float', 'inputdata_noadvance_int32',
    'outputdata_float', 'outputdata_int32', 'acquireindex', 'exec_index',
    'enter_stat_scope', 'exit_stat_scope'})


def execute(bytecode, *, num_instances, constant_tables=(), datasets=None,
            initial_registers=None, start_instance=0, external_functions=(),
            profile='ue5.3-legacy', max_instances=65536,
            max_instructions=100000, max_lane_operations=2000000,
            max_storage_words=2000000, x86_semantics=False,
            math_backend="reference", svml_shim_path=None, fma_mode="unfused",
            external_handler=None, id_states=None, rng_seed=None):
    """Execute explicit caller-provided context, returning new private buffers.

    Unsupported instructions anywhere reject the entire program before it runs.
    initial_registers contains raw uint32 lanes, never implicit float values.
    Missing constants, attributes/registers and invalid indices are errors.
    Input objects are never modified, including when execution fails.
    Bounds limit lanes, instructions, work, and combined register/output storage.
    """
    if x86_semantics:
        return _execute57(bytecode, num_instances=num_instances, constant_tables=constant_tables,
            datasets=datasets, initial_registers=initial_registers, start_instance=start_instance,
            external_functions=external_functions, profile=profile, max_instances=max_instances,
            max_instructions=max_instructions, max_lane_operations=max_lane_operations,
            max_storage_words=max_storage_words, math_backend=math_backend,
            svml_shim_path=svml_shim_path, fma_mode=fma_mode,
            external_handler=external_handler, id_states=id_states, rng_seed=rng_seed)
    if (math_backend != 'reference' or svml_shim_path is not None or fma_mode != 'unfused'
            or external_handler is not None or id_states is not None or rng_seed is not None):
        raise ExecutionError('requires x86_semantics')

    def bounded_int(value, minimum, maximum, label):
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ExecutionError(f'invalid {label}: {value!r}')
        return value

    bounded_int(num_instances, 0, max_instances, 'num_instances')
    bounded_int(start_instance, 0, 0x7fffffff - num_instances, 'start_instance')
    if len(bytecode) > max_storage_words * 4:
        raise ExecutionError('bytecode storage budget exceeded')
    program = disassemble(bytecode, external_functions, profile=profile)
    if len(program.instructions) > max_instructions or len(program.instructions) * num_instances > max_lane_operations:
        raise ExecutionError('instruction/lane work budget exceeded')
    for ins in program.instructions:
        if ins.name not in SUPPORTED_OPS:
            raise ExecutionError(f'byte {ins.offset}: unsupported operation {ins.name}')

    tables = tuple(bytes(table) for table in constant_tables)
    if sum(map(len, tables)) > max_storage_words * 4:
        raise ExecutionError('constant storage budget exceeded')
    datasets = dict(datasets or {})
    registers = {}
    outputs = {}
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
            outputs[index, key] = [None] * capacity
    for index, values in (initial_registers or {}).items():
        bounded_int(index, 0, 65535, 'register index')
        if len(values) != num_instances:
            raise ExecutionError('initial register lane count mismatch')
        allocate(num_instances)
        registers[index] = [bounded_int(v, 0, 0xffffffff, 'raw register word') for v in values]

    def store(index, values):
        if index not in registers:
            allocate(num_instances)
        registers[index] = [v & 0xffffffff for v in values]

    def source(operand):
        if operand.kind == 'constant':
            offset = operand.value
            for table in tables:
                if offset < len(table):
                    if offset + 4 > len(table):
                        raise ExecutionError('constant crosses table boundary')
                    word = struct.unpack_from('<I', table, offset)[0]
                    return [word] * num_instances
                offset -= len(table)
            raise ExecutionError(f'missing constant byte offset {operand.value}')
        if operand.value not in registers:
            raise ExecutionError(f'uninitialized register {operand.value}')
        return registers[operand.value]

    for ins in program.instructions:
        name = ins.name
        op = ins.operands
        if name == 'done':
            break
        if name in ('enter_stat_scope', 'exit_stat_scope'):
            continue  # Profiling only; no simulation state.
        try:
            if name == 'exec_index':
                store(op[0].value, range(start_instance, start_instance + num_instances))
            elif name.startswith('inputdata_'):
                ds = datasets[op[0].value]
                kind = 'int32' if name.endswith('int32') else 'float'
                values = ds.inputs[kind, op[1].value]
                noadvance = 'noadvance' in name
                selected = [values[ds.instance_offset + (0 if noadvance else lane)] for lane in range(num_instances)]
                store(op[2].value, [float_word(v) if kind == 'float' else int(v) for v in selected])
            elif name == 'acquireindex':
                index = op[0].value
                if index not in counters:
                    raise ExecutionError(f'missing dataset {index}')
                acquired = []
                for valid in source(op[1]):
                    if valid:
                        if counters[index] >= 0x7ffffffe:
                            raise ExecutionError('dataset counter overflow')
                        counters[index] += 1
                        acquired.append(counters[index])
                    else:
                        acquired.append(0xffffffff)
                store(op[2].value, acquired)
            elif name.startswith('outputdata_'):
                index = op[0].value
                ds = datasets[index]
                kind = 'int32' if name.endswith('int32') else 'float'
                target = outputs[index, (kind, op[3].value)]
                indices = source(op[1])
                values = source(op[2])
                for lane, word in enumerate(values):
                    dest = word_int(indices[lane])
                    if dest == -1:
                        continue
                    dest += ds.instance_offset
                    if dest < ds.instance_offset or dest >= len(target):
                        raise ExecutionError('output index outside provided capacity')
                    target[dest] = word_float(word) if kind == 'float' else word_int(word)
            else:
                sources = [source(o) for o in op[:-1]]
                result = []
                for lane in range(num_instances):
                    words = [s[lane] for s in sources]
                    if name == 'select':
                        mask, a, b = words
                        result.append((mask & a) | ((~mask & 0xffffffff) & b))
                        continue
                    if name in ('fasi', 'iasf'):
                        result.append(words[0])
                        continue
                    if name == 'i2f':
                        result.append(float_word(word_int(words[0])))
                        continue
                    if name in ('b2i', 'b2f'):
                        result.append(words[0] & (1 if name == 'b2i' else float_word(1.0)))
                        continue
                    if name in ('f2i', 'f2b', 'i2b'):
                        value = word_int(words[0]) if name == 'i2b' else word_float(words[0])
                        if not math.isfinite(value):
                            raise ExecutionError('nonfinite conversion is outside verified subset')
                        if name == 'f2i':
                            if not -2147483648 <= value < 2147483648:
                                raise ExecutionError('out-of-range f2i is outside verified subset')
                            result.append(math.trunc(value) & 0xffffffff)
                        else:
                            result.append(0xffffffff if value > 0 else 0)
                        continue
                    is_int = name in _INT or name in _BITS or (name in _COMPARE and name.endswith('i'))
                    values = [word_int(w) if is_int else word_float(w) for w in words]
                    if name in _COMPARE:
                        base = name[:-1] if is_int else name
                        a, b = values
                        truth = {'cmplt': a < b, 'cmple': a <= b, 'cmpgt': a > b,
                                 'cmpge': a >= b, 'cmpeq': a == b, 'cmpneq': a != b}[base]
                        result.append(0xffffffff if truth else 0)
                        continue
                    if not is_int and not all(math.isfinite(v) for v in values):
                        raise ExecutionError('nonfinite arithmetic is outside verified subset')
                    base = name[:-1] if name in _INT else name
                    a = values[0]
                    b = values[1] if len(values) > 1 else None
                    c = values[2] if len(values) > 2 else None
                    if base == 'add': value = a + b
                    elif base == 'sub': value = a - b
                    elif base == 'mul': value = a * b
                    elif base == 'div':
                        if is_int:
                            value = 0 if b == 0 else (0x7fffffff if a == -0x80000000 and b == -1 else (abs(a) // abs(b)) * (-1 if (a < 0) != (b < 0) else 1))
                        else:
                            value = a / b if abs(b) > word_float(float_word(1e-8)) else 0.0
                    elif base == 'mad': value = word_float(float_word(a * b)) + c
                    elif base == 'lerp': value = word_float(float_word(a * word_float(float_word(1.0 - c)))) + word_float(float_word(b * c))
                    elif base == 'neg': value = -a
                    elif base == 'abs': value = abs(a)
                    elif base == 'min': value = min(a, b)
                    elif base == 'max': value = max(a, b)
                    elif base == 'clamp': value = min(max(a, b), c)
                    elif base == 'sqrt': value = math.sqrt(a) if a > word_float(float_word(1e-8)) else 0.0
                    elif base == 'fmod': value = math.fmod(a, b) if abs(b) > word_float(float_word(1e-8)) else 0.0
                    elif name in ('bit_and', 'logic_and'): value = words[0] & words[1]
                    elif name in ('bit_or', 'logic_or'): value = words[0] | words[1]
                    elif name in ('bit_xor', 'logic_xor'): value = words[0] ^ words[1]
                    elif name in ('bit_not', 'logic_not'): value = ~words[0]
                    else: raise ExecutionError(f'unimplemented operation {name}')
                    result.append(value & 0xffffffff if is_int else float_word(value))
                store(op[-1].value, result)
        except (KeyError, IndexError, TypeError, OverflowError) as exc:
            raise ExecutionError(f'byte {ins.offset} ({name}): invalid or missing execution input: {exc}') from exc
    return ExecutionResult({k: tuple(v) for k, v in registers.items()},
                           {k: tuple(v) for k, v in outputs.items()},
                           counters, len(program.instructions))



@dataclass
class IdState:
    id_table: list
    free_ids: list
    num_free_ids: int
    free_capacity: int
    max_used_id: int
    num_spawned_ids: int
    spawned_ids: list
    id_acquire_tag: int


SUPPORTED_OPS_57 = SUPPORTED_OPS | frozenset('rcp rsq sin cos tan acos atan2 pow floor ceil trunc frac round sign bit_lshift bit_rshift random acquire_id update_id external_func_call'.split())
_SVML57 = frozenset('sin cos tan acos atan2 pow fmod'.split())
_EPS57 = word_float(float_word(1e-8))
_SVML_CACHE = {}


def _f32(value):
    return word_float(float_word(value))


def _fma57(a, b, c):
    try: return _f32(math.fma(a, b, c))
    except ValueError: return float('nan')


def _round57(name, value):
    if not math.isfinite(value): return value
    result = float(round(value) if name == 'round' else getattr(math, name)(value))
    return math.copysign(0.0, value) if result == 0.0 else result


def _backend57(name, a, b, backend, path):
    if backend == 'reference':
        result = []
        for i, x in enumerate(a):
            try:
                y = (1.0 / x if x else 0.0) if name == 'rcp' else getattr(math, name)(x, b[i]) if b is not None else getattr(math, name)(x)
            except ValueError: y = float('nan')
            except OverflowError: y = float('inf')
            result.append(_f32(y))
        return result
    import ctypes
    import numpy as np
    key = str(path)
    if key not in _SVML_CACHE:
        dll = ctypes.CDLL(key)
        if dll.dr_shim_version() != 1: raise ExecutionError('invalid SVML shim version')
        _SVML_CACHE[key] = dll
    dll = _SVML_CACHE[key]
    p = ctypes.POINTER(ctypes.c_float)
    aa = np.asarray(a, dtype=np.float32); out = np.empty(len(a), dtype=np.float32)
    fn = getattr(dll, 'dr_' + name); fn.restype = None
    if b is None:
        fn.argtypes = [p, p, ctypes.c_int]
        fn(aa.ctypes.data_as(p), out.ctypes.data_as(p), len(a))
    else:
        bb = np.asarray(b, dtype=np.float32); fn.argtypes = [p, p, p, ctypes.c_int]
        fn(aa.ctypes.data_as(p), bb.ctypes.data_as(p), out.ctypes.data_as(p), len(a))
    return [float(v) for v in out]


def rng_seed_state(pcg_state, pcg_inc):
    state, inc = pcg_state & 0xffffffffffffffff, pcg_inc & 0xffffffffffffffff
    s = []
    for i in range(5):
        row = []
        for j in range(4):
            old = state; state = (old * 6364136223846793005 + inc) & 0xffffffffffffffff
            xs = (((old >> 18) ^ old) >> 27) & 0xffffffff; rot = old >> 59
            row.append(((xs >> rot) | (xs << ((-rot) & 31))) & 0xffffffff)
        s.append(row)
    return s, [inc & 0xffffffff, (inc >> 32) & 0xffffffff, state & 0xffffffff, (state >> 32) & 0xffffffff]


def _rng57_values(rng, n):
    s, c = rng; values = []
    for _ in range((n + 3) // 4):
        t, first = s[4][:], s[0][:]
        s[4], s[3], s[2], s[1] = s[3], s[2], s[1], first
        for j in range(4):
            t[j] ^= t[j] >> 2; t[j] ^= (t[j] << 1) & 0xffffffff; t[j] ^= (first[j] << 4) & 0xffffffff
            c[j] = (c[j] + 362437) & 0xffffffff
            values.append(word_float((((t[j] + c[j]) & 0xffffffff) >> 9) | 0x3f800000) - 1.0)
        s[0] = t
    return values[:n]


def _execute57(bytecode, *, num_instances, constant_tables=(), datasets=None,
            initial_registers=None, start_instance=0, external_functions=(),
            profile='ue5.3-legacy', max_instances=65536,
            max_instructions=100000, max_lane_operations=2000000,
            max_storage_words=2000000, math_backend="reference", svml_shim_path=None,
            fma_mode="unfused", external_handler=None, id_states=None, rng_seed=None):
    """Execute explicit caller-provided context, returning new private buffers.

    Unsupported instructions anywhere reject the entire program before it runs.
    initial_registers contains raw uint32 lanes, never implicit float values.
    Missing constants, attributes/registers and invalid indices are errors.
    Input objects are never modified, including when execution fails.
    Bounds limit lanes, instructions, work, and combined register/output storage.
    """
    def bounded_int(value, minimum, maximum, label):
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ExecutionError(f'invalid {label}: {value!r}')
        return value

    bounded_int(num_instances, 0, max_instances, 'num_instances')
    bounded_int(start_instance, 0, 0x7fffffff - num_instances, 'start_instance')
    if len(bytecode) > max_storage_words * 4:
        raise ExecutionError('bytecode storage budget exceeded')
    program = disassemble(bytecode, external_functions, profile=profile)
    if len(program.instructions) > max_instructions or len(program.instructions) * num_instances > max_lane_operations:
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
            outputs[index, key] = [None] * capacity
    for index, values in (initial_registers or {}).items():
        bounded_int(index, 0, 65535, 'register index')
        if len(values) != num_instances:
            raise ExecutionError('initial register lane count mismatch')
        allocate(num_instances)
        registers[index] = [bounded_int(v, 0, 0xffffffff, 'raw register word') for v in values]

    def store(index, values, taint=frozenset()):
        if index not in registers:
            allocate(num_instances)
        registers[index] = [v & 0xffffffff for v in values]
        taints[index] = frozenset(taint)

    def source(operand):
        if operand.kind == 'constant':
            offset = operand.value
            for table in tables:
                if offset < len(table):
                    if offset + 4 > len(table):
                        raise ExecutionError('constant crosses table boundary')
                    word = struct.unpack_from('<I', table, offset)[0]
                    return [word] * num_instances
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
                store(op[0].value, range(start_instance, start_instance + num_instances))
            elif name.startswith('inputdata_'):
                ds = datasets[op[0].value]
                kind = 'int32' if name.endswith('int32') else 'float'
                values = ds.inputs[kind, op[1].value]
                noadvance = 'noadvance' in name
                selected = [values[ds.instance_offset + (0 if noadvance else lane)] for lane in range(num_instances)]
                store(op[2].value, [float_word(v) if kind == 'float' else int(v) for v in selected])
            elif name == 'acquireindex':
                index = op[0].value
                if index not in counters:
                    raise ExecutionError(f'missing dataset {index}')
                acquired = []
                for valid in source(op[1]):
                    if valid & 0x80000000:
                        if counters[index] >= 0x7ffffffe:
                            raise ExecutionError('dataset counter overflow')
                        counters[index] += 1
                        acquired.append(counters[index])
                    else:
                        acquired.append(0xffffffff)
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
                for lane, word in enumerate(values):
                    dest = word_int(indices[lane])
                    if dest == -1:
                        continue
                    dest += ds.instance_offset
                    if dest < ds.instance_offset or dest >= len(target):
                        raise ExecutionError('output index outside provided capacity')
                    target[dest] = word_float(word) if kind == 'float' else word_int(word)
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
                    state.id_table[ids[i]] = word_int(indices[i]) + offset
                dead = [ids[i] for i, w in enumerate(indices) if w == 0xffffffff]
                state.free_ids += dead
                state.num_free_ids += len(dead)
                if surviving:
                    state.max_used_id = max(state.max_used_id, max(word_int(w) for w in ids[:len(surviving)]))
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
                    import numpy as np
                    outs = external_handler(fn, [np.asarray([v & 0xffffffff for v in values], dtype=np.uint32) for values in args], num_instances, start_instance)
                    if len(outs) != no or any(len(values) != num_instances for values in outs):
                        raise ExecutionError('external handler output shape')
                    combined = frozenset().union(*arg_taints)
                    for dest, values in zip(destinations, outs):
                        if dest != 0xffff: store(dest, [int(v) & 0xffffffff for v in values], combined)
                    ext_calls.append({'fn': fn, 'start': start_instance, 'count': num_instances,
                        'input_taints': [sorted(t) for t in arg_taints]})
                    continue
                result = [[] for _ in range(no)]; inputs = []
                for lane in range(num_instances):
                    h = (0x811c9dc5 ^ fn) & 0xffffffff
                    for values in args:
                        w = values[lane]; inputs.append(w); h = ((h ^ w) * 0x01000193) & 0xffffffff
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
                backend_values = None
                if name in _SVML57 or name == 'rcp':
                    values_a = [word_float(w) for w in sources[0]]
                    values_b = [word_float(w) for w in sources[1]] if len(sources) > 1 else None
                    backend_name = name
                    if name == 'acos':
                        values_b = values_a
                        values_a = [_f32(math.sqrt(p)) if p > _EPS57 else 0.0 for p in
                            [_f32(_f32(1 - a) * _f32(1 + a)) for a in values_a]]
                        backend_name = 'atan2'
                    backend_values = _backend57(backend_name, values_a, values_b, math_backend, svml_shim_path)
                random_values = _rng57_values(rng, num_instances) if name == 'random' else None
                result = []
                for lane in range(num_instances):
                    words = [s[lane] for s in sources]
                    if name == 'select':
                        mask, a, b = words
                        result.append((mask & a) | ((~mask & 0xffffffff) & b))
                        continue
                    if name in ('fasi', 'iasf'):
                        result.append(words[0])
                        continue
                    if name == 'i2f':
                        result.append(float_word(word_int(words[0])))
                        continue
                    if name in ('b2i', 'b2f'):
                        result.append(words[0] & (1 if name == 'b2i' else float_word(1.0)))
                        continue
                    if name == 'abs':
                        result.append(words[0] & 0x7fffffff)
                        continue
                    if name in ('f2i', 'f2b', 'i2b'):
                        value = word_int(words[0]) if name == 'i2b' else word_float(words[0])
                        if name == 'f2i':
                            result.append(0x80000000 if not math.isfinite(value) or not -2147483648 <= value < 2147483648 else math.trunc(value) & 0xffffffff)
                        else:
                            result.append(0xffffffff if value > 0 else 0)
                        continue
                    is_int = name in _INT or name in _BITS or name in ('bit_lshift', 'bit_rshift') or (name in _COMPARE and name.endswith('i'))
                    values = [word_int(w) if is_int else word_float(w) for w in words]
                    if name in _COMPARE:
                        base = name[:-1] if is_int else name
                        a, b = values
                        truth = {'cmplt': a < b, 'cmple': a <= b, 'cmpgt': a > b,
                                 'cmpge': a >= b, 'cmpeq': a == b, 'cmpneq': a != b}[base]
                        result.append(0xffffffff if truth else 0)
                        continue
                    base = name[:-1] if name in _INT else name
                    a = values[0]
                    b = values[1] if len(values) > 1 else None
                    c = values[2] if len(values) > 2 else None
                    if name in _SVML57 or name == 'rcp':
                        value = backend_values[lane]
                        if name == 'rcp' and not abs(a) > _EPS57: value = 0.0
                        if name == 'pow' and not a > _EPS57: value = 0.0
                        if name == 'fmod' and abs(b) <= _EPS57: value = 0.0
                    elif name == 'rsq': value = 1.0 / _f32(math.sqrt(a)) if a > _EPS57 else 0.0
                    elif name in ('floor', 'ceil', 'trunc', 'round'): value = _round57(name, a)
                    elif name == 'frac': value = a - _round57('trunc', a)
                    elif name == 'sign': value = 1.0 if a >= 0 else -1.0
                    elif name == 'random': value = random_values[lane] * a
                    elif name == 'bit_lshift': value = 0 if (words[1] & 0xffffffff) > 31 else (words[0] << words[1]) & 0xffffffff
                    elif name == 'bit_rshift': value = 0 if (words[1] & 0xffffffff) > 31 else (words[0] & 0xffffffff) >> words[1]
                    elif base == 'add': value = a + b
                    elif base == 'sub': value = a - b
                    elif base == 'mul': value = a * b
                    elif base == 'div':
                        if is_int:
                            value = 0 if b == 0 else (0x7fffffff if a == -0x80000000 and b == -1 else (abs(a) // abs(b)) * (-1 if (a < 0) != (b < 0) else 1))
                        else:
                            value = a / b if abs(b) > word_float(float_word(1e-8)) else 0.0
                    elif base == 'mad': value = _fma57(a, b, c) if fma_mode == 'fused' else _f32(a * b) + c
                    elif base == 'lerp':
                        t = _f32(a * _f32(1.0 - c))
                        value = _fma57(b, c, t) if fma_mode == 'fused' else _f32(b * c) + t
                    elif base == 'neg': value = -a if is_int else 0.0 - a
                    elif base == 'abs': value = abs(a)
                    elif base == 'min': value = min(a, b) if is_int else a if a < b else b
                    elif base == 'max': value = max(a, b) if is_int else a if a > b else b
                    elif base == 'clamp':
                        t = max(a, b) if is_int else a if a > b else b
                        value = min(t, c) if is_int else t if t < c else c
                    elif base == 'sqrt': value = math.sqrt(a) if a > word_float(float_word(1e-8)) else 0.0
                    elif base == 'fmod': value = math.fmod(a, b) if abs(b) > word_float(float_word(1e-8)) else 0.0
                    elif name in ('bit_and', 'logic_and'): value = words[0] & words[1]
                    elif name in ('bit_or', 'logic_or'): value = words[0] | words[1]
                    elif name in ('bit_xor', 'logic_xor'): value = words[0] ^ words[1]
                    elif name in ('bit_not', 'logic_not'): value = ~words[0]
                    else: raise ExecutionError(f'unimplemented operation {name}')
                    result.append(value & 0xffffffff if is_int else float_word(value))
                store(op[-1].value, result, result_taint)
        except (KeyError, IndexError, TypeError, OverflowError) as exc:
            raise ExecutionError(f'byte {ins.offset} ({name}): invalid or missing execution input: {exc}') from exc
    return ExecutionResult({k: tuple(v) for k, v in registers.items()},
                           {k: tuple(v) for k, v in outputs.items()},
                           counters, len(program.instructions), taints, output_taints,
                           dataset_taints, working_ids, ext_calls)

