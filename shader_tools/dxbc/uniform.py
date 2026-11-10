import copy
import re
import numpy as np
from . import ssa
from .opt import ids


def fold_uniform(dag,shader,fixture):
    flat=set()
    for declaration in shader['declarations']:
        if declaration['op']=='dcl_input_ps' and declaration['text'].startswith('constant '):
            match=re.search(r'\bv(\d+)',declaration['text'])
            if match:flat.add('v'+match[1])
    uniform=[]
    for node in dag['nodes']:
        op=node['op']
        if op in ['const','cbload']:value=True
        elif op=='input':value=node['args'][0] in flat
        elif op=='deriv':value=False
        else:value=all(uniform[n] for n in ids(node))
        uniform.append(value)
    boundary=set(dag['outputs'].values())
    for node in dag['nodes']:
        if not uniform[node['id']]:boundary.update(ids(node))
    evaluator=ssa.Evaluator(shader,fixture)
    evaluator.evaluate(dag)
    result=copy.deepcopy(dag)
    for ident in sorted(boundary):
        if not uniform[ident]:continue
        value=evaluator.values[ident]
        if not np.all(value==value[0,0]):raise ValueError('non-uniform value at node '+str(ident))
        result['nodes'][ident].update(op='const',args=[int(value[0,0])])
    return result
