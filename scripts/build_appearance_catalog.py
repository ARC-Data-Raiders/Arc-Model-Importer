"""Build bounded appearance browse data from explicitly supplied retained exports.

Example: py -3.14 -B scripts/build_appearance_catalog.py --source BUILD=CONTENT_PIONEER
Add --previews BUILD=CONTENT_PIONEER to prepare available non-outfit icons.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TABLES = {
    "attachment": "ST_Character_BackpackAttachments",
    "charm": "ST_Customization_Charms",
    "raider_tool": "ST_Customization_RaiderToolSkins",
    "backpack": "ST_Customization_Backpacks",
}
PREFIXES = ("ID_CHARACTER_BACKPACKATTACHMENTS_", "ID_CHARACTER_BACKPACKATTACHMENT_", "ID_CUSTOMIZATION_CHARM_", "ID_RAIDERTOOLCOSMETIC_", "ID_CUSTOMIZATION_BACKPACK_")
SEGMENTS = {"head": "Face", "hair": "Hair", "backpack": "BackpackContainer", "attachment": "BackpackAttachment", "charm": "BackpackCharm", "raider_tool": "RaiderTool"}


def token(value):
    value = re.sub(r"[^A-Za-z0-9]+", "", value).upper()
    return value[:-1] if len(value) > 2 and value.endswith("A") and value[-2].isdigit() else value


def exports(data):
    if isinstance(data, dict):
        data = data.get("Exports") or [data]
    return [entry for entry in data if isinstance(entry, dict)]


def source_argument(value):
    build, separator, directory = value.partition("=")
    if not separator or not build or not Path(directory).is_dir():
        raise argparse.ArgumentTypeError("Expected BUILD=existing Content/Pioneer directory")
    return {"build": build, "root": str(Path(directory).resolve()), "records": {}}


def read_source(source, path):
    raw = path.read_bytes()
    source["records"][path.relative_to(source["root"]).as_posix()] = hashlib.sha256(raw).hexdigest()
    return json.loads(raw.decode("utf-8-sig"))


def one_insert_apart(left, right):
    if abs(len(left) - len(right)) != 1:
        return False
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    index = 0
    while index < len(shorter) and shorter[index] == longer[index]:
        index += 1
    return shorter[index:] == longer[index + 1:]


def choose_icon(row, candidates):
    stems = []
    for value in (row["icon_stem"], row["folder"]):
        normalized = token(value)
        stems.extend([normalized, re.sub(r"\d+$", "", normalized)])
    exact = [candidate for candidate in candidates if candidate[0] in stems]
    if exact:
        return exact[0][1]
    near = [candidate[1] for candidate in candidates if any(len(stem) >= 5 and abs(len(candidate[0]) - len(stem)) <= 2 and (candidate[0].startswith(stem) or stem.startswith(candidate[0]) or one_insert_apart(candidate[0], stem)) for stem in stems)]
    return near[0] if len(near) == 1 else None


def build_catalog(csv_path, sources, preview_sources, output):
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    manifest = {"schema": 1, "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(), "flavour_tables": {}, "icons": {}, "backpack_colorways": {}}
    for kind, table_name in TABLES.items():
        table = {}
        for source in sources:
            path = Path(source["root"]) / "UI" / "Localization" / (table_name + ".json")
            if not path.is_file():
                continue
            for entry in exports(read_source(source, path)):
                for key, value in ((entry.get("StringTable") or {}).get("KeysToEntries") or {}).items():
                    if not value or key.upper().endswith("_ITEMCATEGORY"):
                        continue
                    for prefix in PREFIXES:
                        if key.upper().startswith(prefix):
                            key = key[len(prefix):]
                            break
                    table.setdefault(token(key), str(value).strip())
        manifest["flavour_tables"][kind] = table
    for row in rows:
        if row["kind"] != "backpack":
            continue
        item = row["icon_stem"] or row["folder"]
        marker = "DA_OI_BackpackContainer_" + item + "_Color_"
        variants = {}
        for source in sources:
            directory = Path(source["root"]) / "Items/Characters/Skins/BackpackContainer" / item
            for path in sorted(directory.glob(marker + "*.json")):
                if path.stem.endswith("_Persistence"):
                    continue
                entries = exports(read_source(source, path))
                entry = next((entry for entry in entries if entry.get("Type") == "CharacterVisualPartOnlineItemDataAsset" and entry.get("Name") == path.stem), None)
                if not entry:
                    continue
                package = str(entry.get("Package") or "")
                if not package.startswith("/Game/Pioneer/"):
                    continue
                color = path.stem[len(marker):]
                label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", color).replace("_", " ")
                metadata = path.with_name("DA_UIMetaData_" + path.stem[len("DA_OI_"):] + ".json")
                if metadata.is_file():
                    for meta in exports(read_source(source, metadata)):
                        display = (meta.get("Properties") or {}).get("DisplayName") or {}
                        label = display.get("LocalizedString") or display.get("SourceString") or label
                variants.setdefault(color, {"id": color, "label": label, "package": "PioneerGame/Content/" + package[len("/Game/"):]})
        manifest["backpack_colorways"][item] = sorted(variants.values(), key=lambda value: value["label"].casefold())
    icon_candidates = {}
    for source_index, source in enumerate(preview_sources):
        for segment in set(SEGMENTS.values()) | {"FacialHair"}:
            directory = Path(source["root"]) / "Items/Characters/Skins" / segment
            prefix = "T_UI_Icon_" + segment + "_"
            candidates = []
            for path in sorted(directory.glob("*/" + prefix + "*.png")):
                candidates.append((token(path.stem[len(prefix):]), path))
            icon_candidates[source_index, segment] = candidates
    missing_icons = []
    for row in rows:
        segment = "FacialHair" if row["segment"].casefold() == "facialhairs" else SEGMENTS.get(row["kind"])
        if not segment:
            continue
        selected = None
        for source_index, source in enumerate(preview_sources):
            selected = choose_icon(row, icon_candidates[source_index, segment])
            if selected:
                raw = selected.read_bytes()
                source["records"][selected.relative_to(source["root"]).as_posix()] = hashlib.sha256(raw).hexdigest()
                break
        if not selected:
            missing_icons.append(row["id"])
            continue
        from PIL import Image
        image = Image.open(selected).convert("RGBA")
        image.thumbnail((256, 256), Image.Resampling.LANCZOS)
        filename = hashlib.sha256(raw).hexdigest()[:20] + ".png"
        destination = output / "appearance_previews" / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, optimize=True)
        manifest["icons"][row["id"]] = filename
    manifest["sources"] = [{"build": source["build"], "root": source["root"], "files": source["records"]} for source in sources + preview_sources]
    manifest["coverage"] = {"choices": len(rows), "icons": len(manifest["icons"]), "missing_icon_ids": missing_icons, "backpack_colorways": sum(len(variants) for variants in manifest["backpack_colorways"].values()), "backpacks_without_colorways": [item for item, variants in manifest["backpack_colorways"].items() if not variants]}
    output.mkdir(parents=True, exist_ok=True)
    (output / "appearance_browse.json").write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return manifest["coverage"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=source_argument, action="append", required=True)
    parser.add_argument("--previews", type=source_argument, action="append", default=[])
    parser.add_argument("--csv", type=Path, default=ROOT / "appearance_reference.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "assets")
    args = parser.parse_args()
    coverage = build_catalog(args.csv, args.source, args.previews, args.output)
    print(json.dumps({key: len(value) if key == "missing_icon_ids" else value for key, value in coverage.items()}, indent=2))


if __name__ == "__main__":
    main()
