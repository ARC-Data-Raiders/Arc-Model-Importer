"""Material uniform evaluation, ported from the pinned O6 evaluator."""
import base64
import gzip
import json
import struct
import hashlib
import zlib
import numpy as np

OPCODES = ['Nop', 'ConstantZero', 'Constant', 'Parameter', 'Add', 'Sub', 'Mul', 'Div', 'Fmod', 'Modulo', 'Min', 'Max',
           'Clamp', 'Sin', 'Cos', 'Tan', 'Asin', 'Acos', 'Atan', 'Atan2', 'Dot', 'Cross', 'Sqrt', 'Rcp', 'Length',
           'Normalize', 'Saturate', 'Abs', 'Floor', 'Ceil', 'Round', 'Trunc', 'Sign', 'Frac', 'Fractional', 'Log2',
           'Log10', 'ComponentSwizzle', 'AppendVector', 'TextureSize', 'TexelSize',
           'ExternalTextureCoordinateScaleRotation', 'ExternalTextureCoordinateOffset', 'RuntimeVirtualTextureUniform',
           'SparseVolumeTextureUniform', 'GetField', 'SetField', 'Neg', 'Jump', 'JumpIfFalse', 'PushValue', 'Less',
           'Assign', 'Greater', 'LessEqual', 'GreaterEqual', 'Exp', 'Exp2', 'Log']

VALUE_TYPES = ['Void', 'Float1', 'Float2', 'Float3', 'Float4', 'Double1', 'Double2', 'Double3', 'Double4', 'Int1', 'Int2',
               'Int3', 'Int4', 'Bool1', 'Bool2', 'Bool3', 'Bool4', 'Numeric1', 'Numeric2', 'Numeric3', 'Numeric4',
               'Float4x4', 'Double4x4', 'DoubleInverse4x4', 'Numeric4x4', 'Struct', 'Object', 'Any']

COMPONENT_SIZE = {'Float': 4, 'Double': 8, 'Int': 4, 'Bool': 1, 'Numeric': 8}

def type_info(name):
    for prefix, size in COMPONENT_SIZE.items():
        if name.startswith(prefix) and name[len(prefix):].isdigit():
            return prefix, int(name[len(prefix):]), size
    if name == 'Float4x4':
        return 'Float', 16, 4
    if name in ('Double4x4', 'DoubleInverse4x4'):
        return 'Double', 16, 8
    raise ValueError('unsupported value type ' + name)

class Reader:
    def __init__(self, data, names, start, size):
        self.data, self.names, self.pos, self.end = data, names, start, start + size

    def take(self, fmt):
        value = struct.unpack_from('<' + fmt, self.data, self.pos)
        self.pos += struct.calcsize('<' + fmt)
        if self.pos > self.end:
            raise ValueError('operand overrun')
        return value if len(value) > 1 else value[0]

    def vtype(self):
        name = VALUE_TYPES[self.take('B')]
        if name == 'Struct':
            raise ValueError('struct constants not supported in prototype')
        return name

    def param_info(self):
        name = self.names[self.take('H')]
        index = self.take('i')
        association = self.take('B')
        return {'name': name, 'index': index, 'association': association}

def decode(data, names, start, size):
    r = Reader(data, names, start, size)
    ops = []
    while r.pos < r.end:
        at = r.pos - start
        code = r.take('B')
        if code >= len(OPCODES):
            raise ValueError(f'opcode {code} out of range at +{at}')
        op = OPCODES[code]
        operands = {}
        if op == 'ConstantZero':
            operands['type'] = r.vtype()
        elif op == 'Constant':
            vt = r.vtype()
            kind, count, csize = type_info(vt)
            fmt = {'Float': 'f', 'Double': 'd', 'Int': 'i', 'Bool': '?', 'Numeric': 'd'}[kind]
            operands['type'] = vt
            operands['value'] = [r.take(fmt) for _ in range(count)]
        elif op == 'Parameter':
            operands['parameter'] = r.take('H')
        elif op in ('GetField',):
            operands['type'] = r.vtype()
            operands['component'] = r.take('i')
        elif op == 'SetField':
            operands['component'] = r.take('i')
            operands['count'] = r.take('i')
        elif op == 'PushValue':
            operands['stack_offset'] = r.take('H')
        elif op == 'ComponentSwizzle':
            operands['count'] = r.take('B')
            operands['indices'] = [r.take('B') for _ in range(4)]
        elif op in ('TextureSize', 'TexelSize'):
            operands['parameter'] = r.param_info()
            operands['texture'] = r.take('i')
        elif op in ('RuntimeVirtualTextureUniform', 'SparseVolumeTextureUniform'):
            operands['parameter'] = r.param_info()
            operands['texture'] = r.take('i')
            operands['vector'] = r.take('i')
        elif op in ('ExternalTextureCoordinateScaleRotation', 'ExternalTextureCoordinateOffset'):
            operands['name'] = names[r.take('H')]
            operands['guid'] = r.take('4I')
            operands['texture'] = r.take('i')
        elif op in ('Jump', 'JumpIfFalse'):
            operands['offset'] = r.take('i')
        ops.append({'at': at, 'op': op, **operands})
    if r.pos != r.end:
        raise ValueError('program did not end exactly')
    return ops

