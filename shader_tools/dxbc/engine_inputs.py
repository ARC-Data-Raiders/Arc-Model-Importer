"""Live engine inputs and folding per O5.4."""
import copy
import json
import math
from pathlib import Path
import re
import numpy as np
from . import ssa,opt
from .opt import ids
P=Path(r"Z:\Datamining\Arc Raiders\CodexScratch\DataRaiders-BlenderImporter\.local\parity-v2")
view={m["name"]:m for m in json.loads((P/"supervisor/o6/view_layout_ue574.json").read_text())}
GEO={"wall":dict(C=(600.0,0.0,800.0),F=(-1.0,0.0,0.0)),"floor":dict(C=(0.0,0.0,500.0),F=(0.0,0.0,-1.0))}
VIEW_LIVE={"RelativeWorldCameraOriginTO":"dr_cam","RelativePreViewTranslationTO":"dr_camneg","ViewForward":"dr_camfwd"}

def dcl_cbs(shader):
    out = {}
    for d in shader['declarations']:
        if d['op'] == 'dcl_constantbuffer':
            m = re.match(r'CB(\d+)\[(\d+)\]', d['text'])
            out[int(m[1])] = int(m[2])
    return out

def put(cb, name, element, value, uint=False):
    off = view[name]['offset'] // 4 + element
    if uint:
        cb.view(np.uint32).reshape(-1)[off] = value
    else:
        cb.reshape(-1)[off] = value

def view_cb(n, geometry, size, mode):
    C, F = GEO[geometry]['C'], GEO[geometry]['F']
    s = 2000.0 / size
    if mode == 'material':
        M = np.eye(4)
    elif geometry == 'wall':
        M = [(0, s, 0, 0), (0, 0, -s, 0), (0, 0, 0, 0), (100 - C[0], -1000 - C[1], 1800 - C[2], 1)]
    else:
        M = [(0, s, 0, 0), (s, 0, 0, 0), (0, 0, 0, 0), (-1000 - C[0], -1000 - C[1], -C[2], 1)]
    cb = np.zeros((n, 4), np.float32)
    put(cb, 'ViewToClip', 15, 0.0)
    for r in range(4):
        for c in range(4):
            put(cb, 'SVPositionToTranslatedWorld', 4 * r + c, M[r][c])
    for i in range(3):
        put(cb, 'ViewForward', i, F[i]); put(cb, 'ViewTilePosition', i, 0.0)
        put(cb, 'RelativeWorldCameraOriginTO', i, C[i]); put(cb, 'RelativePreViewTranslationTO', i, -C[i])
    put(cb, 'PreExposure', 0, 1.0)
    for name in ['DiffuseOverrideParameter', 'SpecularOverrideParameter', 'NormalOverrideParameter']:
        for i, v in enumerate((0, 0, 0, 1)):
            put(cb, name, i, v)
    for name in ['RoughnessOverrideParameter', 'AmbientOcclusionOverrideParameter']:
        put(cb, name, 0, 0.0); put(cb, name, 1, 1.0)
    put(cb, 'OutOfBoundsMask', 0, 0.0)
    put(cb, 'StateFrameIndexMod8', 0, 0, uint=True)
    cb[167, 1] = 1.0
    put(cb, 'RenderingReflectionCaptureMask', 0, 0.0)
    put(cb, 'MaterialMaxEmissiveValue', 0, 32256.0)
    return cb

