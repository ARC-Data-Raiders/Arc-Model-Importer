"""Niagara ribbon geometry generation (spec N-O5b): a port of FNiagaraRendererRibbons CPU generation (UE 5.7).

Runs on Blender-space particle rows (cache stream, already converted); UE length constants are scaled by 0.01. Generation has no
cross products, so the Y mirror commutes with it. View-dependent parts (screen facing, right vector, twist, width offset) are left
to the GN rig / niagara_render_ref.ribbon_vertices.
"""
import math

import numpy as np

UNIT = 0.01
MIN_SEGMENT_LENGTH = 1.0 * UNIT          # GNiagaraRibbonMinSegmentLength (cm)
MIN_DISPLACEMENT_ERROR = 0.5 * UNIT      # GNiagaraRibbonTessellationMinDisplacementError (cm)
MAX_TESSELLATION = 16                    # GNiagaraRibbonMaxTessellation
DEFAULT_MIN_ANGLE = 15.0 * math.pi / 180.0
SMALL_NUMBER = 1e-8
MAX_CURVATURE_FACTOR = 0.002
UV_DEFAULT = {'DistributionMode': 'ScaledUsingRibbonSegmentLength', 'LeadingEdgeMode': 'Locked', 'TrailingEdgeMode': 'Locked',
              'bEnablePerParticleUOverride': False, 'bEnablePerParticleVRangeOverride': False, 'TilingLength': 100.0,
              'Offset': [0.0, 0.0], 'Scale': [1.0, 1.0]}


def acos_fast(x):
    """AcosFast in NiagaraRendererRibbons.cpp:192 (Eberly degree 1)."""
    xa = abs(x)
    r = (-0.156583 * xa + 0.5 * math.pi) * math.sqrt(max(0.0, 1.0 - xa))
    return r if x >= 0 else math.pi - r


def slice_vertices(props):
    """Slice vertex table (position xy, normal xy, texture v) and triangle-pair vertex ids (NiagaraRendererRibbons.cpp 1258-1420)."""
    shape = props.get('Shape', 'Plane')
    w = int(props.get('WidthSegmentationCount', 1))
    verts, pairs = [], []

    def rot(v, deg):
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)

    if shape == 'MultiPlane':
        n, accurate = int(props.get('MultiPlaneCount', 2)), bool(props.get('bEnableAccurateGeometry', False))
        for side in ((1,) if not accurate else (1, -1)):
            for p in range(n):
                ang = p / n * 180.0
                for i in range(w + 1):
                    verts.append(rot((i / w - 0.5, 0.0), ang) + rot((0.0, float(side)), ang) + (i / w,))
        front = n * (w + 1)
        for p in range(n):
            base = p * (w + 1)
            pairs += [(base + i, base + i + 1) for i in range(w)]
            if accurate:
                pairs += [(front + base + i + 1, front + base + i) for i in range(w)]
    elif shape == 'Tube':
        t = int(props.get('TubeSubdivisions', 3))
        for i in range(t + 1):
            ang = i / t * -360.0
            verts.append(rot((-0.5, 0.0), ang) + rot((-1.0, 0.0), ang) + (i / t,))
        pairs = [(i, i + 1) for i in range(t)]
    elif shape == 'Plane':
        for i in range(w + 1):
            verts.append((i / w - 0.5, 0.0, 0.0, 1.0, i / w))
        pairs = [(i, i + 1) for i in range(w)]
    else:
        raise ValueError('ribbon_shape_unsupported:' + shape)
    return verts, pairs


def link_order(cols, props):
    """Particle order along the ribbon (NiagaraRibbonRendererProperties.cpp 410-424, NiagaraRendererRibbons.cpp 743-752)."""
    n = len(cols['x'])
    if props.get('link_order_source') == 'RibbonLinkOrder':
        return np.argsort(cols['linkorder'], kind='stable')
    if props.get('link_order_source') == 'UniqueID':
        return np.argsort(-cols['uid'].astype(np.int64), kind='stable')
    return np.argsort(cols['nage'], kind='stable') if n else np.zeros(0, int)


