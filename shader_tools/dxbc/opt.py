import argparse
import collections
import copy
import json
import pathlib
import numpy as np
from . import ssa, interp

CLEAN_OPS=set('add mul div mad dp2 dp3 dp4 min max sqrt rsq rcp exp log frc round_ne round_ni round_pi round_z sincos itof utof f16tof32 sat deriv lt ge eq ne ieq ine ilt ige ult uge'.split())
NO_FOLD=set('const input cbload sbload bufload texfetch sample resinfo deriv'.split())


def ids(node):
    return [] if node['op']=='const' else [a for a in node['args'] if type(a) is int]


def reachable(nodes,outputs):
    live=set()
    pending=list(outputs.values())
    while pending:
        ident=pending.pop()
        if ident not in live:
            live.add(ident)
            pending.extend(ids(nodes[ident]))
    return live


def optimize(dag,evaluator):
    nodes=copy.deepcopy(dag['nodes'])
    outputs=dag['outputs'].copy()
    live_in=len(reachable(nodes,outputs))
    repl={}
    removed=dict(ftz=0,fold=0,select=0,cse=0,dce=0)
    def resolve(n):
        while n in repl:n=repl[n]
        return n
    def apply():
        for node in nodes:
            if node['op']!='const':
                node['args']=[resolve(a) if type(a) is int else a for a in node['args']]
        for name,n in outputs.items():outputs[name]=resolve(n)
    def replace(n,v,kind):
        if n not in repl and n!=v:
            repl[n]=resolve(v)
            removed[kind]+=1
    for iteration in range(1,21):
        old=len(repl)
        folds=0
        clean=[]
        for node in nodes:
            op,args=node['op'],node['args']
            read=op=='mov' and isinstance(args[-1],dict) and args[-1].get('read') is True
            if op in CLEAN_OPS:ok=True
            elif read and args[-1]['float_mod']:ok=True
            elif op=='const':ok=(args[0]&0x7F800000)!=0 or (args[0]&0x007FFFFF)==0
            elif op in ['select','movc']:ok=clean[resolve(args[1])] and clean[resolve(args[2])]
            else:ok=False
            clean.append(ok)
            if node['id'] not in repl and read and args[-1]['float_mod'] and not args[-1]['neg'] and not args[-1]['abs'] and clean[resolve(args[0])]:
                replace(node['id'],args[0],'ftz')
        apply()
        for node in nodes:
            refs=ids(node)
            if node['id'] in repl or node['op'] in NO_FOLD or not refs or not all(nodes[n]['op']=='const' for n in refs):continue
            mini=[]
            args=[]
            for a in node['args']:
                if type(a) is int:
                    args.append(len(mini))
                    mini.append(dict(id=len(mini),op='const',args=nodes[a]['args'].copy(),type=nodes[a]['type'],src=nodes[a]['src']))
                else:args.append(copy.deepcopy(a))
            k=len(mini)
            mini.append(dict(node,id=k,args=args))
            evaluator.values=[]
            value=int(evaluator.evaluate(dict(nodes=mini,outputs=dict(v=k)))['v'][0,0])
            node.update(op='const',args=[value])
            removed['fold']+=1
            folds+=1
        apply()
        for node in nodes:
            if node['id'] in repl or node['op'] not in ['select','movc']:continue
            a=node['args']
            if a[1]==a[2]:replace(node['id'],a[1],'select')
            elif nodes[a[0]]['op']=='const':replace(node['id'],a[1] if nodes[a[0]]['args'][0]!=0 else a[2],'select')
        apply()
        keys={}
        live=reachable(nodes,outputs)
        for node in nodes:
            ident=node['id']
            if ident not in live or ident in repl:continue
            key=(node['op'],json.dumps(node['args'],sort_keys=True),node['type'])
            if key in keys:replace(ident,keys[key],'cse')
            else:keys[key]=ident
        apply()
        if len(repl)==old and folds==0:break
    else:raise RuntimeError('Y1 iteration limit: needs iteration 21')
    live=reachable(nodes,outputs)
    removed['dce']=sum(n not in live and n not in repl for n in range(len(nodes)))
    remap={old:new for new,old in enumerate(sorted(live))}
    result=[]
    for old in sorted(live):
        node=copy.deepcopy(nodes[old])
        node['id']=remap[old]
        if node['op']!='const':node['args']=[remap[a] if type(a) is int else a for a in node['args']]
        result.append(node)
    return dict(nodes=result,outputs={name:remap[n] for name,n in outputs.items()}),dict(nodes_in=len(nodes),live_in=live_in,nodes_out=len(result),iterations=iteration,removed=removed)


