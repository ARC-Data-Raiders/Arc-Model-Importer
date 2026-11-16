"""Python API for scripts: read and change an imported asset's look controls.

    from <addon package>.Shared import api
    api.get_controls(obj)                  # {material name: [control rows]}
    api.set_control(obj, "Wear", 0.5)      # every material of the asset that has "Wear"
    api.reset_look(obj)                    # back to the imported look

*obj* is any object of an imported asset (the asset root is found from it). Control rows are
``user_controls.schema`` rows: id, label, panel, type ("FLOAT" or "COLOR"), min, max, default,
imported, value. See docs/python-api.md.
"""
from .materials import user_controls


def _root(obj):
    from . import shader_control_ops as sco
    if obj is None:
        raise ValueError("obj is None")
    return sco.asset_root(obj)


def _materials(obj):
    from . import look_panel
    return look_panel.asset_materials(_root(obj))


def get_controls(obj):
    """{material name: [schema rows]} for every material of *obj*'s asset that has controls."""
    out = {}
    for mat in _materials(obj):
        rows = user_controls.schema(mat)
        if rows:
            out[mat.name] = rows
    return out


def set_control(obj, name, value, material=None):
    """Set control *name* (id or label) on the asset, or on one *material* name. Returns the
    number of sockets written; raises KeyError when no material has the control."""
    mats = _materials(obj)
    if material is not None:
        mats = [m for m in mats if m.name == material]
    count = 0
    for mat in mats:
        cid = name
        for row in user_controls.schema(mat):
            if name in (row["id"], row["label"], row["socket"]):
                cid = row["id"]
                break
        count += user_controls.schema_set(mat, cid, value)
    if not count:
        raise KeyError("No control named %r on this asset" % (name,))
    return count


def reset_look(obj):
    """Every control of the asset back to its imported value. Returns sockets written."""
    from . import look_panel
    return look_panel.reset_look(_root(obj))
