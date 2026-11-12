"""Persisted semantic lookup for permanent canonical integration."""
import json,re
from .identity import NodeLookup,find_node,add_alias
from .selection import BalancedSelector,_mix_sockets

def _restore_selector(nodes,prefix):
    frames=[n for n in nodes if n.bl_idname=='NodeFrame' and n.get('dr.selection.prefix')==prefix]
    if len(frames)!=1:return None
    frame=frames[0]
    # A collapsed material spreads its nodes over stage trees: the selector lives in its frame's
    # tree, and can only be extended when its sources are in that tree as well.
    home=frame.id_data;view=nodes;nodes=home.nodes
    if frame.get('dr.selection.schema')==3:
        from .general import restore_general
        try:return restore_general(nodes,prefix,frame)
        except LookupError:
            if view.id_data is home and len(view)==len(nodes):raise
            return None
    if frame.get('dr.selection.schema')!=2:return None
    bindings=json.loads(frame['dr.selection.bindings'])
    def resolve(reference):
        node=find_node(nodes,reference.get('semantic') or reference['node'])
        if node is None:raise LookupError('Missing persisted selector source '+str(reference))
        for socket in node.outputs:
            if socket.identifier==reference['identifier']:return socket
        raise LookupError('Persisted selector socket changed')
    selector=BalancedSelector.__new__(BalancedSelector)
    selector.nodes=nodes;selector.links=nodes.id_data.links;selector.prefix=prefix;selector.frame=frame
    selector.channel=frame['dr.semantic_id'].removesuffix('.Selection');selector.dtype=bindings['dtype'];selector.hide=False
    try:
        selector.sources={int(z):resolve(v) for z,v in bindings['sources'].items()}
        selector.factors={int(z):resolve(v) for z,v in bindings['factors'].items()}
        selector.default=resolve(bindings['default'])
    except LookupError:
        if view.id_data is home and len(view)==len(nodes):raise
        return None
    selector.results={};selector.raw_results={};selector.weights={};selector.generated=[]
    for node in nodes:
        if node.parent!=frame:continue
        encoded=node.get('dr.selection.zone_set')
        if encoded:
            zones=tuple(json.loads(encoded));out=_mix_sockets(node,selector.dtype)[3]
            (selector.results if node.get('dr.selection.kind')=='guard' else selector.raw_results)[zones]=(out,node)
        encoded=node.get('dr.selection.weight_zones')
        if encoded:selector.weights[tuple(json.loads(encoded))]=node.outputs[0]
    return selector

class SelectionLookup(NodeLookup):
    """Legacy prefix results are reconstructed lazily with equivalent semantics.

    This permits debugging an old intermediate prefix after save/reopen without
    retaining a redundant sequential chain in the rendered graph.
    """
    top=None
    def get(self,key,default=None):
        found=find_node(self.nodes,key)
        if found is None and self.top is not None:found=find_node(self.top,key)
        if found is not None:return found
        match=re.fullmatch(r'(.+)_z([1-8])',key)
        if not match:return default
        prefix,number=match.group(1),int(match.group(2));selector=_restore_selector(self.nodes,prefix)
        if selector is None or number not in selector.sources:return default
        zones=tuple(z for z in sorted(selector.sources) if z<=number)
        if not zones:return default
        result=selector.result(zones);add_alias(result.node,key)
        return result.node
    def output(self,key):
        from .identity import output_socket
        node=self.get(key)
        if node is None:raise KeyError(key)
        return output_socket(node)


def _wrapper_nodes(nodes):
    """Nodes of the DR Outfit wrapper tree when ``nodes`` is a collapsed material tree, else None."""
    try:node=nodes.get('DR Outfit')
    except (AttributeError,TypeError):return None
    tree=getattr(node,'node_tree',None) if node is not None and node.bl_idname=='ShaderNodeGroup' else None
    if tree is None or not tree.get('dr.wrapper'):return None
    from ..wrapper_tree import graph_nodes
    return graph_nodes(nodes.id_data)

def lookup(nodes):
    """Lookup over a material's setup nodes. A collapsed material keeps them in its wrapper tree;
    the shader and output stay in the material tree and are found second."""
    inner=_wrapper_nodes(nodes)
    if inner is None:return SelectionLookup(nodes)
    found=SelectionLookup(inner);found.top=nodes
    return found
