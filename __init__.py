"""
Arc Raiders PSK Importer - Modular Refactor
Import Arc Raiders models by selecting an outfit folder.
"""

bl_info = {
    "name": "Arc Raiders Model Importer",
    "author": "Silarious (Ai Vibe Code)/ Naryun & Zebulon Core Functions",
    "version": (2, 18, 13),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Arc Raiders",
    "description": "Import Arc Raiders models by selecting an outfit folder.",
    "category": "Import-Export",
}

import bpy
import os
import sys

# Add current directory to path for imports
addon_dir = os.path.dirname(__file__)
if addon_dir not in sys.path:
    sys.path.insert(0, addon_dir)

from . import properties
from . import operators
from . import ui
from . import importing
from . import materials
from . import textures
from . import utils
from . import map_placement  # noqa: F401 — FModel-first placement lane (empties/meshes), not outfit paths

_last_search_value = [""]


def _safe_register_class(cls):
    """Register ``cls``, or replace an existing RNA type of the same name.

    Handles Preferences reinstall / reload where unregister was skipped or a
    stale flat copy of the addon left ``ArcPSKEntry`` (etc.) already registered.
    """
    try:
        bpy.utils.register_class(cls)
        return
    except (ValueError, RuntimeError) as exc:
        msg = str(exc).lower()
        if "already registered" not in msg:
            raise
    # Drop the live RNA type (may be a different Python class object).
    existing = getattr(bpy.types, cls.__name__, None)
    for candidate in (cls, existing):
        if candidate is None:
            continue
        try:
            bpy.utils.unregister_class(candidate)
        except (ValueError, RuntimeError):
            pass
    bpy.utils.register_class(cls)


def _safe_unregister_class(cls):
    existing = getattr(bpy.types, getattr(cls, "__name__", ""), None)
    for candidate in (cls, existing):
        if candidate is None:
            continue
        try:
            bpy.utils.unregister_class(candidate)
        except (ValueError, RuntimeError):
            pass


def _search_poll():
    try:
        current = bpy.context.scene.arc_outfit_search
        if current != _last_search_value[0]:
            _last_search_value[0] = current
            for area in bpy.context.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:
        pass
    return 0.05


def register():
    """Register all modules and properties (idempotent on reinstall/reload)."""
    # Clean slate so Preferences → Install over an enabled copy cannot double-register.
    try:
        unregister()
    except Exception:
        pass

    properties.register()

    for cls in operators.classes:
        _safe_register_class(cls)

    for cls in ui.classes:
        _safe_register_class(cls)

    utils.ensure_psk_addon()

    try:
        if not bpy.app.timers.is_registered(_search_poll):
            bpy.app.timers.register(_search_poll, first_interval=0.5, persistent=True)
    except Exception:
        bpy.app.timers.register(_search_poll, first_interval=0.5, persistent=True)
    bpy.app.timers.register(_auto_start_listener, first_interval=0.25, persistent=False)

    try:
        map_placement.register_instancer_material_focus()
    except Exception as e:
        print(f"Arc Raiders instancer material focus: register failed: {e}")

    print("Arc Raiders PSK Importer registered successfully.")


def _auto_start_listener():
    """Start the FModel TCP listener after register (scene props must exist)."""
    try:
        from .map_tools import fmodel_bridge as bridge

        scene = bpy.context.scene
        if not getattr(scene, "arc_auto_listen", True):
            return None
        if bridge.is_listening():
            return None
        port = int(getattr(scene, "arc_placement_listen_port", bridge.DEFAULT_PORT) or bridge.DEFAULT_PORT)
        msg = bridge.start_listener(port=port)
        print(f"Arc Raiders FModel bridge: {msg}")
    except Exception as e:
        print(f"Arc Raiders FModel bridge auto-start failed: {e}")
    return None


def unregister():
    """Unregister all modules and properties (safe if partially registered)."""
    try:
        map_placement.unregister_instancer_material_focus()
    except Exception:
        pass

    try:
        from .map_tools import fmodel_bridge as bridge

        bridge.stop_listener()
    except Exception:
        pass

    try:
        if bpy.app.timers.is_registered(_search_poll):
            bpy.app.timers.unregister(_search_poll)
    except Exception:
        pass

    for cls in reversed(ui.classes):
        _safe_unregister_class(cls)

    for cls in reversed(operators.classes):
        _safe_unregister_class(cls)

    try:
        properties.unregister()
    except Exception:
        pass

    print("Arc Raiders PSK Importer unregistered successfully.")


if __name__ == "__main__":
    register()
