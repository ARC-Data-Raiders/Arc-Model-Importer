"""Permanent public selection facade; no module/global swapping.

Native physical equations remain retained and credited.
Both proven one-hot and general fractional-mask composition use original DR flow.
"""
from dataclasses import dataclass
import json
from .selection import build_balanced
from .general import build_general
from .lookup import lookup

@dataclass(frozen=True)
class SelectionStats:
    balanced:int
    fallbacks:int
    reasons:tuple

class PrimarySelectionCandidate:
    schema=2
    def stats(self,nodes):
        frames=[n for n in nodes if n.bl_idname=='NodeFrame' and n.get('dr.selection.schema')==self.schema]
        tree=nodes.id_data
        reasons=tuple(json.loads(tree.get('dr.primary_selection.fallbacks','[]')))
        return SelectionStats(len(frames),len(reasons),reasons)
    def reset_stats(self,nodes):
        nodes.id_data['dr.primary_selection.fallbacks']='[]'
    def lookup(self,nodes):return lookup(nodes)
    def mux(self,retained,nodes,links,zone_idx,zone_socks,**kwargs):
        before={n.as_pointer() for n in nodes}
        try:result,detail=build_balanced(nodes,links,zone_idx,zone_socks,**kwargs)
        except Exception:
            for node in reversed(list(nodes)):
                if node.as_pointer() not in before:nodes.remove(node)
            raise
        if result is not None:
            nodes.id_data['dr.primary_selection.schema']=self.schema
            return result
        tree=nodes.id_data;reasons=list(json.loads(tree.get('dr.primary_selection.fallbacks','[]')))
        reasons.append({'prefix':kwargs.get('prefix'),'reason':detail})
        tree['dr.primary_selection.fallbacks']=json.dumps(reasons)
        try:return build_general(nodes,links,zone_idx,zone_socks,**kwargs)
        except Exception:
            for node in reversed(list(nodes)):
                if node.as_pointer() not in before:nodes.remove(node)
            raise

candidate=PrimarySelectionCandidate()
