"""Per-material and per-object structural audit records for the prop path.

The rule core is pure Python and takes plain dict facts, so it runs without
Blender. A thin bpy adapter extracts those facts from a built material, its
mesh and the MI record. No image pixels are ever read.
"""
import json
import re
import time
import traceback

try:
    from . import audit_param_rules as rules
except ImportError:  # loaded as a plain module by offline tools
    import audit_param_rules as rules

AUDIT_VERSION = 2
_PENDING = []
STATS = {'audit_s': 0.0, 'calls': 0, 'build_s': 0.0, 'builds': 0}

# Texture-name layout suffixes the material builders know how to unpack:
# derived from the single builder layout table, never listed here.
try:
    from . import texture_layouts as layouts
except ImportError:  # loaded as a plain module by offline tools
    import texture_layouts as layouts
KNOWN_SUFFIXES = layouts.KNOWN_SUFFIXES
OPAQUE = 'opaque'
EMISSIVE_HINT = re.compile(r'emissi|emit|glow', re.I)
_BLACK_DEFAULT = re.compile(r'black', re.I)
_ARRAY_SLICE = re.compile(r'(.+)_layer\d+$')
# NativeRenderLimitationError texts (native_catalog_bridge, native_data, preset_defaults, records).
_RENDER_LIMIT = re.compile(r'(expression|preset) bindings unresolved|deferred expression bindings', re.I)


def exception_message(exc):
    """str(exc), or 'Type: repr' when that is empty or 'None' (KeyError(None), bare raise X())."""
    text = str(exc)
    if text.strip() in ('', 'None'):
        return f'{type(exc).__name__}: {exc!r}'
    return text


TRACEBACK_TAIL = 600


def row_error(where, mrec):
    """Audit-row error entry from a material record (or its prior stage); keeps type + traceback tail."""
    if not mrec:
        return None
    for source in (mrec, mrec.get('prior') or {}):
        if source.get('error'):
            entry = {'where': where, 'error': source['error']}
            if source.get('error_type'):
                entry['error_type'] = source['error_type']
            if source.get('traceback'):
                entry['traceback_tail'] = str(source['traceback'])[-TRACEBACK_TAIL:]
            return entry
    return None


def record_swallowed(where, exc):
    """Record an exception a caller deliberately swallows; the next audit attaches it."""
    entry = {'where': str(where), 'error_type': type(exc).__name__, 'error': str(exc),
             'traceback': ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}
    _PENDING.append(entry)
    print(f"ARC audit: swallowed at {where}: {type(exc).__name__}: {exc}")
    return entry


def drain_swallowed():
    items = list(_PENDING)
    _PENDING.clear()
    return items


def _stem(path):
    text = str(path or '').replace('\\', '/').rsplit('/', 1)[-1]
    text = text.split('.', 1)[0]
    return text.casefold()


def _bare(path):
    if isinstance(path, dict):
        path = path.get('path') or path.get('value') or path.get('name') or ''
    return re.sub(r'_layer\d+$', '', _stem(path))


def _blend_kind(mi):
    raw = str((mi.get('settings') or {}).get('BlendMode', mi.get('blend_mode', '')) or '')
    low = raw.casefold()
    if 'mask' in low or raw == '1':
        return 'masked'
    if any(k in low for k in ('translucent', 'additive', 'modulate', 'alphacomposite', 'alphaholdout')) or raw in {'2', '3', '4', '5', '6'}:
        return 'translucent'
    return OPAQUE


def _textures(mi):
    tex = mi.get('textures') or {}
    if isinstance(tex, dict):
        return [(str(k), v) for k, v in tex.items() if v]
    out = []
    for item in tex:
        if isinstance(item, dict):
            k, v = item.get('name'), item.get('path', item.get('value'))
        else:
            k, v = item
        if k and v:
            out.append((str(k), v))
    return out


def _scalars(mi):
    return dict(mi.get('scalars') or {})


def _colours(mi):
    return dict(mi.get('colors') or mi.get('colours') or {})


def _emissive_default_off(mi, facts):
    """True when no emissive value is positive and no emissive texture is MI-set."""
    def positive(v):
        if isinstance(v, (int, float)):
            return float(v) > 0
        if isinstance(v, dict):
            return any(float(v.get(c, 0) or 0) > 0 for c in 'RGB')
        if isinstance(v, (list, tuple)):
            return any(isinstance(x, (int, float)) and x > 0 for x in list(v)[:3])
        return False
    values = [v for k, v in list(_scalars(mi).items()) + list(_colours(mi).items()) if EMISSIVE_HINT.search(k)]
    tex = facts.get('textures') or {}
    em_tex = [(k, v) for k, v in _textures(mi) if EMISSIVE_HINT.search(k)]
    set_tex = [k for k, _ in em_tex if (tex.get(rules.canonical(k)) or {}).get('overridden') is not False]
    # R16: emissive texture left at a black master default: intensity x black = 0.
    # approximate: 'black' in the default texture name read as a zero texture.
    if em_tex and not set_tex and all(_BLACK_DEFAULT.search(str(v).rsplit('/', 1)[-1]) for _, v in em_tex):
        return True
    if any(positive(v) for v in values):
        return False
    return not set_tex or bool(values)


