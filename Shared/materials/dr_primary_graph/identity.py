"""Stable semantic identities and explicit legacy output lookup for staged graphs."""
import json

SEMANTIC='dr.semantic_id'
ALIASES='dr.legacy_aliases'

def aliases(node):
    try:return tuple(json.loads(node.get(ALIASES,'[]')))
    except (TypeError,ValueError):return ()

def has_alias(node,alias):
    """``alias in aliases(node)`` without decoding lists that cannot hold it.

    Aliases are written by json.dumps, which emits a plain string verbatim, so
    a missing quoted substring proves absence; anything else decodes as before.
    """
    raw=node.get(ALIASES)
    if raw is None:return False
    if isinstance(raw,str) and isinstance(alias,str) and json.dumps(alias)[1:-1]==alias and '"'+alias+'"' not in raw:return False
    return alias in aliases(node)

def assign(owner,attr,value):
    """Write an RNA property only when it changes.

    Any node or socket property write re-runs the whole node-tree update, even
    when it stores the value already there.
    """
    if getattr(owner,attr)!=value:setattr(owner,attr,value)

def write_locations(nodes,placed):
    """Set parent-relative locations keyed by node pointer in one foreach_set.

    A per-node location write re-runs the whole node-tree update (seconds over
    a 1600-node outfit tree); foreach_set skips the per-item update and leaves
    the same location and location_absolute.
    """
    if not placed:return
    flat=[0.]*(2*len(nodes))
    nodes.foreach_get('location',flat)
    for index,node in enumerate(nodes):
        target=placed.get(node.as_pointer())
        if target is not None:flat[2*index]=float(target[0]);flat[2*index+1]=float(target[1])
    nodes.foreach_set('location',flat)

def add_alias(node,alias):
    values=list(aliases(node))
    if alias not in values:values.append(alias)
    node[ALIASES]=json.dumps(values)

def identify(node,semantic_id,*,label=None,legacy_alias=None):
    node.name=semantic_id
    node.label=label or semantic_id.rsplit('.',1)[-1]
    node[SEMANTIC]=semantic_id
    node['dr.stage']='.'.join(semantic_id.split('.')[:2])
    node['dr.generated_by']='DataRaiders primary graph candidate v1'
    if legacy_alias:add_alias(node,legacy_alias)
    return node

def find_node(nodes,identifier,default=None):
    """Resolve native names, semantic IDs or aliases; ambiguity is an error."""
    matches=[]
    direct=nodes.get(identifier)
    if direct is not None:matches.append(direct)
    # has_alias inlined: every lookup visits every node of the tree.
    quoted='"'+identifier+'"' if isinstance(identifier,str) and json.dumps(identifier)[1:-1]==identifier else None
    for node in nodes:
        if node.get(SEMANTIC)!=identifier:
            raw=node.get(ALIASES)
            if raw is None or quoted is not None and isinstance(raw,str) and quoted not in raw or identifier not in aliases(node):continue
        if node not in matches:matches.append(node)
    if len(matches)>1:raise LookupError('Ambiguous graph identity '+identifier)
    return matches[0] if matches else default

def output_socket(node):
    identifier=node.get('dr.result_identifier')
    if identifier:
        for socket in node.outputs:
            if socket.identifier==identifier:return socket
    if node.bl_idname=='ShaderNodeMix':
        from .selection import _mix_sockets
        return _mix_sockets(node,node.data_type)[3]
    if len(node.outputs)==1:return node.outputs[0]
    raise LookupError('No explicit result socket identity on '+node.name)

def find_output(nodes,identifier):
    node=find_node(nodes,identifier)
    if node is None:raise KeyError(identifier)
    return output_socket(node)

class NodeLookup:
    """Adapter for callback/cache/debug code that formerly used nodes.get().

    Existing parameter/decal names are unchanged. Selector aliases refer to
    equivalent output stages; callers should use output() for typed results.
    """
    def __init__(self,nodes):self.nodes=nodes
    def get(self,key,default=None):return find_node(self.nodes,key,default)
    def output(self,key):return find_output(self.nodes,key)
    def __getitem__(self,key):
        node=self.get(key)
        if node is None:raise KeyError(key)
        return node
    def __iter__(self):return iter(self.nodes)
    def __getattr__(self,name):return getattr(self.nodes,name)