class Smoothing:
    """FNiagaraRendererRibbons::TessellationSmoothingData, carried frame to frame."""

    def __init__(self):
        self.angle = self.curvature = self.twist_angle = self.twist_curvature = self.total = 0.0


def ribbon_part(P, twist, width, tension, want_tess, has_twist, stats):
    """GenerateVertexBufferForRibbonPart for one ribbon: returns (kept row indices in order, tangents [K,3], distances [K])."""
    idx, tangents, dists = [], [], []
    n = len(P)
    if n < 2:
        return idx, np.zeros((0, 3)), np.zeros(0)
    first = P[0]
    curr = first
    last_vec = np.zeros(3)
    last_size = 0.0
    last_twist = last_width = 0.0
    total = 0.0
    ci = 1
    while ci < n:
        curr = P[ci]
        last_vec = curr - first
        last_size = float(np.linalg.norm(last_vec))
        if has_twist:
            last_twist, last_width = twist[ci], width[ci]
        if last_size > MIN_SEGMENT_LENGTH:
            last_vec = last_vec * (1.0 / last_size)
            idx.append(0)
            tangents.append(last_vec.copy())
            dists.append(0.0)
            break
        last_size = 0.0
        ci += 1
    ni = ci + 1
    while ni < n:
        nxt = P[ni]
        vec = nxt - curr
        size = float(np.linalg.norm(vec))
        nt = nw = 0.0
        if has_twist:
            nt, nw = twist[ni], width[ni]
        if size > MIN_SEGMENT_LENGTH or ni == n - 1:
            vec = vec * (1.0 / max(MIN_SEGMENT_LENGTH, size))
            s = last_vec + vec
            L = float(np.linalg.norm(s))
            tangent = (1.0 - tension) * (s / L if L * L > SMALL_NUMBER else np.zeros(3))  # GetSafeNormal: |v|^2 > SMALL_NUMBER
            total += last_size
            idx.append(ci)
            tangents.append(tangent)
            dists.append(total)
            stats['total'] += size
            if want_tess:
                stats['len'] += size * size
                stats['angle'] += size * acos_fast(float(np.clip(np.dot(last_vec, vec), -1.0, 1.0)))
                if has_twist:
                    stats['twist'] += size * abs(nt - last_twist)
                    stats['width'] += size * last_width
            ci = ni
            curr = nxt
            last_vec = vec
            last_size = size
            last_twist, last_width = nt, nw
        ni += 1
    if last_size > 0:
        total += last_size
        idx.append(ci)
        tangents.append(last_vec.copy())
        dists.append(total)
    T = np.array(tangents, dtype=np.float64).reshape(-1, 3)
    if len(idx) > 2:
        T[0] = 2.0 * np.dot(T[0], T[1]) * T[0] - T[1]
        T[-1] = 2.0 * np.dot(T[-1], T[-2]) * T[-1] - T[-2]
    return idx, T, np.array(dists, dtype=np.float64)


def uv_scale_offset(settings, nage_sorted, dists, num_segments, total):
    """CalculateUVScaleAndOffsets (NiagaraRendererRibbons.cpp 1451-1544); lengths in Blender units (TilingLength scaled)."""
    lead = 0.0
    if settings['LeadingEdgeMode'] == 'SmoothTransition':
        step = nage_sorted[1] - nage_sorted[0]
        off = step - nage_sorted[0] if nage_sorted[0] < step else 0.0
        lead = off / step if step > 0 else 0.0
    trail = 0.0
    if settings['TrailingEdgeMode'] == 'SmoothTransition':
        step = nage_sorted[-1] - nage_sorted[-2]
        off = step - (1 - nage_sorted[-1]) if 1 - nage_sorted[-1] < step else 0.0
        trail = off / step if step > 0 else 0.0
    mode = settings['DistributionMode']
    tiling = float(settings['TilingLength']) * UNIT
    if mode == 'ScaledUniformly':
        avail = num_segments - (lead + trail)
        scale = num_segments / avail
        offset = -((lead / num_segments) * scale)
        scaler = 1.0 / num_segments
    elif mode == 'ScaledUsingRibbonSegmentLength':
        lead_d = dists[1] * lead
        trail_d = (dists[-1] - dists[-2]) * trail
        avail = total - (lead_d + trail_d)
        scale = total / avail
        offset = -((lead_d / total) * scale)
        scaler = 1.0 / total
    elif mode == 'TiledOverRibbonLength':
        scale = total / tiling
        offset = -((dists[1] * lead) / tiling)
        scaler = 1.0 / total
    elif mode == 'TiledFromStartOverRibbonLength':
        scale = total / tiling
        offset = 0.0
        scaler = 1.0 / total
    else:
        raise ValueError('ribbon_uv_mode:' + mode)
    sx, ox = float(settings['Scale'][0]), float(settings['Offset'][0])
    return scale * sx, offset * sx + ox, scaler


