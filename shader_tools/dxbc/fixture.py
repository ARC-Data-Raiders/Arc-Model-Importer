import argparse
import collections
import json
import pathlib
import random
import re
import struct
import subprocess
from .vsgen import generate


def _dds(path,width,height,layers,values):
    header=[124,1|2|4|0x1000|0x20000|8,height,width,width*16,0,1]+[0]*11+[32,4,0x30315844,0,0,0,0,0]+[0x1000|(8 if layers>1 else 0),0,0,0,0]+[2,3,0,layers,0]
    path.write_bytes(b'DDS '+struct.pack('<36I',*header)+struct.pack('<'+'f'*len(values),*values))


def _sets(wave1, parsed_root):
    result={}
    for name in ['slice','global']:
        root=wave1/'out/W1'/name
        manifest=json.loads((root/'shader-records.manifest.json').read_text())
        unique={}
        for record in manifest['records']:
            sha=record['container_sha256'];parsed=parsed_root/name/(sha+'.json')
            if sha not in unique and parsed.exists():
                shader=json.loads(parsed.read_text())
                if shader['model'].startswith('ps_'):
                    unique[sha]=(record,shader,root)
        items=sorted(unique.items())
        if name=='global':
            step=len(items)//200
            if not step:
                raise ValueError('Fewer than 200 global SM5 pixel shaders')
            items=items[::step][:200]
            if len(items)!=200:
                raise ValueError('GLOBAL200 selection count')
        result['SLICE' if name=='slice' else 'GLOBAL200']=items
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--wave1',required=True);parser.add_argument('--run',required=True);args=parser.parse_args();run=pathlib.Path(args.run);wave1=pathlib.Path(args.wave1);fxc=pathlib.Path(r'C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\fxc.exe');oracle=run/'build/dr_oracle/dr_oracle.exe';output=run/'out/X2b';output.mkdir(parents=True,exist_ok=True);summary={'shaders':[],'totals':{}}
    parsed_root=run/'out/W7v2'
    for group,items in _sets(wave1, parsed_root).items():
        reasons=collections.Counter();supported=0
        for sha,(record,shader,record_root) in items:
            directory=output/sha;directory.mkdir(parents=True,exist_ok=True);rng=random.Random(int(sha[:16],16));entry={'set':group,'sha':sha,'status':'supported','modes':[]}
            if not any(row['Name'].lower()=='sv_target' for row in shader['signatures']['output']):
                entry['status']='fixture_unsupported:no_target';reasons[entry['status']]+=1;summary['shaders'].append(entry);continue
            for declaration in shader['declarations']:
                op=declaration['op']
                if any(token in op for token in ['texture3d','texturecube','resource_raw']) or op.startswith('dcl_uav_'):
                    entry['status']='fixture_unsupported:'+op;break
            for instruction in shader['instructions']:
                if instruction['opcode'].startswith(('sample_c','gather')):
                    entry['status']='fixture_unsupported:'+instruction['opcode'];break
            if entry['status']=='supported':
                vertex=generate(shader,sha,directory,rng,fxc);entry['vertex']=vertex;entry['status']=vertex['status']
            targets=[]
            if entry['status']=='supported':
                for row in shader['signatures']['output']:
                    name=row['Name'].lower()
                    if name=='sv_target':targets.append(int(row['Index']))
                    elif name in ('sv_depth','sv_depthlessequal','sv_depthgreaterequal'):entry.setdefault('unobserved_outputs',[]).append(row['Name'])
                    else:entry['status']='fixture_unsupported:output:'+row['Name'];break
            if entry['status']!='supported':
                reasons[entry['status']]+=1;summary['shaders'].append(entry)
                if group=='SLICE' and entry['status']!='fixture_unsupported:no_target':
                    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8',newline='\n')
                    raise RuntimeError('X2b unexpected SLICE exclusion '+sha+' '+entry['status']+' expected/actual: '+json.dumps(entry.get('vertex')))
                continue
            has_face=any(row['SysValue']=='FFACE' for row in shader['signatures']['input'])
            for mode,winding in [(mode,winding) for mode in ['A','B'] for winding in (['W0','W1'] if has_face else ['W0'])]:
                mode_dir=directory/mode/winding;mode_dir.mkdir(parents=True,exist_ok=True);rng=random.Random(int(sha[:16],16));vertex_mode=generate(shader,sha,mode_dir,rng,fxc,winding=winding);lines=[['DRJOB',1],['device','warp'],['vs','hlsl',str(mode_dir/'vs.hlsl'),'main','vs_5_0'],['ps','dxbc',str(record_root/record['container_file'])]];resources=[];status=None
                for declaration in shader['declarations']:
                    op,text=declaration['op'],declaration['text']
                    if op=='dcl_constantbuffer':
                        match=re.search(r'CB(\d+)\[(\d+)\]',text)
                        if match is None:raise ValueError('Unresolved constant-buffer declaration '+text)
                        slot,count=map(int,match.groups());path=mode_dir/('cb'+str(slot)+'.bin');values=[rng.uniform(-4,4) for _ in range(count*4)];path.write_bytes(struct.pack('<'+'f'*len(values),*values));lines.append(['cb','ps',slot,str(path)]);resources.append({'kind':'cb','slot':slot,'vectors':count,'file':str(path)})
                    elif op=='dcl_sampler':
                        slot=int(re.search(r'\bs(\d+)\b',text)[1]);lines.append(['samp','ps',slot,0,1,1,1,0,1,0,0]);resources.append({'kind':'sampler','slot':slot})
                    elif op=='dcl_resource_structured':
                        match=re.search(r'\bt(\d+)\s*,\s*(\d+)',text)
                        if match is None:raise ValueError('Unresolved structured declaration '+text)
                        slot,stride=map(int,match.groups())
                        if stride%4:status='fixture_unsupported:'+op+':stride';break
                        path=mode_dir/('sbuf'+str(slot)+'.bin');values=[rng.uniform(-4,4) for _ in range(64*stride//4)];path.write_bytes(struct.pack('<'+'f'*len(values),*values));lines.append(['sbuf','ps',slot,stride,str(path)]);resources.append({'kind':'structured','slot':slot,'stride':stride,'elements':64,'file':str(path)})
                    elif op=='dcl_resource_buffer':
                        slot=int(re.search(r'\bt(\d+)\b',text)[1]);uint='uint' in text;floating='float' in text
                        if not uint and not floating:status='fixture_unsupported:'+op+':return_type';break
                        fmt=3 if uint else 2;path=mode_dir/('tbuf'+str(slot)+'.bin');values=[rng.randrange(64) if uint else rng.uniform(-4,4) for _ in range(64*4)];path.write_bytes(struct.pack('<'+('I' if uint else 'f')*len(values),*values));lines.append(['tbuf','ps',slot,fmt,str(path)]);resources.append({'kind':'buffer','slot':slot,'format':fmt,'elements':64,'file':str(path)})
                    elif op in ['dcl_resource_texture2d','dcl_resource_texture2darray']:
                        slot=int(re.search(r'\bt(\d+)\b',text)[1]);width=height=1 if mode=='A' else 8;layers=4 if op.endswith('array') else 1
                        if 'float' not in text:status='fixture_unsupported:'+op+':return_type';break
                        path=mode_dir/('tex'+str(slot)+'.dds');values=[rng.uniform(0,1) for _ in range(width*height*layers*4)];_dds(path,width,height,layers,values);lines.append(['tex','ps',slot,str(path),2]);resources.append({'kind':'texture2darray' if layers>1 else 'texture2d','slot':slot,'width':width,'height':height,'layers':layers,'file':str(path),'format':2})
                    elif op.startswith('dcl_resource'):
                        status='fixture_unsupported:'+op;break
                if status:
                    entry['status']=status;break
                lines.extend([['draw',3],['clear',-12345,-12345,-12345,-12345]])
                rt_paths=[]
                for target in targets:
                    path=mode_dir/('rt'+str(target)+'.f32');lines.append(['rt',target,16,16,str(path)]);rt_paths.append({'slot':target,'file':str(path)})
                job=mode_dir/'job.txt';job.write_text('\n'.join('\t'.join(map(str,line)) for line in lines)+'\n',encoding='utf-8',newline='\n');fixture={'sha':sha,'set':group,'mode':mode,'width':16,'height':16,'seed':int(sha[:16],16),'vertex':vertex_mode,'winding':winding,'frontface':json.loads((output/'frontface.json').read_text())[winding] if has_face else None,'resources':resources,'outputs':rt_paths,'job':str(job),'shader_json':str(parsed_root/('slice' if group=='SLICE' else 'global')/(sha+'.json'))};(mode_dir/'fixture.json').write_text(json.dumps(fixture,indent=2)+'\n',encoding='utf-8',newline='\n')
                with (run/'logs'/('X2b-'+sha+'-'+mode+'-'+winding+'.log')).open('w',encoding='utf-8') as log:rc=subprocess.run([str(oracle),str(job)],stdout=log,stderr=subprocess.STDOUT,timeout=1800).returncode
                entry['modes'].append({'mode':mode,'winding':winding,'exit':rc,'fixture':str(mode_dir/'fixture.json')})
                if rc:
                    summary['shaders'].append(entry);(output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8',newline='\n');raise RuntimeError('ORACLE failed '+sha+' '+mode+' exit '+str(rc))
            if entry['status']=='supported':supported+=1
            else:reasons[entry['status']]+=1
            summary['shaders'].append(entry)
        summary['totals'][group]={'selected':len(items),'supported':supported,'unsupported_reasons':dict(sorted(reasons.items()))}
        if group=='SLICE' and supported!=21:
            (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8',newline='\n')
            raise RuntimeError('X2b SLICE supported count '+str(supported)+' expected 21')
        (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(summary['totals'],indent=2))


def extra_seed_fixture(run,sha,record,shader,record_root,mode,winding,seed_offset):
    output=run/'out/X2b';directory=output/sha;group='SLICE';parsed_root=run/'out/W7v2';fxc=pathlib.Path(r'C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\fxc.exe');oracle=run/'build/dr_oracle/dr_oracle.exe';has_face=any(row['SysValue']=='FFACE' for row in shader['signatures']['input']);targets=[int(row['Index']) for row in shader['signatures']['output'] if row['Name'].lower()=='sv_target']
    mode_dir=directory/mode/winding/('s'+str(seed_offset));mode_dir.mkdir(parents=True,exist_ok=True);rng=random.Random(int(sha[:16],16)+seed_offset);vertex_mode=generate(shader,sha,mode_dir,rng,fxc,winding=winding);lines=[['DRJOB',1],['device','warp'],['vs','hlsl',str(mode_dir/'vs.hlsl'),'main','vs_5_0'],['ps','dxbc',str(record_root/record['container_file'])]];resources=[];status=None
    if vertex_mode['status']!='supported':raise RuntimeError('X2c signature '+json.dumps(vertex_mode))
    for declaration in shader['declarations']:
        op,text=declaration['op'],declaration['text']
        if op=='dcl_constantbuffer':
            match=re.search(r'CB(\d+)\[(\d+)\]',text)
            if match is None:raise ValueError('Unresolved constant-buffer declaration '+text)
            slot,count=map(int,match.groups());path=mode_dir/('cb'+str(slot)+'.bin');values=[rng.uniform(-4,4) for _ in range(count*4)];path.write_bytes(struct.pack('<'+'f'*len(values),*values));lines.append(['cb','ps',slot,str(path)]);resources.append({'kind':'cb','slot':slot,'vectors':count,'file':str(path)})
        elif op=='dcl_sampler':
            slot=int(re.search(r'\bs(\d+)\b',text)[1]);lines.append(['samp','ps',slot,0,1,1,1,0,1,0,0]);resources.append({'kind':'sampler','slot':slot})
        elif op=='dcl_resource_structured':
            match=re.search(r'\bt(\d+)\s*,\s*(\d+)',text)
            if match is None:raise ValueError('Unresolved structured declaration '+text)
            slot,stride=map(int,match.groups())
            if stride%4:status='fixture_unsupported:'+op+':stride';break
            path=mode_dir/('sbuf'+str(slot)+'.bin');values=[rng.uniform(-4,4) for _ in range(64*stride//4)];path.write_bytes(struct.pack('<'+'f'*len(values),*values));lines.append(['sbuf','ps',slot,stride,str(path)]);resources.append({'kind':'structured','slot':slot,'stride':stride,'elements':64,'file':str(path)})
        elif op=='dcl_resource_buffer':
            slot=int(re.search(r'\bt(\d+)\b',text)[1]);uint='uint' in text;floating='float' in text
            if not uint and not floating:status='fixture_unsupported:'+op+':return_type';break
            fmt=3 if uint else 2;path=mode_dir/('tbuf'+str(slot)+'.bin');values=[rng.randrange(64) if uint else rng.uniform(-4,4) for _ in range(64*4)];path.write_bytes(struct.pack('<'+('I' if uint else 'f')*len(values),*values));lines.append(['tbuf','ps',slot,fmt,str(path)]);resources.append({'kind':'buffer','slot':slot,'format':fmt,'elements':64,'file':str(path)})
        elif op in ['dcl_resource_texture2d','dcl_resource_texture2darray']:
            slot=int(re.search(r'\bt(\d+)\b',text)[1]);width=height=1 if mode=='A' else 8;layers=4 if op.endswith('array') else 1
            if 'float' not in text:status='fixture_unsupported:'+op+':return_type';break
            path=mode_dir/('tex'+str(slot)+'.dds');values=[rng.uniform(0,1) for _ in range(width*height*layers*4)];_dds(path,width,height,layers,values);lines.append(['tex','ps',slot,str(path),2]);resources.append({'kind':'texture2darray' if layers>1 else 'texture2d','slot':slot,'width':width,'height':height,'layers':layers,'file':str(path),'format':2})
        elif op.startswith('dcl_resource'):
            status='fixture_unsupported:'+op;break
    if status:raise RuntimeError(status)
    lines.extend([['draw',3],['clear',-12345,-12345,-12345,-12345]])
    rt_paths=[]
    for target in targets:
        path=mode_dir/('rt'+str(target)+'.f32');lines.append(['rt',target,16,16,str(path)]);rt_paths.append({'slot':target,'file':str(path)})
    job=mode_dir/'job.txt';job.write_text('\n'.join('\t'.join(map(str,line)) for line in lines)+'\n',encoding='utf-8',newline='\n');fixture={'sha':sha,'set':group,'mode':mode,'width':16,'height':16,'seed':int(sha[:16],16)+seed_offset,'seed_offset':seed_offset,'vertex':vertex_mode,'winding':winding,'frontface':json.loads((output/'frontface.json').read_text())[winding] if has_face else None,'resources':resources,'outputs':rt_paths,'job':str(job),'shader_json':str(parsed_root/('slice' if group=='SLICE' else 'global')/(sha+'.json'))};(mode_dir/'fixture.json').write_text(json.dumps(fixture,indent=2)+'\n',encoding='utf-8',newline='\n')
    with (run/'logs'/('X2c-'+sha+'-'+mode+'-'+winding+'-s'+str(seed_offset)+'.log')).open('w',encoding='utf-8') as log:rc=subprocess.run([str(oracle),str(job)],stdout=log,stderr=subprocess.STDOUT,timeout=1800).returncode
    if rc:raise RuntimeError('X2c ORACLE failed '+sha+' '+mode+' '+winding+' seed '+str(seed_offset)+' exit '+str(rc))
    return mode_dir/'fixture.json'


def capture_inputs(run):
    run=pathlib.Path(run);oracle=run/'build/dr_oracle/dr_oracle.exe';items=json.loads((run/'out/X2c/fixtures.json').read_text());totals={'fixtures':0,'members':0}
    for item in items:
        directory=pathlib.Path(item['fixture']).parent;fixture=json.loads((directory/'fixture.json').read_text());struct_text=re.search(r'struct Output \{.*?\};',(directory/'vs.hlsl').read_text())[0];members=[]
        for text in struct_text[len('struct Output {'):-2].split(';'):
            match=re.fullmatch(r'\s*((?:\w+ )*?)(float|uint|int)(\d) (\w+) : (\w+)\s*',text)
            if match is None and text.strip():raise ValueError('Unparsed VS output member '+text)
            if match and match[2]=='float' and match[5]!='SV_Position':
                if not re.fullmatch(r'a\d+',match[4]):raise ValueError('Unexpected VS output member name '+match[4])
                members.append((match[4],int(match[3])))
        manifest={'members':{}}
        for start in range(0,len(members),8):
            group=members[start:start+8];name='cap'+str(start//8);body=''.join('o.t'+str(k)+'=float4(i.'+member+',0'*(4-width)+');' for k,(member,width) in enumerate(group))
            (directory/(name+'.hlsl')).write_text(struct_text+'\nstruct Cap {'+''.join('float4 t'+str(k)+' : SV_Target'+str(k)+';' for k in range(len(group)))+'};\nCap main(Output i) { Cap o; '+body+' return o; }\n',encoding='ascii',newline='\n')
            lines=[['DRJOB',1],['device','warp'],['vs','hlsl',str(directory/'vs.hlsl'),'main','vs_5_0'],['ps','hlsl',str(directory/(name+'.hlsl')),'main','ps_5_0'],['draw',3],['clear',0,0,0,0]]+[['rt',k,fixture['width'],fixture['height'],str(directory/('vin_'+member+'.f32'))] for k,(member,width) in enumerate(group)]
            (directory/(name+'.txt')).write_text(''.join('\t'.join(str(v) for v in line)+'\n' for line in lines),encoding='ascii',newline='\n')
            result=subprocess.run([str(oracle),str(directory/(name+'.txt'))],capture_output=True,text=True)
            if result.returncode!=0:raise RuntimeError('X2d capture failed '+str(directory/(name+'.txt'))+' exit '+str(result.returncode)+' '+result.stderr)
            for member,width in group:manifest['members'][member]={'file':'vin_'+member+'.f32','width':width}
        (directory/'vin.json').write_text(json.dumps(manifest,indent=1)+'\n',encoding='utf-8',newline='\n');totals['fixtures']+=1;totals['members']+=len(members)
    print(json.dumps(totals));return totals


def oracle_discard_mask(fixture):
    import numpy as np
    discarded=np.ones((fixture['height'],fixture['width']),bool)
    for output in fixture['outputs']:
        values=np.fromfile(output['file'],dtype='<f4').reshape(fixture['height'],fixture['width'],4)
        discarded&=np.all(values==np.float32(-12345),axis=-1)
    return discarded


if __name__=='__main__':
    import sys
    if sys.argv[1:2]==['--capture-inputs']:capture_inputs(sys.argv[2])
    else:main()
