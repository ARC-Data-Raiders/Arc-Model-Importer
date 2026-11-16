# Python API: look controls

`Shared/api.py` reads and changes the colors and wear of an imported asset from a script.
Pass any object of the asset; the functions find its root.

```python
import importlib
api = importlib.import_module(__package__ + ".Shared.api")   # or the installed add-on package name

rows = api.get_controls(bpy.context.object)       # {material name: [row, ...]}
api.set_control(bpy.context.object, "Wear", 0.5)  # id, label or socket name
api.reset_look(bpy.context.object)                # back to the imported look
```

| Function | Returns |
| --- | --- |
| `get_controls(obj)` | `{material name: [row]}` for materials that have controls |
| `set_control(obj, name, value, material=None)` | Sockets written; `KeyError` when no material has the control |
| `reset_look(obj)` | Sockets written |

Each row has `id`, `label`, `panel`, `type` (`FLOAT` or `COLOR`), `min`, `max`, `default`
(group default), `imported` (value at import), `value` (current), `node` and `socket`.

The imported values are stored on each look group node as `node["arc_import_defaults"]`
(JSON) the first time the asset is shown in the sidebar.

Look presets saved from the UI are JSON files in Blender's user config folder under
`arc_look_presets/` (format in `Shared/look_presets.py`).
