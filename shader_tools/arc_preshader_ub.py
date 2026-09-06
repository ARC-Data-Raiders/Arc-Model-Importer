"""
arc_preshader_ub.py
Arc Raiders / Pioneer (Embark UE5) preshader → Uniform Buffer mapper.

Adapted from UEShaderMapExtractor/preshaderToUniformBuffer_UE5.5.py with:
  - Embark opcode fix: ComponentSwizzle at 0x24 (36), not 37 (stock UE5.5)
  - Embark UI AppendVector at 0x25 (37); stock uses 38
  - Accepts FModel's Exports[] JSON shape directly (no manual array wrapping)
  - Graceful fallback for unknown opcodes instead of hard crash
  - Standalone: no dependency on decompress_shader.exe

Usage:
    python arc_preshader_ub.py <material.json> [<material.json> ...]

Output:
    <material>_preshader.bin   -- raw preshader bytecode per resource
    <material>_preshader.json  -- decoded opcode tree + UB field layout
"""

import json
import sys
import struct
import base64
from pathlib import Path


# ---------------------------------------------------------------------------
# Opcode decoder
# ---------------------------------------------------------------------------

def _extract_value(data: bytes, offset: int) -> tuple[list, int]:
    """Read a typed numeric value.  Returns (values, bytes_consumed)."""
    typ = struct.unpack_from("<b", data, offset)[0]
    consumed = 1  # type byte
    match typ:
        case 0:   return [], consumed
        case 1:   return [struct.unpack_from("<f", data, offset + 1)[0]], consumed + 4
        case 2:   return list(struct.unpack_from("<ff", data, offset + 1)), consumed + 8
        case 3:   return list(struct.unpack_from("<fff", data, offset + 1)), consumed + 12
        case 4:   return list(struct.unpack_from("<ffff", data, offset + 1)), consumed + 16
        case 5 | 17: return [struct.unpack_from("<d", data, offset + 1)[0]], consumed + 8
        case 6 | 18: return list(struct.unpack_from("<dd", data, offset + 1)), consumed + 16
        case 7 | 19: return list(struct.unpack_from("<ddd", data, offset + 1)), consumed + 24
        case 8 | 20: return list(struct.unpack_from("<dddd", data, offset + 1)), consumed + 32
        case 9:   return [struct.unpack_from("<i", data, offset + 1)[0]], consumed + 4
        case 10:  return list(struct.unpack_from("<ii", data, offset + 1)), consumed + 8
        case 11:  return list(struct.unpack_from("<iii", data, offset + 1)), consumed + 12
        case 12:  return list(struct.unpack_from("<iiii", data, offset + 1)), consumed + 16
        case 13:  return [bool(data[offset + 1])], consumed + 1
        case 14:  return [bool(data[offset + 1]), bool(data[offset + 2])], consumed + 2
        case 15:  return [bool(data[offset + 1]), bool(data[offset + 2]), bool(data[offset + 3])], consumed + 3
        case 16:  return [bool(data[offset + 1]), bool(data[offset + 2]), bool(data[offset + 3]), bool(data[offset + 4])], consumed + 4
        case 21:  return list(struct.unpack_from("<ffff ffff ffff ffff".split()[0::1], data, offset + 1)[:4]), consumed + 64
        case 22 | 23 | 24:
            return list(struct.unpack_from("<dddd dddd dddd dddd".split()[0::1], data, offset + 1)[:4]), consumed + 128
        case _:
            return [f"<unknown_type_{typ}>"], consumed


def _extract_swizzle(data: bytes, offset: int) -> tuple[list, int]:
    """Read a ComponentSwizzle descriptor (5 bytes after the opcode byte)."""
    return list(struct.unpack_from("<5B", data, offset)), 5


