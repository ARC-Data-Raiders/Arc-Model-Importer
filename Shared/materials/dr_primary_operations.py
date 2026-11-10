"""Build-time DR operation identities and dependency-based semantic layout.

Composition/numerical primitives remain retained repository code, credited in
manifest. This bounded factory pass changes identity and organization only.
"""
import json,re,hashlib
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from .dr_primary_graph.identity import identify,aliases,write_locations
from .dr_primary_graph.lookup import lookup

RECIPE=hashlib.sha256(Path(__file__).read_bytes()+Path(__file__).with_name('gt_outfit_pbr.py').read_bytes()+Path(__file__).with_name('dr_packed_decal.py').read_bytes()+Path(__file__).with_name('dr_layered_composer.py').read_bytes()+Path(__file__).with_name('dr_layered_decals.py').read_bytes()+(Path(__file__).with_name('dr_primary_graph')/'general.py').read_bytes()+(Path(__file__).with_name('dr_primary_graph')/'selection.py').read_bytes()+Path(__file__).with_name('graph_blocks.py').read_bytes()).hexdigest()[:12]

STAGES=('Source','Coordinates','LayerInputs','Wear','Surface','Details','Light','Coverage','Output')

def stage_for(name,kind):
    text=str(name).lower()
    # Roles refer to local builder symbols, never infer packed texture channels.
    if any(t in text for t in ('alpha','opacity','cutoff','coverage','decalmask','exclude')):return 'Coverage'
    if any(t in text for t in ('emission','emissive','glow')):return 'Light'
    if any(t in text for t in ('_uv','uv_','imageuv','tile','tiling','coord','rot','offset')):return 'Coordinates'
    if any(t in text for t in ('rough','metal','specular','cloth','ior','coat')):return 'Surface'
    if any(t in text for t in ('normal','_n_','nbase','ncrease','nedge','medium','hue','pattern','rnm')):return 'Details'
    if any(t in text for t in ('wear','crease','edge','breakup','occl','dirt')):return 'Wear'
    if kind=='ShaderNodeTexImage':return 'Source'
    return 'LayerInputs'

def role_for(name):
    text=re.sub(r'^(?:GT_|TA_)','',str(name))
    text=re.sub(r'(?<=[a-z0-9])(?=[A-Z])','.',text)
    text=re.sub(r'[^A-Za-z0-9]+','.',text).strip('.')
    return text or 'Constant'

_BUILD=ContextVar('dr_primary_operation_build',default=None)

class FactoryBuild:
    """Lifetime is one explicit build; owner refs avoid pointer reuse hazards."""
    def __init__(self):self.trees={}
    def state(self,nodes):
        owner=nodes.id_data
        key=owner.as_pointer() if hasattr(owner,'as_pointer') else id(owner)
        found=self.trees.get(key)
        if found is not None:
            try:
                if found['owner'] is owner or hasattr(owner,'as_pointer') and found['owner'].as_pointer()==owner.as_pointer():return found
            except ReferenceError:pass
        # Exactly one initial property scan per node-tree collection/build.
        occupied=set();semantics=set()
        for node in nodes:
            occupied.add(node.name);occupied.update(aliases(node));semantics.add(node.get('dr.semantic_id'))
        found={'owner':owner,'occupied':occupied,'semantics':semantics}
        self.trees[key]=found;return found

@contextmanager
def operation_session():
    current=_BUILD.get()
    if current is not None:
        yield current;return
    context=FactoryBuild();token=_BUILD.set(context)
    try:yield context
    finally:
        context.trees.clear();_BUILD.reset(token)

def operation_build(function):
    @wraps(function)
    def wrapped(*args,**kwargs):
        with operation_session():return function(*args,**kwargs)
    return wrapped

def legacy_name(node):
    getter=getattr(node,'get',None)
    return (getter('dr.legacy_name') if getter else None) or getattr(node,'name','')

def legacy_label(node):
    getter=getattr(node,'get',None)
    value=getter('dr.legacy_label') if getter else None
    return value if value is not None else getattr(node,'label','')

def group_key(group):
    return group.get('dr.legacy_group_key',group.name)

def group_name(key):
    stage=stage_for(key,'ShaderNodeGroup')
    role=role_for(key)[:22]
    digest=hashlib.sha256((str(key)+'|'+RECIPE).encode()).hexdigest()[:12]
    return 'DR.'+stage+'.Definition.'+role+'.'+digest

def find_group(groups,key):return groups.get(group_name(key))

