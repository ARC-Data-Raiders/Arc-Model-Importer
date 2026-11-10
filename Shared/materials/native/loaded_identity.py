"""Bind native build identities to code loaded by this interpreter.
This is a restart guard, not a hot reload facility or a security attestation.
"""
import hashlib,json,pathlib,sys,types

MARKER='_dr_loaded_source_identity_v1'
class RestartRequired(RuntimeError):pass

def _code_digest(code,memo=None):
    memo={} if memo is None else memo
    if id(code) in memo:return memo[id(code)]
    def constant(value):
        if isinstance(value,types.CodeType):return ('code',_code_digest(value,memo))
        if isinstance(value,tuple):return ('tuple',tuple(constant(item) for item in value))
        if isinstance(value,frozenset):return ('frozenset',tuple(sorted((constant(item) for item in value),key=repr)))
        return (type(value).__name__,repr(value))
    # Exclude filenames and line tables. Include executable operands, constants,
    # nested code, closure/argument structure and exception handling.
    facts=(code.co_argcount,code.co_posonlyargcount,code.co_kwonlyargcount,
           code.co_nlocals,code.co_stacksize,code.co_flags,code.co_code.hex(),
           tuple(constant(value) for value in code.co_consts),code.co_names,
           code.co_varnames,code.co_freevars,code.co_cellvars,
           getattr(code,'co_exceptiontable',b'').hex())
    result=hashlib.sha256(repr(facts).encode('utf8')).hexdigest();memo[id(code)]=result;return result

def capture_loaded_source(namespace):
    """Call at module scope after its definitions; bind executing frame to bytes."""
    frame=sys._getframe(1)
    try:
        path=pathlib.Path(namespace['__file__']).resolve();data=path.read_bytes()
        installed=compile(data,frame.f_code.co_filename,'exec',dont_inherit=True,optimize=sys.flags.optimize)
        loaded_code=_code_digest(frame.f_code);installed_code=_code_digest(installed)
        namespace[MARKER]={'path':str(path),'source_sha256':hashlib.sha256(data).hexdigest(),
                          'loaded_code_sha256':loaded_code,'installed_code_sha256':installed_code,
                          'valid':loaded_code==installed_code}
    except Exception as error:
        namespace[MARKER]={'valid':False,'reason':type(error).__name__+': '+str(error)}
    finally:del frame

def require_loaded_identity(module):
    """Return captured loaded SHA only while its original installed source agrees."""
    marker=getattr(module,MARKER,None);name=getattr(module,'__name__','unknown module')
    if not isinstance(marker,dict):raise RestartRequired('Restart Blender: '+name+' has no load-time source identity')
    if not marker.get('valid'):raise RestartRequired('Restart Blender: '+name+' loaded code disagrees with installed source; stale bytecode or an unsupported loader may be present')
    try:
        path=pathlib.Path(module.__file__).resolve()
        if path!=pathlib.Path(marker['path']):raise RestartRequired('Restart Blender: '+name+' module path changed after load')
        from .repository import digest
        current=digest(path)
    except RestartRequired:raise
    except (OSError,ValueError) as error:raise RestartRequired('Restart Blender: '+name+' installed source cannot be verified') from error
    if current!=marker['source_sha256']:raise RestartRequired('Restart Blender: '+name+' source changed after this module loaded')
    return {'module':name.rsplit('.',1)[-1],'source_sha256':marker['source_sha256'],'loaded_code_sha256':marker['loaded_code_sha256']}

def implementation_identity(modules):
    values=[require_loaded_identity(module) for module in modules]
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()

capture_loaded_source(globals())
