"""Evidence-bearing native uniform-default projection; no renderer lookup assumption."""
from dataclasses import replace
from collections.abc import Mapping
import base64,json,struct,math
from .repository import package_ref,_color,_bool
from .core import freeze,serialize

class UniformDefaultsError(ValueError): pass


def _info(parameter):
    info=parameter.get('ParameterInfo') or parameter.get('ParameterInfoOld') or {}
    if isinstance(info,str): info={'Name':info}
    name=info.get('Name',parameter.get('ParameterName'))
    if name is None or str(name) in ('','None'): return None
    association=str(info.get('Association','GlobalParameter')).split('::')[-1]
    return (association,int(info.get('Index',-1)),str(name))


def _vector(value):
    if isinstance(value,Mapping):
        keys='RGBA' if all(key in value for key in 'RGBA') else 'XYZW' if all(key in value for key in 'XYZW') else None
        if keys is None: raise UniformDefaultsError('Uniform vector does not provide four explicit components')
        return tuple(float(value[key]) for key in keys)
    if len(value)!=4: raise UniformDefaultsError('Uniform vector requires four native components')
    return tuple(float(component) for component in value)


def _value(parameter,kind,uniform):
    for key in ('DefaultValue','Value'):
        if key in parameter and parameter[key] is not None:
            value=parameter[key]
            return float(value) if kind=='scalars' else _vector(value) if kind=='colors' else _bool(value)
    if kind not in ('scalars','colors') or 'DefaultValueOffset' not in parameter: raise UniformDefaultsError('No decoded native uniform default value')
    raw=uniform.get('DefaultValues')
    if isinstance(raw,str): data=base64.b64decode(raw,validate=True)
    elif isinstance(raw,(list,tuple)): data=bytes(raw)
    else: raise UniformDefaultsError('No native default-value byte buffer')
    offset=int(parameter['DefaultValueOffset']); size=4 if kind=='scalars' else 16
    if offset<0 or offset+size>len(data): raise UniformDefaultsError('Uniform default offset outside native byte buffer')
    values=struct.unpack_from('<f' if kind=='scalars' else '<4f',data,offset)
    return values[0] if kind=='scalars' else values


def _flat(entries):
    for entry in entries or ():
        if isinstance(entry,(list,tuple)): yield from _flat(entry)
        elif isinstance(entry,Mapping): yield entry


def _flat_occurrences(entries,path=()):
    for index,entry in enumerate(entries or ()):
        occurrence=path+(index,)
        if isinstance(entry,(list,tuple)):yield from _flat_occurrences(entry,occurrence)
        elif isinstance(entry,Mapping):yield occurrence,entry


def _scope_target(parameters,kind,info):
    association,index,name=info
    return parameters[kind] if association=='GlobalParameter' else parameters['scopes'].setdefault((association,index),{}).setdefault(kind,{})


def _resources(material,selector):
    result=[]
    for index,resource in enumerate(material.get('LoadedMaterialResources',())):
        shader=resource.get('LoadedShaderMap',{}); identity=shader.get('ShaderMapId',{})
        descriptor={key:identity[key] for key in ('QualityLevel','FeatureLevel','CookedShaderMapIdHash') if key in identity}
        descriptor['resource_index']=index
        if shader.get('ResourceHash') is not None:descriptor['resource_hash']=shader['ResourceHash']
        for key in ('Platform','ShaderPlatform'):
            if key in shader: descriptor[key]=shader[key]
            elif key in resource: descriptor[key]=resource[key]
        def matches():
            for key,wanted in selector.items():
                if key not in descriptor: return False
                actual=descriptor[key]
                if isinstance(wanted,str) and isinstance(actual,str):
                    if actual.split('::')[-1].casefold()!=wanted.split('::')[-1].casefold(): return False
                elif actual!=wanted: return False
            return True
        if selector and not matches(): continue
        uniform=shader.get('Content',{}).get('MaterialCompilationOutput',{}).get('UniformExpressionSet')
        if uniform is not None: result.append((descriptor,uniform))
    if selector and not result: raise UniformDefaultsError('No native uniform resource matches selector '+str(selector))
    return result


