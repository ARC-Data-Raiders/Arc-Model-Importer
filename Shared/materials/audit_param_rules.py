"""Texture-parameter classification rules for the material audit.

Pure Python, no bpy and no package-relative imports, so offline tools can load
this file directly. Every rule keys on master, parameter or switch names only,
never on prop or MI names.

A texture parameter whose image does not reach the output is classified as:

- ``tex_param_unlinked:<p>`` (defect): the game samples it. Either it is part
  of the core layout (base colour, normal, packed), or it is overridden below
  the root master and its gate is on or unknown, or it keeps the root default
  while a known gate switch is on.
- ``tex_default_unlinked:<p>`` (info): the root master default, no gate on.
- ``tex_gated_off:<p>`` (info): overridden, but its known gate switch is off.
- ``tex_layer_skipped:<layer>`` (info, one per layer): a world-effect layer the
  importer deliberately does not build, with its gate switch off or unknown.
- ``layer_not_built:<layer>`` (inconsistency): the same layer with its gate
  switch on, so the game would show it.
- ``layer_world_state:<layer>`` (info): a snow, frost or waterline layer whose
  enabling switch is off, at the root default, or absent. Its look depends on
  weather or water height, so only an MI-set enabling switch is an
  inconsistency.
"""
import re

RULES_VERSION = 2
DEFECT, INCONSISTENCY, INFO = 'defect', 'inconsistency', 'info'

# World-effect layers the importer does not build. First match wins, so the
# waterline row sits before overlay ("WaterlineOverlay").
# (layer, parameter pattern, gate switch pattern)
LAYERS = (
    ('snow', r'snow', r'snow'),
    ('frost', r'frost|\bice\b', r'frost|\bice\b'),
    ('waterline', r'waterline|puddle|wetness', r'waterline|puddle|wet'),
    ('sign', r'^sign', r'\bsign'),
    ('overlay', r'overlay', r'overlay'),
    ('holes', r'holes?(nxx)?\b|holesnxx', r'hole'),
    ('dirt', r'dirt|dust|grime', r'dirt|dust|grime'),
    # R39: EnemyPreset_Masked_NAO+Detail samples Height/HX only for its Parallax switch.
    ('parallax', r'^pm_|parallax|^height texture$|^height/hx$', r'parallax|\bpom\b|^pm[_ ]|^use height'),
    # approximate: lidar colour is taken to be the runtime scanner pulse
    # (shown only while a scan sweeps the mesh), inferred from the name.
    ('lidar', r'lidar', r'lidar'),
)

# Gate switches for non-layer parameters: (parameter pattern, switch pattern).
PARAM_GATES = (
    (r'blend', r'blend'),
    (r'\bao\b|ambient', r'\bao\b|ambient'),
    (r'breakup', r'breakup'),
    (r'detail', r'detail'),
    (r'paint', r'paint'),
    (r'pattern', r'pattern'),
    (r'tint', r'tint'),
    (r'wear', r'wear'),
    (r'decal', r'decal'),
    (r'lidar', r'lidar'),
    (r'emissi|glow', r'emissi|glow'),
    # R11 approximate: gates read from switch names on M_PropPreset masters.
    (r'^packed', r'packed mask'),
    (r'^masks?$', r'^(use|enable) masks?$'),
)

# Core layout parameters: an unlinked one is always a defect.
CORE = re.compile(
    r'^(base ?colou?r( ?map| ?texture)?|albedo|diffuse( ?map| ?texture)?|colou?r( ?map| ?texture)?'
    r'|normal( ?map| ?texture)?|packed( ?mask| ?texture| ?map)?'
    r'|bc|ca|cr|crm|c|n|nm|noh|nom|nao|naoh|orm|arm|mra|mrao|rma|mro)$', re.I)

# A switch named like this turns its layer off when true.
INVERTED = re.compile(r"\b(disable|disabled|no|hide|without|remove|off|don'?t|do not)\b", re.I)

# Layers whose look in game depends on world state (weather snow/frost, water
# height and rain wetness for waterline/puddles, scanner pulse for lidar),
# not on the MI alone.
WORLD_STATE_LAYERS = frozenset(('snow', 'frost', 'waterline', 'lidar'))

_LAYERS = tuple((name, re.compile(p, re.I), re.compile(s, re.I)) for name, p, s in LAYERS)
_GATES = tuple((re.compile(p, re.I), re.compile(s, re.I)) for p, s in PARAM_GATES)

