import struct
from . import lower_blender as lb
from . import volume


def _reciprocal_zero_bits(node):
    if node['op'] != 'const' or node['args'][0] not in (0, 0x80000000):
        return None
    return -1 if node['args'][0] else 1


def _reciprocal_match(nodes, target):
    if target['op'] != 'sat':
        return None
    product = nodes[target['args'][0]]
    if product['op'] != 'mul':
        return None
    for numerator, reciprocal in (product['args'], product['args'][::-1]):
        reciprocal = nodes[reciprocal]
        if reciprocal['op'] != 'div':
            continue
        unit, denominator = (nodes[index] for index in reciprocal['args'])
        if unit['op'] != 'const' or unit['args'] != [0x3F800000]:
            continue
        sign = _reciprocal_zero_bits(denominator)
        branches = []
        if sign is not None:
            branches.append((None, sign))
        elif denominator['op'] in ('movc', 'select'):
            for branch in (1, 2):
                sign = _reciprocal_zero_bits(nodes[denominator['args'][branch]])
                if sign is not None:
                    branches.append((branch, sign))
        if branches:
            return numerator, denominator['id'], branches
    return None


def _reciprocal_correction(blender, tree, nodes, memo, dependencies, target, value):
    numerator, denominator, branches = _reciprocal_match(nodes, target)
    total = 0.0
    for branch, sign in branches:
        gate = 1.0
        if branch is not None:
            key = dependencies[(denominator, 'float')][0]
            condition = memo[key]
            if key[1] != 'mask':
                condition = blender.nonzero_idiom(tree, condition)
            gate = condition if branch == 1 else blender.math(tree, 'SUBTRACT', 1.0, condition)
        comparison = blender.math(tree, 'GREATER_THAN' if sign > 0 else 'LESS_THAN',
                                  memo[(numerator, 'float')], 0.0)
        total = blender.math(tree, 'ADD', total, blender.math(tree, 'MULTIPLY', gate, comparison))
    return blender.math(tree, 'ADD', blender.sat(tree, value), total, label='reciprocal zero branch')


class Unsupported(Exception):
    pass