def _clip(mi):
    for key in ('opacity_clip',):
        if mi.get(key) is not None:
            return float(mi[key])
    settings = mi.get('settings') or {}
    for key in ('OpacityMaskClipValue', 'opacity_mask_clip_value'):
        if settings.get(key) is not None:
            return float(settings[key])
    return 1.0 / 3.0


def audit_material_facts(graph, mi, build_report=None, mesh=None, error=None, swallowed=(), master=None,
                         master_error=None):
    """Pure rule core. Returns the record dict.

    ``master`` is the root master record (same flat shape as ``mi``). With it,
    a texture whose value equals the root default is "inherited"; see
    ``audit_param_rules`` for the classes and severities.
    """
    graph = graph or {}
    mi = mi or {}
    report = build_report or {}
    mesh = mesh or {}
    flags, details = set(), {}

    def flag(name, detail=None):
        flags.add(name)
        if detail is not None:
            details.setdefault(name, []).append(detail)

    note = str((mi or {}).get('resolution_note') or '')
    if note.startswith('engine_helper_material'):
        flag('engine_helper_material', note)
    images = list(graph.get('images') or ())
    by_stem = {}
    slice_bases = set()
    for img in images:
        for s in {_stem(img.get('filepath')), _stem(img.get('stem'))} - {''}:
            by_stem.setdefault(s, []).append(img)
            # R24: a Texture2DArray param binds one exported slice (<array>_LAYERn);
            # the slice links the array param. The slice index stays unproven.
            base = _ARRAY_SLICE.match(s)
            if base and img not in by_stem.get(base[1], ()):
                by_stem.setdefault(base[1], []).append(img)
                slice_bases.add(base[1])
    skipped = {str(k): v for k, v in dict(report.get('skipped_textures') or {}).items()}
    unset = {str(n) for n in report.get('unset_textures') or ()}
    if unset:
        # Carry the builder's stand-in list into the stored record so audit rows keep it.
        flags.add('unset_textures')
        details['unset_textures'] = sorted(unset)
    missing_refs = [str(m.get('reference', m) if isinstance(m, dict) else m) for m in report.get('missing_textures') or ()]
    missing_stems = {_stem(m) for m in missing_refs}
    sev = {}
    master_record = master if master is not None else mi.get('_audit_master')
    if master_record is None and str(master_error or '').strip() == 'MI is its own root':
        # R33: the slot material is a base UMaterial; its own params are the
        # master defaults (nothing overridden), not a failed master fetch.
        master_record, master_error = mi, None
    facts = rules.param_facts(_flat_to_dict(mi), _flat_to_dict(master_record) if master_record is not None else None)
    if master_record is None and master_error:
        flag('master_unavailable', str(master_error))
        sev['master_unavailable'] = rules.INFO
    layout_users = {}
    named_refs = {str(r) for p, r in _canonical_textures(mi) if str(p).casefold() != _stem(r)}
    for param, ref in _canonical_textures(mi):
        stem = _stem(ref)
        hits = by_stem.get(stem, [])
        if param in skipped or ('Global.' + param) in skipped:
            flag(f'tex_param_skipped:{param}', skipped.get(param, skipped.get('Global.' + param)))
            continue
        if param in unset or ('Global.' + param) in unset:
            # Builder sampled the neutral stand-in (unset_textures carries it); never tex_param_unlinked.
            continue
        if stem in missing_stems or (hits and not any(h.get('has_image') for h in hits)):
            flag(f'image_missing:{param}', str(ref))
            continue
        linked = any(h.get('linked') for h in hits)
        if linked and stem in slice_bases:
            flag(f'array_slice_approximate:{param}', f'{ref} sampled at an unproven slice')
        param_sev = rules.DEFECT
        if not linked:
            name, param_sev, detail = rules.classify_unlinked(param, facts)
            flag(name, detail)
            sev[name] = max(sev.get(name, rules.INFO), param_sev, key=_SEV_RANK.get)
        for h in hits:
            if not h.get('linked'):
                # Unlinked inspection nodes (common._dump_unconnected_tex) never reach the
                # output; Blender loads them as default sRGB. tex_param_unlinked owns them.
                continue
            want = h.get('sidecar_srgb')
            cs = str(h.get('colorspace') or '')
            image = str(h.get('stem') or _stem(h.get('filepath')) or '')
            if want is True and cs != 'sRGB':
                flag(f'colorspace_mismatch:{param}', {'image': image, 'expected': 'sRGB', 'actual': cs})
            elif want is False and not any(k in cs for k in ('Non-Color', 'Linear', 'Raw')):
                flag(f'colorspace_mismatch:{param}', {'image': image, 'expected': 'linear', 'actual': cs})
        suffix = layouts.layout_code(ref)
        # R23: a texture-stem pseudo-param (referenced-texture list entry) repeats a
        # texture a named param already carries; the named param's layout decides.
        if suffix and str(param).casefold() == stem and str(ref) in named_refs:
            suffix = ''
        if suffix and not layouts.is_known(param, suffix):
            name = f'layout_unknown:{suffix}'
            flag(name, {'param': param, 'texture': str(ref), 'suffix': suffix})
            # Severity follows the param carrying the texture (skipped layer,
            # unsampled inherited default -> info).
            used = rules.layout_severity(param, facts, linked, param_sev)
            layout_users[name] = max(layout_users.get(name, rules.INFO), used, key=_SEV_RANK.get)
    sev.update(layout_users)

    kind = _blend_kind(mi)
    method = str(graph.get('render_method') or '')
    alpha = bool(graph.get('alpha_linked'))
    if graph:
        if kind == OPAQUE and method == 'BLENDED':
            flag('blend_mismatch', f'MI opaque, material {method}')
        if kind == 'translucent' and method != 'BLENDED' and not alpha:
            flag('blend_mismatch', f'MI translucent, material {method} without alpha')
        if kind != OPAQUE and not alpha:
            const = graph.get('alpha_constant')
            if kind == 'translucent' and const is not None and float(const) < 0.999:
                # Builder set the BSDF Alpha from an authored opacity scalar.
                flag('opacity_constant', f'{float(const):.3f}')
                sev['opacity_constant'] = rules.INFO
            elif rules.opacity_runtime_reason(facts.get('master')):
                flag('opacity_runtime', rules.opacity_runtime_reason(facts.get('master')))
            else:
                flag('opacity_unwired', kind)
        threshold = graph.get('alpha_clip_threshold')
        if kind == 'masked' and threshold is not None and abs(float(threshold) - _clip(mi)) > 0.01:
            flag('clip_mismatch', f'MI {_clip(mi):.4f}, graph {float(threshold):.4f}')
        if kind == OPAQUE and threshold is not None:
            flag('clip_mismatch', f'MI opaque, graph clip {float(threshold):.4f}')

    notes = ' '.join(str(x) for x in list(report.get('notes') or ()) + list(report.get('warnings') or ()))
    text = f'{notes} {error or ""}'
    for n in re.findall(r'UV channel unavailable:\s*(\d+)', text):
        flag(f'uv_missing:{n}', 'builder')
    uv_layers = list(mesh.get('uv_layers') or ())
    for uv in graph.get('uv_maps_used') or ():
        if uv and uv_layers and uv not in uv_layers:
            flag(f'uv_missing:{uv}', 'graph names absent UV map')
    for n in re.findall(r'UV(\d+) clamped', text):
        flag(f'uv_clamped:{n}', 'builder')

    emissive = any(EMISSIVE_HINT.search(p) for p, _ in _textures(mi))
    emissive = emissive or any(EMISSIVE_HINT.search(k) and float(v or 0) > 0 for k, v in _scalars(mi).items()
                               if isinstance(v, (int, float)))
    emissive = emissive or any(EMISSIVE_HINT.search(k) and isinstance(v, dict) and any(float(v.get(c, 0) or 0) > 0 for c in 'RGB')
                               for k, v in _colours(mi).items())
    # Emissive static switches ('Use Emissive', 'Use CR as Emissive', ...) gate the
    # emissive branch; WPO toggles do not enable it. All present and False: the
    # master never emits, so an unlinked emission socket is correct.
    em_switches = [v.get('value') for k, v in facts['switches'].items()
                   if EMISSIVE_HINT.search(k) and not re.search(r'world ?position', k, re.I)]
    if graph and emissive and not graph.get('emission_linked'):
        if em_switches and not any(bool(v) for v in em_switches):
            flag('emissive_gated_off', 'emissive switches off')
            sev['emissive_gated_off'] = rules.INFO
        elif not em_switches and _emissive_default_off(mi, facts):
            # No emissive switch; the trigger is an inherited texture default or an
            # emissive colour/intensity at 0. approximate: master default read as off.
            flag('emissive_default_off', 'no switch; emissive default or zero intensity')
            sev['emissive_default_off'] = rules.INFO
        elif rules.emissive_runtime_reason(facts.get('master')):
            flag('emissive_runtime', rules.emissive_runtime_reason(facts.get('master')))
        else:
            flag('emissive_unwired')
    have = set(mesh.get('color_attributes') or ())
    for name in graph.get('vertex_colour_attrs') or ():
        if name not in have:
            flag('vertex_colour_missing', name or '(default)')

    # R13 checker gaps (old-audit sanity.py parity).
    if graph and graph.get('has_nodes', True) and graph.get('output_linked') is False:
        flag('surface_disconnected', 'Material Output Surface unlinked')
    for img in images:
        if not img.get('has_image'):
            flag('image_node_empty', str(img.get('stem') or '(unnamed)'))
    two_sided = (mi.get('settings') or {}).get('TwoSided', mi.get('two_sided'))
    if graph and bool(two_sided) and graph.get('backface_culling'):
        flag('two_sided_culled', 'MI TwoSided, material culls back faces')
    if graph and images:
        allowed = {_bare(r) for _p, r in _textures(mi)} | {_bare(n) for n in unset}
        if master_record is not None:
            allowed |= {_bare(r) for _p, r in _textures(master_record)}
        # approximate: builder-owned helper images carry no file path; only file-backed
        # samplers whose stem no MI/master texture names count as foreign. R14: shared
        # library textures never count; a known node label whose role matches the stem
        # suffix (colour/normal/data) clears it. approximate: unknown label keeps the flag.
        for img in images:
            name = _bare(img.get('filepath') or img.get('stem'))
            if not (img.get('linked') and img.get('has_image') and img.get('filepath') and name) \
                    or name in allowed or rules.is_shared_library_texture(name, img.get('filepath')) \
                    or not rules.foreign_role_conflict(name, img.get('label')):
                continue
            flag('foreign_texture', str(img.get('stem') or name))

    if error and _RENDER_LIMIT.search(str(error)):
        # Reviewed native catalog lists this master's expression bindings as
        # unresolved (render_limits); the slot keeps the importer's generic
        # material. A known catalog limit, not a crash: gated, never verified.
        flag('native_render_limit', str(error))
        sev['native_render_limit'] = rules.INCONSISTENCY
    elif error:
        flag('build_exception', str(error))
    family = str(mi.get('family') or report.get('family') or '')
    if family == 'fallback' or graph.get('fallback_family'):
        flag('fallback_family', graph.get('fallback_family') or family)
    elif family == 'parameterless':
        # Master exposes no texture parameters: the constant surface is uncertain, not a missing route.
        flag('no_texture_master', str(mi.get('master') or report.get('master') or family))
    elif family == 'engine_default':
        # Engine placeholder master (WorldGridMaterial): neutral grey by design, informational only.
        flag('engine_default_material', str(mi.get('master') or report.get('master') or family))
    master_name = str(mi.get('master') or mi.get('root_master') or mi.get('parent') or report.get('master') or '')
    if 'Unknown master' in notes or (mi and not master_name):
        flag('unknown_master', master_name or '(none)')
    for item in swallowed:
        flag(f"swallowed:{item['where']}", item)
    record = {'version': AUDIT_VERSION, 'kind': 'material', 'material': graph.get('name') or report.get('material', ''),
              'package': mi.get('package', ''), 'family': family, 'master': master_name, 'blend': kind,
              'flags': sorted(flags), 'details': details, 'error': str(error) if error else '',
              'images': len(images), 'textures': len(_canonical_textures(mi)),
              'overridden_source': facts['overridden_source']}
    return apply_severity(record, sev)