SEVERITY = {
    # R15: producers emit these as INFO; without entries severity() reclassed them DEFECT.
    # master_unavailable stays DEFECT: a missing master must not pass as verified.
    'emissive_gated_off': INFO, 'emissive_default_off': INFO, 'tex_unnamed_unlinked': INFO,
    'tex_default_unlinked': INFO, 'tex_gated_off': INFO, 'tex_layer_skipped': INFO,
    'tex_param_skipped': INFO, 'layer_not_built': INCONSISTENCY, 'layer_world_state': INFO,
    'no_texture_master': INCONSISTENCY, 'engine_default_material': INFO, 'engine_default': INFO,
    'engine_helper_material': INFO,
    'switch_render_only': INFO, 'switch_uv_unbuilt': INCONSISTENCY,
    'switch_runtime_input': INFO, 'switch_layer_unbuilt': INCONSISTENCY,
    'switch_world_projected': INFO, 'switch_view_dependent': INFO, 'switch_light_dependent': INFO, 'switch_time_dependent': INFO,
    'layer_world_projected': INFO,
    # Fallback root master with <5 slots in the audit: no dedicated builder;
    # stays gated and is listed in the coverage report.
    'rare_master': INCONSISTENCY,
    # unresolved_material_ref causes (see unresolved_ref_class).
    'ref_hlod_proxy': INFO, 'ref_not_material': INFO, 'ref_post_process': INFO,
    'ref_missing_package': INCONSISTENCY,
    # Producer fallbacks: output is approximate, so the row stays gated
    # (certainty uncertain) and is never verified.
    'normals_approximate': INCONSISTENCY, 'unset_textures': INCONSISTENCY,
    # R24: Texture2DArray param built from slice 0; the master's slice index is unproven.
    'array_slice_approximate': INCONSISTENCY,
    'uv_owner_unresolved': INCONSISTENCY,
    # R13 old-audit parity. Unlisted (defect): surface_disconnected, image_node_empty,
    # two_sided_culled, section_material_mismatch. approximate: provenance and
    # bounds/render thresholds are heuristics, so they gate without claiming a defect.
    'foreign_texture': INCONSISTENCY, 'huge_bounds': INCONSISTENCY, 'tiny_bounds': INCONSISTENCY,
    'blown_out': INCONSISTENCY,
    # R37: linear >= 1.0 under sun 3 + world 0.5 is reached by plain lit albedo >= 0.29; display clip only.
    'bright_clipped': INFO,
    # R25: >300 m on a world-scale mesh (backdrop, landscape, proxy, tile) is the
    # authored size; all 18 huge records in the 1.11 audit were such meshes.
    'huge_bounds_world': INFO,
    # R13: emission source not decodable offline; stays gated, not a builder defect.
    'emissive_runtime': INCONSISTENCY,
    # R16: opacity source not decodable offline (gated); builder-set constant alpha
    # from an authored opacity scalar is a wired driver (info).
    'opacity_runtime': INCONSISTENCY, 'opacity_constant': INFO,
    # R21: texture param the master's shader never samples (MASTER_UNREAD).
    'tex_unread_unlinked': INFO,
    # R21: engine LocalVertexFactory clamps the UV index (native/interop.py); the
    # importer reproduces the engine, so the clamp is a note, not a defect.
    'uv_clamped': INFO,
    # R21: switch the builder reads (SWITCH_RULES 'handled').
    'switch_handled': INFO,
    # R30: AO texture gated off on a master whose compiled AO pin is constant.
    'ao_pin_only': INFO,
}

# R25: mesh names that are world scale by design (map backdrops, landscape
# chunks, HLOD/POI proxies, world-partition tiles _xN_yM, light blockers).
WORLD_SCALE_MESH = re.compile(r'back_?drop|landscape|lightblocker|heightfield|^sm_proxy_|_x\d+_y\d+', re.I)


def world_scale_mesh(name):
    """True when the mesh/object name (path or basename) is a world-scale mesh."""
    base = str(name or '').replace('\\', '/').rsplit('/', 1)[-1].split('.')[0]
    return bool(base) and bool(WORLD_SCALE_MESH.search(base))


# R21: (root master base, param casefold) -> why the master never samples the
# param. An unlinked texture there matches the game. Keyed by master + param only.
MASTER_UNREAD = {
    ('m_masterlight_lightbulb_01', 'cr'): 'PS 83C059E2 samples only Lightbulb, GlassTexture, Normal',
    ('m_masterlight_lightbulb_swing_01', 'cr'): 'same pixel shader family as Lightbulb_01; CR not sampled',
}


def master_unread_reason(master, param):
    """Reason string when the root master never samples ``param``, else None."""
    return MASTER_UNREAD.get((master_base(master), canonical(param).casefold()))

