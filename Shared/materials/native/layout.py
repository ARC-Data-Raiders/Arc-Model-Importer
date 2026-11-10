"""Original indexed semantic layout. Cyclic shader links are laid out as components."""
from collections import deque
import threading

STAGES=('DR.Coordinates','DR.Source','DR.LayerInputs','DR.Wear','DR.Surface','DR.Details','DR.Light','DR.Coverage','DR.Output')

def _key(node): return node.as_pointer() if hasattr(node,'as_pointer') else id(node)

def _component_depths(keys,edges):
    adjacency={key:[] for key in keys}; reverse={key:[] for key in keys}
    for source,target in edges:
        if source in adjacency and target in adjacency: adjacency[source].append(target); reverse[target].append(source)
    visited=set(); finish=[]
    for start in keys:
        if start in visited: continue
        visited.add(start); stack=[(start,iter(adjacency[start]))]
        while stack:
            key,iterator=stack[-1]
            child=next(iterator,None)
            if child is None: finish.append(key); stack.pop()
            elif child not in visited: visited.add(child); stack.append((child,iter(adjacency[child])))
    membership={}; components=[]
    for start in reversed(finish):
        if start in membership: continue
        component=len(components); members=[]; pending=[start]; membership[start]=component
        while pending:
            key=pending.pop(); members.append(key)
            for previous in reverse[key]:
                if previous not in membership: membership[previous]=component; pending.append(previous)
        components.append(members)
    outgoing={i:[] for i in range(len(components))}; indegree={i:0 for i in outgoing}; deduplicated=set(); cyclic=set()
    for source in keys:
        for target in adjacency[source]:
            a,b=membership[source],membership[target]
            if a==b:
                if source==target or len(components[a])>1: cyclic.add(a)
            elif (a,b) not in deduplicated:
                deduplicated.add((a,b)); outgoing[a].append(b); indegree[b]+=1
    depths={i:0 for i in outgoing}; ready=deque(i for i in outgoing if indegree[i]==0)
    while ready:
        component=ready.popleft()
        for child in outgoing[component]:
            depths[child]=max(depths[child],depths[component]+1); indegree[child]-=1
            if indegree[child]==0: ready.append(child)
    return {key:depths[membership[key]] for key in keys},len(cyclic)

def _height(node):
    sockets=list(getattr(node,'inputs',()))+list(getattr(node,'outputs',()))
    visible=sum(not getattr(socket,'hide',False) and getattr(socket,'enabled',True) for socket in sockets)
    return max(100.,48.+22.*visible)

def arrange(tree):
    """Arrange one tree without altering names, parents, links or shader operands."""
    if threading.current_thread() is not threading.main_thread(): raise RuntimeError('Shader arrangement requires the main thread')
    nodes=list(tree.nodes); keys={_key(node):node for node in nodes}; direct={None:[]}; edges=[]
    for node in nodes:
        parent=getattr(node,'parent',None); direct.setdefault(_key(parent) if parent is not None else None,[]).append(node)
    for link in tree.links:
        edges.append((_key(link.from_node),_key(link.to_node)))
    by_parent_edges={parent:[] for parent in direct}
    for source,target in edges:
        a,b=keys[source],keys[target]
        pa=getattr(a,'parent',None); pb=getattr(b,'parent',None)
        if pa==pb: by_parent_edges[_key(pa) if pa is not None else None].append((source,target))
    _,cycles=_component_depths(list(keys),edges)
    active=set()
    def container(parent):
        if parent in active: return 0.
        active.add(parent); children=direct.get(parent,[])
        ordinary=[node for node in children if getattr(node,'type',None)!='FRAME']
        frames=[node for node in children if getattr(node,'type',None)=='FRAME']
        depths,_=_component_depths([_key(node) for node in ordinary],by_parent_edges.get(parent,()))
        offsets={}
        for node in ordinary:
            depth=depths[_key(node)]; y=offsets.get(depth,0.)
            node.location=(depth*190.,-y); node.width=170.; offsets[depth]=y+_height(node)+30.
        x=(max(depths.values(),default=-1)+1)*190.+(120. if ordinary and frames else 0.)
        ordered=[frame for stage in STAGES for frame in frames if frame.name==stage]+[frame for frame in frames if frame.name not in STAGES]
        for frame in ordered:
            width=container(_key(frame)); frame.location=(x,0.); x+=max(width,190.)+120.
        active.remove(parent); return x
    container(None)
    # Invalid parent cycles cannot occur in Blender; still avoid nontermination for
    # third-party tree adapters and arrange their ordinary children deterministically.
    for parent in direct:
        if parent is not None and parent not in active and parent not in keys: container(parent)
    try: tree['dr_layout_cycle_components']=cycles
    except (TypeError,AttributeError): pass
    return None

