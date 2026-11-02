"""Strict offline constant-table assembly for UE 5.3.2 Niagara layouts.

This opt-in layout profile is source-derived, not inferred from a game version.
Only actual bytecode reads must be supplied. Unread holes contain poison 0xCD;
these bytes are explicitly unavailable and may never be used by another program.
Serialized parameter values are asset defaults, not observed runtime values.
No scheduler, external-interface implementation, or live-game equivalence claim.
"""
from dataclasses import dataclass
import math
import struct

try:
    from .niagara_vm import bytecode_from_vm, disassemble
except ImportError:
    from niagara_vm import bytecode_from_vm, disassemble


class ContextError(ValueError):
    pass


# Field offsets/sizes are ABI facts from NiagaraTypes.h, UE5.3.2.
LAYOUTS = {
    'global': (32, {name: (offset, kind, 1) for name, offset, kind in [
        ('WorldDeltaTime',0,'f'), ('EngineDeltaTime',4,'f'),
        ('EngineInvDeltaTime',8,'f'), ('EngineTime',12,'f'),
        ('EngineRealTime',16,'f'), ('QualityLevel',20,'i')]}),
    'system': (64, {name: (offset, kind, 1) for name, offset, kind in [
        ('EngineTimeSinceRendered',0,'f'), ('EngineLodDistance',4,'f'),
        ('EngineLodDistanceFraction',8,'f'), ('EngineSystemAge',12,'f'),
        ('EngineExecutionState',16,'I'), ('EngineTickCount',20,'i'),
        ('EngineEmitterCount',24,'i'), ('EngineAliveEmitterCount',28,'i'),
        ('SignificanceIndex',32,'i'), ('RandomSeed',36,'i'),
        ('CurrentTimeStep',40,'i'), ('NumTimeSteps',44,'i'),
        ('TimeStepFraction',48,'f'), ('NumParticles',52,'I')]}),
    'owner': (512, dict(
        [(name,(index*64,'f',16)) for index,name in enumerate([
            'EngineLocalToWorld','EngineWorldToLocal','EngineLocalToWorldTransposed',
            'EngineWorldToLocalTransposed','EngineLocalToWorldNoScale','EngineWorldToLocalNoScale'])]
        + [('EngineRotation',(384,'f',4)), ('EnginePosition',(400,'f',3)),
           ('EngineVelocity',(416,'f',3)), ('EngineXAxis',(432,'f',3)),
           ('EngineYAxis',(448,'f',3)), ('EngineZAxis',(464,'f',3)),
           ('EngineScale',(480,'f',3)), ('EngineLWCTile',(496,'f',4))])),
    'emitter': (32, {name: (offset, kind, 1) for name, offset, kind in [
        ('EmitterNumParticles',0,'i'), ('EmitterTotalSpawnedParticles',4,'i'),
        ('EmitterSpawnCountScale',8,'f'), ('EmitterAge',12,'f'),
        ('EmitterRandomSeed',16,'i'), ('EmitterInstanceSeed',20,'i')]}),
}


@dataclass(frozen=True)
class ConstantContext:
    constant_tables: tuple
    table_manifest: tuple
    required_constants: tuple
    layout_profile: str
    bytecode_profile: str
    bytecode: bytes


def _bytes(value, label):
    if not isinstance(value, (bytes, bytearray, list, tuple)):
        raise ContextError(f'{label} must be a byte array')
    try:
        return bytes(value)
    except (ValueError, TypeError) as exc:
        raise ContextError(f'invalid {label}') from exc


