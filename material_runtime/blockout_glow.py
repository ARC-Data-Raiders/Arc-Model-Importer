"""Static Blockout Glow equation recovered from its current cooked SM5 program.
SwirlTexture=False is covered; Pulsing=True (MI_Glow_Pulse_Green, pixel 1F6CC0D6) multiplies the glow by
1 + (DamagePercent + .2) * (sin(2pi * time * PulsingFrequency * 10 * (DamagePercent + 1)) + 1), frozen at time 0,
which is also its mean. Engine exposure remains explicit.
"""
import math
from .core import Expr,MaterialPlan,expr,literal,normalize_material_settings
from .repository import package_ref

MASTER='/Game/Pioneer/MaterialLibrary/M_Presets/M_BlockoutPreset_Glow'
GRADIENT='/Game/Pioneer/MaterialLibrary/Textures/Util/T_Hme_LinearGradient_01_X'
EXPOSURE_KEY='UE.ExposureReciprocal'
EVIDENCE=('native-sm5-resource:4749573C28EBFFC0C58AF70A35FAE33F96EB2210',
 'pixel:602F37178BA2EEF249D506168B99B816B5849A18',
 'uniform-preshader-fields:0-10;MPC:D592764A-453D85BD-2B2B8598-CB1C469D:scalar0')
PULSING_EVIDENCE=('native-sm5-resource:7646A85FABD993EEC3BCE0C5196B3A59950024C1',
 'pixel:1F6CC0D631C8BA2A6878E2E6E112248952142CDD5562CD7ADE958AA47BBEB5F5',
 'uniform-preshader-fields:cb2[3].w=PulsingFrequency*(DamagePercent+1)*10;cb2[4].x=DamagePercent+.2')

def _e(value):return value if isinstance(value,Expr) else literal(value)
def _op(name,*values,**attrs):return expr(name,*(_e(value) for value in values),**attrs)
def _scalar(record,name,default):
 value=record.scalars[name] if name in record.scalars else default
 if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('Invalid finite native scalar: '+name)
 return float(value)
def _color(record,name,default):
 value=record.colors[name] if name in record.colors else default
 if len(value)<3 or any(not isinstance(x,(int,float)) or not math.isfinite(x) for x in value):raise ValueError('Invalid native color: '+name)
 return tuple(value[:3])
def _input(value,name,positive=False):
 if isinstance(value,Expr):return value
 if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or (positive and value<=0):raise ValueError('Invalid renderer input: '+name)
 return literal(float(value))