F = np.float32

UE_DELTA = F(0.00001)

def safe(x):
    return np.where(np.abs(x) < UE_DELTA, np.where(x < 0, -UE_DELTA, UE_DELTA), x).astype(F)

class Value:
    """kind: 'Float' | 'Double' | 'Unresolved'; comps: numpy array."""

    def __init__(self, kind, comps, note=None):
        self.kind, self.comps, self.note = kind, np.asarray(comps, dtype=F if kind == 'Float' else np.float64), note

def binary(op, a, b):
    if 'Unresolved' in (a.kind, b.kind):
        return Value('Unresolved', np.zeros(max(len(a.comps), len(b.comps))), (a.note or '') + (b.note or ''))
    if a.kind != 'Float' or b.kind != 'Float':
        raise NotImplementedError('double arithmetic not needed for this material')
    x, y = a.comps, b.comps
    if len(x) == 1 and len(y) > 1:
        x = np.repeat(x, len(y))
    if len(y) == 1 and len(x) > 1:
        y = np.repeat(y, len(x))
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    if op == 'Add':
        return Value('Float', x + y)
    if op == 'Sub':
        return Value('Float', x - y)
    if op == 'Mul':
        return Value('Float', x * y)
    if op == 'Div':
        return Value('Float', x / safe(y))
    if op == 'Min':
        return Value('Float', np.minimum(x, y))
    if op == 'Max':
        return Value('Float', np.maximum(x, y))
    raise NotImplementedError(op)

def unary(op, a):
    if a.kind == 'Unresolved':
        return a
    x = a.comps
    if op == 'Rcp':
        return Value('Float', F(1) / safe(x))
    if op == 'Saturate':
        return Value('Float', np.clip(x, F(0), F(1)))
    if op == 'Round':
        return Value('Float', np.floor(x + F(0.5)))
    if op == 'Sin':
        return Value('Float', np.sin(x.astype(np.float64)).astype(F))
    if op == 'Cos':
        return Value('Float', np.cos(x.astype(np.float64)).astype(F))
    if op == 'Neg':
        return Value('Float', -x)
    if op == 'Abs':
        return Value('Float', np.abs(x))
    if op == 'Floor':
        return Value('Float', np.floor(x))
    raise NotImplementedError(op)

def _run(ops, parameter):
    stack = []
    for o in ops:
        op = o['op']
        if op == 'Parameter':
            stack.append(parameter(o['parameter']))
        elif op == 'Constant':
            if not o['type'].startswith('Float'):
                raise NotImplementedError(o['type'])
            stack.append(Value('Float', [F(v) for v in o['value']]))
        elif op == 'ConstantZero':
            stack.append(Value('Float', np.zeros(int(o['type'][-1]))))
        elif op in ('Add', 'Sub', 'Mul', 'Div', 'Min', 'Max'):
            b, a = stack.pop(), stack.pop()
            stack.append(binary(op, a, b))
        elif op == 'Clamp':
            c, b, a = stack.pop(), stack.pop(), stack.pop()
            stack.append(binary('Min', binary('Max', a, b), c))
        elif op in ('Rcp', 'Saturate', 'Round', 'Sin', 'Cos', 'Neg', 'Abs', 'Floor'):
            stack.append(unary(op, stack.pop()))
        elif op == 'AppendVector':
            b, a = stack.pop(), stack.pop()
            if 'Unresolved' in (a.kind, b.kind):
                stack.append(Value('Unresolved', np.zeros(len(a.comps) + len(b.comps))))
            else:
                stack.append(Value('Float', np.concatenate([a.comps, b.comps])))
        elif op == 'ComponentSwizzle':
            a = stack.pop()
            n0 = len(a.comps)
            idx = [(0 if n0 == 1 and i != 255 else i) for i in o['indices'][:o['count']]]
            comps = [a.comps[i] if i < n0 else 0 for i in idx]
            stack.append(Value(a.kind, comps, a.note))
        elif op == 'RuntimeVirtualTextureUniform':
            stack.append(Value('Unresolved', np.zeros(4), f"RVT(texture={o['texture']},vector={o['vector']})"))
        else:
            raise NotImplementedError(op)
    if len(stack) != 1:
        raise ValueError('stack not singular')
    return stack[0]

