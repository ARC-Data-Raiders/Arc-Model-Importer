import argparse
import collections
import json
import pathlib
import re
import numpy as np
from .fixture import oracle_discard_mask


class Unsupported(Exception):
    pass


class IterationLimit(Exception):
    pass


def ftz(value):
    value=np.asarray(value,dtype=np.float32)
    return np.where(np.abs(value)<np.float32(2**-126),np.copysign(np.float32(0),value),value).astype(np.float32)


def bits(value):
    return np.asarray(value,dtype=np.float32).view(np.uint32)


def floating(value):
    return ftz(np.asarray(value,dtype=np.uint32).view(np.float32))


def mask_bits(value):
    return np.where(value,np.uint32(0xFFFFFFFF),np.uint32(0))


def per_op(op,args):
    u=[np.asarray(a,dtype=np.uint32) for a in args]
    f=[floating(a) for a in u]
    i=[a.view(np.int32) for a in u]
    with np.errstate(all='ignore'):
        if op=='mov':return [u[0]]
        if op=='movc':return [np.where(u[0]!=0,u[1],u[2])]
        if op=='swapc':return [np.where(u[0]!=0,u[2],u[1]),np.where(u[0]!=0,u[1],u[2])]
        if op in ['add','mul','div','mad','min','max']:
            if op=='add':v=ftz(f[0]+f[1])
            elif op=='mul':v=ftz(f[0]*f[1])
            elif op=='div':v=ftz(f[0]/f[1])
            elif op=='mad':v=ftz(ftz(f[0]*f[1])+f[2])
            elif op=='min':v=ftz(np.fmin(f[0],f[1]))
            else:v=ftz(np.fmax(f[0],f[1]))
            return [bits(v)]
        if op in ['dp2','dp3','dp4']:
            count=int(op[-1]);v=ftz(f[0][...,0]*f[1][...,0])
            for c in range(1,count):v=ftz(v+ftz(f[0][...,c]*f[1][...,c]))
            return [bits(np.repeat(v[...,None],4,axis=-1))]
        unary={'sqrt':np.sqrt,'rsq':lambda a:np.float32(1)/np.sqrt(a),'rcp':lambda a:np.float32(1)/a,'exp':np.exp2,'log':np.log2,'frc':lambda a:a-np.floor(a),'round_ne':np.rint,'round_ni':np.floor,'round_pi':np.ceil,'round_z':np.trunc}
        if op in unary:return [bits(ftz(unary[op](f[0])))]
        if op=='sincos':
            turns=(f[0]*np.float32(1/(2*np.pi))).astype(np.float64);angle=(turns-np.floor(turns))*(2*np.pi)
            return [bits(ftz(np.sin(angle).astype(np.float32))),bits(ftz(np.cos(angle).astype(np.float32)))]
        compares={'lt':lambda a,b:a<b,'ge':lambda a,b:a>=b,'eq':lambda a,b:a==b,'ne':lambda a,b:a!=b}
        if op in compares:return [mask_bits(compares[op](f[0],f[1]))]
        if op=='iadd':return [u[0]+u[1]]
        if op=='imul':return [u[0]*u[1]]
        if op=='imad':return [u[0]*u[1]+u[2]]
        if op=='ishl':return [u[0]<<(u[1]&np.uint32(31))]
        if op=='ishr':return [(i[0]>>(u[1]&np.uint32(31))).astype(np.uint32)]
        if op=='ushr':return [u[0]>>(u[1]&np.uint32(31))]
        if op=='and':return [u[0]&u[1]]
        if op=='or':return [u[0]|u[1]]
        if op=='xor':return [u[0]^u[1]]
        if op=='not':return [~u[0]]
        if op=='ineg':return [np.uint32(0)-u[0]]
        if op in ['ieq','ine','ilt','ige','ult','uge']:
            a,b=(u[:2] if op in ['ult','uge','ieq','ine'] else i[:2]);v={'ieq':np.equal,'ine':np.not_equal,'ilt':np.less,'ige':np.greater_equal,'ult':np.less,'uge':np.greater_equal}[op](a,b)
            return [mask_bits(v)]
        if op in ['umin','umax','imin','imax']:
            a,b=(u[:2] if op[0]=='u' else i[:2]);v=(np.minimum if op.endswith('min') else np.maximum)(a,b)
            return [v.astype(np.uint32)]
        if op=='udiv':
            zero=u[1]==0;den=np.where(zero,np.uint32(1),u[1]);return [np.where(zero,np.uint32(0xFFFFFFFF),u[0]//den),np.where(zero,np.uint32(0xFFFFFFFF),u[0]%den)]
        if op=='itof':return [bits(i[0].astype(np.float32))]
        if op=='utof':return [bits(u[0].astype(np.float32))]
        if op in ['ftoi','ftou']:
            v=np.nan_to_num(f[0].astype(np.float64),nan=0,posinf=np.inf,neginf=-np.inf);lo,hi=(-2147483648,2147483647) if op=='ftoi' else (0,4294967295)
            return [np.clip(np.trunc(v),lo,hi).astype(np.int64).astype(np.uint32)]
        if op=='f16tof32':return [bits((u[0]&np.uint32(65535)).astype(np.uint16).view(np.float16).astype(np.float32))]
        if op=='f32tof16':return [f[0].astype(np.float16).view(np.uint16).astype(np.uint32)]
        if op in ['bfi','ubfe','ibfe']:
            width=u[0]&np.uint32(31);offset=u[1]&np.uint32(31);fieldmask=(np.uint64(1)<<width.astype(np.uint64))-np.uint64(1)
            if op=='bfi':
                m=(fieldmask<<offset.astype(np.uint64)).astype(np.uint32);v=((u[2]<<offset)&m)|(u[3]&~m)
            else:
                v=(u[2]>>offset)&fieldmask.astype(np.uint32)
                if op=='ibfe':
                    sign=np.uint32(1)<<np.maximum(width.astype(np.int64)-1,0).astype(np.uint32);v=np.where((width!=0)&((v&sign)!=0),v|(~fieldmask.astype(np.uint32)),v).astype(np.uint32)
            return [v]
        if op=='sat':return [bits(np.nan_to_num(np.clip(f[0],0,1),nan=0).astype(np.float32))]
    raise Unsupported('interp_unsupported:'+op)


def texel_index(t,size):
    return (np.floor(np.rint(np.asarray(t,dtype=np.float64)*256)/256).astype(np.int64))%size


def texel_excluded(t):
    d=np.asarray(t,dtype=np.float64)+1/512
    return np.abs(d-np.rint(d))<.001


class Interpreter:
    def __init__(self,shader,fixture):
        self.shader=shader;self.fixture=fixture;self.h=fixture['height'];self.w=fixture['width'];self.shape=(self.h,self.w,4);self.reg={};self.cb={};self.resources={};self.indexables={};self.excluded=np.zeros(self.shape[:2],bool);self.discarded=np.zeros(self.shape[:2],bool);self.returned=np.zeros(self.shape[:2],bool)
        for declaration in shader['declarations']:
            if declaration['op']=='dcl_temps':
                for n in range(int(declaration['text'])):self.reg['r'+str(n)]=np.zeros(self.shape,np.uint32)
            if declaration['op']=='dcl_indexableTemp':
                match=re.fullmatch(r'x(\d+)\[(\d+)\],\s*(\d+)',declaration['text'])
                if not match:raise Unsupported('interp_unsupported:operand:'+declaration['text'])
                self.indexables[int(match[1])]=np.zeros((int(match[2]),)+self.shape,np.uint32)
        py,px=np.indices(self.shape[:2],dtype=np.float32);nx=(px+np.float32(.5))/np.float32(8)-np.float32(1);ny=np.float32(1)-(py+np.float32(.5))/np.float32(8)
        for row in shader['signatures']['input']:
            name='v'+row['Register'];self.reg.setdefault(name,np.zeros(self.shape,np.uint32))
            if row['SysValue']=='POS':self.reg[name][:]=bits(np.stack((px+.5,py+.5,np.full_like(px,.5),np.ones_like(px)),axis=-1))
            elif row['SysValue']=='FFACE':self.reg[name][...,0]=np.uint32(0xFFFFFFFF if fixture['frontface'] else 0)
            elif row['SysValue']!='NONE':raise Unsupported('interp_unsupported:input:'+row['SysValue'])
        for c in fixture['vertex']['coefficients']:
            v=np.full(self.shape[:2],c['a0'],np.float32 if c['format']=='float' else np.uint32)
            if c['a1'] is not None:v=np.asarray(np.float32(c['a0'])+np.float32(c['a1'])*nx+np.float32(c['a2'])*ny,dtype=np.float32)
            self.reg['v'+str(c['register'])][...,'xyzw'.index(c['component'])]=bits(v) if c['format']=='float' else v.astype(np.uint32)
        vin=pathlib.Path(fixture['job']).parent/'vin.json'
        if not vin.is_file():raise FileNotFoundError('X3 captured inputs missing: '+str(vin))
        for name,member in json.loads(vin.read_text())['members'].items():
            self.reg.setdefault('v'+name[1:],np.zeros(self.shape,np.uint32))[...,:member['width']]=np.fromfile(vin.parent/member['file'],dtype='<u4').reshape(self.h,self.w,4)[...,:member['width']]
        for row in shader['signatures']['output']:self.reg.setdefault('o'+row['Register'] if row['Register'].isdigit() else row['Register'],np.zeros(self.shape,np.uint32))
        for r in fixture['resources']:
            if r['kind']=='sampler':continue
            data=np.fromfile(r['file'],dtype='<u4',offset=148 if r['kind'].startswith('texture') else 0)
            if r['kind']=='cb':self.cb[r['slot']]=data.reshape(-1,4)
            else:self.resources[r['slot']]=(r,data)
        self.active=np.ones(self.shape[:2],bool)

    def index(self,text):
        if re.fullmatch(r'\d+',text):return np.full(self.shape[:2],int(text),np.int64)
        match=re.fullmatch(r'(r\d+\.[xyzw])\s*(?:\+\s*(\d+))?',text)
        if not match:raise Unsupported('interp_unsupported:operand:'+text)
        return self.read_text(match[1])[...,0].view(np.int32).astype(np.int64)+int(match[2] or 0)

    def read_text(self,text,float_mod=False):
        neg=text.startswith('-');text=text[1:] if neg else text;absolute=text.startswith('|') and text.endswith('|');text=text[1:-1] if absolute else text
        match=re.fullmatch(r'(r\d+|v\d+|o\d+)(?:\.([xyzw]+))?',text)
        if match:
            if match[1] not in self.reg:raise Unsupported('interp_unsupported:operand:'+text)
            value=self.reg[match[1]];swizzle=match[2] or 'xyzw'
        elif text.startswith('l(') and text.endswith(')'):
            tokens=[v.strip() for v in text[2:-1].split(',')]
            if not all(re.fullmatch(r'0x[0-9a-fA-F]+',v) for v in tokens):raise Unsupported('interp_unsupported:operand:'+text)
            values=[int(v,16) for v in tokens]
            if len(values)==1:values*=4
            if len(values)!=4:raise Unsupported('interp_unsupported:operand:'+text)
            value=np.broadcast_to(np.array(values,np.uint32),self.shape);swizzle='xyzw'
        else:
            match=re.fullmatch(r'(cb|x)(\d+)\[(.+)\](?:\.([xyzw]+))?',text)
            if not match:raise Unsupported('interp_unsupported:operand:'+text)
            kind,slot,expression,swizzle=match.groups();slot=int(slot);swizzle=swizzle or 'xyzw';idx=self.index(expression)
            if kind=='cb':
                if slot not in self.cb:raise Unsupported('interp_unsupported:operand:'+text)
                buf=self.cb[slot];valid=(idx>=0)&(idx<len(buf));value=buf[np.clip(idx,0,len(buf)-1)];value=np.where(valid[...,None],value,np.uint32(0))
            else:
                if slot not in self.indexables:raise Unsupported('interp_unsupported:operand:'+text)
                buf=self.indexables[slot];valid=(idx>=0)&(idx<len(buf));yy,xx=np.indices(self.shape[:2]);value=buf[np.clip(idx,0,len(buf)-1),yy,xx];value=np.where(valid[...,None],value,np.uint32(0))
        if len(swizzle)==1:swizzle*=4
        if len(swizzle)!=4:raise Unsupported('interp_unsupported:operand:'+text)
        value=value[...,["xyzw".index(c) for c in swizzle]]
        if float_mod:
            v=floating(value)
            if absolute:v=np.abs(v)
            if neg:v=-v
            return bits(v)
        if absolute:raise Unsupported('interp_unsupported:operand:abs_integer')
        return np.uint32(0)-value if neg else value.copy()

    def write(self,text,value,active,saturate=False):
        if text=='null':return
        value=np.broadcast_to(value,self.shape)
        if saturate:value=per_op('sat',[value])[0]
        match=re.fullmatch(r'(r\d+|o\d+|oDepth|oDepthLE|oDepthGE)(?:\.([xyzw]+))?',text)
        if match:
            name,mask=match.groups();mask=mask or ('x' if name.startswith('oDepth') else 'xyzw');target=self.reg.setdefault(name,np.zeros(self.shape,np.uint32));idx=None
        else:
            match=re.fullmatch(r'x(\d+)\[(.+)\](?:\.([xyzw]+))?',text)
            if not match:raise Unsupported('interp_unsupported:operand:'+text)
            slot,expression,mask=match.groups();mask=mask or 'xyzw';target=self.indexables[int(slot)];idx=self.index(expression)
        for letter in mask:
            c='xyzw'.index(letter)
            if idx is None:target[...,c]=np.where(active,value[...,c],target[...,c])
            else:
                valid=active&(idx>=0)&(idx<len(target));yy,xx=np.nonzero(valid);target[idx[valid],yy,xx,c]=value[yy,xx,c]

    def resource(self,op,operands,active,suffix):
        if op.startswith('ld_structured_indexable'):
            dst,element,offset,resource=operands;match=re.fullmatch(r't(\d+)(?:\.([xyzw]+))?',resource);r,data=self.resources[int(match[1])];stride=int(re.search(r'stride=(\d+)',op)[1]);idx=self.read_text(element)[...,0].astype(np.uint64)*(stride//4)+self.read_text(offset)[...,0].astype(np.uint64)//4;value=np.zeros(self.shape,np.uint32)
            for c in range(4):
                at=idx+c;valid=(at<len(data));value[...,c]=np.where(valid,data[np.minimum(at,len(data)-1).astype(np.int64)],np.uint32(0))
        elif op.startswith('ld_indexable'):
            dst,coords,resource=operands;match=re.fullmatch(r't(\d+)(?:\.([xyzw]+))?',resource);r,data=self.resources[int(match[1])];coord=self.read_text(coords).view(np.int32);value=np.zeros(self.shape,np.uint32)
            if r['kind']=='buffer':
                idx=coord[...,0];buf=data.reshape(-1,4);valid=(idx>=0)&(idx<len(buf));value=np.where(valid[...,None],buf[np.clip(idx,0,len(buf)-1)],np.uint32(0))
            elif r['kind'] in ['texture2d','texture2darray']:
                x,y=coord[...,0],coord[...,1];layer=coord[...,2] if r['layers']>1 else np.zeros_like(x);mip=coord[...,3] if r['layers']>1 else coord[...,2];valid=(x>=0)&(x<r['width'])&(y>=0)&(y<r['height'])&(layer>=0)&(layer<r['layers'])&(mip==0);buf=data.reshape(r['layers'],r['height'],r['width'],4);value=np.where(valid[...,None],buf[np.clip(layer,0,r['layers']-1),np.clip(y,0,r['height']-1),np.clip(x,0,r['width']-1)],np.uint32(0))
            else:raise Unsupported('interp_unsupported:'+op)
        elif op.startswith(('sample_indexable','sample_l_indexable','sample_b_indexable','sample_d_indexable')):
            dst,coords,resource,sampler,*lod=operands;match=re.fullmatch(r't(\d+)(?:\.([xyzw]+))?',resource);r,data=self.resources[int(match[1])]
            if r['kind'] not in ['texture2d','texture2darray']:raise Unsupported('interp_unsupported:'+op)
            coord=floating(self.read_text(coords,True))
            if not np.all(np.isfinite(coord[active,:2])):raise Unsupported('interp_unsupported:nonfinite_sample_coordinate')
            u=ftz((coord[...,0]-np.floor(coord[...,0]))*np.float32(r['width']));v=ftz((coord[...,1]-np.floor(coord[...,1]))*np.float32(r['height']))
            if self.fixture['mode']=='B':self.excluded|=active&(texel_excluded(u)|texel_excluded(v))
            x=texel_index(u,r['width']);y=texel_index(v,r['height']);layer=np.clip(np.rint(coord[...,2]),0,r['layers']-1).astype(np.int64) if r['layers']>1 else np.zeros(self.shape[:2],np.int64)
            value=data.reshape(r['layers'],r['height'],r['width'],4)[layer,y,x]
        elif op.startswith('resinfo_indexable'):
            dst,mip,resource=operands;match=re.fullmatch(r't(\d+)(?:\.([xyzw]+))?',resource);r,data=self.resources[int(match[1])];dims=[r['width'],r['height'],r['layers'] if r['kind']=='texture2darray' else 0,1];value=np.broadcast_to(np.array(dims,np.uint32) if suffix=='uint' else bits(np.array(dims,np.float32)),self.shape)
        else:raise Unsupported('interp_unsupported:'+op)
        swizzle=match[2] or 'xyzw'
        if len(swizzle)==1:swizzle*=4
        if len(swizzle)!=4:raise Unsupported('interp_unsupported:resource_swizzle')
        return dst,value[...,["xyzw".index(c) for c in swizzle]]

    def execute(self):
        instructions=self.shader['instructions'];ifstack=[];loops=[];mask=self.active.copy();pc=0
        matches={};stack=[]
        for n,ins in enumerate(instructions):
            op=ins['opcode']
            if op in ['if_nz','if_z','loop']:stack.append((op,n))
            elif op=='else':
                if not stack or not stack[-1][0].startswith('if'):raise Unsupported('interp_unsupported:control_flow')
                matches[stack[-1][1]]=n
            elif op in ['endif','endloop']:
                if not stack:raise Unsupported('interp_unsupported:control_flow')
                startop,start=stack.pop();matches[n]=start
                if start in matches:matches[matches[start]]=n
                else:matches[start]=n
        float_ops={'add','mul','div','mad','dp2','dp3','dp4','min','max','sqrt','rsq','rcp','exp','log','frc','round_ne','round_ni','round_pi','round_z','sincos','lt','ge','eq','ne','ftoi','ftou','f32tof16'}
        while pc<len(instructions):
            ins=instructions[pc];rawop=ins['opcode'];op=rawop[:-4] if rawop.endswith('_sat') else rawop;sat=rawop.endswith('_sat');operands=[o['text'] for o in ins['operands']];active=mask&~self.returned
            for loop in loops:active&=~(loop['broken']|loop['continued'])
            if op in ['if_nz','if_z']:
                condition=self.read_text(operands[0])[...,0]!=0
                if op=='if_z':condition=~condition
                ifstack.append((mask.copy(),condition));mask=mask&condition
            elif op=='else':
                parent,condition=ifstack[-1];mask=parent&~condition
            elif op=='endif':mask=ifstack.pop()[0]
            elif op=='loop':loops.append(dict(start=pc,entry=mask.copy(),broken=np.zeros_like(mask),continued=np.zeros_like(mask),iterations=0))
            elif op=='endloop':
                loop=loops[-1];loop['iterations']+=1;remaining=loop['entry']&~loop['broken']&~self.returned
                if np.any(remaining):
                    if loop['iterations']>=1024:raise IterationLimit('1024 loop iterations at instruction '+str(ins['index']))
                    loop['continued'][:]=False;mask=loop['entry'].copy();pc=loop['start']+1;continue
                mask=loop['entry'];loops.pop()
            elif op in ['break','breakc_nz','breakc_z','continue','continuec_nz','continuec_z']:
                if not loops:raise Unsupported('interp_unsupported:'+op)
                chosen=active.copy()
                if op not in ['break','continue']:
                    condition=self.read_text(operands[0])[...,0]!=0;chosen&=(~condition if op.endswith('_z') else condition)
                loops[-1]['broken' if op.startswith('break') else 'continued']|=chosen
            elif op in ['discard_nz','discard_z']:
                condition=self.read_text(operands[0])[...,0]!=0;self.discarded|=active&(~condition if op=='discard_z' else condition)
            elif op=='ret':self.returned|=active
            elif op.startswith(('ld_','sample','resinfo')):
                dst,value=self.resource(op,operands,active,ins.get('suffix'));self.write(dst,value,active,sat)
            elif op.startswith('deriv_rt'):
                value=floating(self.read_text(operands[1],True));delta=np.zeros_like(value)
                if op.startswith('deriv_rtx'):
                    diff=ftz(value[:,1::2]-value[:,::2]);delta=np.repeat(diff,2,axis=1)
                    if not op.endswith('_fine'):delta=np.repeat(delta[::2],2,axis=0)
                elif op.startswith('deriv_rty'):
                    diff=ftz(value[1::2]-value[::2]);delta=np.repeat(diff,2,axis=0)
                    if not op.endswith('_fine'):delta=np.repeat(delta[:,::2],2,axis=1)
                else:raise Unsupported('interp_unsupported:'+op)
                self.write(operands[0],bits(delta),active,sat)
            else:
                count=2 if op in ['sincos','udiv','swapc'] or (op=='imul' and len(operands)==4) else 1;destinations=operands[:count];sources=operands[count:]
                if op=='imul' and count==2 and destinations[0]!='null':raise Unsupported('interp_unsupported:imul_high_destination')
                floatmod=op in float_ops or op in ['mov','movc','swapc'];values=[]
                for n,source in enumerate(sources):values.append(self.read_text(source,floatmod and not (op in ['movc','swapc'] and n==0)))
                results=per_op(op,values)
                if op=='imul' and count==2:results=[np.zeros(self.shape,np.uint32),results[0]]
                for dst,value in zip(destinations,results):self.write(dst,value,active,sat)
            pc+=1
        return self.reg,self.discarded,self.excluded


def compare(shader,fixture):
    entry=dict(sha=fixture['sha'],shader_set=fixture['set'],mode=fixture['mode'],winding=fixture['winding'],seed_offset=fixture.get('seed_offset',0),status='pass',compared_components=0,max_rel_err=0.0,first_mismatch=None,unobserved_outputs=[row['Name'] for row in shader['signatures']['output'] if row['Name'].lower() in ['sv_depth','sv_depthlessequal','sv_depthgreaterequal']])
    oracle_discarded=oracle_discard_mask(fixture);entry['non_discarded_pixels']=int(np.count_nonzero(~oracle_discarded));entry['discard_agreement']=False
    try:
        interp=Interpreter(shader,fixture)
        with np.errstate(all='ignore'):registers,discarded,excluded=interp.execute()
    except Unsupported as error:entry.update(status='unsupported',reason=str(error));return entry
    entry['discard_agreement']=bool(np.array_equal(discarded,oracle_discarded))
    valid=~oracle_discarded&~excluded;entry['compared_pixels']=int(np.count_nonzero(valid));entry['excluded_pixels']=int(np.count_nonzero(excluded));entry['discarded_pixels']=int(np.count_nonzero(discarded))
    if not entry['discard_agreement']:
        py,px=np.argwhere(discarded!=oracle_discarded)[0];entry.update(status='fail',reason='discard_mask_mismatch',first_discard_mismatch=dict(px=int(px),py=int(py),oracle=bool(oracle_discarded[py,px]),interp=bool(discarded[py,px])))
    if fixture['mode']=='B' and np.count_nonzero(valid)<.9*entry['non_discarded_pixels']:entry.update(status='fail',reason='mode_B_compared_pixels_below_90_percent_of_non_discarded')
    for output in fixture['outputs']:
        row=next(row for row in shader['signatures']['output'] if row['Name'].lower()=='sv_target' and int(row['Index'])==output['slot']);actual=np.fromfile(output['file'],dtype='<f4').reshape(fixture['height'],fixture['width'],4);predicted=registers['o'+row['Register']].view(np.float32)
        for letter in row['Mask']:
            c='xyzw'.index(letter);a=actual[...,c];b=predicted[...,c];scale=np.maximum(np.float32(1),np.maximum(np.abs(a),np.abs(b)));finite=np.isfinite(a)&np.isfinite(b);rel=np.zeros_like(a);np.divide(np.abs(a-b),scale,out=rel,where=finite);equal=(np.isnan(a)&np.isnan(b))|((a==b)&~np.isnan(a))|(finite&(rel<=np.float32(1e-5)));mismatch=(valid&~equal)|(discarded&(a!=np.float32(-12345)))
            entry['compared_components']+=int(np.count_nonzero(valid));entry['max_rel_err']=max(entry['max_rel_err'],float(np.max(rel[valid])) if np.any(valid) else 0.0)
            if np.any(mismatch) and entry['first_mismatch'] is None:
                py,px=np.argwhere(mismatch)[0];entry.update(status='fail',first_mismatch=dict(px=int(px),py=int(py),rt=output['slot'],comp=letter,oracle=float(a[py,px]),interp=float(b[py,px])))
    return entry


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True);args=parser.parse_args();run=pathlib.Path(args.run);output=run/'out/X3';output.mkdir(parents=True,exist_ok=True);summary=json.loads((run/'out/X2b/summary.json').read_text());results={'shaders':[],'totals':{}}
    def save():
        tables={}
        for group in ['SLICE','GLOBAL200']:
            for mode in ['A','B']:
                rows=[r for r in results['shaders'] if r['shader_set']==group and r['mode']==mode];counts=collections.Counter(r['status'] for r in rows);tables[group+'_'+mode]=dict(counts= dict(sorted(counts.items())),compared_components=sum(r['compared_components'] for r in rows),max_rel_err=max((r['max_rel_err'] for r in rows),default=None))
        results['totals']=tables;(output/'results.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8',newline='\n')
    fixture_index=json.loads((run/'out/X2c/fixtures.json').read_text());seeds=json.loads((run/'out/X2c/seeds.json').read_text())
    for item in fixture_index:
        fixture=json.loads(pathlib.Path(item['fixture']).read_text());parsed=json.loads(pathlib.Path(fixture['shader_json']).read_text());entry=compare(parsed,fixture);results['shaders'].append(entry);save()
        if item['shader_set']=='SLICE' and entry['status']=='fail':raise RuntimeError('X3 SLICE fail '+json.dumps(entry))
    results['exercise']=[]
    for sha,modes in seeds.items():
        for mode,seed in modes.items():
            rows=[e for e in results['shaders'] if e['sha']==sha and e['mode']==mode and e['seed_offset']==seed['exercised_by']];threshold=64 if mode=='A' else 58;compared=max((e.get('compared_pixels',0) for e in rows),default=0);check=dict(sha=sha,mode=mode,seed_offset=seed['exercised_by'],compared_pixels=compared,required_pixels=threshold,passed=compared>=threshold);results['exercise'].append(check);save()
            if not check['passed']:raise RuntimeError('X3 SLICE exercise fail '+json.dumps(check))
    save();print(json.dumps(results['totals'],indent=2))


if __name__=='__main__':main()