def build_constant_context(script_export, *, runtime_values=None,
                           parameter_overrides=None, layout_profile,
                           bytecode_profile='ue5.3-legacy'):
    """Assemble only when every referenced constant word has known bytes.

    runtime_values names use C++ field names, e.g.
    {'emitter.EmitterTotalSpawnedParticles': 100,
     'global.EngineDeltaTime': 1/60}.
    Previous frame uses 'previous.emitter.EmitterAge', etc. All omitted fields
    remain unavailable. parameter_overrides maps serialized variable names to
    complete raw bytes; a full field extent must be supplied (no partial patch).
    Returned tables are validated only for the returned bytecode, never reusable
    blindly. Inspect provenance to distinguish serialized defaults from inputs.
    """
    if layout_profile != 'ue5.3.2':
        raise ContextError(f'unsupported constant layout profile {layout_profile}')
    props = script_export.get('Properties', {})
    vm = props.get('CachedScriptVM')
    if not isinstance(vm, dict):
        raise ContextError('missing CachedScriptVM')
    usage = props.get('Usage')
    names = {'ENiagaraScriptUsage::SystemSpawnScript':11,
             'ENiagaraScriptUsage::SystemUpdateScript':12,
             'ENiagaraScriptUsage::ParticleSpawnScript':3,
             'ENiagaraScriptUsage::ParticleSpawnScriptInterpolated':4,
             'ENiagaraScriptUsage::ParticleUpdateScript':5}
    usage = names.get(usage, usage)
    if usage not in (3,4,5,11,12):
        raise ContextError(f'unsupported script usage {usage}')
    interpolated = usage == 4
    store = props.get('ScriptExecutionParamStore')
    if not isinstance(store, dict):
        raise ContextError('missing ScriptExecutionParamStore')
    if store == {}:
        size, data = 0, b''
    else:
        size = store.get('ParameterSize')
        if not isinstance(size,int) or isinstance(size,bool) or not 0 <= size <= 1048576:
            raise ContextError('invalid/missing ParameterSize')
        data = _bytes(store.get('ParameterData'), 'ParameterData')
    expected = size * (2 if interpolated else 1)
    if len(data) != expected:
        raise ContextError(f'ParameterData size {len(data)} != expected {expected}; no padding guessed')
    parameter_data = bytearray(data)
    override_provenance = {}
    offsets = store.get('SortedParameterOffsets', [])
    for name, raw in (parameter_overrides or {}).items():
        entries = [entry for entry in offsets if entry.get('Name') == name]
        if len(entries) != 1:
            raise ContextError(f'unknown/ambiguous parameter {name}')
        start = entries[0].get('Offset')
        if not isinstance(start,int) or not 0 <= start < expected:
            raise ContextError(f'invalid parameter offset {name}')
        boundary = size if start < size else expected
        end = min([entry['Offset'] for entry in offsets
                   if isinstance(entry.get('Offset'),int) and start < entry['Offset'] <= boundary] + [boundary])
        raw = _bytes(raw, f'override {name}')
        if len(raw) != end-start:
            raise ContextError(f'override {name} must contain {end-start} bytes')
        parameter_data[start:end] = raw
        for offset in range(start,end):
            override_provenance[offset] = f'parameter override {name}'
    literals = _bytes(vm.get('ScriptLiterals'), 'ScriptLiterals')
    raw = bytecode_from_vm(vm)
    if not raw:
        raise ContextError('empty bytecode cannot establish a constant context')
    instructions = disassemble(raw, vm.get('CalledVMExternalFunctions',()), profile=bytecode_profile).instructions
    if any(ins.name == 'external_func_call' for ins in instructions):
        raise ContextError('external function constant bindings are unsupported')
    runtime_values = dict(runtime_values or {})
    consumed = set()
    tables = []
    provenance = []
    table_names = []

    def add_runtime(kind, previous=False):
        length, fields = LAYOUTS[kind]
        table = bytearray([0xcd])*length
        known = [None]*length
        prefix = ('previous.' if previous else '') + kind + '.'
        for name, (offset, fmt, count) in fields.items():
            key = prefix + name
            if key not in runtime_values:
                continue
            value = runtime_values[key]
            sequence = [value] if count == 1 else value
            try:
                if len(sequence) != count or (fmt == 'f' and not all(math.isfinite(v) for v in sequence)):
                    raise ValueError('invalid field values')
                encoded = struct.pack('<'+fmt*count,*sequence)
            except (TypeError,ValueError,struct.error,OverflowError) as exc:
                raise ContextError(f'invalid runtime field {key}') from exc
            consumed.add(key)
            table[offset:offset+len(encoded)] = encoded
            known[offset:offset+len(encoded)] = [f'explicit runtime {key}']*len(encoded)
        tables.append(bytes(table))
        provenance.append(known)
        table_names.append(prefix[:-1])

    def add_external(previous=False):
        base = size if previous else 0
        tables.append(bytes(parameter_data[base:base+size]))
        provenance.append([override_provenance.get(base+i,'serialized parameter default') for i in range(size)])
        table_names.append(('previous.' if previous else '')+'external')

    if usage in (11,12):
        add_runtime('global')
        add_external()
    else:
        for kind in ('global','system','owner','emitter'):
            add_runtime(kind)
        add_external()
        if interpolated:
            for kind in ('global','system','owner','emitter'):
                add_runtime(kind,True)
            add_external(True)
    tables.append(literals)
    provenance.append(['serialized script literal']*len(literals))
    table_names.append('literals')
    if set(runtime_values)-consumed:
        raise ContextError(f'unknown or irrelevant runtime fields: {sorted(set(runtime_values)-consumed)}')
    required = []
    for offset in sorted({o.value for ins in instructions for o in ins.operands if o.kind=='constant'}):
        local = offset
        for index,table in enumerate(tables):
            if local < len(table):
                if local+4 > len(table):
                    raise ContextError(f'constant {offset} crosses table boundary')
                origins = provenance[index][local:local+4]
                if any(origin is None for origin in origins):
                    kind = table_names[index].split('.')[-1]
                    fields = LAYOUTS.get(kind,(0,{}))[1]
                    candidates = [name for name,(field_offset,fmt,count) in fields.items()
                                  if field_offset <= local < field_offset+4*count]
                    label = '.'.join([table_names[index],candidates[0]]) if candidates else table_names[index]+'.padding-or-unknown'
                    raise ContextError(f'constant {offset} needs explicit {label} (table byte {local})')
                required.append({'constantOffset':offset,'table':table_names[index],'tableOffset':local,
                                 'provenance':tuple(dict.fromkeys(origins)),
                                 'wordHex':table[local:local+4].hex()})
                break
            local -= len(table)
        else:
            raise ContextError(f'constant {offset} exceeds assembled table sizes')
    manifest = []
    start = 0
    for name,table,origins in zip(table_names,tables,provenance):
        manifest.append({'name':name,'byteOffset':start,'byteLength':len(table),
                         'knownBytes':sum(origin is not None for origin in origins),
                         'unavailableBytes':sum(origin is None for origin in origins),
                         'unavailableFill':'0xcd, never referenced by validated bytecode'})
        start += len(table)
    return ConstantContext(tuple(tables),tuple(manifest),tuple(required),layout_profile,bytecode_profile,raw)


