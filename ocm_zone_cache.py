"""Bake OCM MaterialID Blue → one full-resolution ZoneIndex PNG.

Encoding: u8 = zone * 32 + 16 (zones 0–7), Non-Color, same WxH as source OCM.
Classification uses nearest ladder peak (37,66,96,126,158,190,222,255).

No PIL. Optional numpy for the pixel loop; pure-Python fallback otherwise.
Cache: %LOCALAPPDATA%/<AddonFolder>/ocm_zone_masks/
"""
from __future__ import annotations

import array
import hashlib
import os
import re
import struct
import zlib

import bpy

from . import addon_line

_CACHE_VERSION = "v5"
_SAFE = re.compile(r"[^\w.\-]+")
_PROP_STEM = "arc_ocm_zone_index_stem"
_PROP_VER = "arc_ocm_zone_index_ver"
_LIVE: dict[str, object] = {}

# Material ID ladder peaks as Non-Color byte values.
_LADDER_PEAKS_U8 = (37, 66, 96, 126, 158, 190, 222, 255)
_ZONE_COUNT = 8


def _user_cache_root() -> str:
    local = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("TEMP")
        or os.path.expanduser("~")
    )
    folder = addon_line.ADDON_FOLDER or "DataRaiders-Outfits"
    return os.path.join(local, folder)


def cache_dir() -> str:
    path = os.path.join(_user_cache_root(), "ocm_zone_masks")
    os.makedirs(path, exist_ok=True)
    return path


def _stem_for_image(image) -> str:
    fp = ""
    try:
        fp = bpy.path.abspath(getattr(image, "filepath_raw", "") or image.filepath or "")
    except Exception:
        fp = ""
    name = (getattr(image, "name", "") or "ocm").replace("\\", "/")
    try:
        w, h = int(image.size[0]), int(image.size[1])
    except Exception:
        w = h = 0
    peaks = ",".join(str(p) for p in _LADDER_PEAKS_U8)
    key = f"{_CACHE_VERSION}|{fp}|{name}|{w}x{h}|{peaks}"
    digest = hashlib.sha1(key.encode("utf-8", errors="replace")).hexdigest()[:16]
    base = _SAFE.sub("_", os.path.splitext(os.path.basename(fp or name))[0])[:40] or "ocm"
    return f"{base}_{digest}"


def _zone_index_path(stem: str) -> str:
    return os.path.join(cache_dir(), f"{stem}_ZoneIndex.png")


def _nearest_zone(u8: int) -> int:
    best_z = 0
    best_d = 999
    for z, peak in enumerate(_LADDER_PEAKS_U8):
        d = abs(int(u8) - int(peak))
        if d < best_d:
            best_d = d
            best_z = z
    return best_z


def _zone_to_u8(zone: int) -> int:
    z = max(0, min(_ZONE_COUNT - 1, int(zone)))
    return z * 32 + 16


def _ocm_size(image) -> tuple[int, int] | None:
    if image is None:
        return None
    try:
        w, h = int(image.size[0]), int(image.size[1])
    except Exception:
        return None
    if w < 1 or h < 1:
        return None
    return w, h


def _write_gray_png(path: str, w: int, h: int, gray_u8: bytes) -> None:
    """8-bit grayscale PNG. Top row = Blender visual top (pixels are bottom-up)."""
    if len(gray_u8) != w * h:
        raise ValueError(f"gray size {len(gray_u8)} != {w}*{h}")

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag)
        crc = zlib.crc32(data, crc) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    raw = bytearray()
    for y in range(h - 1, -1, -1):
        raw.append(0)
        raw.extend(gray_u8[y * w : (y + 1) * w])
    idat = zlib.compress(bytes(raw), 6)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)


