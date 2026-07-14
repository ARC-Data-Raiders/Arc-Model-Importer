"""
Arc Raiders PSK Importer - Modular Refactor
Import Arc Raiders models by selecting an outfit folder.
"""

bl_info = {
    "name": "PSKImporter_SIL_AI",
    "author": "Naryun / Zebulon / (Silarious Tests)",
    "version": (2, 1, 5),
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

def register():
    """Register all modules and properties"""
    # Register properties first
    properties.register()
    
    # Register operators
    for cls in operators.classes:
        bpy.utils.register_class(cls)
    
    # Register UI panels
    for cls in ui.classes:
        bpy.utils.register_class(cls)
    
    # Ensure PSK addon is installed
    utils.ensure_psk_addon()
    
    print("Arc Raiders PSK Importer registered successfully.")

def unregister():
    """Unregister all modules and properties"""
    # Unregister UI panels
    for cls in reversed(ui.classes):
        bpy.utils.unregister_class(cls)
    
    # Unregister operators
    for cls in reversed(operators.classes):
        bpy.utils.unregister_class(cls)
    
    # Unregister properties
    properties.unregister()
    
    print("Arc Raiders PSK Importer unregistered successfully.")

if __name__ == "__main__":
    register()