def tessellation_factor(props, sm):
    """CalculateTessellationFactor with ViewDistance = 0 (MaxDisplacementError = its minimum; spec N-O5 section 16)."""
    mode = props.get('TessellationMode', 'Automatic')
    factor, min_angle, constant = MAX_TESSELLATION, DEFAULT_MIN_ANGLE, False
    if mode == 'Custom':
        factor = min(factor, int(props.get('TessellationFactor', 16)))
        constant = bool(props.get('bUseConstantFactor', False))
        a = float(props.get('TessellationAngle', 15.0))
        min_angle = (1.0 if 0.0 < a < 1.0 else a) * math.pi / 180.0
    elif mode == 'Disabled':
        factor = 1
    if constant:
        return factor
    if factor > 1 and sm.curvature > SMALL_NUMBER:
        min_tess = float(factor) if min_angle == 0.0 else max(1.0, max(sm.twist_angle, sm.angle) / max(SMALL_NUMBER, min_angle))
        err = MIN_DISPLACEMENT_ERROR
        tess = sm.angle / max(MAX_CURVATURE_FACTOR, acos_fast(sm.curvature / (sm.curvature + err)))
        if sm.twist_angle > 0 and sm.twist_curvature > 0:
            tess = max(tess, sm.twist_angle / max(MAX_CURVATURE_FACTOR, acos_fast(sm.twist_curvature / (sm.twist_curvature + err))))
        return int(min(max(int(math.floor(tess + 0.5)), int(math.floor(min_tess + 0.5))), factor))
    return 1


