"""Stream top-level JSON objects from UE dump arrays, skipping HeightMap bodies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def iter_top_level_objects_skipping_heightmaps(path: Path) -> Iterator[dict[str, Any]]:
    """Yield each top-level object in a UE Exports-style JSON array without loading HeightMap arrays."""
    with path.open("r", encoding="utf-8", buffering=1024 * 1024) as f:
        while True:
            ch = f.read(1)
            if not ch:
                return
            if ch == "[":
                break

        in_string = False
        escape = False
        depth = 0
        obj_chars: list[str] = []
        skipping = False
        skip_depth = 0
        key_window = ""

        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                return
            for ch in chunk:
                if skipping:
                    if in_string:
                        if escape:
                            escape = False
                        elif ch == "\\":
                            escape = True
                        elif ch == '"':
                            in_string = False
                    else:
                        if ch == '"':
                            in_string = True
                        elif ch == "[":
                            skip_depth += 1
                        elif ch == "]":
                            skip_depth -= 1
                            if skip_depth == 0:
                                skipping = False
                                obj_chars.append("[]")
                    continue

                if depth == 0:
                    if ch.isspace() or ch == ",":
                        continue
                    if ch == "]":
                        return
                    if ch == "{":
                        depth = 1
                        obj_chars = ["{"]
                        in_string = False
                        escape = False
                        key_window = ""
                    continue

                obj_chars.append(ch)
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                    else:
                        key_window = (key_window + ch)[-16:]
                    continue

                if ch == '"':
                    in_string = True
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        yield json.loads("".join(obj_chars))
                        obj_chars = []
                elif ch == "[":
                    if key_window.endswith("HeightMap"):
                        obj_chars.pop()
                        skipping = True
                        skip_depth = 1
