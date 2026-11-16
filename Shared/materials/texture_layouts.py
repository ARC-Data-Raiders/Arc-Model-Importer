"""Single packed-texture layout table shared by the builders and the audit.

Pure Python (no bpy). ``LAYOUTS`` maps a texture-name suffix code to its
(channel, role) wiring; the rebuild surface builder reads it first and only
falls back to its letter parser for codes not listed here. ``NAMED_SUFFIXES``
are suffixes the other builders resolve by parameter name or a fixed recipe.
The audit treats every key of either table as a known layout.

Entries below carry only channels proven by game data:
  NHM  - master param ``NHM`` ships ``T_SolarPanel_01_NOM`` as its default,
         so NHM is the NOM sheet (same wiring as the NOM letter parse).
  NOX  - master ``M_PropPreset_CR+NOX+XEM`` names the sheet: RG normal, B AO.
  NAOH - sibling NAO (RG normal, B AO); alpha channel use is unproven, unwired.
  NAA, NH - only bound to normal params (Detail Normal, NOH Blend, Blend
         Normal); RG normal proven, B/A unproven, unwired.
Round 4 (evidence: prop-audit evidence/suffix_layouts.md; master param names
and srgb flags from mi_params.jsonl, channel statistics of 3 extracted
textures per suffix):
  CH   - params 'BaceColor/Height', '1. CH', BaseColor (srgb True); RGB
         channels correlate (colour), A smooth non-binary: colour + A height.
  ID   - params 'ID Wear Mask', 'Paintjob Pattern Mask', 'Weapon ID'; RGB
         >96% binary, RG not unit vectors: three region masks.
  NR, NTR, NTX, NRH, NXM, NRC, NMD, NC, NXH - bound to normal params
         (Normals, Normal/Roughness, Normal Trim, DamageNormal, Holes ...);
         RG unit-length on >96% of texels: RG normal proven. B/A roles are
         not separable from the stats (roughness/height/holes guesses);
         unwired and listed in ``UNPROVEN_CHANNELS`` (builder notes them
         ``approximate:``).
  Left unknown: CW, CRA, BCH (one sample each or mixed params), NM (master
  param is 'Frost Fresnel Mask Breakup' but texels are a normal map).

``PARAM_LAYOUTS`` keys (parameter, suffix) where the suffix alone is
ambiguous but the master reads the parameter with a proven channel use:
  Overlay + CX   - M_PropTrimPreset_CR+NOM+AO SM5 base pass reads Overlay R
                   as the blend mask (native/tileable_trim.py).
  NOH Blend + NX - same program decodes NOH Blend as RG normal only.
  NA + NHA/NOA   - NA is the compact NAO param (classify.py): RG normal, B AO,
                   A opacity; the master's read, not the file name, decides.
  BaseColor / TrunkBaseColor + CX - core colour params read RGB.
  NX / Normal / PM_Normals + NX - R10: graphic_atlas decodes param 'NX' as a
                   normal (generic_families.py); environment.py reads Normal and
                   PM_Normals as the normal lookup. RG normal only.
  Normal / PM_Normals + NAM - R16: two MIs bind the same _NAM sheet to param
                   'Normal'; both params are the normal lookup. RG normal only.
                   approximate: B/A unproven.
  Packed Mask + XAM - R10: CR+NOX+XEM master reads Packed Mask through the XEM
                   plan; the _XAM sheet's B is metallic (environment.py note,
                   MI_WeatherMonitor_WindDirection_01_A). approximate: X/A unproven.
  R23 (evidence: prop-audit evidence/xem_layout.md; 25 of 60 _XAM Packed Mask
  textures extracted, channel stats):
  PM_SpecularMasks + XAM - CUE4Parse alias of Packed Mask: 0 MIs in mi_params
                   name PM_SpecularMasks; 5 checked MIs bind the same _XAM sheet
                   as Packed Mask (Use Masks on, Use single Mask off). Same
                   wiring as Packed Mask + XAM (environment.py mask_keys).
  Packed Mask + NX - only texture is T_Default_01_NX (constant .502/.502/1/.412,
                   std 0 on every channel), the engine default the XEM plan reads
                   B from; any read is exact.
  Mask + XAM     - single-mask branch of the XEM plan reads Mask.R only
                   (environment.py _xem_prop_mask_plan).
  XAM G stays unwired: G is ~1.0 constant on 7/9 BLEND_Opaque MIs and varies
  on most BLEND_Masked MIs (opacity-mask pattern), but the master's opacity
  comes from Use Slice Mask/DitherOpacityMask, not a named channel. Unproven.
  NormalMap + NX/NAH - M_Nature_01 param NormalMap is in environment.py's
                   normal lookup list; NX is T_Default_01_NX (flat RG), the 2 NAH
                   sheets are RG unit-length on 100% of texels. RG normal only;
                   approximate: NAH B/A unproven.
  Left unknown: NX on mask params (3. Blend Mask, PaintBreakup).
  R33 (evidence: prop-audit evidence/r33_layouts.md; r23_texprobe channel
  stats; rg_unit does not separate colour from normal, the proof is RG mean
  ~0.5 with |corr(R,G)| < 0.4 versus corr 1.0 on colour sheets):
  CR / CR Blend / 2. CR + CW - masters M_ArchitecturePreset_Concrete_01 and
                   M_Res_Architecture_Snow_01 read these params as CR (584 of 597
                   bound textures are _CR). The one _CW sheet has RGB colour and A
                   constant 0.6 (std 0), so the CR read (A roughness) is exact.
  CH + BCH       - M_ConcreteTrim_UXR param CH; T_ConcreteTrimUXR_BCH RGB corr
                   0.99-1.0 (colour), A smooth non-binary: same as CH.
  Normals + NA/NTA - M_Vegetation_01 param Normals (81 of 121 bound sheets are
                   _NTR, already RG normal). 6 NA + 5 NTA sheets: RG mean
                   0.47-0.52, std 0.07-0.22, corr(R,G) -0.04..0.37. RG normal
                   only; B/A (binary 0.4-0.98, alpha-like) unproven, unwired.
  T_Default_01_NX as its own param key - the constant engine default (see
                   Packed Mask + NX); every read is exact.
  R36 (evidence: prop-audit tmp/r36_ndd.py on the extracted sheet):
  NDD            - only T_BrokenGlassSDF_NDD (M_BrokenGlassSDF default; no MI
                   binds an _NDD sheet). 2048x512; RG mean 0.50/0.50, std 0.06,
                   100% of texels inside the unit disk, corr(R,G) 0.03 -> RG
                   normal. B median 1.0, 86% binary, 256 levels, corr(B,RG)
                   ~0 -> crack cutout mask (generic_families.build_broken_glass
                   reads B as the SDF). A (mean 0.77) unproven, unwired.
  Left unknown: NX on mask params (B varies,
  channel the master reads is unproven), Overlay + CW.
"""
import re

