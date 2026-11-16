"""Icon browser: registration, collect_items filtering, apply_selection (needs bpy).

Run inside Blender: blender --background --factory-startup --python tests/test_icon_browser.py
"""
import importlib
import sys
import unittest
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None

ROOT = Path(__file__).resolve().parents[1]


def run(addon_name, *, require_kinds=("backpack", "attachment", "charm", "raider_tool")):
    ib = importlib.import_module(addon_name + ".Outfit_Accessories.icon_browser")
    ctx = bpy.context
    for op in ("icon_browser", "icon_browser_pick", "add_gear_part"):
        assert hasattr(bpy.types, "ARC_OUTFITS_OT_" + op), op
    rna = bpy.ops.arc_outfits.icon_browser.get_rna_type()
    kinds = [i.identifier for i in rna.properties["kind"].enum_items]
    assert kinds == [k[0] for k in ib.KIND_ITEMS], kinds
    counts = {}
    for kind in kinds:
        items = ib.collect_items(ctx, kind)
        counts[kind] = len(items)
        assert all(ident not in ("", "NONE") for ident, _label in items), kind
        if kind in require_kinds:
            assert items, "no items for " + kind
        if not items:
            continue
        # Memo returns the same list for the same query.
        assert ib.collect_items(ctx, kind) is items
        ident, label = items[len(items) // 2]
        needle = label[1:5] if len(label) > 5 else label
        filtered = ib.collect_items(ctx, kind, needle.upper())
        assert (ident, label) in filtered and len(filtered) <= len(items), (kind, needle)
        assert ib.collect_items(ctx, kind, "zz-no-such-item-zz") == []
        previous = ib.current_selection(ctx, kind)
        assert ib.apply_selection(ctx, kind, ident), (kind, ident)
        assert ib.current_selection(ctx, kind) == ident
        assert not ib.apply_selection(ctx, kind, "zz-no-such-item-zz")
        ib.icon_path(ctx, kind, ident)  # must not raise
        if previous:
            ib.apply_selection(ctx, kind, previous)
    assert not ib.apply_selection(ctx, "nope", "x")
    return counts


@unittest.skipIf(bpy is None, "needs Blender")
class IconBrowserTest(unittest.TestCase):
    def test_icon_browser(self):
        sys.path.insert(0, str(ROOT.parent))
        addon = importlib.import_module(ROOT.name)
        if not hasattr(bpy.types, "ARC_OUTFITS_OT_icon_browser"):
            addon.register()
        print("ICON_BROWSER", run(ROOT.name, require_kinds=()))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], exit=False)