# R13 item 4: root masters whose emissive mask/source lives only in material
# functions the cooked cache does not carry. The master record lists Use Emissive,
# Use CR as Emissive, EmissiveColor/Intensity/HistogramRange and MF_Prop_Emissive +
# MF_Prop_NOM_PackedMaskSetup, but no NEM/XEM texture parameter, so the mask channel
# is unproven. approximate: emission left unbuilt until a function graph is decoded.
EMISSIVE_RUNTIME = {
    'm_proppreset_cr+nom': 'mask channel inside MF_Prop_Emissive/MF_Prop_NOM_PackedMaskSetup; no function graph in cooked cache',
    # R16 item 2: master records carry only an emissive power/strength scalar; no
    # emissive texture, colour or switch names the source. approximate.
    'm_vegetation_01': "only '8. Emissive Power' scalar exported; no emissive texture/colour names the source",
    'm_brokenglasssdf': 'EmissiveStrength scales an SDF edge term built in-graph; unnamed colour params only',
    'm_beacon': "Emissive scalar gated by OnOff switch/scalar; LightColor path not in exported params",
    'm_download_activity_progressbar_01': 'ScreenColor x EmissiveStrength through procedural progress mask (DownloadProgress); no texture',
}

# R16 item 2: masked/translucent root masters whose opacity source is a material
# function or in-graph term absent from exported params (no texture channel or
# scalar named as opacity). approximate: alpha left unbuilt until decoded.
OPACITY_RUNTIME = {
    'm_proppreset_cr+nox+xem': 'opacity from Use Slice Mask/DitherOpacityMask function; no channel named in exported params',
    'm_proppreset_cr+nom': 'opacity from Use Slice Mask/AlphaHoles function; no channel named in exported params',
    'm_proppreset_nr+vpo+detail': 'masked master with no opacity texture/scalar in exported params',
    'm_character_default_2': 'masked master with no opacity texture/scalar in exported params',
    'm_snowpile_01': "masked by 'Use SnowPatch Alpha' path (off); source term not exported",
    'm_swampgrass_01': 'masked master with no opacity texture/scalar in exported params',
    'm_cinematicglass': 'translucent master with no opacity param in exported data',
}


def emissive_runtime_reason(master):
    """Reason string when a root master's emission source is runtime-only, else None."""
    return EMISSIVE_RUNTIME.get(master_base(master))


def opacity_runtime_reason(master):
    """Reason string when a root master's opacity source is runtime-only, else None."""
    return OPACITY_RUNTIME.get(master_base(master))


# R14: shared library textures the builders sample from master defaults or
# MaterialLibrary functions (texture arrays TA_*, colour atlases CA_*, prop dust,
# trim-sheet masks, grunge/dirt/noise/detail/weathering layers, world layers,
# Generic/Global/Default packs). Never foreign. approximate: name families from
# the gate7 census (95 flags, all library), not from a decoded function list.
_SHARED_LIBRARY = re.compile(
    r'^(generic_|global_|default)|propdust|proptrimsheet|grunge|dirt|noise|detail|'
    r'weathering|world|blendnoise|tilingnoise')
# R15: one asset prefix (T_/TA_/CA_) stripped before every pattern (T_Generic_02_GRM).
_ASSET_PREFIX = re.compile(r'^(t|ta|ca)_')


def is_shared_library_texture(stem_name, filepath=None):
    name = str(stem_name or '').casefold()
    # approximate: exported PNG paths mirror the package path; MaterialLibrary is shared.
    if name.startswith(('ta_', 'ca_')) or 'materiallibrary' in str(filepath or '').casefold():
        return True
    return bool(_SHARED_LIBRARY.search(_ASSET_PREFIX.sub('', name, count=1)))


_ROLE_SUFFIX = {'c': 'colour', 'cr': 'colour', 'ca': 'colour', 'bc': 'colour', 'd': 'colour'}


def texture_role(stem_name):
    """'colour' / 'normal' / 'data' from the stem's suffix, None when no suffix."""
    tail = str(stem_name or '').casefold().rsplit('_', 1)
    if len(tail) < 2 or not tail[1].isalpha() or len(tail[1]) > 4:
        return None
    s = tail[1]
    if s in _ROLE_SUFFIX:
        return 'colour'
    return 'normal' if s.startswith('n') else 'data'


def param_role(label):
    """Role a node label / param name asks for, None when unknown."""
    text = str(label or '').casefold()
    if not text:
        return None
    if 'normal' in text:
        return 'normal'
    if any(k in text for k in ('mask', 'rough', 'spec', 'metal', 'height', 'opacity', 'grm')):
        return 'data'
    if any(k in text for k in ('diffuse', 'color', 'colour', 'albedo', 'basecolor')):
        return 'colour'
    return None


