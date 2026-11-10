"""Enemy-only profiles verified against current Tarragon SM5 bytecode.

Graph/UV primitives are shared with the vendored Revamped compiler; no prop or
firearm material builder is invoked. Raw PAK sidecars preserve named defaults.
"""
from __future__ import annotations
import json
import os
import struct
from pathlib import Path
import bpy
from .. import textures, utils
from .rebuild.graph import Graph
from .rebuild.enemy_tile import build_enemy_tile
from .rebuild.records import raw_record, merge
from .rebuild.assets import package
from .common import _resolve_mi_texture_path, _set_material_alpha_mode

SETUP_VERSION = 'enemy-surface-v5'


def _f32(word):
    return struct.unpack('<f', struct.pack('<I', word))[0]


# M_EnemyPreset_CR+NOM+Tint+E+Damage SM5 base pass: BullCrab armour 8C6AD5D6 / legs 86B5A36E,
# LightDrone A7D0E215, Sniper 38F87A7B, master 94F456E5. Words are the shaders' literal operands.
_ALERT_IDLE = tuple(_f32(w) for w in (0x3db4d595, 0x3f16b6ae, 0x3f4eaab0))
_ALERT_SLOPE = tuple(_f32(w) for w in (0x3f69654d, 0x3dd17590, 0xbf4eaab0))  # per 2 x AlertnessState
_ALERT_LOW, _ALERT_HIGH, _ALERT_EPSILON = _f32(0x3ea8f5c3), _f32(0x3f28f5c3), _f32(0x3727c5ac)
_ALERT_GAIN, _NEUTRAL_GAIN = 8.0, 4.0
# MaterialCollection0 = MPC_RimLight, MaterialCollection1 = MPC_FX_GlobalParameters (cooked defaults).
_RIM = {'NearDistance': 500.0, 'FarDistance': 5000.0, 'NearIntensity': 0.025, 'FarIntensity': 0.25,
        'NearSize': 0.75, 'FarSize': 0.2, 'Color': (1.0, 1.0, 1.0)}
_FX_EMISSIVE_INTENSITY = 1.0
ALERTNESS_PROPERTY = 'arc_alertness_state'


def ensure_enemy_sources(paths):
    """Refresh legacy PAK summaries without invalidating unrelated asset caches."""
    missing = []
    for path in paths:
        normalized = path.replace('\\', '/')
        marker = normalized.lower().rfind('/content/')
        if '/arcpak/cache/' not in normalized.lower() or marker < 0:
            continue
        if not os.path.isfile(str(Path(path).with_suffix('.arc-material.json'))):
            missing.append('/Game/' + normalized[marker + 9:].rsplit('.', 1)[0])
    if not missing:
        return
    from .. import pak_extract
    scene = bpy.context.scene
    game = pak_extract.configured_dir(scene)
    if game:
        code, log, _meshes = pak_extract.extract_paths(game, pak_extract.resolve_usmap(scene), list(dict.fromkeys(missing)))
        if code:
            raise RuntimeError('Enemy parent metadata extraction failed: ' + log[-1500:])


def _object_path(reference):
    value = package(reference)
    for prefix in ('PioneerGame/Content/', 'RemappedPlugins/Content/'):
        if value.lower().startswith(prefix.lower()):
            return '/Game/' + value[len(prefix):]
    if value.startswith('Engine/Content/'):
        return '/Engine/' + value[len('Engine/Content/'):]
    return reference


def _source_path(reference, fallback=''):
    if fallback:
        sidecar = str(Path(fallback).with_suffix('.arc-material.json'))
        if os.path.isfile(sidecar):
            return sidecar
        normalized = fallback.replace('\\', '/')
        marker = normalized.lower().rfind('/content/')
        if marker >= 0:
            reference = '/Game/' + normalized[marker + 9:].rsplit('.', 1)[0]
    for ext in ('.arc-material.json', '.json'):
        hit = textures.find_asset_from_object_path(_object_path(reference), ext)
        if hit:
            return hit
    return fallback if os.path.isfile(fallback) else ''