LAYOUTS = {'CR': [('color', 'color'), ('a', 'rough')], 'CA': [('color', 'color'), ('a', 'alpha')],
           'CS': [('color', 'color'), ('a', 'spec')], 'CM': [('color', 'color'), ('a', 'metal')],
           'NCR': [('normal', 'normal'), ('b', 'detail_color'), ('a', 'rough')],
           'NCH': [('normal', 'normal'), ('b', 'detail_color'), ('a', 'height')],
           'NRA': [('normal', 'normal'), ('b', 'rough'), ('a', 'alpha')],
           'NXR': [('normal', 'normal'), ('a', 'rough')],
           'NOM': [('normal', 'normal'), ('b', 'ao'), ('a', 'metal')],
           'NHM': [('normal', 'normal'), ('b', 'ao'), ('a', 'metal')],
           'NOX': [('normal', 'normal'), ('b', 'ao')],
           'NAOH': [('normal', 'normal'), ('b', 'ao')],
           'NAA': [('normal', 'normal')],
           'NH': [('normal', 'normal')],
           'CH': [('color', 'color'), ('a', 'height')],
           'ID': [('r', 'mask'), ('g', 'mask'), ('b', 'mask')],
           **{code: [('normal', 'normal')] for code in ('NR', 'NTR', 'NTX', 'NRH', 'NXM', 'NRC', 'NMD', 'NC', 'NXH')},
           'XEM': [('g', 'emission'), ('b', 'metal')],
           'NDD': [('normal', 'normal'), ('b', 'mask')],
           'GRM': [('g', 'rough'), ('b', 'metal')]}

