"""Original generic E-mask preview policy, explicitly not native shader proof.

Gathered E is a scalar mask. Authored emission hue and gain stay separate from
that mask; exact XEM/Blockout native profiles retain their dedicated equations.
"""
import math

RECIPE='original-generic-emission-mask-hue-v1'
EXACT_MASTERS=frozenset(('m_proppreset_cr+nox+xem','m_blockoutpreset_glow'))
COLOR_NAMES=('EmissiveColor','Emissive Color','Emission Color','Emissive Tint')
GAIN_NAMES=('EmissiveIntensity','Emissive Intensity','Emissive Power','Emission Strength','Emissive Strength')
STATE_NAMES=('Use Emissive','UseEmissive','Enable Emissive','Emissive Enabled')
CR_NAMES=('Use CR as Emissive','UseCRAsEmissive')

def master_name(record):return str(record.get('master','')).rsplit('/',1)[-1].split('.',1)[0].casefold()
def applies_to_cache(record):return record.get('family') in ('surface','fallback') and master_name(record) not in EXACT_MASTERS

def apply_preview(g,record,surface_color,mask):
    """Return (emissionHue, scalarStrength); never reinterpret a native profile."""
    if mask is None or master_name(record) in EXACT_MASTERS:return mask,1
    colors=record.get('colors',{});scalars=record.get('scalars',{});switches=record.get('switches',{})
    state=next((name for name in STATE_NAMES if name in switches),None)
    cr=next((name for name in CR_NAMES if name in switches),None)
    color_key=next((name for name in COLOR_NAMES if name in colors),None)
    gain_key=next((name for name in GAIN_NAMES if name in scalars),None)
    report={'recipe':RECIPE,'accuracy':'original_preview_policy','native_verified':False,'mask_role':'gathered scalar E channel; native packing not independently reconstructed','state_parameter':state,'state_interpretation':'authored switch gate' if state is not None else 'preview assumes gatheredE visible; native state unavailable','hue_parameter':None,'gain_parameter':gain_key,'gain_interpretation':'nonnegative linear preview gain; native operator not reconstructed','unsupported_transport':['native channel/permutation arithmetic outside exact profiles','runtime state, pulse, exposure and damage transport']}
    if state is not None and not bool(switches[state]):
        report.update(enabled=False,hue_source='disabled authored switch');g.report['emission_preview']=report;g.report['engine_parity_verified']=False;return None,0
    if cr is not None and bool(switches[cr]):
        hue=surface_color;source='explicit_use_cr_surface_color';report['hue_parameter']=cr
    elif color_key is not None:
        hue=colors[color_key];source='authored_emission_color';report['hue_parameter']=color_key
    elif cr is not None:
        hue=(1.,1.,1.,1.);source='neutral_missing_authored_hue';g.report['warnings'].append('Generic emission preview has explicit Use-CR=false but no authored emission color; neutral hue retained')
    else:
        hue=surface_color;source='resolved_surface_color_preview';g.report['warnings'].append('Generic emission hue unavailable; original preview uses resolved surface color, not a native shader claim')
    gain=float(scalars[gain_key]) if gain_key is not None else 1.
    if not math.isfinite(gain):gain=0.;g.report['warnings'].append('Nonfinite generic emission gain disabled in preview')
    gain=max(gain,0.)
    strength=g.mul(mask,gain)
    report.update(enabled=True,hue_source=source,gain_value=gain);g.report['emission_preview']=report;g.report['engine_parity_verified']=False
    g.report['warnings'].append('Original generic emission preview separates scalar E mask, hue and linear gain; native runtime transport remains unverified')
    return hue,strength
