"""Gate D / D2: Gate V corpus programs with real data interfaces on both sides (Waves N4-N5; spec N-O3 §7, §11.6).

gen            : copy Gate V cases and attach `di` (exact words) to oracle-checkable functions.
                 --set d  : programs binding >= 1 curve DI (Wave N4).
                 --set d2 : programs binding >= 1 of VectorField.SampleField, SpriteRendererInfo.GetSubUVDetails,
                            FastMatrixToQuaternion; curves in those programs are real too.
eval           : evaluate like niagara_gate_v.eval_one in mode B; functions with `di` are real, others the Gate V stub.
vfdump-cases   : write the plugin vfdump cases for the vector field assets used by the corpus.
vfdump-collect : turn the vfdump oracle results into vector_fields.json.
Oracle, compare and report are niagara_gate_v.py's, unchanged.
"""
import argparse
import base64
import collections
import concurrent.futures
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import Niagara.niagara as niagara_di  # noqa: E402
import niagara_gate_v as gv  # noqa: E402
import Niagara.niagara as niagara_ir  # noqa: E402
from Niagara.niagara import Dataset, ExecutionError, IdState, execute, float_word, word_float, word_int  # noqa: E402

D2_TARGETS = {(niagara_di.di_VECTOR_FIELD, 'SampleField'), (niagara_di.di_SPRITE_INFO, 'GetSubUVDetails'),
              (niagara_di.FAST_PATH, 'FastMatrixToQuaternion')}
VECTOR_FIELD_ASSETS = ['/Niagara/VectorFields/Assets/TilingCurl16.TilingCurl16', '/Niagara/VectorFields/Assets/TilingCurl32.TilingCurl32',
                       '/Niagara/VectorFields/Assets/TilingCurl64.TilingCurl64']


def corpus_json(corpus, package):
    return Path(corpus) / 'PioneerGame' / 'Content' / (package.removeprefix('/Game/') + '.json')


def _key(b):
    return (b.di_class if b.kind == 'di' else niagara_di.FAST_PATH, b.name)


