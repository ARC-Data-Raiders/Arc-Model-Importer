"""Node editor sidebar tab "Arc": graph tools for imported materials.

Draw code reads only the open tree and the active object; it never writes data or
touches files. Every change runs in an operator.
"""

import bpy

from .materials import graph_layout

CATEGORY = "Arc"
_ZONE_ITEMS = []


def _edit_tree(context):
    space = getattr(context, "space_data", None)
    if space is None or getattr(space, "tree_type", "") != "ShaderNodeTree":
        return None
    return getattr(space, "edit_tree", None)


def _root_tree(context):
    space = getattr(context, "space_data", None)
    return getattr(space, "node_tree", None) if space is not None else None


def breadcrumb(path):
    """Readable group path, for example "Material > Look > Zone 1"."""
    names = []
    for p in path or ():
        tree = getattr(p, "node_tree", None)
        if tree is not None:
            names.append(graph_layout._clean_name(tree.name) or tree.name)
    return " > ".join(names)


def zone_nodes(tree):
    """Group nodes in tree that hold a zone: tagged, or named after a zone."""
    out = []
    if tree is None:
        return out
    for n in tree.nodes:
        sub = getattr(n, "node_tree", None) if n.bl_idname.endswith("Group") else None
        if sub is None:
            continue
        label = n.label or graph_layout._clean_name(sub.name)
        if n.get(graph_layout.TAG_ZONE) or "zone" in label.lower():
            out.append((n, label))
    out.sort(key=lambda item: graph_layout._natural(item[1]))
    return out


def look_node(tree):
    """Top-level look group: the group node with user-facing inputs and no consumer of its own tree type."""
    if tree is None:
        return None
    best = None
    for n in tree.nodes:
        sub = getattr(n, "node_tree", None) if n.bl_idname.endswith("Group") else None
        if sub is None:
            continue
        name = sub.name
        if name.endswith("Look") or name.startswith(("Arc ", "DR.Look")) or sub.get("independent_owned_graph"):
            if best is None or len(n.inputs) > len(best.inputs):
                best = n
    return best


def control_rows(material):
    """(label, socket) rows for the Controls box, from the shared schema when present."""
    tree = getattr(material, "node_tree", None)
    look = look_node(tree)
    rows = []
    schema = None
    try:
        from .materials import user_controls
        schema = getattr(user_controls, "schema", None)
    except Exception:
        schema = None
    entries = None
    if schema is not None:
        try:
            entries = schema(material)
        except Exception:
            entries = None
    socks = {}
    if tree is not None:
        for n in tree.nodes:
            if n.bl_idname.endswith("Group"):
                for s in n.inputs:
                    if not s.is_linked and hasattr(s, "default_value"):
                        socks.setdefault(s.identifier, s)
                        socks.setdefault(s.name, s)
    if entries:
        for e in entries:
            s = socks.get(str(e.get("id", ""))) or socks.get(str(e.get("label", "")))
            if s is not None:
                rows.append((str(e.get("label") or s.name), s))
        return rows
    if look is not None:
        for s in look.inputs:
            if not s.is_linked and hasattr(s, "default_value") and not s.hide:
                rows.append((s.name, s))
    return rows


class ARC_OT_node_tidy_graph(bpy.types.Operator):
    bl_idname = "arc.node_tidy_graph"
    bl_label = "Tidy Graph"
    bl_description = "Arrange this graph and its groups into labelled frames, left to right"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _edit_tree(context) is not None

    def invoke(self, context, event):
        tree = _edit_tree(context)
        if graph_layout.is_user_edited(tree):
            return context.window_manager.invoke_confirm(
                self, event, title="Tidy Graph", message="This graph was changed by hand. Rearrange it anyway?",
                confirm_text="Tidy Graph")
        return self.execute(context)

    def execute(self, context):
        tree = _edit_tree(context)
        stats = graph_layout.finalize_tree(tree, tree.get("arc_family"), recursive=True, force=True)
        self.report({"INFO"}, "Tidied %d nodes in %d frames" % (stats["nodes"], stats["frames"]))
        return {"FINISHED"}


class ARC_OT_node_frame_by_zone(bpy.types.Operator):
    bl_idname = "arc.node_frame_by_zone"
    bl_label = "Frame by Zone"
    bl_description = "Put each zone of this graph in its own frame"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _edit_tree(context) is not None

    def execute(self, context):
        tree = _edit_tree(context)
        stats = graph_layout.finalize_tree(tree, tree.get("arc_family"), recursive=False, force=True)
        zones = len({n.get(graph_layout.TAG_ZONE) for n in tree.nodes if n.get(graph_layout.TAG_ZONE)})
        zones += len(zone_nodes(tree))
        if not zones:
            self.report({"INFO"}, "No zones in this graph; framed by stage instead")
        else:
            self.report({"INFO"}, "Framed %d zones in %d frames" % (zones, stats["frames"]))
        return {"FINISHED"}


