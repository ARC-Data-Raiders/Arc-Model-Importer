"""Rebuild compatibility group nodes from the original procedural registry.

Orthogonal to Mask Debug — kept in its own module so mask_debug.py stays lean.
"""
from __future__ import annotations

import os

import bpy

from ..Shared import utils

_HOTSWAP_GROUP_NAMES = (
    "ArcTexturer",
    "CurvatureID_Override",
    "Visor",
    "ColorMask_XYZ",
    "NormalFlipper",
    "Decal Data",
    "Decal LayerMask Gate",
    "WeaponTexturer",
)


def _canonical_group_name(name: str) -> str | None:
    raw = (name or "").strip()
    if not raw:
        return None
    compact = raw.replace(" ", "")
    for known in _HOTSWAP_GROUP_NAMES:
        if compact == known.replace(" ", "") or compact.startswith(
            known.replace(" ", "") + "."
        ):
            return known
        if raw == known or raw.startswith(known + "."):
            return known
    if compact.startswith("NormalFlipper"):
        return "NormalFlipper"
    return None


def force_reload_group_from_blend(name: str):
    """Legacy public API: rebuild an original registry definition, never append."""
    from ..Shared.materials.native.shared_helpers import ensure_group
    return ensure_group(name,bpy,force=True)


def snapshot_group_links(node):
    """Capture input/output links and input defaults by socket name."""
    in_links = []
    in_defaults = {}
    for sock in node.inputs:
        if sock.is_linked:
            for link in list(sock.links):
                in_links.append((link.from_node, link.from_socket.name, sock.name))
        else:
            try:
                val = sock.default_value
                if hasattr(val, "__len__") and not isinstance(val, (str, bytes)):
                    in_defaults[sock.name] = tuple(val)
                else:
                    in_defaults[sock.name] = val
            except Exception:
                pass

    out_links = []
    for sock in node.outputs:
        if sock.is_linked:
            for link in list(sock.links):
                out_links.append((sock.name, link.to_node, link.to_socket.name))
    return in_links, out_links, in_defaults


def restore_group_links(node, tree_links, in_links, out_links, in_defaults) -> tuple[int, int]:
    """Reconnect by socket name. Returns (relinked, dropped)."""
    relinked = dropped = 0
    for sock in list(node.inputs) + list(node.outputs):
        while sock.is_linked:
            tree_links.remove(sock.links[0])

    for from_node, from_name, to_name in in_links:
        to_sock = node.inputs.get(to_name)
        from_sock = from_node.outputs.get(from_name) if from_node is not None else None
        if to_sock is None or from_sock is None:
            dropped += 1
            continue
        try:
            tree_links.new(from_sock, to_sock)
            relinked += 1
        except Exception:
            dropped += 1

    for from_name, to_node, to_name in out_links:
        from_sock = node.outputs.get(from_name)
        to_sock = to_node.inputs.get(to_name) if to_node is not None else None
        if from_sock is None or to_sock is None:
            dropped += 1
            continue
        try:
            tree_links.new(from_sock, to_sock)
            relinked += 1
        except Exception:
            dropped += 1

    for name, val in in_defaults.items():
        sock = node.inputs.get(name)
        if sock is None or sock.is_linked:
            continue
        try:
            if isinstance(val, (list, tuple)):
                sock.default_value = val
            else:
                sock.default_value = val
        except Exception:
            pass
    return relinked, dropped


def update_group_node(node, force_reload: bool = True) -> tuple[bool, str]:
    """Rebuild one original group definition; preserve links/defaults by contract."""
    if getattr(node, "type", "") != "GROUP":
        return False, "not a group"
    tree = getattr(node, "node_tree", None)
    old_name = getattr(tree, "name", "") if tree else ""
    canon = (tree.get('dr.legacy_group_key') if tree else None) or _canonical_group_name(old_name)
    if not canon:
        return False, f"unsupported group {old_name!r}"

    parent_tree = node.id_data
    if parent_tree is None:
        return False, "no parent tree"
    links = parent_tree.links

    in_links, out_links, in_defaults = snapshot_group_links(node)

    if force_reload:
        new_tree = force_reload_group_from_blend(canon)
    else:
        from ..Shared.materials.native.shared_helpers import ensure_group
        new_tree=ensure_group(canon,bpy)

    if new_tree is None:
        return False, f"could not load {canon}"

    node.node_tree = new_tree
    relinked, dropped = restore_group_links(node, links, in_links, out_links, in_defaults)
    return True, f"{canon}: relinked={relinked} dropped={dropped}"


def collect_group_nodes_to_update(context) -> list:
    """Prefer Shader Editor selection; else group nodes on selected objects' materials."""
    nodes = []
    space = getattr(context, "space_data", None)
    if space is not None and getattr(space, "type", "") == "NODE_EDITOR":
        tree = getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)
        if tree is not None:
            for n in tree.nodes:
                if n.select and getattr(n, "type", "") == "GROUP":
                    nodes.append(n)
            if nodes:
                return nodes

    for obj in context.selected_objects:
        for slot in getattr(obj, "material_slots", []) or []:
            mat = slot.material
            if mat is None or not mat.use_nodes or mat.node_tree is None:
                continue
            for n in mat.node_tree.nodes:
                if getattr(n, "type", "") == "GROUP" and (getattr(n, 'node_tree', None) is not None and n.node_tree.get('dr.legacy_group_key') or _canonical_group_name(getattr(getattr(n,'node_tree',None),'name','') or '')):
                    nodes.append(n)
    return nodes


def update_selected_group_nodes(context) -> tuple[int, int, list]:
    """Returns (updated, skipped, messages)."""
    updated = skipped = 0
    messages = []
    seen = set()
    for node in collect_group_nodes_to_update(context):
        key = (node.id_data.as_pointer(), node.as_pointer())
        if key in seen:
            continue
        seen.add(key)
        ok, msg = update_group_node(node, force_reload=True)
        messages.append(msg)
        if ok:
            updated += 1
        else:
            skipped += 1
    return updated, skipped, messages