def load_inputs(wave2):
    result=json.loads((wave2/'out/X3/results.json').read_text())
    passed={(e['sha'],e['mode'],e['winding'],e['seed_offset']) for e in result['shaders'] if e['shader_set']=='SLICE' and e['status']=='pass'}
    return [e for e in json.loads((wave2/'out/X2c/fixtures.json').read_text()) if (e['sha'],e['mode'],e['winding'],e['seed_offset']) in passed]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--wave2',required=True)
    parser.add_argument('--run',required=True)
    args=parser.parse_args()
    wave2=pathlib.Path(args.wave2)
    output=pathlib.Path(args.run)/'out/Y1'
    output.mkdir(parents=True,exist_ok=True)
    def save(name,value):(output/name).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8',newline='\n')
    items=load_inputs(wave2)
    dags={}
    stats={}
    for path in sorted((wave2/'out/X4').glob('*.dag.json')):
        sha=path.name.split('.')[0]
        item=next(e for e in items if e['sha']==sha)
        fixture=json.loads(pathlib.Path(item['fixture']).read_text())
        shader=json.loads(pathlib.Path(fixture['shader_json']).read_text())
        dag,stat=optimize(json.loads(path.read_text()),ssa.Evaluator(shader,fixture))
        if set(dag['outputs'])!=set(json.loads(path.read_text())['outputs']):raise RuntimeError('output keys changed')
        if stat['nodes_out']>stat['live_in']:raise RuntimeError('nodes_out exceeds live_in')
        dags[sha]=dict(schema='dr.dag-opt/1',sha=sha,**dag)
        stats[sha]=stat
        save(sha+'.opt.json',dags[sha])
    equality=[]
    for item in items:
        fixture=json.loads(pathlib.Path(item['fixture']).read_text())
        shader=json.loads(pathlib.Path(fixture['shader_json']).read_text())
        with np.errstate(all='ignore'):registers,discarded,excluded=interp.Interpreter(shader,fixture).execute()
        actual=ssa.Evaluator(shader,fixture).evaluate(dags[item['sha']])
        entry={key:item[key] for key in ['sha','mode','winding','seed_offset']}
        for name,value in actual.items():
            expected=np.where(discarded,np.uint32(0xFFFFFFFF),np.uint32(0)) if name=='kill' else registers[name.split('.')[0]][...,'xyzw'.index(name[-1])]
            mask=np.ones_like(discarded) if name=='kill' else ~discarded
            mismatch=mask&(expected!=value)
            if np.any(mismatch):
                py,px=np.argwhere(mismatch)[0]
                failure=dict(entry,output=name,px=int(px),py=int(py),expected=int(expected[py,px]),actual=int(value[py,px]))
                save('equality.json',dict(passed=False,first_mismatch=failure,fixtures=equality))
                raise RuntimeError(json.dumps(failure))
        equality.append(dict(entry,passed=True))
    save('equality.json',dict(passed=True,count=len(equality),fixtures=equality))
    histogram=collections.Counter(n['op'] for d in dags.values() for n in d['nodes'])
    totals={k:sum(v[k] for v in stats.values()) for k in ['nodes_in','live_in','nodes_out']}
    totals['removed']={k:sum(v['removed'][k] for v in stats.values()) for k in ['ftz','fold','select','cse','dce']}
    save('summary.json',dict(shaders=stats,totals=totals,op_histogram=dict(sorted(histogram.items()))))
    print(json.dumps(dict(shaders=len(dags),fixtures=len(equality),**totals)))


if __name__=='__main__':main()
