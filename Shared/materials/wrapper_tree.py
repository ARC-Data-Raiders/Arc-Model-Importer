"""Tree levels of a collapsed outfit material.

graph_blocks leaves the material tree with the shader, the output and one ``DR Outfit`` group node.
That node's tree (the wrapper) holds one group node per build stage; each stage tree holds that
stage's setup nodes. Older files keep every setup node in the wrapper tree itself.
Tools that look nodes up by name go through here.
No addon imports: shader_pick and shader_control_ops load this file standalone in tests.
"""
from __future__ import annotations

WRAPPER_NODE = "DR Outfit"      # graph_blocks: the one setup group node of a collapsed material
WRAPPER_TAG = "dr.wrapper"
STAGE_TAG = "dr.stage_tree"     # graph_blocks: a per-material stage tree inside the wrapper


def wrapper_node(tree):
    """The ``DR Outfit`` group node of a collapsed material tree, or None."""
    if tree is None:
        return None
    node = tree.nodes.get(WRAPPER_NODE)
    if (node is not None and getattr(node, "bl_idname", "") == "ShaderNodeGroup"
            and node.node_tree is not None and node.node_tree.get(WRAPPER_TAG)):
        return node
    return None


def graph_tree(tree):
    """The wrapper tree when collapsed, else ``tree``. Setup nodes may sit one level deeper
    (``stage_trees``); use ``graph_nodes`` or ``find_node`` to reach them."""
    node = wrapper_node(tree)
    return node.node_tree if node is not None else tree


def stage_nodes(wrapper) -> list:
    """Group nodes of ``wrapper`` that hold a per-material stage tree."""
    out = []
    for node in wrapper.nodes:
        if getattr(node, "bl_idname", "") != "ShaderNodeGroup":
            continue
        sub = getattr(node, "node_tree", None)
        if sub is not None and sub.get(STAGE_TAG):
            out.append(node)
    return out


def stage_trees(wrapper) -> list:
    """Per-material stage trees inside a wrapper tree (empty for a flat wrapper)."""
    out = []
    for node in stage_nodes(wrapper):
        if node.node_tree not in out:
            out.append(node.node_tree)
    return out


def graph_trees(mat_or_tree) -> list:
    """Every per-material tree level: the material tree, its wrapper tree, then its stage trees."""
    tree = getattr(mat_or_tree, "node_tree", mat_or_tree)
    if tree is None or not hasattr(tree, "nodes"):
        return []
    node = wrapper_node(tree)
    if node is None:
        return [tree]
    return [tree, node.node_tree, *stage_trees(node.node_tree)]


def find_node(mat_or_tree, name: str):
    """Node ``name`` on any per-material level (material tree, wrapper tree, stage trees)."""
    for tree in graph_trees(mat_or_tree):
        node = tree.nodes.get(name)
        if node is not None:
            return node
    return None


class NodesView:
    """Read view over the nodes of several trees, shaped like ``tree.nodes`` for lookups.

    ``new`` creates in the first tree and ``id_data`` is the first tree, so code written for
    one tree keeps working when the setup nodes are spread over stage trees."""

    def __init__(self, trees):
        self.trees = list(trees)

    @property
    def id_data(self):
        return self.trees[0]

    def get(self, name, default=None):
        for tree in self.trees:
            node = tree.nodes.get(name)
            if node is not None:
                return node
        return default

    def __getitem__(self, name):
        node = self.get(name)
        if node is None:
            raise KeyError(name)
        return node

    def __contains__(self, name):
        return self.get(name) is not None

    def __iter__(self):
        for tree in self.trees:
            yield from tree.nodes

    def __len__(self):
        return sum(len(tree.nodes) for tree in self.trees)

    def keys(self):
        return [node.name for node in self]

    def values(self):
        return list(self)

    def items(self):
        return [(node.name, node) for node in self]

    def new(self, type):    # noqa: A002  (bpy name)
        return self.trees[0].nodes.new(type)

    def remove(self, node):
        node.id_data.nodes.remove(node)


def graph_nodes(tree):
    """Setup nodes of a material tree: ``tree.nodes`` when flat, the wrapper's nodes when it has
    no stage trees, else one view over the wrapper and its stage trees."""
    inner = graph_tree(tree)
    if inner is tree:
        return tree.nodes
    stages = stage_trees(inner)
    return NodesView([inner, *stages]) if stages else inner.nodes


def own_wrapper(mat):
    """Give ``mat`` its own wrapper and stage trees when it shares them (a copied material), so a
    value or mute written for this material does not change the other. Returns the wrapper tree."""
    tree = getattr(mat, "node_tree", None)
    node = wrapper_node(tree)
    if node is None:
        return tree
    if node.node_tree.users > 1:
        node.node_tree = node.node_tree.copy()
    done = {}
    for snode in stage_nodes(node.node_tree):
        sub = snode.node_tree
        key = sub.as_pointer()
        if key in done:
            snode.node_tree = done[key]
        elif sub.users > 1:
            snode.node_tree = done[key] = sub.copy()
    return node.node_tree
