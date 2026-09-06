"""
arc_ruri_symbols.py
Generate Ruri.ShaderDecompiler `.metadata.json` symbol sidecars for Arc Raiders DXBC files.

What it does
------------
1. Reads the arc_shader_join.py manifest (material JSON → ResourceHash → ShaderCode DXBC dir).
2. For each DXBC under ShaderCode/, builds a SerializedProgramData JSON:
   - ConstantBufferParameters: CB0 "Material" with per-field VectorParameters from
     the preshader UniformPreshaderFields + numeric parameter names.
   - BufferBindingParameters: CB0/CB1/CB2 binding entries (from ASM analysis).
   - TextureParameters: t0..t12 with names inferred from material texture param names.
   - SamplerParameters: s0/s1.
3. Writes the JSON beside each .dxbc as <stem>.dxbc.metadata.json so Ruri
   auto-discovers it without --symbols flag.

Usage
-----
    python arc_ruri_symbols.py \
        --join-manifest   <arc_shader_join.json>   \
        --export-root     <Arc_Raiders_Current>     \
        --preshader-dir   <dir with *_preshader.json from arc_preshader_ub.py>  \
        [--out-dir <dir>  copy DXBC+metadata here instead of writing beside originals]
        [--limit N        only process first N material entries]

Output location
---------------
By default writes .metadata.json BESIDE the source .dxbc in ShaderCode/.
If --out-dir is set, copies .dxbc + .metadata.json there (keeps ShaderCode untouched).
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# ShaderParamType values (matches Ruri's ShaderParamType.cs enum)
# ---------------------------------------------------------------------------
FLOAT  = 0   # float / float2 / float3 / float4
BOOL   = 5
UINT   = 6

# Dim values used by Ruri TextureParameter
DIM_2D       = 2
DIM_2D_ARRAY = 5
DIM_CUBE     = 4

# ---------------------------------------------------------------------------
# CB0 "Material" layout known from ASM analysis
# CB0[180]: 720 floats = 180 float4 slots
# CB1[1]:   4 floats   = "view-like" single constant (mapped from engine global)
# CB2[310]: 1240 floats = decal/primitive data
# ---------------------------------------------------------------------------
CB_MATERIAL_NAME  = "Material"
CB_MATERIAL_INDEX = 0
CB_MATERIAL_SIZE  = 180 * 16   # 2880 bytes

CB1_NAME  = "_CB1"             # engine global, names unknown → placeholder
CB1_INDEX = 1
CB1_SIZE  = 1 * 16

CB2_NAME  = "_PrimitiveData"   # decal / primitive data, names unknown
CB2_INDEX = 2
CB2_SIZE  = 310 * 16

# Texture slot layout from ASM (t0-t12):
# t0  = structured buffer (not a texture, Dim=0 / StructuredBuffer)
# t1..t8 = Texture2D slots
# t9..t12 = Texture2DArray slots
TEXTURE_SLOTS = {
    0:  ("_StructBuf0",         0,        -1),   # structured buffer
    1:  ("OcclusionCurvatureMaterialID", DIM_2D, 0),
    2:  ("ColorMask",           DIM_2D,   1),
    3:  ("_Tex2D_t3",           DIM_2D,   -1),
    4:  ("_Tex2D_t4",           DIM_2D,   -1),
    5:  ("_Tex2D_t5",           DIM_2D,   -1),
    6:  ("_Tex2D_t6",           DIM_2D,   -1),
    7:  ("_Tex2D_t7",           DIM_2D,   -1),
    8:  ("_Tex2D_t8",           DIM_2D,   -1),
    9:  ("TextureArray_Normals",  DIM_2D_ARRAY, -1),
    10: ("TextureArray_Masks",    DIM_2D_ARRAY, -1),
    11: ("TextureArray_Colors",   DIM_2D_ARRAY, -1),
    12: ("_Tex2DArr_t12",         DIM_2D_ARRAY, -1),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _field_to_vector_param(field: dict, name: str, field_idx: int) -> dict:
    """Convert one UniformPreshaderField into a VectorParameter entry."""
    ftype = field.get("Type", "Float4")
    col_count = 4
    if ftype == "Float1":
        col_count = 1
    elif ftype == "Float2":
        col_count = 2
    elif ftype == "Float3":
        col_count = 3
    buf_offset = field.get("BufferOffset", 0)
    # Index = float4 slot (BufferOffset is in float4 units per UE)
    float4_index = buf_offset
    return {
        "Name": name,
        "NameIndex": -1,
        "Index": float4_index,
        "ArraySize": 0,
        "Type": FLOAT,
        "RowCount": 1,
        "ColumnCount": col_count,
        "IsMatrix": False,
    }


def _build_material_cb(preshader: dict) -> dict:
    """Build the Material CB symbol from arc_preshader_ub.py output."""
    fields = preshader.get("fields", [])
    params = preshader.get("numeric_params", [])
    preshaders_list = preshader.get("preshaders", [])

    # Build field index → parameter name using decoded preshader programs
    field_names: dict[int, str] = {}
    for ps_entry in preshaders_list:
        fi = ps_entry.get("field_index", -1)
        program = ps_entry.get("program", [])
        # First NumericParameter opcode in the program names this field
        for instr in program:
            if "NumericParameter" in instr:
                names_list = instr["NumericParameter"]
                if names_list:
                    field_names[fi] = names_list[0]
                break

    vector_params = []
    for idx, field in enumerate(fields):
        name = field_names.get(idx, f"_Field{idx}")
        vector_params.append(_field_to_vector_param(field, name, idx))

    # Also add numeric params not covered by preshader entries
    covered = {v.get("Index") for v in vector_params}
    for i, p in enumerate(params):
        pname = p.get("name", f"_Param{i}")
        if pname not in {v["Name"] for v in vector_params}:
            # Append at end; real offset unknown without deeper analysis
            vector_params.append({
                "Name": pname,
                "NameIndex": -1,
                "Index": len(vector_params) + 100 + i,
                "ArraySize": 0,
                "Type": FLOAT,
                "RowCount": 1,
                "ColumnCount": 4,
                "IsMatrix": False,
            })

    return {
        "Name": CB_MATERIAL_NAME,
        "NameIndex": -1,
        "MatrixParameters": [],
        "VectorParameters": vector_params,
        "StructParameters": [],
        "Size": CB_MATERIAL_SIZE,
        "IsPartialCB": True,
    }


def _texture_params_from_material(mat_json_path: Path, export_root: Path) -> dict[int, str]:
    """Read material JSON and map texture slot index to a name hint."""
    slot_names: dict[int, str] = {}
    try:
        data = json.loads(mat_json_path.read_text(encoding="utf-8", errors="ignore"))
        exports = data.get("Exports") if isinstance(data, dict) else data
        for ex in (exports or []):
            props = ex.get("Properties") or {}
            tex_params = props.get("TextureParameterValues") or []
            for i, p in enumerate(tex_params):
                name = (p.get("ParameterInfo") or {}).get("Name", "") or f"_Tex{i}"
                val = p.get("ParameterValue") or p.get("Value") or {}
                slot_names[i + 1] = name   # t1 onwards
    except Exception:
        pass
    return slot_names


def _build_symbols(preshader: dict | None, tex_hints: dict[int, str], mi_paths: list[str]) -> dict:
    """Build the full SerializedProgramData dict."""

    # ConstantBufferParameters
    cb_params = [
        {
            "Name": CB1_NAME,
            "NameIndex": -1,
            "MatrixParameters": [],
            "VectorParameters": [{"Name": "_View0", "NameIndex": -1, "Index": 0,
                                   "ArraySize": 0, "Type": FLOAT,
                                   "RowCount": 1, "ColumnCount": 4, "IsMatrix": False}],
            "StructParameters": [],
            "Size": CB1_SIZE,
            "IsPartialCB": True,
        },
        {
            "Name": CB2_NAME,
            "NameIndex": -1,
            "MatrixParameters": [],
            "VectorParameters": [],
            "StructParameters": [],
            "Size": CB2_SIZE,
            "IsPartialCB": True,
        },
    ]
    if preshader:
        cb_params.insert(0, _build_material_cb(preshader))
    else:
        cb_params.insert(0, {
            "Name": CB_MATERIAL_NAME, "NameIndex": -1,
            "MatrixParameters": [], "VectorParameters": [], "StructParameters": [],
            "Size": CB_MATERIAL_SIZE, "IsPartialCB": True,
        })

    # BufferBindingParameters (CB bind slots)
    buf_bindings = [
        {"Name": CB_MATERIAL_NAME, "NameIndex": -1, "Index": CB_MATERIAL_INDEX, "ArraySize": 0},
        {"Name": CB1_NAME,          "NameIndex": -1, "Index": CB1_INDEX,         "ArraySize": 0},
        {"Name": CB2_NAME,          "NameIndex": -1, "Index": CB2_INDEX,         "ArraySize": 0},
    ]

    # TextureParameters
    tex_params = []
    for slot, (default_name, dim, sampler_idx) in TEXTURE_SLOTS.items():
        name = tex_hints.get(slot, default_name)
        if dim == 0:
            # structured buffer → BufferParameters slot, not TextureParameters
            continue
        tex_params.append({
            "Name": name,
            "NameIndex": -1,
            "Index": slot,
            "SamplerIndex": sampler_idx if sampler_idx >= 0 else 0,
            "MultiSampled": False,
            "Dim": dim,
        })

    # BufferParameters for the structured buffer at t0
    buf_params = [{"Name": "_StructBuf0", "NameIndex": -1, "Index": 0, "ArraySize": 0}]

    # SamplerParameters s0, s1
    sampler_params = [
        {"Name": "sampler_0", "NameIndex": -1, "BindPoint": 0},
        {"Name": "sampler_1", "NameIndex": -1, "BindPoint": 1},
    ]

    return {
        "VectorParameters": [],
        "MatrixParameters": [],
        "TextureParameters": tex_params,
        "BufferParameters": buf_params,
        "ConstantBufferParameters": cb_params,
        "BufferBindingParameters": buf_bindings,
        "UAVParameters": [],
        "SamplerParameters": sampler_params,
        "DescriptorSetParameters": [],
        "EntryPoint": "main",
        "DebugName": None,
        "UsedMaterials": mi_paths,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(join_manifest_path: Path, export_root: Path, preshader_dir: Path | None,
        out_dir: Path | None, limit: int) -> None:

    manifest = json.loads(join_manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("entries", [])
    shader_code_root = export_root / "ShaderCode"

    # Group joined entries by resource_hash so we build one symbol table per shader map
    from collections import defaultdict
    hash_to_entries: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        if e.get("dxbc_count", 0) > 0:
            hash_to_entries[e["resource_hash"]].append(e)

    hashes = sorted(hash_to_entries.keys())
    if limit > 0:
        hashes = hashes[:limit]

    print(f"Processing {len(hashes)} unique shader maps ...", flush=True)
    written = 0
    skipped = 0

    for resource_hash in hashes:
        group = hash_to_entries[resource_hash]
        e0 = group[0]
        archive = e0.get("shader_archive", "")
        dxbc_dir = shader_code_root / archive / resource_hash if archive else None

        if not dxbc_dir or not dxbc_dir.exists():
            skipped += 1
            continue

        # Load preshader for this hash if available
        preshader: dict | None = None
        if preshader_dir:
            # arc_preshader_ub writes: <stem>_0_Num_SM5_preshader.json
            # We need to find one for this hash — match by resource_hash field
            for pf in preshader_dir.glob("*_preshader.json"):
                try:
                    pd = json.loads(pf.read_text(encoding="utf-8"))
                    if pd.get("resource_hash") == resource_hash:
                        preshader = pd
                        break
                except Exception:
                    pass

        # Texture name hints from first material in group
        tex_hints: dict[int, str] = {}
        mat_rel = e0.get("material", "")
        if mat_rel:
            mat_path = export_root / mat_rel
            if mat_path.exists():
                tex_hints = _texture_params_from_material(mat_path, export_root)

        mi_paths = list({e.get("material", "") for e in group if e.get("material")})
        symbols = _build_symbols(preshader, tex_hints, mi_paths)

        dxbc_files = sorted(dxbc_dir.glob("*.dxbc"))
        for dxbc in dxbc_files:
            if out_dir:
                dest_dxbc = out_dir / archive / resource_hash / dxbc.name
                dest_dxbc.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dxbc, dest_dxbc)
                meta_path = dest_dxbc.parent / (dxbc.name + ".metadata.json")
            else:
                meta_path = dxbc.parent / (dxbc.name + ".metadata.json")

            # Add per-shader debug name
            symbols["DebugName"] = dxbc.stem
            meta_path.write_text(json.dumps(symbols, indent=2), encoding="utf-8")
            written += 1

    print(f"Done. metadata.json written: {written}  skipped (no dir): {skipped}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Ruri symbol sidecars for Arc DXBC")
    parser.add_argument("--join-manifest", required=True)
    parser.add_argument("--export-root",   required=True)
    parser.add_argument("--preshader-dir", default=None)
    parser.add_argument("--out-dir",       default=None)
    parser.add_argument("--limit",         type=int, default=0, help="Max shader maps to process (0=all)")
    args = parser.parse_args()

    run(
        join_manifest_path=Path(args.join_manifest),
        export_root=Path(args.export_root),
        preshader_dir=Path(args.preshader_dir) if args.preshader_dir else None,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
