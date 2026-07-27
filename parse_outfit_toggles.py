#!/usr/bin/env python3
"""
parse_outfit_toggles.py - Extract all toggle options for a given Arc Raiders outfit.

Scans an outfit folder's DA_UIMetaData_Outfit_*.json files and groups each
toggle (UICharacterVisualSkinSlotMetaDataItem) with its option buttons
(UICharacterVisualSkinPartMetaDataItem), printing the toggle name and every
option's display name.

Usage:
    python parse_outfit_toggles.py <outfit_folder_path>
    python parse_outfit_toggles.py --all

Examples:
    python parse_outfit_toggles.py ".../Items/Characters/Skins/Outfit/Wrapper"
    python parse_outfit_toggles.py --all
"""

import argparse
import json
import os
import sys


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        exports = data.get("Exports")
        if isinstance(exports, list):
            return exports
        return [data]
    return []


def parse_toggles(folder_path):
    """
    Parse all toggle options from an outfit folder.

    Returns a list of dicts:
      [
        {
          "toggle_name": "Gloves",
          "toggle_key": "ID_OUTFITTOGGLE_GLOVES",
          "options": [
            {"name": "Off", "key": "ID_CHARACTERCUSTOMIZATIONOPTIONS_OFF"},
            {"name": "On",  "key": "ID_OUTFITTOGGLE_ON"},
          ]
        },
        ...
      ]
    """
    toggles = {}
    options = {}

    for fname in sorted(os.listdir(folder_path)):
        if not fname.lower().endswith(".json"):
            continue
        if not fname.lower().startswith("da_uimetadata_outfit_"):
            continue

        fpath = os.path.join(folder_path, fname)
        try:
            entries = load_json(fpath)
        except Exception:
            continue

        for entry in entries:
            entry_type = entry.get("Type", "")
            props = entry.get("Properties", {})
            dn = props.get("DisplayName", {})
            if not dn:
                continue

            display_name = dn.get("SourceString") or dn.get("LocalizedString") or ""
            key = dn.get("Key", "")

            if entry_type == "UICharacterVisualSkinSlotMetaDataItem":
                toggle_key_base = fname.rsplit(".json", 1)[0]
                toggles[toggle_key_base] = {
                    "toggle_name": display_name,
                    "toggle_key": key,
                    "options": [],
                }

            elif entry_type == "UICharacterVisualSkinPartMetaDataItem":
                options[fname] = {
                    "name": display_name,
                    "key": key,
                }

    # Group options under their parent toggle.
    # A toggle file is named DA_UIMetaData_Outfit_<Outfit>_<Setting>.json.
    # Its options are DA_UIMetaData_Outfit_<Outfit>_<Setting>_<Option>.json.
    # So for each option file, find the toggle whose base name is a prefix.
    result = []
    for toggle_file_base, toggle_data in toggles.items():
        for opt_file, opt_data in options.items():
            opt_base = opt_file.rsplit(".json", 1)[0]
            if opt_base.startswith(toggle_file_base + "_"):
                toggle_data["options"].append(opt_data)

        # Sort options: On/Off first (if present), then alphabetical
        def opt_sort_key(o):
            name_lower = o["name"].lower()
            if name_lower == "on":
                return (1, "")
            if name_lower == "off":
                return (0, "")
            return (2, name_lower)

        toggle_data["options"].sort(key=opt_sort_key)
        result.append(toggle_data)

    # Sort toggles by name
    result.sort(key=lambda t: t["toggle_name"].lower())
    return result


def print_toggles(toggles, folder_name="", out=sys.stdout):
    if folder_name:
        print(f"=== {folder_name} ===", file=out)
    if not toggles:
        print("  (no toggles found)", file=out)
        return
    for t in toggles:
        opts = "/".join(o["name"] for o in t["options"])
        print(f"  {t['toggle_name']} [{opts}]", file=out)
    print(file=out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", default="",
                    help="Path to an outfit folder (e.g. .../Outfit/Wrapper)")
    ap.add_argument("--all", action="store_true",
                    help="Parse every outfit folder under the parent Outfit/ directory")
    ap.add_argument("--json", action="store_true",
                    help="Output as JSON instead of human-readable text")
    ap.add_argument("-o", "--out", default="",
                    help="Output file path (default: stdout)")
    args = ap.parse_args()

    if not args.folder and not args.all:
        ap.error("Provide an outfit folder path, or use --all")

    out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout

    try:
        if args.all:
            root = args.folder or os.path.dirname(os.path.abspath(__file__))
            if not os.path.isdir(root):
                print(f"Error: '{root}' is not a directory", file=sys.stderr)
                sys.exit(1)

            outfit_folders = {}
            for dirpath, dirnames, filenames in os.walk(root):
                if any(f.lower().startswith("da_uimetadata_outfit_") and f.lower().endswith(".json")
                       for f in filenames):
                    name = os.path.basename(dirpath)
                    outfit_folders[name] = dirpath

            all_toggles = {}
            for name in sorted(outfit_folders):
                toggles = parse_toggles(outfit_folders[name])
                if toggles:
                    all_toggles[name] = toggles

            if args.json:
                print(json.dumps(all_toggles, indent=2), file=out)
            else:
                for name, toggles in all_toggles.items():
                    print_toggles(toggles, name, out)
                print(f"Total: {len(all_toggles)} outfit(s) with toggles", file=out)
        else:
            folder = args.folder
            if not os.path.isdir(folder):
                print(f"Error: '{folder}' is not a directory", file=sys.stderr)
                sys.exit(1)

            folder_name = os.path.basename(folder.rstrip("/\\"))
            toggles = parse_toggles(folder)

            if args.json:
                print(json.dumps({folder_name: toggles}, indent=2), file=out)
            else:
                print_toggles(toggles, folder_name, out)
                if not toggles:
                    print("Tip: make sure the folder contains DA_UIMetaData_Outfit_*.json files", file=out)
    finally:
        if args.out:
            out.close()


if __name__ == "__main__":
    main()
