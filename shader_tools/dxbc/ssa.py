import argparse
import collections
import json
import pathlib
import re
import numpy as np
from .interp import Interpreter,Unsupported,per_op,bits,floating,ftz,texel_index


_FLOAT_OPS={'add','mul','div','mad','dp2','dp3','dp4','min','max','sqrt','rsq','rcp','exp','log','frc','round_ne','round_ni','round_pi','round_z','sincos','lt','ge','eq','ne','ftoi','ftou','f32tof16'}
_MASK_OPS={'lt','ge','eq','ne','ieq','ine','ilt','ige','ult','uge'}
_FLOAT_RESULTS={'add','mul','div','mad','dp2','dp3','dp4','min','max','sqrt','rsq','rcp','exp','log','frc','round_ne','round_ni','round_pi','round_z','sincos','itof','utof','f16tof32','sat'}


class Builder:
    def __init__(self,shader):
        self.shader=shader;self.nodes=[];self.state={};self.writes=[]
        for declaration in shader['declarations']:
            if declaration['op']=='dcl_temps':
                for n in range(int(declaration['text'])):
                    for c in 'xyzw':self.state['r'+str(n)+'.'+c]=self.constant(0)
            elif declaration['op']=='dcl_indexableTemp':
                match=re.fullmatch(r'x(\d+)\[(\d+)\],\s*(\d+)',declaration['text'])
                if not match:raise Unsupported('ssa_unsupported:indexable_declaration')
                for n in range(int(match[2])):
                    for c in 'xyzw':self.state['x'+match[1]+'['+str(n)+'].'+c]=self.constant(0)
        inputs={row['Register'] for row in shader['signatures']['input']}
        for reg in sorted(inputs,key=int):
            for c in 'xyzw':
                row=next((row for row in shader['signatures']['input'] if row['Register']==reg and c in row['Mask']),None);kind='f32' if row and row['Format']=='float' else 'u32';self.state['v'+reg+'.'+c]=self.node('input',['v'+reg,c],kind,-1)
        for row in shader['signatures']['output']:
            name='o'+row['Register'] if row['Register'].isdigit() else row['Register']
            for c in 'xyzw':self.state[name+'.'+c]=self.constant(0)
        self.kill=self.constant(0);self.path=self.constant(0xFFFFFFFF)

    def node(self,op,args,kind,src):
        ident=len(self.nodes);self.nodes.append(dict(id=ident,op=op,args=args,type=kind,src=src));return ident

    def constant(self,value):return self.node('const',[int(value)&0xFFFFFFFF],'u32',-1)

    def index(self,expression,state,src):
        if expression.isdigit():return self.constant(int(expression))
        match=re.fullmatch(r'(r\d+\.[xyzw])\s*(?:\+\s*(\d+))?',expression)
        if not match:raise Unsupported('ssa_unsupported:operand:'+expression)
        ident=state[match[1]]
        if match[2]:ident=self.node('iadd',[ident,self.constant(int(match[2]))],'u32',src)
        return ident

    def read(self,text,state,src,float_mod=False):
        neg=text.startswith('-');text=text[1:] if neg else text;absolute=text.startswith('|') and text.endswith('|');text=text[1:-1] if absolute else text
        match=re.fullmatch(r'(r\d+|v\d+|o\d+)(?:\.([xyzw]+))?',text)
        if match:
            name,swizzle=match.groups();swizzle=swizzle or 'xyzw';values=[state[name+'.'+c] for c in 'xyzw']
        elif text.startswith('l(') and text.endswith(')'):
            tokens=[t.strip() for t in text[2:-1].split(',')]
            if not all(re.fullmatch(r'0x[0-9a-fA-F]+',t) for t in tokens):raise Unsupported('ssa_unsupported:literal:'+text)
            if len(tokens)==1:tokens*=4
            if len(tokens)!=4:raise Unsupported('ssa_unsupported:literal:'+text)
            values=[self.constant(int(t,16)) for t in tokens];swizzle='xyzw'
        else:
            match=re.fullmatch(r'(cb|x)(\d+)\[(.+)\](?:\.([xyzw]+))?',text)
            if not match:raise Unsupported('ssa_unsupported:operand:'+text)
            kind,slot,expression,swizzle=match.groups();swizzle=swizzle or 'xyzw'
            if kind=='cb':
                imatch=re.fullmatch(r'(r\d+\.[xyzw])\s*(?:\+\s*(\d+))?',expression)
                if not expression.isdigit() and imatch is None:raise Unsupported('ssa_unsupported:operand:'+text)
                values=[self.node('cbload',([state[imatch[1]]] if imatch else [])+[dict(slot=int(slot),component=c,offset=int(expression) if expression.isdigit() else int(imatch[2] or 0),dynamic=imatch is not None)],'u32',src) for c in range(4)]
            else:
                if expression.isdigit():values=[state['x'+slot+'['+expression+'].'+c] for c in 'xyzw']
                else:
                    index=self.index(expression,state,src);members=sorted({int(re.search(r'\[(\d+)\]',k)[1]) for k in state if k.startswith('x'+slot+'[')});values=[]
                    for c in 'xyzw':
                        value=self.constant(0)
                        for n in members:
                            condition=self.node('ieq',[index,self.constant(n)],'bool-mask',src);value=self.node('select',[condition,state['x'+slot+'['+str(n)+'].'+c],value],'u32',src)
                        values.append(value)
        if len(swizzle)==1:swizzle*=4
        if len(swizzle)!=4:raise Unsupported('ssa_unsupported:swizzle:'+text)
        values=[values['xyzw'.index(c)] for c in swizzle]
        if neg or absolute or float_mod:values=[self.node('mov',[value,dict(read=True,neg=neg,abs=absolute,float_mod=float_mod)],'f32' if float_mod else 'u32',src) for value in values]
        return values

    def write(self,text,values,state,src,destination,saturate=False):
        if text=='null':return
        match=re.fullmatch(r'(r\d+|o\d+|oDepth|oDepthLE|oDepthGE)(?:\.([xyzw]+))?',text)
        if match:
            name,mask=match.groups();mask=mask or ('x' if name.startswith('oDepth') else 'xyzw');index=None
        else:
            match=re.fullmatch(r'x(\d+)\[(.+)\](?:\.([xyzw]+))?',text)
            if not match:raise Unsupported('ssa_unsupported:destination:'+text)
            slot,expression,mask=match.groups();mask=mask or 'xyzw';index=self.index(expression,state,src);members=sorted({int(re.search(r'\[(\d+)\]',k)[1]) for k in state if k.startswith('x'+slot+'[')})
        written={}
        for c in mask:
            value=values['xyzw'.index(c)]
            if saturate:value=self.node('sat',[value],'f32',src)
            written[c]=value
            if index is None:state[name+'.'+c]=value
            else:
                for n in members:
                    key='x'+slot+'['+str(n)+'].'+c;condition=self.node('ieq',[index,self.constant(n)],'bool-mask',src);state[key]=self.node('select',[condition,value,state[key]],self.nodes[value]['type'],src)
        self.writes.append(dict(src=src,destination=destination,text=text,components=written))

    def resource(self,op,operands,state,src,suffix):
        if op.startswith('ld_structured_indexable'):
            dst,element,offset,resource=operands;inputs=[self.read(element,state,src)[0],self.read(offset,state,src)[0]];kind='sbload';meta=dict(stride=int(re.search(r'stride=(\d+)',op)[1]))
        elif op.startswith('ld_indexable'):
            dst,coords,resource=operands;inputs=self.read(coords,state,src);kind='bufload' if '(buffer)' in op else 'texfetch';meta={}
        elif op.startswith(('sample_indexable','sample_l_indexable','sample_b_indexable','sample_d_indexable')):
            dst,coords,resource,sampler,*lod=operands;inputs=self.read(coords,state,src,True);kind='sample';meta={}
        elif op.startswith('resinfo_indexable'):
            dst,mip,resource=operands;inputs=[];kind='resinfo';meta=dict(suffix=suffix)
        else:raise Unsupported('ssa_unsupported:'+op)
        match=re.fullmatch(r't(\d+)(?:\.([xyzw]+))?',resource)
        if not match:raise Unsupported('ssa_unsupported:resource:'+resource)
        slot=int(match[1]);swizzle=match[2] or 'xyzw'
        if len(swizzle)==1:swizzle*=4
        if len(swizzle)!=4:raise Unsupported('ssa_unsupported:resource_swizzle')
        return dst,[self.node(kind,inputs+[dict(meta,slot=slot,component='xyzw'.index(c))],'u32' if kind!='sample' else 'f32',src) for c in swizzle]

    def walk(self,start,end,state,kill,path):
        instructions=self.shader['instructions'];pc=start
        while pc<end:
            ins=instructions[pc];raw=ins['opcode'];op=raw[:-4] if raw.endswith('_sat') else raw;saturate=raw.endswith('_sat');operands=[o['text'] for o in ins['operands']];src=ins['index']
            if op in ['if_nz','if_z']:
                condition=self.read(operands[0],state,src)[0];condition=self.node('ine' if op=='if_nz' else 'ieq',[condition,self.constant(0)],'bool-mask',src);depth=1;middle=None;stop=None
                for n in range(pc+1,end):
                    name=instructions[n]['opcode']
                    if name in ['if_nz','if_z']:depth+=1
                    elif name=='endif':
                        depth-=1
                        if depth==0:stop=n;break
                    elif name=='else' and depth==1:middle=n
                if stop is None:raise Unsupported('ssa_unsupported:unstructured_if')
                then_path=self.node('and',[path,condition],'bool-mask',src);inverse=self.node('not',[condition],'bool-mask',src);else_path=self.node('and',[path,inverse],'bool-mask',src)
                then_state,then_kill=self.walk(pc+1,middle if middle is not None else stop,state.copy(),kill,then_path)
                else_state,else_kill=self.walk(middle+1,stop,state.copy(),kill,else_path) if middle is not None else (state.copy(),kill)
                for key in sorted(set(then_state)|set(else_state)):
                    a,b=then_state[key],else_state[key];state[key]=a if a==b else self.node('select',[condition,a,b],self.nodes[a]['type'],src)
                kill=self.node('or',[then_kill,else_kill],'bool-mask',src);pc=stop+1;continue
            if op in ['else','endif']:raise Unsupported('ssa_unsupported:unstructured_if')
            if op in ['loop','endloop']:raise Unsupported('ssa_unsupported:loop')
            if op in ['discard_nz','discard_z']:
                condition=self.read(operands[0],state,src)[0];condition=self.node('ine' if op=='discard_nz' else 'ieq',[condition,self.constant(0)],'bool-mask',src);condition=self.node('and',[path,condition],'bool-mask',src);kill=self.node('or',[kill,condition],'bool-mask',src)
            elif op=='ret':
                if pc!=len(instructions)-1:raise Unsupported('ssa_unsupported:conditional_ret')
                break
            elif op.startswith(('ld_','sample','resinfo')):
                dst,values=self.resource(op,operands,state,src,ins.get('suffix'));self.write(dst,values,state,src,0,saturate)
            elif op.startswith('deriv_rt'):
                values=self.read(operands[1],state,src,True);values=[self.node('deriv',[value,dict(opcode=op)],'f32',src) for value in values];self.write(operands[0],values,state,src,0,saturate)
            else:
                count=2 if op in ['sincos','udiv','swapc'] or (op=='imul' and len(operands)==4) else 1;destinations=operands[:count];sources=operands[count:];float_mod=op in _FLOAT_OPS or op in ['mov','movc','swapc'];values=[self.read(source,state,src,float_mod and not (op in ['movc','swapc'] and n==0)) for n,source in enumerate(sources)]
                if op=='imul' and count==2 and destinations[0]!='null':raise Unsupported('ssa_unsupported:imul_high_destination')
                for n,destination in enumerate(destinations):
                    if destination=='null':continue
                    result=[]
                    for c in range(4):
                        if op=='mov':value=values[0][c]
                        else:
                            args=values[0][:int(op[-1])]+values[1][:int(op[-1])] if op in ['dp2','dp3','dp4'] else [v[c] for v in values]
                            if op in ['sincos','udiv','swapc']:args=args+[dict(result=n)]
                            kind='bool-mask' if op in _MASK_OPS else ('f32' if op in _FLOAT_RESULTS else 'u32');value=self.node(op,args,kind,src)
                        result.append(value)
                    self.write(destination,result,state,src,n,saturate)
            pc+=1
        return state,kill

    def build(self):
        if any(i['opcode']=='loop' for i in self.shader['instructions']):raise Unsupported('ssa_unsupported:loop')
        state,kill=self.walk(0,len(self.shader['instructions']),self.state,self.kill,self.path);outputs={key:value for key,value in state.items() if re.fullmatch(r'o\d+\.[xyzw]',key)};outputs['kill']=kill
        return dict(nodes=self.nodes,outputs=outputs,writes=self.writes)