def foreign_role_conflict(stem_name, label):
    """True unless both roles are known and agree (unknown label keeps the flag)."""
    want = param_role(label)
    have = texture_role(stem_name)
    return want is None or have is None or want != have

# Static switch classes by name. A switch that can alter base colour, normal,
# roughness or opacity stays gated (no class), so KEEP patterns win first.
# (class, severity, pattern)
SWITCH_KEEP = re.compile(
    r'opacity|dithermask|usedithering|fade|\bvt\b|virtual ?texture|terrain|landscape|ocean|colou?r|normal|rough',
    re.I)
SWITCH_RULES = (
    # R21: switches the rebuild builders read. "Don't use Packed Mask":
    # rebuild/surfaces.py _packed_mask_gate records the gate both ways.
    ('handled', INFO, re.compile(r"^don'?t use packed mask$", re.I)),
    # Vertex animation (wind/WPO), LOD stepping, TAA dither and debug views:
    # no effect on a static still's colour, normal, roughness or opacity.
    ('render_only', INFO,
     re.compile(r'wind(?!ow)|wpo|stop ?moving|^is_?last_?lod$|dithertempaa|debug|nanite'
                r'|^large movement$', re.I)),
    # UV transforms the material builders do not read. Advanced UV Options:
    # its params (UV Mode, WorldTileU/V, RotateMask, UVOffset) are known by
    # name, but the kept metadata carries values only, no graph equation.
    # Atlas cell pick, alternate tiling, UV2 and axis swap change texture
    # placement, not the texture set.
    ('uv_unbuilt', INCONSISTENCY,
     re.compile(r'advanced ?uv|\buse atlas\b|simpe?l ?til+ing|\buv ?2\b|orientation is [xyz]', re.I)),
    # Inputs a static import does not have: the landscape under the mesh
    # (terrain blend), the per-instance random seed, and runtime virtual
    # texture writes (RT base colour feeds the landscape RVT, not this
    # surface). approximate: RVT write semantics inferred from the name.
    # Use Heat: Heat Amount defaults to 0 on every exported MI; the runtime
    # heat source drives it, so a still shows no heat glow.
    ('runtime_input', INFO,
     re.compile(r'terrain ?blend|^is terrain$|per ?instance|^set rt |vt blend mask|^use heat$', re.I)),
    # Masks computed from the placed instance's world normal or position
    # (top-down / top / side / vertical / world-Z projections, world rust):
    # they follow the level placement, not the MI, so a static import has no
    # input for them. approximate: projection semantics inferred from names.
    ('world_projected', INFO,
     re.compile(r'top ?(down|mask|projected)|side ?mask|vertical ?mask|world ?z ?mask|world rust', re.I)),
    # Parallax occlusion offsets texels by view angle; no single static look.
    # Derivative scaling resizes texels by screen-space UV derivatives.
    # approximate: inferred from the name.
    ('view_dependent', INFO, re.compile(r'\bpom\b|derivative ?scaling', re.I)),
    # Fake subsurface scatter brightens with light behind the surface.
    # approximate: inferred from the name.
    ('light_dependent', INFO, re.compile(r'fake ?sss', re.I)),
    # R36: Lightbulb Pulsating modulates emission by game time; a still shows
    # one phase of it. approximate: Time term inferred from the name (PS
    # Time-constant read not verified).
    ('time_dependent', INFO, re.compile(r'pulsat', re.I)),
    # Known material layers the rebuild does not build: weathering masks
    # (wear, rust, moss, whiteness, top/side/vertical/world-Z masks), extra
    # tints and paints, fake subsurface, parallax occlusion. The game shows
    # them, so a switched-on one is an inconsistency, not an unknown defect.
    ('layer_unbuilt', INCONSISTENCY,
     re.compile(r'wear|rust|moss|whiteness|tint|second paint|vx colou?ring', re.I)),
)


# Switches that stay gated, with the reason. Patterns are anchored so they
# never read as consumed literals in the audit.
GATED_REASONS = (
    (r'advanced ?uv', 'UV Mode/WorldTile/RotateMask values exported, no graph equation'),
    (r'^optimize vt colou?r$', 'virtual texture colour optimisation; no static equation'),
    (r'^(warehouse|outpost)$', 'mural/plate variant select; no per-variant params exported, equation unknown'),
    (r'^dithermask$', 'dithered opacity; coverage after TAA unproven'),
)
_GATED_REASONS = tuple((re.compile(p, re.I), why) for p, why in GATED_REASONS)


def gated_reason(name):
    """Why a still-gated switch stays gated, or '' when no reason is recorded."""
    text = canonical(name)
    return next((why for pattern, why in _GATED_REASONS if pattern.search(text)), '')