def _decode_instruction(data: bytes, offset: int, numeric_params: list) -> tuple[dict, int]:
    """
    Decode one preshader instruction at *offset*.
    Returns (node_dict, total_bytes_consumed_including_opcode).

    Arc/Embark opcode differences vs stock UE5.5:
      0x24 (36) = ComponentSwizzle  (stock UE5.5 uses 37)
    """
    op = struct.unpack_from("<b", data, offset)[0]
    consumed = 1  # opcode byte

    match op:
        case 1:
            return {"ConstantZero": []}, consumed
        case 2:
            vals, c = _extract_value(data, offset + 1)
            return {"Constant": vals}, consumed + c
        case 3:
            idx = struct.unpack_from("<H", data, offset + 1)[0]
            consumed += 2
            if idx < len(numeric_params):
                name = numeric_params[idx]["ParameterInfo"]["Name"]
            else:
                name = f"<param_idx_{idx}_out_of_range>"
            return {"NumericParameter": [name]}, consumed
        case 4:  return {"Add": []}, consumed
        case 5:  return {"Sub": []}, consumed
        case 6:  return {"Mul": []}, consumed
        case 7:  return {"Div": []}, consumed
        case 8:  return {"Fmod": []}, consumed
        case 9:  return {"Modulo": []}, consumed
        case 10: return {"Min": []}, consumed
        case 11: return {"Max": []}, consumed
        case 12: return {"Clamp": []}, consumed
        case 13: return {"Sin": []}, consumed
        case 14: return {"Cos": []}, consumed
        case 15: return {"Tan": []}, consumed
        case 16: return {"Asin": []}, consumed
        case 17: return {"Acos": []}, consumed
        case 18: return {"Atan": []}, consumed
        case 19: return {"Atan2": []}, consumed
        case 20: return {"Dot": []}, consumed
        case 21: return {"Cross": []}, consumed
        case 22: return {"Sqrt": []}, consumed
        case 23: return {"Rcp": []}, consumed
        case 24: return {"Length": []}, consumed
        case 25: return {"Normalize": []}, consumed
        case 26: return {"Saturate": []}, consumed
        case 27: return {"Abs": []}, consumed
        case 28: return {"Floor": []}, consumed
        case 29: return {"Ceil": []}, consumed
        case 30: return {"Round": []}, consumed
        case 31: return {"Trunc": []}, consumed
        case 32: return {"Sign": []}, consumed
        case 33: return {"Frac": []}, consumed
        case 34: return {"Fractional": []}, consumed
        case 35: return {"Log2": []}, consumed
        case 36:
            # Arc/Embark: ComponentSwizzle lives at 0x24 (36) not 37
            sw, c = _extract_swizzle(data, offset + 1)
            return {"ComponentSwizzle": sw}, consumed + c
        case 37:
            # Embark UI: AppendVector at 0x25 (37). Stock UE5.5 uses 37 for
            # ComponentSwizzle and 38 for AppendVector. UI RoundedBox Float2
            # programs (Size.x ++ Size.y) require 1-byte AppendVector here.
            return {"AppendVector": []}, consumed
        case 38:
            # Stock AppendVector — keep for non-Embark / engine materials
            return {"AppendVector_stock": []}, consumed
        case 39:
            idx = struct.unpack_from("<i", data, offset + 1)[0]
            return {"TextureSize": [idx]}, consumed + 4
        case 40:
            idx = struct.unpack_from("<i", data, offset + 1)[0]
            return {"TexelSize": [idx]}, consumed + 4
        case 41: return {"ExternalTextureCoordinateScaleRotation": []}, consumed
        case 42: return {"ExternalTextureCoordinateOffset": []}, consumed
        case 43: return {"RuntimeVirtualTextureUniform": []}, consumed
        case 44: return {"SparseVolumeTextureUniform": []}, consumed
        case 45: return {"GetField": []}, consumed
        case 46: return {"SetField": []}, consumed
        case 47: return {"Neg": []}, consumed
        case 48: return {"Jump": []}, consumed
        case 49: return {"JumpIfFalse": []}, consumed
        case 50: return {"PushValue": []}, consumed
        case 51: return {"Less": []}, consumed
        case 52: return {"Assign": []}, consumed
        case 53: return {"Greater": []}, consumed
        case 54: return {"LessEqual": []}, consumed
        case 55: return {"GreaterEqual": []}, consumed
        case 56: return {"Exp": []}, consumed
        case 57: return {"Exp2": []}, consumed
        case 58: return {"Log": []}, consumed
        case _:
            return {"_unknown_opcode": op}, consumed


