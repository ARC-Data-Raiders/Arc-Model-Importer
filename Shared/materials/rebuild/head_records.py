"""Head master roles and verified fixed texture bindings; never infer skin alpha from stubble."""
SHARED='PioneerGame/Content/Pioneer/Characters/Heads/Shared'
MASTERS={
    'm_face':'head_face','m_teeth':'head_teeth','m_eyelashes':'head_eyelashes',
    'm_eyerefractive':'head_eyes','m_eyerefractive_2':'head_eyes',
    'm_eyeocclusion':'head_occlusion','m_tearline':'head_tearline','m_lacrimal_fluid':'head_lacrimal',
    'm_hair_metahuman_01':'hair','m_hair_metahuman_colormap':'hair',
}
FIXED={
    'head_face':{'Micro Normal':SHARED+'/Textures/Head_01_MicroNormal',
                 'Zone Masks':SHARED+'/Face/Textures/T_Head_01_ZONEMASKS/T_ContestantHeads_ZONEMASKS',
                 'Neck Fade':SHARED+'/Textures/T_Face_NeckFade'},
    'head_teeth':{'BaseColor':SHARED+'/Mouth/Textures/teeth_color_map',
                  'Normal':SHARED+'/Mouth/Textures/teeth_normal_map'},
    'head_eyes':{'Eye Normals':SHARED+'/Eyes/Textures/T_EYE_NORMALS'},
    'head_eyelashes':{'Eyelash Texture':SHARED+'/Eyes/Textures/T_Eyelashes_M'},
    'head_lacrimal':{'Lacrimal Normal':SHARED+'/Eyes/Textures/T_lacrimal_n',
                    'Lacrimal Height':SHARED+'/Eyes/Textures/T_lacrimal_h'},
}


def family(record):
    return MASTERS.get(record.get('master','').rsplit('/',1)[-1].casefold())


def augment(record):
    kind=family(record)
    if not kind:return record
    result=dict(record);result['family']=kind;result['textures']=dict(record['textures'])
    for name,reference in FIXED.get(kind,{}).items():result['textures'].setdefault(name,reference)
    return result