def effective_enemy_record(mi_path):
    """Resolve named parent defaults and overrides, without suffix guesses."""
    visited = set()
    def load(path, reference=''):
        key = os.path.normcase(os.path.abspath(path))
        if key in visited:
            raise ValueError('Enemy material parent cycle: ' + path)
        visited.add(key)
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        child = raw_record(data, reference or Path(mi_path).stem)
        parent = child.get('parent')
        master = child.get('package', '')
        if parent:
            parent_path = _source_path(parent)
            if not parent_path:
                child['missing_parent'] = parent
                master = parent
            else:
                inherited = load(parent_path, parent)
                master = inherited.get('master', parent)
                child = merge(inherited, child)
        child['master'] = master
        return child
    source = _source_path('', mi_path)
    record = load(source)
    record['source_json'] = source
    if 'enemypreset' not in record.get('master', '').lower():
        record['inferred_master'] = True
        if 'OX' in record['textures']:
            record['master'] = 'M_EnemyPreset_OX+Tiling+Detail'
        elif 'CR' in record['textures'] and 'NOM' in record['textures']:
            record['master'] = 'M_EnemyPreset_CR+NOM+Tint+E+Damage'
    return record


class _EnemyTextureIndex:
    def __init__(self, mi_path, psk_path):
        self.folders = list(dict.fromkeys(filter(None, (os.path.dirname(mi_path), os.path.dirname(psk_path)))))
    def texture(self, reference, layer=None):
        return _resolve_mi_texture_path(_object_path(reference), self.folders) or None
    def texture_info(self, reference):
        """The PNG's .arc-texture.json, so Graph.image applies the authored sampling (Spearmint EXX keeps the default
        flag on DXT and the game's sampler decodes it)."""
        path = self.texture(reference)
        if not path:
            return {}
        try:
            with open(os.path.splitext(path)[0] + '.arc-texture.json', encoding='utf8') as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {**(data.get('Properties') or {}), 'Type': data.get('Type'), 'source_path': path}


def _owner(mat, psk_path, obj):
    if obj is not None:
        return obj
    source = os.path.normcase(os.path.abspath(psk_path)) if psk_path else ''
    for candidate in bpy.data.objects:
        if candidate.type != 'MESH':
            continue
        if any(s.material == mat for s in candidate.material_slots):
            return candidate
        stamped = str(candidate.get('arc_psk_path') or '')
        if source and stamped and os.path.normcase(os.path.abspath(stamped)) == source:
            return candidate
    candidate = getattr(bpy.context, 'object', None)
    if candidate is not None and candidate.type == 'MESH':
        return candidate
    raise ValueError('Enemy shader requires its imported mesh UV layers')


def ensure_alertness_property(obj, value=None):
    """Per-object AlertnessState read by enemy display and surface emissive graphs (keyframable)."""
    if obj is None:
        return
    if value is not None or ALERTNESS_PROPERTY not in obj:
        obj[ALERTNESS_PROPERTY] = float(value if value is not None else 0.0)
    try:
        obj.id_properties_ui(ALERTNESS_PROPERTY).update(
            min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, step=50, precision=2,
            description='ARC AlertnessState: 0 idle (blue), 0.5 alert (orange), 1 combat (red)')
    except Exception:
        pass
    obj.update_tag()


def _eye_adaptation(g):
    """These emissive terms divide by UE EyeAdaptation; 2^scene exposure is Blender's analogue."""
    value = g.value(1.0, 'Eye adaptation · 2^scene exposure')
    scene = getattr(bpy.context, 'scene', None)
    if scene is not None:
        driver = value.driver_add('default_value').driver
        variable = driver.variables.new()
        variable.name, variable.type = 'exposure', 'SINGLE_PROP'
        variable.targets[0].id_type = 'SCENE'
        variable.targets[0].id = scene
        variable.targets[0].data_path = 'view_settings.exposure'
        driver.expression = 'pow(2, exposure)'
    return value