_SEV_RANK = {rules.INFO: 0, rules.INCONSISTENCY: 1, rules.DEFECT: 2}


def apply_severity(record, known=None):
    """Attach ``severity`` {flag: defect|inconsistency|info} and per-severity ``counts``."""
    known = dict(known or {})
    old = record.get('severity') or {}
    record['severity'] = {f: known.get(f, old.get(f, rules.severity(f))) for f in record.get('flags', ())}
    record['counts'] = rules.summarize(record['severity'])
    record['gate_count'] = record['counts'][rules.DEFECT] + record['counts'][rules.INCONSISTENCY]
    return record


def _canonical_textures(mi):
    """(canonical param, ref) pairs; ``Global.X`` collapses into ``X``, the unscoped entry wins."""
    out = {}
    for param, ref in _textures(mi):
        canon = rules.canonical(param)
        if canon in out and param.startswith('Global.'):
            continue
        out[canon] = ref
    return list(out.items())


HUGE_BOUNDS_M, TINY_BOUNDS_M, BLOWN_FRACTION = 300.0, 0.005, 0.25


# R37: harness light = sun 3.0 + grey world 0.5 (strength 1). A non-emissive diffuse pixel reaches at most
# albedo x (SUN cos + WORLD) <= ALBEDO_MAX x 3.5 linear, so linear 1.0 is reached by any albedo >= 0.286 facing
# the sun (trim CR mean 0.33 -> 1.17; Concrete_01 0.76 -> 2.67). Only radiance above the lit-albedo ceiling
# needs an emissive (or unclamped multiply) input, so ``hot`` = fraction of prop pixels with every channel above it.
HARNESS_SUN, HARNESS_WORLD, ALBEDO_MAX = 3.0, 0.5, 1.0
LIT_CEILING = ALBEDO_MAX * (HARNESS_SUN + HARNESS_WORLD)