def gen(args):
    src, out = Path(args.gatev_cases), Path(args.out)
    index = gv.read(src / 'index.json')
    by_package = collections.defaultdict(set)
    for meta in index.values():
        by_package[meta['package']].add(int(meta['export_index']))
    found_by_program = {}
    for package in sorted(by_package):
        exports = niagara_ir.exports_from_document(gv.read(corpus_json(args.corpus, package)))
        for export_index in sorted(by_package[package]):
            bindings = niagara_di.bind_script(exports, export_index)
            keys = {_key(b) for b in bindings}
            if args.set == 'd':
                wanted = [b for b in bindings if _key(b) in niagara_di.CURVE_ELEMENTS]
            else:
                wanted = [b for b in bindings if _key(b) in niagara_di.CURVE_ELEMENTS or _key(b) in D2_TARGETS] if keys & D2_TARGETS else []
            found_by_program[package, export_index] = {b.fn_index: niagara_di.case_di(b, exports) for b in wanted}
    kept = {}
    for path in gv.case_paths(src):
        cid = path.name.removesuffix('.case.json')
        found = found_by_program[index[cid]['package'], int(index[cid]['export_index'])]
        if not found:
            continue
        case = gv.read(path)
        for k, di in found.items():
            case['ext_functions'][k]['di'] = di
        kept[cid] = case
    for i, cid in enumerate(sorted(kept)):
        gv.write(out / ('batch_' + str(i // 1000).zfill(3)) / (cid + '.case.json'), kept[cid])
    gv.write(out / 'index.json', {cid: index[cid] for cid in sorted(kept)})
    slots = sum(len(found_by_program[index[cid]['package'], int(index[cid]['export_index'])]) for cid in kept)
    only = sum(1 for cid in kept if all('di' in f for f in kept[cid]['ext_functions']))
    if args.set == 'd':
        print(json.dumps({'cases': len(kept), 'cases_curves_only': only, 'curve_slots': slots,
                          'programs': len({index[cid]['program_id'] for cid in kept})}, sort_keys=True))
    else:
        per = collections.Counter(f['di']['class'] for cid in kept for f in kept[cid]['ext_functions'] if 'di' in f)
        print(json.dumps({'cases': len(kept), 'cases_all_real': only, 'di_slots': slots, 'di_slots_per_class': dict(per),
                          'programs': len({index[cid]['program_id'] for cid in kept})}, sort_keys=True))


_VECTOR_FIELDS = {}


def eval_one(payload):
    path, out, shim, fma, vf_path, vf_fma, vf_half = payload
    if vf_path and vf_path not in _VECTOR_FIELDS:
        _VECTOR_FIELDS[vf_path] = niagara_di.load_vector_fields(vf_path)
    c = gv.read(path)
    n = c['num_instances']
    datasets, ids = {}, {}
    for d, ds in enumerate(c['datasets']):
        inputs, capacities = {}, {}
        for kind, internal, convert in [('float', 'float', word_float), ('int', 'int32', word_int)]:
            for a, blob in enumerate(ds['inputs'][kind]):
                inputs[internal, a] = [convert(w) for w in gv.words(blob)]
            for a in range(ds['outputs'][kind]):
                capacities[internal, a] = n
        datasets[d] = Dataset(inputs=inputs, output_capacities=capacities, instance_offset=0, access_index=-1)
        if ds['id'] is not None:
            s = ds['id']
            ids[d] = IdState(s['id_table'], s['free_ids'], s['num_free_ids'], s['free_capacity'], s['max_used_id'], 0, [], s['id_acquire_tag'])
    ext = [{'InputParamLocations': [0] * f['num_inputs'], 'NumOutputs': f['num_outputs']} for f in c['ext_functions']]
    real = {k for k, f in enumerate(c['ext_functions']) if 'di' in f}
    result = {'schema': 'dr.vm-oracle-result/1', 'case_id': c['case_id']}
    try:
        handler = niagara_di.case_handler(c['ext_functions'], fma_mode=fma, vector_fields=_VECTOR_FIELDS.get(vf_path, {}),
                                          vf_fma_mode=vf_fma, vf_half_decode=vf_half, log_stub_calls=True)
        r = execute(gv.unb64(c['legacy_bytecode_b64']), num_instances=n, constant_tables=[gv.unb64(t) for t in c['constant_tables_b64']],
                    datasets=datasets, external_functions=ext, profile='pioneer-2.1.0', x86_semantics=True, math_backend='svml_shim',
                    svml_shim_path=shim, fma_mode='unfused', external_handler=handler, id_states=ids, rng_seed=(1, 3))
        result.update(ok=True, datasets=[], ext_calls=[])
        for d, ds in enumerate(c['datasets']):
            output = {'outputs': {'float': [], 'int': []}, 'access_index': r.access_indices.get(d, -1),
                      'taint': sorted(r.dataset_taints.get(d, ())), 'output_taints': {'float': [], 'int': []}}
            for kind, internal in [('float', 'float'), ('int', 'int32')]:
                for a in range(ds['outputs'][kind]):
                    values = r.outputs[d, (internal, a)]
                    output['outputs'][kind].append(gv.encode_words([0xdeadbeef if v is None else float_word(v) if kind == 'float' else v & 0xffffffff for v in values]))
                    output['output_taints'][kind].append(sorted(r.output_taints.get((d, (internal, a)), ())))
            if d in r.id_states:
                s = r.id_states[d]
                output['id'] = {'id_table': s.id_table, 'free_ids_valid': s.free_ids, 'spawned_ids': s.spawned_ids, 'num_free_ids': s.num_free_ids,
                                'num_spawned_ids': s.num_spawned_ids, 'max_used_id': s.max_used_id, 'free_capacity': s.free_capacity}
            result['datasets'].append(output)
        vm_calls = [call for call in r.ext_calls if call['fn'] not in real]
        if len(vm_calls) != len(handler.stub_calls):
            raise ExecutionError('stub call log mismatch')
        for call, vm in zip(handler.stub_calls, vm_calls):
            result['ext_calls'].append({'fn': call['fn'], 'start': call['start'], 'count': call['count'],
                                        'input_taints': vm['input_taints'], 'inputs_b64': gv.encode_words(call['inputs'])})
    except (ExecutionError, niagara_di.DIError) as e:
        result = {'schema': 'dr.vm-oracle-result/1', 'case_id': c['case_id'], 'ok': False, 'error': str(e)}
    gv.write(Path(out) / (c['case_id'] + '.eval.json'), result)
    return c['case_id']


def evaluate(args):
    Path(args.out).mkdir(parents=True, exist_ok=True)
    payloads = [(str(p), args.out, args.svml_shim, args.fma, args.vector_fields, args.vf_fma, args.vf_half) for p in gv.case_paths(args.cases)]
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for _ in pool.map(eval_one, payloads, chunksize=16):
            pass
    print(json.dumps({'evaluated': len(payloads), 'fma': args.fma, 'vf_fma': args.vf_fma, 'vf_half': args.vf_half}, sort_keys=True))


def vfdump_cases(args):
    out = Path(args.out) / 'batch_000'
    for asset in VECTOR_FIELD_ASSETS:
        cid = 'VF_' + asset.rsplit('.', 1)[1]
        gv.write(out / (cid + '.case.json'), {'schema': 'dr.vm-oracle-vfdump/1', 'case_id': cid, 'asset': asset})
    print(json.dumps({'cases': len(VECTOR_FIELD_ASSETS)}, sort_keys=True))


def vfdump_collect(args):
    fields, stats = {}, {}
    for asset in VECTOR_FIELD_ASSETS:
        cid = 'VF_' + asset.rsplit('.', 1)[1]
        row = gv.read(Path(args.oracle) / 'batch_000' / (cid + '.result.json'))
        if not row.get('ok'):
            raise RuntimeError('vfdump failed for %s: %s' % (asset, row.get('error')))
        keep = {k: row[k] for k in ('size_x', 'size_y', 'size_z', 'bounds_min_bits', 'bounds_max_bits', 'intensity_bits', 'allow_cpu_access',
                                    'has_cpu_data', 'cpu_data_b64', 'cpu_data_halves')}
        fields[asset] = keep
        halves = np.frombuffer(base64.b64decode(keep['cpu_data_b64']), dtype='<u2')
        exponent = (halves >> 10) & 0x1f
        stats[asset] = {'size': [keep['size_x'], keep['size_y'], keep['size_z']], 'allow_cpu_access': keep['allow_cpu_access'],
                        'has_cpu_data': keep['has_cpu_data'], 'halves': int(halves.size),
                        'zero': int(np.count_nonzero((halves & 0x7fff) == 0)), 'denormal': int(np.count_nonzero((exponent == 0) & ((halves & 0x3ff) != 0))),
                        'inf_nan': int(np.count_nonzero(exponent == 31))}
    doc = {'schema': 'dr.niagara-vector-fields/1', 'fields': fields}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(stats, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('gen')
    for name in ['gatev-cases', 'corpus', 'out']:
        p.add_argument('--' + name, required=True)
    p.add_argument('--set', choices=['d', 'd2'], default='d')
    p.set_defaults(func=gen)
    p = sub.add_parser('eval')
    for name in ['cases', 'out', 'svml-shim']:
        p.add_argument('--' + name, required=True)
    p.add_argument('--fma', choices=['unfused', 'fused'], required=True)
    p.add_argument('--vector-fields', default=None)
    p.add_argument('--vf-fma', choices=['unfused', 'fused'], default='unfused')
    p.add_argument('--vf-half', choices=['fast', 'ieee'], default='fast')
    p.add_argument('--jobs', type=int, required=True)
    p.set_defaults(func=evaluate)
    p = sub.add_parser('vfdump-cases')
    p.add_argument('--out', required=True)
    p.set_defaults(func=vfdump_cases)
    p = sub.add_parser('vfdump-collect')
    for name in ['oracle', 'out']:
        p.add_argument('--' + name, required=True)
    p.set_defaults(func=vfdump_collect)
    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
