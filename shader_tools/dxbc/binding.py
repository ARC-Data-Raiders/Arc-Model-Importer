import gzip
import json
import pathlib
import struct
import sys


def classify(container):
    count = struct.unpack_from('<I', container, 28)[0]
    offsets = struct.unpack_from('<' + 'I' * count, container, 32)
    chunks = [container[o:o+4] for o in offsets]
    if b'SHEX' in chunks or b'SHDR' in chunks:
        return 'sm5'
    if b'DXIL' in chunks:
        return 'dxil'
    raise ValueError('unresolved container chunks: ' + repr(chunks))


def parse_header(data):
    if len(data) < 4:
        return None
    offset = 4
    arrays = []
    while len(arrays) < 6 and offset + 4 <= len(data):
        count = struct.unpack_from('<I', data, offset)[0]
        offset += 4
        if offset + count * 4 > len(data):
            return None
        arrays.append(list(struct.unpack_from('<' + 'I' * count, data, offset)))
        offset += count * 4
    if len(arrays) != 6:
        return None
    return {'tail': data[offset:].hex(), 'bits': struct.unpack_from('<I', data)[0], 'srv': arrays[0], 'sampler': arrays[1], 'uav': arrays[2], 'layout_hashes': arrays[3], 'texture': arrays[4], 'collection': arrays[5]}


def parse_trailer(data):
    if len(data) < 4 or struct.unpack_from('<I', data, len(data)-4)[0] != len(data):
        return None
    offset = 0
    result = {}
    while offset < len(data)-4:
        if offset+5 > len(data)-4:
            return None
        key = chr(data[offset])
        size = struct.unpack_from('<I', data, offset+1)[0]
        end = offset+5+size
        if end > len(data)-4:
            return None
        result[key] = data[offset+5:end]
        offset = end
    return result if offset == len(data)-4 else None


def ub_names(u_bytes):
    count = struct.unpack_from('<I', u_bytes)[0]
    offset = 4
    result = []
    for _ in range(count):
        size = struct.unpack_from('<I', u_bytes, offset)[0]
        offset += 4
        result.append(u_bytes[offset:offset+size-1].decode('ascii'))
        offset += size
    return result


def entries(resource_map, n_ub):
    result = []
    for ub in range(n_ub):
        if ub >= len(resource_map) or resource_map[ub] == 0:
            continue
        offset = resource_map[ub]
        while offset < len(resource_map) and resource_map[offset] != 0xffffffff and resource_map[offset] >> 24 == ub:
            value = resource_map[offset]
            result.append({'ub': ub, 'res': (value >> 8) & 0xffff, 'bind': value & 0xff})
            offset += 1
    return result


def bindings(record_bytes, container_offset, container_size, uniform_expression_set=None):
    header = parse_header(record_bytes[:container_offset])
    trailer = parse_trailer(record_bytes[container_offset+container_size:])
    if header is None or trailer is None:
        raise ValueError('unresolved header or trailer')
    names = ub_names(trailer['u']) if 'u' in trailer else []
    parameters = []
    if uniform_expression_set is not None:
        for type_index, group in enumerate(uniform_expression_set['UniformTextureParameters']):
            for parameter in group:
                parameters.append((type_index, parameter))
    result = {'cb': {str(i): name for i, name in enumerate(names)}, 't': {}, 's': {}}
    for map_name in ['srv', 'texture']:
        for entry in entries(header[map_name], len(names)):
            ub = names[entry['ub']]
            resource = entry['res']
            value = {'ub': ub, 'res': resource, 'map': map_name, 'param': None, 'param_index': None, 'type_index': None}
            if ub == 'Material' and uniform_expression_set is not None:
                if resource % 2 or resource >= 2*len(parameters):
                    raise ValueError('Material texture resource violates even parameter rule: ' + str(resource))
                index = resource // 2
                type_index, parameter = parameters[index]
                value.update(param=parameter['ParameterInfo']['Name'], param_index=index, type_index=type_index)
            result['t'][str(entry['bind'])] = value
    for entry in entries(header['sampler'], len(names)):
        ub = names[entry['ub']]
        resource = entry['res']
        value = {'ub': ub, 'res': resource, 'param': None, 'param_index': None, 'type_index': None, 'shared': None, 'sampler_source': None}
        if ub == 'Material' and uniform_expression_set is not None:
            count = len(parameters)
            if resource < 2*count:
                if resource % 2 != 1:
                    raise ValueError('Material own sampler resource must be odd: ' + str(resource))
                index = (resource-1)//2
                type_index, parameter = parameters[index]
                source = parameter['SamplerSource']
                if source != 'SSM_FromTextureAsset':
                    raise ValueError('Material own sampler source is ' + str(source))
                value.update(param=parameter['ParameterInfo']['Name'], param_index=index, type_index=type_index, sampler_source=source)
            elif resource == 2*count:
                value['shared'] = 'wrap'
            elif resource == 2*count+1:
                value['shared'] = 'clamp'
            else:
                raise ValueError('Material sampler resource beyond shared range: ' + str(resource))
        result['s'][str(entry['bind'])] = value
    return result


def _uniform_sets(directory):
    result = {}
    def walk(value):
        if isinstance(value, dict):
            shader_map = value.get('LoadedShaderMap')
            if isinstance(shader_map, dict) and shader_map.get('ResourceHash'):
                key = shader_map['ResourceHash'].upper()
                uniform_set = shader_map['Content']['MaterialCompilationOutput']['UniformExpressionSet']
                if key in result and result[key] != uniform_set:
                    raise ValueError('Conflicting UniformExpressionSet for ' + key)
                result[key] = uniform_set
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    for path in sorted(pathlib.Path(directory).rglob('*.shadermaps.json.gz')):
        walk(json.loads(gzip.decompress(path.read_bytes())))
    return result


def main():
    manifest = pathlib.Path(sys.argv[1])
    uniform_sets = _uniform_sets(sys.argv[2])
    output = pathlib.Path(sys.argv[3])
    output.mkdir(parents=True, exist_ok=True)
    seen = set()
    for record in json.loads(manifest.read_text(encoding='utf-8'))['records']:
        sha = record['container_sha256']
        if sha in seen:
            continue
        raw = (manifest.parent / record['record_file']).read_bytes()
        offset, size = record['container_offset'], record['container_size']
        if classify(raw[offset:offset+size]) != 'sm5':
            continue
        seen.add(sha)
        value = bindings(raw, offset, size, uniform_sets.get(record['map_hash'].upper()))
        (output / (sha+'.bindings.json')).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({'bindings_files': len(seen)}, sort_keys=True))


if __name__ == '__main__':
    main()
