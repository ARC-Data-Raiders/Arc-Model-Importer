import collections
import json
import pathlib
import sys
import struct
from .disasm import disassemble
from .parse import parse


def main():
    manifest = pathlib.Path(sys.argv[1])
    output = pathlib.Path(sys.argv[2])
    output.mkdir(parents=True, exist_ok=True)
    source = json.loads(manifest.read_text(encoding="utf-8"))
    report = {"shaders": 0, "unknown_operands": [], "nonhex_literals": 0, "roundtrip_failures": [], "dxil_containers": [], "sm5_count": 0, "dxil_count": 0, "signature_failures": []}
    seen = set()
    histogram = collections.Counter()
    for record in source["records"]:
        if record["container_magic"] != "DXBC":
            continue
        sha = record["container_sha256"]
        if sha in seen:
            continue
        seen.add(sha)
        container = (manifest.parent / record["container_file"]).read_bytes()
        count = struct.unpack_from("<I", container, 28)[0]
        offsets = struct.unpack_from("<" + "I" * count, container, 32)
        chunks = [container[offset:offset + 4].decode("ascii") for offset in offsets]
        if "SHEX" in chunks or "SHDR" in chunks:
            report["sm5_count"] += 1
        elif "DXIL" in chunks:
            report["dxil_count"] += 1
            report["dxil_containers"].append({"sha": sha, "frequency": record["frequency"], "map_hash": record["map_hash"], "chunks": chunks})
            continue
        else:
            raise RuntimeError("Unresolved container " + sha + " chunks: " + repr(chunks))
        text = disassemble(container)
        (output / (sha + ".asm")).write_text(text, encoding="utf-8", newline="\n")
        data = parse(text)
        (output / (sha + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        report["shaders"] += 1
        report["signature_failures"].extend({"sha": sha, "text": row} for row in data["signature_failures"])
        for instruction in data["instructions"]:
            histogram[instruction["opcode"]] += 1
            for operand in instruction["operands"]:
                if operand["kind"] == "unknown":
                    report["unknown_operands"].append({"sha": sha, "text": operand["text"]})
                report["nonhex_literals"] += sum(c.get("nonhex", False) for c in operand.get("components", []))
        report["roundtrip_failures"].extend({"sha": sha, **r} for r in data["roundtrip_failures"])
    for name, value in [("opcode_histogram.json", dict(sorted(histogram.items()))), ("parse_report.json", report)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