def _evaluate_raw(path, resource):
    d = json.load(gzip.open(path))
    chain = d['chain']
    u = chain[0]['json']['LoadedMaterialResources'][resource]['LoadedShaderMap']['Content']['MaterialCompilationOutput']['UniformExpressionSet']
    defaults = base64.b64decode(u['DefaultValues'])
    overrides = []
    for link in chain[:-1]:
        p = link['json']['Properties']
        table = {}
        for s in p.get('ScalarParameterValues', []):
            i = s['ParameterInfo']
            table[('Scalar', i['Name'], i['Association'].split('::')[-1], i['Index'])] = [s['ParameterValue']]
        for s in p.get('VectorParameterValues', []):
            i = s['ParameterInfo']
            table[('Vector', i['Name'], i['Association'].split('::')[-1], i['Index'])] = [s['ParameterValue'][k] for k in 'RGBA']
        overrides.append((link['object_path'].split('.')[-1], table))
    resolved = []
    for n in u['UniformNumericParameters']:
        i = n['ParameterInfo']
        key = (n['ParameterType'], i['Name'], i['Association'], i['Index'])
        source, comps = 'default', None
        for name, table in overrides:
            if key in table:
                source, comps = name, [F(v) for v in table[key]]
                break
        if comps is None:
            count = 1 if n['ParameterType'] == 'Scalar' else 4
            comps = list(np.frombuffer(defaults, dtype='<f4', count=count, offset=n['DefaultValueOffset']))
        resolved.append({'name': i['Name'], 'type': n['ParameterType'], 'source': source, 'value': [float(c) for c in comps]})
    pd = u['UniformPreshaderData']
    data = base64.b64decode(pd['Data'])
    buffer = np.zeros(u['UniformPreshaderBufferSize'] * 4, dtype=F)
    origin = [None] * len(buffer)
    unresolved = set()
    for k, pre in enumerate(u['UniformPreshaders']):
        ops = decode(data, pd['Names'], pre['OpcodeOffset'], pre['OpcodeSize'])
        params = sorted({o['parameter'] for o in ops if o['op'] == 'Parameter'})
        result = run(ops, lambda index: Value('Float', resolved[index]['value']))
        for fi in range(pre['NumFields']):
            field = u['UniformPreshaderFields'][pre['FieldIndex'] + fi]
            t = field['Type']
            ncomp = int(t[-1])
            width = ncomp * (2 if t.startswith('Double') else 1)
            for j in range(width):
                origin[field['BufferOffset'] + j] = {'preshader': k, 'params': [resolved[p]['name'] for p in params], 'field_type': t, 'component': j}
            if result.kind == 'Unresolved':
                for j in range(width):
                    unresolved.add(field['BufferOffset'] + j)
                    origin[field['BufferOffset'] + j]['unresolved'] = result.note
                continue
            if not t.startswith('Float'):
                raise NotImplementedError(t)
            comps = result.comps[field['ComponentIndex']:]
            for j in range(ncomp):
                buffer[field['BufferOffset'] + j] = comps[j] if j < len(comps) else F(0)
    return d, u, resolved, buffer, origin, unresolved

UBMT = {'EUniformBufferBaseType_NumBits':5,'UBMT_TEXTURE':5,'UBMT_SRV':6,'UBMT_UAV':7,'UBMT_SAMPLER':8}

def layout_hash(ini):
    flags = {'None': 0}[ini['Flags']]
    binding = {'Shader': 1, 'Static': 2, 'StaticAndShader': 3}[ini['BindingFlags']]
    res = [(r['MemberOffset'], UBMT[r['MemberType']]) for r in ini['Resources']]
    h = (ini['ConstantBufferSize'] << 16) | (binding << 8) | (1 if ini['StaticSlot'] != 255 else 0) | flags
    for off, _ in res: h ^= off
    t = [r[1] for r in res]; n = len(t)
    while n >= 4:
        n -= 1; h ^= t[n]; n -= 1; h ^= t[n] << 8; n -= 1; h ^= t[n] << 16; n -= 1; h ^= t[n] << 24
    while n >= 2:
        n -= 1; h ^= t[n]; n -= 1; h ^= t[n] << 16
    while n > 0:
        n -= 1; h ^= t[n]
    h ^= zlib.crc32(ini['Name'].encode('utf-32-le'))
    return h & 0xFFFFFFFF