def primitive(cpd):
    p = np.zeros((44, 4), np.float32)
    p.view(np.uint32)[0, 0] = 512
    for r in range(3):
        p[2 + r, r] = 1.0; p[5 + r, r] = 1.0
    for k, v in cpd.items():
        p[35 + k // 4, k % 4] = v
    return p

def vs_struct(shader):
    interp = {}
    for d in shader['declarations']:
        if d['op'] == 'dcl_input_ps':
            m = re.search(r'\bv(\d+)', d['text'])
            t = d['text']
            interp[m[1]] = 'centroid' if t.startswith('linear centroid') else 'nointerpolation' if t.startswith('constant') else 'linear' if t.startswith('linear ') else None
    members = []
    for row in shader['signatures']['input']:
        if row['SysValue'] != 'NONE':
            continue
        typ = 'uint' if row['Name'] == 'PRIMITIVE_ID' else 'float'
        members.append('%s %s%d a%s : %s%s' % (interp[row['Register']], typ, len(row['Mask']), row['Register'], row['Name'], row['Index']))
    return 'struct Output {' + ' '.join(m + ';' for m in members + ['float4 p : SV_Position']) + '};'

def coefficients(shader, geometry, span):
    C = GEO[geometry]['C']
    h, q = span / 2, span / 8
    t9 = {'wall': [(100 - C[0], 0, 0), (-C[1], 1000, 0), (800 - C[2], 0, 1000)],
          'floor': [(-C[0], 0, -1000), (-C[1], 1000, 0), (-C[2], 0, 0)]}[geometry]
    tan = (0, 1, 0, 0)
    nrm = {'wall': (1, 0, 0, 1), 'floor': (0, 0, 1, 1)}[geometry]
    interp_kw = {}
    for d in shader['declarations']:
        if d['op'] == 'dcl_input_ps':
            import re
            m = re.search(r'\bv(\d+)', d['text']); t = d['text']
            interp_kw[m[1]] = 'centroid' if t.startswith('linear centroid') else 'nointerpolation' if t.startswith('constant') else 'linear'
    coef = []
    for row in shader['signatures']['input']:
        if row['SysValue'] != 'NONE':
            continue
        reg, sem, kw = int(row['Register']), row['Name'] + row['Index'], interp_kw[row['Register']]
        comps = row['Mask']
        if sem == 'TEXCOORD10':
            vals = [(tan[i], 0, 0) for i in range(4)]
        elif sem == 'TEXCOORD11':
            vals = [(nrm[i], 0, 0) for i in range(4)]
        elif sem == 'COLOR1':
            vals = [(v, None, None) for v in (1, 1, 0, 0)]
        elif sem == 'TEXCOORD0':
            vals = [(h, h, 0), (h, 0, -h), (q, q, 0), (q, 0, -q)]
        elif sem == 'PRIMITIVE_ID0':
            vals = [(0, None, None)]
        elif sem == 'TEXCOORD9':
            vals = t9
        else:
            raise ValueError(sem)
        for c, (a0, a1, a2) in zip(comps, vals):
            coef.append({'register': reg, 'component': c, 'format': 'uint' if sem == 'PRIMITIVE_ID0' else 'float', 'interpolation': kw, 'a0': a0, 'a1': a1, 'a2': a2})
    return coef

def vs_text(shader, coef):
    body = []
    for reg in sorted({c['register'] for c in coef}):
        rows = [c for c in coef if c['register'] == reg]
        if rows[0]['format'] == 'uint':
            body.append('o.a%d=uint1(%d);' % (reg, rows[0]['a0']))
            continue
        terms = [repr(float(c['a0'])) if c['a1'] is None else '(%r+(%r)*ndc.x+(%r)*ndc.y)' % (float(c['a0']), float(c['a1']), float(c['a2'])) for c in rows]
        body.append('o.a%d=float%d(%s);' % (reg, len(rows), ','.join(terms)))
    return vs_struct(shader) + '\nOutput main(uint vid : SV_VertexID) { float2 positions[3]={float2(-1,-1),float2(-1,3),float2(3,-1)}; float2 ndc=positions[vid]; Output o; ' + ' '.join(body) + ' o.p=float4(ndc,0.5,1); return o; }\n'

def view_member(byte):
    for m in view.values():
        if m['offset'] <= byte < m['offset'] + m['size']:
            return m['name'], (byte - m['offset']) // 4
    return None, None

def _fold_live(dag, shader, fixture, params):
    flat = set()
    for declaration in shader['declarations']:
        if declaration['op'] == 'dcl_input_ps' and declaration['text'].startswith('constant '):
            match = re.search(r'\bv(\d+)', declaration['text'])
            if match:
                flat.add('v' + match[1])
    uni = []
    for node in dag['nodes']:
        op = node['op']
        if node['id'] in params:
            value = False
        elif op in ['const', 'cbload']:
            value = True
        elif op == 'input':
            value = node['args'][0] in flat
        elif op == 'deriv':
            value = False
        else:
            value = all(uni[n] for n in ids(node))
        uni.append(value)
    boundary = set(dag['outputs'].values())
    for node in dag['nodes']:
        if not uni[node['id']]:
            boundary.update(ids(node))
    ev = ssa.Evaluator(shader, fixture)
    ev.evaluate(dag)
    result = copy.deepcopy(dag)
    for ident in sorted(boundary):
        if not uni[ident]:
            continue
        value = ev.values[ident]
        if not np.all(value == value[0, 0]):
            raise ValueError('non-uniform value at node ' + str(ident))
        result['nodes'][ident].update(op='const', args=[int(value[0, 0])])
    names = sorted(set(params.values()))
    preg = {name: 'p%d' % i for i, name in enumerate(names)}
    values = {}
    for ident, name in params.items():
        v = ev.values[ident]
        assert np.all(v == v[0, 0])
        values.setdefault(name, int(v[0, 0]))
        assert values[name] == int(v[0, 0]), name
        result['nodes'][ident].update(op='input', args=[preg[name], 'x'])
    return result, {preg[k]: (k, v) for k, v in values.items()}

class Unsupported(Exception):
    pass


GEOMETRY = GEO


def mpc_cb(n, state_id, overrides, mpc_root):
    matches = []
    for path in sorted(Path(mpc_root).rglob("*.json")):
        export = json.loads(path.read_text(encoding="utf-8"))["Exports"][0]
        prop = export["Properties"]
        if prop.get("StateId") == state_id:
            if "Base" in prop:
                raise Unsupported("mpc_base")
            matches.append((path, export, prop))
    if len(matches) != 1:
        raise Unsupported("mpc_identity:" + str(state_id))
    path, export, prop = matches[0]
    scalars = prop["ScalarParameters"]
    values = [overrides.get(s["ParameterName"], s["DefaultValue"]) for s in scalars]
    nv = math.ceil(len(values) / 4)
    flat = np.zeros((nv + len(prop.get("VectorParameters", []))) * 4, np.float32)
    flat[:len(values)] = values
    for j, vector in enumerate(prop.get("VectorParameters", [])):
        flat[(nv+j)*4:(nv+j+1)*4] = [vector["DefaultValue"][c] for c in "RGBA"]
    result = np.zeros((n,4),np.float32)
    count = min(n, len(flat)//4)
    result[:count] = flat.reshape(-1,4)[:count]
    name = path.stem
    return result, dict(collection=str(path), name=name, scalars=[s["ParameterName"] for s in scalars],
                        values={s["ParameterName"]:float(v) for s,v in zip(scalars,values)})


def param_value(name, value_set, mpc_meta):
    geometry = GEOMETRY[value_set["geometry"]]
    if name.startswith("dr_camneg_"):
        return -geometry["C"]["xyz".index(name[-1])]
    if name.startswith("dr_camfwd_"):
        return geometry["F"]["xyz".index(name[-1])]
    if name.startswith("dr_cam_"):
        return geometry["C"]["xyz".index(name[-1])]
    if name.startswith("dr_l2w_") or name.startswith("dr_w2l_"):
        _,_,r,c = name.split("_")
        return float(int(r) == "xyzw".index(c))
    if name.startswith("dr_cpd_"):
        return value_set["cpd"].get(int(name[7:]),0.0)
    for meta in mpc_meta.values():
        prefix = "dr_mpc_" + meta["name"] + "_"
        if name.startswith(prefix):
            for scalar, value in meta["values"].items():
                if prefix + re.sub(r"[^A-Za-z0-9_]", "_",scalar) == name:
                    return value_set["mpc"].get(scalar,value)
    raise Unsupported("param_value:" + name)


def find_params(dag, shader, bindings, ev, mpc_meta):
    cbslot = {int(k):v for k,v in bindings["cb"].items()}
    scene_t = {int(k) for k,v in bindings["t"].items() if v["ub"]=="Scene"}
    params = {}
    for node in dag["nodes"]:
        name = None
        if node["op"] == "cbload":
            m = node["args"][-1]
            if m["dynamic"]:
                raise Unsupported("dynamic_cbload")
            ub = cbslot[m["slot"]]
            if ub == "View":
                member,element = view_member(m["offset"]*16 + m["component"]*4)
                if member in VIEW_LIVE:
                    name = VIEW_LIVE[member] + "_" + "xyz"[element]
            elif ub.startswith("MaterialCollection"):
                meta = mpc_meta[ub]
                index = m["offset"]*4 + m["component"]
                if index >= len(meta["scalars"]):
                    raise Unsupported("vector MPC read")
                name = "dr_mpc_" + meta["name"] + "_" + re.sub(r"[^A-Za-z0-9_]","_",meta["scalars"][index])
        elif node["op"] == "sbload" and node["args"][-1]["slot"] in scene_t:
            element = int(ev.values[node["args"][0]][0,0])
            component = int(ev.values[node["args"][1]][0,0])//4 + node["args"][-1]["component"]
            if not np.all(ev.values[node["args"][0]]==element):
                raise Unsupported("nonuniform Scene element")
            if 2 <= element <= 4:
                name = "dr_l2w_%d_%s" % (element-2,"xyzw"[component])
            elif 5 <= element <= 7:
                name = "dr_w2l_%d_%s" % (element-5,"xyzw"[component])
            elif 35 <= element <= 43:
                name = "dr_cpd_%d" % ((element-35)*4+component)
        if name:
            params[node["id"]] = name
    return params


def fold_live(dag, shader, fixture, params):
    folded, table = _fold_live(dag, shader, fixture, params)
    return folded, [dict(preg=reg,name=name,bits=bits) for reg,(name,bits) in sorted(table.items())]


def inject(ev, table):
    for item in table:
        ev.inputs[item["preg"]] = np.full(ev.shape+(4,),item["bits"],np.uint32)


def live_dag(x4, shader, fixture, bindings, mpc_meta, value_set):
    ev = ssa.Evaluator(shader,fixture)
    ev.evaluate(x4)
    params = find_params(x4,shader,bindings,ev,mpc_meta)
    folded,table = fold_live(x4,shader,fixture,params)
    for item in sorted(table,key=lambda item:item["name"]):
        actual = np.asarray(item["bits"],dtype=np.uint32).view(np.float32)
        expected = np.float32(param_value(item["name"],value_set,mpc_meta))
        if actual != expected:
            raise Unsupported("param_value:"+item["name"])
    injected = ssa.Evaluator(shader,fixture)
    inject(injected,table)
    optimized,stats = opt.optimize(folded,injected)
    return optimized,table,stats
