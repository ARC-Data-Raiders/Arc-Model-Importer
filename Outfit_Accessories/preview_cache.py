"""Persistent, source-versioned PNG thumbnails without Blender dependencies."""
from __future__ import annotations

import hashlib
import os
import struct
import zlib

SCHEMA = "rgba256-v3"
MAX_SIDE = 256


def cache_path(source):
    stat = os.stat(source)
    identity = repr((SCHEMA, os.path.normcase(os.path.abspath(source)), stat.st_size, stat.st_mtime_ns))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    root = os.environ.get("DATARAIDERS_PREVIEW_CACHE")
    if not root:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return ""
        root = os.path.join(local, "Arc-Asset-Importer", "cache", "previews")
    return os.path.join(root, digest + ".png")


def thumbnail(pixels, width, height):
    import numpy as np
    rgba = np.asarray(pixels, dtype=np.int32).view(np.uint8).reshape(height, width, 4)
    visible = rgba[:, :, 3] > 0
    if np.all(visible):
        corners = rgba[[0, 0, -1, -1], [0, -1, 0, -1], :3].astype(np.int16)
        background = np.median(corners, axis=0)
        edges = np.concatenate((rgba[0, :, :3], rgba[-1, :, :3], rgba[:, 0, :3], rgba[:, -1, :3]))
        if np.max(np.abs(corners - background)) <= 2 and np.mean(np.max(np.abs(edges.astype(np.int16) - background), axis=1) <= 2) > 0.98:
            visible &= np.max(np.abs(rgba[:, :, :3].astype(np.int16) - background), axis=2) > 2
    positions = np.argwhere(visible)
    if positions.size:
        bottom, left = positions.min(axis=0)
        top, right = positions.max(axis=0) + 1
        rgba = rgba[bottom:top, left:right]
    height, width = rgba.shape[:2]
    if max(width, height) > MAX_SIDE:
        ratio = MAX_SIDE / max(width, height)
        out_width, out_height = max(1, round(width * ratio)), max(1, round(height * ratio))
        horizontal = np.linspace(0, width - 1, out_width)
        vertical = np.linspace(0, height - 1, out_height)
        left = horizontal.astype(np.int32)
        bottom = vertical.astype(np.int32)
        horizontal_weight = (horizontal - left)[None, :, None]
        vertical_weight = (vertical - bottom)[:, None, None]
        right, top = np.minimum(left + 1, width - 1), np.minimum(bottom + 1, height - 1)
        low = rgba[bottom[:, None], left] * (1 - horizontal_weight) + rgba[bottom[:, None], right] * horizontal_weight
        high = rgba[top[:, None], left] * (1 - horizontal_weight) + rgba[top[:, None], right] * horizontal_weight
        rgba = np.rint(low * (1 - vertical_weight) + high * vertical_weight).astype(np.uint8)
    return np.ascontiguousarray(rgba)


def store(destination, rgba):
    import numpy as np
    height, width = rgba.shape[:2]
    straight = rgba.copy()
    alpha = rgba[:, :, 3:4].astype(np.float32)
    straight[:, :, :3] = np.clip(np.rint(rgba[:, :, :3].astype(np.float32) * 255 / np.maximum(alpha, 1)), 0, 255).astype(np.uint8)
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    scanlines = b"".join(b"\0" + row.tobytes() for row in straight[::-1])
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    encoded = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(scanlines, 2)) + chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    temporary = destination + ".tmp-" + str(os.getpid())
    try:
        with open(temporary, "wb") as stream:
            stream.write(encoded)
        os.replace(temporary, destination)
    finally:
        if os.path.isfile(temporary):
            os.remove(temporary)
