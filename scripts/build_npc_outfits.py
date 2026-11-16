"""Audit trader outfit mesh references against an installed game's exact index."""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from Outfit_Accessories.outfit_catalog import canonical_package, unique_paths
from Outfit_Accessories.pak_catalog import read_index, mesh_paths

NPC_SOURCES = {
    "ID_APOLLO_NAME": "Apollo",
    "ID_CELESTE_NAME": "Celeste",
    "ID_JUANITO_NAME": "Juanito",
    "ID_LANCE_NAME": "Lance",
    "ID_SHANI_NAME": "Shani",
}


def authored_meshes(document):
    entries = document.get("Exports", [document]) if isinstance(document, dict) else document
    paths = []
    for entry in entries:
        if entry.get("Type") != "SkeletalMeshComponent":
            continue
        reference = entry.get("Properties", {}).get("SkeletalMesh", {})
        if not isinstance(reference, dict):
            continue
        path = canonical_package(reference.get("ObjectPath", ""))
        if "/characters/assets/" in path.casefold() or "/characters/heroes/" in path.casefold():
            paths.append(path)
    return unique_paths(paths)


def build_reference(json_root, index, reference):
    with reference.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    indexed = {path.casefold(): path for path in mesh_paths(index)}
    for row in rows:
        source = NPC_SOURCES.get(row["ST"])
        if source is None:
            continue
        filename = json_root / source / ("BP_Trader_" + source + ".json")
        paths = authored_meshes(json.loads(filename.read_text(encoding="utf-8-sig")))
        missing = [path for path in paths if path.casefold() not in indexed]
        if not paths or missing:
            raise ValueError(source + ": missing exact authored meshes: " + "; ".join(missing))
        row["Pak Mesh Paths"] = "; ".join(indexed[path.casefold()] for path in paths)
        row["Pak Build"] = index["build"]
        row["Pak Status"] = "available"
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-root", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--reference", type=Path, default=ROOT / "Data/outfit_reference.csv")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = build_reference(args.json_root, read_index(str(args.index)), args.reference)
    if args.check:
        if args.reference.read_text(encoding="utf-8-sig") != expected:
            raise SystemExit("NPC outfit catalog differs from authored references")
        print("NPC_CATALOG_OK")
    else:
        sys.stdout.write(expected)


if __name__ == "__main__":
    main()