def _read_blue_u8(image):
    """Full-res Blue channel as u8 bytes. Never scaled."""
    size = _ocm_size(image)
    if size is None:
        return None
    w, h = size
    try:
        image.colorspace_settings.name = "Non-Color"
    except Exception:
        pass
    try:
        _ = image.pixels[0]
    except Exception:
        try:
            image.reload()
        except Exception:
            pass
    n = w * h
    pix = array.array("f", [0.0]) * (n * 4)
    try:
        image.pixels.foreach_get(pix)
    except Exception:
        try:
            pix = array.array("f", list(image.pixels))
        except Exception as exc:
            print(f"Arc Raiders: OCM read failed: {exc}")
            return None
        if len(pix) < n * 4:
            return None

    try:
        import numpy as np

        arr = np.frombuffer(pix, dtype=np.float32).reshape(n, 4)
        blue = np.clip(arr[:, 2] * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return w, h, blue.tobytes()
    except Exception:
        pass

    out = bytearray(n)
    for i in range(n):
        out[i] = max(0, min(255, int(float(pix[i * 4 + 2]) * 255.0 + 0.5)))
    return w, h, bytes(out)


def _classify_plane(blue_u8: bytes) -> bytes:
    try:
        import numpy as np

        b = np.frombuffer(blue_u8, dtype=np.uint8)
        peaks = np.asarray(_LADDER_PEAKS_U8, dtype=np.int16)
        # Distance to each peak → argmin → zone index
        dist = np.abs(b.astype(np.int16)[:, None] - peaks[None, :])
        zones = dist.argmin(axis=1).astype(np.uint8)
        return (zones * np.uint8(32) + np.uint8(16)).tobytes()
    except Exception:
        pass
    out = bytearray(len(blue_u8))
    for i, u in enumerate(blue_u8):
        out[i] = _zone_to_u8(_nearest_zone(u))
    return bytes(out)


def _load_index_image(path: str, name: str, *, reload: bool = False):
    old = bpy.data.images.get(name)
    if old is not None:
        try:
            old_path = bpy.path.abspath(old.filepath_raw or old.filepath or "")
        except Exception:
            old_path = ""
        if old_path == bpy.path.abspath(path) and os.path.isfile(path):
            try:
                if reload:
                    old.reload()
                old.colorspace_settings.name = "Non-Color"
                return old
            except Exception:
                pass
        try:
            bpy.data.images.remove(old)
        except Exception:
            pass
    img = bpy.data.images.load(path, check_existing=False)
    try:
        img.name = name
    except Exception:
        pass
    try:
        img.colorspace_settings.name = "Non-Color"
    except Exception:
        pass
    return img


def _purge_stale_colorn_pngs() -> int:
    """Remove legacy per-ColorN PNGs from older cache versions."""
    root = cache_dir()
    n = 0
    try:
        for fn in os.listdir(root):
            if "_ColorN" in fn and fn.lower().endswith(".png"):
                try:
                    os.remove(os.path.join(root, fn))
                    n += 1
                except Exception:
                    pass
    except Exception:
        pass
    return n


def _index_matches_ocm(img, ocm_image) -> bool:
    src = _ocm_size(ocm_image)
    dst = _ocm_size(img)
    return src is not None and dst is not None and src == dst


def ensure_ocm_zone_index(ocm_image, *, force: bool = False):
    """Return the ZoneIndex Image for *ocm_image*, baking if needed."""
    if ocm_image is None:
        return None
    src = _ocm_size(ocm_image)
    if src is None:
        return None

    if not force:
        try:
            if (
                ocm_image.get(_PROP_VER) == _CACHE_VERSION
                and ocm_image.get(_PROP_STEM)
            ):
                stem = ocm_image[_PROP_STEM]
                live = _LIVE.get(stem)
                if (
                    live is not None
                    and getattr(live, "name", None) in bpy.data.images
                    and _index_matches_ocm(live, ocm_image)
                ):
                    return live
                path = _zone_index_path(stem)
                if os.path.isfile(path):
                    img = _load_index_image(
                        path, f".arc_ocm_ZoneIndex_{stem}"[:63], reload=False
                    )
                    if _index_matches_ocm(img, ocm_image):
                        _LIVE[stem] = img
                        return img
        except Exception:
            pass

    stem = _stem_for_image(ocm_image)
    path = _zone_index_path(stem)
    if not force and os.path.isfile(path):
        img = _load_index_image(path, f".arc_ocm_ZoneIndex_{stem}"[:63], reload=False)
        if _index_matches_ocm(img, ocm_image):
            try:
                ocm_image[_PROP_STEM] = stem
                ocm_image[_PROP_VER] = _CACHE_VERSION
            except Exception:
                pass
            _LIVE[stem] = img
            return img

    parsed = _read_blue_u8(ocm_image)
    if parsed is None:
        return None
    w, h, blue = parsed
    if (w, h) != src:
        print(f"Arc Raiders: ZoneIndex size mismatch {w}x{h} vs {src}")
        return None

    plane = _classify_plane(blue)
    _write_gray_png(path, w, h, plane)
    img = _load_index_image(path, f".arc_ocm_ZoneIndex_{stem}"[:63], reload=True)
    if not _index_matches_ocm(img, ocm_image):
        print("Arc Raiders: ZoneIndex write/load size mismatch — abort")
        return None

    hist = [0] * _ZONE_COUNT
    for u in plane:
        z = max(0, min(_ZONE_COUNT - 1, (u - 16) // 32))
        hist[z] += 1
    print(
        f"Arc Raiders: ZoneIndex → {os.path.basename(path)} {w}x{h} hist={hist}"
    )

    try:
        ocm_image[_PROP_STEM] = stem
        ocm_image[_PROP_VER] = _CACHE_VERSION
    except Exception:
        pass
    _LIVE[stem] = img
    return img


def ensure_ocm_zone_masks(ocm_image, *, force: bool = False) -> list:
    """Back-compat shim: returns [index] or [] (old callers expected 8 masks)."""
    img = ensure_ocm_zone_index(ocm_image, force=force)
    return [img] if img is not None else []


def preprocess_ocm_image(ocm_image) -> str:
    """Import-time: bake ZoneIndex once per OCM; prune legacy ColorN PNGs."""
    if ocm_image is None:
        return "ocm-masks: skip"
    purged = _purge_stale_colorn_pngs()
    size = _ocm_size(ocm_image)
    img = ensure_ocm_zone_index(ocm_image)
    if img is None:
        return "ocm-masks: fail"
    wh = f"{size[0]}x{size[1]}" if size else "?"
    extra = f"; purged {purged} ColorN" if purged else ""
    return f"ocm-masks: ZoneIndex @ {wh}{extra}"
