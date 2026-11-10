"""Gate V: synthetic cooked Niagara VM cases, engine oracle and bounded comparison.

All data, engine, project and log locations are explicit command arguments.
No game process, package mounting, shader interpretation or addon integration.
"""
import argparse
import base64
import collections
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import Niagara.niagara as niagara_ir
import Niagara.niagara as niagara_vm
from Niagara.niagara import Dataset, ExecutionError, IdState, SUPPORTED_OPS_57, execute, float_word, word_float, word_int

HEADER_NAMES='NumBytecodeBytes MaxOutputDataSet NumConstsAlloced NumTempRegisters NumConstsRemapped NumInputsRemapped NumNoAdvanceInputs NumInputDataSets NumOutputsRemapped NumOutputInstructions NumExtFns MaxExtFnRegisters NumDummyRegsReq MaxExtFnUsed Flags HashId'.split()
CLASSES='oracle_error eval_error exact_mismatch id_mismatch access_mismatch ext_mismatch fma_diff approx_fail approx_pass exact_pass'.split()
FLAGS='has_random has_id has_ext has_svml has_rcp has_fma'.split()
CYCLE=(0.0,1.0,-1.0,0.5,2.0,1e6,1e-6)

def read(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def write(p,obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
def b64(data):return base64.b64encode(data).decode('ascii')
def unb64(data):return base64.b64decode(data)
def words(data):
    raw=unb64(data);return list(struct.unpack('<'+'I'*(len(raw)//4),raw))
def encode_words(values):return b64(struct.pack('<'+'I'*len(values),*(v & 0xffffffff for v in values)))
def case_paths(root):return sorted(Path(root).glob('batch_*/*.case.json'))
def gen(args):
    import numpy as np
    corpus,out=Path(args.corpus),Path(args.out);out.mkdir(parents=True,exist_ok=True)
    programs=[];unsupported=[];all_cases=[];index={}
    for path in sorted(corpus.rglob('NS_*.json')):
        ir=niagara_ir.extract_document(read(path),source_path=str(path))
        for s in ir['scripts']:
            vm=s['vm']
            if s['bytecode']['state']!='present' or not vm.get('ExperimentalContextData'):continue
            programs.append((ir['source']['packagePath'],s['exportIndex'],s))
    for package,export_index,s in sorted(programs,key=lambda item:(item[0],item[1])):
        vm=s['vm'];legacy=niagara_vm.bytecode_from_vm(vm);carrier=vm['ExperimentalContextData'];context=bytes(carrier) if isinstance(carrier,list) else unb64(carrier);ext=vm.get('CalledVMExternalFunctions') or []
        try:program=niagara_vm.vm_disassemble(legacy,external_functions=ext,profile='pioneer-2.1.0')
        except niagara_vm.DecodeError:
            unsupported.append({'program_id':s['id'],'reason':'decode_error'});continue
        header=dict(zip(HEADER_NAMES,struct.unpack_from('<IIHIHHHHHHIIIiIQ',context,0)))
        names={ins.name for ins in program.instructions};reason=None
        if header['NumExtFns']!=len(ext):reason='extfn_count'
        elif any('half' in name for name in names):reason='half_io'
        elif names-SUPPORTED_OPS_57:reason='op:'+sorted(names-SUPPORTED_OPS_57)[0]
        elif any(not isinstance(f,dict) or not isinstance(f.get('InputParamLocations'),list) or not isinstance(f.get('NumOutputs'),int) or isinstance(f.get('NumOutputs'),bool) or f['NumOutputs']<0 for f in ext):reason='ext_signature'
        if reason:unsupported.append({'program_id':s['id'],'reason':reason});continue
        layout={};id_sets=set();max_offset=-1
        for ins in program.instructions:
            name,ops=ins.name,ins.operands
            for operand in ops:
                if operand.kind=='constant':max_offset=max(max_offset,operand.value)
            if name.startswith(('inputdata','outputdata')) or name in ('acquireindex','acquire_id','update_id'):
                d=ops[0].value;layout.setdefault(d,{'inputs':{'float':0,'int':0},'outputs':{'float':0,'int':0}})
                if name.startswith('inputdata'):
                    kind='int' if name.endswith('int32') else 'float';layout[d]['inputs'][kind]=max(layout[d]['inputs'][kind],ops[1].value+1)
                elif name.startswith('outputdata'):
                    kind='int' if name.endswith('int32') else 'float';layout[d]['outputs'][kind]=max(layout[d]['outputs'][kind],ops[3].value+1)
                elif name in ('acquire_id','update_id'):id_sets.add(d)
            if name=='external_func_call':
                signature=ext[ops[0].value]
                for operand in ops[1:1+len(signature['InputParamLocations'])]:
                    if operand.value==0xffff:raise RuntimeError('Unspecified corpus external input 0xFFFF')
                    if not operand.value & 0x8000:max_offset=max(max_offset,operand.value)
        dataset_count=max(max(layout,default=-1)+1,header['MaxOutputDataSet'],header['NumInputDataSets'])
        for n in [int(v) for v in args.sizes.split(',')]:
            cid=hashlib.sha256(s['id'].encode('utf-8')).hexdigest().upper()[:16]+'_n'+str(n)
            rng=np.random.default_rng(int(cid[:8],16));float_position=0;cycle_position=0
            def floats(count):
                nonlocal float_position,cycle_position
                values=rng.uniform(-4,4,count).astype(np.float32)
                for i in range(count):
                    if float_position % 8==7:values[i]=CYCLE[cycle_position%len(CYCLE)];cycle_position+=1
                    float_position+=1
                return values
            constant_size=max(16,((max_offset+4+15)//16)*16);table=floats(constant_size//4).astype('<f4').tobytes();datasets=[]
            for d in range(dataset_count):
                counts=layout.get(d,{'inputs':{'float':0,'int':0},'outputs':{'float':0,'int':0}});inputs={'float':[],'int':[]}
                for kind in ['float','int']:
                    for reg in range(counts['inputs'][kind]):
                        if kind=='float':raw=floats(n).astype('<f4').tobytes()
                        else:raw=rng.integers(0 if id_sets else -1000,n if id_sets else 1001,n).astype('<i4').tobytes()
                        inputs[kind].append(b64(raw))
                state={'id_table':[-1]*(2*n+64),'free_ids':list(range(n+8))[::-1],'num_free_ids':n+8,'free_capacity':4*n+64,'max_used_id':-1,'id_acquire_tag':7} if d in id_sets else None
                datasets.append({'inputs':inputs,'outputs':counts['outputs'],'id':state})
            case={'schema':'dr.vm-oracle-case/1','case_id':cid,'program_id':s['id'],'num_instances':n,'context_b64':b64(context),'legacy_bytecode_b64':b64(legacy),'constant_tables_b64':[b64(table)],'datasets':datasets,'ext_functions':[{'num_inputs':len(f['InputParamLocations']),'num_outputs':f['NumOutputs']} for f in ext],'num_user_ptrs':16}
            all_cases.append(case);meta={'program_id':s['id'],'package':package,'export_index':export_index,'usage':s['usage'],'N':n,'header':{**header,'HashId':format(header['HashId'],'016X')},'opcodes':sorted(names),'has_random':'random' in names,'has_id':bool(id_sets),'has_ext':'external_func_call' in names,'has_svml':bool(names & {'sin','cos','tan','acos','atan2','pow','fmod'}),'has_rcp':'rcp' in names,'has_fma':bool(names & {'mad','lerp'})};index[cid]=meta
    for i,case in enumerate(sorted(all_cases,key=lambda c:c['case_id'])):write(out/('batch_'+str(i//1000).zfill(3))/(case['case_id']+'.case.json'),case)
    write(out/'index.json',index);write(out/'unsupported.json',unsupported)
    print(json.dumps({'programs':len(programs),'cases':len(all_cases),'supported_programs':len({c['program_id'] for c in all_cases}),'unsupported_reasons':dict(collections.Counter(x['reason'] for x in unsupported))},sort_keys=True))

def psquote(value):return "'"+str(value).replace("'","''")+"'"
def oracle(args):
    root,out,logs,scripts=map(Path,[args.cases,args.out,args.logs,args.scripts]);out.mkdir(parents=True,exist_ok=True);logs.mkdir(parents=True,exist_ok=True);scripts.mkdir(parents=True,exist_ok=True);journal={'crashes':[],'timeouts':[],'batches':[]};first=True
    for batch in sorted(root.glob('batch_*')):
        target=out/batch.name;target.mkdir(parents=True,exist_ok=True);startup_failures=0
        for attempt in range(26):
            log=logs/('NN5-oracle-'+batch.name+'-'+str(attempt)+'.log');console=logs/(log.stem+'-console.log');meta=target/'_process.json'
            cmd=[str(args.uecmd),str(args.uproj),'-unattended','-nosplash','-NoSound','-NoSourceControl','-NoEpicPortal','-ddc=InstalledNoZenLocalFallback','-NoZenAutoLaunch','-nullrhi','-NoCompileGlobalShaders','-abslog='+str(log),'-NiagaraVMOracleCases='+str(batch),'-NiagaraVMOracleOut='+str(target),'-NiagaraVMOracleExit','-ini:EditorSettings:[/Script/UnrealEd.AnalyticsPrivacySettings]:bSendUsageData=False','-ini:EditorSettings:[/Script/UnrealEd.CrashReportsPrivacySettings]:bSendUnattendedBugReports=False']
            argline=subprocess.list2cmdline(cmd[1:]);lab=Path(args.lab)
            script="""$ErrorActionPreference='Stop'
$all=@(Get-CimInstance Win32_Process -Filter "Name='UnrealEditor.exe' OR Name='UnrealEditor-Cmd.exe' OR Name='CrashReportClientEditor.exe'" | Select-Object ProcessId,Name,ExecutablePath,CommandLine)
$inventory=@($all | Where-Object {$_.ExecutablePath -and $_.ExecutablePath.StartsWith(ENGINE_ROOT,[StringComparison]::OrdinalIgnoreCase)}) | ConvertTo-Json -Compress
$othersText=@($all | Where-Object {-not ($_.ExecutablePath -and $_.ExecutablePath.StartsWith(ENGINE_ROOT,[StringComparison]::OrdinalIgnoreCase))}) | ConvertTo-Json -Compress
$inventoryText=if([string]::IsNullOrWhiteSpace($inventory)){'<empty output>'}else{$inventory}
[IO.File]::WriteAllText(INVENTORY_LOG,"command: R10 CIM inventory (UE57 scope)`nstatus: 0`noutput: $inventoryText`nother_engines: $othersText`n",(New-Object Text.UTF8Encoding($false)))
if($inventory -and $inventory -ne '[]'){exit 73}
$env:TEMP=LAB_TMP;$env:TMP=$env:TEMP
[Environment]::SetEnvironmentVariable('UE-LocalDataCachePath',LAB_DDC,'Process')
[Environment]::SetEnvironmentVariable('UE-SharedDataCachePath','None','Process')
[Environment]::SetEnvironmentVariable('UE-ZenDataPath',LAB_ZEN,'Process')
$p=Start-Process -FilePath UECMD -ArgumentList ARGS -WindowStyle Hidden -PassThru
$timedout=-not $p.WaitForExit(TIMEOUT_MS)
if($timedout){Stop-Process -Id $p.Id -Force;$p.WaitForExit()}
$monitorPattern='-MONITOR='+$p.Id+'( |$)';$monitorDeadline=(Get-Date).AddSeconds(120)
do{$monitors=@(Get-CimInstance Win32_Process -Filter "Name='CrashReportClientEditor.exe'" | Where-Object {$_.CommandLine -match $monitorPattern});if($monitors.Count -eq 0){break};Start-Sleep -Milliseconds 500}while((Get-Date) -lt $monitorDeadline)
[IO.File]::WriteAllText(META,(@{pid=$p.Id;exit=$p.ExitCode;timeout=$timedout;monitor_left=$monitors.Count}|ConvertTo-Json -Compress),(New-Object Text.UTF8Encoding($false)))
if($monitors.Count -gt 0){exit 74}
"""
            for token,value in [('ENGINE_ROOT',psquote(Path(args.uecmd).parents[3])),('INVENTORY_LOG',psquote(logs/(log.stem+'-R10.log'))),('LAB_TMP',psquote(lab/'tmp')),('LAB_DDC',psquote(lab/'ddc')),('LAB_ZEN',psquote(lab/'zen')),('UECMD',psquote(args.uecmd)),('ARGS',psquote(argline)),('TIMEOUT_MS',str(int(args.timeout_min*60000))),('META',psquote(meta))]:script=script.replace(token,value)
            path=scripts/('oracle-'+batch.name+'-'+str(attempt)+'.ps1');path.write_text(script,encoding='utf-8')
            with console.open('w',encoding='utf-8') as f:
                command=['powershell.exe','-NoProfile','-File',str(path)];f.write('command: '+subprocess.list2cmdline(command)+'\n');f.flush();p=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT);f.write('\nstatus: '+str(p.returncode)+'\n')
            if p.returncode==73:raise RuntimeError('R10: existing Unreal process; no process touched')
            if p.returncode==74:raise RuntimeError('Crash-report monitor of this launch still running after 120 s; no process touched')
            if p.returncode:raise RuntimeError('Oracle launch script failed: '+str(p.returncode))
            process=read(meta);journal['batches'].append({'batch':batch.name,'attempt':attempt,**process})
            if (target/'_summary.json').exists():break
            progress=target/'_progress.txt';cid=progress.read_text(encoding='utf-8-sig').strip().removesuffix('.case.json') if progress.exists() else None
            if cid and not (target/(cid+'.result.json')).exists():
                kind='oracle_timeout' if process['timeout'] else 'oracle_crash';write(target/(cid+'.result.json'),{'schema':'dr.vm-oracle-result/1','case_id':cid,'ok':False,'error':kind});journal['timeouts' if process['timeout'] else 'crashes'].append(cid);startup_failures=0
            else:startup_failures+=1
            write(out/'oracle_runs.json',journal)
            if startup_failures>=3:raise RuntimeError('Three consecutive oracle startup failures')
            if attempt==25:raise RuntimeError('Oracle batch relaunch limit 25 reached')
        if first:
            results=[read(p) for p in target.glob('*.result.json')];errors=collections.Counter(r.get('error') for r in results if not r.get('ok'))
            if errors and max(errors.values())*2>=len(results):write(out/'oracle_runs.json',{**journal,'systemic_errors':dict(errors)});raise RuntimeError('Systemic oracle errors: '+json.dumps(dict(errors),sort_keys=True))
            first=False
        write(out/'oracle_runs.json',journal)

def eval_one(payload):
    path,out,backend,shim,fma=payload;c=read(path);n=c['num_instances'];datasets={};ids={}
    for d,ds in enumerate(c['datasets']):
        inputs={};capacities={}
        for kind,internal,convert in [('float','float',word_float),('int','int32',word_int)]:
            for a,blob in enumerate(ds['inputs'][kind]):inputs[internal,a]=[convert(w) for w in words(blob)]
            for a in range(ds['outputs'][kind]):capacities[internal,a]=n
        datasets[d]=Dataset(inputs=inputs,output_capacities=capacities,instance_offset=0,access_index=-1)
        if ds['id'] is not None:
            s=ds['id'];ids[d]=IdState(s['id_table'],s['free_ids'],s['num_free_ids'],s['free_capacity'],s['max_used_id'],0,[],s['id_acquire_tag'])
    ext=[{'InputParamLocations':[0]*f['num_inputs'],'NumOutputs':f['num_outputs']} for f in c['ext_functions']]
    result={'schema':'dr.vm-oracle-result/1','case_id':c['case_id']}
    try:
        r=execute(unb64(c['legacy_bytecode_b64']),num_instances=n,constant_tables=[unb64(t) for t in c['constant_tables_b64']],datasets=datasets,external_functions=ext,profile='pioneer-2.1.0',x86_semantics=True,math_backend=backend,svml_shim_path=shim,fma_mode=fma,external_handler='gate_v_stub',id_states=ids,rng_seed=(1,3))
        result.update(ok=True,datasets=[],ext_calls=[])
        for d,ds in enumerate(c['datasets']):
            output={'outputs':{'float':[],'int':[]},'access_index':r.access_indices.get(d,-1),'taint':sorted(r.dataset_taints.get(d,())),'output_taints':{'float':[],'int':[]}}
            for kind,internal in [('float','float'),('int','int32')]:
                for a in range(ds['outputs'][kind]):
                    values=r.outputs[d,(internal,a)];output['outputs'][kind].append(encode_words([0xdeadbeef if v is None else float_word(v) if kind=='float' else v & 0xffffffff for v in values]));output['output_taints'][kind].append(sorted(r.output_taints.get((d,(internal,a)),())))
            if d in r.id_states:
                s=r.id_states[d];output['id']={'id_table':s.id_table,'free_ids_valid':s.free_ids,'spawned_ids':s.spawned_ids,'num_free_ids':s.num_free_ids,'num_spawned_ids':s.num_spawned_ids,'max_used_id':s.max_used_id,'free_capacity':s.free_capacity}
            result['datasets'].append(output)
        for call in r.ext_calls:result['ext_calls'].append({**{k:v for k,v in call.items() if k!='inputs'},'inputs_b64':encode_words(call['inputs'])})
    except ExecutionError as e:result.update(ok=False,error=str(e))
    write(Path(out)/(c['case_id']+'.eval.json'),result)
    return c['case_id']
def evaluate(args):
    paths=case_paths(args.cases);selected=None
    if args.only_list:
        text=Path(args.only_list).read_text(encoding='utf-8-sig');selected=set(json.loads(text) if text.lstrip().startswith('[') else text.splitlines())
    Path(args.out).mkdir(parents=True,exist_ok=True);payloads=[(str(p),args.out,args.math_backend,args.svml_shim,args.fma) for p in paths if selected is None or p.name.removesuffix('.case.json') in selected]
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for _ in pool.map(eval_one,payloads):pass

def same_word(a,b):
    if a==b:return True
    return a is not None and b is not None and math.isnan(word_float(a)) and math.isnan(word_float(b))
def classify(taint,exact='exact_mismatch'):
    if 'rng' in taint:return 'excluded_rng'
    if 'svml' in taint or 'hwest' in taint:return 'approx'
    if 'fma' in taint:return 'fma_diff'
    return exact
def normalize_calls(calls,signatures):
    counts=collections.Counter();normalized={}
    for call in calls:
        fn,start,count=call['fn'],call['start'],call['count'];k=counts[fn,start];counts[fn,start]+=1;ni=signatures[fn]['num_inputs'];values=words(call['inputs_b64']);entry=normalized.setdefault((fn,k),{})
        for i in range(count):
            for operand in range(ni):entry[start+i,operand]=(values[i*ni+operand],(call.get('input_taints') or [[] for _ in range(ni)])[operand])
    return normalized

def compare(args):
    root=Path(args.cases);index=read(root/'index.json');oracle_paths={p.name.removesuffix('.result.json'):p for p in Path(args.oracle).rglob('*.result.json')};eval_paths={p.name.removesuffix('.eval.json'):p for p in Path(args.eval).rglob('*.eval.json')};counts=collections.Counter();by_usage={};by_flag={f:{} for f in FLAGS};per_case={};failures=[];max_rel=0.0
    for path in case_paths(root):
        c=read(path);cid=c['case_id']
        if cid not in eval_paths:continue
        if cid not in oracle_paths:raise RuntimeError('Missing oracle result '+cid)
        a,b=read(oracle_paths[cid]),read(eval_paths[cid]);classes=[];diffs=[];excluded=0
        def difference(location,lane,aw,bw,taint,exact='exact_mismatch',state=False):
            nonlocal max_rel,excluded
            cls=classify(taint,exact)
            if cls=='excluded_rng':excluded+=1;return
            rel=None
            if cls=='approx':
                if state:cls='approx_fail'
                else:
                    x,y=word_float(aw) if aw is not None else math.nan,word_float(bw) if bw is not None else math.nan
                    rel=abs(x-y)/max(1,abs(y)) if math.isfinite(x) and math.isfinite(y) else math.inf;max_rel=max(max_rel,rel);cls='approx_fail' if rel>1e-4 else 'approx_pass'
            classes.append(cls)
            if len(diffs)<5:diffs.append({'location':location,'lane':lane,'oracle_word':aw,'eval_word':bw,'taint':sorted(taint),**({'relative_error':rel if math.isfinite(rel) else 'Infinity'} if rel is not None else {})})
        if not a.get('ok'):classes=['oracle_error']
        elif not b.get('ok'):classes=['eval_error']
        else:
            for d,case_ds in enumerate(c['datasets']):
                ad,bd=a['datasets'][d],b['datasets'][d]
                for kind in ['float','int']:
                    for reg in range(case_ds['outputs'][kind]):
                        av,bv=words(ad['outputs'][kind][reg]),words(bd['outputs'][kind][reg]);taint=bd['output_taints'][kind][reg]
                        for lane,(aw,bw) in enumerate(zip(av,bv)):
                            if lane<=min(ad['access_index'],bd['access_index']) and not same_word(aw,bw):difference('dataset:'+str(d)+'/output:'+kind+':'+str(reg),lane,aw,bw,taint)
                taint=bd['taint']
                if ad['access_index']!=bd['access_index']:difference('dataset:'+str(d)+'/access_index',None,ad['access_index'],bd['access_index'],taint,'access_mismatch',True)
                if case_ds['id'] is not None:
                    for key in ['id_table','free_ids_valid','spawned_ids','num_free_ids','num_spawned_ids','max_used_id']:
                        if ad['id'][key]!=bd['id'][key]:difference('dataset:'+str(d)+'/id:'+key,None,ad['id'][key],bd['id'][key],taint,'id_mismatch',True)
            ac,bc=normalize_calls(a['ext_calls'],c['ext_functions']),normalize_calls(b['ext_calls'],c['ext_functions'])
            for key in sorted(set(ac)|set(bc)):
                av,bv=ac.get(key,{}),bc.get(key,{})
                for lane_operand in sorted(set(av)|set(bv)):
                    aw=av.get(lane_operand,(None,[]))[0];bw,t=bv.get(lane_operand,(None,[]))
                    if not same_word(aw,bw):difference('external:'+str(key)+'/input:'+str(lane_operand[1]),lane_operand[0],aw,bw,t,'ext_mismatch')
        cls=min(classes,key=CLASSES.index) if classes else 'exact_pass';counts[cls]+=1;usage=index[cid]['usage'];by_usage.setdefault(usage,collections.Counter())[cls]+=1
        for flag in FLAGS:by_flag[flag].setdefault(str(index[cid][flag]).lower(),collections.Counter())[cls]+=1
        per_case[cid]={'class':cls,'excluded_lanes':excluded,'differences':diffs}
        if cls not in ['exact_pass','approx_pass'] and len(failures)<200:failures.append({'case_id':cid,'program_id':c['program_id'],'class':cls,'differences':diffs})
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True);write(out/'summary.json',{'counts':dict(counts),'per_usage':by_usage,'per_flag':by_flag,'cases':per_case,'max_relative_error':max_rel if math.isfinite(max_rel) else 'Infinity'});(out/'failures.jsonl').write_text(''.join(json.dumps(row,sort_keys=True)+'\n' for row in failures),encoding='utf-8')
def report(args):
    modes={p.parent.name:read(p) for p in sorted(Path(args.compare).glob('*/summary.json'))};best={}
    for mode,s in modes.items():
        for cid,result in s['cases'].items():
            cls=result['class']
            if cid not in best or CLASSES.index(cls)>CLASSES.index(best[cid]['class']):best[cid]={'class':cls,'mode':mode}
    write(args.out,{'modes':{mode:{k:v for k,v in s.items() if k!='cases'} for mode,s in modes.items()},'best_cases':best,'best_counts':dict(collections.Counter(v['class'] for v in best.values()))})

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('gen');p.add_argument('--corpus',required=True);p.add_argument('--out',required=True);p.add_argument('--sizes',required=True);p.set_defaults(func=gen)
    p=sub.add_parser('oracle')
    for name in ['cases','out','uecmd','uproj','lab','logs','scripts']:p.add_argument('--'+name,required=True)
    p.add_argument('--timeout-min',required=True,type=float);p.set_defaults(func=oracle)
    p=sub.add_parser('eval')
    for name in ['cases','out']:p.add_argument('--'+name,required=True)
    p.add_argument('--math-backend',choices=['reference','svml_shim'],required=True);p.add_argument('--fma',choices=['unfused','fused'],required=True);p.add_argument('--svml-shim');p.add_argument('--only-list');p.add_argument('--jobs',type=int,required=True);p.set_defaults(func=evaluate)
    p=sub.add_parser('compare')
    for name in ['cases','oracle','eval','out']:p.add_argument('--'+name,required=True)
    p.set_defaults(func=compare)
    p=sub.add_parser('report');p.add_argument('--compare',required=True);p.add_argument('--out',required=True);p.set_defaults(func=report)
    args=parser.parse_args();args.func(args)
if __name__=='__main__':main()