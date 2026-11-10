"""GPU emulation census (spec N-O4): every system with an enabled GPU emitter, real DIs + GPU path, N ticks; classes and blockers."""
import argparse
import collections
import json
import multiprocessing as mp
import pathlib
import sys
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from Niagara.niagara import BakeError, BakeInputs, Scheduler
from Niagara.niagara import load_system


def run(job):
    path, ticks, shim, vf, bindings, shaders, ground = job
    start = time.perf_counter()
    try:
        model = load_system(path)
        if model.unsupported:
            return pathlib.Path(path).stem, {'class': 'blocked', 'reason': 'unsupported:' + ','.join(model.unsupported), 'seconds': 0.0}
        s = Scheduler(model, BakeInputs(ticks=ticks, dt=1 / 60, di_mode='real', svml_shim_path=shim, vector_fields=vf, gpu_bindings=bindings, gpu_shaders=shaders,
                                        ground_height=ground))
        s.run()
        counts = [max((r.count for r in s.emitters if getattr(r.model, 'gpu', None) is not None), default=0)]
        res = {'class': 'pass', 'max_gpu_count': max(counts)}
    except BakeError as exc:
        res = {'class': 'blocked', 'reason': str(exc)}
    except Exception as exc:  # noqa: BLE001
        res = {'class': 'error', 'reason': repr(exc)[:300], 'traceback': traceback.format_exc()[-1500:]}
    res['seconds'] = round(time.perf_counter() - start, 2)
    return pathlib.Path(path).stem, res


def main():
    ap = argparse.ArgumentParser()
    for a in ('--corpus', '--out', '--shim', '--vector-fields', '--gpu-bindings', '--gpu-shaders'):
        ap.add_argument(a, required=True)
    ap.add_argument('--ticks', type=int, default=60)
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--all', action='store_true', help='every corpus system (CPU-only too), not just GPU-binding packages')
    ap.add_argument('--only', help='comma-separated system names (subset of the selection)')
    ap.add_argument('--ground-height', type=float, help='synthetic flat ground (UE cm) for Landscape / GBuffer DIs; default empty world')
    a = ap.parse_args()
    paths = {p.stem: p for p in pathlib.Path(a.corpus).rglob('NS_*.json')}
    names = sorted(paths) if a.all else sorted(p.stem for p in pathlib.Path(a.gpu_bindings).glob('NS_*.json'))
    if a.only:
        names = [n for n in names if n in set(a.only.split(','))]
    jobs = [(str(paths[n]), a.ticks, a.shim, a.vector_fields, a.gpu_bindings, a.gpu_shaders, a.ground_height) for n in names if n in paths]
    with mp.Pool(a.jobs) as pool:
        results = dict(pool.imap_unordered(run, jobs))
    classes = collections.Counter(r['class'] for r in results.values())
    reasons = collections.Counter(r['reason'].split(':')[0] + (':' + r['reason'].split(':')[1] if r['reason'].count(':') else '') for r in results.values()
                                  if r['class'] in ('blocked', 'error'))
    out = {'systems': len(jobs), 'classes': dict(classes), 'top_reasons': dict(reasons.most_common(40)), 'results': dict(sorted(results.items()))}
    pathlib.Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({k: out[k] for k in ('systems', 'classes')}, sort_keys=True))
    for k, v in reasons.most_common(25):
        print(v, k)


if __name__ == '__main__':
    main()