def blown_fraction(rgba, ceiling=LIT_CEILING, alpha_min=0.5):
    """Fraction of prop pixels (alpha > alpha_min) whose every linear RGB channel exceeds ``ceiling``.
    rgba: scene-linear float array (H, W, 4) (EXR read-back). Pure numpy; render_stats should emit it as ``hot``."""
    import numpy as np
    a = np.nan_to_num(np.asarray(rgba, dtype=np.float32), nan=0.0, posinf=1e9, neginf=0.0)
    mask = np.clip(a[..., 3], 0.0, 1.0) > alpha_min
    if not mask.any():
        return 0.0
    return round(float((a[..., :3][mask] > ceiling).all(axis=-1).mean()), 5)


def render_stats_flags(stats):
    """Harness-facing S3 rule: {flag: detail} from render stats.
    ``hot`` (pixels above the lit-albedo ceiling, see blown_fraction) gives the blocking blown_out. Legacy ``blown``
    (every linear channel >= 1.0) sits below the ceiling a plain lit albedo reaches, so it alone is info
    ``bright_clipped`` (display clip, no material evidence)."""
    s = stats or {}
    out = {}
    if not s.get('coverage_px'):
        return out
    if s.get('hot') is not None:
        if float(s['hot']) > BLOWN_FRACTION:
            out['blown_out'] = f"{float(s['hot']):.0%} pixels above lit-albedo ceiling {LIT_CEILING:.1f}"
    elif float(s.get('blown') or 0) > BLOWN_FRACTION:
        out['bright_clipped'] = f"{float(s['blown']):.0%} pixels >= linear 1.0 (below lit ceiling {LIT_CEILING:.1f})"
    return out


BLOWN_MATE_MIN, BLOWN_MATE_CLEAN = 3, 0.05


BLOWN_LIGHT_TOKENS = ('emiss', 'bright', 'intens', 'glow', 'lum', 'exposure', 'albedo', 'basecolor', 'tint',
                      'color', 'colour', 'multiplier', 'strength')