def _alertness_color(g):
    """Shader select chain over AlertnessState: blue <=0.33 < ramp <=0.66 < red blend."""
    attribute = g.node('ShaderNodeAttribute', 'AlertnessState · object ' + ALERTNESS_PROPERTY,
                       attribute_type='OBJECT', attribute_name=ALERTNESS_PROPERTY)
    alert = g.split(attribute.outputs['Vector'])[0]

    def window(edge):
        reached = g.sub(1, g.math('LESS_THAN', alert, edge))
        return g.mul(reached, g.math('GREATER_THAN', g.math('ABSOLUTE', g.sub(alert, edge)), _ALERT_EPSILON))
    low, high = window(_ALERT_LOW), window(_ALERT_HIGH)
    doubled = g.mul(alert, 2)
    channels = []
    for idle, slope, red in zip(_ALERT_IDLE, _ALERT_SLOPE, (1.0, 0.0, 0.0)):
        ramp = g.add(idle, g.mul(doubled, slope))
        selected = g.add(idle, g.mul(low, g.sub(ramp, idle)))
        toward_red = g.add(selected, g.mul(high, g.mul(alert, g.sub(red, selected))))
        channels.append(g.add(selected, g.mul(alert, g.sub(toward_red, selected))))
    return channels


def _native_emission(g, record, ex, world_normal, color):
    """Healthy-state emissive output of the master: EXX.R alertness/neutral terms plus MPC rim light."""
    s, sw, colors = record['scalars'], record['switches'], record.get('colors') or {}
    stages = []
    with g.block('Enemy emissive light · native SM5 arithmetic'):
        adaptation = _eye_adaptation(g)
        emission = None
        if ex and (sw.get('Use Neutral Emissive') or sw.get('Use Alertness Emissive')):
            mask = ex['r']
            if sw.get('Use Neutral Emissive'):
                # UE V tile selects Emissive Intensity [0,1), V1 [-1,0), V2 [-2,-1). Neutral replaces
                # the alertness term while alive when both switches are set (Sniper 38F87A7B).
                v = g.split(g.uv(0))[1]
                below = [g.math('LESS_THAN', v, edge) for edge in (0, -1, -2)]
                first, second = g.sub(below[0], below[1]), g.sub(below[1], below[2])
                base = g.mul(mask, float(s.get('Emissive Intensity', 1.0)))
                tiled = g.add(base, g.mul(first, g.sub(g.mul(mask, float(s.get('V1 Emissive Intensity', 0.0))), base)))
                intensity = g.add(tiled, g.mul(second, g.sub(g.mul(mask, float(s.get('V2 Emissive Intensity', 0.0))), tiled)))
                amount = g.div(g.mul(g.mul(intensity, g.clamp(intensity)), _NEUTRAL_GAIN * _FX_EMISSIVE_INTENSITY), adaptation)
                tint = tuple(colors.get('Neutral Emissive', (1.0, 1.0, 1.0, 1.0)))[:3]
                emission = g.vector('SCALE', g.combine(*tint), scale=amount)
                stages.append('EXX.R neutral emissive (UV-tile intensity, I*sat(I)*4)')
            else:
                scale = g.div(_ALERT_GAIN * _FX_EMISSIVE_INTENSITY, adaptation)
                rgb = [g.mul(g.mul(mask, g.clamp(g.mul(mask, c))), scale) for c in _alertness_color(g)]
                emission = g.combine(*rgb)
                stages.append('EXX.R alertness palette from per-object AlertnessState (X*sat(X*C)*8)')
        geometry = g.node('ShaderNodeNewGeometry', 'Rim view inputs')
        camera = g.node('ShaderNodeCameraData', 'Rim camera distance')
        distance = g.mul(camera.outputs['View Distance'], 100.0)
        near = _RIM['NearDistance'] + float(s.get('NearDistanceModifier', 0.0))
        far = _RIM['FarDistance'] + float(s.get('FarDistanceModifier', 0.0))
        blend = g.clamp(g.div(g.sub(distance, near), far - near))
        near_size = _RIM['NearSize'] + float(s.get('NearSizeModifier', 0.0))
        far_size = _RIM['FarSize'] + float(s.get('FarSizeModifier', 0.0))
        size = g.add(near_size, g.mul(blend, far_size - near_size))
        incoming = geometry.outputs['Incoming']
        facing = g.mul(g.sub(1, g.vector('DOT_PRODUCT', world_normal, incoming)),
                       g.sub(1, g.vector('DOT_PRODUCT', geometry.outputs['Normal'], incoming)))
        near_intensity = _RIM['NearIntensity'] * float(s.get('NearIntensityMultiplier', 1.0))
        far_intensity = _RIM['FarIntensity'] * float(s.get('FarIntensityMultiplier', 1.0))
        strength = g.add(near_intensity, g.mul(blend, far_intensity - near_intensity))
        rim = g.div(g.mul(g.clamp(g.div(facing, size)), strength), adaptation)
        lit = g.vector('MULTIPLY', g.vector('MAXIMUM', color, (0.1, 0.1, 0.1)), _RIM['Color'])
        rim = g.vector('SCALE', lit, scale=rim)
        stages.append('MPC_RimLight distance/fresnel rim on max(base colour, 0.1)')
        emission = rim if emission is None else g.vector('ADD', emission, rim)
    return emission, stages


