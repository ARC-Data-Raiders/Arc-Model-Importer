"""Build portable animation and Niagara browse indexes from retained typed exports."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import logging
import re
import sys
import types
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TYPES = {"AnimSequence", "AnimMontage", "AnimComposite", "NiagaraSystem"}
TYPE_TOKEN = re.compile(rb'"(?:Type|ExportType)"\s*:\s*"(?:AnimSequence|AnimMontage|AnimComposite|NiagaraSystem)"')
ANIMATION_COLUMNS = (
    "key", "stem", "kind", "package", "game_path", "folder", "source_json",
    "source_sha256", "skeleton", "length", "notify_count", "has_props", "has_fx",
)
EFFECT_COLUMNS = (
    "key", "stem", "package", "game_path", "folder", "source_json",
    "source_sha256", "has_lut", "size",
)


def load_animation_parser():
    name = "_browse_catalog_producer"
    package = types.ModuleType(name)
    package.__path__ = [str(ROOT)]
    sys.modules[name] = package
    for section in ("Shared", "Animations"):
        sub = types.ModuleType(name + "." + section)
        sub.__path__ = [str(ROOT / section)]
        sys.modules[sub.__name__] = sub
        setattr(package, section, sub)
    utils = types.ModuleType(name + ".Shared.utils")
    sys.modules[name + ".Shared"].utils = utils
    utils.get_logger = lambda: logging.getLogger(name)
    utils.get_pioneer_root = lambda: ""
    sys.modules[utils.__name__] = utils
    spec = importlib.util.spec_from_file_location(name + ".Animations.animation_catalog", ROOT / "Animations" / "animation_catalog.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def game_path_from_source(relative: str) -> str:
    path = relative.rsplit(".", 1)[0]
    if path.startswith("Engine/Content/"):
        return "/Engine/" + path[len("Engine/Content/"):]
    if path.startswith("RemappedPlugins/") and "/Content/" in path:
        plugin, content = path[len("RemappedPlugins/"):].split("/Content/", 1)
        return "/" + plugin.rsplit("/", 1)[-1] + "/" + content
    if "/Content/" in path:
        return "/Game/" + path.split("/Content/", 1)[1]
    if path.startswith("Content/"):
        return "/Game/" + path[len("Content/"):]
    if path.startswith("Pioneer/"):
        return "/Game/" + path
    raise ValueError("Source root must preserve a Content or Pioneer package tree: " + relative)


def typed_sources(source_root: Path):
    def read_source(path):
        raw = path.read_bytes()
        return (path, raw) if TYPE_TOKEN.search(raw) else (path, None)

    paths = iter(sorted(source_root.rglob("*.json")))
    with ThreadPoolExecutor(max_workers=8) as pool:
        while batch := list(islice(paths, 32)):
            yield from pool.map(read_source, batch)


def build_catalog(source_root: Path, build: str) -> dict:
    parser = load_animation_parser()
    animations = []
    effects = []
    provenance = hashlib.sha256()
    scanned = 0
    typed_files = 0
    for path, raw in typed_sources(source_root):
        scanned += 1
        if scanned % 10000 == 0:
            print(f"Scanned {scanned} JSON files; retained {typed_files} typed sources", file=sys.stderr, flush=True)
        if raw is None:
            continue
        document = json.loads(raw)
        exports = parser._exports_from_dump(document)
        selected = [export for export in exports
                    if parser._type_leaf(str(export.get("Type") or export.get("ExportType") or "")) in TYPES]
        if not selected:
            continue
        relative = path.relative_to(source_root).as_posix()
        game_path = game_path_from_source(relative)
        source_hash = hashlib.sha256(raw).hexdigest()
        provenance.update((relative + "\0" + source_hash + "\n").encode("utf-8"))
        typed_files += 1
        for export in selected:
            kind = parser._type_leaf(str(export.get("Type") or export.get("ExportType") or ""))
            stem = str(export.get("Name") or path.stem)
            if stem != path.stem:
                continue
            package = export.get("Package") or game_path
            if not isinstance(package, str) or not package.startswith("/"):
                raise ValueError("Invalid typed package reference: " + relative)
            row = {
                "key": stem, "stem": stem, "package": package,
                "game_path": package, "folder": parser._folder_from_package(package),
                "source_json": relative, "source_sha256": source_hash,
            }
            if kind == "NiagaraSystem":
                row.update(has_lut=bool(re.search(rb'"ShaderLUT"\s*:', raw)), size=len(raw))
                effects.append(row)
            else:
                metadata = parser.parse_notify_document(document)
                notifies = metadata["notifies"]
                row.update(
                    kind=kind, skeleton=metadata["skeleton"], length=metadata["length"],
                    notify_count=len(notifies),
                    has_props=any(notify.get("kind") == "prop" for notify in notifies),
                    has_fx=any(notify.get("kind") == "fx" for notify in notifies),
                )
                animations.append(row)
    for entries in (animations, effects):
        counts = Counter(entry["stem"].lower() for entry in entries)
        for entry in entries:
            if counts[entry["stem"].lower()] > 1:
                entry["key"] = entry["game_path"]
        entries.sort(key=lambda entry: (entry["folder"].lower(), entry["stem"].lower(), entry["key"]))
        if len({entry["key"].lower() for entry in entries}) != len(entries):
            raise ValueError("Duplicate package identities in retained source")
    if not animations or not effects:
        raise ValueError("Source must contain typed animation clips and Niagara systems")
    return {
        "schema": 1, "producer": "scripts/build_browse_catalogs.py", "source_build": build,
        "source_generation": provenance.hexdigest(), "source_files": typed_files,
        "scanned_json_files": scanned,
        "animations": {
            "columns": ANIMATION_COLUMNS,
            "rows": [[entry[column] for column in ANIMATION_COLUMNS] for entry in animations],
            "stats": {
                "total": len(animations), "typed_exports": len(animations),
                "fmdex_tagged": 0, "fmdex_named": 0,
                "psa_files": 0, "cache_dir": "", "fmdex_loaded": False,
                "fmdex_entries": 0, "fmdex_error": "",
            },
        },
        "effects": {
            "columns": EFFECT_COLUMNS,
            "rows": [[entry[column] for column in EFFECT_COLUMNS] for entry in effects],
            "stats": {
                "total": len(effects), "with_lut": sum(entry["has_lut"] for entry in effects),
                "without_lut": sum(not entry["has_lut"] for entry in effects), "catalog_root": "",
            },
        },
    }


def write_catalog(document: dict, output: Path) -> int:
    payload = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(payload, compresslevel=9, mtime=0)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(compressed)
    return len(compressed)


def verify_catalog(document: dict, source_root: Path) -> dict:
    parser = load_animation_parser()
    checked = 0
    for kind in ("animations", "effects"):
        catalog = document[kind]
        for row in catalog["rows"]:
            entry = dict(zip(catalog["columns"], row))
            source = source_root / entry["source_json"]
            resolved = parser.fmdex.resolve_catalog_file(entry, ".json", str(source_root))
            if not resolved or Path(resolved) != source:
                raise ValueError("Catalog reference does not resolve to its source: " + entry["key"])
            raw = source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != entry["source_sha256"]:
                raise ValueError("Catalog source changed: " + entry["source_json"])
            exports = parser._exports_from_dump(json.loads(raw))
            expected = "NiagaraSystem" if kind == "effects" else entry["kind"]
            if not any(export.get("Name") == entry["stem"] and
                       parser._type_leaf(str(export.get("Type") or export.get("ExportType") or "")) == expected
                       and (export.get("Package") or entry["game_path"]) == entry["game_path"]
                       for export in exports):
                raise ValueError("Catalog package identity differs from source: " + entry["key"])
            checked += 1
    return {"verified_references": checked, "source_generation": document["source_generation"]}


def main():
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--source-root", required=True, type=Path)
    argument_parser.add_argument("--build", required=True)
    argument_parser.add_argument("--output", type=Path, default=ROOT / "assets" / "browse_catalogs.json.gz")
    argument_parser.add_argument("--verify", action="store_true")
    args = argument_parser.parse_args()
    if args.verify:
        with gzip.open(args.output, "rt", encoding="utf-8") as source:
            document = json.load(source)
        if document["source_build"] != args.build:
            raise ValueError("Catalog source build differs from requested build")
        print(json.dumps(verify_catalog(document, args.source_root), indent=2))
        return
    document = build_catalog(args.source_root, args.build)
    size = write_catalog(document, args.output)
    print(json.dumps({"source_build": document["source_build"],
                      "source_generation": document["source_generation"],
                      "scanned_json_files": document["scanned_json_files"],
                      "source_files": document["source_files"],
                      "animations": document["animations"]["stats"],
                      "effects": document["effects"]["stats"], "gzip_bytes": size}, indent=2))


if __name__ == "__main__":
    main()