class Unsupported(Exception):
    pass


def run(ops, parameter):
    try:
        return _run(ops, parameter)
    except NotImplementedError as error:
        raise Unsupported(str(error)) from error


def evaluate(shadermaps_path):
    d = json.load(gzip.open(shadermaps_path))
    chain = d["chain"]
    resources = chain[0]["json"]["LoadedMaterialResources"]
    ri = next(i for i, r in enumerate(resources) if r["LoadedShaderMap"]["ShaderPlatform"] == "SP_PCD3D_SM5")
    u = resources[ri]["LoadedShaderMap"]["Content"]["MaterialCompilationOutput"]["UniformExpressionSet"]
    for n in u["UniformNumericParameters"]:
        if n["ParameterInfo"]["Association"].split("::")[-1] != "GlobalParameter":
            raise Unsupported("association:" + n["ParameterInfo"]["Association"])
    try:
        d, u, resolved, buffer, origin, unresolved = _evaluate_raw(shadermaps_path, ri)
        computed = layout_hash(u["UniformBufferLayoutInitializer"])
    except (NotImplementedError, KeyError) as error:
        raise Unsupported(str(error)) from error
    ini = u["UniformBufferLayoutInitializer"]
    if computed != ini["Hash"]:
        raise Unsupported("layout_hash")
    offset = (len(u["UniformTextureCollectionParameters"]) + 2 * len(u["VTStacks"])
              + len(u["MaterialCacheTagStacks"] or []) + len(u["UniformTextureParameters"][5])
              + 2 * len(u["UniformTextureParameters"][6]))
    textures = []
    base = chain[-1]["json"]
    refs = (base.get("CachedExpressionData") or base["Properties"]["CachedExpressionData"])["ReferencedTextures"]
    for ti, group in enumerate(u["UniformTextureParameters"]):
        for pi, parameter in enumerate(group):
            info = parameter["ParameterInfo"]
            key = (info["Name"], info["Association"].split("::")[-1], info["Index"])
            if key[1] != "GlobalParameter":
                raise Unsupported("association:" + key[1])
            value, source = None, None
            for link in chain[:-1]:
                for t in link["json"]["Properties"].get("TextureParameterValues", []):
                    i = t["ParameterInfo"]
                    if (i["Name"], i["Association"].split("::")[-1], i["Index"]) == key and t["ParameterValue"]:
                        value = t["ParameterValue"]["ObjectPath"].rsplit(".", 1)[0]
                        source = link["object_path"].rsplit(".", 1)[0]
                        break
                if value:
                    break
            if value is None:
                default = refs[parameter["TextureIndex"]]
                default = default.get("ObjectPath") if isinstance(default, dict) else default
                value, source = default.rsplit(".", 1)[0], "default"
            textures.append(dict(type_index=ti, param_index=pi, parameter=key[0], association=key[1], index=key[2],
                                 texture=value, resolved_from=source, sampler_source=parameter["SamplerSource"]))
    return dict(schema="dr.material-ub/1", material_instance=chain[0]["object_path"].rsplit(".", 1)[0],
                chain=[c["object_path"].rsplit(".", 1)[0] for c in chain],
                base_material=chain[-1]["object_path"].rsplit(".", 1)[0], resource_index=ri,
                resource_hash=resources[ri]["LoadedShaderMap"]["ResourceHash"],
                ub=dict(name=ini["Name"], constant_buffer_size=ini["ConstantBufferSize"], layout_hash=ini["Hash"],
                        layout_hash_computed=computed),
                preshader_offset_vectors=offset, preshader_vectors=u["UniformPreshaderBufferSize"],
                parameters=resolved, preshader_floats=[None if i in unresolved else float(v) for i,v in enumerate(buffer)],
                preshader_float_bits=[None if i in unresolved else int(np.asarray(v,dtype=F).view(np.uint32)) for i,v in enumerate(buffer)],
                origin=origin, unresolved_floats=sorted(unresolved), parameter_collections=u["ParameterCollections"], textures=textures)


def cb_words(ub, n):
    result = np.zeros((n, 4), dtype="<u4")
    offset, size = ub["preshader_offset_vectors"], ub["preshader_vectors"]
    result[offset:offset+size] = np.asarray([0 if b is None else b for b in ub["preshader_float_bits"]], dtype="<u4").reshape(size, 4)
    return result


def bits_sha256(ub):
    return hashlib.sha256(np.asarray([0 if b is None else b for b in ub["preshader_float_bits"]], dtype="<u4").tobytes()).hexdigest().upper()