# Channels the suffix letters suggest but game data does not prove (unwired).
UNPROVEN_CHANNELS = {'NR': 'B/A roughness', 'NTR': 'B/A', 'NTX': 'B/A', 'NRH': 'B roughness, A height',
                     'NXM': 'A metal', 'NRC': 'B/A', 'NMD': 'B/A', 'NC': 'B/A', 'NXH': 'A holes mask', 'NDD': 'A'}

NAMED_SUFFIXES = (
    'A', 'AO', 'ARM', 'B', 'BC', 'BN', 'C', 'CR', 'CRM', 'Color', 'D', 'DA', 'DM', 'E', 'EM', 'Emissive', 'GRM',
    'H', 'HX', 'M', 'MA', 'Mask', 'Masks', 'MRAE', 'MRA', 'MRO', 'N', 'NAO', 'NMX', 'NOH', 'NOM', 'Normal', 'NXX',
    'NRM', 'O', 'OP', 'ORM', 'R', 'RMA', 'Roughnessmetal', 'S', 'T', 'Tint', 'Tintmask', 'TM', 'X', 'XM',
)

_NAO = [('normal', 'normal'), ('b', 'ao'), ('a', 'alpha')]
PARAM_LAYOUTS = {('overlay', 'CX'): [('r', 'mask')],
                 ('noh blend', 'NX'): [('normal', 'normal')],
                 ('nx', 'NX'): [('normal', 'normal')], ('normal', 'NX'): [('normal', 'normal')],
                 ('pm_normals', 'NX'): [('normal', 'normal')],
                 ('pm_normals', 'NAM'): [('normal', 'normal')], ('normal', 'NAM'): [('normal', 'normal')],
                 ('packed mask', 'XAM'): [('b', 'metal')], ('pm_specularmasks', 'XAM'): [('b', 'metal')],
                 ('packed mask', 'NX'): [('b', 'metal')], ('mask', 'XAM'): [('r', 'mask')],
                 ('normalmap', 'NX'): [('normal', 'normal')], ('normalmap', 'NAH'): [('normal', 'normal')],
                 ('na', 'NHA'): _NAO, ('na', 'NOA'): _NAO,
                 ('basecolor', 'CX'): [('color', 'color')],
                 ('trunkbasecolor', 'CX'): [('color', 'color')],
                 **{(p, 'CW'): LAYOUTS['CR'] for p in ('cr', 'cr blend', '2. cr')},
                 ('ch', 'BCH'): LAYOUTS['CH'],
                 ('normals', 'NA'): [('normal', 'normal')], ('normals', 'NTA'): [('normal', 'normal')],
                 ('t_default_01_nx', 'NX'): [('normal', 'normal')]}

KNOWN_SUFFIXES = frozenset(s.casefold() for s in (*NAMED_SUFFIXES, *LAYOUTS))


_CODE = re.compile(r'[A-Z]{1,5}')


def layout_code(stem):
    """Layout-code suffix of a texture stem, or '' for a plain name word.

    Codes are short upper-case tokens (NM, CX, NAOH). Words such as
    ``Grayscale``, ``Meshes``, ``tex`` or ``13x13`` are part of the asset name.
    Long known words (``Normal``, ``Tintmask``) still count as suffixes.
    """
    text = str(stem or '').replace('\\', '/').rsplit('/', 1)[-1].split('.', 1)[0]
    if '_' not in text:
        return ''
    raw = text.rsplit('_', 1)[-1]
    if raw.casefold() in KNOWN_SUFFIXES or _CODE.fullmatch(raw):
        return raw.casefold()
    return ''


def param_layout(param, suffix):
    """Proven (param, suffix) wiring, or None."""
    name = str(param or '').strip()
    name = name[7:] if name.startswith('Global.') else name
    return PARAM_LAYOUTS.get((name.casefold(), str(suffix or '').upper()))


def is_known(param, suffix):
    return str(suffix or '').casefold() in KNOWN_SUFFIXES or param_layout(param, suffix) is not None