def _zone_items(self, context):
    _ZONE_ITEMS.clear()
    for i, (n, label) in enumerate(zone_nodes(_edit_tree(context))):
        _ZONE_ITEMS.append((n.name, label, "Open %s" % label, i))
    if not _ZONE_ITEMS:
        _ZONE_ITEMS.append(("NONE", "No Zones", "This graph has no zone groups", 0))
    return _ZONE_ITEMS


class ARC_OT_node_show_zone(bpy.types.Operator):
    bl_idname = "arc.node_show_zone"
    bl_label = "Show Zone..."
    bl_description = "Open a zone group and frame it in the view"
    bl_property = "zone"

    zone: bpy.props.EnumProperty(name="Zone", items=_zone_items)

    @classmethod
    def poll(cls, context):
        return _edit_tree(context) is not None

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        tree = _edit_tree(context)
        node = tree.nodes.get(self.zone) if self.zone != "NONE" else None
        if node is None or getattr(node, "node_tree", None) is None:
            self.report({"WARNING"}, "This graph has no zone groups")
            return {"CANCELLED"}
        context.space_data.path.append(node.node_tree, node=node)
        try:
            bpy.ops.node.view_all()
        except RuntimeError:
            pass
        return {"FINISHED"}


class ARC_OT_node_remove_unused(bpy.types.Operator):
    bl_idname = "arc.node_remove_unused"
    bl_label = "Remove Unused Nodes"
    bl_description = "Delete nodes in this graph that feed no output"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _edit_tree(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title="Remove Unused Nodes", message="Delete every node that feeds no output?",
            confirm_text="Remove")

    def execute(self, context):
        count = graph_layout.remove_unused(_edit_tree(context))
        self.report({"INFO"}, "Removed %d unused nodes" % count if count else "No unused nodes")
        return {"FINISHED"}


class ARC_OT_node_select_output(bpy.types.Operator):
    bl_idname = "arc.node_select_output"
    bl_label = "Select Material Output"
    bl_description = "Go to the top of the material and select its active output"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _root_tree(context) is not None and _edit_tree(context) is not None

    def execute(self, context):
        root = _root_tree(context)
        space = context.space_data
        if space.edit_tree is not root:
            space.path.start(root)
        outs = [n for n in root.nodes if n.type == "OUTPUT_MATERIAL"]
        out = next((n for n in outs if getattr(n, "is_active_output", False)), outs[0] if outs else None)
        if out is None:
            self.report({"WARNING"}, "This material has no output node")
            return {"CANCELLED"}
        for n in root.nodes:
            n.select = n is out
        root.nodes.active = out
        try:
            bpy.ops.node.view_selected()
        except RuntimeError:
            pass
        return {"FINISHED"}


class ARC_PT_node_tools(bpy.types.Panel):
    bl_idname = "ARC_PT_node_tools"
    bl_label = "Material Graph"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = CATEGORY

    @classmethod
    def poll(cls, context):
        return _edit_tree(context) is not None

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        mat = getattr(obj, "active_material", None) if obj is not None else None
        col = layout.column(align=True)
        col.label(text="Asset: %s" % (obj.name if obj is not None else "None"), icon="OBJECT_DATA")
        col.label(text="Material: %s" % (mat.name if mat is not None else "None"), icon="MATERIAL")
        crumb = breadcrumb(getattr(context.space_data, "path", ()))
        if crumb:
            col.label(text=crumb, icon="NODETREE")
        if mat is not None:
            rows = control_rows(mat)
            if rows:
                box = layout.box()
                box.label(text="Controls")
                for label, sock in rows:
                    box.prop(sock, "default_value", text=label)
        col = layout.column(align=True)
        col.operator(ARC_OT_node_tidy_graph.bl_idname, icon="ALIGN_JUSTIFY")
        col.operator(ARC_OT_node_frame_by_zone.bl_idname, icon="SELECT_SET")
        col.operator_menu_enum(ARC_OT_node_show_zone.bl_idname, "zone", text="Show Zone...", icon="ZOOM_SELECTED")
        col.operator(ARC_OT_node_remove_unused.bl_idname, icon="TRASH")
        col.operator(ARC_OT_node_select_output.bl_idname, icon="RESTRICT_SELECT_OFF")


classes = (
    ARC_OT_node_tidy_graph,
    ARC_OT_node_frame_by_zone,
    ARC_OT_node_show_zone,
    ARC_OT_node_remove_unused,
    ARC_OT_node_select_output,
    ARC_PT_node_tools,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