def generate(cols, props, sm):
    """One frame: returns dict of per-vertex arrays (Blender space) and quad faces, or None when nothing is drawn. Updates sm."""
    keep = np.nonzero(cols['nx_valid'] > 0.5)[0]
    c = {k: v[keep].astype(np.float64) for k, v in cols.items()}
    n = len(keep)
    mode = props.get('TessellationMode', 'Automatic')
    constant = mode == 'Custom' and bool(props.get('bUseConstantFactor', False))
    want_tess = mode != 'Disabled' and not constant
    has_twist = bool(props.get('twist_bound'))
    tension = float(props.get('CurveTension', 0.0))
    groups = [np.arange(n)]
    if props.get('ribbon_id_bound') and n:
        # TMap keyed by FNiagaraID (or int), KeySort(TLess): Index, then AcquireTag
        ids = np.stack([c['rid_0'], c['rid_1']], axis=1)
        keys = sorted({(a, b) for a, b in ids})
        groups = [np.nonzero((ids[:, 0] == a) & (ids[:, 1] == b))[0] for a, b in keys]
    stats = {'total': 0.0, 'len': 0.0, 'angle': 0.0, 'twist': 0.0, 'width': 0.0}
    ribbons = []
    for g in groups:
        sub = {k: v[g] for k, v in c.items()}
        order = link_order(sub, props)
        rows = g[order]
        P = np.stack([c['x'][rows], c['y'][rows], c['z'][rows]], axis=1)
        kept, T, D = ribbon_part(P, c['twist'][rows], c['width'][rows], tension, want_tess, has_twist, stats)
        if len(kept) >= 2:
            ribbons.append((rows[np.array(kept)], T, D))
    if stats['total'] > 0.0:
        inv = 1.0 / max(1.0 * UNIT, stats['total'])
        avg_factor = sm.total / (stats['total'] + sm.total)
        sm.total = stats['total']
        ang = stats['angle'] * inv
        seg = stats['len'] * inv
        curv = seg / max(SMALL_NUMBER, abs(math.sin(ang)))
        sm.angle = ang + (max(sm.angle, ang) - ang) * avg_factor
        sm.curvature = curv + (max(sm.curvature, curv) - curv) * avg_factor
        if has_twist:
            tw, wd = stats['twist'] * inv, stats['width'] * inv
            sm.twist_angle = tw + (max(sm.twist_angle, tw) - tw) * avg_factor
            sm.twist_curvature = wd + (max(sm.twist_curvature, wd) - wd) * avg_factor
    else:
        sm.__init__()
    if not ribbons:
        return None
    nsub = tessellation_factor(props, sm) if (want_tess or constant) else 1
    sv, pairs = slice_vertices(props)
    uv_settings = [dict(UV_DEFAULT, **(props.get('UV0Settings') or {})), dict(UV_DEFAULT, **(props.get('UV1Settings') or {}))]
    out = {k: [] for k in ('center', 'dir', 'facing', 'twist', 'width', 'slice', 'col', 'dyn0', 'dyn1', 'dyn2', 'dyn3', 'nage', 'matrand', 'uv0', 'uv1', 'interp')}
    faces = []
    base = 0
    for rows, T, D in ribbons:
        nseg = len(rows) - 1
        total = D[-1]
        nage_sorted = c['nage'][rows]
        uvp = []
        for k, st in enumerate(uv_settings):
            override = st.get('bEnablePerParticleUOverride') and props.get('u%d_override_bound' % k)
            uvp.append((1.0, 0.0, 1.0) if override else uv_scale_offset(st, nage_sorted, D, nseg, total))
        interp = [(s, a / nsub) for s in range(nseg) for a in range(nsub)] + [(nseg, 0.0)]
        for s, alpha in interp:
            i0, i1 = s, min(s + 1, nseg)
            r0, r1 = rows[i0], rows[i1]
            P0 = np.array([c['x'][r0], c['y'][r0], c['z'][r0]])
            P1 = np.array([c['x'][r1], c['y'][r1], c['z'][r1]])
            ts = D[i1] - D[i0]
            a2, a3 = alpha * alpha, alpha * alpha * alpha
            center = (2 * a3 - 3 * a2 + 1) * P0 + ts * (a3 - 2 * a2 + alpha) * T[i0] + ts * (a3 - a2) * T[i1] + (-2 * a3 + 3 * a2) * P1
            d = T[i0] + (T[i1] - T[i0]) * alpha
            dl = float(np.linalg.norm(d))
            direction = d / dl if dl > 0 else np.zeros(3)
            lerp = lambda key, w=None: c[key][r0] + (c[key][r1] - c[key][r0]) * alpha
            vec = lambda p, m: np.array([lerp('%s_%d' % (p, j)) for j in range(m)])
            dist_on = D[i0] + (D[i1] - D[i0]) * alpha
            us = []
            for k, st in enumerate(uv_settings):
                scale, offset, scaler = uvp[k]
                if st.get('bEnablePerParticleUOverride') and props.get('u%d_override_bound' % k):
                    u = lerp('u%dovr' % k)
                elif st['DistributionMode'] == 'ScaledUniformly':
                    u = (s + alpha) * scaler
                elif st['DistributionMode'] in ('ScaledUsingRibbonSegmentLength', 'TiledOverRibbonLength'):
                    u = dist_on * scaler
                elif props.get('uvdist_bound'):
                    u = lerp('uvdist') * scaler
                else:
                    u = 0.0
                us.append((u, scale, offset, k))
            for (px, py, nx, ny, tv) in sv:
                out['center'].append(center)
                out['dir'].append(direction)
                out['facing'].append(vec('facing', 3))
                out['twist'].append(lerp('twist'))
                out['width'].append(lerp('width'))
                out['slice'].append((px, py, nx, ny))
                out['col'].append(vec('col', 4))
                for k in range(4):
                    out['dyn%d' % k].append(vec('dyn%d' % k, 4))
                out['nage'].append(lerp('nage'))
                out['matrand'].append(lerp('matrand'))
                for (u, scale, offset, k) in us:
                    st = uv_settings[k]
                    v = tv
                    if st.get('bEnablePerParticleVRangeOverride') and props.get('v%d_range_bound' % k):
                        lo, hi = lerp('v%drng_0' % k), lerp('v%drng_1' % k)
                        v = (1 - tv * lo) + tv * hi
                    out['uv%d' % k].append((u * scale + offset, v * float(st['Scale'][1]) + float(st['Offset'][1])))
                out['interp'].append((s, alpha))
        nslice = len(sv)
        for j in range(len(interp) - 1):
            a0, a1 = base + j * nslice, base + (j + 1) * nslice
            for f, s2 in pairs:
                faces.append((a0 + f, a0 + s2, a1 + s2, a1 + f))
        base += len(interp) * nslice
    res = {k: np.array(v, dtype=np.float64) for k, v in out.items()}
    res['faces'] = np.array(faces, dtype=np.int64).reshape(-1, 4)
    res['nsub'] = nsub
    return res


