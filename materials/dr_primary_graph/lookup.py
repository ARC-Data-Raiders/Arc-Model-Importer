"""Persisted semantic lookup for permanent canonical integration."""
import json,re
from .identity import NodeLookup,find_node,add_alias
from .selection import BalancedSelector,_mix_sockets

def _restore_selector(nodes,prefix):
    frames=[n for n in nodes if n.bl_idname=='NodeFrame' and n.get('dr.selection.prefix')==prefix]
    if len(frames)!=1:return None
    frame=frames[0]
    if frame.get('dr.selection.schema')==3:
        from .general import restore_general
        return restore_general(nodes,prefix,frame)
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
    selector.sources={int(z):resolve(v) for z,v in bindings['sources'].items()}
    selector.factors={int(z):resolve(v) for z,v in bindings['factors'].items()}
    selector.default=resolve(bindings['default']);selector.results={};selector.raw_results={};selector.weights={};selector.generated=[]
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
    def get(self,key,default=None):
        found=find_node(self.nodes,key)
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


def lookup(nodes):return SelectionLookup(nodes)
