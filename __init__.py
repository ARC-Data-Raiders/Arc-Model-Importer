"""
Arc Asset Importer - Modular Refactor
Import Arc Raiders models by selecting an outfit folder.
"""

bl_info = {
    "name": "Arc Asset Importer v2.3.12",
    "author": "Silarious / Naryun & Zebulon Core Functions",
    "version": (2, 3, 12),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Arc Asset Importer",
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
    # Per-addon folder (Arc-Asset-Importer vs DataRaiders-MapImporter) so the
    # two installs never share bytecode.
    folder = os.path.basename(_addon_dir) or "Arc-Asset-Importer"
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

from .Outfit_Accessories import addon_line
from .Outfit_Accessories import properties
from .Shared import operators
from . import sidebar
from .Shared import shader_control_ops
from .Shared import importing
from .Shared import materials
from .Shared import textures
from .Shared import utils
from .Shared import pak_extract
from .Maps import map_placement  # noqa: F401 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â map-importer lane; bridge helpers shared with outfits



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
    # Clean slate so Preferences ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ Install over an enabled copy cannot double-register.
    try:
        unregister()
    except Exception:
        pass

    properties.register()
    from .Weapons import weapon_ui
    weapon_ui.register()
    from .Arc import arc_ui
    arc_ui.register()
    pak_extract.register_defaults()
    if not addon_line.is_map_importer_line():
        from .Outfit_Accessories import character_fit
        character_fit.register()
        from .Outfit_Accessories import appearance_queue
        appearance_queue.register()

    try:
        from .Outfit_Accessories import appearance as _appearance

        _appearance.register_previews()
    except Exception as e:
        print(f"Arc Asset Importer: appearance previews register failed: {e}")

    from .Maps import staged_import
    for cls in staged_import.classes:
        _safe_register_class(cls)

    for cls in operators.classes:
        _safe_register_class(cls)

    for cls in pak_extract.classes:
        _safe_register_class(cls)

    shader_control_ops.register()
    from .Shared import texture_sets
    texture_sets.register()
    for cls in sidebar.classes:
        _safe_register_class(cls)
    try:
        from .Shared.materials.native import shader_parity
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
        f"Arc Asset Importer registered successfully "
        f"(line={addon_line.normalize_line(addon_line.ADDON_LINE)})."
    )


def unregister():
    """Unregister all modules and properties (safe if partially registered)."""
    try:
        from .Shared.materials.native import shader_parity
        shader_parity.unregister()
    except Exception:
        pass
    from .Outfit_Accessories import reference_overlay
    reference_overlay.unregister()
    from .Arc import arc_ui
    arc_ui.unregister()
    from .Weapons import weapon_ui
    weapon_ui.unregister()
    from .Outfit_Accessories import appearance_queue
    appearance_queue.unregister()
    pak_extract.unregister_defaults()
    from .Outfit_Accessories import character_fit
    character_fit.unregister()
    try:
        if addon_line.is_map_importer_line():
            map_placement.unregister_instancer_material_focus()
    except Exception:
        pass

    try:
        from .Maps.map_tools import fmodel_bridge as bridge

        bridge.stop_listener()
    except Exception:
        pass


    for cls in reversed(sidebar.classes):
        _safe_unregister_class(cls)
    try:
        shader_control_ops.unregister()
    except Exception:
        pass
    try:
        from .Shared import texture_sets
        texture_sets.unregister()
    except Exception:
        pass

    for cls in reversed(pak_extract.classes):
        _safe_unregister_class(cls)

    for cls in reversed(operators.classes):
        _safe_unregister_class(cls)

    from .Maps import staged_import
    for cls in reversed(staged_import.classes):
        _safe_unregister_class(cls)

    try:
        properties.unregister()
    except Exception:
        pass

    try:
        from .Outfit_Accessories import appearance as _appearance

        _appearance.unregister_previews()
    except Exception:
        pass

    print("Arc Asset Importer unregistered successfully.")


if __name__ == "__main__":
    register()