def _build_enemy_cr_nom(g, record):
    """Healthy Tint+E+Damage baseline joined to Tarragon pixel permutations."""
    t, s, sw = record['textures'], record['scalars'], record['switches']
    cr = g.image(t.get('CR', ''), g.uv(0), srgb=True, label='Enemy CR · colour / alpha roughness')
    nom = g.image(t.get('NOM', ''), g.uv(0), srgb=False, label='Enemy NOM · RG normal / B AO / A metallic')
    if not cr or not nom:
        raise ValueError('Enemy CR/NOM pixel textures are missing')
    native_master = str(record.get('master', '')).lower().endswith('m_enemypreset_cr+nom+tint+e+damage')
    ex_ref = t.get('EX') or t.get('EXX')
    ex = g.image(ex_ref, g.uv(0), srgb=False, required=False,
                 label='Enemy EXX · R emissive mask / G B A damage, soot, holes') if ex_ref else None
    detail_ref = t.get('Detail NXR') or t.get('Detail Texture')
    use_detail = bool(sw.get('Use Detail', sw.get('UseDetail', True)))
    detail = g.image(detail_ref, g.transform_uv(0, s.get('Detail Tile', s.get('DetailTile', 1))),
                     srgb=False, label='Enemy detail NXR · tiled RG normal / B colour / A roughness') if use_detail and detail_ref else None
    with g.block('Enemy surface · verified healthy SM5 arithmetic') as b:
        color = cr['color']
        rough = b.math('POWER', cr['a'], 1.1, label='Native CR roughness exponent 1.1')
        normal = b.decode_normal(nom)
        if detail:
            intensity = b.parameter('Detail Color Intensity', float(s.get('Detail Color Intensity', 0.4)))
            modulation = b.add(1, b.mul(intensity, b.sub(b.mul(detail['b'], 2), 1)))
            color = b.vector('SCALE', color, scale=modulation)
            rough = b.mul(rough, b.mul(detail['a'], 2))
            x, y, z = b.split(normal)
            normal = b.vector('NORMALIZE', b.combine(b.add(x, b.sub(b.mul(detail['r'], 2), 1)),
                              b.add(y, b.sub(b.mul(detail['g'], 2), 1)), b.add(z, 1)))
        b.export(nom['b'], 'Engine ambient occlusion')
        color = b.vector('MINIMUM', b.vector('MAXIMUM', color, (0, 0, 0)), (1, 1, 1))
        emission, emissive_stages = None, []
        if native_master:
            normal = b.normal_to_world(normal)
            emission, emissive_stages = _native_emission(b, record, ex, normal, color)
        b.finish(color, b.clamp(rough), nom['a'], normal, emission=emission)
    if t.get('Cubemap'):
        g.image(t['Cubemap'], required=False, srgb=False, label='Enemy cubemap · runtime reflection input')
    g.report['implemented_stages'] = ['CR linear colour', 'CR.A^1.1 roughness', 'NOM RG normal reconstruction',
        'NOM.A metallic', 'NOM.B exposed engine AO', 'authored detail UV scale', 'detail B colour modulation',
        'detail A roughness modulation', 'native additive XY detail normal'] + (emissive_stages or ['no emissive output for this master'])
    g.report['warnings'].append('Engine AO exposed separately; runtime damage/soot/holes, reflection/glare and contact shadows are not reconstructed')
    if native_master:
        g.report['warnings'].append('Damage-only emissive flicker and death sweep (negative bone damage) are not reconstructed; '
                                    'MPC_RimLight/MPC_FX_GlobalParameters use cooked defaults')
    if float(s.get('DamagePercent', 0)) != 0:
        g.report['warnings'].append('Nonzero DamagePercent is unsupported by this healthy surface profile')