def decode_preshader_program(raw: bytes, numeric_params: list) -> list:
    """Decode a preshader byte slice (one UniformPreshader entry) into a list of instructions."""
    instructions = []
    offset = 0
    while offset < len(raw):
        node, consumed = _decode_instruction(raw, offset, numeric_params)
        instructions.append(node)
        if consumed <= 0:
            # Safety: avoid infinite loop on unknown opcode
            offset += 1
        else:
            offset += consumed
    return instructions


# ---------------------------------------------------------------------------
# Main per-resource processing
# ---------------------------------------------------------------------------

def process_resource(resource: dict, stem: str, out_dir: Path) -> dict:
    sm = resource.get("LoadedShaderMap") or {}
    sm_id = sm.get("ShaderMapId") or {}
    quality = sm_id.get("QualityLevel", "Unk")
    feature = sm_id.get("FeatureLevel", "Unk")

    content = sm.get("Content") or {}
    mco = content.get("MaterialCompilationOutput") or {}
    ues = mco.get("UniformExpressionSet") or {}
    upd = ues.get("UniformPreshaderData") or {}
    raw_b64 = upd.get("Data") or ""
    raw = base64.b64decode(raw_b64) if raw_b64 else b""

    numeric_params = ues.get("UniformNumericParameters") or []
    preshaders = ues.get("UniformPreshaders") or []
    fields = ues.get("UniformPreshaderFields") or []

    tag = f"{quality}_{feature}"
    bin_path = out_dir / f"{stem}_{tag}_preshader.bin"
    bin_path.write_bytes(raw)

    decoded = []
    for entry in preshaders:
        field_idx = entry.get("FieldIndex", -1)
        num_fields = entry.get("NumFields", 1)
        ofs = entry.get("OpcodeOffset", 0)
        size = entry.get("OpcodeSize", 0)
        program_raw = raw[ofs:ofs + size]
        instructions = decode_preshader_program(program_raw, numeric_params)
        field_info = fields[field_idx] if 0 <= field_idx < len(fields) else None
        decoded.append({
            "field_index": field_idx,
            "num_fields": num_fields,
            "field_info": field_info,
            "opcode_offset": ofs,
            "opcode_size": size,
            "program": instructions,
        })

    result = {
        "quality_level": quality,
        "feature_level": feature,
        "resource_hash": sm.get("ResourceHash"),
        "preshader_bin": str(bin_path.name),
        "preshader_buf_size": ues.get("UniformPreshaderBufferSize"),
        "numeric_params": [
            {
                "name": p["ParameterInfo"]["Name"],
                "type": p.get("ParameterType"),
                "default": p.get("Value"),
            }
            for p in numeric_params
        ],
        "fields": fields,
        "preshaders": decoded,
    }
    json_path = out_dir / f"{stem}_{tag}_preshader.json"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def process_material(json_path: Path, out_dir: Path | None = None) -> list[dict]:
    """Process one FModel material JSON. Returns list of per-resource results."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    out = out_dir or json_path.parent
    out.mkdir(parents=True, exist_ok=True)

    # FModel shape: {"Exports": [...], "Metadata": ...}
    exports = data.get("Exports") if isinstance(data, dict) else data
    if not isinstance(exports, list):
        exports = [data]

    results = []
    for export in exports:
        lmr = export.get("LoadedMaterialResources") or []
        if not lmr:
            continue
        stem = json_path.stem
        for i, resource in enumerate(lmr):
            r = process_resource(resource, f"{stem}_{i}", out)
            r["source"] = str(json_path)
            results.append(r)

    return results


def main(argv: list[str]) -> None:
    if not argv:
        print("Usage: arc_preshader_ub.py <material.json> [...]")
        sys.exit(1)

    for arg in argv:
        p = Path(arg)
        if not p.exists():
            print(f"SKIP (not found): {p}")
            continue
        try:
            results = process_material(p)
            if not results:
                print(f"SKIP (no LoadedMaterialResources): {p.name}")
            else:
                for r in results:
                    out_name = r.get("preshader_bin", "?")
                    n_preshaders = len(r.get("preshaders", []))
                    n_params = len(r.get("numeric_params", []))
                    print(f"OK  {p.name}  [{r['quality_level']}/{r['feature_level']}]  "
                          f"params={n_params}  preshaders={n_preshaders}  -> {out_name}")
        except Exception as e:
            print(f"ERR {p.name}: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