def compile_blockout_glow(record,context=None):
 context={} if context is None else context;diagnostics=[]
 def refuse(reason):return MaterialPlan(record.package,'blockout_glow',{},record.settings,('unsupported: '+reason,),EVIDENCE)
 if package_ref(record.master or record.package)!=MASTER:return refuse('exact Blockout Glow master identity required')
 active=[name for name in ('SwirlTexture',) if record.switches.get(name,False)]
 if active:return refuse('unextracted active compiled permutation: '+','.join(active))
 pulsing=bool(record.switches.get('Pulsing',False))
 try:
  color=_color(record,'Color',(1.,1.,1.));charging=_color(record,'ChargingUpColor',(0.,0.,0.));no_power=_color(record,'NoPowerColor',(0.,0.,0.))
  charged=_op('mix',charging,color,_scalar(record,'ChargingUpPercent',1.))
  powered=_op('mix',no_power,charged,_scalar(record,'PowerPercent',1.))
  mixed=_op('mix',color,powered,_scalar(record,'ShouldUsePowerForColor',0.),stage='DR.Surface',label='Native charge and power color state')
  amount=_scalar(record,'GradientAmount',0.)
  if amount==0.:gradient=literal(1.)
  else:
   texture=expr('texture',reference=GRADIENT,role='data',uv=expr('uv',index=0,stage='DR.Coordinates'),stage='DR.Source',label='Native fixed gradient R')
   gradient=_op('mix',1.,expr('channel',texture,index=0),amount)
  if 'vertex_ndotv' in context:ndotv=_input(context['vertex_ndotv'],'vertex_ndotv')
  else:
   ndotv=expr('dot',expr('geometry_normal'),expr('incoming'),stage='DR.Coordinates',label='Blender vertex-normal view transport')
   diagnostics.append('Blender shading-normal/view transport; exact native unnormalized vertex interpolant may differ on curved meshes')
  grazing=_op('max',_op('abs',_op('subtract',1.,_op('max',ndotv,0.))),.0001)
  native_facing=_op('add',_op('multiply',grazing,.96),.04)
  gain=_op('multiply',gradient,_scalar(record,'Intensity',1.))
  gain=_op('multiply',gain,_op('subtract',1.,_op('multiply',native_facing,.8)))
  gain=_op('multiply',gain,_scalar(record,'DamagePercent',0.)+1.)
  if pulsing:
   gain=_op('multiply',gain,_scalar(record,'DamagePercent',0.)+1.2)
   diagnostics.append('Pulsing frozen at time 0 (the pulse mean): glow x (1.2 + DamagePercent)')
  if 'fx_emissive_intensity' in context:mpc=_input(context['fx_emissive_intensity'],'fx_emissive_intensity')
  else:
   mpc=literal(1.);diagnostics.append('FX Emissive Intensity uses current native MPC_FX_GlobalParameters scalar0 default1; runtime collection changes require explicit context')
  if 'eye_adaptation' in context and 'exposure_reciprocal' in context:raise ValueError('Supply eye_adaptation or exposure_reciprocal, not both')
  if 'eye_adaptation' in context:exposure=_op('divide',1.,_input(context['eye_adaptation'],'eye_adaptation',positive=True))
  elif 'exposure_reciprocal' in context:exposure=_input(context['exposure_reciprocal'],'exposure_reciprocal')
  else:
   exposure=expr('preview_parameter',key=EXPOSURE_KEY,default=1.,stage='DR.Light',label='Game exposure reciprocal (preview)',policy='game eye-adaptation value unavailable; Blender neutral preview uses1')
   diagnostics.append('game eye-adaptation value unavailable; Blender neutral preview uses1')
  emission=_op('multiply',mixed,_op('multiply',gain,_op('multiply',mpc,exposure)))
  selection=context.get('selection_color',record.colors.get('SelectionColor',(0.,0.,0.,0.)))
  if not isinstance(selection,(tuple,list)) or len(selection)!=4 or any(not isinstance(x,(int,float)) or not math.isfinite(x) for x in selection):raise ValueError('selection_color requires four finite values')
  emission=_op('max',_op('mix',emission,tuple(selection[:3]),selection[3]),0.,stage='DR.Light',label='Native exposure-compensated glow and editor selection')
  settings=dict(record.settings)
  if not any(key in settings for key in ('blend','BlendMode','blend_mode')):settings['blend']=0
  normalized,_=normalize_material_settings(settings)
  if normalized['blend']!='opaque':raise ValueError('unextracted nonopaque Blockout Glow blend permutation')
  for name in ('shading_model','ShadingModel'):
   if name in settings and str(settings[name]).rsplit('::',1)[-1].lower() not in ('default_lit','msm_defaultlit','1'):raise ValueError('unextracted Blockout Glow shading-model override')
  settings.setdefault('shading_model','default_lit')
  return MaterialPlan(record.package,'blockout_glow',{'base_color':_op('clamp',mixed,0.,1.),'roughness':literal(.85),'metallic':literal(0.),'specular':literal(.5),'emission':emission,'emission_strength':literal(1.)},settings,tuple(diagnostics),EVIDENCE+PULSING_EVIDENCE if pulsing else EVIDENCE)
 except (ValueError,KeyError,TypeError) as error:return refuse(str(error))

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
