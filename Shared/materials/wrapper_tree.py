"""Tree levels of a collapsed outfit material.

graph_blocks leaves the material tree with the shader, the output and one ``DR Outfit`` group node;
every setup node lives in that node's tree. Tools that look nodes up by name go through here.
No addon imports: shader_pick and shader_control_ops load this file standalone in tests.
"""
from __future__ import annotations

WRAPPER_NODE = "DR Outfit"      # graph_blocks: the one setup group node of a collapsed material
WRAPPER_TAG = "dr.wrapper"


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
    """The tree that holds a material's setup nodes: the wrapper tree when collapsed, else ``tree``."""
    node = wrapper_node(tree)
    return node.node_tree if node is not None else tree


def graph_trees(mat_or_tree) -> list:
    """Every per-material tree level: the material tree, then its wrapper tree when collapsed."""
    tree = getattr(mat_or_tree, "node_tree", mat_or_tree)
    if tree is None or not hasattr(tree, "nodes"):
        return []
    node = wrapper_node(tree)
    return [tree, node.node_tree] if node is not None else [tree]


def find_node(mat_or_tree, name: str):
    """Node ``name`` on any per-material level (material tree first, then the wrapper tree)."""
    for tree in graph_trees(mat_or_tree):
        node = tree.nodes.get(name)
        if node is not None:
            return node
    return None


def own_wrapper(mat):
    """Give ``mat`` its own wrapper tree when it shares one (a copied material), so a value or
    mute written for this material does not change the other. Returns the setup tree."""
    tree = getattr(mat, "node_tree", None)
    node = wrapper_node(tree)
    if node is None:
        return tree
    if node.node_tree.users > 1:
        node.node_tree = node.node_tree.copy()
    return node.node_tree