def switch_class(name):
    """(class, severity) for a static switch name, or (None, None) when it stays gated."""
    text = canonical(name)
    for cls, sev, pattern in SWITCH_RULES:
        if pattern.search(text) and not (cls == 'render_only' and SWITCH_KEEP.search(text)):
            return cls, sev
    return None, None


def canonical(name):
    """``Global.X`` and ``X`` are the same parameter."""
    text = str(name)
    return text[7:] if text.startswith('Global.') else text


def stem(path):
    text = str(path or '').replace('\\', '/').rsplit('/', 1)[-1]
    return text.split('.', 1)[0].casefold()


def unresolved_ref_class(path):
    """Cause label for an unresolved material reference, from its package path.

    HLOD proxy materials (M_PROXY_* under a *LOD folder) are baked by the HLOD
    build for distant views; a static import places the full-detail actors.
    A static-mesh path (SM_*) is not a material. Post-process materials draw
    on the screen, never on a mesh. Anything else is a missing package.
    """
    text = str(path or '').replace('\\', '/')
    base = stem(text)
    folder = text.rsplit('/', 2)[-2].casefold() if text.count('/') >= 2 else ''
    if base.startswith('m_proxy_') and folder.endswith('lod'):
        return 'ref_hlod_proxy', INFO
    if base.startswith('sm_'):
        return 'ref_not_material', INFO
    if base.startswith('m_pp_') or '/postprocess/' in text.casefold():
        return 'ref_post_process', INFO
    return 'ref_missing_package', INCONSISTENCY


def severity(flag):
    return SEVERITY.get(str(flag).split(':', 1)[0], DEFECT)


def layer_of(param):
    for name, pattern, _switch in _LAYERS:
        if pattern.search(canonical(param)):
            return name
    return None


def is_core(param):
    return bool(CORE.match(canonical(param).strip()))


def _gate(switches, pattern):
    """(state, evidence) for switches whose name matches; state True/False/None."""
    evidence, state = [], None
    for name, info in switches.items():
        if not pattern.search(name):
            continue
        value = bool(info.get('value'))
        on = (not value) if INVERTED.search(name) else value
        evidence.append({'switch': name, 'value': value, 'overridden': info.get('overridden')})
        state = True if on else (state if state is True else False)
    return state, evidence


# R26: switches that pick a layer's projection, UV set, blend mode or slot
# tint, not whether it compiles. MI switch census: PropTrim TriPlanar
# Overlay=1 with UseOverlay=0 and Color Overlay=0; XEM TriPlanar Overlay=1
# with UseOverlay=0 (20 MIs); Concrete_01 Flip Overlay on Paint / Use UVn as
# Overlay UVs beside UseOverlay; TrimMap Overlay Random Offset / Range Invert
# beside numbered Overlay Texture slots.
LAYER_MODIFIER = re.compile(
    r'tri ?planar|\buvs?\d*\b|2nd uv|\bflip\b|random offset|range invert|tint colou?r'
    r'|alpha ?height|instead of|\bas\b', re.I)


def layer_gate(layer, switches):
    """(state, evidence) of a layer's enabling switches.

    Modifier switches (``LAYER_MODIFIER``) are ignored when the master also
    has a non-modifier switch for the layer; a master with modifier switches
    only keeps them as the gate.
    """
    pattern = next(s for name, _p, s in _LAYERS if name == layer)
    gates = {k: v for k, v in switches.items() if pattern.search(k) and not LAYER_MODIFIER.search(k)}
    return _gate(gates if gates else switches, pattern)


WORLD_PROJECTED = re.compile(r'top ?down|projected', re.I)


def _enabling(evidence):
    return [e for e in evidence if bool(e['value']) != bool(INVERTED.search(e['switch']))]


def layer_rule(layer, state, evidence, param_overridden=None):
    """(flag, severity) for an unbuilt layer given its gate state and switch evidence.

    World-state layers (``WORLD_STATE_LAYERS``) report an inconsistency when
    an enabling switch is set on the MI (overridden True, or unknown), or when
    the flagged layer param itself is MI-overridden (``param_overridden`` True)
    and no switch turns the layer off: both are authored intent. Inherited
    master defaults leave the look to weather or water height:
    ``layer_world_state`` (info).
    """
    if layer == 'lidar':
        # Scanner pulse colour exists only while a runtime scan sweeps the mesh.
        return f'layer_world_state:{layer}', INFO
    enabling = _enabling(evidence)
    if layer == 'dirt' and state is True and enabling and all(
            WORLD_PROJECTED.search(e['switch']) for e in enabling):
        # Top-down / projected dust follows the instance's world up axis.
        return f'layer_world_projected:{layer}', INFO
    if layer in WORLD_STATE_LAYERS:
        if state is True and any(e.get('overridden') is not False for e in _enabling(evidence)):
            return f'layer_not_built:{layer}', INCONSISTENCY
        if param_overridden is True and state is not False:
            # R19: an MI-set snow/frost/waterline texture is authored intent.
            return f'layer_not_built:{layer}', INCONSISTENCY
        return f'layer_world_state:{layer}', INFO
    if state is True:
        return f'layer_not_built:{layer}', INCONSISTENCY
    return f'tex_layer_skipped:{layer}', INFO