def lower(nt,dag,sources,images,outputs,mode,samplers=None):
    nodes=dag['nodes']
    errors={}
    demands=set()
    dependencies={}
    idioms={}
    masks={}
    def fail(node,reason):
        errors[node['id']]=dict(id=node['id'],op=node['op'],src=node['src'],reason=reason)
    def isconst(ident,value=None):
        n=nodes[ident]
        return n['op']=='const' and (value is None or n['args'][0]==value)
    def mask(ident):
        if ident in masks:return masks[ident]
        n=nodes[ident]
        value=n['type']=='bool-mask'
        if n['op'] in ['and','or','not']:value=value or all(mask(a) or isconst(a,0) or isconst(a,0xFFFFFFFF) for a in n['args'] if type(a) is int)
        masks[ident]=value
        return value
    for n in nodes:
        if n['op']!='utof' or len(n['args'])!=1:continue
        u=nodes[n['args'][0]]
        if u['op']!='umin' or len(u['args'])!=2 or not isconst(u['args'][1],1):continue
        a=nodes[u['args'][0]]
        if a['op']=='and' and len(a['args'])==2 and isconst(a['args'][1],0x7FFFFFFF) and nodes[a['args'][0]]['type']=='f32':
            idioms[n['id']]=a['args'][0]
    unary={'rcp','sqrt','rsq','exp','log','frc','round_ni','round_pi','round_z','round_ne','sat'}
    binary={'add','mul','min','max','div','lt','ge','eq','ne'}
    def need(ident,context='float'):
        key=(ident,context)
        if key in demands:return
        demands.add(key)
        n=nodes[ident]
        op,args=n['op'],n['args']
        deps=[]
        if ident in idioms:deps=[(idioms[ident],'float')]
        elif op=='const':
            value=args[0]
            if context=='mask' and value not in [0,0xFFFFFFFF]:fail(n,'unsupported:mask_const')
            elif context=='int' and not 0<=value<2**24:fail(n,'unsupported:int_const')
        elif op=='input':
            if tuple(args) not in sources:fail(n,'unsupported:input_source')
        elif op in ['and','or','not']:
            for a in args:
                if type(a) is not int or not (mask(a) or isconst(a,0) or isconst(a,0xFFFFFFFF)):fail(n,'unsupported:nonmask_'+op)
                elif type(a) is int:deps.append((a,'mask'))
        elif op in ['ieq','ine']:
            for a in args:
                if mask(a) or isconst(a,0) or isconst(a,0xFFFFFFFF):deps.append((a,'mask'))
                elif isconst(a) and 0<=nodes[a]['args'][0]<2**24:deps.append((a,'int'))
                else:fail(n,'unsupported:int_operand')
        elif op in ['select','movc']:
            if len(args)!=3:fail(n,'unsupported:operand_form')
            else:deps=[(args[0],'mask' if mask(args[0]) or isconst(args[0],0) or isconst(args[0],0xFFFFFFFF) else 'float'),(args[1],context),(args[2],context)]
        elif op=='mov':
            if len(args)!=2 or not isinstance(args[-1],dict) or args[-1].get('read') is not True:fail(n,'unsupported:mov_form')
            else:deps=[(args[0],'float')]
        elif op=='sample':
            if len(args)<3 or not isinstance(args[-1],dict) or args[-1]['slot'] not in images:fail(n,'unsupported:sample_form')
            elif isinstance(images[args[-1]['slot']],volume.Volume):
                if len(args)<4 or (samplers or {}).get((args[-1]['slot'],args[-1].get('sampler'))) != volume.POLICY or args[-1].get('volume_opcode','sample_indexable(texture3d)(float,float,float,float)') != 'sample_indexable(texture3d)(float,float,float,float)':fail(n,'unsupported:volume_sampler_or_coordinates')
                else:deps=[(args[0],'float'),(args[1],'float'),(args[2],'float')]
            else:deps=[(args[0],'float'),(args[1],'float')]
        elif op=='sincos':
            if len(args)!=2 or not isinstance(args[-1],dict) or args[-1].get('result') not in [0,1]:fail(n,'unsupported:sincos_form')
            else:deps=[(args[0],'float')]
        elif op in unary or op in binary or op=='mad' or op in ['dp2','dp3','dp4']:
            count=1 if op in unary else 2 if op in binary else 3 if op=='mad' else int(op[-1])*2
            if len(args)!=count or not all(type(a) is int for a in args):fail(n,'unsupported:operand_form')
            else:deps=[(a,'float') for a in args]
        else:fail(n,'unsupported:'+op)
        dependencies[key]=deps
        for a,ctx in deps:need(a,ctx)
    for name in outputs:need(dag['outputs'][name],'mask' if name=='kill' else 'float')
    if mode not in ['check','production']:raise Unsupported('unsupported:mode:'+str(mode))
    if errors:raise Unsupported(str([errors[k] for k in sorted(errors)]))
    memo={}
    samples={}
    functions={'min':lb.minimum,'max':lb.maximum,'and':lb.mask_and,'or':lb.mask_or,'not':lb.mask_not}
    def socket(value):
        if isinstance(value,(int,float)):
            n=nt.nodes.new('ShaderNodeValue')
            n.outputs[0].default_value=float(value)
            return n.outputs[0]
        return value
    def production_sample(n,args):
        meta=n['args'][-1]
        policy=(samplers or {}).get((meta['slot'],meta.get('sampler')))
        if policy not in (None,'bilinear_clamp'):
            raise Unsupported('unsupported:sampler_policy:'+str(policy))
        key=(meta['slot'],meta.get('sampler'),policy,n['args'][0],n['args'][1])
        if key not in samples:
            vec=nt.nodes.new('ShaderNodeCombineXYZ')
            for i,value in enumerate([args[0],lb.math(nt,'SUBTRACT',1.0,args[1],label='sample'),0.0]):
                if isinstance(value,(int,float)):vec.inputs[i].default_value=float(value)
                else:nt.links.new(value,vec.inputs[i])
            tex=nt.nodes.new('ShaderNodeTexImage')
            tex.image=images[meta['slot']]
            tex.interpolation='Linear'
            tex.extension='EXTEND' if policy=='bilinear_clamp' else 'REPEAT'
            nt.links.new(vec.outputs[0],tex.inputs['Vector'])
            sep=nt.nodes.new('ShaderNodeSeparateColor')
            nt.links.new(tex.outputs['Color'],sep.inputs[0])
            samples[key]=list(sep.outputs[:3])+[tex.outputs['Alpha']]
        return samples[key][meta['component']]
    for ident,context in sorted(demands):
        n=nodes[ident]
        op,args=n['op'],n['args']
        values=[memo[key] for key in dependencies[(ident,context)]]
        before=set(nt.nodes)
        if ident in idioms:value=lb.nonzero_idiom(nt,*values)
        elif op=='const':
            raw=args[0]
            value=(1.0 if raw else 0.0) if context=='mask' else float(raw) if context=='int' else struct.unpack('<f',struct.pack('<I',raw))[0]
        elif op=='input':value=sources[tuple(args)]
        elif op in ['select','movc']:
            condition=values[0]
            if dependencies[(ident,context)][0][1]!='mask':condition=lb.nonzero_idiom(nt,condition)
            value=getattr(lb,op)(nt,condition,*values[1:])
        elif op=='mov':
            value=values[0]
            if args[-1]['abs']:value=lb.read_abs(nt,value)
            if args[-1]['neg']:value=lb.read_neg(nt,value)
        elif op in ['dp2','dp3','dp4']:
            count=int(op[-1])
            value=getattr(lb,op)(nt,values[:count],values[count:])
        elif op=='sincos':value=lb.sincos(nt,values[0],args[-1]['result'])
        elif op=='sample':
            meta=args[-1]
            if isinstance(images[meta['slot']],volume.Volume):
                value=volume.lower(nt,images[meta['slot']],values,meta['component'],samples,meta['slot'],meta.get('sampler'))
            else:value=lb.sample(nt,images[meta['slot']],*values,meta['component'],cache=samples,slot=meta['slot']) if mode=='check' else production_sample(n,values)
        elif op == 'sat' and _reciprocal_match(nodes, n) is not None:
            value = _reciprocal_correction(lb, nt, nodes, memo, dependencies, n, values[0])
        else:value=(functions[op] if op in functions else getattr(lb,op))(nt,*values)
        value=socket(value)
        memo[(ident,context)]=value
        for bnode in nt.nodes:
            if bnode not in before:bnode.label='id='+str(ident)+' src='+str(n['src'])+' op='+op
    return {name:memo[(dag['outputs'][name],'mask' if name=='kill' else 'float')] for name in outputs}