def setup_enemy_surface_material(mat, mi_path, psk_path='', *, obj=None):
    from .native.native_families import setup_entrypoint
    if setup_entrypoint(mat, mi_path, psk_path, obj=obj, expected_family='enemy_shell', cache_family='enemy'):
        return json.loads(mat.get('dr_native_report') or mat.get('arc_enemy_build_report') or '{}')
    record = effective_enemy_record(mi_path)
    report = {'family': 'enemy', 'master': record.get('master', ''), 'source_json': record['source_json'],
              'missing_textures': [], 'warnings': [], 'engine_parity_verified': False,
              'evidence': 'Tarragon SM5 367C2A90 OX, 6609894A interior, 07985D85 exterior; current PAK build 25879518'}
    if record.get('missing_parent'):
        report['warnings'].append('Missing exact parent metadata: ' + record['missing_parent'])
    if record.get('inferred_master'):
        report['warnings'].append('Legacy summary lacks Parent; re-extract PAKs for authoritative named defaults')
    graph = Graph(mat, _owner(mat, psk_path, obj), _EnemyTextureIndex(mi_path, psk_path), report)
    if str(record.get('master', '')).lower().endswith('m_enemypreset_ox+tiling+detail'):
        build_enemy_tile(graph, record)
    else:
        if not str(record.get('master', '')).lower().endswith('m_enemypreset_cr+nom+tint+e+damage'):
            report['warnings'].append('This master uses the Tarragon CR/NOM baseline; its own compiled permutation has not been verified')
        _build_enemy_cr_nom(graph, record)
    if report['missing_textures']:
        mat['arc_enemy_missing_textures'] = '; '.join(report['missing_textures'])
        utils.get_logger().error('Enemy shader has missing pixel textures: %s', mat['arc_enemy_missing_textures'])
    elif 'arc_enemy_missing_textures' in mat:
        del mat['arc_enemy_missing_textures']
    _set_material_alpha_mode(mat, mode='OPAQUE', two_sided=bool(record['settings'].get('bTwoSided', False)))
    live = str(record.get('master', '')).lower().endswith('m_enemypreset_cr+nom+tint+e+damage') and \
        bool(record['switches'].get('Use Alertness Emissive')) and not record['switches'].get('Use Neutral Emissive')
    mat['arc_alertness_live'] = live
    if live:
        ensure_alertness_property(graph.obj)
    mat['arc_enemy_surface_setup'] = SETUP_VERSION
    mat['arc_enemy_master'] = record.get('master', '')
    mat['arc_enemy_build_report'] = json.dumps(report, ensure_ascii=False)
    return report
