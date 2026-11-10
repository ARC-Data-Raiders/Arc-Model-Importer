"""Helpers for synthetic addon packages after the one-folder-per-section layout.

Addon modules live in section folders (``Shared/utils.py``, ``Outfit_Accessories/appearance.py``).
Fake packages built by tests must therefore register ``PKG.<Section>`` parent packages and use
``PKG.<Section>.<module>`` names so the addon's relative imports resolve.
"""
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("Outfit_Accessories", "Weapons", "Arc", "Animations", "Niagara", "Lighting", "Maps",
            "Script_Helpers", "Shared")

SECTION_OF = {}
for _folder in SECTIONS:
    for _entry in (ROOT / _folder).iterdir():
        if _entry.name.startswith(("_", ".")):
            continue
        if _entry.suffix == ".py":
            SECTION_OF[_entry.stem] = _folder
        elif _entry.is_dir() and (_entry / "__init__.py").exists() or _entry.name == "map_tools":
            SECTION_OF[_entry.name] = _folder


def q(name):
    """Return the section-qualified dotted name for a moved top-level module name."""
    head = name.split(".", 1)[0]
    folder = SECTION_OF.get(head)
    return f"{folder}.{name}" if folder else name


def path_of(name):
    """Return the source path for a (possibly unqualified) module name."""
    rel = q(name).replace(".", "/")
    p = ROOT / (rel + ".py")
    return p if p.exists() else ROOT / rel / "__init__.py"


def add_parents(modules, prefix):
    """Create missing ``prefix.<Section>`` packages in ``modules`` and link children as attributes."""
    root = modules.get(prefix)
    for name in sorted(list(modules), key=lambda n: n.count(".")):
        if not name.startswith(prefix + "."):
            continue
        parts = name.split(".")
        for depth in range(len(parts) - 1, 0, -1):
            parent = ".".join(parts[:depth])
            if parent not in modules:
                pkg = ModuleType(parent)
                rel = "/".join(parts[1:depth])
                pkg.__path__ = [str(ROOT / rel)] if rel else []
                modules[parent] = pkg
    for name, module in list(modules.items()):
        if name.startswith(prefix + ".") and module is not None:
            parent, _, leaf = name.rpartition(".")
            if parent in modules:
                setattr(modules[parent], leaf, module)
    return root
