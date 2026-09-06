"""
arc_shader_join.py
Material ↔ ShaderCode join for Arc Raiders / Pioneer exports.

Builds a manifest that links every FModel material JSON (which has a ResourceHash
in LoadedShaderMap) to the corresponding decompressed DXBC/DXIL files that
FModel's "Extract IoStore Shader Code" already wrote under ShaderCode/.

Usage:
    python arc_shader_join.py \
        --export-root  <Arc_Raiders_Current>   \
        --out          <join_manifest.json>    \
        [--material-glob "PioneerGame/**/*.json"]
        [--asset-filter  "MI_*"]

Output JSON (one entry per material × resource):
    {
        "material": "PioneerGame/.../MI_Foo.json",
        "export":   "MaterialInstance",
        "resource_hash": "AAABBB...",
        "shader_archive": "ShaderArchive-PioneerGame_Chunk9-PCD3D_SM5-PCD3D_SM5",
        "shader_dir": "ShaderCode/<archive>/<hash>/",
        "dxbc_files": ["0092_SF_Pixel_....dxbc", ...],
        "dxbc_count": 12,
        "numeric_params": [...],
        "ub_fields": [...],
        "has_preshader": true,
        "corpus_entry": {...}   # from ShaderCorpus/manifest.json if present
    }
"""

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iter_materials(export_root: Path, glob: str, asset_filter: str | None) -> list[Path]:
    paths = sorted(export_root.glob(glob))
    if asset_filter:
        paths = [p for p in paths if Path(p.name).match(asset_filter) or p.stem.startswith(asset_filter.rstrip("*"))]
    return paths


def _load_material_lmr(json_path: Path) -> list[dict]:
    """Return list of LoadedShaderMap dicts from all exports in a material JSON."""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    exports = data.get("Exports") if isinstance(data, dict) else data
    if not isinstance(exports, list):
        return []
    out = []
    for export in exports:
        lmr = export.get("LoadedMaterialResources") or []
        for resource in lmr:
            sm = resource.get("LoadedShaderMap") or {}
            rh = sm.get("ResourceHash")
            if not rh:
                continue
            out.append({
                "export_type": export.get("Type") or export.get("Class", ""),
                "export_name": export.get("Name", ""),
                "resource_hash": rh,
                "shader_map_id": sm.get("ShaderMapId") or {},
                "shader_platform": sm.get("ShaderPlatform", ""),
                "numeric_params": (
                    sm.get("Content", {})
                    .get("MaterialCompilationOutput", {})
                    .get("UniformExpressionSet", {})
                    .get("UniformNumericParameters") or []
                ),
                "ub_fields": (
                    sm.get("Content", {})
                    .get("MaterialCompilationOutput", {})
                    .get("UniformExpressionSet", {})
                    .get("UniformPreshaderFields") or []
                ),
                "has_preshader": bool(
                    (sm.get("Content", {})
                     .get("MaterialCompilationOutput", {})
                     .get("UniformExpressionSet", {})
                     .get("UniformPreshaderData") or {}).get("Data")
                ),
            })
    return out


def _find_shader_dirs(shader_code_root: Path, resource_hash: str) -> list[Path]:
    """Search ShaderCode/<archive>/<hash>/ for the given hash."""
    return [p for p in shader_code_root.glob(f"*/{resource_hash}") if p.is_dir()]


def _corpus_entries(corpus_manifest: dict | None, resource_hash: str) -> list[dict]:
    if not corpus_manifest:
        return []
    entries = corpus_manifest.get("entries") or corpus_manifest
    if isinstance(entries, dict):
        return [v for k, v in entries.items() if v.get("map_hash") == resource_hash]
    elif isinstance(entries, list):
        return [e for e in entries if e.get("map_hash") == resource_hash]
    return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_join(export_root: Path, out_path: Path, glob: str, asset_filter: str | None) -> None:
    shader_code_root = export_root / "ShaderCode"
    corpus_dir = export_root / "ShaderCorpus"

    # Load corpus manifest if available (maps hash → asm/meta paths)
    corpus_manifest = None
    corpus_manifest_path = corpus_dir / "manifest.json"
    if corpus_manifest_path.exists():
        try:
            corpus_manifest = json.loads(corpus_manifest_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            pass

    material_paths = _iter_materials(export_root, glob, asset_filter)
    print(f"Scanning {len(material_paths)} material JSONs...", flush=True)

    results = []
    matched = 0
    unmatched = 0
    empty_lmr = 0

    for mat_path in material_paths:
        lmr_entries = _load_material_lmr(mat_path)
        if not lmr_entries:
            empty_lmr += 1
            continue

        rel_mat = str(mat_path.relative_to(export_root))

        for entry in lmr_entries:
            rh = entry["resource_hash"]
            shader_dirs = _find_shader_dirs(shader_code_root, rh) if shader_code_root.exists() else []

            if shader_dirs:
                matched += 1
            else:
                unmatched += 1

            dxbc_files: list[str] = []
            archive_name = ""
            shader_dir_rel = ""
            if shader_dirs:
                sd = shader_dirs[0]
                archive_name = sd.parent.name
                shader_dir_rel = str(sd.relative_to(export_root))
                dxbc_files = sorted(f.name for f in sd.iterdir() if f.suffix in (".dxbc", ".dxil"))

            corpus = _corpus_entries(corpus_manifest, rh)

            results.append({
                "material": rel_mat,
                "export_type": entry["export_type"],
                "export_name": entry["export_name"],
                "resource_hash": rh,
                "shader_platform": entry["shader_platform"],
                "quality_level": entry["shader_map_id"].get("QualityLevel", ""),
                "feature_level": entry["shader_map_id"].get("FeatureLevel", ""),
                "shader_archive": archive_name,
                "shader_dir": shader_dir_rel,
                "dxbc_count": len(dxbc_files),
                "dxbc_files": dxbc_files,
                "numeric_params": [
                    {"name": p["ParameterInfo"]["Name"], "type": p.get("ParameterType"), "default": p.get("Value")}
                    for p in entry["numeric_params"]
                ],
                "ub_fields": entry["ub_fields"],
                "has_preshader": entry["has_preshader"],
                "corpus_entries": len(corpus),
                "corpus_asm_sample": corpus[0].get("asm") if corpus else None,
            })

    manifest = {
        "generated_by": "arc_shader_join.py",
        "export_root": str(export_root),
        "total_materials_scanned": len(material_paths),
        "empty_lmr": empty_lmr,
        "with_resource_hash": matched + unmatched,
        "joined_to_shader_code": matched,
        "no_shader_code_match": unmatched,
        "entries": results,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\nDone.")
    print(f"  Materials scanned : {len(material_paths)}")
    print(f"  Empty LMR (no map): {empty_lmr}")
    print(f"  With ResourceHash : {matched + unmatched}")
    print(f"  Joined to DXBC    : {matched}")
    print(f"  No ShaderCode match: {unmatched}")
    print(f"  Manifest written  : {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Arc material ↔ ShaderCode join")
    parser.add_argument("--export-root", required=True, help="Arc_Raiders_Current root")
    parser.add_argument("--out", required=True, help="Output manifest JSON path")
    parser.add_argument("--material-glob", default="**/*.json", help="Glob for material JSONs under export-root")
    parser.add_argument("--asset-filter", default=None, help="Optional filename filter e.g. MI_* or M_Character*")
    args = parser.parse_args()

    export_root = Path(args.export_root)
    if not export_root.exists():
        print(f"ERROR: export-root not found: {export_root}")
        sys.exit(1)

    build_join(export_root, Path(args.out), args.material_glob, args.asset_filter)


if __name__ == "__main__":
    main()
