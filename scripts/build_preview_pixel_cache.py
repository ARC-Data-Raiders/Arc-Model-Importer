"""Build lossless native Blender pixels after build_preview_thumbnails.py."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback
import zlib

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    import bpy.utils.previews
    import numpy as np

    output = output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("Pixel cache output must remain inside the addon")
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    thumbnails = sorted({entry["thumbnail"] for entry in manifest["entries"].values()})
    entries = {}
    collection = bpy.utils.previews.new()
    temporary = output / "pixels.bin.tmp"
    try:
        with temporary.open("wb") as stream:
            for name in thumbnails:
                thumbnail = (ROOT / name).resolve()
                if not thumbnail.is_relative_to(ROOT) or thumbnail.suffix.lower() != '.avif':
                    raise ValueError("Thumbnail escapes its asset directory")
                preview = collection.load(name, str(thumbnail), "IMAGE", force_reload=True)
                width, height = preview.image_size
                if not width or not height:
                    raise ValueError("Empty thumbnail: " + name)
                pixels = np.asarray(preview.image_pixels[:], dtype="<i4").tobytes()
                compressed = zlib.compress(pixels, level=6)
                entries[name] = {"offset": stream.tell(), "length": len(compressed),
                                 "width": width, "height": height,
                                 "sha256": hashlib.sha256(thumbnail.read_bytes()).hexdigest(),
                                 "pixels_sha256": hashlib.sha256(pixels).hexdigest()}
                stream.write(compressed)
                del collection[name]
        temporary.replace(output / "pixels.bin")
    finally:
        bpy.utils.previews.remove(collection)
    manifest["pixel_cache"] = {"file": "pixels.bin", "encoding": "rgba8-bottom-up-zlib", "entries": entries}
    manifest_temporary = output / "manifest.json.tmp"
    manifest_temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest_temporary.replace(manifest_path)
    print("PIXEL_CACHE " + json.dumps({"entries": len(entries), "bytes": (output / "pixels.bin").stat().st_size}))
    print("WROTE " + str(output / "pixels.bin"))
    print("WROTE " + str(manifest_path))


if __name__ == "__main__":
    result = 1
    try:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--output", type=Path, default=ROOT / "assets/preview_thumbnails")
        build(parser.parse_args(argv).output)
        result = 0
    except BaseException:
        traceback.print_exc()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(result)