_ENABLE = re.compile(r'^(enable|use)\b', re.I)
_WORDS = re.compile(r'[a-z0-9]+')


def _words(text):
    return {w.rstrip('s') for w in _WORDS.findall(str(text).casefold())}


def param_gate(param, switches):
    """(state, evidence) of the switch gating a non-layer param.

    R11: modifier switches (Invert Blend Mask, BlendTextureUV0) no longer turn
    a gate on. Order: a switch named as the param's prefix (RGB Tint Mask ->
    RGB Tint Mask Input); else an Enable/Use switch whose words are all in the
    param name (Enable Detail Normal -> Detail Normal Texture); else the
    Enable/Use switches of the family (Enable Blend Textures -> CR Blend);
    else any matching switch. approximate: gate semantics read from names.
    """
    canon = canonical(param)
    for pattern, switch in _GATES:
        if not pattern.search(canon):
            continue
        state, evidence = _gate(switches, switch)
        if not evidence:
            return state, evidence
        name = canon.casefold()
        pwords = _words(canon)
        prefix = [e for e in evidence if name.startswith(e['switch'].casefold()) and name != e['switch'].casefold()]
        enable = [e for e in evidence if _ENABLE.search(e['switch'])]
        specific = [e for e in enable if _words(_ENABLE.sub('', e['switch'])) <= pwords]
        for chosen in (prefix, specific, enable):
            if chosen:
                on = [bool(e['value']) != bool(INVERTED.search(e['switch'])) for e in chosen]
                return any(on), chosen
        return state, evidence
    return None, []


def master_base(name):
    """Lower-case base name of a master path (/Game/.../M_X.M_X -> m_x)."""
    return str(name or '').replace('\\', '/').rsplit('/', 1)[-1].split('.', 1)[0].casefold()


# R12 approximate: Packed Mask read conditions on the M_PropPreset masters, keyed
# by root master base name, decoded from the importer builders
# (_xem_prop_mask_plan, _nom_prop_tint_plan). Entry: all-of (switch, wanted)
# pairs, then any-of pairs; a missing switch leaves the gate unknown.
_NOM_PACKED = ((("Don't use Packed Mask", False), ('Use Tinting', True), ('Use Default Texture Setup', True)),
               (('Use Packed Tint Mask', True), ('Use Mask Texture', True)))
MASTER_PARAM_GATES = {
    ('m_cr_noh_parallax_vtblend_01', '1. wear noh'): ((('Use Rust', True),), ()),
    ('m_blockoutpreset', 'damagenormal'): ((('EnableDamage', True),), ()),
    ('m_blockoutpreset', 'damagemask'): ((('EnableDamage', True),), ()),
    ('m_architecturepreset_cr+nom+detail', 'detail normal worn'): ((('Use Wear Material', True),), ()),
    ('m_graphic_atlas_01', 'nx'): ((('Use Normal (NX)', True),), ()),
    ('m_glassopaque_nx_01', 'c'): ((), (('Use solid Color instead of CR', False), ('Use Roughness Channel', True))),
    ('m_trimpreset_rusty', 'base normal'): ((('UseNormalMap', True),), ()),
    ('m_proppreset_cr+nox+xem', 'packed mask'): ((('Use Masks', True), ('Use single Mask Texture', False)), ()),
    # R13: _xem_prop_mask_plan samples Mask only on the single-mask permutation (Mask.R metal or tint).
    ('m_proppreset_cr+nox+xem', 'mask'): ((('Use Masks', True), ('Use single Mask Texture', True)), ()),
    ('m_proppreset_cr+nom', 'packed mask'): _NOM_PACKED,
    ('m_itempreset_cr+nom', 'packed mask'): _NOM_PACKED,
    # R16 approximate: CR+NOM Mask is read only with Use Mask Texture on (switch named
    # for the slot; _nom_prop_tint_plan reads Mask.R only then). Slice-mask reads with
    # it on stay unknown, so on keeps the defect.
    ('m_proppreset_cr+nom', 'mask'): ((('Use Mask Texture', True),), ()),
    ('m_itempreset_cr+nom', 'mask'): ((('Use Mask Texture', True),), ()),
    # R16 approximate: switch "3. Use Additional Rust Mask" names its param "3.1 ...".
    ('m_architecturepreset_metal', '3.1 additional rust mask'): ((('3. Use Additional Rust Mask', True),), ()),
}


