"""
arc_family_rename.py
Generalized Material UES → Ruri symbols → decompile for any Pioneer family.

Does NOT assume clothing ColorA / b3 layout. Builds VectorParameters from
UniformPreshaderFields BufferOffset (float units → byte Index = fo*4).

Usage:
  python arc_family_rename.py \\
    --ues path/to/material_ues.json \\
    --dxbc path/to/SF_Pixel.dxbc \\
    --out-dir path/to/out \\
    --ruri path/to/Ruri.ShaderDecompiler.exe \\
    [--cb-index 3] [--cb-size 8192] [--tag family_full]

Requires Ruri.ShaderDecompiler on PATH or --ruri.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

FLOAT = 0


def _param_name(p: dict) -> str | None:
    info = p.get("ParameterInfo") or {}
    return info.get("Name")


def load_ues_fields(ues: dict, resource_hash: str | None = None) -> dict[str, list[dict]]:
    """Collect UniformNumericParameters + BufferOffset from UniformPreshaderFields."""
    # UES may be wrapped Exports[0] or bare Content
    content = ues
    if "Exports" in ues and isinstance(ues["Exports"], list) and ues["Exports"]:
        content = ues["Exports"][0]
    # Walk for UniformExpressionSet
    stack = [content]
    numeric = []
    fields = []
    preshaders = []
    preshader_data = {}
    if resource_hash:
        stack = [content]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            if node.get("ResourceHash") == resource_hash:
                target = (
                    (node.get("Content") or {})
                    .get("MaterialCompilationOutput", {})
                    .get("UniformExpressionSet", {})
                )
                if target:
                    numeric = target.get("UniformNumericParameters") or []
                    fields = target.get("UniformPreshaderFields") or []
                    preshaders = target.get("UniformPreshaders") or []
                    preshader_data = target.get("UniformPreshaderData") or {}
                    break
            stack.extend(node.values())

    if not fields:
        stack = [content]
    while stack:
        if fields:
            break
        node = stack.pop()
        if not isinstance(node, dict):
            if isinstance(node, list):
                stack.extend(node)
            continue
        if "UniformNumericParameters" in node and "UniformPreshaderFields" in node:
            numeric = node["UniformNumericParameters"] or []
            fields = node["UniformPreshaderFields"] or []
            preshaders = node.get("UniformPreshaders") or []
            preshader_data = node.get("UniformPreshaderData") or {}
            break
        stack.extend(node.values())

    # Decode each preshader program. NumericParameter opcodes provide the real
    # name → FieldIndex join; DefaultValueOffset points into the default-value
    # blob and is not a uniform-buffer offset.
    by_name: dict[str, list[dict]] = {}
    raw_b64 = preshader_data.get("Data") or ""
    raw = base64.b64decode(raw_b64) if raw_b64 else b""
    if raw and preshaders:
        from arc_preshader_ub import decode_preshader_program

        for entry in preshaders:
            field_idx = entry.get("FieldIndex", -1)
            if not isinstance(field_idx, int) or not 0 <= field_idx < len(fields):
                continue
            start = int(entry.get("OpcodeOffset", 0))
            size = int(entry.get("OpcodeSize", 0))
            try:
                program = decode_preshader_program(raw[start:start + size], numeric)
            except (IndexError, KeyError, struct.error):
                continue
            name = next(
                (
                    instr["NumericParameter"][0]
                    for instr in program
                    if instr.get("NumericParameter")
                ),
                None,
            )
            if not name:
                continue
            field = fields[field_idx]
            by_name.setdefault(name, []).append({
                "buffer_offset": field.get("BufferOffset"),
                "component_index": field.get("ComponentIndex"),
                "field_type": field.get("Type"),
            })

    # Retain compatibility with exports that put names directly on fields.
    for f in fields:
        fo = f.get("BufferOffset")
        name = f.get("Name") or _param_name(f) or ""
        if not name and "ParameterIndex" in f:
            idx = f["ParameterIndex"]
            if isinstance(idx, int) and idx < len(numeric):
                name = _param_name(numeric[idx]) or ""
        if name and fo is not None:
            by_name.setdefault(name, []).append({
                "buffer_offset": fo,
                "component_index": f.get("ComponentIndex"),
                "field_type": f.get("Type"),
            })

    # Keep unmapped names in the denominator so coverage reflects the complete
    # UES, including programs skipped by an unsupported Embark opcode.
    for p in numeric:
        n = _param_name(p)
        if n:
            by_name.setdefault(n, [])
    return by_name


def build_vectors(by_name: dict, prefer_swizzle: bool = True) -> list[dict]:
    vectors = []
    seen: set[int] = set()
    for name, ents in sorted(by_name.items()):
        if not ents:
            continue
        # Prefer ComponentSwizzle / lowest component
        ents_sorted = sorted(
            ents,
            key=lambda e: (0 if e.get("component_index") in (0, None) else 1, e["buffer_offset"]),
        )
        e = ents_sorted[0]
        fo = int(e["buffer_offset"])
        byte = fo * 4
        if byte in seen:
            continue
        seen.add(byte)
        field_type = str(e.get("field_type") or "")
        type_match = re.search(r"(\d+)$", field_type)
        comps = int(type_match.group(1)) if type_match else 1
        comps = max(1, min(comps, 4))
        if not field_type and (
            name in ("WetTint", "SnowColor", "SelectionColor") or name.startswith("Color")
        ):
            comps = 4
        idxs = {x.get("component_index") for x in ents if x.get("component_index") is not None}
        if len(idxs) >= 3:
            comps = 4
        elif len(idxs) == 2:
            comps = 2
        safe = re.sub(r"[^A-Za-z0-9_]", "_", name)
        vectors.append({
            "Name": safe,
            "NameIndex": -1,
            "Index": byte,
            "ArraySize": 0,
            "Type": FLOAT,
            "RowCount": comps,
            "ColumnCount": 1,
            "IsMatrix": False,
        })
    return vectors


def write_symbols(vectors: list, out_meta: Path, cb_index: int, cb_size: int, tag: str) -> None:
    symbols = {
        "VectorParameters": [],
        "MatrixParameters": [],
        "TextureParameters": [],
        "BufferParameters": [],
        "ConstantBufferParameters": [{
            "Name": "Material",
            "NameIndex": -1,
            "MatrixParameters": [],
            "VectorParameters": vectors,
            "StructParameters": [],
            "Size": cb_size,
            "IsPartialCB": True,
        }],
        "BufferBindingParameters": [
            {"Name": "Material", "NameIndex": -1, "Index": cb_index, "ArraySize": 0},
        ],
        "UAVParameters": [],
        "SamplerParameters": [],
        "DescriptorSetParameters": [],
        "EntryPoint": "main",
        "DebugName": tag,
        "UsedMaterials": [],
    }
    out_meta.write_text(json.dumps(symbols, indent=2), encoding="utf-8")


def measure(hlsl: Path, by_name: dict) -> dict:
    text = hlsl.read_text(encoding="utf-8", errors="ignore")
    in_h = []
    miss = []
    for n in by_name:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", n)
        if re.search(rf"\bMaterial_{re.escape(safe)}\b", text):
            in_h.append(n)
        else:
            miss.append(n)
    mat_refs = re.findall(r"Material_([A-Za-z0-9_]+)", text)
    named = [m for m in mat_refs if not m.startswith("Unmapped") and m != "Unstructured"]
    return {
        "ues_total": len(by_name),
        "ues_named_in_body": len(in_h),
        "ues_named_pct": 100.0 * len(in_h) / max(len(by_name), 1),
        "named_ref_pct": 100.0 * len(named) / max(len(mat_refs), 1),
        "unstructured": text.count("Material_Unstructured"),
        "missing_sample": miss[:30],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ues", type=Path, required=True)
    ap.add_argument("--dxbc", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--ruri", type=Path, default=None)
    ap.add_argument("--cb-index", type=int, default=3)
    ap.add_argument("--cb-size", type=int, default=8192)
    ap.add_argument("--tag", type=str, default="family_full")
    args = ap.parse_args()

    ruri = args.ruri
    if ruri is None:
        cand = Path(__file__).resolve().parents[2] / "Ruri.ShaderDecompiler" / "bin" / "Release" / "net10.0" / "Ruri.ShaderDecompiler.exe"
        # sibling of BlenderImporter is GitHub root
        github = Path(__file__).resolve().parents[2]
        for p in (
            github / "Ruri.ShaderDecompiler" / "bin" / "Release" / "net10.0" / "Ruri.ShaderDecompiler.exe",
            Path.home() / "Documents" / "GitHub" / "Ruri.ShaderDecompiler" / "bin" / "Release" / "net10.0" / "Ruri.ShaderDecompiler.exe",
        ):
            if p.exists():
                ruri = p
                break
    if ruri is None or not ruri.exists():
        print("Ruri not found — pass --ruri", file=sys.stderr)
        return 2

    ues = json.loads(args.ues.read_text(encoding="utf-8"))
    resource_hash = args.dxbc.parent.name
    if not re.fullmatch(r"[0-9A-Fa-f]{40}", resource_hash):
        resource_hash = None
    by_name = load_ues_fields(ues, resource_hash)
    if not by_name:
        print("No UniformPreshaderFields found in UES", file=sys.stderr)
        return 3

    vectors = build_vectors(by_name)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    dst = args.out_dir / args.dxbc.name
    shutil.copy2(args.dxbc, dst)
    meta = Path(str(dst) + ".metadata.json")
    write_symbols(vectors, meta, args.cb_index, args.cb_size, args.tag)
    hlsl = args.out_dir / (args.dxbc.stem + ".hlsl")
    r = subprocess.run([str(ruri), str(dst), str(hlsl), "--symbols", str(meta)], capture_output=True, text=True)
    if r.returncode != 0 or not hlsl.exists():
        print("decompile FAIL", r.stderr[:500], file=sys.stderr)
        return 4
    cov = measure(hlsl, by_name)
    (args.out_dir / "coverage.json").write_text(json.dumps(cov, indent=2), encoding="utf-8")
    (args.out_dir / "vectors.json").write_text(
        json.dumps([{"Name": v["Name"], "Index": v["Index"], "RowCount": v["RowCount"]} for v in vectors], indent=2),
        encoding="utf-8",
    )
    print(json.dumps(cov, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