def new_group(groups,key,tree_type):
    group=groups.new(group_name(key),tree_type)
    group['dr.legacy_group_key']=str(key);group['dr.group.recipe']=RECIPE
    group['dr.semantic_id']='DR.'+stage_for(key,'ShaderNodeGroup')+'.Definition.'+role_for(key)
    return group

_SEQ_SUFFIX=re.compile(r'\.\d{3,}$')
_STAGE_CACHE={};_ROLE_CACHE={}

def new_operation(nodes,kind,legacy_name=None,*,stage=None,role=None):
    context=_BUILD.get()
    if context is None:raise RuntimeError('Primary operation factory requires explicit operation_session/build')
    state=context.state(nodes);occupied=state['occupied']
    # For formerly unnamed constructors, ask Blender for its actual native
    # default display name, then reserve its legacy duplicate sequence. No
    # hardcoded UI/default-node-name table is needed.
    node=nodes.new(kind)
    pointer=node.as_pointer()
    base=str(legacy_name) if legacy_name is not None else _SEQ_SUFFIX.sub('',node.name)
    alias=base;index=0
    # nodes.get is a linear name scan; keep a name set per tree instead and
    # rebuild it only when nodes appeared outside this factory.
    count=len(nodes);names=state.get('names')
    if names is None or state.get('names_count')!=count-1:
        names=state['names']={n.name for n in nodes}
    else:names.add(node.name)
    state['names_count']=count
    own=node.name
    def native_collision(value):
        return value!=own and value in names
    while alias in occupied or native_collision(alias):
        index+=1;alias=base+'.'+str(index).zfill(3)
    if not stage:
        key=(base,kind);stage=_STAGE_CACHE.get(key)
        if stage is None:stage=_STAGE_CACHE[key]=stage_for(base,kind)
    if not role:
        role=_ROLE_CACHE.get(alias)
        if role is None:role=_ROLE_CACHE[alias]=role_for(alias)
    semantic='DR.'+stage+'.'+role;used=state['semantics'];semantic_base=semantic;counter=1
    while semantic in used or native_collision(semantic):
        counter+=1;semantic=semantic_base+'.Instance'+str(counter)
    # identify() inlined for a fresh node: same writes, same order; a new
    # node has no aliases, so add_alias always stores [alias].
    node['dr.legacy_label']=node.label
    node.name=semantic
    if node.name!=semantic:
        # A node renamed outside the factory kept the set stale: rescan, retry.
        own=node.name;names=state['names']={n.name for n in nodes}
        semantic=semantic_base;counter=1
        while semantic in used or native_collision(semantic):
            counter+=1;semantic=semantic_base+'.Instance'+str(counter)
        node.name=semantic
    names.discard(own);names.add(semantic)
    node.label=semantic.rsplit('.',1)[-1]
    node['dr.semantic_id']=semantic
    node['dr.stage']='.'.join(semantic.split('.')[:2])
    node['dr.generated_by']='DataRaiders primary graph candidate v1'
    if alias:node['dr.legacy_aliases']=json.dumps([alias])
    occupied.add(alias);used.add(semantic)
    node['dr.operation.factory']=1;node['dr.legacy_name']=alias
    node['dr.operation.role']=role;node['dr.stage.rule']='explicit construction role/stage or retained helper role v2'
    if kind=='NodeFrame':
        node['dr.operation.container']=1
        if role.startswith('Annotation.'):node['dr.annotation']=1
    return node

@operation_build
def set_identity(node,legacy_name,*,stage=None,role=None):
    """Apply an explicit later construction-name assignment.

    Native .name assignment frees the prior name. A transient default alias must
    likewise be released, rather than becoming a permanent compatibility alias
    that steals the next unnamed node's native identity.
    """
    if not node.get('dr.operation.factory'):
        node.name=legacy_name;return node
    nodes=node.id_data.nodes;state=_BUILD.get().state(nodes)
    previous=node.get('dr.legacy_name');previous_semantic=node.get('dr.semantic_id')
    if previous:state['occupied'].discard(previous)
    if previous_semantic:state['semantics'].discard(previous_semantic)
    keep=[a for a in aliases(node) if a!=previous]
    node['dr.legacy_aliases']=json.dumps(keep)
    base=str(legacy_name);alias=base;index=0
    def collision(value):
        found=nodes.get(value)
        return found is not None and found.as_pointer()!=node.as_pointer()
    while alias in state['occupied'] or collision(alias):
        index+=1;alias=base+'.'+str(index).zfill(3)
    stage=stage or node.get('dr.stage','DR.LayerInputs').removeprefix('DR.')
    role=role or role_for(alias);semantic='DR.'+stage+'.'+role;base_semantic=semantic;index=1
    while semantic in state['semantics'] or collision(semantic):
        index+=1;semantic=base_semantic+'.Instance'+str(index)
    identify(node,semantic,legacy_alias=alias)
    node['dr.legacy_name']=alias;node['dr.operation.role']=role
    state['occupied'].add(alias);state['semantics'].add(semantic)
    return node