def master_param_gate(master, param, switches):
    """(state, evidence) of a master-keyed read gate; (None, []) when no rule applies."""
    rule = MASTER_PARAM_GATES.get((master_base(master), canonical(param).casefold()))
    if rule is None:
        return None, []
    all_of, any_of = rule
    values, evidence = {}, []
    for name, _want in all_of + any_of:
        entry = switches.get(canonical(name), switches.get(name))
        if entry is None:
            continue
        value = bool(entry.get('value') if isinstance(entry, dict) else entry)
        values[name] = value
        evidence.append({'switch': name, 'value': value, 'rule': 'master_gate',
                         'overridden': entry.get('overridden') if isinstance(entry, dict) else None})
    if any(n not in values for n, _ in all_of):
        return None, evidence
    on = all(values[n] == w for n, w in all_of)
    if any_of:
        matched = any(n in values and values[n] == w for n, w in any_of)
        if not matched and any(n not in values for n, _ in any_of):
            return (None, evidence) if on else (False, evidence)
        on = on and matched
    return on, evidence


def _value_equal(a, b):
    if isinstance(a, str) or isinstance(b, str):
        return stem(a) == stem(b)
    return a == b


def param_facts(mi, master=None):
    """Normalise one MI to {'textures': {canon: {path, overridden}}, 'switches': {name: {value, overridden}}}.

    Sources, in order: explicit per-parameter lists (mi_params rows:
    ``textures``/``switches`` as lists of dicts with ``overridden``), else flat
    dicts compared against the root master record. ``overridden`` is None when
    neither source can tell.
    """
    mi = mi or {}
    out = {'textures': {}, 'switches': {}, 'overridden_source': 'none',
           'master': str(mi.get('root') or mi.get('root_master') or mi.get('master')
                         or (master or {}).get('package') or (master or {}).get('name') or '')}

    def take(category, key, value, overridden):
        canon = canonical(key)
        slot = out[category]
        # A scoped-free name wins over its Global. copy; Layer/Blend scopes stay distinct.
        if canon in slot and str(key).startswith('Global.'):
            return
        entry = {'path': value} if category == 'textures' else {'value': value}
        entry['overridden'] = overridden
        slot[canon] = entry

    for category in ('textures', 'switches'):
        raw = mi.get(category)
        if isinstance(raw, list):
            out['overridden_source'] = 'explicit'
            for item in raw:
                if isinstance(item, dict) and item.get('name'):
                    value = item.get('path', item.get('value')) if category == 'textures' else item.get('value')
                    ov = item.get('overridden')
                    take(category, item['name'], value, None if ov is None else bool(ov))
            continue
        base = dict((master or {}).get(category) or {})
        if master is not None and out['overridden_source'] != 'explicit':
            out['overridden_source'] = 'root_master_compare'
        for key, value in dict(raw or {}).items():
            if category == 'textures' and not value:
                continue
            ov = None
            if master is not None:
                default = base.get(key, base.get(canonical(key), base.get('Global.' + canonical(key))))
                ov = None if default is None else not _value_equal(value, default)
            take(category, key, value, ov)
    return out


def _named_twin(canon, path, facts):
    """Name of another texture param holding the same path, or None."""
    key = stem(path).casefold()
    for name, entry in (facts.get('textures') or {}).items():
        if name != canon and name.casefold() != key and stem(entry.get('path')).casefold() == key:
            return name
    return None


# R30: root masters whose compiled switch-off permutation writes a constant
# material AO (GBuffer AO = multibounce(1.0)), so the AO texture is neither
# multiplied into base colour nor bound to the AO pin. Proof:
# prop-audit/tmp/r30_ao/proof.txt (PS 19585, PropTrim ResourceHash 8332980CFA4D).
AO_PIN_ONLY = {
    'm_proptrimpreset_cr+nom+ao': 'Use Prop AO Dirt off: PS 8332980CFA4D GBuffer AO constant 1.0; AO texture not in base colour',
}
AO_PARAM = re.compile(r'\bao\b', re.I)


