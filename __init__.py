"""
Arc Raiders PSK Importer - Modular Refactor
Import Arc Raiders models by selecting an outfit folder.
"""

bl_info = {
    "name": "Arc Model Importer v2.22.15",
    "author": "Silarious (Ai Vibe Code)/ Naryun & Zebulon Core Functions",
    "version": (2, 22, 15),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Arc Model Importer",
    "description": "Import Arc Raiders outfits, weapons, animations, and models by selecting content folders.",
    "category": "Import-Export",
}

import os
import sys

# Redirect bytecode out of the addon tree BEFORE submodule imports so zip/install
# folders stay free of growing __pycache__ (process-global; see PEP 304).
_addon_dir = os.path.dirname(__file__)


def _configure_pycache_prefix():
    if sys.pycache_prefix:
        return
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or os.path.expanduser("~")
    # Per-addon folder (DataRaiders-Outfits vs DataRaiders-MapImporter) so the
    # two installs never share bytecode.
    folder = os.path.basename(_addon_dir) or "DataRaiders-Outfits"
    prefix = os.path.join(local, folder, "pycache")
    try:
        os.makedirs(prefix, exist_ok=True)
        sys.pycache_prefix = prefix
    except OSError as exc:
        print(f"Arc Raiders: could not set pycache_prefix ({prefix}): {exc}")


_configure_pycache_prefix()

import bpy

# Add current directory to path for imports
addon_dir = _addon_dir
if addon_dir not in sys.path:
    sys.path.insert(0, addon_dir)

from . import addon_line
from . import properties
from . import operators
from . import ui
from . import importing
from . import materials
from . import textures
from . import utils
from . import pak_extract
from . import map_placement  # noqa: F401 Ã¢â‚¬â€ map-importer lane; bridge helpers shared with outfits



def _safe_register_class(cls):
    """Register ``cls``, or replace an existing RNA type of the same name.

    Handles Preferences reinstall / reload where unregister was skipped or a
    stale flat copy of the addon left ``ArcOutfitsPSKEntry`` (etc.) already registered.
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


def register():
    """Register all modules and properties (idempotent on reinstall/reload)."""
    # Clean slate so Preferences Ã¢â€ â€™ Install over an enabled copy cannot double-register.
    try:
        unregister()
    except Exception:
        pass

    properties.register()
    from . import weapon_ui
    weapon_ui.register()
    from . import arc_ui
    arc_ui.register()
    pak_extract.register_defaults()
    if not addon_line.is_map_importer_line():
        from . import character_fit
        character_fit.register()
        from . import appearance_queue
        appearance_queue.register()

    try:
        from . import appearance as _appearance

        _appearance.register_previews()
    except Exception as e:
        print(f"Arc Raiders PSK Importer: appearance previews register failed: {e}")

    for cls in operators.classes:
        _safe_register_class(cls)

    for cls in pak_extract.classes:
        _safe_register_class(cls)

    for cls in ui.classes:
        _safe_register_class(cls)
    try:
        from .material_runtime import shader_parity
        shader_parity.register()
    except Exception as e:
        print(f"Arc Raiders shader parity: register failed: {e}")

    # Runtime resources and listeners are initialized on demand by their UI actions.

    # Instancer material focus is map Fast-import only.
    if addon_line.is_map_importer_line():
        try:
            map_placement.register_instancer_material_focus()
        except Exception as e:
            print(f"Arc Raiders instancer material focus: register failed: {e}")

    print(
        f"Arc Raiders PSK Importer registered successfully "
        f"(line={addon_line.normalize_line(addon_line.ADDON_LINE)})."
    )


def unregister():
    """Unregister all modules and properties (safe if partially registered)."""
    try:
        from .material_runtime import shader_parity
        shader_parity.unregister()
    except Exception:
        pass
    from . import reference_overlay
    reference_overlay.unregister()
    from . import arc_ui
    arc_ui.unregister()
    from . import weapon_ui
    weapon_ui.unregister()
    from . import appearance_queue
    appearance_queue.unregister()
    pak_extract.unregister_defaults()
    from . import character_fit
    character_fit.unregister()
    try:
        if addon_line.is_map_importer_line():
            map_placement.unregister_instancer_material_focus()
    except Exception:
        pass

    try:
        from .map_tools import fmodel_bridge as bridge

        bridge.stop_listener()
    except Exception:
        pass


    for cls in reversed(ui.classes):
        _safe_unregister_class(cls)

    for cls in reversed(pak_extract.classes):
        _safe_unregister_class(cls)

    for cls in reversed(operators.classes):
        _safe_unregister_class(cls)

    try:
        properties.unregister()
    except Exception:
        pass

    try:
        from . import appearance as _appearance

        _appearance.unregister_previews()
    except Exception:
        pass

    print("Arc Raiders PSK Importer unregistered successfully.")


if __name__ == "__main__":
    register()