BLOWN_PARAM_TOL = 1.05


def _blown_light_params(params):
    """{name: magnitude} of MI scalars/vectors that can push a pixel to white (emissive, brightness, colour)."""
    out = {}
    for p in params or ():
        name = str(p.get('name') or '')
        if not any(t in name.casefold() for t in BLOWN_LIGHT_TOKENS):
            continue
        v = p.get('value')
        if isinstance(v, (list, tuple)):
            v = max((abs(float(x)) for x in v[:3]), default=0.0)
        try:
            out[name] = abs(float(v))
        except (TypeError, ValueError):
            continue
    return out


def blown_out_by_prop_light(slot_clusters, cluster_mates, own_params=None):
    """R36: blown_out comes from one whole-prop render, so it names no slot material. It is a checker light or
    framing artifact only when, for every slot, the slot's CLUSTER (root, static permutation, texture set, blend)
    renders clean on other props (>= ``BLOWN_MATE_MIN`` mates, median ``blown`` <= ``BLOWN_MATE_CLEAN``) AND the
    prop's own light-raising MI values (emissive, brightness, colour scalars/vectors) do not exceed the largest
    clean mate's by more than ``BLOWN_PARAM_TOL``. Then the material inputs equal clean renders' inputs and the
    clipped pixels are explained by prop geometry under the checker light.
    slot_clusters: cluster keys of the prop's slots. cluster_mates: {cluster: [(blown, params), ...]} of other
    props' direct renders (params: mi_params scalars+vectors list). own_params: {cluster: params} of this prop.
    Returns proof text, or None (keep the flag)."""
    clusters = sorted({c for c in slot_clusters if c})
    if not clusters:
        return None
    own_params = own_params or {}
    proof = []
    for c in clusters:
        mates = list(cluster_mates.get(c, ()))
        vals = sorted(float(b) for b, _ in mates)
        if len(vals) < BLOWN_MATE_MIN:
            return None
        n = len(vals)
        med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
        if med > BLOWN_MATE_CLEAN:
            return None
        clean = [_blown_light_params(p) for b, p in mates if float(b) <= BLOWN_MATE_CLEAN]
        for name, val in _blown_light_params(own_params.get(c)).items():
            top = max((m.get(name, 0.0) for m in clean), default=0.0)
            if val > top * BLOWN_PARAM_TOL + 1e-6:
                return None
        proof.append(f"{c.split('|', 1)[0].rsplit('/', 1)[-1]} median {med:.2f} over {n}, inputs within mates")
    return "blown_out_prop_light: " + "; ".join(proof)


def audit_object_facts(obj, mesh_meta=None, tolerance=0.02):
    """Pure object rule core. obj: name, slots [material name or None], dims, tris."""
    meta = mesh_meta or {}
    flags, details, unchecked = set(), {}, []
    slots = list(obj.get('slots') or ())
    if meta.get('slots') is not None:
        if len(meta['slots']) != len(slots):
            flags.add('slot_count_mismatch')
            details['slot_count_mismatch'] = f"source {len(meta['slots'])}, object {len(slots)}"
    else:
        unchecked.append('slot_count')
    empty = [i for i, s in enumerate(slots) if not s]
    if empty:
        flags.add('slot_unassigned')
        details['slot_unassigned'] = empty
    if meta.get('dims') and obj.get('dims'):
        bad = [(a, b) for a, b in zip(meta['dims'], obj['dims']) if abs(a - b) > tolerance * max(abs(a), abs(b), 1e-6)]
        if bad:
            flags.add('bounds_mismatch')
            details['bounds_mismatch'] = {'source': list(meta['dims']), 'object': list(obj['dims'])}
    else:
        unchecked.append('bounds')
    if obj.get('dims'):
        size = max(abs(float(v)) for v in obj['dims'])
        if size > HUGE_BOUNDS_M:
            key = 'huge_bounds_world' if rules.world_scale_mesh(obj.get('mesh') or obj.get('name')) else 'huge_bounds'
            flags.add(key)
            details[key] = f'{size:.1f} m'
        elif size < TINY_BOUNDS_M:
            flags.add('tiny_bounds')
            details['tiny_bounds'] = f'{size:.4f} m'
    want, got = meta.get('section_tris'), obj.get('slot_tris')
    if want is not None and got is not None and len(want) == len(got):
        wrong = [(i, int(g), int(w)) for i, (g, w) in enumerate(zip(got, want)) if abs(int(g) - int(w)) > max(2, 0.01 * int(w))]
        if wrong:
            flags.add('section_material_mismatch')
            details['section_material_mismatch'] = [{'slot': i, 'object_tris': g, 'source_tris': w} for i, g, w in wrong]
    if meta.get('tris') is not None and obj.get('tris') is not None:
        if int(meta['tris']) != int(obj['tris']):
            flags.add('tri_count_mismatch')
            details['tri_count_mismatch'] = f"source {meta['tris']}, object {obj['tris']}"
    else:
        unchecked.append('tris')
    return {'version': AUDIT_VERSION, 'kind': 'object', 'object': obj.get('name', ''), 'flags': sorted(flags),
            'details': details, 'unchecked': unchecked, 'slots': slots,
            'material_flags': obj.get('material_flags', {})}


# ---- bpy adapter -----------------------------------------------------------

