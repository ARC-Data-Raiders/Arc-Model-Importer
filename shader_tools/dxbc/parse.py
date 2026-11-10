import re
import struct

_PATTERNS = [
    ("temp", r"^r(\d+)(\.[xyzw]+)?$"),
    ("input", r"^v(\d+)(\.[xyzw]+)?$"),
    ("output", r"^o(\d+)(\.[xyzw]+)?$"),
    ("indexable", r"^x(\d+)\[(.+)\](\.[xyzw]+)?$"),
    ("cbuffer", r"^cb(\d+)\[(.+)\](\.[xyzw]+)?$"),
    ("icb", r"^icb\[(.+)\](\.[xyzw]+)?$"),
    ("resource", r"^t(\d+)(\.[xyzw]+)?$"),
    ("sampler", r"^s(\d+)(\.[xyzw]+)?$"),
    ("uav", r"^u(\d+)(\.[xyzw]+)?$"),
    ("literal", r"^l\((.+)\)$"),
    ("special", r"^(vThreadID|vThreadGroupID|vThreadIDInGroup|vThreadIDInGroupFlattened|vCoverage|vPrim|vOutputControlPointID|vForkInstanceID|vJoinInstanceID|vDomain|vGSInstanceID|oDepth|oDepthGE|oDepthLE|oMask|null)(\.[xyzw]+)?$"),
    ("tgsm", r"^g(\d+)(\.[xyzw]+)?$"),
    ("input2d", r"^v\[(.+?)\]\[(.+?)\](\.[xyzw]+)?$"),
    ("stream", r"^m(\d+)$"),
]


def _split(text):
    values = []
    start = 0
    depth = 0
    for i, char in enumerate(text):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            values.append(text[start:i].strip())
            start = i + 1
    if text[start:].strip():
        values.append(text[start:].strip())
    return values


def _operand(text):
    value = text
    neg = value.startswith("-")
    if neg:
        value = value[1:]
    absolute = value.startswith("|") and value.endswith("|")
    if absolute:
        value = value[1:-1]
    result = {"text": text, "neg": neg, "abs": absolute, "kind": "unknown"}
    for kind, pattern in _PATTERNS:
        match = re.fullmatch(pattern, value)
        if not match:
            continue
        result["kind"] = kind
        if kind == "literal":
            components = []
            for token in match[1].split(","):
                token = token.strip()
                if token.startswith("0x"):
                    uint = int(token, 16)
                    components.append({"text": token, "uint32": uint, "float32": struct.unpack("<f", struct.pack("<I", uint))[0]})
                else:
                    components.append({"text": token, "nonhex": True})
            result["components"] = components
        break
    return result


def parse(text: str) -> dict:
    lines = text.splitlines()
    result = {"schema": "dr.dxbc-asm/1", "model": None, "signatures": {"input": [], "output": [], "patch_constant": []}, "declarations": [], "instructions": [], "roundtrip_failures": [], "signature_failures": []}
    for line in lines:
        if re.fullmatch(r"(vs|ps|gs|hs|ds|cs)_\d_\d", line.strip()):
            result["model"] = line.strip()
            break
    for label, key in [("Input", "input"), ("Output", "output"), ("Patch Constant", "patch_constant")]:
        for i, line in enumerate(lines):
            if line.strip() != f"// {label} signature:":
                continue
            spans = None
            for row in lines[i + 1:]:
                if not row.lstrip().startswith("//"):
                    break
                body = row[row.index("//") + 2:]
                if re.match(r"\s*-{2,}", body):
                    spans = [(m.start(), m.end()) for m in re.finditer(r"-+", body)]
                    continue
                if spans:
                    if not body.strip():
                        break
                    if body.strip() in ("no Input", "no Output", "no Patch Constant"):
                        continue
                    name_match = re.match(r"\S+", body[spans[0][0]:])
                    name = name_match[0] if name_match else ""
                    width0 = spans[0][1] - spans[0][0]
                    shift = max(0, len(name) - width0)
                    fields = [name] + [body[a + shift:b + shift].strip() for a, b in spans[1:]]
                    if len(fields) == 7:
                        fields[2] = fields[2].replace(" ", "")
                        fields[6] = fields[6].replace(" ", "")
                        mask_valid = fields[2] == "N/A" or (bool(fields[2]) and fields[2] == "".join(c for c in "xyzw" if c in fields[2]))
                        used_valid = fields[6] in ("YES", "NO", "") or fields[6] == "".join(c for c in "xyzw" if c in fields[6])
                        valid = bool(name and re.fullmatch(r"\d+", fields[1]) and re.fullmatch(r"(?:\d+|o[A-Za-z]+)", fields[3]) and mask_valid and used_valid and re.fullmatch(r"[A-Za-z0-9_]+", fields[4]) and re.fullmatch(r"[A-Za-z0-9_]+", fields[5]))
                        if not valid:
                            result["signature_failures"].append(row)
                        result["signatures"][key].append(dict(zip(["Name", "Index", "Mask", "Register", "SysValue", "Format", "Used"], fields)))
                    else:
                        result["signature_failures"].append(row)
    i = 0
    while i < len(lines):
        original = lines[i]
        match = re.match(r"^\s*(\d+):\s+", original)
        body = original[match.end():] if match else original.strip()
        if body.startswith("dcl_"):
            if body.startswith("dcl_immediateConstantBuffer"):
                joined = [body]
                while "}" not in joined[-1] and i + 1 < len(lines):
                    i += 1
                    joined.append(lines[i])
                body = "\n".join(joined)
            parts = body.split(None, 1)
            result["declarations"].append({"op": parts[0], "text": parts[1] if len(parts) > 1 else ""})
        elif match:
            depth = 0
            end = len(body)
            for j, char in enumerate(body):
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                elif char.isspace() and depth == 0:
                    end = j
                    break
            opcode = body[:end]
            remaining = body[end:].strip()
            precise = None
            suffix = None
            annotation = re.match(r"^\[precise(?:\(([xyzw]+)\))?\]", remaining)
            if annotation:
                precise = annotation[1] if annotation[1] is not None else "all"
                remaining = remaining[annotation.end():]
                while remaining.startswith("("):
                    depth = 0
                    group_end = None
                    for j, char in enumerate(remaining):
                        if char == "(":
                            depth += 1
                        elif char == ")":
                            depth -= 1
                            if depth == 0:
                                group_end = j + 1
                                break
                    if group_end is None:
                        raise ValueError("Unclosed opcode metadata")
                    opcode += remaining[:group_end]
                    remaining = remaining[group_end:]
                attached_suffix = re.match(r"^_[A-Za-z0-9]+", remaining)
                if attached_suffix:
                    suffix = attached_suffix[0]
                    remaining = remaining[attached_suffix.end():]
            operands = [_operand(v) for v in _split(remaining.strip())]
            index = int(match[1])
            result["instructions"].append({"index": index, "opcode": opcode, "precise": precise, "suffix": suffix, "operands": operands})
            metadata_start = opcode.find("(")
            opcode_name = opcode if metadata_start < 0 else opcode[:metadata_start]
            metadata = "" if metadata_start < 0 else opcode[metadata_start:]
            precise_text = "" if precise is None else " [precise]" if precise == "all" else f" [precise({precise})]"
            reconstructed = f"{index}: {opcode_name}" + precise_text + metadata + (suffix or "") + (" " + ", ".join(v["text"] for v in operands) if operands else "")
            if " ".join(reconstructed.split()) != " ".join(original.rstrip().split()):
                result["roundtrip_failures"].append({"original": original, "reconstructed": reconstructed})
        i += 1
    return result
