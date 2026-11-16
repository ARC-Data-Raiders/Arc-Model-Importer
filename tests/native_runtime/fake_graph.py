"""Symbolic stand-in for the rebuild Graph: every op returns an expression string, so legacy builders run without bpy."""
from contextlib import contextmanager
import importlib,sys,types
try:
    from . import native_test_support as s
except ImportError:
    import native_test_support as s


def rebuild(name):
    s.real_package('Shared.materials.native')
    k=s.PACKAGE+'.Shared.materials.rebuild'
    if k not in sys.modules:
        m=types.ModuleType(k);m.__path__=[str(s.ROOT/'Shared/materials/rebuild')];sys.modules[k]=m
    return importlib.import_module(k+'.'+name)


class _Scale(tuple):
    def to_scale(self):return (1,1,1)


class _Obj:
    matrix_world=_Scale()
    class data:color_attributes=[]


class FakeGraph:
    def __init__(self):
        self.obj=_Obj();self.report={'warnings':[],'implemented_stages':[]};self.params={};self.images=[];self.out=None
    def _f(self,name,*a,**k):
        return name+'('+','.join(map(str,a))+(','+','.join(f'{x}={y}' for x,y in k.items()) if k else '')+')'
    @contextmanager
    def block(self,label):yield self
    section=block
    def export(self,v,label,typ=None):return v
    def parameter(self,label,value,typ='Float'):self.params[label]=value;return 'P['+label+']'
    def value(self,v,label=''):return str(v)
    def time(self,label='Time (seconds)'):return self._f('TIME',label)
    def color(self,v,label=''):return str(tuple(v))
    def math(self,op,a,b=0,c=0,label=''):return self._f(op,a,b)
    def add(self,a,b):return self.math('ADD',a,b)
    def sub(self,a,b):return self.math('SUBTRACT',a,b)
    def mul(self,a,b):return self.math('MULTIPLY',a,b)
    def div(self,a,b):return self.math('DIVIDE',a,b)
    def clamp(self,a):return self._f('CLAMP',a)
    def mix(self,a,b,f,label='lerp'):return self._f('MIX',a,b,f)
    def cmul(self,a,b,label='multiply'):return self._f('CMUL',a,b)
    def split(self,v):return (self._f('X',v),self._f('Y',v),self._f('Z',v))
    def combine(self,x,y,z=0):return self._f('VEC',x,y,z)
    def vector(self,op,a,b=(0,0,0),scale=1):return self._f('V'+op,a,b,scale)
    def uv(self,channel=0):return 'UV'+str(channel)
    def transform_uv(self,channel=0,scale=1,offset=(0,0),rotation=0):return f'UVT({channel},{scale})'
    def image(self,ref,uv=None,layer=None,srgb=None,label='',required=True):
        if not ref:return None
        self.images.append(label);n='T['+label+']'
        return {'color':n+'.rgb','r':n+'.r','g':n+'.g','b':n+'.b','a':n+'.a','alpha':n+'.a'}
    def rnm(self,a,b):return self._f('RNM',a,b)
    def decode_normal(self,tex,strength=1):return self._f('NORMAL',tex.get('r'),tex.get('g'))
    def finish(self,color,roughness=.5,metallic=0,normal=None,specular=.5,**k):
        self.out={'color':color,'roughness':roughness,'metallic':metallic,'normal':normal,'specular':specular,**k}
