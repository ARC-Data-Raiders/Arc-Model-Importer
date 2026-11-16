"""Public addon interface for installed shader parity plans."""
RUNTIME = "parity-1"


def planned(mi_path):
    from . import plans
    return plans.find(mi_path) is not None


def setup_if_planned(mat, mi_path, obj=None):
    from . import plans, build, props
    try:
        plan = plans.find(mi_path)
        if plan is None:
            return False
        build.setup_material(mat, plan, obj)
        import bpy
        props.ensure_scene(bpy.context.scene)
        if obj is not None:
            props.ensure_object(obj)
        props.activate()
        return True
    except Exception as error:
        from .... import utils
        utils.get_logger().exception("Shader parity setup failed: %s", error)
        mat["dr_parity_diagnostic"] = str(error)
        mat.use_nodes = True
        mat.node_tree.nodes.clear()
        return False


def apply_uemodel_slots(obj, uemodel_path):
    """Give every UEModel slot whose MI has an installed plan its shared parity material.

    Import routes such as the part-folder MI path never dispatch per-slot materials;
    this runs after them so planned slots are exact regardless of the route taken.
    """
    import os
    from .....Outfit_Accessories import pak_catalog; from .... import pak_extract, uemodel_import
    from ... import dispatch
    if obj is None or obj.type != "MESH" or not str(uemodel_path or "").casefold().endswith(".uemodel"):
        return 0
    try:
        _name, _version, lods = uemodel_import.parse_uemodel(uemodel_path, scale=1.0)
    except Exception:
        return 0
    if not lods or not lods[0].materials or len(lods[0].materials) != len(obj.material_slots):
        return 0
    count = 0
    for index, (_slot, path, _first, _faces) in enumerate(lods[0].materials):
        if not path:
            continue
        try:
            canonical = pak_catalog.canonical_path(path)
        except ValueError:
            continue
        mi_json = os.path.join(pak_extract.cache_path(), *canonical.split("/")) + ".json"
        if not os.path.isfile(mi_json) or not planned(mi_json):
            continue
        mat = dispatch._get_or_build_shared_mi_material(canonical.rsplit("/", 1)[-1], mi_json, uemodel_path, obj=obj)
        if mat is None or mat.get("arc_mi_family") != "shader_parity":
            continue
        slot = obj.material_slots[index]
        if slot.link == "OBJECT":
            slot.material = mat
        else:
            obj.data.materials[index] = mat
        count += 1
    if count:
        from . import props
        props.ensure_object(obj)
        props.activate()
    return count


def material_current(mat, mi_path):
    from . import plans
    plan = plans.find(mi_path)
    return plan is not None and mat.get("arc_mi_family") == "shader_parity" and mat.get("dr_parity_plan_sha256") == plan.stamp and mat.get("dr_parity_runtime") == RUNTIME


def register():
    import bpy
    from . import props, ui
    _unregister_class(ui.ARC_PT_shader_parity)
    bpy.types.Scene.dr_parity_root = bpy.props.StringProperty(name="Shader Parity Root", subtype="DIR_PATH", default="")
    for cls in ui.classes:
        _unregister_class(cls)
        bpy.utils.register_class(cls)
    props.register()


def _unregister_class(cls):
    # A reinstalled module brings new class objects; remove the registered one by name too.
    import bpy
    for candidate in (cls, getattr(bpy.types, cls.__name__, None)):
        if candidate is None:
            continue
        try:
            bpy.utils.unregister_class(candidate)
        except (ValueError, RuntimeError):
            pass


def unregister():
    import bpy
    from . import props, ui
    props.unregister()
    _unregister_class(ui.ARC_PT_shader_parity)
    for cls in reversed(ui.classes):
        _unregister_class(cls)
    if hasattr(bpy.types.Scene, "dr_parity_root"):
        del bpy.types.Scene.dr_parity_root