def _walk(tree, seen):
    if tree is None or tree.name in seen:
        return
    seen.add(tree.name)
    for node in tree.nodes:
        yield tree, node
        if node.type == 'GROUP' and getattr(node, 'node_tree', None) is not None:
            yield from _walk(node.node_tree, seen)


def _socket_linked(node, names):
    for s in node.inputs:
        if s.name in names and s.is_linked:
            return True
    return False


def _sidecar(path):
    try:
        from ..textures import authored_srgb_sidecar, authored_linear_sidecar
    except Exception as exc:  # adapter only; never silent
        record_swallowed('material_audit.sidecar_import', exc)
        return None
    try:
        if authored_srgb_sidecar(path):
            return True
        if authored_linear_sidecar(path):
            return False
    except Exception as exc:
        record_swallowed('material_audit.sidecar', exc)
    return None


def graph_facts(mat):
    facts = {'name': mat.name, 'images': [], 'render_method': getattr(mat, 'surface_render_method', ''),
             'alpha_linked': False, 'alpha_clip_threshold': None, 'emission_linked': False,
             'vertex_colour_attrs': [], 'uv_maps_used': [], 'output_linked': False,
             'has_nodes': bool(mat.use_nodes and mat.node_tree is not None),
             'backface_culling': bool(getattr(mat, 'use_backface_culling', False))}
    if mat.get('arc_audit_fallback'):
        facts['fallback_family'] = str(mat['arc_audit_fallback'])
    if not mat.use_nodes or mat.node_tree is None:
        return facts
    for tree, node in _walk(mat.node_tree, set()):
        t = node.type
        if t == 'TEX_IMAGE':
            img = node.image
            path = img.filepath if img is not None else ''
            facts['images'].append({'stem': img.name if img is not None else node.label, 'filepath': path,
                                    'colorspace': img.colorspace_settings.name if img is not None else '',
                                    'linked': any(o.is_linked for o in node.outputs), 'has_image': img is not None,
                                    'label': node.label or '',
                                    'sidecar_srgb': _sidecar(path) if path else None})
        elif t == 'UVMAP' and node.uv_map:
            facts['uv_maps_used'].append(node.uv_map)
        elif t == 'VERTEX_COLOR':
            facts['vertex_colour_attrs'].append(node.layer_name)
        elif t == 'ATTRIBUTE' and getattr(node, 'attribute_type', 'GEOMETRY') == 'GEOMETRY' and node.attribute_name \
                and node.attribute_name.casefold().startswith(('col', 'color', 'vertex')):
            facts['vertex_colour_attrs'].append(node.attribute_name)
        elif t == 'BSDF_PRINCIPLED':
            facts['alpha_linked'] |= _socket_linked(node, {'Alpha'})
            for sock in node.inputs:
                if sock.name == 'Alpha' and not sock.is_linked and float(sock.default_value) < 0.999:
                    prev = facts.get('alpha_constant')
                    facts['alpha_constant'] = min(float(sock.default_value), 1.0 if prev is None else prev)
            facts['emission_linked'] |= _socket_linked(node, {'Emission Color', 'Emission Strength', 'Emission'})
        elif t in ('EMISSION',):
            facts['emission_linked'] |= any(o.is_linked for o in node.outputs)
        elif t in ('MIX_SHADER',) and node.inputs[0].is_linked:
            facts['alpha_linked'] |= any(getattr(n.from_node, 'type', '') == 'BSDF_TRANSPARENT'
                                         for s in node.inputs[1:] for n in s.links)
        elif t == 'OUTPUT_MATERIAL':
            facts['output_linked'] |= _socket_linked(node, {'Surface'})
        elif t == 'MATH' and node.operation in ('GREATER_THAN', 'LESS_THAN') and tree is not mat.node_tree:
            pass
    if 'arc_alpha_clip' in mat:
        facts['alpha_clip_threshold'] = float(mat['arc_alpha_clip'])
    elif hasattr(mat, 'alpha_threshold') and facts['alpha_linked'] and getattr(mat, 'blend_method', '') == 'CLIP':
        facts['alpha_clip_threshold'] = float(mat.alpha_threshold)
    return facts


def mesh_facts(obj):
    if obj is None or getattr(obj, 'type', '') != 'MESH':
        return {}
    return {'uv_layers': [u.name for u in obj.data.uv_layers], 'color_attributes': [a.name for a in obj.data.color_attributes]}


def with_routed_family(build_report, mat):
    """R12: a builder that writes no report (dispatch env route) still has the
    family dispatch routed it to (arc_mi_family); the audit records that
    instead of an empty family, which the ledger reads as 'unknown'."""
    if (build_report or {}).get('family') or mat is None:
        return build_report
    try:
        routed = mat.get('arc_mi_family')
    except Exception:
        routed = None
    if not routed:
        return build_report
    return {**(build_report or {}), 'family': str(routed)}


def stamp_kept_family(mat, family):
    """R12: a kept native material audited earlier with no family (or only the
    dispatch-routed one) takes the kept report's family (native_packed_prop);
    flags are left as they are."""
    if mat is None or not family or not mat.get('arc_audit'):
        return False
    try:
        record = json.loads(mat['arc_audit'])
    except ValueError:
        return False
    current = record.get('family')
    if current and current != mat.get('arc_mi_family'):
        return False  # a builder report named it; only a routed placeholder is replaced
    if current == str(family):
        return False
    record['family'] = str(family)
    _store(mat, record)
    return True