class Evaluator:
    def __init__(self,shader,fixture):
        self.reader=Interpreter(shader,fixture);self.fixture=fixture;self.shape=(fixture['height'],fixture['width']);self.values=[]
        self.inputs={name:value.copy() for name,value in self.reader.reg.items()}

    def vector(self,value):return np.repeat(np.asarray(value,dtype=np.uint32)[...,None],4,axis=-1)

    def resource(self,op,args):
        meta=args[-1];values=[self.values[n] for n in args[:-1]];r,data=self.reader.resources[meta['slot']];c=meta['component'];h,w=self.shape
        if op=='sbload':
            index=values[0].astype(np.uint64)*(meta['stride']//4)+values[1].astype(np.uint64)//4+c;valid=index<len(data);return np.where(valid,data[np.minimum(index,len(data)-1).astype(np.int64)],np.uint32(0))
        if op=='bufload':
            index=values[0].view(np.int32);buffer=data.reshape(-1,4);valid=(index>=0)&(index<len(buffer));return np.where(valid,buffer[np.clip(index,0,len(buffer)-1),c],np.uint32(0))
        if op=='texfetch':
            coords=[value.view(np.int32) for value in values];x,y=coords[:2];layer=coords[2] if r['layers']>1 else np.zeros(self.shape,np.int32);mip=coords[3] if r['layers']>1 else coords[2];valid=(x>=0)&(x<r['width'])&(y>=0)&(y<r['height'])&(layer>=0)&(layer<r['layers'])&(mip==0);ix=texel_index(x,r['width']);iy=texel_index(y,r['height']);return np.where(valid,data.reshape(r['layers'],r['height'],r['width'],4)[np.clip(layer,0,r['layers']-1),iy,ix,c],np.uint32(0))
        if op=='sample':
            coords=[floating(value) for value in values];u=ftz((coords[0]-np.floor(coords[0]))*np.float32(r['width']));v=ftz((coords[1]-np.floor(coords[1]))*np.float32(r['height']));x=texel_index(u,r['width']);y=texel_index(v,r['height']);layer=np.clip(np.rint(coords[2]),0,r['layers']-1).astype(np.int64) if r['layers']>1 else np.zeros(self.shape,np.int64);return data.reshape(r['layers'],r['height'],r['width'],4)[layer,y,x,c]
        if op=='resinfo':
            dims=[r['width'],r['height'],r['layers'] if r['kind']=='texture2darray' else 0,1];value=np.uint32(dims[c]) if meta['suffix']=='uint' else bits(np.float32(dims[c]));return np.full(self.shape,value,np.uint32)
        raise Unsupported('ssa_unsupported:'+op)

    def evaluate(self,dag):
        with np.errstate(all='ignore'):
            for node in dag['nodes']:
                op,args=node['op'],node['args']
                if op=='const':value=np.full(self.shape,args[0],np.uint32)
                elif op=='input':value=self.inputs[args[0]][...,'xyzw'.index(args[1])].copy()
                elif op=='select':value=np.where(self.values[args[0]]!=0,self.values[args[1]],self.values[args[2]])
                elif op=='mov' and isinstance(args[-1],dict) and args[-1].get('read'):
                    meta=args[-1];self.reader.reg['r999999']=self.vector(self.values[args[0]]);text='r999999.xxxx';text='|'+text+'|' if meta['abs'] else text;text='-'+text if meta['neg'] else text;value=self.reader.read_text(text,meta['float_mod'])[...,0]
                elif op=='cbload':
                    meta=args[-1];index=(self.values[args[0]].view(np.int32).astype(np.int64) if meta['dynamic'] else np.zeros(self.shape,np.int64))+meta['offset'];buffer=self.reader.cb[meta['slot']];valid=(index>=0)&(index<len(buffer));value=np.where(valid,buffer[np.clip(index,0,len(buffer)-1),meta['component']],np.uint32(0))
                elif op in ['sbload','bufload','texfetch','sample','resinfo']:value=self.resource(op,args)
                elif op=='deriv':
                    self.reader.reg['r999999']=self.vector(self.values[args[0]]);self.reader.shader=dict(instructions=[dict(index=node['src'],opcode=args[-1]['opcode'],operands=[dict(text='r999998.xyzw'),dict(text='r999999.xyzw')])]);registers,discarded,excluded=self.reader.execute();value=registers['r999998'][...,0].copy()
                else:
                    selector=args[-1]['result'] if args and isinstance(args[-1],dict) else 0;ids=args[:-1] if args and isinstance(args[-1],dict) else args
                    if op in ['dp2','dp3','dp4']:
                        count=int(op[-1]);a=[self.values[n] for n in ids[:count]];b=[self.values[n] for n in ids[count:]];zero=np.zeros(self.shape,np.uint32);a+= [zero]*(4-count);b+=[zero]*(4-count);operands=[np.stack(a,axis=-1),np.stack(b,axis=-1)]
                    else:operands=[self.vector(self.values[n]) for n in ids]
                    value=per_op(op,operands)[selector][...,0]
                self.values.append(np.asarray(value,dtype=np.uint32))
        return {name:self.values[ident] for name,ident in dag['outputs'].items()}


class TraceInterpreter(Interpreter):
    def __init__(self,shader,fixture):
        super().__init__(shader,fixture);self.trace=[];self.write_order=[];self.write_cursor=0
        for ins in shader['instructions']:
            op=ins['opcode'];base=op[:-4] if op.endswith('_sat') else op
            if base in ['if_nz','if_z','else','endif','loop','endloop','break','breakc_nz','breakc_z','continue','continuec_nz','continuec_z','discard_nz','discard_z','ret']:continue
            count=2 if base in ['sincos','udiv','swapc'] or (base=='imul' and len(ins['operands'])==4) else 1
            self.write_order.extend((ins['index'],n) for n in range(count))

    def write(self,text,value,active,saturate=False):
        src,destination=self.write_order[self.write_cursor];self.write_cursor+=1
        if text!='null':self.trace.append(dict(src=src,destination=destination,value=(per_op('sat',[value])[0] if saturate else np.asarray(value,np.uint32)).copy(),active=active.copy()))
        super().write(text,value,active,saturate)


def compare(shader,fixture,dag):
    evaluator=Evaluator(shader,fixture);outputs=evaluator.evaluate(dag);reference=TraceInterpreter(shader,fixture)
    with np.errstate(all='ignore'):registers,discarded,excluded=reference.execute()
    valid=~discarded;compared=0;write_nodes={(entry['src'],entry['destination']):entry for entry in dag['writes']}
    for trace in reference.trace:
        entry=write_nodes[(trace['src'],trace['destination'])];active=trace['active']&valid
        for c,node in entry['components'].items():
            expected=trace['value'][...,'xyzw'.index(c)];actual=evaluator.values[node];different=active&(expected!=actual)
            if np.any(different):
                py,px=np.argwhere(different)[0];return dict(pass_=False,first_differing_node=node,src=trace['src'],px=int(px),py=int(py),expected_bits=int(expected[py,px]),actual_bits=int(actual[py,px]),compared_components=compared)
            compared+=int(np.count_nonzero(active))
    for name,actual in outputs.items():
        expected=np.where(discarded,np.uint32(0xFFFFFFFF),np.uint32(0)) if name=='kill' else registers[name.split('.')[0]][...,'xyzw'.index(name[-1])];mask=np.ones_like(valid) if name=='kill' else valid;different=mask&(expected!=actual)
        if np.any(different):
            py,px=np.argwhere(different)[0];node=dag['outputs'][name];return dict(pass_=False,first_differing_node=node,src=dag['nodes'][node]['src'],output=name,px=int(px),py=int(py),expected_bits=int(expected[py,px]),actual_bits=int(actual[py,px]),compared_components=compared)
        compared+=int(np.count_nonzero(mask))
    return dict(pass_=True,first_differing_node=None,compared_components=compared)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True);args=parser.parse_args();run=pathlib.Path(args.run);output=run/'out/X4';output.mkdir(parents=True,exist_ok=True);result=json.loads((run/'out/X3/results.json').read_text());passed={(e['sha'],e['mode'],e['winding'],e['seed_offset']) for e in result['shaders'] if e['shader_set']=='SLICE' and e['status']=='pass'};items=[item for item in json.loads((run/'out/X2c/fixtures.json').read_text()) if (item['sha'],item['mode'],item['winding'],item['seed_offset']) in passed];dags={};summary=dict(shaders={},fixtures=[],totals={})
    def save():
        histogram=collections.Counter(node['op'] for dag in dags.values() for node in dag['nodes']);summary['totals']=dict(shaders=len(dags),nodes=sum(len(dag['nodes']) for dag in dags.values()),fixtures=len(summary['fixtures']),passed=sum(e['pass_'] for e in summary['fixtures']),op_histogram=dict(sorted(histogram.items())));(output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8',newline='\n')
    for item in items:
        sha=item['sha'];fixture=json.loads(pathlib.Path(item['fixture']).read_text());shader=json.loads(pathlib.Path(fixture['shader_json']).read_text())
        if sha not in dags:
            try:dag=Builder(shader).build()
            except Unsupported as error:
                summary['shaders'][sha]=dict(status=str(error));save();raise RuntimeError('X4 SLICE unsupported '+sha+' '+str(error))
            dags[sha]=dag;summary['shaders'][sha]=dict(status='built',nodes=len(dag['nodes']));(output/(sha+'.dag.json')).write_text(json.dumps(dag,indent=2)+'\n',encoding='utf-8',newline='\n')
        check=compare(shader,fixture,dags[sha]);entry=dict(sha=sha,mode=item['mode'],winding=item['winding'],seed_offset=item['seed_offset'],**check);summary['fixtures'].append(entry);save()
        if not check['pass_']:raise RuntimeError('X4 bit mismatch '+json.dumps(entry))
    save();print(json.dumps(summary['totals'],indent=2))


if __name__=='__main__':main()