@dataclass(frozen=True)
class DatasetInputContext:
    inputs: dict
    provenance: tuple


def build_dataset_inputs(context, compiled_data, dataset_index, *, num_instances,
                         values=None, parameter_store=None):
    """Map required input attributes to compiled variable names, fail closed.

    Explicit ``values`` are uniform numeric scalars or component sequences,
    broadcast across instances. Parameter-store values are decoded only when
    their component byte offsets survive in compiled LayoutInfo. Supports
    uniform float/int32 variables, not mixed structs, half, or interfaces.
    No missing runtime value is filled with zero. Output is suitable for the
    evaluator's Dataset(inputs=...). Per-particle datasets should instead be
    fed the previous execution's output buffers.
    """
    if not isinstance(num_instances,int) or isinstance(num_instances,bool) or not 0 <= num_instances <= 65536:
        raise ContextError('invalid dataset instance count')
    instructions = disassemble(context.bytecode, profile=context.bytecode_profile).instructions
    needed = set()
    for ins in instructions:
        if ins.name.startswith('inputdata_') and ins.operands[0].value == dataset_index:
            kind = 'int32' if ins.name.endswith('int32') else ('half' if ins.name.endswith('half') else 'float')
            needed.add((kind,ins.operands[1].value))
    variables = compiled_data.get('Variables')
    layouts = compiled_data.get('VariableLayouts')
    if not isinstance(variables,list) or not isinstance(layouts,list) or len(variables) != len(layouts):
        raise ContextError('incomplete compiled dataset variable layouts')
    values = dict(values or {})
    store = parameter_store or {}
    data = _bytes(store.get('ParameterData',[]),'instance ParameterData')
    store_offsets = store.get('SortedParameterOffsets',[])
    inputs, provenance, consumed = {}, [], set()
    for variable,layout in zip(variables,layouts):
        name = variable.get('Name')
        info = layout.get('LayoutInfo',{})
        nf, ni, nh = (info.get(k,0) for k in ('NumFloatComponents','NumInt32Components','NumHalfComponents'))
        if not all(isinstance(v,int) and not isinstance(v,bool) and 0 <= v <= 64 for v in (nf,ni,nh)):
            raise ContextError(f'invalid component counts for {name}')
        float_start, int_start, half_start = (layout.get(k,0) for k in ('FloatComponentStart','Int32ComponentStart','HalfComponentStart'))
        if not all(isinstance(v,int) and not isinstance(v,bool) and 0 <= v <= 65535 for v in (float_start,int_start,half_start)):
            raise ContextError(f'invalid component starts for {name}')
        keys = ([('float',float_start+i) for i in range(nf)] + [('int32',int_start+i) for i in range(ni)]
                + [('half',half_start+i) for i in range(nh)])
        selected = [key for key in keys if key in needed]
        if not selected:
            continue
        if nh or (nf and ni):
            raise ContextError(f'unsupported mixed/half layout for {name}')
        count,fmt = (nf,'f') if nf else (ni,'i')
        if name in values:
            sequence = [values[name]] if count == 1 else values[name]
            try:
                if len(sequence) != count:
                    raise ValueError('component count')
                raw = struct.pack('<'+fmt*count,*sequence)
                components = struct.unpack('<'+fmt*count,raw)
                if fmt == 'f' and not all(math.isfinite(v) for v in components):
                    raise ValueError('nonfinite')
            except (TypeError,ValueError,struct.error,OverflowError) as exc:
                raise ContextError(f'invalid explicit dataset value {name}') from exc
            origin = 'explicit uniform input'
            consumed.add(name)
        else:
            entries = [entry for entry in store_offsets if entry.get('Name') == name]
            if len(entries) != 1:
                raise ContextError(f'missing explicit dataset input {name}')
            offsets = info.get('ComponentsOffsets')
            if not isinstance(offsets,list) or len(offsets) != count*2:
                raise ContextError(f'missing component byte offsets for {name}')
            base = entries[0].get('Offset')
            if not isinstance(base,int) or isinstance(base,bool) or base < 0:
                raise ContextError(f'invalid parameter store offset for {name}')
            components = []
            for offset in offsets[:count]:
                if not isinstance(offset,int) or isinstance(offset,bool) or offset < 0 or base+offset+4 > len(data):
                    raise ContextError(f'missing parameter bytes for {name}')
                components.append(struct.unpack_from('<'+fmt,data,base+offset)[0])
            origin = 'serialized instance parameter default'
        for component,key in enumerate(keys):
            if key in needed:
                if key in inputs:
                    raise ContextError(f'overlapping compiled dataset attribute {key}')
                inputs[key] = (components[component],)*num_instances
                provenance.append({'dataset':dataset_index,'kind':key[0],'attribute':key[1],
                                   'variable':name,'component':component,'provenance':origin,
                                   'value':components[component]})
    if needed-set(inputs):
        raise ContextError(f'unmapped required dataset attributes {sorted(needed-set(inputs))}')
    if set(values)-consumed:
        raise ContextError(f'unknown or unread explicit dataset values {sorted(set(values)-consumed)}')
    return DatasetInputContext(inputs,tuple(provenance))
