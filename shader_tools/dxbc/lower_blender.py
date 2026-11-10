def math(nt,op,*args,label=None,clamp=False):
    node=nt.nodes.new('ShaderNodeMath')
    node.operation=op
    node.label=label or op.lower()
    node.use_clamp=clamp
    for i,value in enumerate(args):
        if isinstance(value,(int,float)):node.inputs[i].default_value=float(value)
        else:nt.links.new(value,node.inputs[i])
    return node.outputs[0]


def add(nt,a,b):return math(nt,'ADD',a,b,label='add')
def mul(nt,a,b):return math(nt,'MULTIPLY',a,b,label='mul')
def minimum(nt,a,b):return math(nt,'MINIMUM',a,b,label='min')
def maximum(nt,a,b):return math(nt,'MAXIMUM',a,b,label='max')
def mad(nt,a,b,c):return math(nt,'ADD',math(nt,'MULTIPLY',a,b,label='mad'),c,label='mad')
def div(nt,a,b):return math(nt,'DIVIDE',a,b,label='div')
def rcp(nt,a):return math(nt,'DIVIDE',1.0,a,label='rcp')


def dot(nt,a,b,label):
    value=math(nt,'MULTIPLY',a[0],b[0],label=label)
    for x,y in zip(a[1:],b[1:]):value=math(nt,'ADD',value,math(nt,'MULTIPLY',x,y,label=label),label=label)
    return value


def dp2(nt,a,b):return dot(nt,a,b,'dp2')
def dp3(nt,a,b):return dot(nt,a,b,'dp3')
def dp4(nt,a,b):return dot(nt,a,b,'dp4')
def sqrt(nt,a):return math(nt,'SQRT',a,label='sqrt')
def rsq(nt,a):return math(nt,'INVERSE_SQRT',a,label='rsq')
def exp(nt,a):return math(nt,'POWER',2.0,a,label='exp')
def log(nt,a):return math(nt,'LOGARITHM',a,2.0,label='log')
def sincos(nt,a,selector=0):
    turns=math(nt,'FRACT',math(nt,'MULTIPLY',a,0.15915493667125702,label='sincos'),label='sincos')
    return math(nt,'SINE' if selector==0 else 'COSINE',math(nt,'MULTIPLY',turns,6.2831854820251465,label='sincos'),label='sincos')
def frc(nt,a):return math(nt,'FRACT',a,label='frc')
def round_ni(nt,a):return math(nt,'FLOOR',a,label='round_ni')
def round_pi(nt,a):return math(nt,'CEIL',a,label='round_pi')
def round_z(nt,a):return math(nt,'TRUNC',a,label='round_z')


def round_ne(nt,a):
    def m(op,*args):return math(nt,op,*args,label='round_ne')
    t=m('FLOOR',m('ADD',a,0.5))
    tie=m('COMPARE',m('SUBTRACT',t,a),0.5,0.0)
    odd=m('SUBTRACT',t,m('MULTIPLY',2.0,m('FLOOR',m('MULTIPLY',t,0.5))))
    return m('SUBTRACT',t,m('MULTIPLY',tie,odd))


def sat(nt,a):return math(nt,'MULTIPLY',a,1.0,label='sat',clamp=True)
def read_neg(nt,a):return math(nt,'MULTIPLY',a,-1.0,label='read neg')
def read_abs(nt,a):return math(nt,'ABSOLUTE',a,label='read abs')
def read_ftz(nt,a):return a
def lt(nt,a,b):return math(nt,'LESS_THAN',a,b,label='lt')
def ge(nt,a,b):return math(nt,'SUBTRACT',1.0,math(nt,'LESS_THAN',a,b,label='ge'),label='ge')
def eq(nt,a,b):return math(nt,'COMPARE',a,b,0.0,label='eq')
def ne(nt,a,b):return math(nt,'SUBTRACT',1.0,math(nt,'COMPARE',a,b,0.0,label='ne'),label='ne')


def select(nt,c,a,b):
    def m(op,*args):return math(nt,op,*args,label='select')
    return m('ADD',m('MULTIPLY',a,c),m('MULTIPLY',b,m('SUBTRACT',1.0,c)))


def movc(nt,c,a,b):
    def m(op,*args):return math(nt,op,*args,label='movc')
    return m('ADD',m('MULTIPLY',a,c),m('MULTIPLY',b,m('SUBTRACT',1.0,c)))
def mask_and(nt,a,b):return math(nt,'MULTIPLY',a,b,label='and')
def mask_or(nt,a,b):return math(nt,'MAXIMUM',a,b,label='or')
def mask_not(nt,a):return math(nt,'SUBTRACT',1.0,a,label='not')
def ieq(nt,a,b):return math(nt,'COMPARE',a,b,0.0,label='ieq')
def ine(nt,a,b):return math(nt,'SUBTRACT',1.0,math(nt,'COMPARE',a,b,0.0,label='ine'),label='ine')
def iadd(nt,a,b):return math(nt,'ADD',a,b,label='iadd')
def imul(nt,a,b):return math(nt,'MULTIPLY',a,b,label='imul')
def imad(nt,a,b,c):return math(nt,'ADD',math(nt,'MULTIPLY',a,b,label='imad'),c,label='imad')
def umin(nt,a,b):return math(nt,'MINIMUM',a,b,label='umin')
def umax(nt,a,b):return math(nt,'MAXIMUM',a,b,label='umax')
def utof(nt,a):return a
def itof(nt,a):return a
def nonzero_idiom(nt,a):return math(nt,'SUBTRACT',1.0,math(nt,'COMPARE',a,0.0,0.0,label='utof_umin_and'),label='utof_umin_and')


def sample(nt,image,u,v,component,cache=None,slot=0):
    cache={} if cache is None else cache
    key=(slot,u.as_pointer() if hasattr(u,'as_pointer') else u,v.as_pointer() if hasattr(v,'as_pointer') else v)
    if key not in cache:
        vec=nt.nodes.new('ShaderNodeCombineXYZ')
        vec.label='sample'
        size_u,size_v=image.size
        def centre(c,size):
            scaled=math(nt,'MULTIPLY',math(nt,'FRACT',c,label='sample'),float(size),label='sample')
            q=math(nt,'FLOOR',math(nt,'ADD',math(nt,'MULTIPLY',scaled,256.0,label='sample'),0.5,label='sample'),label='sample')
            k=math(nt,'FLOOR',math(nt,'MULTIPLY',q,1.0/256.0,label='sample'),label='sample')
            return math(nt,'MULTIPLY',math(nt,'ADD',k,0.5,label='sample'),1.0/size,label='sample')
        for i,value in enumerate((centre(u,size_u),math(nt,'SUBTRACT',1.0,centre(v,size_v),label='sample'),0.0)):
            if isinstance(value,(int,float)):vec.inputs[i].default_value=float(value)
            else:nt.links.new(value,vec.inputs[i])
        tex=nt.nodes.new('ShaderNodeTexImage')
        tex.label='sample'
        tex.image=image
        tex.interpolation='Closest'
        tex.extension='REPEAT'
        nt.links.new(vec.outputs[0],tex.inputs['Vector'])
        sep=nt.nodes.new('ShaderNodeSeparateColor')
        sep.label='sample'
        nt.links.new(tex.outputs['Color'],sep.inputs[0])
        cache[key]=list(sep.outputs[:3])+[tex.outputs['Alpha']]
    return cache[key][component]