def _store(idblock, record):
    text = json.dumps(record, ensure_ascii=False, default=str)
    try:
        idblock['arc_audit'] = text
    except Exception as exc:
        print(f"ARC audit: could not store record on {getattr(idblock, 'name', idblock)}: {exc}")
    return record


def audit_material(mat, mi_record, mesh_obj=None, build_report=None, error=None, extra=None, master_record=None,
                   master_error=None):
    """Audit one built (or failed) material and store the JSON record on it.

    A material that already carries a record (an earlier build stage) keeps it
    under ``prior`` and its flags are carried into the new record.
    """
    start = time.perf_counter()
    swallowed = drain_swallowed()
    prior = None
    if mat is not None and mat.get('arc_audit'):
        try:
            prior = json.loads(mat['arc_audit'])
        except ValueError:
            prior = {'flags': ['audit_error'], 'raw': str(mat['arc_audit'])}
    try:
        if build_report is None and mat is not None:
            # Native and enemy builders keep their report under their own key.
            for key in ('arc_build_report', 'dr_native_report', 'arc_enemy_build_report'):
                if mat.get(key):
                    build_report = json.loads(mat[key])
                    break
        build_report = with_routed_family(build_report, mat)
        graph = graph_facts(mat) if mat is not None else {}
        record = audit_material_facts(graph, mi_record or {}, build_report, mesh_facts(mesh_obj), error, swallowed,
                                      master=master_record, master_error=master_error)
    except Exception as exc:
        record = {'version': AUDIT_VERSION, 'kind': 'material', 'flags': ['audit_error'] + [f"swallowed:{s['where']}" for s in swallowed],
                  'audit_error': ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                  'error': str(error) if error else '', 'details': {'swallowed': swallowed}}
        if error:
            record['flags'].append('build_exception')
    if extra:
        record.update(extra)
    if prior is not None:
        prior.pop('prior', None)
        record['prior'] = prior
        record['flags'] = sorted(set(record['flags']) | set(prior.get('flags', ())))
        record['severity'] = {**(prior.get('severity') or {}), **(record.get('severity') or {})}
    apply_severity(record)
    if mat is not None:
        record['material'] = mat.name
        _store(mat, record)
    if mesh_obj is not None:
        record['object'] = mesh_obj.name
    STATS['audit_s'] += time.perf_counter() - start
    STATS['calls'] += 1
    return record


def audit_imported_object(obj, source_path):
    from pathlib import Path
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or mat.get('arc_audit'):
            continue
        selected = str(mat.get('arc_mi_path') or '')
        if not selected or not Path(selected).is_file():
            continue
        try:
            from .common import _parse_flat_mi_json
            mi = _parse_flat_mi_json(selected)
            master, master_error = master_for_flat(mi, selected)
            audit_material(mat, mi, obj, master_record=master, master_error=master_error)
        except Exception as exc:
            record_swallowed('audit.imported_material', exc)
    if not obj.get('arc_audit'):
        audit_object(obj, mesh_meta_for(obj, source_path))


def audit_object(obj, mesh_meta=None):
    """Audit one imported object's slots, bounds and triangles; store the JSON record."""
    start = time.perf_counter()
    try:
        facts = {'name': obj.name, 'slots': [s.material.name if s.material else None for s in obj.material_slots],
                 'material_flags': {}, 'material_packages': []}
        for s in obj.material_slots:
            package = s.material.get('arc_package') if s.material is not None else None
            if s.material is not None and s.material.get('arc_audit'):
                try:
                    material_record = json.loads(s.material['arc_audit'])
                    facts['material_flags'][s.material.name] = material_record.get('flags', [])
                    package = package or material_record.get('package')
                except ValueError as exc:
                    facts['material_flags'][s.material.name] = [f'audit_error:{exc}']
            facts['material_packages'].append(package)
        if obj.type == 'MESH':
            facts['dims'] = [float(v) for v in obj.dimensions]
            facts['tris'] = sum(len(p.vertices) - 2 for p in obj.data.polygons)
            per = [0] * len(obj.material_slots)
            for p in obj.data.polygons:
                if p.material_index < len(per):
                    per[p.material_index] += len(p.vertices) - 2
            facts['slot_tris'] = per
        record = audit_object_facts(facts, mesh_meta)
        if obj.type == 'MESH' and mesh_meta and 'path' in mesh_meta and 'lod_index' in mesh_meta:
            record['source_mesh'] = {key: mesh_meta[key] for key in
                                     ('path', 'lod_index', 'slots', 'section_tris', 'tris') if key in mesh_meta}
            record['source_mesh']['imported_section_tris'] = list(facts['slot_tris'])
            record['source_mesh']['imported_tris'] = facts['tris']
            record['source_mesh']['imported_materials'] = list(facts['material_packages'])
    except Exception as exc:
        record = {'version': AUDIT_VERSION, 'kind': 'object', 'object': getattr(obj, 'name', ''), 'flags': ['audit_error'],
                  'audit_error': ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}
    _store(obj, record)
    STATS['audit_s'] += time.perf_counter() - start
    STATS['calls'] += 1
    return record