MESH_PROPS = ['x', 'y', 'z', 'dir_0', 'dir_1', 'dir_2', 'facing_0', 'facing_1', 'facing_2', 'twist', 'width', 'sl_px', 'sl_py', 'sl_nx', 'sl_ny',
              'col_0', 'col_1', 'col_2', 'col_3'] + ['dyn%d_%d' % (k, j) for k in range(4) for j in range(4)] + ['nage', 'matrand', 'uv0_0', 'uv0_1', 'uv1_0', 'uv1_1']


def mesh_ply_bytes(mesh):
    """Binary PLY with per-vertex MESH_PROPS and quad faces; an empty frame is a single degenerate sentinel vertex with no faces."""
    if mesh is None:
        rows = np.zeros((1, len(MESH_PROPS)), dtype='<f4')
        faces = np.zeros((0, 4), dtype=np.int64)
    else:
        rows = np.concatenate([mesh['center'], mesh['dir'], mesh['facing'], mesh['twist'][:, None], mesh['width'][:, None], mesh['slice'], mesh['col'],
                               mesh['dyn0'], mesh['dyn1'], mesh['dyn2'], mesh['dyn3'], mesh['nage'][:, None], mesh['matrand'][:, None], mesh['uv0'], mesh['uv1']],
                              axis=1).astype('<f4')
        faces = mesh['faces']
    head = ('ply\nformat binary_little_endian 1.0\nelement vertex %d\n' % len(rows) + ''.join('property float %s\n' % p for p in MESH_PROPS)
            + 'element face %d\nproperty list uchar int vertex_indices\nend_header\n' % len(faces)).encode('ascii')
    packed = np.zeros(len(faces), dtype=[('n', 'u1'), ('i', '<i4', (4,))])
    packed['n'] = 4
    packed['i'] = faces
    return head + np.ascontiguousarray(rows).tobytes() + packed.tobytes()


def read_mesh_ply(path):
    from pathlib import Path
    data = Path(path).read_bytes()
    end = data.index(b'end_header\n') + len(b'end_header\n')
    lines = data[:end].decode('ascii').splitlines()
    nv = int(lines[2].split()[2])
    props = [l.split()[2] for l in lines if l.startswith('property float ')]
    nf = int(next(l for l in lines if l.startswith('element face')).split()[2])
    rows = np.frombuffer(data, dtype='<f4', count=nv * len(props), offset=end).reshape(nv, len(props))
    off = end + nv * len(props) * 4
    raw = np.frombuffer(data, dtype=np.uint8, count=nf * 17, offset=off).reshape(nf, 17)
    faces = raw[:, 1:].copy().view('<i4').reshape(nf, 4)
    return {p: rows[:, i].copy() for i, p in enumerate(props)}, faces