def classify_unlinked(param, facts):
    """Return (flag, severity, detail) for one unlinked texture parameter."""
    canon = canonical(param)
    tex = facts['textures'].get(canon, {})
    switches = facts['switches']
    overridden = tex.get('overridden')
    path = tex.get('path')
    layer = layer_of(canon)
    if layer:
        state, evidence = layer_gate(layer, switches)
        detail = {'param': canon, 'texture': str(path or ''), 'enabled': state, 'switches': evidence}
        flag, sev = layer_rule(layer, state, evidence, overridden)
        if sev == INCONSISTENCY:
            mi_set = overridden is True or any(e.get('overridden') for e in _enabling(evidence))
            detail['level'] = 'medium' if mi_set else 'low'
        return flag, sev, detail
    detail = {'param': canon, 'texture': str(path or ''), 'overridden': overridden}
    unread = master_unread_reason(facts.get('master'), canon)
    if unread:
        detail['reason'] = unread
        return f'tex_unread_unlinked:{canon}', INFO, detail
    stem = str(path or '').replace('\\', '/').rsplit('/', 1)[-1].split('.', 1)[0]
    twin = _named_twin(canon, path, facts) if stem and canon.casefold() == stem.casefold() else None
    if twin:
        # Flat duplicate keyed by texture stem; the same texture is listed under
        # its real param (twin), which carries the link verdict. A stem entry
        # with no named twin may be a real MI-set texture: normal rules apply.
        detail['reason'] = f'stem duplicate of param {twin}'
        return f'tex_unnamed_unlinked:{canon}', INFO, detail
    if is_core(canon):
        # A core param with an explicit off switch ("Don't use Packed Mask"
        # True) is not sampled in game either. R12: a master-keyed read gate
        # (MASTER_PARAM_GATES) wins over the name-read gate when it decides.
        state, evidence = master_param_gate(facts.get('master'), canon, switches)
        if state is None:
            state, evidence = param_gate(canon, switches)
        if state is False and evidence:
            detail.update(gate=state, switches=evidence)
            return f'tex_gated_off:{canon}', INFO, detail
        detail['reason'] = 'core layout'
        return f'tex_param_unlinked:{canon}', DEFECT, detail
    # R13: master-keyed read gates also decide non-core params (XEM Mask).
    state, evidence = master_param_gate(facts.get('master'), canon, switches)
    if state is None:
        state, evidence = param_gate(canon, switches)
    detail.update(gate=state, switches=evidence)
    proof = AO_PIN_ONLY.get(master_base(facts.get('master')))
    if state is False and evidence and proof and AO_PARAM.search(canon):
        detail['reason'] = proof
        return f'ao_pin_only:{canon}', INFO, detail
    if overridden is False:
        if state is True:
            detail['reason'] = 'root default sampled: gate on'
            return f'tex_param_unlinked:{canon}', DEFECT, detail
        return f'tex_default_unlinked:{canon}', INFO, detail
    if state is False:
        return f'tex_gated_off:{canon}', INFO, detail
    detail['reason'] = 'overridden, gate ' + ('on' if state else 'unknown') if overridden else 'override unknown'
    return f'tex_param_unlinked:{canon}', DEFECT, detail


def layout_severity(param, facts, linked, param_sev=None):
    """Severity of a layout_unknown flag: follows the param carrying the texture.

    A param in a layer whose gate is not on, or a non-core inherited default
    whose own gate is not on, is never sampled by the game: info. Otherwise a
    sampled texture (linked, or an unlinked defect) is a defect. R21: an
    unlinked param without a caller verdict takes classify_unlinked's severity
    (gated-off Packed Mask, stem duplicate, unread param: info).
    """
    canon = canonical(param)
    switches = facts.get('switches') or {}
    if param_sev is None:
        param_sev = DEFECT
        if not linked and 'textures' in facts and 'switches' in facts:
            param_sev = classify_unlinked(canon, facts)[1]
    layer = layer_of(canon)
    if layer and (not linked or layer_gate(layer, switches)[0] is False):
        return INFO  # skipped layer; an unlinked layer param keeps its layer flag
    if layer in WORLD_STATE_LAYERS and layer_rule(
            layer, *layer_gate(layer, switches),
            ((facts.get('textures') or {}).get(canon) or {}).get('overridden'))[1] == INFO:
        # Frost/snow/waterline/lidar not enabled on the MI: the look is world
        # state (layer_world_state), so its texture layout is never sampled here.
        return INFO
    tex = (facts.get('textures') or {}).get(canon, {})
    if (not linked and not is_core(canon) and tex.get('overridden') is False
            and param_gate(canon, switches)[0] is not True):
        return INFO
    return DEFECT if (linked or param_sev == DEFECT) else INFO


def summarize(severities):
    counts = {DEFECT: 0, INCONSISTENCY: 0, INFO: 0}
    for sev in severities.values():
        counts[sev] = counts.get(sev, 0) + 1
    return counts