def project_uniform_defaults(material,record, *, selector=None,policy='preserve-authored'):
    """Supplement missing master defaults with compiled consensus, retaining conflicts.

    Cached/editor values versus compiled fallback values do not establish render-proxy
    lookup semantics. Default policy keeps authored observations and records both. The
    caller may explicitly request compiled-consensus; that policy remains diagnosed.
    Material-instance Properties overrides always remain authoritative.
    """
    if policy not in ('preserve-authored','compiled-consensus','defer-bindings'): raise ValueError('Unknown native uniform policy')
    resources=_resources(material,(selector or {}) if material.get('Type')=='Material' else {})
    report={'policy':policy,'selector':dict(selector or {}),'resources':[descriptor for descriptor,_ in resources],'recovered':[],'already_present':[],'conflicts':[],'missing':[],'unnamed_texture_constants':0,'constant_textures':[],'deferred_bindings':[],**({'uniform_bindings':[]} if policy=='defer-bindings' else {})}
    if material.get('Type')!='Material':
        report['instance_uniform_defaults_not_promoted']=True
        return record,report
    parameters={kind:dict(getattr(record,kind)) for kind in ('scalars','colors','textures','switches','settings')}
    parameters['scopes']={scope:{kind:dict(values) for kind,values in scoped.items()} for scope,scoped in record.scopes.items()}
    explicit={kind:set(material.get('Properties',{}).get(kind,{})) for kind in ('scalars','colors','textures','switches')}
    for kind,field in (('scalars','ScalarParameterValues'),('colors','VectorParameterValues'),('textures','TextureParameterValues'),('switches','StaticSwitchParameters')):
        for entry in material.get('Properties',{}).get(field,()):
            info=_info(entry)
            if info is not None and entry.get('bOverride',True): explicit[kind].add(info)
    cached=material.get('CachedExpressionData',material.get('Properties',{}).get('CachedExpressionData',{}))
    if 'ReferencedTextures' in cached:
        references=cached['ReferencedTextures']; report['texture_index_table']='CachedExpressionData.ReferencedTextures'
    else:
        references=material.get('ReferencedTextures',material.get('Properties',{}).get('ReferencedTextures',())); report['texture_index_table']='Material.ReferencedTextures'
    candidates={};binding_groups={}
    def observe(kind,info,value,descriptor,table,occurrence,parameter):
        if info is None:return
        row={'kind':kind,'info':info,'value':serialize(value),'resource':descriptor,'table':table,'occurrence':occurrence,'native_parameter':serialize(parameter)}
        binding_groups.setdefault((kind,info),[]).append(row)
        if policy=='defer-bindings':report['uniform_bindings'].append(row)
        candidates.setdefault((kind,info),[]).append((value,descriptor))
    for descriptor,uniform in resources:
        for kind,field in (('scalars','UniformScalarParameters'),('colors','UniformVectorParameters')):
            for occurrence,parameter in _flat_occurrences(uniform.get(field,())):
                info=_info(parameter)
                if info is None: continue
                try: observe(kind,info,_value(parameter,kind,uniform),descriptor,field,occurrence,parameter)
                except (ValueError,TypeError,KeyError) as error: report['missing'].append({'kind':kind,'info':info,'reason':str(error),'resource':descriptor})
        for parameter_index,parameter in enumerate(uniform.get('UniformNumericParameters',())):
            typ=str(parameter.get('ParameterType','')).split('::')[-1]
            kind={'Scalar':'scalars','Vector':'colors','StaticSwitch':'switches'}.get(typ); info=_info(parameter)
            if info is None: continue
            if kind is None: report['missing'].append({'kind':typ,'info':info,'reason':'unsupported native numeric type','resource':descriptor}); continue
            try: observe(kind,info,_value(parameter,kind,uniform),descriptor,'UniformNumericParameters',(parameter_index,),parameter)
            except (ValueError,TypeError,KeyError) as error: report['missing'].append({'kind':kind,'info':info,'reason':str(error),'resource':descriptor})
        for occurrence,parameter in _flat_occurrences(uniform.get('UniformTextureParameters',())):
            info=_info(parameter)
            if info is None:
                report['unnamed_texture_constants']+=1
                try:
                    index=int(parameter.get('TextureIndex',parameter.get('DefaultValue',-1)))
                    reference=package_ref(references[index]) if 0<=index<len(references) else ''
                    if not reference: raise UniformDefaultsError('Fixed native texture index has no exact reference')
                    report['constant_textures'].append({'reference':reference,'texture_index':index,'resource':descriptor,'table':'UniformTextureParameters','occurrence':occurrence,'native_parameter':serialize(parameter)})
                except (ValueError,TypeError,KeyError) as error: report['missing'].append({'kind':'constant-texture','reason':str(error),'resource':descriptor})
                continue
            try:
                if 'TextureIndex' in parameter: index=int(parameter['TextureIndex']); value=references[index] if 0<=index<len(references) else None
                elif 'DefaultValue' in parameter and isinstance(parameter['DefaultValue'],int):
                    index=parameter['DefaultValue']; value=references[index] if 0<=index<len(references) else None
                else: value=parameter.get('DefaultValue')
                if value is None: raise UniformDefaultsError('Uniform texture index has no exact referenced texture')
                observe('textures',info,package_ref(value),descriptor,'UniformTextureParameters',occurrence,parameter)
            except (ValueError,TypeError,KeyError) as error: report['missing'].append({'kind':'textures','info':info,'reason':str(error),'resource':descriptor})
    for (kind,info),observations in candidates.items():
        association,index,name=info; target=_scope_target(parameters,kind,info)
        unique={json.dumps(serialize(value),sort_keys=True):value for value,_ in observations}
        item={'kind':kind,'info':info,'compiled_values':list(unique.values()),'resources':[descriptor for _,descriptor in observations],'bindings':binding_groups[(kind,info)]}
        authored=target.get(name); present=name in target
        is_explicit=info in explicit[kind] or (association=='GlobalParameter' and name in explicit[kind])
        if len(unique)!=1:
            per_resource={}
            for value,descriptor in observations:per_resource.setdefault(descriptor['resource_index'],set()).add(json.dumps(serialize(value),sort_keys=True))
            item.update(authored_value=authored,status='explicit-properties-preserved' if is_explicit else 'expression-default-conflict',ambiguity='within-resource-expression-defaults' if any(len(values)>1 for values in per_resource.values()) else 'cross-resource-defaults')
            report['conflicts'].append(item)
            if policy=='defer-bindings' and not is_explicit:
                target.pop(name,None)
                report['deferred_bindings'].append(item)
                report['missing'].append({'kind':kind,'info':info,'reason':'Unresolved native expression bindings; no singular named default selected','bindings':binding_groups[(kind,info)]})
                continue
            if not present:
                reason='Distinct native per-expression defaults require binding-level resolution: ' if item['ambiguity']=='within-resource-expression-defaults' else 'Different native resource defaults require an exact selector: '
                raise UniformDefaultsError(reason+name)
            continue
        value=next(iter(unique.values()))
        if not present:
            target[name]=value; item['value']=value; report['recovered'].append(item)
        elif serialize(authored)==serialize(value): report['already_present'].append({'kind':kind,'info':info})
        else:
            item.update(authored_value=authored,status='explicit-properties-preserved' if is_explicit else 'cached-or-editor-versus-compiled-unresolved')
            if policy=='compiled-consensus' and not is_explicit: target[name]=value; item['status']='declared-compiled-policy-not-runtime-proof'
            report['conflicts'].append(item)
    return replace(record,**parameters),report

# Bind this module's source identity at import; overwrites require restart.
from .loaded_identity import capture_loaded_source as _capture_loaded_source
_capture_loaded_source(globals())