def psk_counts(path, unit_scale=0.01):
    """Triangle count and point-extent dimensions from PSK chunk headers (no texture reads)."""
    import array
    import struct
    out = {}
    with open(path, 'rb') as fh:
        data = fh.read()
    pos = 0
    while pos + 32 <= len(data):
        cid = data[pos:pos + 20].split(b'\0', 1)[0].decode('ascii', 'replace')
        size, count = struct.unpack_from('<ii', data, pos + 24)
        body = pos + 32
        if cid == 'PNTS0000' and size == 12 and count > 0:
            pts = array.array('f')
            pts.frombytes(data[body:body + 12 * count])
            out['dims'] = [(max(pts[a::3]) - min(pts[a::3])) * unit_scale for a in range(3)]
        elif cid in ('FACE0000', 'FACE3200'):
            out['tris'] = count
        pos = body + max(size, 0) * max(count, 0)
    return out


def mesh_meta_for(obj, psk_path):
    """Source-side facts for audit_object: slot list from the mesh's material JSON, PSK tris and extents."""
    meta = {}
    low = str(psk_path).casefold()
    if low.endswith('.uemodel'):
        try:
            # parse_uemodel is cached by file key; the importer already paid for this parse.
            from ..uemodel_import import parse_uemodel
            _name, _ver, lods = parse_uemodel(psk_path, scale=0.01)
            if lods:
                lod = lods[0]
                meta['slots'] = [[slot, path] for slot, path, _first, _faces in lod.materials]
                meta['section_tris'] = [int(faces) for _slot, _path, _first, faces in lod.materials]
                if len(lod.vertices):
                    span = lod.vertices.max(axis=0) - lod.vertices.min(axis=0)
                    scale = [float(abs(v)) for v in obj.scale]
                    meta['dims'] = [float(d) * s for d, s in zip(span, scale)]
                meta['tris'] = int(len(lod.indices))
                meta['path'] = str(psk_path)
                meta['lod_index'] = 0
        except Exception as exc:
            record_swallowed('material_audit.uemodel', exc)
        return meta
    try:
        from .common import _parse_sk_material_slots, CTX_MAP
        slots = [list(s) for s in _parse_sk_material_slots(psk_path, context=CTX_MAP)]
        # No sibling mesh JSON means no source slot list, not a zero-slot mesh.
        if slots:
            meta['slots'] = slots
    except Exception as exc:
        record_swallowed('material_audit.slots', exc)
    if low.endswith(('.psk', '.pskx')):
        try:
            meta.update(psk_counts(psk_path))
            if 'dims' in meta:
                # obj.dimensions include object scale; PSK points are centimetres (x0.01).
                scale = [float(abs(v)) for v in obj.scale]
                meta['dims'] = [d * s for d, s in zip(meta['dims'], scale)]
        except Exception as exc:
            record_swallowed('material_audit.psk', exc)
    return meta


def note_build_time(seconds):
    STATS['build_s'] += float(seconds)
    STATS['builds'] += 1


# Root master records, cached per session: (scope, key) -> (record, error).
_MASTERS = {}
_LIBS = {}


def clear_masters():
    _MASTERS.clear()
    _LIBS.clear()


def load_master(key, loader, scope=''):
    """Load a root master once per session; a failure returns (None, error), never raises."""
    k = (str(scope).casefold(), str(key or '').casefold())
    if k not in _MASTERS:
        if not key:
            _MASTERS[k] = (None, 'no master key')
        else:
            try:
                _MASTERS[k] = (loader(key), None)
            except Exception as exc:
                _MASTERS[k] = (None, f'{type(exc).__name__}: {exc}')
    return _MASTERS[k]


def master_for_flat(mi, mi_path):
    """Root master record for a flat MI (dispatch route), via the rebuild material library."""
    sources = list((mi or {}).get('material_source_paths') or ())
    if not sources or (mi or {}).get('unresolved_material_parents'):
        return None, 'lineage unresolved'
    try:
        from .rebuild.assets import get_index, infer_export_root, relative_package
        from .rebuild.records import MaterialLibrary
        index = get_index(str(infer_export_root(mi_path)))
        root = str(index.root)
        key = relative_package(sources[-1], root)
        if key.casefold() == relative_package(mi_path, root).casefold():
            return None, 'MI is its own root'
        lib = _LIBS.get(root.casefold())
        if lib is None:
            lib = _LIBS[root.casefold()] = MaterialLibrary(index)
    except Exception as exc:
        return None, f'{type(exc).__name__}: {exc}'
    return load_master(key, lib.load, root)


def _flat_to_dict(mi):
    """Flat MI rows carry textures/colours as (name, value) pairs; the rule core wants dicts."""
    mi = dict(mi or {})
    for category in ('textures', 'colours', 'switches'):
        raw = mi.get(category)
        if isinstance(raw, list) and raw and all(isinstance(x, (tuple, list)) and len(x) == 2 for x in raw):
            out = {}
            for k, v in raw:
                out.setdefault(k, v)
            mi[category] = out
    return mi


def collect(objects):
    """Gather stored records for objects and their slot materials (operator / harness)."""
    out = {'objects': [], 'materials': []}
    seen = set()
    for obj in objects:
        if obj.get('arc_audit'):
            out['objects'].append(json.loads(obj['arc_audit']))
        for s in getattr(obj, 'material_slots', ()):
            m = s.material
            if m is None or m.name in seen:
                continue
            seen.add(m.name)
            out['materials'].append(json.loads(m['arc_audit']) if m.get('arc_audit') else {'material': m.name, 'flags': ['unaudited']})
    return out