def new_if_primary(nodes,kind,legacy_name=None,*,stage=None,role=None):
    if _BUILD.get() is not None:return new_operation(nodes,kind,legacy_name,stage=stage,role=role)
    node=nodes.new(kind)
    if legacy_name is not None:node.name=legacy_name
    return node


def set_label(node,label):
    if not node.get('dr.operation.factory'):
        node.label=label;return
    node['dr.legacy_label']=str(label)
    if node.bl_idname in {'ShaderNodeValue','ShaderNodeRGB'}:
        node['dr.parameter_key']=str(label)
    node.label=node.get('dr.operation.role',node.name).replace('.',' ')

def organize(nodes,links):
    """Place factory-created ops by semantic stage and intra-stage DAG depth.

    Links, sockets, defaults and untouched operation identities do not change.
    Existing selector frames retain their own proven balanced DAG layout.
    Parents are assigned first; every location is written once at the end.
    """
    owned=[n for n in nodes if n.get('dr.operation.factory')==1 and n.bl_idname!='NodeFrame']
    if not owned:return {'operations':0,'stages':0}
    if nodes.id_data.get('dr.operation.layout_recipe')==RECIPE:return {'operations':len(owned),'stages':sum(n.get('dr.operation.stage_frame',0)==1 for n in nodes)}
    incoming={n.as_pointer():[] for n in owned}
    for link in links:
        if link.to_node.as_pointer() in incoming:incoming[link.to_node.as_pointer()].append(link.from_node)
    depths={};active=set()
    def depth(node):
        key=node.as_pointer()
        if key in depths:return depths[key]
        if key in active:raise ValueError('Cycle in primary shader operation dependencies')
        active.add(key);stage=node.get('dr.stage')
        parents=[p for p in incoming.get(key,()) if p.get('dr.operation.factory')==1 and p.get('dr.stage')==stage]
        value=0 if not parents else 1+max(depth(p) for p in parents)
        active.remove(key);depths[key]=value;return value
    frames={};placed={};by_stage={}
    for n in owned:by_stage.setdefault(n.get('dr.stage'),[]).append(n)
    for stage in STAGES:
        members=by_stage.get('DR.'+stage,[])
        if not members:continue
        frame=nodes.new('NodeFrame');identify(frame,'DR.'+stage+'.Operations',label=stage)
        frame['dr.operation.stage_frame']=1;frame.label_size=24;frames[stage]=frame
        bins={}
        for node in sorted(members,key=lambda n:n['dr.semantic_id']):bins.setdefault(depth(node),[]).append(node)
        for column,members_in_column in sorted(bins.items()):
            for row,node in enumerate(members_in_column):
                node.parent=frame;placed[node.as_pointer()]=(column*250.,-row*160.);node.hide=False
        frame['dr.operation.depth']=max(bins)+1
    # Stage widths reflect actual dependency depth, not old zone-column width.
    x=0.
    for stage in STAGES:
        frame=frames.get(stage)
        if frame is None:continue
        placed[frame.as_pointer()]=(x,0.);x+=frame['dr.operation.depth']*250.+450.
    # Existing explanatory notes are repositioned with their semantic stage;
    # obsolete empty zone-column containers do not retain distant AABBs.
    annotations=sorted((n for n in nodes if n.get('dr.annotation')),key=lambda n:n['dr.semantic_id'])
    for index,node in enumerate(annotations):
        stage=node.get('dr.stage','DR.LayerInputs').removeprefix('DR.')
        frame=frames.get(stage);node.parent=frame
        members=by_stage.get('DR.'+stage,())
        placed[node.as_pointer()]=(0.,-max(1,len(members))*160.-300.-index*130.)
    write_locations(nodes,placed)
    occupied_parents={n.parent.as_pointer() for n in nodes if n.parent is not None}
    for node in list(nodes):
        if node.get('dr.operation.container') and node.get('dr.operation.role','').startswith('Layout.') and node.as_pointer() not in occupied_parents:nodes.remove(node)
    nodes.id_data['dr.operation.layout']='semantic stages and intra-stage dependency depths v2'
    nodes.id_data['dr.operation.layout_recipe']=RECIPE
    return {'operations':len(owned),'stages':len(frames)}


def finish_definition(group):
    organize(group.nodes,group.links)
    return group
