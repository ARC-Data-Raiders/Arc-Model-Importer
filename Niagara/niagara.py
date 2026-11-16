# Merged Niagara FX module: former niagara_*.py modules and prop_fx.py.
# Names defined in more than one source module are prefixed with the source short name.
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
def _sibling(name):
    """utils and fmdex import bpy at load time, so the catalog helpers resolve them on first use."""
    import importlib
    try:
        return importlib.import_module(__package__.split('.')[0] + '.' + {'fmdex': 'fmdex', 'materials': 'Shared.materials', 'niagara': 'Niagara.niagara', 'scripts': 'scripts', 'textures': 'Shared.textures', 'utils': 'Shared.utils'}.get(name, name))
    except (ImportError, TypeError):  # tests import this module top-level
        return importlib.import_module(name)
import ctypes
from ctypes import POINTER, Structure, byref, c_char_p, c_int, c_size_t, c_uint, c_void_p, wintypes
import numpy as np
import json
from functools import lru_cache
from pathlib import Path
import math
import hashlib
import re
from dataclasses import dataclass
import struct
from dataclasses import dataclass, field
from types import SimpleNamespace
import collections
from dataclasses import dataclass,field
from dataclasses import dataclass,asdict
import copy
import hashlib,json,math,struct,sys
import sys
import time


class _LazyModule:
    def __init__(self, name):
        self._name = name
        self._module = None

    def __getattr__(self, name):
        if self._module is None:
            import importlib
            self._module = importlib.import_module(self._name)
        return getattr(self._module, name)


base64 = _LazyModule('base64')
zlib = _LazyModule('zlib')
argparse = _LazyModule('argparse')
glob = _LazyModule('glob')
del _LazyModule
try:
    import bpy
    from bpy.props import BoolProperty, IntProperty, StringProperty
    from bpy.types import Operator
except ImportError:  # outside Blender the UI operator and props are unavailable; helpers still import
    bpy = None
    BoolProperty = IntProperty = StringProperty = None
    class Operator:
        pass

# ---- niagara_catalog.py ----
# Packaged Niagara browse metadata with explicit-action source resolution.




_cache: Dict[str, Any] = {
    "fingerprint": None,
    "entries": None,
    "by_key": None,
    "stats": None,
}

# Cheap LUT probe: read this many bytes looking for the ShaderLUT token.
_LUT_PROBE_BYTES = 131072


def invalidate() -> None:
    _cache["fingerprint"] = None
    _cache["entries"] = None
    _cache["by_key"] = None
    _cache["stats"] = None


def resolved_catalog_root(catalog_root: str = "") -> str:
    """Folder to scan for ``NS_*.json``.

    Order: explicit catalog root → Pioneer ``…/Effects`` → empty.
    """
    if catalog_root:
        path = os.path.abspath(catalog_root)
        if os.path.isdir(path):
            return path

    pioneer = _sibling('utils').get_pioneer_root() or ""
    if not pioneer or not os.path.isdir(pioneer):
        return catalog_root or ""

    candidates = (
        os.path.join(pioneer, "Content", "Pioneer", "Effects"),
        os.path.join(pioneer, "PioneerGame", "Content", "Pioneer", "Effects"),
        os.path.join(pioneer, "Pioneer", "Effects"),
        os.path.join(pioneer, "Effects"),
    )
    for cand in candidates:
        if os.path.isdir(cand):
            return os.path.abspath(cand)
    return ""


def resolved_asset_root(asset_root: str = "") -> str:
    """Mesh search root: explicit asset root, else Pioneer root."""
    if asset_root:
        path = os.path.abspath(asset_root)
        if os.path.isdir(path):
            return path
    pioneer = _sibling('utils').get_pioneer_root() or ""
    if pioneer and os.path.isdir(pioneer):
        return os.path.abspath(pioneer)
    return ""




def _folder_label(path: str, root: str) -> str:
    """Relative folder under the catalog root (Effects-relative when possible)."""
    try:
        rel = os.path.relpath(os.path.dirname(path), root)
    except ValueError:
        return ""
    if rel in (".", ""):
        return ""
    parts = rel.replace("\\", "/").split("/")
    # Drop a leading PioneerGame/Content/Pioneer/Effects prefix if present.
    lower = [p.lower() for p in parts]
    if "effects" in lower:
        i = lower.index("effects")
        parts = parts[i + 1 :]
    return "/".join(parts)


def _probe_has_lut(path: str) -> bool:
    """True when the JSON blob mentions ShaderLUT in the first probe window."""
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(_LUT_PROBE_BYTES)
    except OSError:
        return False
    return b"ShaderLUT" in chunk


def _index_ns_dir(root: str) -> List[dict]:
    if not root or not os.path.isdir(root):
        return []
    out: List[dict] = []
    for walk_root, _dirs, files in os.walk(root):
        for name in files:
            if not name.startswith("NS_") or not name.lower().endswith(".json"):
                continue
            if name.startswith("_"):
                continue
            path = os.path.join(walk_root, name)
            stem = name[:-5] if name.lower().endswith(".json") else name
            folder = _folder_label(path, root)
            out.append(
                {
                    "key": stem,
                    "stem": stem,
                    "path": path,
                    "folder": folder,
                    "has_lut": _probe_has_lut(path),
                    "size": os.path.getsize(path) if os.path.isfile(path) else 0,
                }
            )
    out.sort(key=lambda e: ((e.get("folder") or "").lower(), (e.get("stem") or "").lower()))
    return out


def ensure_loaded(catalog_root: str = "") -> List[dict]:
    """Return shipped systems; the root override is used only when importing."""
    if _cache.get("entries") is not None:
        return _cache["entries"]
    entries, stats = _sibling('fmdex').packaged_browser("effects")
    for entry in entries:
        entry["path"] = ""
    _cache["entries"] = entries
    _cache["by_key"] = {entry["key"].lower(): entry for entry in entries}
    _cache["stats"] = stats
    return entries

def resolve_entry_path(entry: dict, catalog_root: str = "", scene=None) -> str:
    """Resolve an actual NS JSON only for an explicit import or playback action."""
    if not entry:
        return ""
    if not catalog_root and scene is not None:
        catalog_root = getattr(scene, "arc_ns_catalog_root", "") or ""
    try:
        import bpy
        catalog_root = bpy.path.abspath(catalog_root) if catalog_root else ""
    except ImportError:
        pass
    existing = entry.get("path") or ""
    if existing and os.path.isfile(existing):
        return existing
    found = _sibling('fmdex').resolve_catalog_file(entry, ".json", catalog_root)
    if found:
        return found
    if (entry.get("key") or entry.get("stem")) != entry.get("stem"):
        return ""
    root = resolved_catalog_root(catalog_root)
    matches = [candidate for candidate in _index_ns_dir(root)
               if candidate["stem"].lower() == (entry.get("stem") or "").lower()]
    if len(matches) == 1:
        return matches[0]["path"]
    return _sibling('fmdex').resolve_export_file(entry.get("stem") or "", ".json") or ""


def catalog_stats(catalog_root: str = "") -> dict:
    ensure_loaded(catalog_root)
    return dict(_cache.get("stats") or {})


def get_entry(key: str, catalog_root: str = "") -> Optional[dict]:
    if not key or key == "NONE":
        return None
    ensure_loaded(catalog_root)
    entry = (_cache.get("by_key") or {}).get(key.strip().lower())
    return dict(entry) if entry is not None else None


def display_label(entry: dict) -> str:
    stem = entry.get("stem") or entry.get("key") or "?"
    folder = entry.get("folder") or ""
    lut = "LUT" if entry.get("has_lut") else "no-LUT"
    if folder:
        return f"{stem}  ·  {folder}  ·  {lut}"
    return f"{stem}  ·  {lut}"


def _query_match(entry: dict, query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    hay = " ".join(
        (
            entry.get("stem") or "",
            entry.get("folder") or "",
            "lut" if entry.get("has_lut") else "nolut",
        )
    ).lower()
    return all(tok in hay for tok in q.split())


def filter_entries(
    query: str = "",
    *,
    catalog_root: str = "",
    lut_only: bool = False,
    limit: int = 12,
) -> List[dict]:
    entries = ensure_loaded(catalog_root)
    out: List[dict] = []
    for entry in entries:
        if lut_only and not entry.get("has_lut"):
            continue
        if not _query_match(entry, query):
            continue
        out.append(entry)
        if limit and len(out) >= limit:
            break
    return out


def count_entries(
    query: str = "",
    *,
    catalog_root: str = "",
    lut_only: bool = False,
) -> int:
    entries = ensure_loaded(catalog_root)
    n = 0
    for entry in entries:
        if lut_only and not entry.get("has_lut"):
            continue
        if _query_match(entry, query):
            n += 1
    return n

# ---- niagara_curves.py ----
# Decode cooked Niagara curve data interfaces into plain sample tables.
#
# A cooked ``NiagaraDataInterface*Curve`` export carries the baked ``ShaderLUT``
# that the GPU sampled at runtime, so size/colour/velocity-over-life curves are
# recoverable without touching compute bytecode.
#
# The tables only survive a property export that resolves those classes from
# mappings; FModel's own export drops them. Dump with::
#
#     CUE4Parse.Example niagara-ns-dump --out-dir <dir>
#
# No ``bpy`` import here on purpose: the NSIR extractor runs outside Blender.
#
# Layout, verified against 35409 curve interfaces in the Pioneer corpus (100%):
# ``len(ShaderLUT) == (LUTNumSamplesMinusOne + 1) * channels``, with channels
# interleaved inside each sample (sample-major, channel-minor).
#
# Timing comes from ``LUTMinTime`` and ``LUTInvTimeRange``. ``LUTMaxTime`` is
# **not** trustworthy in this cook: interfaces reading ``min=0.8, inv=5.0``
# (a 0.2 span, so max 1.0) report ``LUTMaxTime`` as 0.0, and 20828 interfaces
# report all three as zero while still holding a varying table. Min and inverse
# range agree with each other on 9527 of 9769 interfaces that state a real range,
# so span is taken as ``1 / LUTInvTimeRange`` and falls back to a normalised
# 0..1 curve when the inverse range is absent.

CURVE_CHANNELS = {
    "NiagaraDataInterfaceCurve": 1,
    "NiagaraDataInterfaceVector2DCurve": 2,
    "NiagaraDataInterfaceVectorCurve": 3,
    "NiagaraDataInterfaceColorCurve": 4,
}

# Channel names per interface class, used for Blender F-curve / socket naming.
CURVE_CHANNEL_NAMES = {
    1: ("value",),
    2: ("x", "y"),
    3: ("x", "y", "z"),
    4: ("r", "g", "b", "a"),
}

DEFAULT_SPAN = 1.0


def is_curve_interface(class_name):
    return class_name in CURVE_CHANNELS


def _as_float(value, fallback=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def decode_curve(class_name, props):
    """Decode one curve data interface into a sample table.

    Returns ``None`` when the export carries no usable table, otherwise::

        {
          "class": str, "channels": int, "channelNames": (str, ...),
          "samples": int, "minTime": float, "maxTime": float, "span": float,
          "normalised": bool,          # True when the cook stated no time range
          "times": [float, ...],       # len == samples
          "values": [[float, ...], ...],   # one list per channel
          "constant": bool,            # every channel flat
        }
    """
    channels = CURVE_CHANNELS.get(class_name)
    if not channels:
        return None

    lut = props.get("ShaderLUT")
    if not isinstance(lut, list) or not lut:
        return None

    count = props.get("LUTNumSamplesMinusOne")
    if count is None:
        # Fall back to the array length; only safe because channels is known.
        samples = len(lut) // channels
    else:
        samples = int(round(_as_float(count))) + 1

    if samples < 1 or samples * channels != len(lut):
        return None

    try:
        flat = [float(x) for x in lut]
    except (TypeError, ValueError):
        return None

    values = [flat[c::channels] for c in range(channels)]

    min_time = _as_float(props.get("LUTMinTime"))
    inv_range = _as_float(props.get("LUTInvTimeRange"))
    normalised = inv_range <= 0.0
    span = DEFAULT_SPAN if normalised else 1.0 / inv_range

    if samples == 1:
        times = [min_time]
    else:
        step = span / (samples - 1)
        times = [min_time + step * i for i in range(samples)]

    constant = all(max(ch) - min(ch) <= 1e-6 for ch in values)

    return {
        "class": class_name,
        "channels": channels,
        "channelNames": CURVE_CHANNEL_NAMES[channels],
        "samples": samples,
        "minTime": min_time,
        "maxTime": min_time + span,
        "span": span,
        "normalised": normalised,
        "times": times,
        "values": values,
        "constant": constant,
    }


def curve_summary(curve):
    """Compact, loggable description of a decoded curve."""
    if not curve:
        return "none"
    rng = ", ".join(f"{min(ch):.4g}..{max(ch):.4g}" for ch in curve["values"])
    span = "0..1 normalised" if curve["normalised"] else \
        f"{curve['minTime']:.4g}..{curve['maxTime']:.4g}"
    flat = " flat" if curve["constant"] else ""
    return f"{curve['channels']}ch x {curve['samples']} over {span} [{rng}]{flat}"


def decode_curves_from_exports(exports):
    """Decode every curve interface in a cooked export list.

    Yields ``(export_name, owner, curve)`` where owner is the raw ``Outer``
    name, left for the caller to map onto an emitter.
    """
    for export in exports or ():
        if not isinstance(export, dict):
            continue
        class_name = export.get("Type")
        if not is_curve_interface(class_name):
            continue
        curve = decode_curve(class_name, export.get("Properties") or {})
        if curve is None:
            continue
        outer = export.get("Outer")
        if isinstance(outer, dict):
            outer = outer.get("ObjectName")
        yield export.get("Name"), outer, curve

# ---- niagara_d3d11.py ----
# Minimal D3D11 compute harness over ctypes (spec N-O4 G1): run the game's own DXBC compute shaders on WARP or hardware.
#
# No third-party dependencies: d3d11.dll / d3dcompiler_47.dll are Windows system components. COM methods are called through their
# vtable slots (d3d11.h order). Buffers are typed (R32 float/sint/uint, RGBA32 variants) or raw/structured, with SRV/UAV views,
# constant buffers, 2D/3D textures for data-interface textures and vector fields, and staging read-back.


S_OK = 0
D3D_DRIVER_TYPE_HARDWARE, D3D_DRIVER_TYPE_WARP = 1, 5
D3D11_SDK_VERSION = 7
USAGE_DEFAULT, USAGE_DYNAMIC, USAGE_STAGING = 0, 2, 3
BIND_CONSTANT_BUFFER, BIND_SHADER_RESOURCE, BIND_UNORDERED_ACCESS = 0x4, 0x8, 0x80
CPU_ACCESS_WRITE, CPU_ACCESS_READ = 0x10000, 0x20000
MISC_BUFFER_ALLOW_RAW_VIEWS, MISC_BUFFER_STRUCTURED = 0x20, 0x40
MAP_READ, MAP_WRITE_DISCARD = 1, 4
FORMAT = {'float': 41, 'uint': 42, 'sint': 43, 'float4': 2, 'uint4': 3, 'sint4': 4, 'raw': 39, 'structured': 0, 'half4': 10, 'float2': 16, 'r8uint': 62}
SRV_BUFFER, SRV_BUFFEREX, SRV_TEXTURE2D, SRV_TEXTURE3D = 1, 11, 4, 8
UAV_BUFFER = 1
UAV_FLAG_RAW = 0x1

# vtable slots
DEV_CreateBuffer, DEV_CreateTexture2D, DEV_CreateTexture3D, DEV_CreateSRV, DEV_CreateUAV, DEV_CreateComputeShader, DEV_CreateSamplerState = 3, 5, 6, 7, 8, 18, 23
CTX_Map, CTX_Unmap, CTX_Dispatch, CTX_CopyResource, CTX_UpdateSubresource = 14, 15, 41, 47, 48
CTX_ClearUAVUint = 51
CTX_CSSetSRVs, CTX_CSSetUAVs, CTX_CSSetShader, CTX_CSSetSamplers, CTX_CSSetCBs = 67, 68, 69, 70, 71
CTX_ClearState, CTX_Flush = 110, 111
UNK_Release = 2


class BufferDesc(Structure):
    _fields_ = [('ByteWidth', c_uint), ('Usage', c_uint), ('BindFlags', c_uint), ('CPUAccessFlags', c_uint), ('MiscFlags', c_uint), ('StructureByteStride', c_uint)]


class SubresourceData(Structure):
    _fields_ = [('pSysMem', c_void_p), ('SysMemPitch', c_uint), ('SysMemSlicePitch', c_uint)]


class ViewDesc(Structure):
    """D3D11_SHADER_RESOURCE_VIEW_DESC / D3D11_UNORDERED_ACCESS_VIEW_DESC: format, dimension, 16-byte union."""
    _fields_ = [('Format', c_uint), ('ViewDimension', c_uint), ('u', c_uint * 4)]


class Texture2DDesc(Structure):
    _fields_ = [('Width', c_uint), ('Height', c_uint), ('MipLevels', c_uint), ('ArraySize', c_uint), ('Format', c_uint), ('SampleCount', c_uint),
                ('SampleQuality', c_uint), ('Usage', c_uint), ('BindFlags', c_uint), ('CPUAccessFlags', c_uint), ('MiscFlags', c_uint)]


class Texture3DDesc(Structure):
    _fields_ = [('Width', c_uint), ('Height', c_uint), ('Depth', c_uint), ('MipLevels', c_uint), ('Format', c_uint), ('Usage', c_uint),
                ('BindFlags', c_uint), ('CPUAccessFlags', c_uint), ('MiscFlags', c_uint)]


class SamplerDesc(Structure):
    _fields_ = [('Filter', c_uint), ('AddressU', c_uint), ('AddressV', c_uint), ('AddressW', c_uint), ('MipLODBias', ctypes.c_float),
                ('MaxAnisotropy', c_uint), ('ComparisonFunc', c_uint), ('BorderColor', ctypes.c_float * 4), ('MinLOD', ctypes.c_float),
                ('MaxLOD', ctypes.c_float)]


class MappedSubresource(Structure):
    _fields_ = [('pData', c_void_p), ('RowPitch', c_uint), ('DepthPitch', c_uint)]


class D3D11Error(RuntimeError):
    pass


def _method(obj, slot, restype, *argtypes):
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[slot])


def _check(hr, what):
    if hr != S_OK:
        raise D3D11Error('%s failed: 0x%08X' % (what, hr & 0xffffffff))


def release(obj):
    if obj:
        _method(obj, UNK_Release, c_uint)(obj)


class Device:
    def __init__(self, warp=True):
        d3d11 = ctypes.WinDLL('d3d11')
        dev, ctx, fl = c_void_p(), c_void_p(), c_uint()
        hr = d3d11.D3D11CreateDevice(None, D3D_DRIVER_TYPE_WARP if warp else D3D_DRIVER_TYPE_HARDWARE, None, 0, None, 0, D3D11_SDK_VERSION,
                                     byref(dev), byref(fl), byref(ctx))
        _check(hr, 'D3D11CreateDevice')
        self.dev, self.ctx, self.feature_level = dev, ctx, fl.value
        self._owned = []

    def close(self):
        for obj in reversed(self._owned):
            release(obj)
        self._owned = []
        release(self.ctx)
        release(self.dev)
        self.dev = self.ctx = None

    def _own(self, obj):
        self._owned.append(obj)
        return obj

    def mark(self):
        return len(self._owned)

    def release_since(self, mark):
        """Release every object created after mark (per-tick buffers and views)."""
        for obj in reversed(self._owned[mark:]):
            release(obj)
        del self._owned[mark:]

    # ------------------------------------------------------------------ resources
    def buffer(self, data, bind, usage=USAGE_DEFAULT, cpu=0, misc=0, stride=0):
        data = np.ascontiguousarray(data)
        size = max(16, (data.nbytes + 15) // 16 * 16) if bind & BIND_CONSTANT_BUFFER else max(4, data.nbytes)
        desc = BufferDesc(size, usage, bind, cpu, misc, stride)
        init = None
        if data.nbytes:
            padded = np.zeros(size, dtype=np.uint8)
            padded[:data.nbytes] = data.view(np.uint8).ravel()
            self._keep = padded
            init = SubresourceData(padded.ctypes.data, 0, 0)
        buf = c_void_p()
        _check(_method(self.dev, DEV_CreateBuffer, c_int, POINTER(BufferDesc), POINTER(SubresourceData), POINTER(c_void_p))(
            self.dev, byref(desc), byref(init) if init else None, byref(buf)), 'CreateBuffer')
        return self._own(buf)

    def constant_buffer(self, words):
        return self.buffer(np.asarray(words, dtype=np.uint32), BIND_CONSTANT_BUFFER)

    def typed_buffer(self, data, fmt, uav=False):
        """Typed buffer + SRV (and UAV); data is a flat array of 32-bit words (or 4-word elements for *4 formats)."""
        data = np.asarray(data)
        bind = BIND_SHADER_RESOURCE | (BIND_UNORDERED_ACCESS if uav else 0)
        buf = self.buffer(data.view(np.uint32) if data.size else np.zeros(1, np.uint32), bind)
        n = max(1, data.size // (4 if fmt.endswith('4') else 1))
        srv = self.view(buf, fmt, n, uav=False)
        return buf, srv, (self.view(buf, fmt, n, uav=True) if uav else None)

    def format_buffer(self, data_bytes, fmt, elements):
        """Read-only typed buffer + SRV over raw bytes with an explicit element count (array DIs: RG32F, R8_UINT, ...)."""
        raw = np.frombuffer(bytes(data_bytes) + b'\0' * (-len(data_bytes) % 4), dtype=np.uint32) if data_bytes else np.zeros(1, np.uint32)
        buf = self.buffer(raw, BIND_SHADER_RESOURCE)
        return buf, self.view(buf, fmt, max(1, elements), uav=False)

    def raw_buffer(self, data, uav=False, structured_stride=0):
        data = np.asarray(data, dtype=np.uint32)
        bind = BIND_SHADER_RESOURCE | (BIND_UNORDERED_ACCESS if uav else 0)
        misc = MISC_BUFFER_STRUCTURED if structured_stride else MISC_BUFFER_ALLOW_RAW_VIEWS
        buf = self.buffer(data if data.size else np.zeros(1, np.uint32), bind, misc=misc, stride=structured_stride)
        n = max(1, data.size * 4 // (structured_stride or 4))
        kind = 'structured' if structured_stride else 'raw'
        return buf, self.view(buf, kind, n, uav=False), (self.view(buf, kind, n, uav=True) if uav else None)

    def view(self, buf, fmt, n, uav):
        if uav:
            desc = ViewDesc(FORMAT[fmt], UAV_BUFFER, (c_uint * 4)(0, n, UAV_FLAG_RAW if fmt == 'raw' else 0, 0))
            out = c_void_p()
            _check(_method(self.dev, DEV_CreateUAV, c_int, c_void_p, POINTER(ViewDesc), POINTER(c_void_p))(self.dev, buf, byref(desc), byref(out)), 'CreateUAV')
        else:
            if fmt == 'raw':
                desc = ViewDesc(FORMAT[fmt], SRV_BUFFEREX, (c_uint * 4)(0, n, 1, 0))
            else:
                desc = ViewDesc(FORMAT[fmt], SRV_BUFFER, (c_uint * 4)(0, n, 0, 0))
            out = c_void_p()
            _check(_method(self.dev, DEV_CreateSRV, c_int, c_void_p, POINTER(ViewDesc), POINTER(c_void_p))(self.dev, buf, byref(desc), byref(out)), 'CreateSRV')
        return self._own(out)

    def texture2d(self, data, size, fmt):
        """Single-mip 2D texture + SRV in a FORMAT key ('half4', 'float', 'uint4', ...); data holds the packed texels, row-major [H, W, ...]."""
        w, h = size
        desc = Texture2DDesc(w, h, 1, 1, FORMAT[fmt], 1, 0, USAGE_DEFAULT, BIND_SHADER_RESOURCE, 0, 0)
        data = np.ascontiguousarray(data)
        self._keep_tex = data
        init = SubresourceData(data.ctypes.data, data.nbytes // h, data.nbytes)
        tex = c_void_p()
        _check(_method(self.dev, DEV_CreateTexture2D, c_int, POINTER(Texture2DDesc), POINTER(SubresourceData), POINTER(c_void_p))(
            self.dev, byref(desc), byref(init), byref(tex)), 'CreateTexture2D')
        self._own(tex)
        vd = ViewDesc(FORMAT[fmt], SRV_TEXTURE2D, (c_uint * 4)(0, 1, 0, 0))
        srv = c_void_p()
        _check(_method(self.dev, DEV_CreateSRV, c_int, c_void_p, POINTER(ViewDesc), POINTER(c_void_p))(self.dev, tex, byref(vd), byref(srv)), 'CreateSRV2D')
        return self._own(srv)

    def texture3d(self, data_rgba16f, size):
        """RGBA16F 3D texture (vector fields): data uint16 [D,H,W,4]."""
        w, h, d = size
        desc = Texture3DDesc(w, h, d, 1, FORMAT['half4'], USAGE_DEFAULT, BIND_SHADER_RESOURCE, 0, 0)
        data = np.ascontiguousarray(data_rgba16f, dtype=np.uint16)
        self._keep_tex = data
        init = SubresourceData(data.ctypes.data, w * 8, w * h * 8)
        tex = c_void_p()
        _check(_method(self.dev, DEV_CreateTexture3D, c_int, POINTER(Texture3DDesc), POINTER(SubresourceData), POINTER(c_void_p))(
            self.dev, byref(desc), byref(init), byref(tex)), 'CreateTexture3D')
        self._own(tex)
        vd = ViewDesc(FORMAT['half4'], SRV_TEXTURE3D, (c_uint * 4)(0, 1, 0, 0))
        srv = c_void_p()
        _check(_method(self.dev, DEV_CreateSRV, c_int, c_void_p, POINTER(ViewDesc), POINTER(c_void_p))(self.dev, tex, byref(vd), byref(srv)), 'CreateSRV3D')
        return self._own(srv)

    def sampler(self, filter_=0x15, address=1):
        """Default: MIN_MAG_MIP_LINEAR (0x15), WRAP (1)."""
        desc = SamplerDesc(filter_, address, address, address, 0.0, 1, 8, (ctypes.c_float * 4)(0, 0, 0, 0), 0.0, 3.402823466e38)
        out = c_void_p()
        _check(_method(self.dev, DEV_CreateSamplerState, c_int, POINTER(SamplerDesc), POINTER(c_void_p))(self.dev, byref(desc), byref(out)), 'CreateSampler')
        return self._own(out)

    def sampler_axes(self, filter_, u, v, w):
        desc = SamplerDesc(filter_, u, v, w, 0.0, 1, 8, (ctypes.c_float * 4)(0, 0, 0, 0), 0.0, 3.402823466e38)
        out = c_void_p()
        _check(_method(self.dev, DEV_CreateSamplerState, c_int, POINTER(SamplerDesc), POINTER(c_void_p))(self.dev, byref(desc), byref(out)), 'CreateSampler')
        return self._own(out)

    def compute_shader(self, dxbc):
        code = ctypes.create_string_buffer(bytes(dxbc), len(dxbc))
        out = c_void_p()
        _check(_method(self.dev, DEV_CreateComputeShader, c_int, c_void_p, c_size_t, c_void_p, POINTER(c_void_p))(
            self.dev, code, len(dxbc), None, byref(out)), 'CreateComputeShader')
        return self._own(out)

    # ------------------------------------------------------------------ execution
    def dispatch(self, cs, groups, cbs=(), srvs=(), uavs=(), samplers=()):
        ctx = self.ctx
        arr = lambda items: (c_void_p * max(1, len(items)))(*[i if i else None for i in items])
        _method(ctx, CTX_CSSetShader, None, c_void_p, c_void_p, c_uint)(ctx, cs, None, 0)
        if cbs:
            _method(ctx, CTX_CSSetCBs, None, c_uint, c_uint, c_void_p)(ctx, 0, len(cbs), arr(cbs))
        if srvs:
            _method(ctx, CTX_CSSetSRVs, None, c_uint, c_uint, c_void_p)(ctx, 0, len(srvs), arr(srvs))
        if samplers:
            _method(ctx, CTX_CSSetSamplers, None, c_uint, c_uint, c_void_p)(ctx, 0, len(samplers), arr(samplers))
        if uavs:
            _method(ctx, CTX_CSSetUAVs, None, c_uint, c_uint, c_void_p, c_void_p)(ctx, 0, len(uavs), arr(uavs), None)
        _method(ctx, CTX_Dispatch, None, c_uint, c_uint, c_uint)(ctx, *groups)
        # unbind so the same buffers can be read back / rebound as SRVs
        nulls = (c_void_p * 8)()
        _method(ctx, CTX_CSSetUAVs, None, c_uint, c_uint, c_void_p, c_void_p)(ctx, 0, 8, nulls, None)
        _method(ctx, CTX_CSSetSRVs, None, c_uint, c_uint, c_void_p)(ctx, 0, 8, nulls)

    def read(self, buf, nbytes):
        staging = self.buffer(np.zeros(nbytes // 4 or 1, np.uint32), 0, usage=USAGE_STAGING, cpu=CPU_ACCESS_READ)
        _method(self.ctx, CTX_CopyResource, None, c_void_p, c_void_p)(self.ctx, staging, buf)
        mapped = MappedSubresource()
        _check(_method(self.ctx, CTX_Map, c_int, c_void_p, c_uint, c_uint, c_uint, POINTER(MappedSubresource))(
            self.ctx, staging, 0, MAP_READ, 0, byref(mapped)), 'Map')
        out = np.frombuffer(ctypes.string_at(mapped.pData, nbytes), dtype=np.uint32).copy()
        _method(self.ctx, CTX_Unmap, None, c_void_p, c_uint)(self.ctx, staging, 0)
        return out


def compile_hlsl(source, entry='main', target='cs_5_0'):
    """D3DCompile via the system d3dcompiler_47.dll (tests and lab fixtures only)."""
    lib = ctypes.WinDLL('d3dcompiler_47')
    src = source.encode('utf-8')
    code, err = c_void_p(), c_void_p()
    hr = lib.D3DCompile(src, len(src), None, None, None, entry.encode(), target.encode(), 0, 0, byref(code), byref(err))
    if hr != S_OK:
        msg = ''
        if err:
            ptr = _method(err, 3, c_void_p)(err)
            n = _method(err, 4, c_size_t)(err)
            msg = ctypes.string_at(ptr, n).decode('utf-8', 'replace')
        raise D3D11Error('D3DCompile failed: ' + msg)
    ptr = _method(code, 3, c_void_p)(code)
    n = _method(code, 4, c_size_t)(code)
    blob = ctypes.string_at(ptr, n)
    release(code)
    return blob


def d3d11_disassemble(dxbc):
    """D3DDisassemble via the system d3dcompiler_47.dll."""
    lib = ctypes.WinDLL('d3dcompiler_47')
    out = c_void_p()
    code = ctypes.create_string_buffer(bytes(dxbc), len(dxbc))
    hr = lib.D3DDisassemble(code, c_size_t(len(dxbc)), c_uint(0), None, byref(out))
    _check(hr, 'D3DDisassemble')
    ptr = _method(out, 3, c_void_p)(out)
    n = _method(out, 4, c_size_t)(out)
    text = ctypes.string_at(ptr, n).decode('latin1')
    release(out)
    return text

# ---- niagara_dump_params.py ----
# Runtime Niagara parameters the cook does not hold, read offline from the user-made 2.3.0 minidump
# (assets/niagara_dump_params_2.3.0.json): NPC_Global_* parameter collection values and the User. overrides
# that placed components and gameplay cues set on each system. Returns BakeInputs.parameter_overrides dicts.
DATA = Path(__file__).resolve().parent.parent / 'assets' / 'niagara_dump_params_2.3.0.json'

@lru_cache(maxsize=1)
def load(path=None):
    p = Path(path) if path else DATA
    return json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}

def _stem(system):
    return Path(str(system).split('.')[0]).stem

def system_overrides(system, data=None):
    data = data or load()
    stem = _stem(system)
    for key, entry in (data.get('systems') or {}).items():
        if _stem(key) == stem:
            return dict(entry['overrides'])
    return {}

def collection_values(data=None, live=True):
    """live: the in-map snapshot (wind, FX_Emissive_Intensity, exclusion volumes) minus player-dependent values."""
    data = data or load()
    skip = set(data.get('player_dependent') or ())
    out = {}
    for sets in (data.get('collections') or {}).values():
        values = sets.get('live' if live else 'asset') or sets.get('asset') or {}
        out.update({k: v for k, v in values.items() if not (live and k in skip)})
    if live:
        for sets in (data.get('collections') or {}).values():
            out.update({k: v for k, v in (sets.get('asset') or {}).items() if k in skip})
    return out

def overrides_for(system, live_globals=True, data=None):
    data = data or load()
    out = collection_values(data, live_globals)
    out.update(system_overrides(system, data))
    return out

# ---- niagara_fx_materials.py ----
# Niagara FX material translation (N-O6 lite): cooked FX materials -> node graphs for the GN particle rigs (niagara_rigs).
#
# A renderer's material is resolved from extracted JSON (ArcPak .arc-material.json exports, CUE4Parse raw export lists):
# the MI chain is walked to its master, parameters merge root defaults (CachedExpressionData) under MI overrides, and the
# blend mode comes from the nearest BasePropertyOverrides. Textures are the extracted PNGs; a .arc-texture.json sidecar
# decides sRGB decoding (textures.authored_srgb_sidecar), otherwise FX masks sample raw.
#
# The graph is a per-master approximation (the expression graphs are not cooked), keyed by master name with a blend-mode
# fallback. Particle inputs come from the instancer attributes the rigs store (nx_color, nx_dyn0..3, nx_misc, nx_uvx);
# sprites sample the SubUV frame pair the renderer selects, mesh particles their own UVs. Emissive values are UE scene
# units scaled by one shared exposure node group (DR_NX_FXExposure, EV100 default below) so lit smoke and HDR fire keep
# their authored ratio.
EV100 = 4.0
EXPOSURE_GROUP = 'DR_NX_FXExposure'
MATERIAL_TYPES = ('Material', 'MaterialInstanceConstant', 'MaterialInstanceDynamic')
FAMILIES = {
    'M_FX_MonolithCloud': 'six_point', 'M_FX_MonolithFlame': 'flame', 'M_FX_Fire_Simple': 'fire',
    'M_FX_Simple_Additive_Master': 'additive', 'M_FX_JetEngine_Frigate': 'jet', 'M_JetEngineMat': 'jet',
    'M_FX_Frigate_JetCone': 'jetcone', 'M_FX_Frigate_ThrusterShockwave': 'shockwave', 'M_FX_ArcPlaneMaster': 'arcplane',
    'M_FX_Smoke_Cheap_Master': 'smoke', 'M_FX_Smoke_Cheap_Master_DOF': 'smoke', 'M_FX_Smoke_Cheap_NoCollapse_CloudFogging_Master': 'smoke',
    'M_FX_Debris_Masked_Master': 'debris',
}
BLEND_FAMILY = {'BLEND_Additive': 'additive', 'BLEND_Masked': 'debris', 'BLEND_Opaque': 'debris'}
SKIP_TEXTURES = ('DefaultTexture',)

def _short(value):
    return str(value).rsplit('::', 1)[-1] if value else ''

def _leaf(object_path):
    return str(object_path).rsplit('/', 1)[-1].split('.')[0]

def content_rel(object_path):
    """'/Game/Pioneer/X/Y.Y' -> 'PioneerGame/Content/Pioneer/X/Y' (Engine -> Engine/Content); None for other mounts."""
    path = str(object_path or '').split('.')[0]
    for mount, prefix in (('/Game/', 'PioneerGame/Content/'), ('/Engine/', 'Engine/Content/')):
        if path.startswith(mount):
            return prefix + path[len(mount):]
    return None

def _material_export(doc, name):
    items = doc.get('Exports') if isinstance(doc, dict) and 'Exports' in doc else doc if isinstance(doc, list) else [doc]
    hits = [e for e in items or [] if isinstance(e, dict) and e.get('Type') in MATERIAL_TYPES]
    return next((e for e in hits if e.get('Name') == name), hits[0] if hits else None)

def _cached(exp):
    return exp.get('CachedExpressionData') or (exp.get('Properties') or {}).get('CachedExpressionData') or {}

def _root_defaults(exp):
    """Master parameter defaults: RuntimeEntries[k].ParameterInfoSet names pair with Scalar/Vector/Texture values."""
    ced = _cached(exp)
    names = [[x.get('Name') for x in (ced.get('RuntimeEntries' if k == 0 else 'RuntimeEntries[%d]' % k) or {}).get('ParameterInfoSet') or []]
             for k in range(4)]
    scalars = dict(zip(names[0], ced.get('ScalarValues') or []))
    vectors = {n: tuple(float(v.get(c, 0.0)) for c in 'RGBA') for n, v in zip(names[1], ced.get('VectorValues') or []) if isinstance(v, dict)}
    textures = {n: v.get('AssetPathName') for n, v in zip(names[3], ced.get('TextureValues') or []) if isinstance(v, dict)}
    return scalars, vectors, textures

class fx_materials_Resolver:
    """Finds material exports and texture PNGs under extraction roots (package layout first, then a basename index)."""

    def __init__(self, roots):
        self.roots = [Path(r) for r in roots or [] if r and os.path.isdir(r)]
        self._index = None
        self._exports = {}
        self._infos = {}

    def _by_name(self, filename):
        if self._index is None:
            self._index = {}
            for root in self.roots:
                for base, _dirs, files in os.walk(root):
                    for f in files:
                        self._index.setdefault(f.lower(), os.path.join(base, f))
        return self._index.get(filename.lower())

    def _candidates(self, object_path, exts):
        rel, name = content_rel(object_path), _leaf(object_path)
        for root in self.roots:
            for ext in exts:
                if rel:
                    yield root / (rel + ext)
                yield root / (name + ext)
        for ext in exts:
            hit = self._by_name(name + ext)
            if hit:
                yield Path(hit)

    def export(self, object_path):
        key = str(object_path).split('.')[0]
        if key not in self._exports:
            self._exports[key] = None
            for cand in self._candidates(object_path, ('.arc-material.json', '.json')):
                if not cand.is_file():
                    continue
                try:
                    exp = _material_export(json.loads(cand.read_text(encoding='utf-8')), _leaf(object_path))
                except (OSError, ValueError):
                    continue
                if exp is not None:
                    self._exports[key] = exp
                    break
        return self._exports[key]

    def texture(self, object_path):
        if not object_path or _leaf(object_path) in SKIP_TEXTURES:
            return None
        return next((str(c) for c in self._candidates(object_path, ('.png', '.tga')) if c.is_file()), None)

    def material_info(self, object_path):
        """Merged view of one material: name, master, blend, unlit, two_sided, scalars/vectors/textures/switches, dyn names."""
        key = str(object_path or '').split('.')[0]
        if not key:
            return None
        if key in self._infos:
            return self._infos[key]
        chain, path, seen = [], object_path, set()
        while path and str(path).split('.')[0] not in seen and len(chain) < 12:
            seen.add(str(path).split('.')[0])
            exp = self.export(path)
            if exp is None:
                break
            chain.append(exp)
            parent = (exp.get('Properties') or {}).get('Parent')
            path = parent.get('ObjectPath') if isinstance(parent, dict) else None
        info = None
        if chain:
            root = chain[-1]
            scalars, vectors, textures = _root_defaults(root) if root.get('Type') == 'Material' else ({}, {}, {})
            switches = {}
            for exp in reversed(chain):
                p = exp.get('Properties') or {}
                for x in p.get('ScalarParameterValues') or []:
                    scalars[x['ParameterInfo']['Name']] = float(x.get('ParameterValue', 0.0))
                for x in p.get('VectorParameterValues') or []:
                    v = x.get('ParameterValue') or {}
                    vectors[x['ParameterInfo']['Name']] = tuple(float(v.get(c, 0.0)) for c in 'RGBA')
                for x in p.get('TextureParameterValues') or []:
                    v = x.get('ParameterValue')
                    textures[x['ParameterInfo']['Name']] = v.get('ObjectPath') if isinstance(v, dict) else v
                for x in (p.get('StaticParametersRuntime') or p.get('StaticParameters') or {}).get('StaticSwitchParameters') or []:
                    switches[x['ParameterInfo']['Name']] = bool(x.get('Value'))
            rp = root.get('Properties') or {}
            blend = next((_short((e.get('Properties') or {}).get('BasePropertyOverrides', {}).get('BlendMode')) for e in chain
                          if (e.get('Properties') or {}).get('BasePropertyOverrides', {}).get('BlendMode')), '') or _short(rp.get('BlendMode')) or 'BLEND_Opaque'
            two = any((e.get('Properties') or {}).get('TwoSided') or (e.get('Properties') or {}).get('BasePropertyOverrides', {}).get('TwoSided') for e in chain)
            refs = [r.get('ObjectPath') for r in root.get('ReferencedTextures') or _cached(root).get('ReferencedTextures') or [] if isinstance(r, dict)]
            clip = next(((e.get('Properties') or {}).get('BasePropertyOverrides', {}).get('OpacityMaskClipValue') for e in chain
                         if 'OpacityMaskClipValue' in (e.get('Properties') or {}).get('BasePropertyOverrides', {})), rp.get('OpacityMaskClipValue', 0.3333))
            info = {'name': chain[0].get('Name') or _leaf(object_path), 'master': root.get('Name') or _leaf(path or object_path), 'blend': blend,
                    'unlit': _short(rp.get('ShadingModel')) == 'MSM_Unlit', 'two_sided': bool(two), 'scalars': scalars, 'vectors': vectors,
                    'textures': {k: v for k, v in textures.items() if v and _leaf(v) not in SKIP_TEXTURES}, 'switches': switches,
                    'referenced': [r for r in refs if r], 'dyn_names': list(_cached(root).get('DynamicParameterNames') or []),
                    'clip': float(clip), 'complete': chain[-1].get('Type') == 'Material'}
        self._infos[key] = info
        return info

    def main_texture(self, info, *names):
        """First named texture parameter that resolves, else the first non-normal referenced texture."""
        for n in names:
            path = self.texture(info['textures'].get(n))
            if path:
                return path
        for ref in list(info['textures'].values()) + info['referenced']:
            leaf = _leaf(ref)
            if leaf.endswith(('_N', '_NM')) or leaf.startswith('VT_'):
                continue
            path = self.texture(ref)
            if path:
                return path
        return None

def family(info):
    """Graph family: the master's own translation, else one by blend mode (translucent -> lit smoke card)."""
    if info is None:
        return None
    return FAMILIES.get(info['master']) or BLEND_FAMILY.get(info['blend'], 'smoke')

def channel_index(info):
    """Smoke_Cheap 'Channel select' (0..3 = R, G, B, A); defaults to alpha."""
    v = info['scalars'].get('Channel select', info['scalars'].get('Channel Select'))
    return 3 if v is None else max(0, min(3, int(round(float(v)))))

def cheap_contrast(x, c):
    """UE CheapContrast: saturate(lerp(-c, 1 + c, x))."""
    return min(1.0, max(0.0, -c + x * (1.0 + 2.0 * c)))

# ---------------------------------------------------------------- Blender graph construction (bpy only below)

class _S:
    def __init__(self, mat):
        import bpy
        self.bpy = bpy
        self.N, self.L = mat.node_tree.nodes, mat.node_tree.links

    def node(self, idname, **props):
        n = self.N.new(idname)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def put(self, sock, value):
        if isinstance(value, self.bpy.types.NodeSocket):
            self.L.new(value, sock)
        else:
            sock.default_value = value

    def math(self, op, a, b=0.0, clamp=False):
        n = self.node('ShaderNodeMath', operation=op, use_clamp=clamp)
        self.put(n.inputs[0], a)
        self.put(n.inputs[1], b)
        return n.outputs[0]

    def attr(self, name, kind='INSTANCER'):
        return self.node('ShaderNodeAttribute', attribute_type=kind, attribute_name=name)

    def sep(self, vec):
        n = self.node('ShaderNodeSeparateColor')
        self.put(n.inputs[0], vec)
        return n.outputs

    def rgb(self, op, a, b, fac=1.0):
        n = self.node('ShaderNodeMix', data_type='RGBA', blend_type=op, clamp_result=False)
        self.put(n.inputs['Factor'], fac)
        self.put(n.inputs['A'], a)
        self.put(n.inputs['B'], b)
        return n.outputs['Result']

    def scale(self, color, s):
        n = self.node('ShaderNodeVectorMath', operation='SCALE')
        self.put(n.inputs[0], color)
        self.put(n.inputs['Scale'], s)
        return n.outputs[0]

    def color(self, rgba):
        n = self.node('ShaderNodeRGB')
        n.outputs[0].default_value = tuple(rgba[:3]) + (1.0,)
        return n.outputs[0]

    def image(self, path, non_color):
        bpy = self.bpy
        img = bpy.data.images.load(path, check_existing=True)
        decoded = _authored(path)
        img.colorspace_settings.name = 'sRGB' if (decoded if decoded is not None else not non_color) else 'Non-Color'
        img.alpha_mode = 'CHANNEL_PACKED'
        return img

    def tex(self, img, vector, extension='REPEAT'):
        n = self.node('ShaderNodeTexImage', image=img, interpolation='Linear', extension=extension)
        self.L.new(vector, n.inputs['Vector'])
        return n

def _authored(path):
    """Sidecar sampling: True = sRGB decode, False = raw, None = no sidecar."""
    try:
        from ..Shared import textures
    except ImportError:
        return None
    if textures.authored_srgb_sidecar(path):
        return True
    return False if textures.authored_linear_sidecar(path) else None

def image_stats(img):
    """Per-channel max and whether alpha is constant (cached on the image)."""
    if 'dr_nx_cmax' in img:
        return list(img['dr_nx_cmax']), bool(img['dr_nx_alpha_const'])
    import numpy as np
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(-1, 4)[::7]
    cmax = [float(x) for x in px.max(0)]
    const = bool(px[:, 3].max() - px[:, 3].min() < 1.0 / 255.0)
    img['dr_nx_cmax'], img['dr_nx_alpha_const'] = cmax, const
    return cmax, const

def exposure_group():
    import bpy
    grp = bpy.data.node_groups.get(EXPOSURE_GROUP)
    if grp is None:
        grp = bpy.data.node_groups.new(EXPOSURE_GROUP, 'ShaderNodeTree')
        grp.interface.new_socket('Scale', in_out='OUTPUT', socket_type='NodeSocketFloat')
        out = grp.nodes.new('NodeGroupOutput')
        val = grp.nodes.new('ShaderNodeValue')
        val.name = val.label = 'UE emissive to scene (1 / (1.2 * 2^EV100))'
        grp.links.new(val.outputs[0], out.inputs[0])
        set_exposure(EV100, grp)
    return grp

def set_exposure(ev100, grp=None):
    grp = grp or exposure_group()
    next(n for n in grp.nodes if n.type == 'VALUE').outputs[0].default_value = 1.0 / (1.2 * 2.0 ** float(ev100))
    grp['ev100'] = float(ev100)

def _particle(s):
    col = s.attr('nx_color')
    dyn = s.attr('nx_dyn0')
    d = s.sep(dyn.outputs['Color'])
    return {'rgb': col.outputs['Color'], 'a': col.outputs['Alpha'], 'd0': [d[0], d[1], d[2], dyn.outputs['Alpha']]}

def _sprite_sample(s, img):
    """SubUV frame pair (renderer SubImageSize, nx_misc.x index, blend flag nx_misc.w) blended like the game sampler."""
    misc, uvx, quv = s.attr('nx_misc'), s.attr('nx_uvx'), s.attr('nx_quad_uv', 'GEOMETRY')
    m, u, q = s.sep(misc.outputs['Color']), s.sep(uvx.outputs['Color']), s.sep(quv.outputs['Vector'])
    idx, sx, sy = m[0], u[2], uvx.outputs['Alpha']
    frac = s.math('SUBTRACT', idx, s.math('FLOOR', idx))
    a = s.math('ADD', s.math('SUBTRACT', idx, frac), 0.5)
    ut = s.math('ADD', 0.5, s.math('MULTIPLY', s.math('SUBTRACT', q[0], 0.5), u[0]))
    vt = s.math('ADD', 0.5, s.math('MULTIPLY', s.math('SUBTRACT', q[1], 0.5), u[1]))
    out = []
    for k in (a, s.math('ADD', a, 1.0)):
        h = s.math('FLOOR', s.math('MODULO', k, sx))
        v = s.math('FLOOR', s.math('DIVIDE', k, sx))
        comb = s.node('ShaderNodeCombineXYZ')
        s.put(comb.inputs[0], s.math('DIVIDE', s.math('ADD', h, ut), sx))
        s.put(comb.inputs[1], s.math('SUBTRACT', 1.0, s.math('DIVIDE', s.math('ADD', v, vt), sy)))
        out.append(s.tex(img, comb.outputs[0]))
    fac = s.math('MULTIPLY', frac, misc.outputs['Alpha'])
    rgb = s.rgb('MIX', out[0].outputs['Color'], out[1].outputs['Color'], fac)
    alpha = s.math('ADD', s.math('MULTIPLY', out[0].outputs['Alpha'], s.math('SUBTRACT', 1.0, fac)), s.math('MULTIPLY', out[1].outputs['Alpha'], fac))
    return rgb, alpha

def _mesh_sample(s, img, tiling=(1.0, 1.0)):
    uv = s.node('ShaderNodeTexCoord').outputs['UV']
    if tuple(tiling) != (1.0, 1.0):
        mp = s.node('ShaderNodeMapping')
        s.L.new(uv, mp.inputs['Vector'])
        mp.inputs['Scale'].default_value = (float(tiling[0]), float(tiling[1]), 1.0)
        uv = mp.outputs[0]
    t = s.tex(img, uv)
    return t.outputs['Color'], t.outputs['Alpha']

def _sample(s, kind, img, tiling=(1.0, 1.0)):
    return _sprite_sample(s, img) if kind == 'sprite' else _mesh_sample(s, img, tiling)

def _uv(s):
    return s.sep(s.node('ShaderNodeTexCoord').outputs['UV'])

def _lum(s, rgb):
    c = s.sep(rgb)
    return s.math('DIVIDE', s.math('ADD', s.math('ADD', c[0], c[1]), c[2]), 3.0)

def _channel(s, rgb, alpha, k):
    return alpha if k == 3 else s.sep(rgb)[k]

def _facing(s):
    """1 where the surface faces the camera, 0 at grazing angles."""
    lw = s.node('ShaderNodeLayerWeight')
    return s.math('SUBTRACT', 1.0, lw.outputs['Facing'], clamp=True)

def _emission(s, color, strength=1.0):
    exp = s.node('ShaderNodeGroup')
    exp.node_tree = exposure_group()
    em = s.node('ShaderNodeEmission')
    s.put(em.inputs['Color'], color)
    # UE translucent FX emissive is seen, never a light source: camera rays only (transparent bounces keep the camera
    # flag), so Cycles does not light hull/smoke with it. Particle light renderers do the lighting.
    camera = s.node('ShaderNodeLightPath').outputs['Is Camera Ray']
    s.put(em.inputs['Strength'], s.math('MULTIPLY', s.math('MULTIPLY', exp.outputs[0], strength), camera))
    return em.outputs[0]

def _additive(s, color, strength=1.0):
    add = s.node('ShaderNodeAddShader')
    s.L.new(s.node('ShaderNodeBsdfTransparent').outputs[0], add.inputs[0])
    s.L.new(_emission(s, color, strength), add.inputs[1])
    return add.outputs[0]

def _lit(s, albedo, opacity, emissive=None):
    """Lit translucent card: half diffuse, half back-lit, over transparency by opacity (+ UE-unit emissive)."""
    mix = s.node('ShaderNodeMixShader')
    mix.inputs[0].default_value = 0.5
    dif, tra = s.node('ShaderNodeBsdfDiffuse'), s.node('ShaderNodeBsdfTranslucent')
    s.put(dif.inputs['Color'], albedo)
    s.put(tra.inputs['Color'], albedo)
    s.L.new(dif.outputs[0], mix.inputs[1])
    s.L.new(tra.outputs[0], mix.inputs[2])
    surf = mix.outputs[0]
    if emissive is not None:
        add = s.node('ShaderNodeAddShader')
        s.L.new(surf, add.inputs[0])
        s.L.new(_emission(s, emissive), add.inputs[1])
        surf = add.outputs[0]
    out = s.node('ShaderNodeMixShader')
    s.put(out.inputs[0], s.math('MINIMUM', s.math('MAXIMUM', opacity, 0.0), 1.0))
    s.L.new(s.node('ShaderNodeBsdfTransparent').outputs[0], out.inputs[1])
    s.L.new(surf, out.inputs[2])
    return out.outputs[0]

def _contrast(s, x, c):
    return s.math('SUBTRACT', s.math('MULTIPLY', x, s.math('ADD', 1.0, s.math('MULTIPLY', c, 2.0))), c, clamp=True)

def _build_six_point(s, info, kind, res, P):
    img = s.image(res.main_texture(info, '6Point'), True)
    rgb, a = _sample(s, kind, img)
    light = _lum(s, rgb)
    v = info['vectors']
    tint = s.rgb('MIX', s.scale(s.color(v.get('Shadow Color', (0.73, 0.8, 1.0, 1.0))), 0.35), s.color(v.get('Light Tint', (1.0, 1.0, 1.0, 1.0))), light)
    albedo = s.rgb('MULTIPLY', P['rgb'], tint)
    return _lit(s, albedo, s.math('MULTIPLY', _contrast(s, a, s.math('MAXIMUM', P['d0'][0], 0.0)), P['a']))

def _build_smoke(s, info, kind, res, P):
    img = s.image(res.main_texture(info, 'Texture'), True)
    rgb, a = _sample(s, kind, img)
    k = channel_index(info)
    _cmax, alpha_const = image_stats(img)
    mask = _lum(s, rgb) if (k == 3 and alpha_const) else _channel(s, rgb, a, k)
    packed = info['switches'].get('bUsePackedTexture', False)
    albedo = P['rgb'] if packed else s.rgb('MULTIPLY', P['rgb'], rgb)
    add = info['vectors'].get('Color Add')
    if add and any(add[:3]):
        albedo = s.rgb('ADD', albedo, s.color(add))
    emissive = s.scale(albedo, P['d0'][0]) if info['dyn_names'][:1] == ['Emissive Multiplier'] else None
    return _lit(s, albedo, s.math('MULTIPLY', mask, P['a']), emissive)

def _build_fire(s, info, kind, res, P):
    img = s.image(res.main_texture(info, 'Texture'), True)
    rgb, _a = _sample(s, kind, img)
    cs = info['vectors'].get('ChannelSelect', (1.0, 0.0, 0.0, 1.0))
    c = s.sep(rgb)
    mask = s.math('ADD', s.math('ADD', s.math('MULTIPLY', c[0], cs[0]), s.math('MULTIPLY', c[1], cs[1])), s.math('MULTIPLY', c[2], cs[2]))
    return _additive(s, s.scale(P['rgb'], s.math('MULTIPLY', mask, P['a'])))

def _build_flame(s, info, kind, res, P):
    img = s.image(res.main_texture(info), True)
    rgb, a = _sample(s, kind, img)
    light = _lum(s, rgb)
    mask = light if image_stats(img)[1] else a
    v = info['vectors']
    col = s.rgb('MIX', s.color(v.get('EdgeColor', (0.0, 0.07, 2.0, 1.0))), s.color(v.get('MainColor', (20.0, 4.85, 1.08, 1.0))), light)
    return _additive(s, s.scale(s.rgb('MULTIPLY', col, P['rgb']), s.math('MULTIPLY', mask, P['a'])))

def _build_additive(s, info, kind, res, P):
    img = s.image(res.main_texture(info, 'Texture'), True)
    rgb, a = _sample(s, kind, img)
    mask = s.sep(rgb)[0]
    curve = float(info['scalars'].get('Gradient Curve', 0.0))
    if curve > 0.0 and info['switches'].get('bUseGradientMap', True):
        mask = s.math('POWER', s.math('MAXIMUM', mask, 0.0), curve)
    if not image_stats(img)[1]:
        mask = s.math('MULTIPLY', mask, a)
    return _additive(s, s.scale(P['rgb'], s.math('MULTIPLY', mask, P['a'])))

def _build_jet(s, info, kind, res, P):
    sc, v = info['scalars'], info['vectors']
    uv = _uv(s)
    grad = s.math('MINIMUM', s.math('MAXIMUM', uv[1], 0.0), 1.0)
    col = s.rgb('MIX', s.color(v.get('BottomCol', (10.0, 4.09, 0.83, 1.0))), s.color(v.get('TopCol', (1.54, 4.64, 10.0, 1.0))), grad)
    fade = s.math('POWER', s.math('SUBTRACT', 1.0, grad, clamp=True), float(sc.get('Gradient Contrast Alpha', sc.get('Gradient Contrast', 2.0))) * 0.5)
    path = res.main_texture(info)
    noise = 1.0
    if path:
        n_rgb, _ = _mesh_sample(s, s.image(path, True), (sc.get('Tiling_X', 1.0), sc.get('Tiling_Y', 1.0)))
        noise = s.math('POWER', s.math('MAXIMUM', s.sep(n_rgb)[0], 0.0), float(sc.get('Contrast', 2.0)) * 0.5)
    f = s.math('POWER', _facing(s), float(sc.get('Fresnel Exponent', 0.5)))
    k = s.math('MULTIPLY', s.math('MULTIPLY', fade, noise), s.math('MULTIPLY', f, P['a']))
    return _additive(s, s.scale(s.rgb('MULTIPLY', col, P['rgb']), k))

def _build_jetcone(s, info, kind, res, P):
    v = info['vectors']
    path = res.main_texture(info)
    noise = 1.0
    if path:
        n_rgb, _ = _mesh_sample(s, s.image(path, True))
        noise = s.math('ADD', 0.5, s.math('MULTIPLY', s.sep(n_rgb)[0], 0.5))
    uv = _uv(s)
    # Distance from the nozzle: SM_FX_JetCone_ShockDiamonds_XL imports with V=1 at the nozzle (z=0) and V=0 at the tip,
    # and the beam is brightest at the nozzle (frigate entrance footage).
    along = s.math('SUBTRACT', 1.0, s.math('MINIMUM', s.math('MAXIMUM', uv[1], 0.0), 1.0))
    diamonds = s.math('POWER', s.math('ABSOLUTE', s.math('SINE', s.math('MULTIPLY', along, 6.0 * math.pi))), 8.0)
    flame = s.scale(s.color(v.get('Param_1', (3.57, 1.22, 8.0, 1.0))), s.math('MULTIPLY', s.math('MAXIMUM', P['d0'][2], 0.0), noise))
    dia = s.scale(s.color(v.get('Param_3', (7.19, 2.74, 8.0, 1.0))), s.math('MULTIPLY', s.math('MAXIMUM', P['d0'][3], 0.0), diamonds))
    fade = s.math('MULTIPLY', s.math('SUBTRACT', 1.0, along, clamp=True), _facing(s))
    return _additive(s, s.scale(s.rgb('MULTIPLY', s.rgb('ADD', flame, dia), P['rgb']), s.math('MULTIPLY', fade, P['a'])))

def _build_shockwave(s, info, kind, res, P):
    path = res.main_texture(info)
    mask = 1.0
    if path:
        rgb, _a = _mesh_sample(s, s.image(path, True))
        mask = s.sep(rgb)[0]
    edge = s.math('DIVIDE', _facing(s), max(float(info['scalars'].get('EdgeFade', 0.7)), 1e-3), clamp=True)
    return _lit(s, P['rgb'], s.math('MULTIPLY', s.math('MULTIPLY', mask, edge), P['a']))

def _build_arcplane(s, info, kind, res, P):
    path = res.main_texture(info, 'SmokeTexture')
    if path:
        img = s.image(path, True)
        rgb, a = _mesh_sample(s, img)
        mask = s.sep(rgb)[0] if image_stats(img)[1] else a
    else:
        uv = _uv(s)
        mask = s.math('MULTIPLY', s.math('SINE', s.math('MULTIPLY', uv[1], math.pi)), s.math('SUBTRACT', 1.0, uv[0], clamp=True))
    glow = s.color(info['vectors'].get('GlowColor', (100.0, 50.3, 10.1, 1.0)))
    op = s.math('MULTIPLY', mask, s.math('MINIMUM', P['a'], 1.0))
    return _lit(s, P['rgb'], op, s.scale(s.rgb('MULTIPLY', glow, P['rgb']), s.math('MULTIPLY', mask, P['a'])))

def _build_debris(s, info, kind, res, P):
    path = res.main_texture(info, 'Base Color', 'Texture')
    bsdf = s.node('ShaderNodeBsdfPrincipled')
    alpha = 1.0
    if path:
        rgb, a = _sample(s, kind, s.image(path, False))
        s.put(bsdf.inputs['Base Color'], s.rgb('MULTIPLY', rgb, P['rgb']))
        alpha = s.math('GREATER_THAN', a, float(info.get('clip', 0.3333)))
    bsdf.inputs['Roughness'].default_value = 0.8
    mix = s.node('ShaderNodeMixShader')
    s.put(mix.inputs[0], alpha)
    s.L.new(s.node('ShaderNodeBsdfTransparent').outputs[0], mix.inputs[1])
    s.L.new(bsdf.outputs[0], mix.inputs[2])
    return mix.outputs[0]

BUILDERS = {'six_point': _build_six_point, 'smoke': _build_smoke, 'fire': _build_fire, 'flame': _build_flame, 'additive': _build_additive,
            'jet': _build_jet, 'jetcone': _build_jetcone, 'shockwave': _build_shockwave, 'arcplane': _build_arcplane, 'debris': _build_debris}
NEEDS_TEXTURE = ('six_point', 'smoke', 'fire', 'flame', 'additive')

def build_material(object_path, kind, resolver):
    """Translated material for a sprite or mesh renderer; None when unresolved (caller keeps its preview; ribbons keep theirs)."""
    import bpy
    if kind == 'mesh' and str(object_path).split('.')[0] == '/Game/Pioneer/Effects/Materials/Enemies/VisionCone/MI_FX_VisionCone_Turret_Shadowed':
        return _sibling('shader_tools.dxbc.visioncone').build(object_path, kind, resolver)
    if kind not in ('sprite', 'mesh'):
        return None
    info = resolver.material_info(object_path)
    fam = family(info)
    if fam is None or (fam in NEEDS_TEXTURE and resolver.main_texture(info, 'Texture', '6Point') is None):
        return None
    name = 'NXM_%s_%s' % (info['name'], kind)
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    s = _S(mat)
    surface = BUILDERS[fam](s, info, kind, resolver, _particle(s))
    s.L.new(surface, s.node('ShaderNodeOutputMaterial').inputs['Surface'])
    mat.surface_render_method = 'BLENDED' if fam != 'debris' else 'DITHERED'
    mat.use_backface_culling = False
    for prop, value in (('use_transparency_overlap', True), ('use_transparent_shadow', True)):
        if hasattr(mat, prop):
            setattr(mat, prop, value)
    mat['dr_nx_material'] = str(object_path)
    mat['dr_nx_family'] = fam
    mat['dr_nx_master'] = info['master']
    mat['dr_nx_blend'] = info['blend']
    return mat

def _empty_cache_ply(path, ribbon=False):
    data = path.read_bytes()
    marker = b'end_header\n'
    offset = data.index(marker) + len(marker)
    header = data[:offset].decode('ascii').splitlines()
    if header[:2] != ['ply', 'format binary_little_endian 1.0'] or not header[2].startswith('element vertex '):
        return False
    count = int(header[2].split()[2])
    properties = header[3:-1]
    if ribbon:
        if properties[-2:] != ['element face 0', 'property list uchar int vertex_indices']:
            return False
        properties = properties[:-2]
    if count < 0 or not properties or any(not row.startswith('property float ') or len(row.split()) != 3 for row in properties):
        return False
    names = [row.split()[2] for row in properties]
    if len(set(names)) != len(names) or len(data) != offset + count * len(names) * 4:
        return False
    if count == 0:
        return True
    rows = np.frombuffer(data, dtype='<f4', offset=offset).reshape(count, len(names))
    if ribbon:
        if 'sl_nx' not in names or 'sl_ny' not in names:
            return False
        normals = rows[:, [names.index('sl_nx'), names.index('sl_ny')]]
        return bool(np.isfinite(normals).all() and (np.abs(normals).sum(axis=1) < 0.5).all())
    if 'nx_valid' not in names:
        return False
    valid = rows[:, names.index('nx_valid')]
    return bool(np.isfinite(valid).all() and (valid <= 0.5).all())


def stream_proven_empty_across_cache(cache_dir, manifest, info):
    if not cache_dir or not isinstance(manifest, dict) or not isinstance(info, dict):
        return False
    try:
        kind = info.get('kind')
        emitter, ordinal = info.get('emitter'), info.get('ordinal')
        if kind not in ('sprite', 'mesh', 'ribbon') or type(emitter) is not int or type(ordinal) is not int:
            return False
        stream = 'e%d_r%d' % (emitter, ordinal)
        start, end = manifest.get('frame_start'), manifest.get('frame_end')
        if type(start) is not int or type(end) is not int or end < start:
            return False
        frames = end - start + 1
        entries = [row for row in manifest.get('streams', ()) if row.get('name') == stream]
        if len(entries) != 1 or entries[0].get('kind') != kind or entries[0].get('emitter') != emitter or entries[0].get('ordinal') != ordinal:
            return False
        stats = manifest['stats']
        counts = (stats['frames'], stats['max_count'][stream], stats['empty_frames'][stream])
        if any(type(value) is not int for value in counts) or counts != (frames, 0, frames):
            return False
        if kind == 'ribbon' and stats['ribbon_subsegments'][stream] != [0] * frames:
            return False
        root = Path(cache_dir).resolve()
        directory = (root / stream).resolve()
        if not root.is_dir() or not directory.is_dir() or not directory.is_relative_to(root):
            return False
        if json.loads((root / 'cache_manifest.json').read_text(encoding='utf-8')) != manifest:
            return False
        for prefix in (('f', 'm') if kind == 'ribbon' else ('f',)):
            expected = {'%s_%04d.ply' % (prefix, frame) for frame in range(start, end + 1)}
            if {entry.name for entry in directory.glob(prefix + '_*.ply')} != expected:
                return False
            for name in expected:
                entry = (directory / name).resolve()
                if not entry.is_relative_to(directory) or not _empty_cache_ply(entry, ribbon=prefix == 'm'):
                    return False
        sidecar = directory / 'sidecar.json'
        binaries = {entry.name for entry in directory.glob('s_*.bin')}
        if sidecar.exists() or binaries:
            records = json.loads(sidecar.read_text(encoding='utf-8'))['frames']
            if [record['frame'] for record in records] != list(range(start, end + 1)) or any(type(record['count']) is not int or record['count'] != 0 for record in records):
                return False
            if binaries != {'s_%04d.bin' % frame for frame in range(start, end + 1)}:
                return False
            if any((directory / name).stat().st_size != 0 for name in binaries):
                return False
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return False
    return True


def renderer_materials(roots):
    """Callback for niagara_rigs.build_system: (renderer info, kind, slot, slot material name) -> material or None."""
    resolver = fx_materials_Resolver(roots)
    context = {}

    def bind_runtime(cache, manifest):
        context.pop('geometry_context', None)
        context.update(cache=cache, manifest=manifest)

    def bind_view(view):
        context['view'] = view

    def bind_geometry(info, objects):
        if is_empty_stream(info):
            return
        overrides = info.get('override_materials', []) if info.get('props', {}).get('bOverrideMaterials') else []
        if not any(str(path).split('.')[0] == '/Game/Pioneer/Effects/Materials/Enemies/VisionCone/MI_FX_VisionCone_Turret_Shadowed' for path in overrides):
            return
        vf = _sibling('shader_tools.dxbc.visioncone_vf')
        vc = _sibling('shader_tools.dxbc.visioncone')
        vf.validate_view(context.get('view'), bpy.context.scene)
        source_context = vc._context(resolver.roots)
        pin = source_context.get('mesh')
        if not pin or len(objects) != 1 or objects[0].get('dr_nx_mesh_source') != info['meshes'][0]['object_path']:
            raise vc.Unsupported('pinned source mesh required for diagnostic VF')
        receipt = vf.hydrate_mesh(objects[0], _sibling('Shared.uemodel_import'), pin)
        particles = vf.validate_particles(sys.modules[__name__], context['cache'], context['manifest'], info)
        context['geometry_context'] = dict(mesh=receipt, particles=particles)

    def is_empty_stream(info):
        return stream_proven_empty_across_cache(context.get('cache'), context.get('manifest'), info)

    def pick(info, kind, slot=None, slot_name=None):
        if is_empty_stream(info):
            return None
        if kind == 'mesh':
            over = info.get('override_materials') or []
            path = over[slot] if info['props'].get('bOverrideMaterials') and slot is not None and slot < len(over) else None
            if not path and slot_name:
                path = '/Game/' + slot_name.split('.')[0]  # basename lookup (no package path on an imported slot)
            if str(path).split('.')[0] == '/Game/Pioneer/Effects/Materials/Enemies/VisionCone/MI_FX_VisionCone_Turret_Shadowed':
                resolver.source_runtime = dict(context, renderer=info)
            return build_material(path, 'mesh', resolver) if path else None
        return build_material(info.get('material'), kind, resolver) if info.get('material') else None
    pick.resolver = resolver
    pick.bind_runtime = bind_runtime
    pick.bind_view = bind_view
    pick.bind_geometry = bind_geometry
    pick.is_empty_stream = is_empty_stream
    return pick

# ---- niagara_ir.py ----
# Lossless, bpy-free evidence IR for cooked Niagara JSON exports.
#
# This module preserves compiled evidence; it does not claim executable playback.
# Export identity includes package, outer chain and export slot so repeated names
# cannot overwrite one another. Unknown serialized fields remain in rawExport.


SCHEMA_VERSION = 1
SCRIPT_USAGES = {
    0: "Function", 1: "Module", 2: "DynamicInput", 3: "ParticleSpawnScript",
    4: "ParticleSpawnScriptInterpolated", 5: "ParticleUpdateScript",
    6: "ParticleEventScript", 7: "ParticleSimulationStageScript",
    8: "ParticleGPUComputeScript", 9: "EmitterSpawnScript",
    10: "EmitterUpdateScript", 11: "SystemSpawnScript", 12: "SystemUpdateScript",
}
SIM_TARGETS = {0: "CPUSim", 1: "GPUComputeSim"}


def normalize_enum(value, names):
    """Normalize known integers and qualified names; retain unknown values."""
    if isinstance(value, str):
        short = value.rsplit("::", 1)[-1]
        if short in names.values():
            return short
        try:
            return names.get(int(value), value)
        except ValueError:
            return value
    return names.get(value, value) if isinstance(value, int) else value


def exports_from_document(document):
    if isinstance(document, list):
        exports = document
    elif isinstance(document, dict) and isinstance(document.get("Exports"), list):
        exports = document["Exports"]
    else:
        raise ValueError("expected an export list or an object containing Exports")
    if any(not isinstance(e, dict) for e in exports):
        raise ValueError("export list contains a non-object entry")
    return exports


def reference_name(reference):
    if isinstance(reference, str):
        return reference
    if not isinstance(reference, dict):
        return None
    name = reference.get("ObjectName")
    if not isinstance(name, str):
        return None
    if "'" in name:
        name = name.split("'", 1)[1].rstrip("'")
    return name


def export_object_name(export):
    outer = reference_name(export.get("Outer"))
    name = export.get("Name") or "<unnamed>"
    if not outer:
        return name
    # The outer of a top-level owned export is the system itself.
    separator = ":" if ":" not in outer and "." not in outer else "."
    return outer + separator + name


def package_path(exports, fallback):
    candidates = []
    for export in exports:
        outer = export.get("Outer")
        path = outer.get("ObjectPath") if isinstance(outer, dict) else None
        if isinstance(path, str) and path.startswith("/Game/"):
            candidates.append(re.sub(r"\.\d+$", "", path))
    return max(set(candidates), key=candidates.count) if candidates else fallback


def byte_payload(value):
    """Read the existing JSON byte forms without coercing corrupt values."""
    if isinstance(value, dict):
        return byte_payload(value.get("Data"))
    if value is None:
        return b""
    if isinstance(value, list):
        if any(type(x) is not int or not 0 <= x <= 255 for x in value):
            raise ValueError("byte array contains an invalid byte")
        return bytes(value)
    if isinstance(value, str):
        return base64.b64decode(value, validate=True)
    raise ValueError("unsupported byte payload representation")


def payload_state(container, key):
    if key not in container:
        return {"state": "missing", "byteCount": 0}
    try:
        data = byte_payload(container[key])
    except (ValueError, TypeError) as exc:
        return {"state": "invalid", "byteCount": 0, "error": str(exc)}
    return {"state": "present" if data else "empty", "byteCount": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def select_emitter_version(properties):
    versions = properties.get("VersionData") or []
    if not isinstance(versions, list):
        return None, "invalid-version-list"
    exposed = properties.get("ExposedVersion")
    for index, version in enumerate(versions):
        if isinstance(version, dict) and (version.get("Version") or {}).get("VersionGuid") == exposed:
            return index, "exposed-guid"
    if len(versions) == 1 and isinstance(versions[0], dict):
        return 0, "only-version"
    return None, "ambiguous" if versions else "missing"


def extract_document(document, *, source_path="", game_version=None, source_sha256=None):
    exports = exports_from_document(document)
    package = package_path(exports, Path(source_path).stem or "<unknown-package>")
    records = []
    by_object = {}
    for index, export in enumerate(exports):
        name = export_object_name(export)
        record = {"id": f"{package}#{index}", "exportIndex": index,
                  "objectName": name, "type": export.get("Type"),
                  "name": export.get("Name"), "outer": export.get("Outer"),
                  "rawExport": export}
        records.append(record)
        by_object.setdefault(name, []).append(record)
    emitters, scripts, renderers, interfaces = [], [], [], []
    for record in records:
        export = record["rawExport"]
        kind = record["type"] or ""
        properties = export.get("Properties") or {}
        outer_name = reference_name(record["outer"])
        owners = by_object.get(outer_name, [])
        record["outerId"] = owners[0]["id"] if len(owners) == 1 else None
        if kind == "NiagaraEmitter":
            selected, reason = select_emitter_version(properties)
            version = properties["VersionData"][selected] if selected is not None else {}
            record.update({"selectedVersionIndex": selected, "versionSelection": reason,
                           "simTarget": normalize_enum(version.get("SimTarget"), SIM_TARGETS)})
            emitters.append(record)
        elif kind == "NiagaraScript":
            vm = properties.get("CachedScriptVM")
            vm = vm if isinstance(vm, dict) else {}
            maps = export.get("LoadedScriptResources")
            record.update({"usage": normalize_enum(properties.get("Usage"), SCRIPT_USAGES),
                           "usageRaw": properties.get("Usage"), "vm": vm,
                           "bytecode": payload_state(vm, "ByteCode"),
                           "literals": payload_state(vm, "ScriptLiterals"),
                           "shaderMaps": maps,
                           "shaderMapState": "present" if maps else (
                               "empty" if "LoadedScriptResources" in export else "missing"),
                           "shaderTailBytes": export.get("ShaderMapLeftoverBytes"),
                           "shaderReadError": export.get("ShaderMapReadError")})
            scripts.append(record)
        elif kind.endswith("RendererProperties"):
            renderers.append(record)
        elif "DataInterface" in kind:
            interfaces.append(record)
    by_id = {r["id"]: r for r in records}
    # Parent references resolve using full object names, never a short-name dict.
    def emitter_owner(record):
        seen = set()
        while record and record["id"] not in seen:
            seen.add(record["id"])
            if record["type"] == "NiagaraEmitter":
                return record["id"]
            parent_id = record.get("outerId")
            record = by_id.get(parent_id)
        return None
    for record in scripts + renderers + interfaces:
        record["emitterId"] = emitter_owner(record)
        ancestor = record
        seen = set()
        while ancestor and ancestor["id"] not in seen and ancestor["type"] != "NiagaraSystem":
            seen.add(ancestor["id"])
            ancestor = by_id.get(ancestor.get("outerId"))
        record["scope"] = "emitter" if record["emitterId"] else ("system" if ancestor else "unresolved")
    systems = [r for r in records if r["type"] == "NiagaraSystem"]
    gaps = []
    for script in scripts:
        if script["bytecode"]["state"] != "present" and script["usage"] != "ParticleGPUComputeScript":
            gaps.append({"exportId": script["id"], "kind": "bytecode-" + script["bytecode"]["state"],
                         "usage": script["usage"]})
    if any(e["selectedVersionIndex"] is None for e in emitters):
        gaps.append({"kind": "unresolved-emitter-version"})
    classified_ids = {r["id"] for group in (systems, emitters, scripts, renderers, interfaces) for r in group}
    return {"schemaVersion": SCHEMA_VERSION, "source": {"path": str(source_path),
            "packagePath": package, "gameVersion": game_version, "sha256": source_sha256,
            "metadata": document.get("Metadata") if isinstance(document, dict) else None},
            "systems": systems, "emitters": emitters, "scripts": scripts,
            "renderers": renderers, "dataInterfaces": interfaces,
            "otherExports": [r for r in records if r["id"] not in classified_ids],
            "coverage": {"structural": "retained", "simulation": "not-executed",
                         "rendering": "not-evaluated", "gaps": gaps}}


def extract_file(path, *, game_version=None):
    path = Path(path)
    raw = path.read_bytes()
    return extract_document(json.loads(raw.decode("utf-8-sig")), source_path=str(path.resolve()),
                            game_version=game_version, source_sha256=hashlib.sha256(raw).hexdigest())

# ---- niagara_render_ref.py ----
# Reference Niagara renderer maths in Blender space (spec N-O5 sections 5-7; Gate R0 oracle). numpy only, no bpy.
#
# UE formulas are evaluated on Blender-space vectors (UE (x,y,z) -> (x,-y,z)); every UE cross product becomes cross_ue(a,b) = -cross(a,b).
# Sprite corners map directly (corner_b = M corner_ue). Mesh frames are conjugated (M R M), so the facing frame's Y column is negated.

E1_CYCLES=1.0/(4.0*math.pi)   # Blender point-light irradiance per Watt at 1 m (Cycles; measured 0.07965 in the N6 dry run); Gate R0 re-measures it
UE_CM2_PER_M2=1.0e4
X_AXIS,Z_AXIS=np.array([1.0,0.0,0.0]),np.array([0.0,0.0,1.0])

def read_ply(path):
    data=Path(path).read_bytes();end=data.index(b'end_header\n')+len(b'end_header\n');lines=data[:end].decode('ascii').splitlines()
    if lines[1]!='format binary_little_endian 1.0':raise ValueError('ply_format')
    n=int(lines[2].split()[2]);props=[l.split()[2] for l in lines if l.startswith('property float ')]
    if n<0 or not props or len(set(props))!=len(props) or len(data)-end!=n*len(props)*4:raise ValueError('ply_array_size_or_schema')
    rows=np.frombuffer(data,dtype='<f4',count=n*len(props),offset=end).reshape(n,len(props));return {p:rows[:,i].copy() for i,p in enumerate(props)}

def cross_ue(a,b):return -np.cross(a,b)
def safe_normalize(v,eps):
    L=np.linalg.norm(v,axis=-1,keepdims=True);return v/np.maximum(L,eps)
def normalize(v):
    L=np.linalg.norm(v,axis=-1,keepdims=True);return np.where(L>0,v/np.where(L>0,L,1),0.0)
def vec(cols,prefix,n):return np.stack([cols[f'{prefix}_{c}'].astype(np.float64) for c in range(n)],axis=1)
def scalar(cols,prefix):return cols[prefix].astype(np.float64)

def camera_rel(cam_world,rig_world):
    """Camera in rig space: position and unit axes (X right, Y up, Z backward)."""
    rel=np.linalg.inv(np.asarray(rig_world,dtype=np.float64))@np.asarray(cam_world,dtype=np.float64);axes=rel[:3,:3]/np.linalg.norm(rel[:3,:3],axis=0,keepdims=True)
    return {'pos':rel[:3,3],'x':axes[:,0],'y':axes[:,1],'z':axes[:,2]}

def visible_rows(cols,props):
    keep=cols['nx_valid']>0.5
    if 'vistag' in cols:keep&=np.rint(cols['vistag']).astype(np.int64)==int(props.get('RendererVisibility',0))
    return np.nonzero(keep)[0]

def view_sort(P,cam,mode):
    if mode=='ViewDistance':key=np.linalg.norm(P-cam['pos'],axis=1)
    elif mode=='ViewDepth':key=(P-cam['pos'])@(-cam['z'])
    else:return np.arange(len(P))
    return np.argsort(-key,kind='stable')

def sprite_frame(cols,info,cam):
    """Per drawn sprite (in draw order): corners [N,4,3] for (u,v) = (0,0),(1,0),(1,1),(0,1); packed instancer attributes."""
    pr=info['props'];rows=visible_rows(cols,pr);c={k:v[rows] for k,v in cols.items()}
    P=np.stack([c['x'],c['y'],c['z']],axis=1).astype(np.float64);order=view_sort(P,cam,pr.get('SortMode','None'));c={k:v[order] for k,v in c.items()};P=P[order];n=len(P)
    S=vec(c,'size',2);theta=scalar(c,'rot')*math.pi/180.0;D=safe_normalize(cam['pos']-P,1e-8);CO=D*scalar(c,'camoff')[:,None]
    V=vec(c,'vel',3);Vl=np.linalg.norm(V,axis=1,keepdims=True);Vhat=np.where(Vl>1e-8,V/np.where(Vl>0,Vl,1),Z_AXIS)
    F=safe_normalize(vec(c,'facing',3),1e-6);A=safe_normalize(vec(c,'align',3),1e-6)
    align,facing=pr['Alignment_resolved'],pr['FacingMode_resolved'];camX,camY,camZ=(np.tile(cam[k],(n,1)) for k in ('x','y','z'))
    if align=='Unaligned':
        if facing=='FaceCamera':R,U=camX,-camY
        elif facing=='FaceCameraPlane':R=safe_normalize(cross_ue(camZ,camY),1e-6);U=cross_ue(camZ,R)
        elif facing=='CustomFacingVector':R=safe_normalize(cross_ue(F,camY),1e-6);U=cross_ue(F,R)
        elif facing=='FaceCameraPosition':R=safe_normalize(cross_ue(D,np.tile(Z_AXIS,(n,1))),1e-6);U=cross_ue(D,R)
        else:raise ValueError('facing_unsupported:'+facing)
    else:
        Dir=Vhat if align=='VelocityAligned' else A;CF=F if facing=='CustomFacingVector' else D
        R=safe_normalize(cross_ue(CF,Dir),1e-6);U=cross_ue(CF,R) if facing=='CustomFacingVector' else -Dir
    cs,sn=np.cos(theta)[:,None],np.sin(theta)[:,None];OutR=R*cs+U*sn;OutU=U*cs-R*sn
    Pv=vec(c,'pivot',2);aS=np.abs(S);uv=np.array([[0,0],[1,0],[1,1],[0,1]],dtype=np.float64)
    corners=(P+CO)[:,None,:]+(aS[:,0:1]*(uv[None,:,0]-Pv[:,0:1]))[:,:,None]*OutR[:,None,:]+(aS[:,1:2]*(uv[None,:,1]-Pv[:,1:2]))[:,:,None]*OutU[:,None,:]
    blend=1.0 if pr.get('bSubImageBlend',True) else 0.0;sub=pr.get('SubImageSize',[1.0,1.0]);uvs=vec(c,'uvs',2)
    attrs={'nx_color':vec(c,'col',4),'nx_dyn0':vec(c,'dyn0',4),'nx_dyn1':vec(c,'dyn1',4),'nx_dyn2':vec(c,'dyn2',4),'nx_dyn3':vec(c,'dyn3',4),
           'nx_misc':np.stack([scalar(c,'subimg'),scalar(c,'nage'),scalar(c,'matrand'),np.full(n,blend)],axis=1),
           'nx_uvx':np.stack([uvs[:,0]*np.sign(S[:,0]),uvs[:,1]*np.sign(S[:,1]),np.full(n,float(sub[0])),np.full(n,float(sub[1]))],axis=1)}
    return {'rows':rows[order],'corners':corners,'quad_uv':uv,'attrs':attrs}

def subuv(quad_uv,misc,uvx):
    """UE SubUV texture coordinates (UE v-down convention) for corners quad_uv [K,2] of instances with nx_misc/nx_uvx [N,4]."""
    idx=misc[:,0];lerp=idx-np.floor(idx);a=idx-lerp+0.5;b=a+1.0;sx,sy=uvx[:,2],uvx[:,3]
    uvT=0.5+(quad_uv[None,:,:]-0.5)*uvx[:,None,0:2]
    def tex(k):
        h=np.floor(np.fmod(k,sx));v=np.floor(k/sx);return (np.stack([h,v],axis=1)[:,None,:]+uvT)/np.stack([sx,sy],axis=1)[:,None,:]
    return tex(a),tex(b),lerp*misc[:,3]

def quat_mul(a,b):
    """Hamilton product (NiagaraQuatMul); quaternions as (x,y,z,w)."""
    av,aw,bv,bw=a[...,:3],a[...,3:],b[...,:3],b[...,3:];return np.concatenate([bv*aw+av*bw+np.cross(av,bv),aw*bw-np.sum(av*bv,axis=-1,keepdims=True)],axis=-1)
def quat_matrix(q):
    """Column-vector rotation matrices [N,3,3] for unit quaternions (x,y,z,w)."""
    x,y,z,w=(q[...,i] for i in range(4))
    return np.stack([np.stack([1-2*(y*y+z*z),2*(x*y-w*z),2*(x*z+w*y)],-1),np.stack([2*(x*y+w*z),1-2*(x*x+z*z),2*(y*z-w*x)],-1),np.stack([2*(x*z-w*y),2*(y*z+w*x),1-2*(x*x+y*y)],-1)],-2)

def mesh_frame(cols,info,cam,mesh_k):
    """Instance matrices [N,4,4] (rig space) for mesh entry mesh_k, in draw order; vertex_b = T + Rot (S * v_b)."""
    pr=info['props'];rows=visible_rows(cols,pr);entry=info['meshes'][mesh_k]
    if info.get('mesh_index_bound'):rows=rows[np.rint(cols['meshidx'][rows]).astype(np.int64)==mesh_k]
    c={k:v[rows] for k,v in cols.items()};P=np.stack([c['x'],c['y'],c['z']],axis=1).astype(np.float64);n=len(P)
    q=vec(c,'quat',4);q=q/np.maximum(np.linalg.norm(q,axis=1,keepdims=True),1e-30)
    mq=np.array(entry['rotation_quat_ue'],dtype=np.float64)*np.array([-1,1,-1,1]);Rot=quat_matrix(quat_mul(q,np.tile(mq,(n,1))))
    mode=pr.get('FacingMode','Default');D=safe_normalize(cam['pos']-P,1e-8)
    if mode!='Default':
        if mode=='Velocity':
            V=vec(c,'vel',3);Vl=np.linalg.norm(V,axis=1,keepdims=True);F=np.where(Vl>0,V/np.where(Vl>0,Vl,1),Z_AXIS)
        elif mode=='CameraPosition':F=D
        else:F=np.tile(cam['z'],(n,1))
        if pr.get('bLockedAxisEnable'):
            L=np.array(pr.get('LockedAxis',[0,0,1]),dtype=np.float64)*np.array([1,-1,1]);Lt=np.tile(L,(n,1))
            near=np.abs(F@L)>0.99;F=np.where(near[:,None],X_AXIS if abs(L[2])>0.99 else Z_AXIS,F)
            Zc=Lt;Yc=normalize(cross_ue(Zc,F));Xc=cross_ue(Yc,Zc)
        else:
            if mode=='CameraPlane':Ref=np.tile(cam['y'],(n,1))
            else:
                dz=F@Z_AXIS;Ref=np.where((np.abs(dz)>0.99)[:,None],(-np.sign(dz))[:,None]*X_AXIS,Z_AXIS)
            Xc=F;Yc=normalize(cross_ue(Ref,F));Zc=cross_ue(Xc,Yc)
        Fm=np.stack([Xc,-Yc,Zc],axis=-1);Rot=Fm@Rot
    pscale=vec(c,'scale',3);S=pscale*np.array(entry['scale'],dtype=np.float64)
    T=P+D*scalar(c,'camoff')[:,None];piv=np.array(entry['pivot_offset'],dtype=np.float64)*np.array([0.01,-0.01,0.01])
    T=T+(np.einsum('nij,nj->ni',Rot,piv*pscale) if entry.get('pivot_space','Mesh')=='Mesh' else piv)
    M=np.tile(np.eye(4),(n,1,1));M[:,:3,:3]=Rot*S[:,None,:];M[:,:3,3]=T
    return {'rows':rows,'matrices':M}

def light_frame(cols,info,e1=E1_CYCLES):
    """Drawn lights in row order: uid, position, Blender colour/energy, radius (m), exponent, scales."""
    pr=info['props'];keep=(cols['nx_valid']>0.5)&(cols['enabled']!=0)&(np.rint(cols['vistag']).astype(np.int64)==int(pr.get('RendererVisibility',0)))
    radius=cols['radius'].astype(np.float64)*float(pr.get('RadiusScale',1.0));keep&=radius>0;rows=np.nonzero(keep)[0];out=[]
    for i in rows:
        col=np.array([cols['col_%d'%k][i] for k in range(4)],dtype=np.float64);C=col[:3]*(col[3] if pr.get('bAlphaScalesBrightness') else 1.0)+np.array(pr.get('ColorAdd',[0,0,0]),dtype=np.float64)
        peak=float(max(C.max(),0.0));color=C/peak if peak>0 else np.ones(3)
        out.append({'uid':int(cols['uid'][i]),'pos':np.array([cols['x'][i],cols['y'][i],cols['z'][i]],dtype=np.float64),'color':color,'energy':peak/(UE_CM2_PER_M2*e1),
                    'radius':float(radius[i]),'exponent':0.0 if pr.get('bUseInverseSquaredFalloff',True) else float(cols['exponent'][i]),
                    'volume':float(cols['volscat'][i]),'specular':float(cols['spec'][i]),'diffuse':float(cols['diff'][i])})
    return out

def ribbon_frame(mesh_cols,info,cam):
    """Ribbon vertex positions (rig space) in mesh-PLY vertex order, sentinel dropped (NiagaraRibbonVertexFactory.ush 533-640, 859-866)."""
    pr=info['props'];keep=np.abs(mesh_cols['sl_nx'])+np.abs(mesh_cols['sl_ny'])>=0.5;c={k:v[keep].astype(np.float64) for k,v in mesh_cols.items()};n=int(keep.sum())
    P=np.stack([c['x'],c['y'],c['z']],axis=1);Dir=vec(c,'dir',3)
    custom=pr.get('FacingMode','Screen') in ('Custom','CustomSideVector')
    F=normalize(vec(c,'facing',3) if custom else cam['pos']-P);R=normalize(cross_ue(F,Dir))
    if pr.get('FacingMode')=='CustomSideVector':R,F=-F,R
    if pr.get('twist_bound'):
        tw=scalar(c,'twist')[:,None];cs,sn=np.cos(tw),np.sin(tw);R,F=cs*R+sn*F,-sn*R+cs*F
    up=cross_ue(Dir,R);off=R*scalar(c,'sl_px')[:,None]+up*scalar(c,'sl_py')[:,None]
    attrs={'nx_color':vec(c,'col',4),'nx_uv':np.stack([c['uv0_0'],c['uv0_1'],c['uv1_0'],c['uv1_1']],axis=1),'nx_misc':np.stack([np.zeros(n),c['nage'],c['matrand'],np.zeros(n)],axis=1)}
    return {'positions':P+off*scalar(c,'width')[:,None],'attrs':attrs}


def assign_slots(frames):
    """Stable light-pool slots: frames is a list of lists of uids; returns (slot lists per frame, pool size)."""
    live={};free=[];size=0;result=[]
    for uids in frames:
        for uid in [u for u in live if u not in set(uids)]:free.append(live.pop(uid))
        free.sort();slots=[]
        for uid in uids:
            if uid not in live:
                if free:live[uid]=free.pop(0)
                else:live[uid]=size;size+=1
            slots.append(live[uid])
        result.append(slots)
    return result,size

# ---- niagara_ribbons.py ----
# Niagara ribbon geometry generation (spec N-O5b): a port of FNiagaraRendererRibbons CPU generation (UE 5.7).
#
# Runs on Blender-space particle rows (cache stream, already converted); UE length constants are scaled by 0.01. Generation has no
# cross products, so the Y mirror commutes with it. View-dependent parts (screen facing, right vector, twist, width offset) are left
# to the GN rig / niagara_render_ref.ribbon_vertices.


ribbons_UNIT = 0.01
MIN_SEGMENT_LENGTH = 1.0 * ribbons_UNIT          # GNiagaraRibbonMinSegmentLength (cm)
MIN_DISPLACEMENT_ERROR = 0.5 * ribbons_UNIT      # GNiagaraRibbonTessellationMinDisplacementError (cm)
MAX_TESSELLATION = 16                    # GNiagaraRibbonMaxTessellation
DEFAULT_MIN_ANGLE = 15.0 * math.pi / 180.0
SMALL_NUMBER = 1e-8
MAX_CURVATURE_FACTOR = 0.002
UV_DEFAULT = {'DistributionMode': 'ScaledUsingRibbonSegmentLength', 'LeadingEdgeMode': 'Locked', 'TrailingEdgeMode': 'Locked',
              'bEnablePerParticleUOverride': False, 'bEnablePerParticleVRangeOverride': False, 'TilingLength': 100.0,
              'Offset': [0.0, 0.0], 'Scale': [1.0, 1.0]}


def acos_fast(x):
    """AcosFast in NiagaraRendererRibbons.cpp:192 (Eberly degree 1)."""
    xa = abs(x)
    r = (-0.156583 * xa + 0.5 * math.pi) * math.sqrt(max(0.0, 1.0 - xa))
    return r if x >= 0 else math.pi - r


def slice_vertices(props):
    """Slice vertex table (position xy, normal xy, texture v) and triangle-pair vertex ids (NiagaraRendererRibbons.cpp 1258-1420)."""
    shape = props.get('Shape', 'Plane')
    w = int(props.get('WidthSegmentationCount', 1))
    verts, pairs = [], []

    def rot(v, deg):
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)

    if shape == 'MultiPlane':
        n, accurate = int(props.get('MultiPlaneCount', 2)), bool(props.get('bEnableAccurateGeometry', False))
        for side in ((1,) if not accurate else (1, -1)):
            for p in range(n):
                ang = p / n * 180.0
                for i in range(w + 1):
                    verts.append(rot((i / w - 0.5, 0.0), ang) + rot((0.0, float(side)), ang) + (i / w,))
        front = n * (w + 1)
        for p in range(n):
            base = p * (w + 1)
            pairs += [(base + i, base + i + 1) for i in range(w)]
            if accurate:
                pairs += [(front + base + i + 1, front + base + i) for i in range(w)]
    elif shape == 'Tube':
        t = int(props.get('TubeSubdivisions', 3))
        for i in range(t + 1):
            ang = i / t * -360.0
            verts.append(rot((-0.5, 0.0), ang) + rot((-1.0, 0.0), ang) + (i / t,))
        pairs = [(i, i + 1) for i in range(t)]
    elif shape == 'Plane':
        for i in range(w + 1):
            verts.append((i / w - 0.5, 0.0, 0.0, 1.0, i / w))
        pairs = [(i, i + 1) for i in range(w)]
    else:
        raise ValueError('ribbon_shape_unsupported:' + shape)
    return verts, pairs


def link_order(cols, props):
    """Particle order along the ribbon (NiagaraRibbonRendererProperties.cpp 410-424, NiagaraRendererRibbons.cpp 743-752)."""
    n = len(cols['x'])
    if props.get('link_order_source') == 'RibbonLinkOrder':
        return np.argsort(cols['linkorder'], kind='stable')
    if props.get('link_order_source') == 'UniqueID':
        return np.argsort(-cols['uid'].astype(np.int64), kind='stable')
    return np.argsort(cols['nage'], kind='stable') if n else np.zeros(0, int)


class Smoothing:
    """FNiagaraRendererRibbons::TessellationSmoothingData, carried frame to frame."""

    def __init__(self):
        self.angle = self.curvature = self.twist_angle = self.twist_curvature = self.total = 0.0


def ribbon_part(P, twist, width, tension, want_tess, has_twist, stats):
    """GenerateVertexBufferForRibbonPart for one ribbon: returns (kept row indices in order, tangents [K,3], distances [K])."""
    idx, tangents, dists = [], [], []
    n = len(P)
    if n < 2:
        return idx, np.zeros((0, 3)), np.zeros(0)
    first = P[0]
    curr = first
    last_vec = np.zeros(3)
    last_size = 0.0
    last_twist = last_width = 0.0
    total = 0.0
    ci = 1
    while ci < n:
        curr = P[ci]
        last_vec = curr - first
        last_size = float(np.linalg.norm(last_vec))
        if has_twist:
            last_twist, last_width = twist[ci], width[ci]
        if last_size > MIN_SEGMENT_LENGTH:
            last_vec = last_vec * (1.0 / last_size)
            idx.append(0)
            tangents.append(last_vec.copy())
            dists.append(0.0)
            break
        last_size = 0.0
        ci += 1
    ni = ci + 1
    while ni < n:
        nxt = P[ni]
        vec = nxt - curr
        size = float(np.linalg.norm(vec))
        nt = nw = 0.0
        if has_twist:
            nt, nw = twist[ni], width[ni]
        if size > MIN_SEGMENT_LENGTH or ni == n - 1:
            vec = vec * (1.0 / max(MIN_SEGMENT_LENGTH, size))
            s = last_vec + vec
            L = float(np.linalg.norm(s))
            tangent = (1.0 - tension) * (s / L if L * L > SMALL_NUMBER else np.zeros(3))  # GetSafeNormal: |v|^2 > SMALL_NUMBER
            total += last_size
            idx.append(ci)
            tangents.append(tangent)
            dists.append(total)
            stats['total'] += size
            if want_tess:
                stats['len'] += size * size
                stats['angle'] += size * acos_fast(float(np.clip(np.dot(last_vec, vec), -1.0, 1.0)))
                if has_twist:
                    stats['twist'] += size * abs(nt - last_twist)
                    stats['width'] += size * last_width
            ci = ni
            curr = nxt
            last_vec = vec
            last_size = size
            last_twist, last_width = nt, nw
        ni += 1
    if last_size > 0:
        total += last_size
        idx.append(ci)
        tangents.append(last_vec.copy())
        dists.append(total)
    T = np.array(tangents, dtype=np.float64).reshape(-1, 3)
    if len(idx) > 2:
        T[0] = 2.0 * np.dot(T[0], T[1]) * T[0] - T[1]
        T[-1] = 2.0 * np.dot(T[-1], T[-2]) * T[-1] - T[-2]
    return idx, T, np.array(dists, dtype=np.float64)


def uv_scale_offset(settings, nage_sorted, dists, num_segments, total):
    """CalculateUVScaleAndOffsets (NiagaraRendererRibbons.cpp 1451-1544); lengths in Blender units (TilingLength scaled)."""
    lead = 0.0
    if settings['LeadingEdgeMode'] == 'SmoothTransition':
        step = nage_sorted[1] - nage_sorted[0]
        off = step - nage_sorted[0] if nage_sorted[0] < step else 0.0
        lead = off / step if step > 0 else 0.0
    trail = 0.0
    if settings['TrailingEdgeMode'] == 'SmoothTransition':
        step = nage_sorted[-1] - nage_sorted[-2]
        off = step - (1 - nage_sorted[-1]) if 1 - nage_sorted[-1] < step else 0.0
        trail = off / step if step > 0 else 0.0
    mode = settings['DistributionMode']
    tiling = float(settings['TilingLength']) * ribbons_UNIT
    if mode == 'ScaledUniformly':
        avail = num_segments - (lead + trail)
        scale = num_segments / avail
        offset = -((lead / num_segments) * scale)
        scaler = 1.0 / num_segments
    elif mode == 'ScaledUsingRibbonSegmentLength':
        lead_d = dists[1] * lead
        trail_d = (dists[-1] - dists[-2]) * trail
        avail = total - (lead_d + trail_d)
        scale = total / avail
        offset = -((lead_d / total) * scale)
        scaler = 1.0 / total
    elif mode == 'TiledOverRibbonLength':
        scale = total / tiling
        offset = -((dists[1] * lead) / tiling)
        scaler = 1.0 / total
    elif mode == 'TiledFromStartOverRibbonLength':
        scale = total / tiling
        offset = 0.0
        scaler = 1.0 / total
    else:
        raise ValueError('ribbon_uv_mode:' + mode)
    sx, ox = float(settings['Scale'][0]), float(settings['Offset'][0])
    return scale * sx, offset * sx + ox, scaler


def tessellation_factor(props, sm):
    """CalculateTessellationFactor with ViewDistance = 0 (MaxDisplacementError = its minimum; spec N-O5 section 16)."""
    mode = props.get('TessellationMode', 'Automatic')
    factor, min_angle, constant = MAX_TESSELLATION, DEFAULT_MIN_ANGLE, False
    if mode == 'Custom':
        factor = min(factor, int(props.get('TessellationFactor', 16)))
        constant = bool(props.get('bUseConstantFactor', False))
        a = float(props.get('TessellationAngle', 15.0))
        min_angle = (1.0 if 0.0 < a < 1.0 else a) * math.pi / 180.0
    elif mode == 'Disabled':
        factor = 1
    if constant:
        return factor
    if factor > 1 and sm.curvature > SMALL_NUMBER:
        min_tess = float(factor) if min_angle == 0.0 else max(1.0, max(sm.twist_angle, sm.angle) / max(SMALL_NUMBER, min_angle))
        err = MIN_DISPLACEMENT_ERROR
        tess = sm.angle / max(MAX_CURVATURE_FACTOR, acos_fast(sm.curvature / (sm.curvature + err)))
        if sm.twist_angle > 0 and sm.twist_curvature > 0:
            tess = max(tess, sm.twist_angle / max(MAX_CURVATURE_FACTOR, acos_fast(sm.twist_curvature / (sm.twist_curvature + err))))
        return int(min(max(int(math.floor(tess + 0.5)), int(math.floor(min_tess + 0.5))), factor))
    return 1


def generate(cols, props, sm):
    """One frame: returns dict of per-vertex arrays (Blender space) and quad faces, or None when nothing is drawn. Updates sm."""
    keep = np.nonzero(cols['nx_valid'] > 0.5)[0]
    c = {k: v[keep].astype(np.float64) for k, v in cols.items()}
    n = len(keep)
    mode = props.get('TessellationMode', 'Automatic')
    constant = mode == 'Custom' and bool(props.get('bUseConstantFactor', False))
    want_tess = mode != 'Disabled' and not constant
    has_twist = bool(props.get('twist_bound'))
    tension = float(props.get('CurveTension', 0.0))
    groups = [np.arange(n)]
    if props.get('ribbon_id_bound') and n:
        # TMap keyed by FNiagaraID (or int), KeySort(TLess): Index, then AcquireTag
        ids = np.stack([c['rid_0'], c['rid_1']], axis=1)
        keys = sorted({(a, b) for a, b in ids})
        groups = [np.nonzero((ids[:, 0] == a) & (ids[:, 1] == b))[0] for a, b in keys]
    stats = {'total': 0.0, 'len': 0.0, 'angle': 0.0, 'twist': 0.0, 'width': 0.0}
    ribbons = []
    for g in groups:
        sub = {k: v[g] for k, v in c.items()}
        order = link_order(sub, props)
        rows = g[order]
        P = np.stack([c['x'][rows], c['y'][rows], c['z'][rows]], axis=1)
        kept, T, D = ribbon_part(P, c['twist'][rows], c['width'][rows], tension, want_tess, has_twist, stats)
        if len(kept) >= 2:
            ribbons.append((rows[np.array(kept)], T, D))
    if stats['total'] > 0.0:
        inv = 1.0 / max(1.0 * ribbons_UNIT, stats['total'])
        avg_factor = sm.total / (stats['total'] + sm.total)
        sm.total = stats['total']
        ang = stats['angle'] * inv
        seg = stats['len'] * inv
        curv = seg / max(SMALL_NUMBER, abs(math.sin(ang)))
        sm.angle = ang + (max(sm.angle, ang) - ang) * avg_factor
        sm.curvature = curv + (max(sm.curvature, curv) - curv) * avg_factor
        if has_twist:
            tw, wd = stats['twist'] * inv, stats['width'] * inv
            sm.twist_angle = tw + (max(sm.twist_angle, tw) - tw) * avg_factor
            sm.twist_curvature = wd + (max(sm.twist_curvature, wd) - wd) * avg_factor
    else:
        sm.__init__()
    if not ribbons:
        return None
    nsub = tessellation_factor(props, sm) if (want_tess or constant) else 1
    sv, pairs = slice_vertices(props)
    uv_settings = [dict(UV_DEFAULT, **(props.get('UV0Settings') or {})), dict(UV_DEFAULT, **(props.get('UV1Settings') or {}))]
    out = {k: [] for k in ('center', 'dir', 'facing', 'twist', 'width', 'slice', 'col', 'dyn0', 'dyn1', 'dyn2', 'dyn3', 'nage', 'matrand', 'uv0', 'uv1', 'interp')}
    faces = []
    base = 0
    for rows, T, D in ribbons:
        nseg = len(rows) - 1
        total = D[-1]
        nage_sorted = c['nage'][rows]
        uvp = []
        for k, st in enumerate(uv_settings):
            override = st.get('bEnablePerParticleUOverride') and props.get('u%d_override_bound' % k)
            uvp.append((1.0, 0.0, 1.0) if override else uv_scale_offset(st, nage_sorted, D, nseg, total))
        interp = [(s, a / nsub) for s in range(nseg) for a in range(nsub)] + [(nseg, 0.0)]
        for s, alpha in interp:
            i0, i1 = s, min(s + 1, nseg)
            r0, r1 = rows[i0], rows[i1]
            P0 = np.array([c['x'][r0], c['y'][r0], c['z'][r0]])
            P1 = np.array([c['x'][r1], c['y'][r1], c['z'][r1]])
            ts = D[i1] - D[i0]
            a2, a3 = alpha * alpha, alpha * alpha * alpha
            center = (2 * a3 - 3 * a2 + 1) * P0 + ts * (a3 - 2 * a2 + alpha) * T[i0] + ts * (a3 - a2) * T[i1] + (-2 * a3 + 3 * a2) * P1
            d = T[i0] + (T[i1] - T[i0]) * alpha
            dl = float(np.linalg.norm(d))
            direction = d / dl if dl > 0 else np.zeros(3)
            lerp = lambda key, w=None: c[key][r0] + (c[key][r1] - c[key][r0]) * alpha
            vec = lambda p, m: np.array([lerp('%s_%d' % (p, j)) for j in range(m)])
            dist_on = D[i0] + (D[i1] - D[i0]) * alpha
            us = []
            for k, st in enumerate(uv_settings):
                scale, offset, scaler = uvp[k]
                if st.get('bEnablePerParticleUOverride') and props.get('u%d_override_bound' % k):
                    u = lerp('u%dovr' % k)
                elif st['DistributionMode'] == 'ScaledUniformly':
                    u = (s + alpha) * scaler
                elif st['DistributionMode'] in ('ScaledUsingRibbonSegmentLength', 'TiledOverRibbonLength'):
                    u = dist_on * scaler
                elif props.get('uvdist_bound'):
                    u = lerp('uvdist') * scaler
                else:
                    u = 0.0
                us.append((u, scale, offset, k))
            for (px, py, nx, ny, tv) in sv:
                out['center'].append(center)
                out['dir'].append(direction)
                out['facing'].append(vec('facing', 3))
                out['twist'].append(lerp('twist'))
                out['width'].append(lerp('width'))
                out['slice'].append((px, py, nx, ny))
                out['col'].append(vec('col', 4))
                for k in range(4):
                    out['dyn%d' % k].append(vec('dyn%d' % k, 4))
                out['nage'].append(lerp('nage'))
                out['matrand'].append(lerp('matrand'))
                for (u, scale, offset, k) in us:
                    st = uv_settings[k]
                    v = tv
                    if st.get('bEnablePerParticleVRangeOverride') and props.get('v%d_range_bound' % k):
                        lo, hi = lerp('v%drng_0' % k), lerp('v%drng_1' % k)
                        v = (1 - tv * lo) + tv * hi
                    out['uv%d' % k].append((u * scale + offset, v * float(st['Scale'][1]) + float(st['Offset'][1])))
                out['interp'].append((s, alpha))
        nslice = len(sv)
        for j in range(len(interp) - 1):
            a0, a1 = base + j * nslice, base + (j + 1) * nslice
            for f, s2 in pairs:
                faces.append((a0 + f, a0 + s2, a1 + s2, a1 + f))
        base += len(interp) * nslice
    res = {k: np.array(v, dtype=np.float64) for k, v in out.items()}
    res['faces'] = np.array(faces, dtype=np.int64).reshape(-1, 4)
    res['nsub'] = nsub
    return res


MESH_PROPS = ['x', 'y', 'z', 'dir_0', 'dir_1', 'dir_2', 'facing_0', 'facing_1', 'facing_2', 'twist', 'width', 'sl_px', 'sl_py', 'sl_nx', 'sl_ny',
              'col_0', 'col_1', 'col_2', 'col_3'] + ['dyn%d_%d' % (k, j) for k in range(4) for j in range(4)] + ['nage', 'matrand', 'uv0_0', 'uv0_1', 'uv1_0', 'uv1_1']


def mesh_ply_bytes(mesh):
    """Binary PLY with per-vertex MESH_PROPS and quad faces; an empty frame is a single degenerate sentinel vertex with no faces."""
    if mesh is None:
        rows = np.zeros((1, len(MESH_PROPS)), dtype='<f4')
        faces = np.zeros((0, 4), dtype=np.int64)
    else:
        rows = np.concatenate([mesh['center'], mesh['dir'], mesh['facing'], mesh['twist'][:, None], mesh['width'][:, None], mesh['slice'], mesh['col'],
                               mesh['dyn0'], mesh['dyn1'], mesh['dyn2'], mesh['dyn3'], mesh['nage'][:, None], mesh['matrand'][:, None], mesh['uv0'], mesh['uv1']],
                              axis=1).astype('<f4')
        faces = mesh['faces']
    head = ('ply\nformat binary_little_endian 1.0\nelement vertex %d\n' % len(rows) + ''.join('property float %s\n' % p for p in MESH_PROPS)
            + 'element face %d\nproperty list uchar int vertex_indices\nend_header\n' % len(faces)).encode('ascii')
    packed = np.zeros(len(faces), dtype=[('n', 'u1'), ('i', '<i4', (4,))])
    packed['n'] = 4
    packed['i'] = faces
    return head + np.ascontiguousarray(rows).tobytes() + packed.tobytes()


def read_mesh_ply(path):
    from pathlib import Path
    data = Path(path).read_bytes()
    end = data.index(b'end_header\n') + len(b'end_header\n')
    lines = data[:end].decode('ascii').splitlines()
    nv = int(lines[2].split()[2])
    props = [l.split()[2] for l in lines if l.startswith('property float ')]
    nf = int(next(l for l in lines if l.startswith('element face')).split()[2])
    rows = np.frombuffer(data, dtype='<f4', count=nv * len(props), offset=end).reshape(nv, len(props))
    off = end + nv * len(props) * 4
    raw = np.frombuffer(data, dtype=np.uint8, count=nf * 17, offset=off).reshape(nf, 17)
    faces = raw[:, 1:].copy().view('<i4').reshape(nf, 4)
    return {p: rows[:, i].copy() for i, p in enumerate(props)}, faces

# ---- niagara_rigs.py ----
# Blender playback rigs for Niagara caches (spec N-O5 sections 5-7, 9). Requires bpy; maths mirrors niagara_render_ref.py.
#
# build_system(cache_dir) creates a root empty at the bake origin with one rig object per sprite/mesh stream (Geometry Nodes reading
# f_####.ply per frame) and a keyframed point-light pool per light stream. Every rig modifier has a 'Realize' input for Gate R0.



PREVIEW_MATERIAL = 'DR_NX_Preview'
PLACEHOLDER_MESH = 'DR_NX_MeshPlaceholder'


class G:
    """Small node-building helper for one GeometryNodeTree."""

    def __init__(self, tree):
        self.t, self.n, self.l = tree, tree.nodes, tree.links

    def node(self, idname, **props):
        node = self.n.new(idname)
        for key, value in props.items():
            setattr(node, key, value)
        return node

    def link(self, a, b):
        self.l.new(a, b)
        return b

    def _in(self, node, i, value):
        sock = node.inputs[i]
        if isinstance(value, bpy.types.NodeSocket):
            self.link(value, sock)
        else:
            sock.default_value = value

    def attr(self, name, data_type='FLOAT'):
        node = self.node('GeometryNodeInputNamedAttribute', data_type=data_type)
        node.inputs['Name'].default_value = name
        return node.outputs['Attribute']

    def math(self, op, a, b=0.0, c=None):
        node = self.node('ShaderNodeMath', operation=op)
        self._in(node, 0, a)
        self._in(node, 1, b)
        if c is not None:
            self._in(node, 2, c)
        return node.outputs[0]

    def vmath(self, op, a, b=(0.0, 0.0, 0.0), scale=None):
        node = self.node('ShaderNodeVectorMath', operation=op)
        self._in(node, 0, a)
        self._in(node, 1, b)
        if scale is not None:
            self._in(node, 'Scale', scale)
        return node.outputs['Value' if op in ('LENGTH', 'DOT_PRODUCT') else 'Vector']

    def cross_ue(self, a, b):
        return self.vmath('SCALE', self.vmath('CROSS_PRODUCT', a, b), scale=-1.0)

    def combine(self, x, y, z):
        node = self.node('ShaderNodeCombineXYZ')
        for i, v in enumerate((x, y, z)):
            self._in(node, i, v)
        return node.outputs[0]

    def vec_attr(self, prefix, n):
        comps = [self.attr('%s_%d' % (prefix, c)) for c in range(n)] + [0.0] * (3 - n)
        return self.combine(*comps[:3])

    def color_attr(self, prefixes):
        node = self.node('FunctionNodeCombineColor')
        for i, p in enumerate(prefixes):
            self._in(node, i, self.attr(p) if isinstance(p, str) else p)
        return node.outputs[0]

    def switch(self, input_type, cond, false, true):
        node = self.node('GeometryNodeSwitch', input_type=input_type)
        self._in(node, 'Switch', cond)
        self._in(node, 'False', false)
        self._in(node, 'True', true)
        return node.outputs[0]

    def store(self, geo, name, data_type, value, domain='POINT'):
        node = self.node('GeometryNodeStoreNamedAttribute', data_type=data_type, domain=domain)
        node.inputs['Name'].default_value = name
        self.link(geo, node.inputs['Geometry'])
        self._in(node, 'Value', value)
        return node.outputs[0]

    def rotate_vector(self, v, rot):
        node = self.node('FunctionNodeRotateVector')
        self._in(node, 'Vector', v)
        self._in(node, 'Rotation', rot)
        return node.outputs[0]


def _new_tree(name):
    tree = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    tree.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    sock = tree.interface.new_socket('Realize', in_out='INPUT', socket_type='NodeSocketBool')
    sock.default_value = False
    return tree


def _stream_points(g, stream_dir, frame_start, frame_end, props, prefix='f_', ribbon=False):
    """Import PLY for the clamped scene frame; empty outside the cache range; drop sentinels and hidden visibility tags."""
    time = g.node('GeometryNodeInputSceneTime')
    clamp = g.node('ShaderNodeClamp')
    g.link(time.outputs['Frame'], clamp.inputs['Value'])
    clamp.inputs['Min'].default_value = float(frame_start)
    clamp.inputs['Max'].default_value = float(frame_end)
    to_int = g.node('FunctionNodeFloatToInt', rounding_mode='ROUND')
    g.link(clamp.outputs[0], to_int.inputs[0])
    fmt = g.node('FunctionNodeFormatString')
    fmt.inputs['Format'].default_value = str(stream_dir).replace('\\', '/') + '/' + prefix + '{frame:04}.ply'
    fmt.format_items.new('INT', 'frame')
    g.link(to_int.outputs[0], fmt.inputs['frame'])
    imp = g.node('GeometryNodeImportPLY')
    g.link(fmt.outputs[0], imp.inputs['Path'])
    below = g.math('LESS_THAN', time.outputs['Frame'], float(frame_start) - 0.5)
    above = g.math('GREATER_THAN', time.outputs['Frame'], float(frame_end) + 0.5)
    outside = g.math('MAXIMUM', below, above)
    if ribbon:  # ribbon mesh sentinel: the only vertex with a zero slice normal
        hidden = g.math('LESS_THAN', g.math('ADD', g.math('ABSOLUTE', g.attr('sl_nx')), g.math('ABSOLUTE', g.attr('sl_ny'))), 0.5)
    else:
        hidden = g.math('LESS_THAN', g.attr('nx_valid'), 0.5)
        tag = g.math('ROUND', g.attr('vistag'))
        hidden = g.math('MAXIMUM', hidden, g.math('SUBTRACT', 1.0, g.math('COMPARE', tag, float(props.get('RendererVisibility', 0)), 0.25)))
    hidden = g.math('MAXIMUM', hidden, outside)
    delete = g.node('GeometryNodeDeleteGeometry', domain='POINT')
    g.link(imp.outputs[0], delete.inputs['Geometry'])
    g.link(hidden, delete.inputs['Selection'])
    return delete.outputs[0]


def _camera(g):
    cam = g.node('GeometryNodeObjectInfo', transform_space='RELATIVE')
    g.link(g.node('GeometryNodeInputActiveCamera').outputs[0], cam.inputs['Object'])
    rot = cam.outputs['Rotation']
    return {'pos': cam.outputs['Location'], 'x': g.rotate_vector((1.0, 0.0, 0.0), rot),
            'y': g.rotate_vector((0.0, 1.0, 0.0), rot), 'z': g.rotate_vector((0.0, 0.0, 1.0), rot)}


def _safe_normalize(g, v, eps):
    """v / max(|v|, eps) (UE SafeNormalize)."""
    length = g.math('MAXIMUM', g.vmath('LENGTH', v), eps)
    return g.vmath('SCALE', v, scale=g.math('DIVIDE', 1.0, length))


def _finish(g, inst, realize_input):
    realize = g.node('GeometryNodeRealizeInstances')
    g.link(inst, realize.inputs['Geometry'])
    out = g.node('NodeGroupOutput')
    g.link(g.switch('GEOMETRY', realize_input, inst, realize.outputs[0]), out.inputs['Geometry'])


def _sort(g, geo, cam, mode):
    if mode not in ('ViewDistance', 'ViewDepth'):
        return geo
    pos = g.node('GeometryNodeInputPosition').outputs[0]
    rel = g.vmath('SUBTRACT', pos, cam['pos'])
    key = g.vmath('LENGTH', rel) if mode == 'ViewDistance' else g.vmath('DOT_PRODUCT', rel, g.vmath('SCALE', cam['z'], scale=-1.0))
    node = g.node('GeometryNodeSortElements', domain='POINT')
    g.link(geo, node.inputs['Geometry'])
    g.link(g.math('MULTIPLY', key, -1.0), node.inputs['Sort Weight'])
    return node.outputs[0]


def _packed(g, geo, info):
    """Store the N-O6 instancer attributes (spec section 9)."""
    pr = info['props']
    for name, prefixes in (('nx_color', ['col_0', 'col_1', 'col_2', 'col_3']),) + tuple(
            ('nx_dyn%d' % k, ['dyn%d_%d' % (k, c) for c in range(4)]) for k in range(4)):
        geo = g.store(geo, name, 'FLOAT_COLOR', g.color_attr(prefixes))
    blend = 1.0 if pr.get('bSubImageBlend', True) else 0.0
    geo = g.store(geo, 'nx_misc', 'FLOAT_COLOR', g.color_attr(['subimg', 'nage', 'matrand', blend]))
    if info['kind'] == 'sprite':
        sub = pr.get('SubImageSize', [1.0, 1.0])
        sx = g.math('MULTIPLY', g.attr('uvs_0'), g.math('SIGN', g.attr('size_0')))
        sy = g.math('MULTIPLY', g.attr('uvs_1'), g.math('SIGN', g.attr('size_1')))
        geo = g.store(geo, 'nx_uvx', 'FLOAT_COLOR', g.color_attr([sx, sy, float(sub[0]), float(sub[1])]))
    return geo


def _quad(g, material):
    """Unit quad with verts at (u, -v, 0), u, v in {0, 1}; nx_quad_uv = (u, v) in UE convention."""
    grid = g.node('GeometryNodeMeshGrid')
    grid.inputs['Size X'].default_value = 1.0
    grid.inputs['Size Y'].default_value = 1.0
    grid.inputs['Vertices X'].default_value = 2
    grid.inputs['Vertices Y'].default_value = 2
    xf = g.node('GeometryNodeTransform')
    g.link(grid.outputs['Mesh'], xf.inputs['Geometry'])
    xf.inputs['Translation'].default_value = (0.5, -0.5, 0.0)
    pos = g.node('GeometryNodeInputPosition').outputs[0]
    sep = g.node('ShaderNodeSeparateXYZ')
    g.link(pos, sep.inputs[0])
    uv = g.combine(sep.outputs['X'], g.math('MULTIPLY', sep.outputs['Y'], -1.0), 0.0)
    geo = g.store(xf.outputs[0], 'nx_quad_uv', 'FLOAT2', uv)
    setm = g.node('GeometryNodeSetMaterial')
    g.link(geo, setm.inputs['Geometry'])
    setm.inputs['Material'].default_value = material
    return setm.outputs[0]


def build_sprite_tree(name, stream_dir, manifest, info, material):
    tree = _new_tree(name)
    g = G(tree)
    gin = g.node('NodeGroupInput')
    pr = info['props']
    cam = _camera(g)
    geo = _stream_points(g, stream_dir, manifest['frame_start'], manifest['frame_end'], pr)
    geo = _sort(g, geo, cam, pr.get('SortMode', 'None'))
    geo = _packed(g, geo, info)
    P = g.node('GeometryNodeInputPosition').outputs[0]
    S = g.vec_attr('size', 2)
    aS = g.vmath('ABSOLUTE', S)
    sep = g.node('ShaderNodeSeparateXYZ')
    g.link(aS, sep.inputs[0])
    theta = g.math('MULTIPLY', g.attr('rot'), math.pi / 180.0)
    D = _safe_normalize(g, g.vmath('SUBTRACT', cam['pos'], P), 1e-8)
    align, facing = pr['Alignment_resolved'], pr['FacingMode_resolved']
    F = _safe_normalize(g, g.vec_attr('facing', 3), 1e-6)
    A = _safe_normalize(g, g.vec_attr('align', 3), 1e-6)
    if align == 'Unaligned':
        if facing == 'FaceCamera':
            R, U = cam['x'], g.vmath('SCALE', cam['y'], scale=-1.0)
        elif facing == 'FaceCameraPlane':
            R = _safe_normalize(g, g.cross_ue(cam['z'], cam['y']), 1e-6)
            U = g.cross_ue(cam['z'], R)
        elif facing == 'CustomFacingVector':
            R = _safe_normalize(g, g.cross_ue(F, cam['y']), 1e-6)
            U = g.cross_ue(F, R)
        elif facing == 'FaceCameraPosition':
            R = _safe_normalize(g, g.cross_ue(D, (0.0, 0.0, 1.0)), 1e-6)
            U = g.cross_ue(D, R)
        else:
            raise ValueError('facing_unsupported:' + facing)
    else:
        if align == 'VelocityAligned':
            V = g.vec_attr('vel', 3)
            Vl = g.vmath('LENGTH', V)
            Dir = g.switch('VECTOR', g.math('GREATER_THAN', Vl, 1e-8), (0.0, 0.0, 1.0),
                           g.vmath('SCALE', V, scale=g.math('DIVIDE', 1.0, g.math('MAXIMUM', Vl, 1e-30))))
        else:
            Dir = A
        CF = F if facing == 'CustomFacingVector' else D
        R = _safe_normalize(g, g.cross_ue(CF, Dir), 1e-6)
        U = g.cross_ue(CF, R) if facing == 'CustomFacingVector' else g.vmath('SCALE', Dir, scale=-1.0)
    c, s = g.math('COSINE', theta), g.math('SINE', theta)
    OutR = g.vmath('ADD', g.vmath('SCALE', R, scale=c), g.vmath('SCALE', U, scale=s))
    OutU = g.vmath('SUBTRACT', g.vmath('SCALE', U, scale=c), g.vmath('SCALE', R, scale=s))
    axes = g.node('FunctionNodeAxesToRotation', primary_axis='X', secondary_axis='Y')
    g.link(OutR, axes.inputs['Primary Axis'])
    g.link(g.vmath('SCALE', OutU, scale=-1.0), axes.inputs['Secondary Axis'])
    geo = g.store(geo, 'nx_rot', 'QUATERNION', axes.outputs[0])
    geo = g.store(geo, 'nx_scale', 'FLOAT_VECTOR', g.combine(sep.outputs['X'], sep.outputs['Y'], 1.0))
    CO = g.vmath('SCALE', D, scale=g.attr('camoff'))
    off_r = g.vmath('SCALE', OutR, scale=g.math('MULTIPLY', sep.outputs['X'], g.attr('pivot_0')))
    off_u = g.vmath('SCALE', OutU, scale=g.math('MULTIPLY', sep.outputs['Y'], g.attr('pivot_1')))
    setp = g.node('GeometryNodeSetPosition')
    g.link(geo, setp.inputs['Geometry'])
    g.link(g.vmath('SUBTRACT', g.vmath('SUBTRACT', g.vmath('ADD', P, CO), off_r), off_u), setp.inputs['Position'])
    inst = g.node('GeometryNodeInstanceOnPoints')
    g.link(setp.outputs[0], inst.inputs['Points'])
    g.link(_quad(g, material), inst.inputs['Instance'])
    g.link(g.attr('nx_rot', 'QUATERNION'), inst.inputs['Rotation'])
    g.link(g.attr('nx_scale', 'FLOAT_VECTOR'), inst.inputs['Scale'])
    _finish(g, inst.outputs[0], gin.outputs['Realize'])
    return tree


def _mesh_branch(g, geo, info, cam, k, entry, mesh_object):
    pr = info['props']
    if info.get('mesh_index_bound'):
        other = g.math('SUBTRACT', 1.0, g.math('COMPARE', g.math('ROUND', g.attr('meshidx')), float(k), 0.25))
        delete = g.node('GeometryNodeDeleteGeometry', domain='POINT')
        g.link(geo, delete.inputs['Geometry'])
        g.link(other, delete.inputs['Selection'])
        geo = delete.outputs[0]
    P = g.node('GeometryNodeInputPosition').outputs[0]
    D = _safe_normalize(g, g.vmath('SUBTRACT', cam['pos'], P), 1e-8)
    qx, qy, qz, qw = (g.attr('quat_%d' % c) for c in range(4))
    qlen = g.math('MAXIMUM', g.math('SQRT', g.math('ADD', g.math('ADD', g.math('MULTIPLY', qx, qx), g.math('MULTIPLY', qy, qy)),
                                                     g.math('ADD', g.math('MULTIPLY', qz, qz), g.math('MULTIPLY', qw, qw)))), 1e-30)
    q2r = g.node('FunctionNodeQuaternionToRotation')
    for sock, v in zip(('X', 'Y', 'Z', 'W'), (qx, qy, qz, qw)):
        g.link(g.math('DIVIDE', v, qlen), q2r.inputs[sock])
    mq = entry['rotation_quat_ue']
    mrot = g.node('FunctionNodeQuaternionToRotation')
    for sock, v in zip(('X', 'Y', 'Z', 'W'), (-mq[0], mq[1], -mq[2], mq[3])):
        mrot.inputs[sock].default_value = v
    rr = g.node('FunctionNodeRotateRotation', rotation_space='LOCAL')
    g.link(q2r.outputs[0], rr.inputs['Rotation'])
    g.link(mrot.outputs[0], rr.inputs['Rotate By'])
    rot = rr.outputs[0]
    mode = pr.get('FacingMode', 'Default')
    if mode != 'Default':
        if mode == 'Velocity':
            V = g.vec_attr('vel', 3)
            Vl = g.vmath('LENGTH', V)
            F = g.switch('VECTOR', g.math('GREATER_THAN', Vl, 0.0), (0.0, 0.0, 1.0),
                         g.vmath('SCALE', V, scale=g.math('DIVIDE', 1.0, g.math('MAXIMUM', Vl, 1e-30))))
        elif mode == 'CameraPosition':
            F = D
        else:
            F = cam['z']
        if pr.get('bLockedAxisEnable'):
            la = pr.get('LockedAxis', [0.0, 0.0, 1.0])
            L = (float(la[0]), -float(la[1]), float(la[2]))
            near = g.math('GREATER_THAN', g.math('ABSOLUTE', g.vmath('DOT_PRODUCT', F, L)), 0.99)
            F = g.switch('VECTOR', near, F, (1.0, 0.0, 0.0) if abs(L[2]) > 0.99 else (0.0, 0.0, 1.0))
            Zc = L
            Yc = g.vmath('NORMALIZE', g.cross_ue(Zc, F))
            Xc = g.cross_ue(Yc, Zc)
        else:
            if mode == 'CameraPlane':
                Ref = cam['y']
            else:
                dz = g.vmath('DOT_PRODUCT', F, (0.0, 0.0, 1.0))
                flip = g.vmath('SCALE', (1.0, 0.0, 0.0), scale=g.math('MULTIPLY', g.math('SIGN', dz), -1.0))
                Ref = g.switch('VECTOR', g.math('GREATER_THAN', g.math('ABSOLUTE', dz), 0.99), (0.0, 0.0, 1.0), flip)
            Xc = F
            Yc = g.vmath('NORMALIZE', g.cross_ue(Ref, F))
        axes = g.node('FunctionNodeAxesToRotation', primary_axis='X', secondary_axis='Y')
        g.link(Xc, axes.inputs['Primary Axis'])
        g.link(g.vmath('SCALE', Yc, scale=-1.0), axes.inputs['Secondary Axis'])
        rf = g.node('FunctionNodeRotateRotation', rotation_space='GLOBAL')
        g.link(rot, rf.inputs['Rotation'])
        g.link(axes.outputs[0], rf.inputs['Rotate By'])
        rot = rf.outputs[0]
    pscale = g.vec_attr('scale', 3)
    ms = entry['scale']
    S = g.vmath('MULTIPLY', pscale, (float(ms[0]), float(ms[1]), float(ms[2])))
    geo = g.store(geo, 'nx_rot', 'QUATERNION', rot)
    geo = g.store(geo, 'nx_scale', 'FLOAT_VECTOR', S)
    geo = g.store(geo, 'nx_mesh', 'FLOAT', float(k))  # Join Geometry does not keep link order; branches are told apart by this tag
    po = entry['pivot_offset']
    piv = (float(po[0]) * 0.01, -float(po[1]) * 0.01, float(po[2]) * 0.01)
    pivot = g.rotate_vector(g.vmath('MULTIPLY', pscale, piv), rot) if entry.get('pivot_space', 'Mesh') == 'Mesh' else piv
    setp = g.node('GeometryNodeSetPosition')
    g.link(geo, setp.inputs['Geometry'])
    g.link(g.vmath('ADD', g.vmath('ADD', P, g.vmath('SCALE', D, scale=g.attr('camoff'))), pivot), setp.inputs['Position'])
    source_geometry = setp.outputs[0]
    if info['props'].get('bOverrideMaterials') and '/Game/Pioneer/Effects/Materials/Enemies/VisionCone/MI_FX_VisionCone_Turret_Shadowed' in [str(path).split('.')[0] for path in info.get('override_materials', [])]:
        source_geometry = _sibling('shader_tools.dxbc.visioncone').store_cone(g, source_geometry, info, cam, rot, S)
        source_geometry = _sibling('shader_tools.dxbc.visioncone_vf').store_particles(g, source_geometry, rot, S)
    info_node = g.node('GeometryNodeObjectInfo', transform_space='ORIGINAL')
    info_node.inputs['Object'].default_value = mesh_object
    info_node.inputs['As Instance'].default_value = True
    inst = g.node('GeometryNodeInstanceOnPoints')
    g.link(source_geometry, inst.inputs['Points'])
    g.link(info_node.outputs['Geometry'], inst.inputs['Instance'])
    g.link(g.attr('nx_rot', 'QUATERNION'), inst.inputs['Rotation'])
    g.link(g.attr('nx_scale', 'FLOAT_VECTOR'), inst.inputs['Scale'])
    return inst.outputs[0]


def build_ribbon_tree(name, stream_dir, manifest, info, material):
    """Per-vertex view-dependent ribbon expansion (NiagaraRibbonVertexFactory.ush GetRibbonPositionIntermediates, ConstructWorldPosition)."""
    tree = _new_tree(name)
    g = G(tree)
    gin = g.node('NodeGroupInput')
    pr = info['props']
    cam = _camera(g)
    geo = _stream_points(g, stream_dir, manifest['frame_start'], manifest['frame_end'], pr, prefix='m_', ribbon=True)
    for nm, prefixes in (('nx_color', ['col_0', 'col_1', 'col_2', 'col_3']),) + tuple(
            ('nx_dyn%d' % k, ['dyn%d_%d' % (k, c) for c in range(4)]) for k in range(4)):
        geo = g.store(geo, nm, 'FLOAT_COLOR', g.color_attr(prefixes))
    geo = g.store(geo, 'nx_misc', 'FLOAT_COLOR', g.color_attr([0.0, 'nage', 'matrand', 0.0]))
    geo = g.store(geo, 'nx_uv', 'FLOAT_COLOR', g.color_attr(['uv0_0', 'uv0_1', 'uv1_0', 'uv1_1']))
    P = g.node('GeometryNodeInputPosition').outputs[0]
    Dir = g.vec_attr('dir', 3)
    custom = pr.get('FacingMode', 'Screen') in ('Custom', 'CustomSideVector')
    F = _ribbon_normalize(g, g.vec_attr('facing', 3) if custom else g.vmath('SUBTRACT', cam['pos'], P))
    R = _ribbon_normalize(g, g.cross_ue(F, Dir))
    if pr.get('FacingMode') == 'CustomSideVector':
        R, F = g.vmath('SCALE', F, scale=-1.0), R
    if pr.get('twist_bound'):
        tw = g.attr('twist')
        c, s = g.math('COSINE', tw), g.math('SINE', tw)
        R, F = (g.vmath('ADD', g.vmath('SCALE', R, scale=c), g.vmath('SCALE', F, scale=s)),
                g.vmath('ADD', g.vmath('SCALE', R, scale=g.math('MULTIPLY', s, -1.0)), g.vmath('SCALE', F, scale=c)))
    up = g.cross_ue(Dir, R)
    off = g.vmath('ADD', g.vmath('SCALE', R, scale=g.attr('sl_px')), g.vmath('SCALE', up, scale=g.attr('sl_py')))
    setp = g.node('GeometryNodeSetPosition')
    g.link(geo, setp.inputs['Geometry'])
    g.link(g.vmath('ADD', P, g.vmath('SCALE', off, scale=g.attr('width'))), setp.inputs['Position'])
    setm = g.node('GeometryNodeSetMaterial')
    g.link(setp.outputs[0], setm.inputs['Geometry'])
    setm.inputs['Material'].default_value = material
    out = g.node('NodeGroupOutput')
    g.link(setm.outputs[0], out.inputs['Geometry'])
    return tree


def _ribbon_normalize(g, v):
    """SafeNormalize(V, 0) in NiagaraRibbonVertexFactory.ush: zero for a zero vector."""
    return g.vmath('NORMALIZE', v)


def ribbon_preview_material():
    """Geometry-attribute colour x UV grid at UV0; alpha-blended. Replaced by translated FX materials in N-O6."""
    mat = bpy.data.materials.get(PREVIEW_MATERIAL + '_Ribbon')
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(PREVIEW_MATERIAL + '_Ribbon')
    mat.use_nodes = True
    mat.surface_render_method = 'BLENDED'
    nt = mat.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links
    col = N.new('ShaderNodeAttribute')
    col.attribute_type = 'GEOMETRY'
    col.attribute_name = 'nx_color'
    uv = N.new('ShaderNodeAttribute')
    uv.attribute_type = 'GEOMETRY'
    uv.attribute_name = 'nx_uv'
    sep = N.new('ShaderNodeSeparateXYZ')
    L.new(uv.outputs['Vector'], sep.inputs[0])
    inv = N.new('ShaderNodeMath')
    inv.operation = 'SUBTRACT'
    inv.inputs[0].default_value = 1.0
    L.new(sep.outputs['Y'], inv.inputs[1])
    comb = N.new('ShaderNodeCombineXYZ')
    L.new(sep.outputs['X'], comb.inputs[0])
    L.new(inv.outputs[0], comb.inputs[1])
    tex = N.new('ShaderNodeTexImage')
    img = bpy.data.images.get('DR_NX_UVGrid') or bpy.data.images.new('DR_NX_UVGrid', 256, 256, alpha=True)
    img.generated_type = 'UV_GRID'
    tex.image = img
    L.new(comb.outputs[0], tex.inputs['Vector'])
    rgb = N.new('ShaderNodeMix')
    rgb.data_type = 'RGBA'
    rgb.blend_type = 'MULTIPLY'
    rgb.inputs['Factor'].default_value = 1.0
    L.new(col.outputs['Color'], rgb.inputs['A'])
    L.new(tex.outputs['Color'], rgb.inputs['B'])
    em = N.new('ShaderNodeEmission')
    L.new(rgb.outputs['Result'], em.inputs['Color'])
    tr = N.new('ShaderNodeBsdfTransparent')
    mix = N.new('ShaderNodeMixShader')
    L.new(col.outputs['Alpha'], mix.inputs['Fac'])
    L.new(tr.outputs[0], mix.inputs[1])
    L.new(em.outputs[0], mix.inputs[2])
    out = N.new('ShaderNodeOutputMaterial')
    L.new(mix.outputs[0], out.inputs['Surface'])
    return mat


def build_mesh_tree(name, stream_dir, manifest, info, mesh_objects):
    tree = _new_tree(name)
    g = G(tree)
    gin = g.node('NodeGroupInput')
    cam = _camera(g)
    geo = _stream_points(g, stream_dir, manifest['frame_start'], manifest['frame_end'], info['props'])
    geo = _packed(g, geo, info)
    join = g.node('GeometryNodeJoinGeometry')
    for k, entry in enumerate(info['meshes']):
        g.link(_mesh_branch(g, geo, info, cam, k, entry, mesh_objects[k]), join.inputs[0])
    diagnostic = any(o.get('dr_nx_vf_source') for o in mesh_objects)
    _finish(g, join.outputs[0], True if diagnostic else gin.outputs['Realize'])
    return tree


def placeholder_mesh_object():
    obj = bpy.data.objects.get(PLACEHOLDER_MESH)
    if obj is None:
        mesh = bpy.data.meshes.new(PLACEHOLDER_MESH)
        s = 0.05
        mesh.from_pydata([(s, 0, 0), (-s, s, 0), (-s, -s, 0), (0, 0, 2 * s)], [], [(0, 1, 2), (0, 1, 3), (1, 2, 3), (2, 0, 3)])
        obj = bpy.data.objects.new(PLACEHOLDER_MESH, mesh)
        obj['dr_nx_label'] = 'mesh: placeholder'
    return obj


def preview_material():
    """Instancer colour x SubUV-sampled UV grid; alpha-blended. Replaced by translated FX materials in N-O6."""
    mat = bpy.data.materials.get(PREVIEW_MATERIAL)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(PREVIEW_MATERIAL)
    mat.use_nodes = True
    mat.surface_render_method = 'BLENDED'
    nt = mat.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links

    def attr(name, kind):
        node = N.new('ShaderNodeAttribute')
        node.attribute_type = kind
        node.attribute_name = name
        return node

    def m(op, a, b=0.0):
        node = N.new('ShaderNodeMath')
        node.operation = op
        for i, v in enumerate((a, b)):
            if isinstance(v, bpy.types.NodeSocket):
                L.new(v, node.inputs[i])
            else:
                node.inputs[i].default_value = v
        return node.outputs[0]

    col, misc, uvx = attr('nx_color', 'INSTANCER'), attr('nx_misc', 'INSTANCER'), attr('nx_uvx', 'INSTANCER')
    quv = attr('nx_quad_uv', 'GEOMETRY')
    sep_misc, sep_uvx, sep_q = N.new('ShaderNodeSeparateXYZ'), N.new('ShaderNodeSeparateXYZ'), N.new('ShaderNodeSeparateXYZ')
    L.new(misc.outputs['Vector'], sep_misc.inputs[0])
    L.new(uvx.outputs['Vector'], sep_uvx.inputs[0])
    L.new(quv.outputs['Vector'], sep_q.inputs[0])
    idx = sep_misc.outputs['X']
    sx, sy = sep_uvx.outputs['Z'], uvx.outputs['Alpha']
    lerp = m('SUBTRACT', idx, m('FLOOR', idx))
    a = m('ADD', m('SUBTRACT', idx, lerp), 0.5)
    b = m('ADD', a, 1.0)
    ut = m('ADD', 0.5, m('MULTIPLY', m('SUBTRACT', sep_q.outputs['X'], 0.5), sep_uvx.outputs['X']))
    vt = m('ADD', 0.5, m('MULTIPLY', m('SUBTRACT', sep_q.outputs['Y'], 0.5), sep_uvx.outputs['Y']))
    img = bpy.data.images.get('DR_NX_UVGrid') or bpy.data.images.new('DR_NX_UVGrid', 256, 256, alpha=True)
    img.generated_type = 'UV_GRID'
    samples = []
    for k in (a, b):
        h = m('FLOOR', m('MODULO', k, sx))
        v = m('FLOOR', m('DIVIDE', k, sx))
        tu = m('DIVIDE', m('ADD', h, ut), sx)
        tv = m('DIVIDE', m('ADD', v, vt), sy)
        comb = N.new('ShaderNodeCombineXYZ')
        L.new(tu, comb.inputs[0])
        L.new(m('SUBTRACT', 1.0, tv), comb.inputs[1])
        tex = N.new('ShaderNodeTexImage')
        tex.name = 'nx_tex_%s' % ('a' if k is a else 'b')
        tex.image = img
        tex.interpolation = 'Closest'
        L.new(comb.outputs[0], tex.inputs['Vector'])
        samples.append(tex)
    fac = m('MULTIPLY', lerp, misc.outputs['Alpha'])
    mix = N.new('ShaderNodeMix')
    mix.data_type = 'RGBA'
    L.new(fac, mix.inputs['Factor'])
    L.new(samples[0].outputs['Color'], mix.inputs['A'])
    L.new(samples[1].outputs['Color'], mix.inputs['B'])
    mixa = m('ADD', m('MULTIPLY', samples[0].outputs['Alpha'], m('SUBTRACT', 1.0, fac)), m('MULTIPLY', samples[1].outputs['Alpha'], fac))
    rgb = N.new('ShaderNodeMix')
    rgb.data_type = 'RGBA'
    rgb.blend_type = 'MULTIPLY'
    rgb.inputs['Factor'].default_value = 1.0
    L.new(col.outputs['Color'], rgb.inputs['A'])
    L.new(mix.outputs['Result'], rgb.inputs['B'])
    em = N.new('ShaderNodeEmission')
    L.new(rgb.outputs['Result'], em.inputs['Color'])
    tr = N.new('ShaderNodeBsdfTransparent')
    mixs = N.new('ShaderNodeMixShader')
    L.new(m('MULTIPLY', col.outputs['Alpha'], mixa), mixs.inputs['Fac'])
    L.new(tr.outputs[0], mixs.inputs[1])
    L.new(em.outputs[0], mixs.inputs[2])
    out = N.new('ShaderNodeOutputMaterial')
    L.new(mixs.outputs[0], out.inputs['Surface'])
    return mat


def _keyframe_constant(idblock):
    ad = idblock.animation_data
    if ad is None or ad.action is None:
        return
    from bpy_extras import anim_utils
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    if bag is None:
        return
    for fc in bag.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = 'CONSTANT'


def fx_exposure_scale():
    """UE-units-to-scene scale of the FX exposure group (niagara_fx_materials), 1.0 when the scene has none."""
    grp = bpy.data.node_groups.get('DR_NX_FXExposure')
    value = next((n for n in grp.nodes if n.type == 'VALUE'), None) if grp else None
    return float(value.outputs[0].default_value) if value else 1.0


def build_light_pool(name, stream_dir, manifest, info, parent, collection, e1=E1_CYCLES, exposure=1.0):
    """exposure: the scale FX emissives get (fx_exposure_scale), so particle lights keep their authored ratio to them."""
    frames = list(range(manifest['frame_start'], manifest['frame_end'] + 1))
    states = [light_frame(read_ply(Path(stream_dir) / ('f_%04d.ply' % f)), info, e1) for f in frames]
    for st in states:
        for s in st:
            s['energy'] *= exposure
    slots, size = assign_slots([[s['uid'] for s in st] for st in states])
    objects = []
    for k in range(size):
        data = bpy.data.lights.new('%s_L%02d' % (name, k), 'POINT')
        data.use_shadow = False
        data.shadow_soft_size = 0.0
        data.use_custom_distance = True
        obj = bpy.data.objects.new(data.name, data)
        obj.parent = parent
        obj['dr_nx_label'] = 'light_falloff: approx' if info['props'].get('bUseInverseSquaredFalloff', True) else 'light_falloff: unsupported_exponent'
        collection.objects.link(obj)
        objects.append(obj)
    for f, st, sl in zip(frames, states, slots):
        by_slot = dict(zip(sl, st))
        for k, obj in enumerate(objects):
            s = by_slot.get(k)
            d = obj.data
            obj.hide_render = obj.hide_viewport = s is None
            if s is not None:
                obj.location = tuple(s['pos'])
                d.color = tuple(s['color'])
                d.energy = s['energy']
                d.cutoff_distance = s['radius']
                d.volume_factor, d.specular_factor, d.diffuse_factor = s['volume'], s['specular'], s['diffuse']
            else:
                d.energy = 0.0
            for path in ('location', 'hide_render', 'hide_viewport'):
                obj.keyframe_insert(path, frame=f)
            for path in ('color', 'energy', 'cutoff_distance', 'volume_factor', 'specular_factor', 'diffuse_factor'):
                d.keyframe_insert(path, frame=f)
    for obj in objects:
        _keyframe_constant(obj)
        _keyframe_constant(obj.data)
    return objects


def _material_variant(obj, mats):
    """Hidden copy of a mesh source carrying per-slot renderer materials (overrides / translated slot materials); obj when none."""
    if not any(mats):
        return obj
    key = '%s|%s' % (obj.name, '|'.join(m.name if m else '' for m in mats))
    hit = next((o for o in bpy.data.objects if o.get('dr_nx_variant') == key), None)
    if hit is not None:
        return hit
    new = obj.copy()
    new.data = obj.data.copy()
    for i, m in enumerate(mats):
        if m is None:
            continue
        if i < len(new.data.materials):
            new.data.materials[i] = m
        else:
            new.data.materials.append(m)
    new['dr_nx_variant'] = key
    for c in obj.users_collection or [bpy.context.scene.collection]:
        c.objects.link(new)
    hide_instance_source(new)
    return new


def hide_instance_source(obj):
    """Hide a GN instancing source without dropping its instances from the Cycles Rendered viewport.

    'Disable in Viewports' (hide_viewport) also removes the Object Info instances from the Rendered viewport (F12 still shows
    them); the eye toggle and 'Disable in Renders' hide only the source."""
    obj.hide_render = True
    obj.hide_viewport = False
    try:
        obj.hide_set(True)
    except RuntimeError:  # not in the active view layer
        pass


def rigs_build_system(cache_dir, collection=None, mesh_objects=None, material=None, materials=None):
    """Build playback for one cache. mesh_objects maps a mesh ObjectPath to a bpy object (placeholder otherwise).

    materials: optional niagara_fx_materials.renderer_materials callback (renderer info, kind, slot, slot material name) ->
    translated material or None; unresolved renderers keep the preview material.
    """
    cache_dir = Path(cache_dir)
    manifest = json.loads((cache_dir / 'cache_manifest.json').read_text(encoding='utf-8'))
    collection = collection or bpy.context.scene.collection
    root = bpy.data.objects.new('NX_' + manifest['system'], None)
    root.location = tuple(manifest['bake_origin_b'])
    root['dr_nx_cache'] = str(cache_dir)
    collection.objects.link(root)
    material = material or preview_material()
    infos = {(r['emitter'], r['ordinal']): r for r in manifest['renderers']}
    bind_runtime = getattr(materials, 'bind_runtime', None)
    if bind_runtime:
        bind_runtime(cache_dir, manifest)
    built = {'root': root, 'rigs': [], 'lights': [], 'unsupported_streams': []}
    for st in manifest['streams']:
        info = infos[(st['emitter'], st['ordinal'])]
        name = '%s_%s_%s' % (manifest['system'], st['name'], info['kind'])
        sdir = cache_dir / st['name']
        if info['kind'] not in ('light', 'sprite', 'ribbon', 'mesh'):
            built['unsupported_streams'].append(dict(stream=st['name'], kind=info['kind'], reason='no source-backed playback adapter'))
            continue
        if info['kind'] == 'light':
            built['lights'].append((st['name'], build_light_pool(name, sdir, manifest, info, root, collection, exposure=fx_exposure_scale())))
            continue
        if info['kind'] == 'sprite':
            tree = build_sprite_tree(name, sdir, manifest, info, (materials(info, 'sprite') if materials else None) or material)
        elif info['kind'] == 'ribbon':
            tree = build_ribbon_tree(name, sdir, manifest, info, ribbon_preview_material())
        else:
            objs = [(mesh_objects or {}).get(m['object_path']) or placeholder_mesh_object() for m in info['meshes']]
            if materials:
                bind_geometry = getattr(materials, 'bind_geometry', None)
                if bind_geometry:
                    bind_geometry(info, objs)
                objs = [_material_variant(o, [materials(info, 'mesh', i, n) for i, n in enumerate(
                    list(o.get('dr_nx_slot_materials') or []) or [s.material.name if s.material else None for s in o.material_slots] or [None])])
                    for o in objs]
            tree = build_mesh_tree(name, sdir, manifest, info, objs)
        obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        obj.parent = root
        obj['dr_nx_stream'] = st['name']
        obj['dr_nx_kind'] = info['kind']
        collection.objects.link(obj)
        mod = obj.modifiers.new('NX', 'NODES')
        mod.node_group = tree
        built['rigs'].append((st['name'], obj))
    scene = bpy.context.scene
    scene.render.fps = int(round(manifest['fps']))
    scene.render.fps_base = float(manifest['fps_base'])
    return built


def set_realize(obj, value):
    mod = obj.modifiers['NX']
    for item in mod.node_group.interface.items_tree:
        if getattr(item, 'name', '') == 'Realize' and item.in_out == 'INPUT':
            mod[item.identifier] = bool(value)
    obj.update_tag()

# ---- niagara_vm.py ----
# Offline decoder for unoptimized Niagara VectorVM programs.
#
# This module parses instructions; it does not execute Niagara or recover graphs.
# Profiles distinguish UE 5.3 legacy and Pioneer 2.1.0 source bytecode. The wire layout was checked against the locally installed UE 5.3.2 legacy
# kernel reader. Optimized/experimental VM streams and later engine formats must
# not be passed as legacy bytecode merely because opcode numbers overlap.
# Opcode identifiers are format facts. Operand counts follow kernel handlers,
# not the experimental VM metadata (which gives bit_not a misleading arity).
_NAMES = '''done add sub mul div mad lerp rcp rsq sqrt neg abs exp exp2 log log2
sin cos tan asin acos atan atan2 ceil floor fmod frac trunc clamp min max pow
round sign step random noise cmplt cmple cmpgt cmpge cmpeq cmpneq select
addi subi muli divi clampi mini maxi absi negi signi randomi cmplti cmplei
cmpgti cmpgei cmpeqi cmpneqi bit_and bit_or bit_xor bit_not bit_lshift
bit_rshift logic_and logic_or logic_xor logic_not f2i i2f f2b b2f i2b b2i
inputdata_float inputdata_int32 inputdata_half inputdata_noadvance_float
inputdata_noadvance_int32 inputdata_noadvance_half outputdata_float
outputdata_int32 outputdata_half acquireindex external_func_call exec_index
noise2D noise3D enter_stat_scope exit_stat_scope update_id acquire_id
half_to_float fasi iasf'''.split()
OPCODES = dict(enumerate(_NAMES))
_UNARY = set('rcp rsq sqrt neg abs exp exp2 log log2 sin cos tan asin acos atan ceil floor frac trunc round sign random noise absi negi signi randomi bit_not logic_not f2i i2f f2b b2f i2b b2i fasi iasf'.split())
_TRINARY = {'mad', 'lerp', 'clamp', 'select', 'clampi', 'noise3D'}
_BINARY = set(_NAMES[1:77]) - _UNARY - _TRINARY
_BINARY.add('noise2D')

class DecodeError(ValueError):
    """A program cannot be decoded without guessing its instruction boundary."""
    def __init__(self, offset, message, instructions=()):
        self.offset = offset
        self.instructions = tuple(instructions)
        super().__init__(f'byte {offset}: {message}')

@dataclass(frozen=True)
class Operand:
    kind: str
    value: int

@dataclass(frozen=True)
class Instruction:
    offset: int
    end_offset: int
    opcode: int
    name: str
    operands: tuple
    source_mask: object = None

@dataclass(frozen=True)
class Disassembly:
    instructions: tuple
    byte_length: int
    trailing_padding: int = 0
    profile: str = 'ue5.3-legacy'

def external_register_counts(functions):
    """Return signature-derived word counts, including inputs and outputs.

    External operands remain raw encoded words: their interpretation belongs
    to each data-interface implementation, not the arithmetic operand mask.
    Missing signature fields are errors, rather than zero-argument guesses.
    """
    result = []
    for index, function in enumerate(functions):
        inputs = function.get('InputParamLocations')
        outputs = function.get('NumOutputs')
        if not isinstance(inputs, list) or not isinstance(outputs, int) or isinstance(outputs, bool) or outputs < 0:
            raise ValueError(f'invalid external function signature {index}')
        result.append(len(inputs) + outputs)
    return result

def bytecode_from_vm(vm, max_bytes=64 * 1024 * 1024):
    """Read CUE4Parse ByteCode.Data and honor optional zlib UncompressedSize.

    Bounds apply to both compressed input and output. An empty payload stays
    empty; ExperimentalContextData is deliberately never used as a substitute.
    """
    carrier = vm.get('ByteCode') or {}
    data = carrier.get('Data', [])
    if isinstance(data, str):
        if len(data) > (max_bytes + 2) // 3 * 4:
            raise ValueError('bytecode exceeds size limit')
        raw = base64.b64decode(data, validate=True)
    else:
        if len(data) > max_bytes:
            raise ValueError('bytecode exceeds size limit')
        raw = bytes(data)
    size = carrier.get('UncompressedSize', -1)
    if size != -1 and raw:
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= max_bytes:
            raise ValueError('invalid uncompressed bytecode size')
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(raw, size + 1)
        if len(decoded) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('compressed bytecode size/stream mismatch')
        return decoded
    return raw

def vm_disassemble(bytecode, external_functions=(), *, profile='ue5.3-legacy', allow_zero_padding=False):
    """Decode a complete program, with explicit byte offsets and operands.

    ``external_functions`` is a sequence of word counts, a mapping of function
    index to word count, or CUE4Parse signature dictionaries. No resynchronizing
    scan occurs after an invalid opcode. Errors retain the decoded prefix.
    Pioneer 2.1.0 uses a 16-bit external function index, also documented by the UE 5.7 optimizer, while the legacy profile uses 8 bits. Strict mode requires done at the exact end; explicitly permitted zero
    padding is reported and is never counted as additional instructions.
    """
    if profile not in ('ue5.3-legacy', 'pioneer-2.1.0'):
        raise ValueError(f'unsupported VectorVM profile: {profile}')
    raw = memoryview(bytes(bytecode))
    if external_functions and not isinstance(external_functions, dict) and isinstance(external_functions[0], dict):
        external_functions = external_register_counts(external_functions)
    position = 0
    instructions = []
    start = 0

    def fail(message):
        raise DecodeError(start, message, instructions)

    def read(width):
        nonlocal position
        if position + width > len(raw):
            fail(f'truncated instruction; need {width} bytes at {position}')
        value = raw[position] if width == 1 else struct.unpack_from('<H', raw, position)[0]
        position += width
        return value

    def words(kinds):
        return tuple(Operand(kind, read(2)) for kind in kinds)

    while position < len(raw):
        start = position
        opcode = read(1)
        name = OPCODES.get(opcode)
        if name is None or name == 'half_to_float':
            fail(f'unsupported legacy opcode {opcode}')
        mask = None
        operands = ()
        if name in _UNARY or name in _BINARY or name in _TRINARY:
            count = 1 if name in _UNARY else 3 if name in _TRINARY else 2
            mask = read(1)
            if mask >= (1 << count):
                fail(f'invalid {name} source mask {mask}')
            operands = words(['constant' if mask & (1 << i) else 'register' for i in range(count)] + ['destination'])
        elif name.startswith('inputdata_'):
            operands = words(['dataset', 'input_attribute', 'destination'])
        elif name.startswith('outputdata_'):
            mask = read(1)
            if mask not in (0, 1):
                fail(f'invalid output source mask {mask}')
            operands = words(['dataset', 'index_register', 'constant' if mask else 'register', 'output_attribute'])
        elif name == 'acquireindex':
            mask = read(1)
            if mask not in (0, 1):
                fail(f'invalid acquireindex source mask {mask}')
            operands = words(['dataset', 'constant' if mask else 'register', 'destination'])
        elif name == 'external_func_call':
            index = read(2 if profile == 'pioneer-2.1.0' else 1)
            try:
                count = external_functions[index]
            except (KeyError, IndexError, TypeError):
                fail(f'missing external function signature {index}')
            if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 65535:
                fail(f'invalid external function word count {index}')
            operands = (Operand('external_function', index),) + words(['external_argument'] * count)
        elif name == 'exec_index':
            operands = words(['destination'])
        elif name == 'enter_stat_scope':
            operands = words(['constant'])
        elif name == 'update_id':
            operands = words(['dataset', 'id_register', 'index_register'])
        elif name == 'acquire_id':
            operands = words(['dataset', 'id_destination', 'tag_destination'])
        elif name not in ('done', 'exit_stat_scope'):
            fail(f'unsupported legacy instruction {name}')
        instructions.append(Instruction(start, position, opcode, name, operands, mask))
        if name == 'done':
            padding = len(raw) - position
            if padding and (not allow_zero_padding or any(raw[position:])):
                fail(f'{padding} trailing bytes after done')
            return Disassembly(tuple(instructions), len(raw), padding, profile)
    start = position
    fail('missing done terminator')


# ---- niagara_context.py ----
# Strict offline constant-table assembly for UE 5.3.2 Niagara layouts.
#
# This opt-in layout profile is source-derived, not inferred from a game version.
# Only actual bytecode reads must be supplied. Unread holes contain poison 0xCD;
# these bytes are explicitly unavailable and may never be used by another program.
# Serialized parameter values are asset defaults, not observed runtime values.
# No scheduler, external-interface implementation, or live-game equivalence claim.
class ContextError(ValueError):
    pass

# Field offsets/sizes are ABI facts from NiagaraTypes.h, UE5.3.2.
LAYOUTS = {
    'global': (32, {name: (offset, kind, 1) for name, offset, kind in [
        ('WorldDeltaTime',0,'f'), ('EngineDeltaTime',4,'f'),
        ('EngineInvDeltaTime',8,'f'), ('EngineTime',12,'f'),
        ('EngineRealTime',16,'f'), ('QualityLevel',20,'i')]}),
    'system': (64, {name: (offset, kind, 1) for name, offset, kind in [
        ('EngineTimeSinceRendered',0,'f'), ('EngineLodDistance',4,'f'),
        ('EngineLodDistanceFraction',8,'f'), ('EngineSystemAge',12,'f'),
        ('EngineExecutionState',16,'I'), ('EngineTickCount',20,'i'),
        ('EngineEmitterCount',24,'i'), ('EngineAliveEmitterCount',28,'i'),
        ('SignificanceIndex',32,'i'), ('RandomSeed',36,'i'),
        ('CurrentTimeStep',40,'i'), ('NumTimeSteps',44,'i'),
        ('TimeStepFraction',48,'f'), ('NumParticles',52,'I')]}),
    'owner': (512, dict(
        [(name,(index*64,'f',16)) for index,name in enumerate([
            'EngineLocalToWorld','EngineWorldToLocal','EngineLocalToWorldTransposed',
            'EngineWorldToLocalTransposed','EngineLocalToWorldNoScale','EngineWorldToLocalNoScale'])]
        + [('EngineRotation',(384,'f',4)), ('EnginePosition',(400,'f',3)),
           ('EngineVelocity',(416,'f',3)), ('EngineXAxis',(432,'f',3)),
           ('EngineYAxis',(448,'f',3)), ('EngineZAxis',(464,'f',3)),
           ('EngineScale',(480,'f',3)), ('EngineLWCTile',(496,'f',4))])),
    'emitter': (32, {name: (offset, kind, 1) for name, offset, kind in [
        ('EmitterNumParticles',0,'i'), ('EmitterTotalSpawnedParticles',4,'i'),
        ('EmitterSpawnCountScale',8,'f'), ('EmitterAge',12,'f'),
        ('EmitterRandomSeed',16,'i'), ('EmitterInstanceSeed',20,'i')]}),
}

@dataclass(frozen=True)
class ConstantContext:
    constant_tables: tuple
    table_manifest: tuple
    required_constants: tuple
    layout_profile: str
    bytecode_profile: str
    bytecode: bytes

def _bytes(value, label):
    if not isinstance(value, (bytes, bytearray, list, tuple)):
        raise ContextError(f'{label} must be a byte array')
    try:
        return bytes(value)
    except (ValueError, TypeError) as exc:
        raise ContextError(f'invalid {label}') from exc

def build_constant_context(script_export, *, runtime_values=None,
                           parameter_overrides=None, layout_profile,
                           bytecode_profile='ue5.3-legacy'):
    """Assemble only when every referenced constant word has known bytes.

    runtime_values names use C++ field names, e.g.
    {'emitter.EmitterTotalSpawnedParticles': 100,
     'global.EngineDeltaTime': 1/60}.
    Previous frame uses 'previous.emitter.EmitterAge', etc. All omitted fields
    remain unavailable. parameter_overrides maps serialized variable names to
    complete raw bytes; a full field extent must be supplied (no partial patch).
    Returned tables are validated only for the returned bytecode, never reusable
    blindly. Inspect provenance to distinguish serialized defaults from inputs.
    """
    if layout_profile != 'ue5.3.2':
        raise ContextError(f'unsupported constant layout profile {layout_profile}')
    props = script_export.get('Properties', {})
    vm = props.get('CachedScriptVM')
    if not isinstance(vm, dict):
        raise ContextError('missing CachedScriptVM')
    usage = props.get('Usage')
    names = {'ENiagaraScriptUsage::SystemSpawnScript':11,
             'ENiagaraScriptUsage::SystemUpdateScript':12,
             'ENiagaraScriptUsage::ParticleSpawnScript':3,
             'ENiagaraScriptUsage::ParticleSpawnScriptInterpolated':4,
             'ENiagaraScriptUsage::ParticleUpdateScript':5}
    usage = names.get(usage, usage)
    if usage not in (3,4,5,11,12):
        raise ContextError(f'unsupported script usage {usage}')
    interpolated = usage == 4
    store = props.get('ScriptExecutionParamStore')
    if not isinstance(store, dict):
        raise ContextError('missing ScriptExecutionParamStore')
    if store == {}:
        size, data = 0, b''
    else:
        size = store.get('ParameterSize')
        if not isinstance(size,int) or isinstance(size,bool) or not 0 <= size <= 1048576:
            raise ContextError('invalid/missing ParameterSize')
        data = _bytes(store.get('ParameterData'), 'ParameterData')
    expected = size * (2 if interpolated else 1)
    if len(data) != expected:
        raise ContextError(f'ParameterData size {len(data)} != expected {expected}; no padding guessed')
    parameter_data = bytearray(data)
    override_provenance = {}
    offsets = store.get('SortedParameterOffsets', [])
    for name, raw in (parameter_overrides or {}).items():
        entries = [entry for entry in offsets if entry.get('Name') == name]
        if len(entries) != 1:
            raise ContextError(f'unknown/ambiguous parameter {name}')
        start = entries[0].get('Offset')
        if not isinstance(start,int) or not 0 <= start < expected:
            raise ContextError(f'invalid parameter offset {name}')
        boundary = size if start < size else expected
        end = min([entry['Offset'] for entry in offsets
                   if isinstance(entry.get('Offset'),int) and start < entry['Offset'] <= boundary] + [boundary])
        raw = _bytes(raw, f'override {name}')
        if len(raw) != end-start:
            raise ContextError(f'override {name} must contain {end-start} bytes')
        parameter_data[start:end] = raw
        for offset in range(start,end):
            override_provenance[offset] = f'parameter override {name}'
    literals = _bytes(vm.get('ScriptLiterals'), 'ScriptLiterals')
    raw = bytecode_from_vm(vm)
    if not raw:
        raise ContextError('empty bytecode cannot establish a constant context')
    instructions = vm_disassemble(raw, vm.get('CalledVMExternalFunctions',()), profile=bytecode_profile).instructions
    if any(ins.name == 'external_func_call' for ins in instructions):
        raise ContextError('external function constant bindings are unsupported')
    runtime_values = dict(runtime_values or {})
    consumed = set()
    tables = []
    provenance = []
    table_names = []

    def add_runtime(kind, previous=False):
        length, fields = LAYOUTS[kind]
        table = bytearray([0xcd])*length
        known = [None]*length
        prefix = ('previous.' if previous else '') + kind + '.'
        for name, (offset, fmt, count) in fields.items():
            key = prefix + name
            if key not in runtime_values:
                continue
            value = runtime_values[key]
            sequence = [value] if count == 1 else value
            try:
                if len(sequence) != count or (fmt == 'f' and not all(math.isfinite(v) for v in sequence)):
                    raise ValueError('invalid field values')
                encoded = struct.pack('<'+fmt*count,*sequence)
            except (TypeError,ValueError,struct.error,OverflowError) as exc:
                raise ContextError(f'invalid runtime field {key}') from exc
            consumed.add(key)
            table[offset:offset+len(encoded)] = encoded
            known[offset:offset+len(encoded)] = [f'explicit runtime {key}']*len(encoded)
        tables.append(bytes(table))
        provenance.append(known)
        table_names.append(prefix[:-1])

    def add_external(previous=False):
        base = size if previous else 0
        tables.append(bytes(parameter_data[base:base+size]))
        provenance.append([override_provenance.get(base+i,'serialized parameter default') for i in range(size)])
        table_names.append(('previous.' if previous else '')+'external')

    if usage in (11,12):
        add_runtime('global')
        add_external()
    else:
        for kind in ('global','system','owner','emitter'):
            add_runtime(kind)
        add_external()
        if interpolated:
            for kind in ('global','system','owner','emitter'):
                add_runtime(kind,True)
            add_external(True)
    tables.append(literals)
    provenance.append(['serialized script literal']*len(literals))
    table_names.append('literals')
    if set(runtime_values)-consumed:
        raise ContextError(f'unknown or irrelevant runtime fields: {sorted(set(runtime_values)-consumed)}')
    required = []
    for offset in sorted({o.value for ins in instructions for o in ins.operands if o.kind=='constant'}):
        local = offset
        for index,table in enumerate(tables):
            if local < len(table):
                if local+4 > len(table):
                    raise ContextError(f'constant {offset} crosses table boundary')
                origins = provenance[index][local:local+4]
                if any(origin is None for origin in origins):
                    kind = table_names[index].split('.')[-1]
                    fields = LAYOUTS.get(kind,(0,{}))[1]
                    candidates = [name for name,(field_offset,fmt,count) in fields.items()
                                  if field_offset <= local < field_offset+4*count]
                    label = '.'.join([table_names[index],candidates[0]]) if candidates else table_names[index]+'.padding-or-unknown'
                    raise ContextError(f'constant {offset} needs explicit {label} (table byte {local})')
                required.append({'constantOffset':offset,'table':table_names[index],'tableOffset':local,
                                 'provenance':tuple(dict.fromkeys(origins)),
                                 'wordHex':table[local:local+4].hex()})
                break
            local -= len(table)
        else:
            raise ContextError(f'constant {offset} exceeds assembled table sizes')
    manifest = []
    start = 0
    for name,table,origins in zip(table_names,tables,provenance):
        manifest.append({'name':name,'byteOffset':start,'byteLength':len(table),
                         'knownBytes':sum(origin is not None for origin in origins),
                         'unavailableBytes':sum(origin is None for origin in origins),
                         'unavailableFill':'0xcd, never referenced by validated bytecode'})
        start += len(table)
    return ConstantContext(tuple(tables),tuple(manifest),tuple(required),layout_profile,bytecode_profile,raw)


@dataclass(frozen=True)
class DatasetInputContext:
    inputs: dict
    provenance: tuple

def build_dataset_inputs(context, compiled_data, dataset_index, *, num_instances,
                         values=None, parameter_store=None):
    """Map required input attributes to compiled variable names, fail closed.

    Explicit ``values`` are uniform numeric scalars or component sequences,
    broadcast across instances. Parameter-store values are decoded only when
    their component byte offsets survive in compiled LayoutInfo. Supports
    uniform float/int32 variables, not mixed structs, half, or interfaces.
    No missing runtime value is filled with zero. Output is suitable for the
    evaluator's Dataset(inputs=...). Per-particle datasets should instead be
    fed the previous execution's output buffers.
    """
    if not isinstance(num_instances,int) or isinstance(num_instances,bool) or not 0 <= num_instances <= 65536:
        raise ContextError('invalid dataset instance count')
    instructions = vm_disassemble(context.bytecode, profile=context.bytecode_profile).instructions
    needed = set()
    for ins in instructions:
        if ins.name.startswith('inputdata_') and ins.operands[0].value == dataset_index:
            kind = 'int32' if ins.name.endswith('int32') else ('half' if ins.name.endswith('half') else 'float')
            needed.add((kind,ins.operands[1].value))
    variables = compiled_data.get('Variables')
    layouts = compiled_data.get('VariableLayouts')
    if not isinstance(variables,list) or not isinstance(layouts,list) or len(variables) != len(layouts):
        raise ContextError('incomplete compiled dataset variable layouts')
    values = dict(values or {})
    store = parameter_store or {}
    data = _bytes(store.get('ParameterData',[]),'instance ParameterData')
    store_offsets = store.get('SortedParameterOffsets',[])
    inputs, provenance, consumed = {}, [], set()
    for variable,layout in zip(variables,layouts):
        name = variable.get('Name')
        info = layout.get('LayoutInfo',{})
        nf, ni, nh = (info.get(k,0) for k in ('NumFloatComponents','NumInt32Components','NumHalfComponents'))
        if not all(isinstance(v,int) and not isinstance(v,bool) and 0 <= v <= 64 for v in (nf,ni,nh)):
            raise ContextError(f'invalid component counts for {name}')
        float_start, int_start, half_start = (layout.get(k,0) for k in ('FloatComponentStart','Int32ComponentStart','HalfComponentStart'))
        if not all(isinstance(v,int) and not isinstance(v,bool) and 0 <= v <= 65535 for v in (float_start,int_start,half_start)):
            raise ContextError(f'invalid component starts for {name}')
        keys = ([('float',float_start+i) for i in range(nf)] + [('int32',int_start+i) for i in range(ni)]
                + [('half',half_start+i) for i in range(nh)])
        selected = [key for key in keys if key in needed]
        if not selected:
            continue
        if nh or (nf and ni):
            raise ContextError(f'unsupported mixed/half layout for {name}')
        count,fmt = (nf,'f') if nf else (ni,'i')
        if name in values:
            sequence = [values[name]] if count == 1 else values[name]
            try:
                if len(sequence) != count:
                    raise ValueError('component count')
                raw = struct.pack('<'+fmt*count,*sequence)
                components = struct.unpack('<'+fmt*count,raw)
                if fmt == 'f' and not all(math.isfinite(v) for v in components):
                    raise ValueError('nonfinite')
            except (TypeError,ValueError,struct.error,OverflowError) as exc:
                raise ContextError(f'invalid explicit dataset value {name}') from exc
            origin = 'explicit uniform input'
            consumed.add(name)
        else:
            entries = [entry for entry in store_offsets if entry.get('Name') == name]
            if len(entries) != 1:
                raise ContextError(f'missing explicit dataset input {name}')
            offsets = info.get('ComponentsOffsets')
            if not isinstance(offsets,list) or len(offsets) != count*2:
                raise ContextError(f'missing component byte offsets for {name}')
            base = entries[0].get('Offset')
            if not isinstance(base,int) or isinstance(base,bool) or base < 0:
                raise ContextError(f'invalid parameter store offset for {name}')
            components = []
            for offset in offsets[:count]:
                if not isinstance(offset,int) or isinstance(offset,bool) or offset < 0 or base+offset+4 > len(data):
                    raise ContextError(f'missing parameter bytes for {name}')
                components.append(struct.unpack_from('<'+fmt,data,base+offset)[0])
            origin = 'serialized instance parameter default'
        for component,key in enumerate(keys):
            if key in needed:
                if key in inputs:
                    raise ContextError(f'overlapping compiled dataset attribute {key}')
                inputs[key] = (components[component],)*num_instances
                provenance.append({'dataset':dataset_index,'kind':key[0],'attribute':key[1],
                                   'variable':name,'component':component,'provenance':origin,
                                   'value':components[component]})
    if needed-set(inputs):
        raise ContextError(f'unmapped required dataset attributes {sorted(needed-set(inputs))}')
    if set(values)-consumed:
        raise ContextError(f'unknown or unread explicit dataset values {sorted(set(values)-consumed)}')
    return DatasetInputContext(inputs,tuple(provenance))

# ---- niagara_runtime.py ----
# Bounded scalar evaluator for a deliberately small legacy VectorVM subset.
#
# Semantics checked against UE 5.3.2 VectorVM.cpp / VectorVMLegacy.h. This is
# not a Niagara scheduler or a validated replacement for Unreal execution.
# Registers are raw 32-bit words (float/int views share bits); constants are byte
# offsets across ordered constant tables. Float arithmetic rounds to binary32,
# but SIMD/FMA platform equivalence is not claimed. Random/noise, external
# interfaces, persistent IDs, GPU programs and unimplemented math fail closed.
# No Blender dependency, implicit parameters, fabricated motion, or I/O.



class ExecutionError(ValueError):
    pass


def float_word(value):
    """Encode a binary32 float as an unsigned register word."""
    try:
        return struct.unpack('<I', struct.pack('<f', value))[0]
    except OverflowError:
        return 0xff800000 if value < 0 else 0x7f800000


def word_float(value):
    return struct.unpack('<f', struct.pack('<I', value & 0xffffffff))[0]


def word_int(value):
    value &= 0xffffffff
    return value - 0x100000000 if value & 0x80000000 else value


@dataclass
class Dataset:
    """Typed attributes indexed within each type, not a flattened register list.

    inputs: {('float'|'int32', attribute): sequence of numeric values}.
    output_capacities: same keys with explicit maximum output lengths.
    instance_offset applies to both input reads and output writes, like UE's
    FDataSetMeta. access_index is the last acquired index (initially -1).
    Half storage is intentionally unsupported until engine comparison exists.
    """
    inputs: dict = field(default_factory=dict)
    output_capacities: dict = field(default_factory=dict)
    instance_offset: int = 0
    access_index: int = -1


@dataclass(frozen=True)
class ExecutionResult:
    registers: dict
    outputs: dict
    access_indices: dict
    instruction_count: int
    taints: dict = field(default_factory=dict)
    output_taints: dict = field(default_factory=dict)
    dataset_taints: dict = field(default_factory=dict)
    id_states: dict = field(default_factory=dict)
    ext_calls: list = field(default_factory=list)


_FLOAT = set('add sub mul div mad lerp neg abs clamp min max sqrt fmod'.split())
_INT = set('addi subi muli divi clampi mini maxi absi negi'.split())
_COMPARE = set('cmplt cmple cmpgt cmpge cmpeq cmpneq cmplti cmplei cmpgti cmpgei cmpeqi cmpneqi'.split())
_BITS = set('bit_and bit_or bit_xor bit_not logic_and logic_or logic_xor logic_not'.split())
_CONVERT = set('i2f f2i f2b b2f i2b b2i fasi iasf'.split())
SUPPORTED_OPS = frozenset(_FLOAT | _INT | _COMPARE | _BITS | _CONVERT | {
    'done', 'select', 'inputdata_float', 'inputdata_int32',
    'inputdata_noadvance_float', 'inputdata_noadvance_int32',
    'outputdata_float', 'outputdata_int32', 'acquireindex', 'exec_index',
    'enter_stat_scope', 'exit_stat_scope'})


def execute(bytecode, *, num_instances, constant_tables=(), datasets=None,
            initial_registers=None, start_instance=0, external_functions=(),
            profile='ue5.3-legacy', max_instances=65536,
            max_instructions=100000, max_lane_operations=2000000,
            max_storage_words=2000000, x86_semantics=False,
            math_backend="reference", svml_shim_path=None, fma_mode="unfused",
            external_handler=None, id_states=None, rng_seed=None):
    """Execute explicit caller-provided context, returning new private buffers.

    Unsupported instructions anywhere reject the entire program before it runs.
    initial_registers contains raw uint32 lanes, never implicit float values.
    Missing constants, attributes/registers and invalid indices are errors.
    Input objects are never modified, including when execution fails.
    Bounds limit lanes, instructions, work, and combined register/output storage.
    """
    if x86_semantics:
        return _execute57(bytecode, num_instances=num_instances, constant_tables=constant_tables,
            datasets=datasets, initial_registers=initial_registers, start_instance=start_instance,
            external_functions=external_functions, profile=profile, max_instances=max_instances,
            max_instructions=max_instructions, max_lane_operations=max_lane_operations,
            max_storage_words=max_storage_words, math_backend=math_backend,
            svml_shim_path=svml_shim_path, fma_mode=fma_mode,
            external_handler=external_handler, id_states=id_states, rng_seed=rng_seed)
    if (math_backend != 'reference' or svml_shim_path is not None or fma_mode != 'unfused'
            or external_handler is not None or id_states is not None or rng_seed is not None):
        raise ExecutionError('requires x86_semantics')

    def bounded_int(value, minimum, maximum, label):
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ExecutionError(f'invalid {label}: {value!r}')
        return value

    bounded_int(num_instances, 0, max_instances, 'num_instances')
    bounded_int(start_instance, 0, 0x7fffffff - num_instances, 'start_instance')
    if len(bytecode) > max_storage_words * 4:
        raise ExecutionError('bytecode storage budget exceeded')
    program = vm_disassemble(bytecode, external_functions, profile=profile)
    if len(program.instructions) > max_instructions or len(program.instructions) * num_instances > max_lane_operations:
        raise ExecutionError('instruction/lane work budget exceeded')
    for ins in program.instructions:
        if ins.name not in SUPPORTED_OPS:
            raise ExecutionError(f'byte {ins.offset}: unsupported operation {ins.name}')

    tables = tuple(bytes(table) for table in constant_tables)
    if sum(map(len, tables)) > max_storage_words * 4:
        raise ExecutionError('constant storage budget exceeded')
    datasets = dict(datasets or {})
    registers = {}
    outputs = {}
    counters = {}
    storage = 0

    def allocate(words):
        nonlocal storage
        storage += words
        if storage > max_storage_words:
            raise ExecutionError('register/output storage budget exceeded')

    for index, ds in datasets.items():
        bounded_int(index, 0, 65535, 'dataset index')
        bounded_int(ds.instance_offset, 0, max_storage_words, 'instance offset')
        bounded_int(ds.access_index, -1, 0x7ffffffe, 'access index')
        counters[index] = ds.access_index
        for key, capacity in ds.output_capacities.items():
            if not isinstance(key, tuple) or len(key) != 2 or key[0] not in ('float', 'int32'):
                raise ExecutionError('invalid output attribute key')
            bounded_int(key[1], 0, 65535, 'output attribute')
            bounded_int(capacity, 0, max_storage_words, 'output capacity')
            allocate(capacity)
            outputs[index, key] = [None] * capacity
    for index, values in (initial_registers or {}).items():
        bounded_int(index, 0, 65535, 'register index')
        if len(values) != num_instances:
            raise ExecutionError('initial register lane count mismatch')
        allocate(num_instances)
        registers[index] = [bounded_int(v, 0, 0xffffffff, 'raw register word') for v in values]

    def store(index, values):
        if index not in registers:
            allocate(num_instances)
        registers[index] = [v & 0xffffffff for v in values]

    def source(operand):
        if operand.kind == 'constant':
            offset = operand.value
            for table in tables:
                if offset < len(table):
                    if offset + 4 > len(table):
                        raise ExecutionError('constant crosses table boundary')
                    word = struct.unpack_from('<I', table, offset)[0]
                    return [word] * num_instances
                offset -= len(table)
            raise ExecutionError(f'missing constant byte offset {operand.value}')
        if operand.value not in registers:
            raise ExecutionError(f'uninitialized register {operand.value}')
        return registers[operand.value]

    for ins in program.instructions:
        name = ins.name
        op = ins.operands
        if name == 'done':
            break
        if name in ('enter_stat_scope', 'exit_stat_scope'):
            continue  # Profiling only; no simulation state.
        try:
            if name == 'exec_index':
                store(op[0].value, range(start_instance, start_instance + num_instances))
            elif name.startswith('inputdata_'):
                ds = datasets[op[0].value]
                kind = 'int32' if name.endswith('int32') else 'float'
                values = ds.inputs[kind, op[1].value]
                noadvance = 'noadvance' in name
                selected = [values[ds.instance_offset + (0 if noadvance else lane)] for lane in range(num_instances)]
                store(op[2].value, [float_word(v) if kind == 'float' else int(v) for v in selected])
            elif name == 'acquireindex':
                index = op[0].value
                if index not in counters:
                    raise ExecutionError(f'missing dataset {index}')
                acquired = []
                for valid in source(op[1]):
                    if valid:
                        if counters[index] >= 0x7ffffffe:
                            raise ExecutionError('dataset counter overflow')
                        counters[index] += 1
                        acquired.append(counters[index])
                    else:
                        acquired.append(0xffffffff)
                store(op[2].value, acquired)
            elif name.startswith('outputdata_'):
                index = op[0].value
                ds = datasets[index]
                kind = 'int32' if name.endswith('int32') else 'float'
                target = outputs[index, (kind, op[3].value)]
                indices = source(op[1])
                values = source(op[2])
                for lane, word in enumerate(values):
                    dest = word_int(indices[lane])
                    if dest == -1:
                        continue
                    dest += ds.instance_offset
                    if dest < ds.instance_offset or dest >= len(target):
                        raise ExecutionError('output index outside provided capacity')
                    target[dest] = word_float(word) if kind == 'float' else word_int(word)
            else:
                sources = [source(o) for o in op[:-1]]
                result = []
                for lane in range(num_instances):
                    words = [s[lane] for s in sources]
                    if name == 'select':
                        mask, a, b = words
                        result.append((mask & a) | ((~mask & 0xffffffff) & b))
                        continue
                    if name in ('fasi', 'iasf'):
                        result.append(words[0])
                        continue
                    if name == 'i2f':
                        result.append(float_word(word_int(words[0])))
                        continue
                    if name in ('b2i', 'b2f'):
                        result.append(words[0] & (1 if name == 'b2i' else float_word(1.0)))
                        continue
                    if name in ('f2i', 'f2b', 'i2b'):
                        value = word_int(words[0]) if name == 'i2b' else word_float(words[0])
                        if not math.isfinite(value):
                            raise ExecutionError('nonfinite conversion is outside verified subset')
                        if name == 'f2i':
                            if not -2147483648 <= value < 2147483648:
                                raise ExecutionError('out-of-range f2i is outside verified subset')
                            result.append(math.trunc(value) & 0xffffffff)
                        else:
                            result.append(0xffffffff if value > 0 else 0)
                        continue
                    is_int = name in _INT or name in _BITS or (name in _COMPARE and name.endswith('i'))
                    values = [word_int(w) if is_int else word_float(w) for w in words]
                    if name in _COMPARE:
                        base = name[:-1] if is_int else name
                        a, b = values
                        truth = {'cmplt': a < b, 'cmple': a <= b, 'cmpgt': a > b,
                                 'cmpge': a >= b, 'cmpeq': a == b, 'cmpneq': a != b}[base]
                        result.append(0xffffffff if truth else 0)
                        continue
                    if not is_int and not all(math.isfinite(v) for v in values):
                        raise ExecutionError('nonfinite arithmetic is outside verified subset')
                    base = name[:-1] if name in _INT else name
                    a = values[0]
                    b = values[1] if len(values) > 1 else None
                    c = values[2] if len(values) > 2 else None
                    if base == 'add': value = a + b
                    elif base == 'sub': value = a - b
                    elif base == 'mul': value = a * b
                    elif base == 'div':
                        if is_int:
                            value = 0 if b == 0 else (0x7fffffff if a == -0x80000000 and b == -1 else (abs(a) // abs(b)) * (-1 if (a < 0) != (b < 0) else 1))
                        else:
                            value = a / b if abs(b) > word_float(float_word(1e-8)) else 0.0
                    elif base == 'mad': value = word_float(float_word(a * b)) + c
                    elif base == 'lerp': value = word_float(float_word(a * word_float(float_word(1.0 - c)))) + word_float(float_word(b * c))
                    elif base == 'neg': value = -a
                    elif base == 'abs': value = abs(a)
                    elif base == 'min': value = min(a, b)
                    elif base == 'max': value = max(a, b)
                    elif base == 'clamp': value = min(max(a, b), c)
                    elif base == 'sqrt': value = math.sqrt(a) if a > word_float(float_word(1e-8)) else 0.0
                    elif base == 'fmod': value = math.fmod(a, b) if abs(b) > word_float(float_word(1e-8)) else 0.0
                    elif name in ('bit_and', 'logic_and'): value = words[0] & words[1]
                    elif name in ('bit_or', 'logic_or'): value = words[0] | words[1]
                    elif name in ('bit_xor', 'logic_xor'): value = words[0] ^ words[1]
                    elif name in ('bit_not', 'logic_not'): value = ~words[0]
                    else: raise ExecutionError(f'unimplemented operation {name}')
                    result.append(value & 0xffffffff if is_int else float_word(value))
                store(op[-1].value, result)
        except (KeyError, IndexError, TypeError, OverflowError) as exc:
            raise ExecutionError(f'byte {ins.offset} ({name}): invalid or missing execution input: {exc}') from exc
    return ExecutionResult({k: tuple(v) for k, v in registers.items()},
                           {k: tuple(v) for k, v in outputs.items()},
                           counters, len(program.instructions))



@dataclass
class IdState:
    id_table: list
    free_ids: list
    num_free_ids: int
    free_capacity: int
    max_used_id: int
    num_spawned_ids: int
    spawned_ids: list
    id_acquire_tag: int


SUPPORTED_OPS_57 = SUPPORTED_OPS | frozenset('rcp rsq sin cos tan acos atan2 pow floor ceil trunc frac round sign bit_lshift bit_rshift random acquire_id update_id external_func_call'.split())
_SVML57 = frozenset('sin cos tan acos atan2 pow fmod'.split())
_EPS57 = word_float(float_word(1e-8))
_SVML_CACHE = {}


def _f32(value):
    return word_float(float_word(value))


def _fma57(a, b, c):
    try: return _f32(math.fma(a, b, c))
    except ValueError: return float('nan')


def _round57(name, value):
    if not math.isfinite(value): return value
    result = float(round(value) if name == 'round' else getattr(math, name)(value))
    return math.copysign(0.0, value) if result == 0.0 else result


def _backend57(name, a, b, backend, path):
    if backend == 'reference':
        result = []
        for i, x in enumerate(a):
            try:
                y = (1.0 / x if x else 0.0) if name == 'rcp' else getattr(math, name)(x, b[i]) if b is not None else getattr(math, name)(x)
            except ValueError: y = float('nan')
            except OverflowError: y = float('inf')
            result.append(_f32(y))
        return result
    import ctypes
    import numpy as np
    key = str(path)
    if key not in _SVML_CACHE:
        dll = ctypes.CDLL(key)
        if dll.dr_shim_version() != 1: raise ExecutionError('invalid SVML shim version')
        _SVML_CACHE[key] = dll
    dll = _SVML_CACHE[key]
    p = ctypes.POINTER(ctypes.c_float)
    aa = np.asarray(a, dtype=np.float32); out = np.empty(len(a), dtype=np.float32)
    fn = getattr(dll, 'dr_' + name); fn.restype = None
    if b is None:
        fn.argtypes = [p, p, ctypes.c_int]
        fn(aa.ctypes.data_as(p), out.ctypes.data_as(p), len(a))
    else:
        bb = np.asarray(b, dtype=np.float32); fn.argtypes = [p, p, p, ctypes.c_int]
        fn(aa.ctypes.data_as(p), bb.ctypes.data_as(p), out.ctypes.data_as(p), len(a))
    return [float(v) for v in out]


def rng_seed_state(pcg_state, pcg_inc):
    state, inc = pcg_state & 0xffffffffffffffff, pcg_inc & 0xffffffffffffffff
    s = []
    for i in range(5):
        row = []
        for j in range(4):
            old = state; state = (old * 6364136223846793005 + inc) & 0xffffffffffffffff
            xs = (((old >> 18) ^ old) >> 27) & 0xffffffff; rot = old >> 59
            row.append(((xs >> rot) | (xs << ((-rot) & 31))) & 0xffffffff)
        s.append(row)
    return s, [inc & 0xffffffff, (inc >> 32) & 0xffffffff, state & 0xffffffff, (state >> 32) & 0xffffffff]


def _rng57_values(rng, n):
    s, c = rng; values = []
    for _ in range((n + 3) // 4):
        t, first = s[4][:], s[0][:]
        s[4], s[3], s[2], s[1] = s[3], s[2], s[1], first
        for j in range(4):
            t[j] ^= t[j] >> 2; t[j] ^= (t[j] << 1) & 0xffffffff; t[j] ^= (first[j] << 4) & 0xffffffff
            c[j] = (c[j] + 362437) & 0xffffffff
            values.append(word_float((((t[j] + c[j]) & 0xffffffff) >> 9) | 0x3f800000) - 1.0)
        s[0] = t
    return values[:n]


def _execute57(bytecode, *, num_instances, constant_tables=(), datasets=None,
            initial_registers=None, start_instance=0, external_functions=(),
            profile='ue5.3-legacy', max_instances=65536,
            max_instructions=100000, max_lane_operations=2000000,
            max_storage_words=2000000, math_backend="reference", svml_shim_path=None,
            fma_mode="unfused", external_handler=None, id_states=None, rng_seed=None):
    """Execute explicit caller-provided context, returning new private buffers.

    Unsupported instructions anywhere reject the entire program before it runs.
    initial_registers contains raw uint32 lanes, never implicit float values.
    Missing constants, attributes/registers and invalid indices are errors.
    Input objects are never modified, including when execution fails.
    Bounds limit lanes, instructions, work, and combined register/output storage.
    """
    def bounded_int(value, minimum, maximum, label):
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ExecutionError(f'invalid {label}: {value!r}')
        return value

    bounded_int(num_instances, 0, max_instances, 'num_instances')
    bounded_int(start_instance, 0, 0x7fffffff - num_instances, 'start_instance')
    if len(bytecode) > max_storage_words * 4:
        raise ExecutionError('bytecode storage budget exceeded')
    program = vm_disassemble(bytecode, external_functions, profile=profile)
    if len(program.instructions) > max_instructions or len(program.instructions) * num_instances > max_lane_operations:
        raise ExecutionError('instruction/lane work budget exceeded')
    for ins in program.instructions:
        if ins.name not in SUPPORTED_OPS_57:
            raise ExecutionError(f'byte {ins.offset}: unsupported operation {ins.name}')

    if math_backend not in ('reference', 'svml_shim') or fma_mode not in ('unfused', 'fused'):
        raise ExecutionError('invalid math backend or fma mode')
    if math_backend == 'svml_shim' and svml_shim_path is None:
        raise ExecutionError('svml_shim requires svml_shim_path')
    names = {i.name for i in program.instructions}
    if 'random' in names and rng_seed is None:
        raise ExecutionError('random requires rng_seed')
    if 'external_func_call' in names:
        if external_handler != 'gate_v_stub' and not callable(external_handler):
            raise ExecutionError('external_func_call requires gate_v_stub or a handler')
        if not isinstance(external_functions, list):
            raise ExecutionError('external_functions must be a list of signature dicts')
    for ins in program.instructions:
        if ins.name in ('acquire_id', 'update_id') and ins.operands[0].value not in (id_states or {}):
            raise ExecutionError('ID operation requires id_states')
    working_ids = {d: IdState(list(s.id_table), list(s.free_ids), s.num_free_ids,
        s.free_capacity, s.max_used_id, s.num_spawned_ids, list(s.spawned_ids), s.id_acquire_tag)
        for d, s in (id_states or {}).items()}
    taints, output_taints, dataset_taints, ext_calls = {}, {}, {}, []
    rng = rng_seed_state(*rng_seed) if rng_seed is not None else None
    tables = tuple(bytes(table) for table in constant_tables)
    if sum(map(len, tables)) > max_storage_words * 4:
        raise ExecutionError('constant storage budget exceeded')
    datasets = dict(datasets or {})
    registers = {}
    outputs = {}
    counters = {}
    storage = 0

    def allocate(words):
        nonlocal storage
        storage += words
        if storage > max_storage_words:
            raise ExecutionError('register/output storage budget exceeded')

    for index, ds in datasets.items():
        bounded_int(index, 0, 65535, 'dataset index')
        bounded_int(ds.instance_offset, 0, max_storage_words, 'instance offset')
        bounded_int(ds.access_index, -1, 0x7ffffffe, 'access index')
        counters[index] = ds.access_index
        for key, capacity in ds.output_capacities.items():
            if not isinstance(key, tuple) or len(key) != 2 or key[0] not in ('float', 'int32'):
                raise ExecutionError('invalid output attribute key')
            bounded_int(key[1], 0, 65535, 'output attribute')
            bounded_int(capacity, 0, max_storage_words, 'output capacity')
            allocate(capacity)
            outputs[index, key] = [None] * capacity
    for index, values in (initial_registers or {}).items():
        bounded_int(index, 0, 65535, 'register index')
        if len(values) != num_instances:
            raise ExecutionError('initial register lane count mismatch')
        allocate(num_instances)
        registers[index] = [bounded_int(v, 0, 0xffffffff, 'raw register word') for v in values]

    def store(index, values, taint=frozenset()):
        if index not in registers:
            allocate(num_instances)
        registers[index] = [v & 0xffffffff for v in values]
        taints[index] = frozenset(taint)

    def source(operand):
        if operand.kind == 'constant':
            offset = operand.value
            for table in tables:
                if offset < len(table):
                    if offset + 4 > len(table):
                        raise ExecutionError('constant crosses table boundary')
                    word = struct.unpack_from('<I', table, offset)[0]
                    return [word] * num_instances
                offset -= len(table)
            raise ExecutionError(f'missing constant byte offset {operand.value}')
        if operand.value not in registers:
            raise ExecutionError(f'uninitialized register {operand.value}')
        return registers[operand.value]

    def source_taint(operand):
        return taints.get(operand.value, frozenset()) if operand.kind != 'constant' else frozenset()

    for ins in program.instructions:
        name = ins.name
        op = ins.operands
        if name == 'done':
            break
        if name in ('enter_stat_scope', 'exit_stat_scope'):
            continue  # Profiling only; no simulation state.
        try:
            if name == 'exec_index':
                store(op[0].value, range(start_instance, start_instance + num_instances))
            elif name.startswith('inputdata_'):
                ds = datasets[op[0].value]
                kind = 'int32' if name.endswith('int32') else 'float'
                values = ds.inputs[kind, op[1].value]
                noadvance = 'noadvance' in name
                selected = [values[ds.instance_offset + (0 if noadvance else lane)] for lane in range(num_instances)]
                store(op[2].value, [float_word(v) if kind == 'float' else int(v) for v in selected])
            elif name == 'acquireindex':
                index = op[0].value
                if index not in counters:
                    raise ExecutionError(f'missing dataset {index}')
                acquired = []
                for valid in source(op[1]):
                    if valid & 0x80000000:
                        if counters[index] >= 0x7ffffffe:
                            raise ExecutionError('dataset counter overflow')
                        counters[index] += 1
                        acquired.append(counters[index])
                    else:
                        acquired.append(0xffffffff)
                condition_taint = source_taint(op[1])
                store(op[2].value, acquired, condition_taint)
                dataset_taints[index] = dataset_taints.get(index, frozenset()) | condition_taint
            elif name.startswith('outputdata_'):
                index = op[0].value
                ds = datasets[index]
                kind = 'int32' if name.endswith('int32') else 'float'
                target = outputs[index, (kind, op[3].value)]
                indices = source(op[1])
                values = source(op[2])
                for lane, word in enumerate(values):
                    dest = word_int(indices[lane])
                    if dest == -1:
                        continue
                    dest += ds.instance_offset
                    if dest < ds.instance_offset or dest >= len(target):
                        raise ExecutionError('output index outside provided capacity')
                    target[dest] = word_float(word) if kind == 'float' else word_int(word)
                key = (index, (kind, op[3].value))
                output_taints[key] = output_taints.get(key, frozenset()) | source_taint(op[1]) | source_taint(op[2])
            elif name == 'acquire_id':
                d, dest, tag = (o.value for o in op)
                state = working_ids[d]
                if state.num_free_ids < num_instances:
                    raise ExecutionError('insufficient free IDs')
                acquired = [state.free_ids[state.num_free_ids - 1 - i] for i in range(num_instances)]
                state.num_free_ids -= num_instances
                state.free_ids = state.free_ids[:state.num_free_ids]
                state.num_spawned_ids += num_instances
                state.spawned_ids = acquired + state.spawned_ids
                store(dest, acquired, dataset_taints.get(d, frozenset()))
                store(tag, [state.id_acquire_tag] * num_instances, dataset_taints.get(d, frozenset()))
            elif name == 'update_id':
                d, id_reg, index_reg = (o.value for o in op)
                state = working_ids[d]
                ids, indices = registers[id_reg], registers[index_reg]
                surviving = [i for i, w in enumerate(indices) if w != 0xffffffff]
                offset = datasets[d].instance_offset if d in datasets else 0
                for i in surviving:
                    state.id_table[ids[i]] = word_int(indices[i]) + offset
                dead = [ids[i] for i, w in enumerate(indices) if w == 0xffffffff]
                state.free_ids += dead
                state.num_free_ids += len(dead)
                if surviving:
                    state.max_used_id = max(state.max_used_id, max(word_int(w) for w in ids[:len(surviving)]))
                dataset_taints[d] = dataset_taints.get(d, frozenset()) | taints.get(id_reg, frozenset()) | taints.get(index_reg, frozenset())
            elif name == 'external_func_call':
                fn = op[0].value
                signature = external_functions[fn]
                ni, no = len(signature['InputParamLocations']), signature['NumOutputs']
                args, arg_taints = [], []
                for operand in op[1:1 + ni]:
                    w = operand.value
                    if w == 0xffff:
                        raise ExecutionError('invalid external input operand')
                    arg = Operand('register', w & 0x7fff) if w & 0x8000 else Operand('constant', w)
                    args.append(source(arg)); arg_taints.append(source_taint(arg))
                destinations = [o.value for o in op[1 + ni:]]
                if any(w != 0xffff and w & 0x8000 for w in destinations):
                    raise ExecutionError('invalid external output operand')
                if callable(external_handler):
                    import numpy as np
                    outs = external_handler(fn, [np.asarray([v & 0xffffffff for v in values], dtype=np.uint32) for values in args], num_instances, start_instance)
                    if len(outs) != no or any(len(values) != num_instances for values in outs):
                        raise ExecutionError('external handler output shape')
                    combined = frozenset().union(*arg_taints)
                    for dest, values in zip(destinations, outs):
                        if dest != 0xffff: store(dest, [int(v) & 0xffffffff for v in values], combined)
                    ext_calls.append({'fn': fn, 'start': start_instance, 'count': num_instances,
                        'input_taints': [sorted(t) for t in arg_taints]})
                    continue
                result = [[] for _ in range(no)]; inputs = []
                for lane in range(num_instances):
                    h = (0x811c9dc5 ^ fn) & 0xffffffff
                    for values in args:
                        w = values[lane]; inputs.append(w); h = ((h ^ w) * 0x01000193) & 0xffffffff
                    for out_index in range(no):
                        hk = ((h ^ ((0x9e3779b9 + out_index) & 0xffffffff)) * 0x01000193) & 0xffffffff
                        result[out_index].append(float_word(word_float((hk >> 9) | 0x3f800000) - 1.0))
                combined = frozenset().union(*arg_taints)
                for dest, values in zip(destinations, result):
                    if dest != 0xffff: store(dest, values, combined)
                ext_calls.append({'fn': fn, 'start': start_instance, 'count': num_instances,
                    'inputs': inputs, 'input_taints': [sorted(t) for t in arg_taints]})
            else:
                sources = [source(o) for o in op[:-1]]
                own = ({'rng'} if name == 'random' else {'svml'} if name in _SVML57 else
                    {'hwest'} if name == 'rcp' else {'fma'} if name in ('mad', 'lerp') else set())
                result_taint = frozenset(own).union(*(source_taint(o) for o in op[:-1]))
                backend_values = None
                if name in _SVML57 or name == 'rcp':
                    values_a = [word_float(w) for w in sources[0]]
                    values_b = [word_float(w) for w in sources[1]] if len(sources) > 1 else None
                    backend_name = name
                    if name == 'acos':
                        values_b = values_a
                        values_a = [_f32(math.sqrt(p)) if p > _EPS57 else 0.0 for p in
                            [_f32(_f32(1 - a) * _f32(1 + a)) for a in values_a]]
                        backend_name = 'atan2'
                    backend_values = _backend57(backend_name, values_a, values_b, math_backend, svml_shim_path)
                random_values = _rng57_values(rng, num_instances) if name == 'random' else None
                result = []
                for lane in range(num_instances):
                    words = [s[lane] for s in sources]
                    if name == 'select':
                        mask, a, b = words
                        result.append((mask & a) | ((~mask & 0xffffffff) & b))
                        continue
                    if name in ('fasi', 'iasf'):
                        result.append(words[0])
                        continue
                    if name == 'i2f':
                        result.append(float_word(word_int(words[0])))
                        continue
                    if name in ('b2i', 'b2f'):
                        result.append(words[0] & (1 if name == 'b2i' else float_word(1.0)))
                        continue
                    if name == 'abs':
                        result.append(words[0] & 0x7fffffff)
                        continue
                    if name in ('f2i', 'f2b', 'i2b'):
                        value = word_int(words[0]) if name == 'i2b' else word_float(words[0])
                        if name == 'f2i':
                            result.append(0x80000000 if not math.isfinite(value) or not -2147483648 <= value < 2147483648 else math.trunc(value) & 0xffffffff)
                        else:
                            result.append(0xffffffff if value > 0 else 0)
                        continue
                    is_int = name in _INT or name in _BITS or name in ('bit_lshift', 'bit_rshift') or (name in _COMPARE and name.endswith('i'))
                    values = [word_int(w) if is_int else word_float(w) for w in words]
                    if name in _COMPARE:
                        base = name[:-1] if is_int else name
                        a, b = values
                        truth = {'cmplt': a < b, 'cmple': a <= b, 'cmpgt': a > b,
                                 'cmpge': a >= b, 'cmpeq': a == b, 'cmpneq': a != b}[base]
                        result.append(0xffffffff if truth else 0)
                        continue
                    base = name[:-1] if name in _INT else name
                    a = values[0]
                    b = values[1] if len(values) > 1 else None
                    c = values[2] if len(values) > 2 else None
                    if name in _SVML57 or name == 'rcp':
                        value = backend_values[lane]
                        if name == 'rcp' and not abs(a) > _EPS57: value = 0.0
                        if name == 'pow' and not a > _EPS57: value = 0.0
                        if name == 'fmod' and abs(b) <= _EPS57: value = 0.0
                    elif name == 'rsq': value = 1.0 / _f32(math.sqrt(a)) if a > _EPS57 else 0.0
                    elif name in ('floor', 'ceil', 'trunc', 'round'): value = _round57(name, a)
                    elif name == 'frac': value = a - _round57('trunc', a)
                    elif name == 'sign': value = 1.0 if a >= 0 else -1.0
                    elif name == 'random': value = random_values[lane] * a
                    elif name == 'bit_lshift': value = 0 if (words[1] & 0xffffffff) > 31 else (words[0] << words[1]) & 0xffffffff
                    elif name == 'bit_rshift': value = 0 if (words[1] & 0xffffffff) > 31 else (words[0] & 0xffffffff) >> words[1]
                    elif base == 'add': value = a + b
                    elif base == 'sub': value = a - b
                    elif base == 'mul': value = a * b
                    elif base == 'div':
                        if is_int:
                            value = 0 if b == 0 else (0x7fffffff if a == -0x80000000 and b == -1 else (abs(a) // abs(b)) * (-1 if (a < 0) != (b < 0) else 1))
                        else:
                            value = a / b if abs(b) > word_float(float_word(1e-8)) else 0.0
                    elif base == 'mad': value = _fma57(a, b, c) if fma_mode == 'fused' else _f32(a * b) + c
                    elif base == 'lerp':
                        t = _f32(a * _f32(1.0 - c))
                        value = _fma57(b, c, t) if fma_mode == 'fused' else _f32(b * c) + t
                    elif base == 'neg': value = -a if is_int else 0.0 - a
                    elif base == 'abs': value = abs(a)
                    elif base == 'min': value = min(a, b) if is_int else a if a < b else b
                    elif base == 'max': value = max(a, b) if is_int else a if a > b else b
                    elif base == 'clamp':
                        t = max(a, b) if is_int else a if a > b else b
                        value = min(t, c) if is_int else t if t < c else c
                    elif base == 'sqrt': value = math.sqrt(a) if a > word_float(float_word(1e-8)) else 0.0
                    elif base == 'fmod': value = math.fmod(a, b) if abs(b) > word_float(float_word(1e-8)) else 0.0
                    elif name in ('bit_and', 'logic_and'): value = words[0] & words[1]
                    elif name in ('bit_or', 'logic_or'): value = words[0] | words[1]
                    elif name in ('bit_xor', 'logic_xor'): value = words[0] ^ words[1]
                    elif name in ('bit_not', 'logic_not'): value = ~words[0]
                    else: raise ExecutionError(f'unimplemented operation {name}')
                    result.append(value & 0xffffffff if is_int else float_word(value))
                store(op[-1].value, result, result_taint)
        except (KeyError, IndexError, TypeError, OverflowError) as exc:
            raise ExecutionError(f'byte {ins.offset} ({name}): invalid or missing execution input: {exc}') from exc
    return ExecutionResult({k: tuple(v) for k, v in registers.items()},
                           {k: tuple(v) for k, v in outputs.items()},
                           counters, len(program.instructions), taints, output_taints,
                           dataset_taints, working_ids, ext_calls)


# ---- niagara_di.py ----
# Niagara data interfaces for the offline VM (Waves N4-N5; spec N-O3 §1-§11).
#
# Binding of VM external functions to cooked data interfaces, the UE 5.7 implementations of the supported DI functions,
# the Gate V hash stub (vectorized) and the external-call handler used by both VM backends.



FAST_PATH = 'FastPathLibrary'
CURVE_ELEMENTS = {
    ('NiagaraDataInterfaceCurve', 'SampleCurve'): 1,
    ('NiagaraDataInterfaceVector2DCurve', 'SampleVector2DCurve'): 2,
    ('NiagaraDataInterfaceVectorCurve', 'SampleVectorCurve'): 3,
    ('NiagaraDataInterfaceVector4Curve', 'SampleColorCurve'): 4,
    ('NiagaraDataInterfaceColorCurve', 'SampleColorCurve'): 4,
}
CURVE_CLASSES = {cls: e for (cls, _), e in CURVE_ELEMENTS.items()}
di_SPRITE_INFO = 'NiagaraDataInterfaceSpriteRendererInfo'
EMITTER_PROPERTIES = 'NiagaraDataInterfaceEmitterProperties'
AUDIO_PLAYER = 'NiagaraDataInterfaceAudioPlayer'
di_VECTOR_FIELD = 'NiagaraDataInterfaceVectorField'
ARRAY_MESH = 'NiagaraDataInterfaceArrayMesh'
di_CAMERA = 'NiagaraDataInterfaceCamera'
di_PARTICLE_READ = 'NiagaraDataInterfaceParticleRead'
# value type -> (float components, int components, default words); NiagaraDataInterfaceParticleRead.cpp ReadWithCheck defaults
PR_TYPES = {'Int': (0, 1, [0]), 'Bool': (0, 1, [0]), 'Float': (1, 0, [0]), 'Vector 2D': (2, 0, [0, 0]), 'Vector': (3, 0, [0, 0, 0]),
            'Vector 4': (4, 0, [0, 0, 0, 0]), 'Color': (4, 0, [0x3f800000] * 4), 'Position': (3, 0, [0, 0, 0]),
            'Quaternion': (4, 0, [0, 0, 0, 0x3f800000]), 'ID': (0, 2, [0xffffffff, 0xffffffff])}
PR_READS = {('Get %s By %s' % (t, k)): (t, k) for t in PR_TYPES for k in ('Index', 'ID')}
di_COLLISION = 'NiagaraDataInterfaceCollisionQuery'
# class -> (cooked property, words per element, component keys, default element as floats/bool)
ARRAY_TYPES = {
    'NiagaraDataInterfaceArrayFloat': ('FloatData', 1, None, (0.0,)),
    'NiagaraDataInterfaceArrayFloat2': ('InternalFloatData', 2, ('X', 'Y'), (0.0, 0.0)),
    'NiagaraDataInterfaceArrayFloat3': ('InternalFloatData', 3, ('X', 'Y', 'Z'), (0.0, 0.0, 0.0)),
    'NiagaraDataInterfaceArrayPosition': ('PositionData', 3, ('X', 'Y', 'Z'), (0.0, 0.0, 0.0)),
    'NiagaraDataInterfaceArrayColor': ('ColorData', 4, ('R', 'G', 'B', 'A'), (1.0, 1.0, 1.0, 1.0)),
    'NiagaraDataInterfaceArrayBool': ('BoolData', 1, None, (False,)),
}
ARRAY_FUNCTIONS = ('Length', 'LastIndex', 'IsValidIndex', 'Get', 'SetArrayElem', 'Resize')
TRUE_WORD = 0xffffffff
IMPLEMENTED = frozenset(CURVE_ELEMENTS) | {(FAST_PATH, 'FastMatrixToQuaternion'), (di_SPRITE_INFO, 'GetSubUVDetails'),
                                           (EMITTER_PROPERTIES, 'SetFixedBounds'), (AUDIO_PLAYER, 'PlayAudioAtLocation'),
                                           (di_VECTOR_FIELD, 'SampleField'), (ARRAY_MESH, 'Length'),
                                           (di_CAMERA, 'GetCameraPropertiesCPU/GPU'), (di_COLLISION, 'PerformCollisionQueryAsyncCPU'),
                                           (di_PARTICLE_READ, 'Get Num Particles'), (di_PARTICLE_READ, 'Get Particle Index'),
                                           (di_PARTICLE_READ, 'Get Particle Index From ID Table')} | {(di_PARTICLE_READ, f) for f in PR_READS} | \
    {(cls, fn) for cls in ARRAY_TYPES for fn in ARRAY_FUNCTIONS}


class DIError(ValueError):
    pass


@dataclass(frozen=True)
class Binding:
    fn_index: int
    owner_name: str
    name: str
    num_inputs: int
    num_outputs: int
    specifiers: tuple
    variadic: tuple
    kind: str
    di_class: object
    di_export: object
    user_ptr: int

    @property
    def label(self):
        return '%s.%s' % (self.di_class if self.kind == 'di' else FAST_PATH, self.name)

    @property
    def identity(self):
        """Spec §11.4: User.* parameters share one instance per name; others are keyed by the bound DI export."""
        return ('user', self.owner_name) if str(self.owner_name).startswith('User.') else ('export', self.di_export)


@dataclass(frozen=True)
class CurveLUT:
    di_class: str
    e: int
    lut: np.ndarray
    min_time: np.float32
    max_time: np.float32
    inv_time_range: np.float32
    num_samples_minus_one: np.float32


@dataclass(frozen=True)
class VectorFieldData:
    asset: str
    size: tuple
    bounds_min: tuple
    bounds_max: tuple
    allow_cpu_access: bool
    halves: np.ndarray        # uint16, shape (SizeX*SizeY*SizeZ, 3), x fastest


@dataclass
class DIInstanceState:
    """Per system instance DI state (spec §11.4): modified arrays plus the bounds and audio logs."""
    arrays: dict = field(default_factory=dict)
    bounds_log: list = field(default_factory=list)
    audio_log: list = field(default_factory=list)
    camera: tuple = None            # scene input (x, y, z, pitch, yaw, roll, fov), UE world units; None = no player camera
    collision: object = None        # CollisionWorld (scene triangles) or None = empty world
    collision_batches: dict = field(default_factory=dict)
    collision_log: list = field(default_factory=list)
    particle_sources: dict = field(default_factory=dict)   # emitter name -> callable() -> (count, layout, buffer, id_table, is_gpu)

    def pre_tick(self):
        """DI PerInstanceTick: collision batches collect the results of the traces dispatched last tick."""
        for batch in self.collision_batches.values():
            batch.collect(self.collision)

    def post_tick(self):
        """DI PerInstanceTickPostSimulate: dispatch this tick's traces (flip buffers, clear the write buffer)."""
        for batch in self.collision_batches.values():
            batch.dispatch()


def rotation_axes(pitch, yaw, roll):
    """FRotationMatrix(FRotator) scaled axes X (forward), Y (right), Z (up) in UE world space (double maths, as in 5.7 LWC)."""
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    sr, cr = math.sin(math.radians(roll)), math.cos(math.radians(roll))
    x = (cp * cy, cp * sy, sp)
    y = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    z = (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp)
    return x, y, z


def camera_properties_words(camera):
    """UNiagaraDataInterfaceCamera::GetCameraProperties outputs: position, forward (X), up (Z), right (Y) as float words."""
    if camera is None:  # FCameraDataInterface_InstanceData defaults (no player controller, no editor viewport)
        camera = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    x, y, z = rotation_axes(*camera[3:6])
    return _f32_words(list(camera[0:3]) + list(x) + list(z) + list(y))


class CollisionWorld:
    """Static scene triangles (UE world cm) for line traces; first blocking hit = nearest intersection (two-sided)."""

    def __init__(self, triangles):
        t = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
        self.v0, self.e1, self.e2 = t[:, 0], t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
        n = np.cross(self.e1, self.e2)
        self.normal = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
        self.lo, self.hi = t.min(axis=1), t.max(axis=1)

    @classmethod
    def load(cls, path):
        return cls(np.load(path)['triangles'])

    def trace(self, start, end):
        """(hit, position, normal) for one segment (Moller-Trumbore on bbox-overlapping triangles)."""
        start, end = np.asarray(start, dtype=np.float64), np.asarray(end, dtype=np.float64)
        lo, hi = np.minimum(start, end), np.maximum(start, end)
        cand = np.nonzero(np.all(self.hi >= lo, axis=1) & np.all(self.lo <= hi, axis=1))[0]
        if not len(cand):
            return False, None, None
        d = end - start
        e1, e2, v0 = self.e1[cand], self.e2[cand], self.v0[cand]
        p = np.cross(d, e2)
        det = np.einsum('ij,ij->i', e1, p)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = start - v0
        u = np.einsum('ij,ij->i', s, p) * inv
        q = np.cross(s, e1)
        v = (q @ d) * inv
        t = np.einsum('ij,ij->i', e2, q) * inv
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t >= 0) & (t <= 1)
        if not hit.any():
            return False, None, None
        k = np.argmin(np.where(hit, t, np.inf))
        nrm = self.normal[cand[k]]
        if np.dot(nrm, d) > 0:
            nrm = -nrm
        return True, start + d * t[k], nrm


class CollisionBatch:
    """FNiagaraDICollisionQueryBatch (NiagaraCollision.cpp): double-buffered async traces, results available next tick."""

    def __init__(self):
        self.traces = [[], []]
        self.hit_index = [[], []]
        self.results = []
        self.cur = 0

    def submit(self, start, end):
        if float(np.sum((np.asarray(end, dtype=np.float64) - np.asarray(start, dtype=np.float64)) ** 2)) <= 1e-8:  # SMALL_NUMBER
            return -1
        self.traces[self.cur].append((tuple(start), tuple(end)))
        self.hit_index[self.cur].append(-1)
        return len(self.traces[self.cur]) - 1

    def dispatch(self):
        self.cur ^= 1
        self.traces[self.cur] = []
        self.hit_index[self.cur] = []

    def collect(self, world):
        read = self.cur ^ 1
        if world is None or not self.traces[read]:
            return
        self.results = []
        for i, (a, b) in enumerate(self.traces[read]):
            hit, pos, nrm = world.trace(a, b)
            if hit:
                self.hit_index[read][i] = len(self.results)
                self.results.append((False, pos, nrm, 0.0, 0.0, 0))

    def result(self, trace_id):
        read = self.cur ^ 1
        if 0 <= trace_id < len(self.traces[read]):
            k = self.hit_index[read][trace_id]
            if 0 <= k < len(self.results):
                return self.results[k]
        return None


def particle_read_impl(b, props, state):
    """UNiagaraDataInterfaceParticleRead CPU functions on the source emitter's current data (GPU sources read as invalid)."""
    source = ((props.get('EmitterBinding') or {}).get('EmitterName')) or ''
    attr = dict(b.specifiers).get('Attribute')

    def src():
        f = state.particle_sources.get(source)
        return f() if f is not None else None

    def column_words(layout, buffer, nf, ni, rows):
        v = layout.find(attr) if attr else None
        if v is None or len(v.type.float_offsets) != nf or len(v.type.int_offsets) != ni:
            return None
        cols = [buffer['float', v.float_start + j] for j in range(nf)] + [buffer['int32', v.int_start + j] for j in range(ni)]
        return [c[rows] for c in cols]

    def id_lookup(info, idx, tag):
        count, layout, buffer, table, gpu = info
        v = layout.find('ID')
        ok = np.zeros(len(idx), bool)
        rows = np.full(len(idx), -1, np.int64)
        if v is None or not table:
            return ok, rows
        table = np.asarray(table, np.int64)
        inr = (idx >= 0) & (idx < len(table))
        cand = np.where(inr, table[np.clip(idx, 0, len(table) - 1)], -1)
        live = (cand >= 0) & (cand < count)
        ids = buffer['int32', v.int_start][:count].view(np.int32)
        tags = buffer['int32', v.int_start + 1][:count].view(np.int32)
        c = np.clip(cand, 0, max(0, count - 1))
        ok = live & (ids[c] == idx) & (tags[c] == tag) if count else live & False
        return ok, np.where(ok, cand, -1)

    name = b.name
    if name == 'Get Num Particles':
        def fn(args, n, start):
            info = src()
            return [np.full(n, info[0] if info else 0, dtype=np.uint32)]
        return fn
    if name in ('Get Particle Index', 'Get Particle Index From ID Table'):
        def fn(args, n, start):
            info = src()
            idx = np.asarray(args[1], np.uint32).view(np.int32).astype(np.int64)
            if name == 'Get Particle Index':
                if info is None:
                    return [np.full(n, 0xffffffff, np.uint32)]
                tag = np.asarray(args[2], np.uint32).view(np.int32).astype(np.int64)
                ok, rows = id_lookup(info, idx, tag)
                return [rows.astype(np.int32).view(np.uint32)]
            table = np.asarray(info[3] if info else [], np.int64)
            inr = (idx >= 0) & (idx < len(table))
            rows = np.where(inr, table[np.clip(idx, 0, max(0, len(table) - 1))] if len(table) else -1, -1)
            return [np.where(inr & (rows != -1), TRUE_WORD, 0).astype(np.uint32), rows.astype(np.int32).view(np.uint32)]
        return fn
    t, key = PR_READS[name]
    nf, ni, default = PR_TYPES[t]

    def fn(args, n, start):
        info = src()
        out_valid = np.zeros(n, np.uint32)
        outs = [np.full(n, w, np.uint32) for w in default]
        if info is None or info[4]:
            return [out_valid] + outs
        count, layout, buffer, table, _ = info
        if key == 'Index':
            idx = np.asarray(args[1], np.uint32).view(np.int32).astype(np.int64)
            ok = (idx >= 0) & (idx < count) if count > 0 else np.zeros(n, bool)
            rows = np.where(ok, idx, 0)
        else:
            if not table:
                return [out_valid] + outs
            ok, rows = id_lookup(info, np.asarray(args[1], np.uint32).view(np.int32).astype(np.int64),
                                 np.asarray(args[2], np.uint32).view(np.int32).astype(np.int64))
            rows = np.where(ok, rows, 0)
        cols = column_words(layout, buffer, nf, ni, rows) if count else None
        if cols is None:
            return [out_valid] + outs
        return [np.where(ok, TRUE_WORD, 0).astype(np.uint32)] + [np.where(ok, c, d).astype(np.uint32) for c, d in zip(cols, outs)]
    return fn


def collision_query_async(batch, args, n):
    """UNiagaraDataInterfaceCollisionQuery::PerformQueryAsyncCPU (inputs: userptr, id, start, end, channel, skip, complex)."""
    w = [np.asarray(a, dtype=np.uint32) for a in args]
    f = [w[i].view(np.float32) for i in range(2, 8)]
    ids = w[1].view(np.int32)
    out = [np.zeros(n, dtype=np.uint32) for _ in range(12)]
    for i in range(n):
        skip = w[9][i] != 0
        qid = -1 if skip else batch.submit((float(f[0][i]), float(f[1][i]), float(f[2][i])), (float(f[3][i]), float(f[4][i]), float(f[5][i])))
        out[0][i] = np.uint32(np.int32(qid + 1).view(np.uint32))
        res = batch.result(int(ids[i]) - 1) if ids[i] > 0 else None
        if res is not None:
            inside, pos, nrm, friction, restitution, mat = res
            vals = _f32_words([float(pos[0]), float(pos[1]), float(pos[2]), float(nrm[0]), float(nrm[1]), float(nrm[2]), friction, restitution])
            out[1][i] = TRUE_WORD
            out[2][i] = TRUE_WORD if inside else 0
            for j in range(8):
                out[3 + j][i] = vals[j]
            out[11][i] = mat
    return out


def _class_short(typedef):
    name = ((typedef or {}).get('ClassStructOrEnum') or {}).get('ObjectName', '')
    return name.split("'")[1] if "'" in name else name


def di__export_index(ref):
    path = ref.get('ObjectPath') if isinstance(ref, dict) else None
    if not path or '.' not in path:
        return None
    try:
        return int(path.rsplit('.', 1)[1])
    except ValueError:
        return None


def _f32_words(values):
    return np.asarray(values, dtype=np.float32).view(np.uint32)


def _bits_f64(text):
    return float(np.array([int(text, 16)], dtype=np.uint64).view(np.float64)[0])


def bind_script(exports, script_export_index):
    """Spec §1: one Binding per CalledVMExternalFunctions entry (index = VM fn_index)."""
    props = exports[script_export_index].get('Properties') or {}
    vm = props.get('CachedScriptVM') or {}
    infos = vm.get('DataInterfaceInfo') or []
    store = (props.get('ScriptExecutionParamStore') or {}).get('DataInterfaces') or []
    bindings = []
    for k, call in enumerate(vm.get('CalledVMExternalFunctions') or []):
        specifiers = tuple((s.get('Key'), s.get('Value')) for s in call.get('FunctionSpecifiers') or [])
        common = dict(fn_index=k, owner_name=call.get('OwnerName'), name=call.get('Name'),
                      num_inputs=len(call.get('InputParamLocations') or []), num_outputs=int(call.get('NumOutputs') or 0),
                      specifiers=specifiers,
                      variadic=(len(call.get('VariadicInputs') or []), len(call.get('VariadicOutputs') or [])))
        if call.get('OwnerName') == FAST_PATH:
            bindings.append(Binding(**common, kind='fastpath', di_class=None, di_export=None, user_ptr=-1))
            continue
        slot = next((i for i, info in enumerate(infos) if info.get('Name') == call.get('OwnerName')), None)
        if slot is None:
            raise DIError('di_unresolved')
        di_class = _class_short(infos[slot].get('Type'))
        di_export = di__export_index(store[slot]) if slot < len(store) else None
        if di_export is None or not 0 <= di_export < len(exports) or exports[di_export].get('Type') != di_class:
            raise DIError('di_class_mismatch')
        bindings.append(Binding(**common, kind='di', di_class=di_class, di_export=di_export,
                                user_ptr=int(infos[slot].get('UserPtrIdx', -1))))
    return bindings


# ---------------------------------------------------------------- curves (spec §3)
def curve_from_properties(di_class, properties):
    """Spec §3.2: cooked properties with class-default values for absent fields."""
    e = CURVE_CLASSES.get(di_class)
    if e is None:
        raise DIError('di_not_curve')
    if properties.get('bUseLUT', True) is False:
        raise DIError('curve_without_lut')
    lut = properties.get('ShaderLUT')
    lut = np.array([0.0] * e if lut is None else lut, dtype=np.float32)
    n1 = np.float32(properties.get('LUTNumSamplesMinusOne', 0.0))
    if lut.size % e or float(n1) != lut.size // e - 1:
        raise DIError('curve_lut_size')
    return CurveLUT(di_class, e, lut, np.float32(properties.get('LUTMinTime', 0.0)), np.float32(properties.get('LUTMaxTime', 1.0)),
                    np.float32(properties.get('LUTInvTimeRange', 1.0)), n1)


def _word_f32(word):
    return np.array([int(word) & 0xffffffff], dtype=np.uint32).view(np.float32)[0]


def curve_from_words(di):
    """Gate D case form: {class, function, lut: [uint32 words], lut_min_time, lut_max_time, lut_inv_time_range,
    lut_num_samples_minus_one: uint32 words}."""
    e = CURVE_ELEMENTS.get((di.get('class'), di.get('function')))
    if e is None:
        raise DIError('di_not_curve')
    lut = np.array([int(w) & 0xffffffff for w in di['lut']], dtype=np.uint32).view(np.float32)
    n1 = _word_f32(di['lut_num_samples_minus_one'])
    if lut.size % e or float(n1) != lut.size // e - 1:
        raise DIError('curve_lut_size')
    return CurveLUT(di['class'], e, lut, _word_f32(di['lut_min_time']), _word_f32(di['lut_max_time']),
                    _word_f32(di['lut_inv_time_range']), n1)


def curve_words(curve, function):
    """Inverse of curve_from_words (exact float32 words)."""
    def word(value):
        return int(np.array([value], dtype=np.float32).view(np.uint32)[0])
    return {'class': curve.di_class, 'function': function, 'lut': [int(w) for w in curve.lut.view(np.uint32)],
            'lut_min_time': word(curve.min_time), 'lut_max_time': word(curve.max_time),
            'lut_inv_time_range': word(curve.inv_time_range), 'lut_num_samples_minus_one': word(curve.num_samples_minus_one)}


def sample_curve(curve, x_words, fma_mode='unfused'):
    """Spec §3.3: UE 5.7 SampleCurveInternal<true>; float32 throughout; one uint32 word array per output element."""
    if fma_mode not in ('unfused', 'fused'):
        raise DIError('fma_mode')
    x = np.asarray(x_words, dtype=np.uint32).view(np.float32)
    n1, e, lut = curve.num_samples_minus_one, curve.e, curve.lut
    zero, one = np.float32(0.0), np.float32(1.0)
    with np.errstate(all='ignore'):
        r0 = ((x - curve.min_time) * curve.inv_time_range) * n1
        m = np.where(r0 < n1, r0, n1).astype(np.float32)
        r = np.where(zero < m, m, zero).astype(np.float32)
        lo = np.trunc(r).astype(np.float32)
        hi = np.where(lo < n1, lo + one, lo).astype(np.float32)
        w = (r - lo).astype(np.float32)
        ia = (lo * np.float32(e)).astype(np.int64)
        ib = (hi * np.float32(e)).astype(np.int64)
        outputs = []
        for k in range(e):
            a = lut[ia + k]
            d = (lut[ib + k] - a).astype(np.float32)
            if fma_mode == 'unfused':
                value = (a + (w * d).astype(np.float32)).astype(np.float32)
            else:
                value = np.array([_fma57(float(wi), float(di), float(ai)) for wi, di, ai in zip(w, d, a)], dtype=np.float32)
            outputs.append(value.view(np.uint32).copy())
    return outputs


# ---------------------------------------------------------------- fast path (spec §11.3a)
def fast_matrix_to_quaternion(args):
    """FastPathLibrary.FastMatrixToQuaternion, UE 5.3/5.7 CPU kernel; float32; InvSqrt = 1/sqrt correctly rounded."""
    m = [np.asarray(a, dtype=np.uint32).view(np.float32) for a in args]
    n = m[0].size
    one, half = np.float32(1.0), np.float32(0.5)
    mat = np.stack([np.stack(m[4 * r:4 * r + 3]) for r in range(3)])        # (3, 3, n): M[r][c] for r, c < 3
    with np.errstate(all='ignore'):
        tr = (mat[0, 0] + mat[1, 1]) + mat[2, 2]
        s0 = one / np.sqrt(tr + one)
        s = half * s0
        pos = [((mat[1, 2] - mat[2, 1]) * s), ((mat[2, 0] - mat[0, 2]) * s), ((mat[0, 1] - mat[1, 0]) * s), half * (one / s0)]
        lanes = np.arange(n)
        i = np.where(mat[1, 1] > mat[0, 0], 1, 0)
        i = np.where(mat[2, 2] > mat[i, i, lanes], 2, i)
        j = (i + 1) % 3
        k = (j + 1) % 3
        sn = ((mat[i, i, lanes] - mat[j, j, lanes]) - mat[k, k, lanes]) + one
        sn0 = one / np.sqrt(sn)
        sh = half * sn0
        q = np.zeros((4, n), dtype=np.float32)
        q[i, lanes] = half * (one / sn0)
        q[3] = (mat[j, k, lanes] - mat[k, j, lanes]) * sh
        q[j, lanes] = (mat[i, j, lanes] + mat[j, i, lanes]) * sh
        q[k, lanes] = (mat[i, k, lanes] + mat[k, i, lanes]) * sh
        positive = tr > np.float32(0.0)
        return [np.where(positive, pos[c], q[c]).astype(np.float32).view(np.uint32).copy() for c in range(4)]


# ---------------------------------------------------------------- sprite renderer info (spec §11.3b)
def sprite_subuv_words(exports, properties):
    """GetSubUVDetails constants: [BlendEnabled, SubImageSize.X, SubImageSize.Y]; absent bSubImageBlend = true (game default)."""
    ref = properties.get('SpriteRenderer')
    renderer = {}
    if ref:
        idx = di__export_index(ref)
        if idx is None or not 0 <= idx < len(exports) or exports[idx].get('Type') != 'NiagaraSpriteRendererProperties':
            raise DIError('sprite_renderer_ref')
        renderer = exports[idx].get('Properties') or {}
    size = renderer.get('SubImageSize') or {}
    return [TRUE_WORD if renderer.get('bSubImageBlend', True) else 0,
            int(_f32_words([float(size.get('X', 1.0))])[0]), int(_f32_words([float(size.get('Y', 1.0))])[0])]


# ---------------------------------------------------------------- vector field (spec §11.3f)
_DECODED = {}


def _decoded(vf, mode):
    key = (id(vf.halves), mode)
    if key not in _DECODED:
        _DECODED[key] = (vf.halves, _decode_halves(vf.halves, mode))
    return _DECODED[key][1]


def _decode_halves(h, mode):
    h = np.asarray(h, dtype=np.uint16)
    if mode == 'ieee':
        return h.view(np.float16).astype(np.float32).astype(np.float64)
    if mode == 'fast':
        bits = ((h.astype(np.uint32) & np.uint32(0x8000)) << np.uint32(16)) | \
               (((h.astype(np.uint32) & np.uint32(0x7fff)) << np.uint32(13)) + np.uint32(0x38000000))
        return bits.astype(np.uint32).view(np.float32).astype(np.float64)
    raise DIError('vf_half_decode')


def _fma_vec(a, b, c):
    return np.array([math.fma(float(x), float(y), float(z)) for x, y, z in zip(a, b, c)], dtype=np.float64)


def sample_vector_field(vf, tiling, xyz_words, fma_mode='unfused', half_decode='fast'):
    """VectorField.SampleField, the Win64 ISPC kernel: double precision trilinear sampling of half3 voxels."""
    n = np.asarray(xyz_words[0]).size
    zero = [np.zeros(n, dtype=np.uint32) for _ in range(3)]
    if fma_mode not in ('unfused', 'fused'):
        raise DIError('fma_mode')
    if vf is None or not vf.allow_cpu_access or vf.halves.size == 0 or min(vf.size) <= 0:
        return zero
    size = np.array(vf.size, dtype=np.float64)
    bmin = np.array(vf.bounds_min, dtype=np.float64)
    bsize = np.array(vf.bounds_max, dtype=np.float64) - bmin
    if bsize.min() <= 1e-8:
        return zero
    inv_b = 1.0 / bsize
    inv_s = 1.0 / size
    tile = np.array([1.0 if t else 0.0 for t in tiling], dtype=np.float64)
    count = vf.halves.shape[0]
    with np.errstate(all='ignore'):
        i0, i1, frac = [], [], []
        for c in range(3):
            x = np.asarray(xyz_words[c], dtype=np.uint32).view(np.float32).astype(np.float64)
            scaled = ((x - bmin[c]) * inv_b[c])
            p = _fma_vec(scaled, np.full(n, size[c]), np.full(n, -0.5)) if fma_mode == 'fused' else scaled * size[c] - 0.5
            a = np.floor(p)
            b = a + 1.0
            frac.append(p - a)
            out = []
            for v in (a, b):
                wrapped = v - (tile[c] * np.floor(v * inv_s[c])) * size[c]
                out.append(np.minimum(np.maximum(wrapped, 0.0), size[c] - 1.0))
            i0.append(out[0])
            i1.append(out[1])
        decoded = _decoded(vf, half_decode)

        def corner(dx, dy, dz):
            ix, iy, iz = (i1[0] if dx else i0[0]), (i1[1] if dy else i0[1]), (i1[2] if dz else i0[2])
            lin = (ix + size[0] * iy) + (size[0] * size[1]) * iz
            ok = np.isfinite(lin) & (lin >= 0) & (lin <= count - 1)
            idx = np.where(ok, np.trunc(np.where(ok, lin, 0.0)), 0.0).astype(np.int64)
            return decoded[idx]                                               # (n, 3) doubles

        def lerp(a, b, t):
            t = t[:, None]
            if fma_mode == 'fused':
                d = b - a
                return np.stack([_fma_vec(d[:, c], t[:, 0], a[:, c]) for c in range(3)], axis=1)
            return a + (b - a) * t
        v00 = lerp(corner(0, 0, 0), corner(1, 0, 0), frac[0])
        v10 = lerp(corner(0, 1, 0), corner(1, 1, 0), frac[0])
        v0 = lerp(v00, v10, frac[1])
        v01 = lerp(corner(0, 0, 1), corner(1, 0, 1), frac[0])
        v11 = lerp(corner(0, 1, 1), corner(1, 1, 1), frac[0])
        v1 = lerp(v01, v11, frac[1])
        v = lerp(v0, v1, frac[2])
        return [v[:, c].astype(np.float32).view(np.uint32).copy() for c in range(3)]


def vector_field_from_dump(asset, row):
    """Field data from a plugin vfdump result (exact bit patterns; CPU data = half3 per voxel plus one padding half)."""
    size = tuple(int(row[k]) for k in ('size_x', 'size_y', 'size_z'))
    count = size[0] * size[1] * size[2]
    raw = base64.b64decode(row.get('cpu_data_b64') or '')
    halves = np.frombuffer(raw, dtype='<u2')
    if row.get('has_cpu_data') and halves.size < 3 * count:
        raise DIError('vector_field_cpu_data_size')
    halves = halves[:3 * count].reshape(-1, 3) if row.get('has_cpu_data') else np.zeros((0, 3), dtype=np.uint16)
    return VectorFieldData(asset, size, tuple(_bits_f64(b) for b in row['bounds_min_bits']), tuple(_bits_f64(b) for b in row['bounds_max_bits']),
                           bool(row['allow_cpu_access']), halves.astype(np.uint16))


def load_vector_fields(path):
    """{asset path -> VectorFieldData} from vector_fields.json (written by niagara_gate_d.py vfdump-collect)."""
    with open(path, encoding='utf-8-sig') as handle:
        doc = json.load(handle)
    return {asset: vector_field_from_dump(asset, row) for asset, row in doc['fields'].items()}


def field_asset(ref):
    """'/Niagara/VectorFields/Assets/TilingCurl32.0' + "VectorFieldStatic'TilingCurl32'" -> ('/Niagara/.../TilingCurl32.TilingCurl32', 'VectorFieldStatic')."""
    if not isinstance(ref, dict) or not ref.get('ObjectPath'):
        return None, None
    name = ref.get('ObjectName', '')
    cls, _, obj = name.partition("'")
    return ref['ObjectPath'].rsplit('.', 1)[0] + '.' + obj.rstrip("'"), cls


# ---------------------------------------------------------------- arrays (spec §11.3e)
def array_words(di_class, values):
    """Cooked elements -> uint32 words, shape (Num, E)."""
    _, e, keys, _ = ARRAY_TYPES[di_class]
    values = values or []
    if di_class == 'NiagaraDataInterfaceArrayBool':
        return np.array([[TRUE_WORD if v else 0] for v in values], dtype=np.uint32).reshape(-1, 1)
    rows = [[float(v)] if keys is None else [float(v.get(key, 0.0)) for key in keys] for v in values]
    return _f32_words(np.array(rows, dtype=np.float64).reshape(-1, e))


def array_default_words(di_class):
    _, e, _, default = ARRAY_TYPES[di_class]
    if di_class == 'NiagaraDataInterfaceArrayBool':
        return np.zeros(1, dtype=np.uint32)
    return _f32_words(np.array(default, dtype=np.float64))


class ArrayInstance:
    """Copy-on-write array state (5.7 FReadArrayRef / FWriteArrayRef), shared through DIInstanceState by DI identity."""

    def __init__(self, di_class, properties, identity, state):
        self.di_class, self.identity, self.state = di_class, identity, state
        self.e = ARRAY_TYPES[di_class][1]
        self.cooked = array_words(di_class, properties.get(ARRAY_TYPES[di_class][0]))
        self.default = array_default_words(di_class)

    def data(self):
        return self.state.arrays.get(self.identity, self.cooked)

    def call(self, name, args, n):
        data = self.data()
        num = data.shape[0]
        if name == 'Length':
            return [np.full(n, num, dtype=np.int64).astype(np.int32).view(np.uint32)]
        if name == 'LastIndex':
            return [np.full(n, num - 1, dtype=np.int64).astype(np.int32).view(np.uint32)]
        index = np.asarray(args[1], dtype=np.uint32).view(np.int32).astype(np.int64)
        if name == 'IsValidIndex':
            return [np.where((index >= 0) & (index < num), TRUE_WORD, 0).astype(np.uint32)]
        if name == 'Get':
            if num == 0:
                return [np.full(n, w, dtype=np.uint32) for w in self.default]
            rows = data[np.clip(index, 0, num - 1)]
            return [rows[:, c].copy() for c in range(self.e)]
        if name == 'SetArrayElem':
            skip = np.asarray(args[1], dtype=np.uint32)
            index = np.asarray(args[2], dtype=np.uint32).view(np.int32).astype(np.int64)
            values = np.stack([np.asarray(a, dtype=np.uint32) for a in args[3:3 + self.e]], axis=1)
            if self.di_class == 'NiagaraDataInterfaceArrayBool':
                values = np.where(values != 0, TRUE_WORD, 0).astype(np.uint32)
            target = self.state.arrays.setdefault(self.identity, self.cooked.copy())
            for lane in range(n):
                if skip[lane] == 0 and 0 <= index[lane] < target.shape[0]:
                    target[index[lane]] = values[lane]
            return []
        if name == 'Resize':
            new_num = min(int(np.asarray(args[1], dtype=np.uint32).view(np.int32)[0]), 2 ** 31 - 1)
            if new_num < 0:
                raise DIError('array_resize_negative')
            current = self.state.arrays.get(self.identity, self.cooked)
            if new_num <= current.shape[0]:
                resized = current[:new_num].copy()
            else:
                resized = np.concatenate([current, np.tile(self.default, (new_num - current.shape[0], 1))]).astype(np.uint32)
            self.state.arrays[self.identity] = resized
            return []
        raise DIError('di_unimplemented:%s.%s' % (self.di_class, name))


# ---------------------------------------------------------------- stub and handler (spec §4, §5, §11)
def stub_outputs(fn, args, num_outputs, num_instances):
    """Gate V deterministic stub, vectorized; bit-identical to niagara_runtime._execute57 external_handler='gate_v_stub'."""
    h = np.full(num_instances, (0x811c9dc5 ^ fn) & 0xffffffff, dtype=np.uint32)
    prime = np.uint32(0x01000193)
    with np.errstate(over='ignore'):
        for values in args:
            h = (h ^ np.asarray(values, dtype=np.uint32)) * prime
        outputs = []
        for k in range(num_outputs):
            hk = (h ^ np.uint32((0x9e3779b9 + k) & 0xffffffff)) * prime
            value = ((hk >> np.uint32(9)) | np.uint32(0x3f800000)).view(np.float32) - np.float32(1.0)
            outputs.append(value.astype(np.float32).view(np.uint32).copy())
    return outputs


class ScriptHandler:
    """VM external handler (spec §4). impls[fn] is a CurveLUT (sampled) or a callable(args, n, start) -> outputs; other fn -> stub."""

    def __init__(self, curves, num_outputs, *, fma_mode='unfused', log_stub_calls=False, unimplemented_functions=()):
        if fma_mode not in ('unfused', 'fused'):
            raise DIError('fma_mode')
        self.curves = dict(curves)
        self.num_outputs = dict(num_outputs)
        self.fma_mode = fma_mode
        self.log_stub_calls = log_stub_calls
        self.unimplemented_functions = sorted(unimplemented_functions)
        self.stub_calls = []

    def __call__(self, fn, args, num_instances, start_instance):
        impl = self.curves.get(fn)
        if isinstance(impl, CurveLUT):
            if len(args) != 1:
                raise DIError('di_signature')
            return sample_curve(impl, args[0], self.fma_mode)
        if impl is not None:
            return impl(args, num_instances, start_instance)
        outputs = stub_outputs(fn, args, self.num_outputs[fn], num_instances)
        if self.log_stub_calls:
            inputs = np.stack([np.asarray(a, dtype=np.uint32) for a in args], axis=1).reshape(-1) if args else np.zeros(0, dtype=np.uint32)
            self.stub_calls.append({'fn': fn, 'start': start_instance, 'count': num_instances, 'inputs': [int(v) for v in inputs]})
        return outputs


def _signature(di_class, name):
    """(inputs, outputs) per spec §11.2, user pointer included."""
    if di_class == di_PARTICLE_READ:
        if name in PR_READS:
            t, key = PR_READS[name]
            nf, ni, _ = PR_TYPES[t]
            return (2 if key == 'Index' else 3), 1 + nf + ni
        return {'Get Num Particles': (1, 1), 'Get Particle Index': (3, 1), 'Get Particle Index From ID Table': (2, 2)}[name]
    if (di_class, name) in CURVE_ELEMENTS:
        return 1, CURVE_ELEMENTS[di_class, name]
    if di_class in ARRAY_TYPES:
        e = ARRAY_TYPES[di_class][1]
        return {'Length': (1, 1), 'LastIndex': (1, 1), 'IsValidIndex': (2, 1), 'Get': (2, e), 'SetArrayElem': (3 + e, 0), 'Resize': (2, 0)}[name]
    return {(FAST_PATH, 'FastMatrixToQuaternion'): (16, 4), (di_SPRITE_INFO, 'GetSubUVDetails'): (0, 3), (EMITTER_PROPERTIES, 'SetFixedBounds'): (9, 0),
            (AUDIO_PLAYER, 'PlayAudioAtLocation'): (12, 1), (di_VECTOR_FIELD, 'SampleField'): (3, 3), (ARRAY_MESH, 'Length'): (1, 1),
            (di_CAMERA, 'GetCameraPropertiesCPU/GPU'): (1, 12), (di_COLLISION, 'PerformCollisionQueryAsyncCPU'): (11, 12)}[di_class, name]


def _constant(words):
    return lambda args, n, start: [np.full(n, w, dtype=np.uint32) for w in words]


def _impl(b, exports, state, vector_fields, vf_fma_mode, vf_half_decode):
    props = exports[b.di_export].get('Properties') or {} if b.kind == 'di' else {}
    cls = b.di_class if b.kind == 'di' else FAST_PATH
    if (cls, b.name) in CURVE_ELEMENTS:
        return curve_from_properties(cls, props)
    if cls == FAST_PATH:
        return lambda args, n, start: fast_matrix_to_quaternion(args)
    if cls == di_SPRITE_INFO:
        return _constant(sprite_subuv_words(exports, props))
    if cls == EMITTER_PROPERTIES:
        def bounds(args, n, start):
            execute = np.asarray(args[1], dtype=np.uint32) != 0
            state.bounds_log.append({'fn': b.fn_index, 'identity': list(b.identity), 'lanes': int(execute.sum())})
            return []
        return bounds
    if cls == AUDIO_PLAYER:
        valid = bool(props.get('SoundToPlay'))

        def play(args, n, start):
            success = (np.asarray(args[1], dtype=np.uint32) != 0) & valid
            state.audio_log.append({'fn': b.fn_index, 'identity': list(b.identity), 'plays': int(success.sum())})
            return [np.where(success, TRUE_WORD, 0).astype(np.uint32)]
        return play
    if cls == ARRAY_MESH:
        return _constant([len(props.get('MeshData') or [])])
    if cls == di_CAMERA:
        return lambda args, n, start: [np.full(n, w, dtype=np.uint32) for w in camera_properties_words(state.camera)]
    if cls == di_PARTICLE_READ:
        return particle_read_impl(b, props, state)
    if cls == di_COLLISION:
        batch = state.collision_batches.setdefault(tuple(b.identity), CollisionBatch())

        def query(args, n, start):
            out = collision_query_async(batch, args, n)
            state.collision_log.append({'fn': b.fn_index, 'lanes': n, 'valid': int((out[1] != 0).sum())})
            return out
        return query
    if cls in ARRAY_TYPES:
        array = ArrayInstance(cls, props, b.identity, state)
        return lambda args, n, start: array.call(b.name, args, n)
    if cls == di_VECTOR_FIELD:
        asset, field_class = field_asset(props.get('Field'))
        tiling = (bool(props.get('bTileX', False)), bool(props.get('bTileY', False)), bool(props.get('bTileZ', False)))
        vf = None
        if asset is not None and field_class == 'VectorFieldStatic':
            if asset not in vector_fields:
                raise DIError('vector_field_data_missing:' + asset)
            vf = vector_fields[asset]
        return lambda args, n, start: sample_vector_field(vf, tiling, args, vf_fma_mode, vf_half_decode)
    raise DIError('di_unimplemented:%s.%s' % (cls, b.name))


def script_handler(exports, bindings, *, fma_mode='unfused', unimplemented='error', log_stub_calls=False, state=None,
                   vector_fields=None, vf_fma_mode='unfused', vf_half_decode='fast'):
    """Spec §5/§11.5: implemented bindings get real DIs; others raise (unimplemented='error') or use the stub ('stub')."""
    if unimplemented not in ('error', 'stub'):
        raise DIError('unimplemented')
    state = DIInstanceState() if state is None else state
    vector_fields = {} if vector_fields is None else vector_fields
    missing = [b for b in bindings if (b.di_class if b.kind == 'di' else FAST_PATH, b.name) not in IMPLEMENTED]
    if missing and unimplemented == 'error':
        raise DIError('di_unimplemented:' + min(missing, key=lambda b: b.fn_index).label)
    impls = {}
    for b in bindings:
        key = (b.di_class if b.kind == 'di' else FAST_PATH, b.name)
        if key not in IMPLEMENTED:
            continue
        if (b.num_inputs, b.num_outputs) != _signature(*key) or (b.specifiers and key[0] != di_PARTICLE_READ) or b.variadic != (0, 0):
            raise DIError('di_signature')
        impls[b.fn_index] = _impl(b, exports, state, vector_fields, vf_fma_mode, vf_half_decode)
    handler = ScriptHandler(impls, {b.fn_index: b.num_outputs for b in bindings}, fma_mode=fma_mode, log_stub_calls=log_stub_calls,
                            unimplemented_functions={b.label for b in missing})
    handler.state = state
    return handler


# ---------------------------------------------------------------- Gate D / D2 case forms
def case_di(binding, exports):
    """The `di` object a Gate D/D2 case carries for an oracle-checkable binding, or None."""
    cls = binding.di_class if binding.kind == 'di' else FAST_PATH
    props = exports[binding.di_export].get('Properties') or {} if binding.kind == 'di' else {}
    if (cls, binding.name) in CURVE_ELEMENTS:
        return curve_words(curve_from_properties(cls, props), binding.name)
    if (cls, binding.name) == (FAST_PATH, 'FastMatrixToQuaternion'):
        return {'class': FAST_PATH, 'function': binding.name}
    if (cls, binding.name) == (di_SPRITE_INFO, 'GetSubUVDetails'):
        ref = props.get('SpriteRenderer')
        renderer = exports[di__export_index(ref)].get('Properties') or {} if ref else {}
        size = renderer.get('SubImageSize') or {}

        def bits(value):
            return format(int(np.array([float(value)], dtype=np.float64).view(np.uint64)[0]), '016X')
        return {'class': di_SPRITE_INFO, 'function': binding.name, 'has_renderer': bool(ref),
                'sub_image_blend': bool(renderer.get('bSubImageBlend', True)),
                'sub_image_size_x_bits': bits(size.get('X', 1.0)), 'sub_image_size_y_bits': bits(size.get('Y', 1.0))}
    if (cls, binding.name) == (di_VECTOR_FIELD, 'SampleField'):
        asset, field_class = field_asset(props.get('Field'))
        return {'class': di_VECTOR_FIELD, 'function': binding.name, 'field': asset if field_class == 'VectorFieldStatic' else '',
                'tile_x': bool(props.get('bTileX', False)), 'tile_y': bool(props.get('bTileY', False)), 'tile_z': bool(props.get('bTileZ', False))}
    return None


def case_handler(ext_functions, *, fma_mode='unfused', vector_fields=None, vf_fma_mode='unfused', vf_half_decode='fast', log_stub_calls=True):
    """Handler for a Gate D/D2 case: functions with `di` are real, every other function is the Gate V stub."""
    vector_fields = {} if vector_fields is None else vector_fields
    impls = {}
    for k, f in enumerate(ext_functions):
        di = f.get('di')
        if di is None:
            continue
        cls = di['class']
        if (cls, di['function']) in CURVE_ELEMENTS:
            impls[k] = curve_from_words(di)
        elif cls == FAST_PATH:
            impls[k] = lambda args, n, start: fast_matrix_to_quaternion(args)
        elif cls == di_SPRITE_INFO:
            if di['has_renderer']:
                words = [TRUE_WORD if di['sub_image_blend'] else 0,
                         int(_f32_words([_bits_f64(di['sub_image_size_x_bits'])])[0]), int(_f32_words([_bits_f64(di['sub_image_size_y_bits'])])[0])]
            else:
                words = [TRUE_WORD, int(_f32_words([1.0])[0]), int(_f32_words([1.0])[0])]
            impls[k] = _constant(words)
        elif cls == di_VECTOR_FIELD:
            vf = None
            if di['field']:
                if di['field'] not in vector_fields:
                    raise DIError('vector_field_data_missing:' + di['field'])
                vf = vector_fields[di['field']]
            tiling = (di['tile_x'], di['tile_y'], di['tile_z'])
            impls[k] = (lambda vf_, t_: (lambda args, n, start: sample_vector_field(vf_, t_, args, vf_fma_mode, vf_half_decode)))(vf, tiling)
        else:
            raise DIError('di_not_case_checkable:' + cls)
    return ScriptHandler(impls, {k: f['num_outputs'] for k, f in enumerate(ext_functions)}, fma_mode=fma_mode, log_stub_calls=log_stub_calls)

# ---- niagara_sched_model.py ----
# Static cooked Niagara scheduler model (Wave N3).

class ModelError(ValueError): pass
@dataclass(frozen=True)
class TypeInfo:
    key: str
    is_object: bool
    float_offsets: tuple
    int_offsets: tuple

def type_info(d):
    key=d.get('ClassStructOrEnum',{}).get('ObjectName','')
    if d.get('UnderlyingType')==1:return TypeInfo(key,True,(),())
    if d.get('UnderlyingType')==3:return TypeInfo(key,False,(),(0,))
    floats={'NiagaraFloat':1,'Vector2f':2,'Vector3f':3,'NiagaraPosition':3,'Vector4f':4,'LinearColor':4,'Quat4f':4,'NiagaraMatrix':16}
    for name,n in floats.items():
        if key=="Class'"+name+"'":return TypeInfo(key,False,tuple(range(0,n*4,4)),())
    if key in ["Class'NiagaraInt32'","Class'NiagaraBool'"]:return TypeInfo(key,False,(),(0,))
    if key=="Class'NiagaraID'":return TypeInfo(key,False,(),(0,4))
    if key=="Class'NiagaraSpawnInfo'":return TypeInfo(key,False,(4,8),(0,12))
    raise ModelError('unknown_type:'+key)
@dataclass(frozen=True)
class VarLayout:
    name: str
    type: TypeInfo
    float_start: int
    int_start: int
@dataclass(frozen=True)
class DataSetLayout:
    variables: tuple
    total_float: int
    total_int: int
    def find(self,name):return next((v for v in self.variables if v.name==name),None)
def build_layout(variables):
    f=i=0;out=[]
    for v in variables:
        t=type_info(v['TypeDef'])
        if t.is_object:raise ModelError('object_dataset:'+v['Name'])
        out.append(VarLayout(v['Name'],t,f,i));f+=len(t.float_offsets);i+=len(t.int_offsets)
    return DataSetLayout(tuple(out),f,i)
def compare_cooked_layout(layout,cooked_variable_layouts):
    errors=[]
    if len(layout.variables)!=len(cooked_variable_layouts):errors.append('variable_count')
    for v,c in zip(layout.variables,cooked_variable_layouts):
        info=c.get('LayoutInfo',{})
        for key,actual,expected in [('FloatComponentStart',c.get('FloatComponentStart',0),v.float_start),('Int32ComponentStart',c.get('Int32ComponentStart',0),v.int_start),('NumFloatComponents',info.get('NumFloatComponents',0),len(v.type.float_offsets)),('NumInt32Components',info.get('NumInt32Components',0),len(v.type.int_offsets))]:
            if actual!=expected:errors.append(v.name+':'+key)
        if info.get('NumHalfComponents',0):errors.append('half')
    return errors
@dataclass
class ParamStore:
    size:int
    interpolated:bool
    data:bytearray
    entries:dict
    object_entries:dict
def load_param_store(store_json,interpolated):
    data=bytearray(byte_payload(store_json.get('ParameterData')))
    if 'ParameterSize' in store_json:size=store_json['ParameterSize']
    elif not interpolated:size=len(data)
    else:raise ModelError('param_store_size')
    if len(data)!=size*(2 if interpolated else 1):raise ModelError('param_store_size')
    entries={};objects={}
    for p in store_json.get('SortedParameterOffsets',[]):
        t=type_info(p['TypeDef']);name=p['Name'];offset=p['Offset']
        if t.is_object:objects[name]=(offset,t.key)
        else:entries[name]=(offset,t)
    return ParamStore(size,interpolated,data,entries,objects)
@dataclass
class ScriptModel:
    export_index:int
    name:str
    usage:str
    bytecode:bytes
    literals:bytes
    attributes:tuple
    ext_functions:list
    param_store:ParamStore
    num_user_ptrs:int
    program:object
    has_ext:bool
    reads:dict
    writes:dict
@dataclass
class EventGeneratorModel:
    event_id:str
    layout:DataSetLayout
    shared:bool
@dataclass
class EventHandlerModel:
    script:ScriptModel
    execution_mode:str
    spawn_number:int
    min_spawn_number:int
    random_spawn_number:bool
    max_events_per_frame:int
    source_emitter_id:str
    source_event_name:str
@dataclass
class EmitterModel:
    index:int
    handle_name:str
    handle_id:str
    id_name:str
    unique_name:str
    enabled:bool
    disabled_reason:object
    sim_target:str
    local_space:bool
    determinism:bool
    random_seed:int
    interpolated:bool
    requires_persistent_ids:bool
    allocation_mode:str
    pre_allocation_count:int
    spawn:object
    update:object
    event_handlers:list
    spawn_generators:list
    update_generators:list
    particle_layout:object
    spawn_attributes:tuple
    names:dict
@dataclass
class SystemModel:
    package:str
    path:str
    emitters:list
    system_spawn:object
    system_update:object
    main_layout:object
    spawn_params_layout:object
    update_params_layout:object
    bindings:dict
    instance_params:ParamStore
    determinism:bool
    random_seed:int
    warmup_tick_count:int
    warmup_tick_delta:float
    fixed_tick_delta:object
    initial_owner_velocity_from_actor:bool
    execution_order:list
    quality_level:int
    unsupported:tuple
    notes:tuple

def short(v):return str(v).rsplit('::',1)[-1]
def load_system(path,*,quality_level=3):
    path=Path(path);doc=json.loads(path.read_text(encoding='utf-8-sig'));reasons=[];notes=[];emitters=[];bindings={};empty=build_layout([])
    model=SystemModel(path.stem,str(path),emitters,None,None,empty,empty,empty,bindings,load_param_store({},False),False,0,0,1/15,None,True,[],quality_level,(),())
    def unsupported(reason):
        if reason not in reasons:reasons.append(reason)
    try:
        evidence=extract_document(doc,source_path=str(path));model.package=evidence['source']['packagePath'];systems=evidence['systems']
        if len(systems)!=1:raise ModelError('system_count')
        props=systems[0]['rawExport'].get('Properties',{});records={r['exportIndex']:r for key in ['systems','emitters','scripts','renderers','dataInterfaces','otherExports'] for r in evidence[key]}
        def resolve(ref):
            try:
                r=records[int(ref['ObjectPath'].rsplit('.',1)[1])];name=ref['ObjectName'].split("'",1)[1].rstrip("'");name=re.split('[:.]',name)[-1]
                if r['name']!=name:raise ValueError()
                return r
            except (KeyError,ValueError,TypeError,IndexError):raise ModelError('reference_mismatch')
        def script(ref):
            r=resolve(ref);p=r['rawExport'].get('Properties',{});v=p.get('CachedScriptVM',{});code=bytecode_from_vm(v);ext=v.get('CalledVMExternalFunctions') or [];usage=short(p.get('Usage',''));prog=vm_disassemble(code,external_functions=ext,profile='pioneer-2.1.0') if code else None;reads={};writes={}
            for ins in prog.instructions if prog else []:
                if ins.name.startswith(('inputdata','outputdata')):
                    o=ins.operands;dest=reads if ins.name.startswith('inputdata') else writes;key=(o[0].value,'int32' if ins.name.endswith('int32') else 'float');dest[key]=max(dest.get(key,0),o[1 if dest is reads else 3].value+1)
            return ScriptModel(r['exportIndex'],r['name'],usage,code,byte_payload(v.get('ScriptLiterals')),tuple(v.get('Attributes',[])),ext,load_param_store(p.get('ScriptExecutionParamStore',{}),usage=='ParticleSpawnScriptInterpolated'),v.get('NumUserPtrs',0),prog,any(i.name=='external_func_call' for i in prog.instructions) if prog else False,reads,writes)
        def compiled(data):
            layout=build_layout(data.get('Variables',[]))
            if compare_cooked_layout(layout,data.get('VariableLayouts',[])):unsupported('layout_mismatch')
            return layout
        c=props.get('SystemCompiledData',{});model.main_layout=compiled(c.get('DataSetCompiledData',{}));model.spawn_params_layout=compiled(c.get('SpawnInstanceParamsDataSetCompiledData',{}));model.update_params_layout=compiled(c.get('UpdateInstanceParamsDataSetCompiledData',{}));model.instance_params=load_param_store(c.get('InstanceParamStore',{}),False)
        model.system_spawn=script(props.get('SystemSpawnScript'));model.system_update=script(props.get('SystemUpdateScript'))
        if not model.system_spawn.bytecode or not model.system_update.bytecode:unsupported('system_script_missing_bytecode')
        for i,h in enumerate(props.get('EmitterHandles',[])):
            emitter_ref=(h.get('VersionedInstance') or {}).get('Emitter')
            if not isinstance(emitter_ref,dict):
                reason='handle_disabled' if not h.get('bIsEnabled',True) else 'no_emitter'
                if reason=='no_emitter':notes.append('no_emitter:%d'%i)
                emitters.append(EmitterModel(i,h.get('Name'),h.get('ID'),h.get('IdName'),'',False,reason,'Unknown',False,False,0,False,False,'AutomaticEstimate',0,None,None,[],[],[],None,(),{k:'' for k in ('spawn_interval','interp_spawn_start_dt','spawn_group','execution_state')}));continue
            r=resolve(emitter_ref);p=r['rawExport'].get('Properties',{});selected,_=select_emitter_version(p)
            if selected is None:raise ModelError('emitter_version')
            v=p['VersionData'][selected];platform=v.get('Platforms',{});reason=None
            if not h.get('bIsEnabled',True):reason='handle_disabled'
            elif short(h.get('EmitterMode','Standard'))!='Standard':reason='stateless'
            elif not (platform.get('QualityLevelMask',-1)&(1<<quality_level)):reason='quality'
            if platform.get('CVarConditions') or platform.get('DeviceProfileStates'):unsupported('platform_conditions')
            u=p.get('UniqueEmitterName','');target=short(v.get('SimTarget','CPUSim'));interp=short(v.get('InterpolatedSpawnMode','None'))=='Interpolation'
            e=EmitterModel(i,h.get('Name'),h.get('ID'),h.get('IdName'),u,reason is None,reason,target,v.get('bLocalSpace',False),v.get('bDeterminism',False),v.get('RandomSeed',0),interp,v.get('bRequiresPersistentIDs',False),short(v.get('AllocationMode','AutomaticEstimate')),v.get('PreAllocationCount',0),None,None,[],[],[],None,(),{k:u+'.'+suffix for k,suffix in [('spawn_interval','SpawnInterval'),('interp_spawn_start_dt','InterpSpawnStartDt'),('spawn_group','SpawnGroup'),('execution_state','ExecutionState')]});emitters.append(e)
            def spawn_attributes():
                spawnattrs=[]
                for a in model.system_spawn.attributes+model.system_update.attributes:
                    if type_info(a['TypeDef']).key=="Class'NiagaraSpawnInfo'" and a['Name'].startswith(u+'.') and a['Name'] not in spawnattrs:
                        spawnattrs.append(a['Name']);found=model.main_layout.find(a['Name'])
                        if found is None or found.type.key!="Class'NiagaraSpawnInfo'":unsupported('spawn_attribute_missing')
                return tuple(spawnattrs)
            if not e.enabled:continue
            if target=='GPUComputeSim':
                # spec N-O4: the particle logic is the GPU compute script's shader; CPU system scripts still produce spawn infos
                gr=resolve(v.get('GPUComputeScript'));gvm=gr['rawExport'].get('Properties',{}).get('CachedScriptVM',{})
                gps=gr['rawExport'].get('Properties',{}).get('ScriptExecutionParamStore',{});gdata=byte_payload(gps.get('ParameterData'))
                e.gpu=SimpleNamespace(script_index=gr['exportIndex'],emitter_index=r['exportIndex'],stages=len(gvm.get('SimulationStageMetaData',[])),
                                      param_store=load_param_store(gps,len(gdata)==2*gps.get('ParameterSize',len(gdata)) and len(gdata)>0))
                e.particle_layout=build_layout(gvm.get('Attributes',[]));e.requires_persistent_ids|=e.particle_layout.find('ID') is not None
                for rd in gr['rawExport'].get('Properties',{}).get('ResolvedDataInterfaces') or []:
                    ref=rd.get('ResolvedDataInterface')
                    dx=(records.get(int(ref['ObjectPath'].rsplit('.',1)[1]),{}).get('rawExport') or {}) if isinstance(ref,dict) else {}
                    if dx.get('Type')=='NiagaraDataInterfaceParticleRead':   # GPU ParticleRead: the source dispatches first
                        e.particle_read_sources=getattr(e,'particle_read_sources',set())|{((dx.get('Properties') or {}).get('EmitterBinding') or {}).get('EmitterName','')}
                if v.get('EventHandlerScriptProps'):unsupported('gpu_event_handlers')
                e.spawn_attributes=spawn_attributes()
                if model.main_layout.find(e.names['execution_state']) is None:notes.append('missing_emitter_state:'+str(i))
                continue
            if target!='CPUSim':unsupported('sim_target:'+target);continue
            e.spawn=script(v.get('SpawnScriptProps',{}).get('Script'));e.update=script(v.get('UpdateScriptProps',{}).get('Script'))
            if (e.spawn.usage=='ParticleSpawnScriptInterpolated')!=interp:unsupported('interp_mismatch')
            for hp in v.get('EventHandlerScriptProps',[]):e.event_handlers.append(EventHandlerModel(script(hp.get('Script')),short(hp.get('ExecutionMode','SpawnedParticles')),hp.get('SpawnNumber',0),hp.get('MinSpawnNumber',0),hp.get('bRandomSpawnNumber',False),hp.get('MaxEventsPerFrame',0),hp.get('SourceEmitterID','00000000-00000000-00000000-00000000'),hp.get('SourceEventName','None')))
            attrs=list(e.update.attributes)
            for a in e.spawn.attributes:
                if not any(x['Name']==a['Name'] and type_info(x['TypeDef']).key==type_info(a['TypeDef']).key for x in attrs):attrs.append(a)
            e.particle_layout=build_layout(attrs);e.requires_persistent_ids|=e.particle_layout.find('ID') is not None
            for s in [e.spawn,e.update]+[h.script for h in e.event_handlers]:
                if not s.bytecode:unsupported('cpu_script_missing_bytecode')
                for counts in [s.reads,s.writes]:
                    if counts.get((0,'float'),0)>e.particle_layout.total_float or counts.get((0,'int32'),0)>e.particle_layout.total_int:unsupported('component_out_of_layout')
                vp=records[s.export_index]['rawExport'].get('Properties',{}).get('CachedScriptVM',{})
                for k,info in enumerate(vp.get('DataInterfaceInfo',[])):
                    if 'NiagaraDataInterfaceParticleRead' not in json.dumps(info.get('Type',{})):continue
                    dis=(records[s.export_index]['rawExport'].get('Properties',{}).get('ScriptExecutionParamStore',{}) or {}).get('DataInterfaces') or []
                    if k<len(dis) and isinstance(dis[k],dict):
                        dp=(records.get(int(dis[k]['ObjectPath'].rsplit('.',1)[1]),{}).get('rawExport') or {}).get('Properties',{})
                        e.particle_read_sources=getattr(e,'particle_read_sources',set())|{(dp.get('EmitterBinding') or {}).get('EmitterName','')}
            sg=v.get('SpawnScriptProps',{}).get('EventGenerators',[]);ug=v.get('UpdateScriptProps',{}).get('EventGenerators',[]);shared={g['ID'] for g in sg}&{g['ID'] for g in ug}
            for gs,out in [(sg,e.spawn_generators),(ug,e.update_generators)]:
                for g in gs:out.append(EventGeneratorModel(g['ID'],compiled(g.get('DataSetCompiledData',{})),g['ID'] in shared))
            e.spawn_attributes=spawn_attributes()
            if model.main_layout.find(e.names['execution_state']) is None:notes.append('missing_emitter_state:'+str(i))
        for phase in ['spawn','update']:
            for group in ['global','system','owner','emitter']:
                sources=c.get(phase.title()+'Instance'+group.title()+('Bindings' if group=='emitter' else 'Binding'),[] if group=='emitter' else {})
                for j,b in enumerate(sources if group=='emitter' else [sources]):
                    if b.get('HalfOffsets'):unsupported('half_binding')
                    bindings[(phase,group,j) if group=='emitter' else (phase,group)]=tuple((kind,x['ParameterOffset'],x['DataSetComponentOffset']) for field,kind in [('FloatOffsets','float'),('Int32Offsets','int32')] for x in b.get(field,[]))
        if model.main_layout.find('System.ExecutionState') is None:unsupported('missing_system_state')
        dependencies={e.index:[] for e in emitters}
        for e in emitters:
            for name in getattr(e,'particle_read_sources',()):
                src=next((x for x in emitters if name and (x.unique_name==name or x.handle_name==name)),None)
                if src is not None and src.index!=e.index:dependencies[e.index].append(src.index)   # ParticleRead: reader ticks after its source
            for h in e.event_handlers:
                source=next((x for x in emitters if x.handle_id==h.source_emitter_id),None)
                h.dead=source is None or not any(g.event_id==h.source_event_name for g in source.spawn_generators+source.update_generators)
                if h.dead:notes.append('dead_event_handler');continue
                if e.requires_persistent_ids:unsupported('event_receiver_with_ids')
                if h.execution_mode!='SpawnedParticles':unsupported('event_mode:'+h.execution_mode)
                dependencies[e.index].append(source.index)
        priorities={};visiting=set()
        def priority(i):
            if i in visiting:raise ModelError('execution_order_cycle')
            if i not in priorities:
                visiting.add(i);priorities[i]=max((priority(d)+1 for d in dependencies[i]),default=0);visiting.remove(i)
            return priorities[i]
        model.execution_order=sorted(range(len(emitters)),key=priority)
        model.determinism=props.get('bDeterminism',False);model.random_seed=props.get('RandomSeed',0);model.warmup_tick_count=props.get('WarmupTickCount',0);model.warmup_tick_delta=props.get('WarmupTickDelta',1/15);model.initial_owner_velocity_from_actor=props.get('bInitialOwnerVelocityFromActor',True)
        if props.get('bFixedTickDelta',False):model.fixed_tick_delta=props.get('FixedTickDeltaTime',.01667);unsupported('fixed_tick')
    except (ModelError,DecodeError,KeyError,TypeError,ValueError,IndexError) as exc:unsupported(str(exc))
    model.unsupported=tuple(reasons);model.notes=tuple(notes);return model

def census(corpus,out,quality):
    rows=[]
    for path in sorted(Path(corpus).rglob('NS_*.json')):
        m=load_system(path,quality_level=quality);enabled=[e for e in m.emitters if e.enabled];scripts=[s for s in [m.system_spawn,m.system_update] if s]+[s for e in enabled for s in [e.spawn,e.update]+[h.script for h in e.event_handlers if not getattr(h,'dead',False)] if s]
        allcpu=all(e.sim_target=='CPUSim' for e in enabled);eligible=allcpu and not m.unsupported;noext=eligible and not any(s.has_ext for s in scripts)
        rows.append(dict(package=m.package,path=m.path,unsupported=m.unsupported,notes=m.notes,emitter_count=len(m.emitters),enabled_cpu=sum(e.sim_target=='CPUSim' for e in enabled),enabled_gpu=sum(e.sim_target!='CPUSim' for e in enabled),has_ext=[{'export_index':s.export_index,'has_ext':s.has_ext} for s in scripts],all_enabled_cpu=allcpu,noext=noext,live_event_pairs=[(h.source_emitter_id,e.handle_id) for e in enabled for h in e.event_handlers if not getattr(h,'dead',False)],warmup_ticks=m.warmup_tick_count,requires_persistent_ids=sum(e.requires_persistent_ids for e in enabled)))
    data=dict(systems=rows,totals=dict(systems=len(rows),reasons=dict(collections.Counter(r for row in rows for r in row['unsupported'])),s0_universe=sum(r['all_enabled_cpu'] and not r['unsupported'] for r in rows),noext=sum(r['noext'] for r in rows)))
    Path(out).parent.mkdir(parents=True,exist_ok=True);Path(out).write_text(json.dumps(data,sort_keys=True,indent=2)+'\n',encoding='utf-8');print(json.dumps(data['totals'],sort_keys=True))
def _main_sched_model():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True);c=s.add_parser('census');c.add_argument('--corpus',required=True);c.add_argument('--out',required=True);c.add_argument('--quality',type=int,required=True);a=p.parse_args();census(a.corpus,a.out,a.quality)

# ---- niagara_view_ub.py ----
# View uniform buffer layout and contents (spec N-O4 §4): FViewUniformShaderParameters constants from VIEW_UNIFORM_BUFFER_MEMBER_TABLE.
#
# The constant part of a global uniform buffer is its non-resource members in declaration order under UE shader-parameter
# alignment (TShaderParameterTypeInfo): scalars 4, 2-vectors 8, 3-/4-vectors and matrices 16, array elements 16-byte strided.
# The table is parsed from the engine header (the game renderer is the 5.3 fork).


DEFAULT_HEADER = os.environ.get('ARC_UE_SCENE_VIEW_HEADER', '')   # UE 5.7 Engine/Source/Runtime/Engine/Public/SceneView.h; game View UB = 5.7 layout (spec N-O4 section 4)
TYPES = {  # name -> (size, alignment, component kind)
    'float': (4, 4, 'f'), 'int32': (4, 4, 'i'), 'uint32': (4, 4, 'u'), 'int': (4, 4, 'i'), 'uint': (4, 4, 'u'),
    'FVector2f': (8, 8, 'f'), 'FIntPoint': (8, 8, 'i'), 'FUintVector2': (8, 8, 'u'), 'FVector2D': (8, 8, 'f'),
    'FVector3f': (12, 16, 'f'), 'FIntVector': (12, 16, 'i'), 'FUintVector3': (12, 16, 'u'),
    'FVector4f': (16, 16, 'f'), 'FLinearColor': (16, 16, 'f'), 'FIntVector4': (16, 16, 'i'), 'FUintVector4': (16, 16, 'u'),
    'FIntRect': (16, 16, 'i'), 'FMatrix44f': (64, 16, 'f'),
}
DIMENSIONS = {'TVC_MAX': 2, 'NUM_ATMOSPHERE_LIGHTS': 2, 'SKY_IRRADIANCE_ENVIRONMENT_MAP_VEC4_COUNT': 8, 'GlobalDistanceField::MaxClipmaps': 6,
              'MAX_PHYSICS_FIELD_TARGETS': 32}   # 5.3 header constants
MEMBER = re.compile(r'VIEW_UNIFORM_BUFFER_MEMBER(_PER_VIEW)?(_EX|_ARRAY)?\(\s*([A-Za-z0-9_]+)\s*,\s*([A-Za-z0-9_]+)\s*(?:,\s*([^)]+))?\)')
VIEW_SIZE = (1920, 1080)   # bake view (and scene texture) resolution
NEAR = 10.0                # near plane, cm
DEFAULT_CAMERA = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 90.0)   # no player camera: origin, facing +X


def parse_table(scene_view_h):
    text = Path(scene_view_h).read_text(encoding='utf-8', errors='replace')
    start = text.index('#define VIEW_UNIFORM_BUFFER_MEMBER_TABLE')
    end = text.index('\n\n', start)
    body = text[start:end]
    members = []
    for m in MEMBER.finditer(body):
        _, kind, typ, name, extra = m.groups()
        count = 1
        if kind == '_ARRAY':
            dim = extra.strip()
            count = DIMENSIONS[dim] if dim in DIMENSIONS else int(dim)
        members.append((typ, name, count, kind == '_ARRAY'))
    return members


def layout(members):
    """name -> (byte offset, size, type, count); total constant size."""
    out, off = {}, 0
    for typ, name, count, is_array in members:
        if typ not in TYPES:
            raise ValueError('view_ub_type:' + typ)
        size, align, _ = TYPES[typ]
        if is_array:
            stride = max(16, -(-size // 16) * 16)
            off = -(-off // 16) * 16
            out[name] = (off, stride * count, typ, count)
            off += stride * count
        else:
            off = -(-off // align) * align
            out[name] = (off, size, typ, 1)
            off += size
    return out, -(-off // 16) * 16


SHIPPED_TABLE = (Path(__file__).resolve().parent.parent / 'Data' / 'niagara_view_ub_57.json')


def load_members(scene_view_h=None):
    """Member table from the engine header when available, else the shipped 5.7 table (addon installs have no engine source)."""
    if scene_view_h and Path(scene_view_h).is_file():
        return parse_table(scene_view_h)
    import json
    return [tuple(m) for m in json.loads(SHIPPED_TABLE.read_text(encoding='utf-8'))['members']]


class ViewBuffer:
    """Byte image of the View UB constants with named setters (float32 / int32 words)."""

    def __init__(self, scene_view_h=None):
        self.members = load_members(scene_view_h)
        self.layout, self.size = layout(self.members)
        self.words = np.zeros(self.size // 4, np.uint32)

    def set(self, name, values):
        off, size, typ, _ = self.layout[name]
        kind = TYPES[typ][2]
        arr = np.asarray(values, dtype=np.float32 if kind == 'f' else (np.int32 if kind == 'i' else np.uint32)).ravel()
        self.words[off // 4:off // 4 + arr.size] = arr.view(np.uint32)

    def matrix(self, name, m):
        """UE row-vector matrix: HLSL mul(v, M); store row-major rows (UE FMatrix44f memory order)."""
        self.set(name, np.asarray(m, dtype=np.float64).reshape(4, 4))


def _df(v):
    """FDFVector3 high/low split (double -> float + float remainder)."""
    v = np.asarray(v, dtype=np.float64)
    hi = v.astype(np.float32).astype(np.float64)
    return hi, (v - hi)


def camera_view(vb, cam, width=VIEW_SIZE[0], height=VIEW_SIZE[1], near=NEAR):
    """Fill the camera-related View members (UE world units, LWC tile 0) for a perspective camera.

    cam: (x, y, z, pitch, yaw, roll, fov_deg); UE left-handed, reversed-Z infinite far projection (FReversedZPerspectiveMatrix).
    """
    pass
    if cam is None:
        cam = DEFAULT_CAMERA
    x, y, z = cam[0:3]
    fwd, right, up = (np.array(v, dtype=np.float64) for v in rotation_axes(*cam[3:6]))
    fov = np.radians(cam[6] if cam[6] > 0 else 90.0)
    # ViewMatrix (UE): world -> view with view axes (right, up, forward) -> columns X=right? UE view space: X right, Y up, Z forward.
    rot = np.stack([right, up, fwd], axis=1)                 # world-to-view rotation (row-vector convention: v_view = v_world @ rot)
    t2v = np.eye(4)
    t2v[:3, :3] = rot
    half = np.tan(fov / 2.0)
    aspect = width / height
    proj = np.zeros((4, 4))
    proj[0, 0] = 1.0 / half
    proj[1, 1] = aspect / half
    proj[2, 3] = 1.0
    proj[3, 2] = near                                         # reversed-Z infinite: z_clip = near, w = view z
    pre = -np.array([x, y, z])                                # PreViewTranslation: translated world = world + pre (camera at origin)
    t2c = t2v @ proj
    vb.matrix('TranslatedWorldToClip', t2c)
    vb.matrix('TranslatedWorldToView', t2v)
    vb.matrix('ViewToTranslatedWorld', np.linalg.inv(t2v))
    vb.matrix('TranslatedWorldToCameraView', t2v)
    vb.matrix('CameraViewToTranslatedWorld', np.linalg.inv(t2v))
    vb.matrix('ViewToClip', proj)
    vb.matrix('ViewToClipNoAA', proj)
    vb.matrix('ClipToView', np.linalg.inv(proj))
    vb.matrix('ClipToTranslatedWorld', np.linalg.inv(t2c))
    for prev, cur in (('PrevViewToClip', proj), ('PrevClipToView', np.linalg.inv(proj)), ('PrevTranslatedWorldToClip', t2c),
                      ('PrevTranslatedWorldToView', t2v), ('PrevViewToTranslatedWorld', np.linalg.inv(t2v))):
        if prev in vb.layout:
            vb.matrix(prev, cur)
    screen = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, proj[2, 2], 1], [0, 0, proj[3, 2], 0]], dtype=np.float64)
    s2t = screen @ np.linalg.inv(t2c)
    for name, m in (('ScreenToTranslatedWorld', s2t), ('PrevScreenToTranslatedWorld', s2t), ('SVPositionToTranslatedWorld', s2t),
                    ('ScreenToRelativeWorld', s2t), ('RelativeWorldToClip', t2c), ('ClipToRelativeWorld', np.linalg.inv(t2c)),
                    ('PrevClipToRelativeWorld', np.linalg.inv(t2c)), ('ClipToPrevClip', np.eye(4)), ('ClipToPrevClipWithAA', np.eye(4)),
                    ('PrevTranslatedWorldToCameraView', t2v), ('PrevCameraViewToTranslatedWorld', np.linalg.inv(t2v))):
        if name in vb.layout:
            vb.matrix(name, m)
    origin = np.array([x, y, z], dtype=np.float64)
    for prefix in ('ViewOrigin', 'WorldViewOrigin', 'PrevWorldCameraOrigin', 'PrevWorldViewOrigin'):
        hi, lo = _df(origin)
        if prefix + 'High' in vb.layout:
            vb.set(prefix + 'High', hi)
            vb.set(prefix + 'Low', lo)
    for prefix in ('PreViewTranslation', 'PrevPreViewTranslation'):
        hi, lo = _df(-origin)
        if prefix + 'High' in vb.layout:
            vb.set(prefix + 'High', hi)
            vb.set(prefix + 'Low', lo)
    for name, val in (('ViewTilePosition', (0.0, 0.0, 0.0)), ('RelativeWorldCameraOriginTO', origin), ('RelativeWorldViewOriginTO', origin),
                      ('RelativePreViewTranslationTO', -origin), ('PrevRelativeWorldCameraOriginTO', origin), ('PrevRelativeWorldViewOriginTO', origin),
                      ('RelativePrevPreViewTranslationTO', -origin), ('ViewRectMinAndSize', (0, 0, width, height))):
        if name in vb.layout:
            vb.set(name, val)
    vb.set('ViewForward', fwd)
    vb.set('ViewUp', up)
    vb.set('ViewRight', right)
    for name in ('HMDViewNoRollUp',):
        vb.set(name, up)
    vb.set('HMDViewNoRollRight', right)
    for name in ('RelativeWorldCameraOrigin', 'RelativeWorldViewOrigin', 'PrevRelativeWorldCameraOrigin', 'PrevRelativeWorldViewOrigin'):
        if name in vb.layout:
            vb.set(name, (x, y, z))
    for name in ('TranslatedWorldCameraOrigin', 'PrevTranslatedWorldCameraOrigin'):
        if name in vb.layout:
            vb.set(name, (0.0, 0.0, 0.0))
    for name in ('RelativePreViewTranslation', 'RelativePrevPreViewTranslation'):
        if name in vb.layout:
            vb.set(name, pre)
    for name, v in (('ViewSizeAndInvSize', (width, height, 1.0 / width, 1.0 / height)), ('BufferSizeAndInvSize', (width, height, 1.0 / width, 1.0 / height)),
                    ('ViewRectMin', (0.0, 0.0, 0.0, 0.0)), ('FieldOfViewWideAngles', (fov, 2 * np.arctan(half / aspect))),
                    ('PrevFieldOfViewWideAngles', (fov, 2 * np.arctan(half / aspect))), ('NearPlane', near), ('TemporalAAJitter', (0, 0, 0, 0)),
                    ('ScreenPositionScaleBias', (0.5, -0.5, 0.5, 0.5)), ('ScreenToViewSpace', (half, half / aspect, 0.0, 0.0)),
                    # CreateInvDeviceZToWorldZTransform: perspective (0, 0, 1 / M[3][2], M[2][2] / M[3][2]), so ConvertFromDeviceZ = near / DeviceZ
                    ('InvDeviceZToWorldZTransform', (0.0, 0.0, 1.0 / near, proj[2, 2] / near)), ('BufferToSceneTextureScale', (1.0, 1.0)),
                    ('PreExposure', 1.0), ('OneOverPreExposure', 1.0)):
        if name in vb.layout:
            vb.set(name, v)
    return vb

# ---- niagara_gpu.py ----
# GPU emitter emulation (spec N-O4): run each GPU emitter's own cooked compute shader (DXBC SM5) under D3D11 WARP per tick.
#
# CB0 ($Globals) is assembled from the frozen FShader::Bindings exported by CUE4Parse (niagara-ns-maps -> data/gpu_bindings), keyed by
# C++ FNiagaraShader::FParameters byte offsets (5.7 layout, verified on NS_FrontEnd_RoofSmoke_01). Particle data is component-major
# (component c of instance i at [c * stride + i]), exactly as FNiagaraDataBuffer's GPU float/int buffers.



INDEX_NONE = 0xFFFFFFFF
INT32_MAX = 0x7FFFFFFF
MAX_SPAWN_INFOS = 8
THREADS = 64
MAX_GROUPS_X = 65535
STATIC_PARAMETERS_SIZE = 1728       # C++ sizeof(FNiagaraShader::FParameters) before DI structs (5.7; first DI struct of M2 at 1728)
# C++ FParameters offsets (spec N-O4 section 1)
OFF = dict(ComponentBufferSizeRead=0, ComponentBufferSizeWrite=4, SimStart=8, InputFloat=16, InputHalf=24, InputInt=32, StaticInputFloat=40,
           RWOutputFloat=48, RWOutputHalf=56, RWOutputInt=64, RWInstanceCounts=72, ReadInstanceCountOffset=80, WriteInstanceCountOffset=84,
           FreeIDList=88, RWIDToIndexTable=96, SimulationStageIterationInfo=112, ParticleIterationStateInfo=128, EmitterTickCounter=140,
           EmitterSpawnInfoOffsets=144, EmitterSpawnInfoParams=176, NumSpawnedInstances=304, DispatchThreadIdBounds=320,
           DispatchThreadIdToLinear=336, IndirectDispatchArgs=352, IndirectDispatchArgsOffset=360,
           Global=384, System=448, Owner=576, Emitter=1600)
CURVES = {'NiagaraDataInterfaceCurve', 'NiagaraDataInterfaceVector2DCurve', 'NiagaraDataInterfaceVectorCurve', 'NiagaraDataInterfaceColorCurve',
          'NiagaraDataInterfaceVector4Curve'}
gpu_VECTOR_FIELD = 'NiagaraDataInterfaceVectorField'
gpu_SPRITE_INFO = 'NiagaraDataInterfaceSpriteRendererInfo'
gpu_CAMERA = 'NiagaraDataInterfaceCamera'
gpu_COLLISION = 'NiagaraDataInterfaceCollisionQuery'
LANDSCAPE = 'NiagaraDataInterfaceLandscape'
gpu_PARTICLE_READ = 'NiagaraDataInterfaceParticleRead'
GBUFFER = 'NiagaraDataInterfaceGBuffer'
# Array DIs (NiagaraDataInterfaceArrayImplHelpers.h): {FIntPoint ArrayBufferParams, SRV ArrayReadBuffer @8, UAV ArrayRWBuffer @16} -> 32 B.
ARRAY_FORMATS = {'NiagaraDataInterfaceArrayFloat': ('float', 1), 'NiagaraDataInterfaceArrayFloat2': ('float2', 1),
                 'NiagaraDataInterfaceArrayFloat3': ('float', 3), 'NiagaraDataInterfaceArrayPosition': ('float', 3),
                 'NiagaraDataInterfaceArrayColor': ('float4', 1), 'NiagaraDataInterfaceArrayFloat4': ('float4', 1),
                 'NiagaraDataInterfaceArrayBool': ('r8uint', 1)}
# Included FGlobalDistanceFieldParameters2 (shared by every CollisionQuery DI of a script; the first one owns it): 544 B.
# Offsets observed in the frozen bindings (C++ struct + a 16-byte insert after the four textures, spec N-O4 section 5).
GDF = dict(PageAtlasTexture=0, CoverageAtlasTexture=8, PageTableTexture=16, MipTexture=24, CenterAndExtent=48, WorldToUVAddAndMul=144,
           MipWorldToUVScale=240, MipWorldToUVBias=336, MipFactor=432, MipTransition=436, ClipmapSizeInPages=440, InvPageAtlasSize=448,
           InvCoverageAtlasSize=464, VolumeDimension=476, VolumeTexelSize=480, MaxDFAOConeDistance=484, NumClipmaps=488,
           CoveredExpandSurfaceScale=492, NotCoveredExpandSurfaceScale=496, NotCoveredMinStepScale=500, DitheredStepThreshold=504,
           DitheredTraceThreshold=508, CoverageSampler=512, PageAtlasSampler=520, MipSampler=528)
GDF_SIZE = 544
# C++ nested FShaderParameters sizes (16-aligned):
#   curves       {MinTime, MaxTime, InvTimeRange, CurveLUTNumMinusOne, LUTOffset, SRV CurveLUT}          -> 32
#   vector field {float3 TilingAxes, Dimensions, MinBounds, MaxBounds (16 each), Texture3D @64, Sampler @72} -> 80
#   sprite info  {bIsValid, SourceMode, Alignment, FacingMode, bSubImageBlend, float2 SubImageSize @24}      -> 32
#   landscape    {BaseColor VT: SRV, uint4 page table @8, sampler @16, LWCTile @32, WorldToUv @48, UnpackSRGB/YCoCg/Enabled @112,
#                 PackedUniform0/1 + Uniforms @128; Height VT @176 (same shape, Enabled @288); Normal VT: SRVs @352/360, page table @368,
#                 samplers @376/384, LWCTile @400, WorldToUv @416, uniforms @480..528, UnpackMode @544, Enabled @548; cached height:
#                 texture @552, sampler @560, Enabled @568, LWCTile @576, WorldToUv @592, UvToWorld @656, UvScaleBias @720, WorldGridSize @736,
#                 Dimension @744; PointClampedSampler @752; Texture2D<uint> CachedPhysMat @760, Dimension @768}  -> 784
#   particle read {IsLocalSpace, NumSpawnedParticles, SpawnedParticlesAcquireTag, InstanceCountOffset, ParticleStrideFloat/Int/Half,
#                 AcquireTagRegisterIndex, SRVs SpawnedIDs @32, IDToIndexTable @40, InputFloat @48, InputInt @56, InputHalf @64} -> 80,
#                 then loose int4 AttributeIndices[N4] and AttributeCompressed[N4], N4 = max(1, ceil(GeneratedFunctions / 4))
#   gbuffer      {RDG Texture2D VelocityTexture, sampler @8} -> 16; the G-buffer itself is the SceneTexturesStruct uniform buffer
DI_STRUCT_SIZE = {**{c: 32 for c in CURVES}, gpu_VECTOR_FIELD: 80, gpu_SPRITE_INFO: 32, gpu_CAMERA: 16,   # camera {int32 SplitscreenMode}
                  **{c: 32 for c in ARRAY_FORMATS}, LANDSCAPE: 784, GBUFFER: 16}
LANDSCAPE_TEXTURES = {0: 'half4', 8: 'uint4', 176: 'half4', 184: 'uint4', 352: 'half4', 360: 'half4', 368: 'uint4', 552: 'half4', 760: 'uint4'}
LANDSCAPE_IDENTITY = (48, 224, 416, 592, 656)
LANDSCAPE_SAMPLERS = {16: 'bilinear', 192: 'bilinear', 376: 'bilinear', 384: 'bilinear', 560: 'bilinear', 752: 'point'}
PR_SRVS = dict(SpawnedIDsBuffer=32, IDToIndexTable=40, InputFloatBuffer=48, InputIntBuffer=56, InputHalfBuffer=64)
# GetValueTypeFromFuncName + CheckVariableType: 'Get <Value> By Index|ID' -> (int registers?, accepted variable types)
PR_VALUE_TYPES = {'Int': (True, ("Class'NiagaraInt32'",)), 'Bool': (True, ("Class'NiagaraBool'",)), 'Float': (False, ("Class'NiagaraFloat'",)),
                  'Vector 2D': (False, ("Class'Vector2f'",)), 'Vector': (False, ("Class'Vector3f'", "Class'NiagaraPosition'")),
                  'Vector 4': (False, ("Class'Vector4f'",)), 'Color': (False, ("Class'LinearColor'",)),
                  'Position': (False, ("Class'NiagaraPosition'",)), 'Quaternion': (False, ("Class'Quat4f'",)), 'ID': (True, ("Class'NiagaraID'",))}
SPRITE_ENUMS = {'SourceMode': ['Particles', 'Emitter'], 'Alignment': ['Unaligned', 'VelocityAligned', 'CustomAlignment', 'Automatic'],
                'FacingMode': ['FaceCamera', 'FaceCameraPlane', 'CustomFacingVector', 'FaceCameraPosition', 'FaceCameraDistanceBlend', 'Automatic']}
FILTER_LINEAR, FILTER_POINT, ADDRESS_WRAP, ADDRESS_CLAMP = 0x15, 0x0, 1, 3
# SceneTexturesStruct (FSceneTextureUniformParameters, resources only): resource-table index -> member. The game layout has 17 resources
# (PointClampSampler is 16; stock 5.3 has 14, 5.7 15) and keeps the stock order for 0-5. The 121 SM5 corpus records that bind the buffer
# use only 1 (depth, through InvDeviceZToWorldZTransform), 3 (GBufferA normal, N * 2 - 1), 4 (GBufferB.a shading model), 5 (GBufferC) and 16.
SCENE_TEXTURES = {0: 'SceneColorTexture', 1: 'SceneDepthTexture', 2: 'ScenePartialDepthTexture', 3: 'GBufferATexture', 4: 'GBufferBTexture',
                  5: 'GBufferCTexture', 16: 'PointClampSampler'}
SCENE_DEPTHS = ('SceneDepthTexture', 'ScenePartialDepthTexture')
SHADING_MODEL_DEFAULT_LIT = 1


class GpuError(ValueError):
    pass


def f32w(v):
    return struct.unpack('<I', struct.pack('<f', float(v)))[0]


def gpu__ref_index(ref):
    return int(ref['ObjectPath'].rsplit('.', 1)[1])


def find_binding(bindings_dir, system_stem, script_name, outer_name, platform='SP_PCD3D_SM5'):
    path = Path(bindings_dir) / (system_stem + '.json')
    if not path.is_file():
        raise GpuError('gpu_bindings_missing:' + system_stem)
    j = json.loads(path.read_text(encoding='utf-8'))
    for s in j.get('scripts', []):
        if s.get('name') == script_name and s.get('outer') == outer_name:
            for r in s.get('resources', []):
                if r.get('shaderPlatform') == platform and r.get('shaders'):
                    return r['resourceHash'], r['shaders'][0]['bindings']
    raise GpuError('gpu_binding_not_found:%s.%s' % (outer_name, script_name))


def _long(path):
    path = os.path.abspath(path)
    return '\\\\?\\' + path if os.name == 'nt' and len(path) >= 260 and not path.startswith('\\\\?\\') else path


def _read_record(shader_root, resource_hash):
    """Shader record split around its DXBC container: (FShaderResourceTable header, trailer), or None without a record."""
    hits = glob.glob(str(Path(shader_root) / '*SM5*' / resource_hash / '*.ushaderrecord'))
    if not hits:
        return None
    with open(_long(hits[0]), 'rb') as f:
        data = f.read()
    i = data.find(b'DXBC')
    return data[:i], data[i + struct.unpack_from('<I', data, i + 24)[0]:]


def record_uniform_buffers(shader_root, resource_hash):
    """Uniform buffer names in cbuffer-slot order from the shader record trailer ('u' chunk; $Globals is slot 0)."""
    record = _read_record(shader_root, resource_hash)
    if record is None:
        return ['$Globals']
    trailer = record[1]
    if len(trailer) < 4 or struct.unpack_from('<I', trailer, len(trailer) - 4)[0] != len(trailer):
        return ['$Globals']
    off, chunks = 0, {}
    while off < len(trailer) - 4:
        key, size = chr(trailer[off]), struct.unpack_from('<I', trailer, off + 1)[0]
        chunks[key] = trailer[off + 5:off + 5 + size]
        off += 5 + size
    u = chunks.get('u')
    if not u:
        return ['$Globals']
    count, pos, names = struct.unpack_from('<I', u)[0], 4, []
    for _ in range(count):
        n = struct.unpack_from('<I', u, pos)[0]
        names.append(u[pos + 4:pos + 4 + n - 1].decode('ascii'))
        pos += 4 + n
    return names


RESOURCE_TABLE_MAPS = ('srv', 'sampler', 'uav', 'layout_hashes', 'texture', 'collection')   # FShaderResourceTable arrays after ResourceTableBits


def record_resource_table(shader_root, resource_hash):
    """Global uniform buffer resources from the record's FShaderResourceTable: [(map, uniform buffer, resource index, bind slot)] for the
    srv, sampler, uav and texture maps. Each map is a BuildResourceTableTokenStream: one start offset per cbuffer slot (0 = none), then
    (UB << 24 | Resource << 8 | Bind) tokens per buffer, closed by 0xFFFFFFFF."""
    record = _read_record(shader_root, resource_hash)
    if record is None or len(record[0]) < 4:
        return []
    header, names = record[0], record_uniform_buffers(shader_root, resource_hash)
    maps, off = {}, 4
    for name in RESOURCE_TABLE_MAPS:
        n = struct.unpack_from('<I', header, off)[0]
        maps[name] = struct.unpack_from('<%dI' % n, header, off + 4)
        off += 4 + 4 * n
    out = []
    for name in ('srv', 'sampler', 'uav', 'texture'):
        words = maps[name]
        for ub in range(min(len(names), len(words))):
            k = words[ub]
            while k and k < len(words) and words[k] != INDEX_NONE and words[k] >> 24 == ub:
                out.append((name, names[ub], (words[k] >> 8) & 0xFFFF, words[k] & 0xFF))
                k += 1
    return out


def scene_texture_bindings(table):
    """SceneTexturesStruct members a shader reads: [(member, 'srv' | 'sampler', slot)]; an unmapped resource index is an error."""
    out = []
    for kind, ub, res, slot in table:
        if ub != 'SceneTexturesStruct':
            continue
        member = SCENE_TEXTURES.get(res)
        if member is None or kind == 'uav' or (kind == 'sampler') != member.endswith('Sampler'):
            raise GpuError('gpu_scene_texture_unmapped:%s:%d' % (kind, res))
        out.append((member, 'sampler' if kind == 'sampler' else 'srv', slot))
    return out


def flat_ground_scene(cam, ground_z, base_color, size=None, near=None):
    """Synthetic SceneTextures of an infinite flat ground at world height ground_z (UE cm) seen by cam (the View UB camera, same tuple as
    niagara_view_ub.camera_view): per texel the camera ray through the texel centre (ScreenPositionScaleBias UV convention) meets the plane at
    view depth t; ground texels get reversed-Z device depth near / t and an encoded default-lit G-buffer (normal +Z, specular 0.5,
    roughness 1, base colour), sky texels depth 0 and a black G-buffer. Shaders only sample these with normalized UVs (sample_l, point
    clamp), so the texture size only quantizes the lookup. Returns member -> (format, texels [H, W, ...])."""
    pass
    w, h = size or VIEW_SIZE
    near = near or NEAR
    cam = cam if cam is not None else DEFAULT_CAMERA
    fwd, right, up = (np.array(v, dtype=np.float64) for v in rotation_axes(*cam[3:6]))
    half = np.tan(np.radians(cam[6] if cam[6] > 0 else 90.0) / 2.0)
    aspect = VIEW_SIZE[0] / VIEW_SIZE[1]
    ndc_x = (np.arange(w) + 0.5) / w * 2.0 - 1.0
    ndc_y = 1.0 - (np.arange(h) + 0.5) / h * 2.0
    # ray d = fwd + right * x * half + up * y * half / aspect has view-space z = 1, so its plane hit is at view depth t
    dz = np.float32(fwd[2]) + (right[2] * half * ndc_x).astype(np.float32)[None, :] + (up[2] * half / aspect * ndc_y).astype(np.float32)[:, None]
    with np.errstate(divide='ignore', invalid='ignore'):
        t = np.float32(float(ground_z) - float(cam[2])) / dz
        ground = np.isfinite(t) & (t >= near)
        depth = np.where(ground, np.float32(near) / t, np.float32(0))
    # one RGBA16F texel is 64 bits: select its bit pattern per texel (no float16 arithmetic, no 4-channel broadcast)
    texel = lambda value: (ground * np.asarray(value, np.float16).view(np.uint64)[0]).view(np.float16).reshape(h, w, 4)
    gbuffer_a = texel((0.5, 0.5, 1.0, 1.0))                                            # EncodeNormal(+Z) = N * 0.5 + 0.5, PerObjectGBufferData
    gbuffer_b = texel((0.0, 0.5, 1.0, SHADING_MODEL_DEFAULT_LIT / 255.0))              # metallic, specular, roughness, shading model | mask << 4
    gbuffer_c = texel(tuple(base_color[:3]) + (1.0,))                                  # linear base colour, AO
    scene = {name: ('float', depth) for name in SCENE_DEPTHS}
    scene.update(GBufferATexture=('half4', gbuffer_a), GBufferBTexture=('half4', gbuffer_b), GBufferCTexture=('half4', gbuffer_c))
    return scene


def scene_views(dev, scene, members):
    """SRVs / sampler for the SceneTexturesStruct members a shader reads. scene None (no scene) binds the engine's empty-scene
    fallbacks: black (0, 0, 0, 0) colour and G-buffers, DepthDummy device depth 0 (nothing drawn); the sampler is point clamp."""
    out = {}
    for member in set(members):
        if member == 'PointClampSampler':
            out[member] = dev.sampler(FILTER_POINT, ADDRESS_CLAMP)
            continue
        fmt, texels = (scene or {}).get(member) or (('float', np.zeros((1, 1), np.float32)) if member in SCENE_DEPTHS
                                                    else ('half4', np.zeros((1, 1, 4), np.float16)))
        out[member] = dev.texture2d(texels, (texels.shape[1], texels.shape[0]), fmt)
    return out


def find_dxbc(shader_root, resource_hash):
    hits = glob.glob(str(Path(shader_root) / '*SM5*' / resource_hash / '*.dxbc'))
    if not hits:
        raise GpuError('gpu_shader_missing:' + resource_hash)
    path = os.path.abspath(hits[0])
    if os.name == 'nt' and len(path) >= 260 and not path.startswith('\\\\?\\'):
        path = '\\\\?\\' + path   # extended-length path: record names exceed MAX_PATH (Blender's Python is not long-path aware)
    with open(path, 'rb') as f:
        return f.read()


def gpu_spawn_info(spawn_infos, active, cap=2000000):
    """Spawn info arrays and InstancesToSpawn (NiagaraEmitterInstanceImpl.cpp 1373-1453, spec N-O2 section 8)."""
    offsets = [INT32_MAX] * MAX_SPAWN_INFOS
    params = [(0.0, 0.0, 0, 0)] * MAX_SPAWN_INFOS
    total = 0
    k = 0
    if active:
        for count, start_dt, interval, group in spawn_infos:
            if count <= 0 or k >= MAX_SPAWN_INFOS:
                continue
            if total + count > cap:
                count = cap - total
                if count <= 0:
                    break
            params[k] = (interval, start_dt, group, total)
            total += count
            offsets[k] = total
            k += 1
    params = [p if i < k else (0.0, 0.0, 0, total) for i, p in enumerate(params)]
    return total, offsets, params


class GpuEmitterProgram:
    """One GPU emitter: shader, binding table, data-interface static data."""

    def __init__(self, device, doc, emitter_export, gpu_script_index, bindings_dir, shader_root, system_stem, vector_fields=None, ground=None):
        """ground: world height (UE cm) of the synthetic flat ground the Landscape DIs report; None is an empty world (no landscape)."""
        self.dev = device
        script = doc[gpu_script_index]
        self.script_name = script.get('Name')
        self.outer = emitter_export.get('Name')
        props = script.get('Properties') or {}
        vm = props.get('CachedScriptVM') or {}
        self.stages = vm.get('SimulationStageMetaData') or []
        if len(self.stages) != 1:
            raise GpuError('gpu_multi_stage:%d' % len(self.stages))
        self.resource_hash, self.bindings = find_binding(bindings_dir, system_stem, self.script_name, self.outer)
        self.cs = device.compute_shader(find_dxbc(shader_root, self.resource_hash))
        self.uniform_buffers = record_uniform_buffers(shader_root, self.resource_hash)
        self.scene_resources = scene_texture_bindings(record_resource_table(shader_root, self.resource_hash)) \
            if 'SceneTexturesStruct' in self.uniform_buffers else []
        self.resources = {(r['baseType'], r['byteOffset']): r['baseIndex'] for r in self.bindings['resources']}
        meta = (vm.get('ShaderScriptParametersMetadata') or {}).get('DataInterfaceParamInfo') or []
        infos = vm.get('DataInterfaceInfo') or []
        resolved = {r['CompileName']: r for r in props.get('ResolvedDataInterfaces') or []}
        self.di_values = {}
        self.di_views = {}          # C++ offset of a DI resource member -> SRV / sampler object
        self.particle_reads = []    # per-dispatch source bindings (dispatch_tick)
        static = []
        base = STATIC_PARAMETERS_SIZE
        gdf_owned = False
        for m, info in zip(meta, infos):
            cls = m['DIClassName']
            if cls not in DI_STRUCT_SIZE and cls not in (gpu_COLLISION, gpu_PARTICLE_READ):
                raise GpuError('gpu_di_unimplemented:' + cls)
            rd = resolved.get(info['Name'])
            if rd is None:
                raise GpuError('gpu_di_unresolved:' + info['Name'])
            exp = doc[gpu__ref_index(rd['ResolvedDataInterface'])]
            di_props = exp.get('Properties') or {}
            if cls in CURVES:
                curve = curve_from_properties(exp['Type'], di_props)
                offset = sum(len(x) for x in static)
                static.append(curve.lut)
                self.di_values.update({base: f32w(curve.min_time), base + 4: f32w(curve.max_time), base + 8: f32w(curve.inv_time_range),
                                       base + 12: int(curve.num_samples_minus_one), base + 16: int(offset)})
            elif cls == gpu_VECTOR_FIELD:
                self._vector_field(base, di_props, vector_fields or {})
            elif cls == gpu_SPRITE_INFO:
                self._sprite_info(base, doc, di_props)
            elif cls == gpu_CAMERA:
                self.di_values[base] = 0        # ESplitScreenType::None
            elif cls in ARRAY_FORMATS:
                self._array(base, cls, di_props)
            elif cls == LANDSCAPE:
                if ground is None:
                    self._no_landscape(base)
                else:
                    self._flat_landscape(base, ground)
            elif cls == GBUFFER:
                self._gbuffer(base)
            elif cls == gpu_PARTICLE_READ:
                base += self._particle_read(base, di_props, m)
                continue
            elif cls == gpu_COLLISION:
                if gdf_owned:
                    continue                    # included struct already added by the first CollisionQuery DI
                gdf_owned = True
                self._empty_gdf(base)
                base += GDF_SIZE
                continue
            base += DI_STRUCT_SIZE[cls]
        lut = np.concatenate(static).astype(np.float32) if static else np.zeros(4, np.float32)
        _, self.static_srv, _ = device.typed_buffer(lut.view(np.uint32), 'float')
        self.cb_words = max(p['baseIndex'] + p['byteSize'] for p in self.bindings['parameters']) // 4 + 4 if self.bindings['parameters'] else 4

    def _vector_field(self, base, props, vector_fields):
        """UNiagaraDataInterfaceVectorField::SetShaderParameters: proxy dims/bounds, RGBA16F volume, bilinear sampler by tiling."""
        asset, field_class = field_asset(props.get('Field'))
        tiles = [bool(props.get('bTileX', False)), bool(props.get('bTileY', False)), bool(props.get('bTileZ', False))]
        vf = vector_fields.get(asset) if asset is not None and field_class == 'VectorFieldStatic' else None
        if asset is not None and field_class == 'VectorFieldStatic' and vf is None:
            raise GpuError('vector_field_data_missing:' + asset)
        if vf is not None:
            sx, sy, sz = vf.size
            tex = np.zeros((sz, sy, sx, 4), np.uint16)
            tex[..., :3] = vf.halves.reshape(sz, sy, sx, 3)
            dims, lo, hi = vf.size, vf.bounds_min, vf.bounds_max
        else:  # GBlackVolumeTexture, zero proxy
            sx = sy = sz = 1
            tex = np.zeros((1, 1, 1, 4), np.uint16)
            dims, lo, hi = (0, 0, 0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        for k, vec in enumerate((tuple(1.0 if t else 0.0 for t in tiles), dims, lo, hi)):
            for c in range(3):
                self.di_values[base + 16 * k + 4 * c] = f32w(vec[c])
        self.di_views[base + 64] = self.dev.texture3d(tex, (sx, sy, sz))
        self.di_views[base + 72] = self.dev.sampler_axes(
            FILTER_LINEAR, *[ADDRESS_WRAP if t else ADDRESS_CLAMP for t in tiles])

    def _array(self, base, cls, props):
        """Read-only array: ArrayBufferParams = (Num, max(0, Num - 1)); data in the helper's ReadPixelFormat (spec N-O4 section 5)."""
        fmt, per = ARRAY_FORMATS[cls]
        words = array_words(cls, props.get(ARRAY_TYPES[cls][0]))
        num = words.shape[0]
        if cls == 'NiagaraDataInterfaceArrayBool':
            data = bytes(1 if w else 0 for w in words.ravel())
            elements = num
        else:
            data = words.astype('<u4').tobytes()
            elements = num * per if per > 1 else num
        self.di_values.update({base: num, base + 4: max(0, num - 1)})
        _, srv = self.dev.format_buffer(data, fmt, elements)
        self.di_views[base + 8] = srv

    def _empty_gdf(self, base):
        """Global distance field with no clipmaps (empty scene): black volumes, every distance query reports invalid."""
        for off in range(GDF['CenterAndExtent'], GDF['MipWorldToUVBias'] + 96, 4):
            self.di_values[base + off] = 0
        for name in ('MipFactor', 'MipTransition', 'ClipmapSizeInPages', 'VolumeDimension', 'VolumeTexelSize', 'MaxDFAOConeDistance', 'NumClipmaps',
                     'CoveredExpandSurfaceScale', 'NotCoveredExpandSurfaceScale', 'NotCoveredMinStepScale', 'DitheredStepThreshold', 'DitheredTraceThreshold'):
            self.di_values[base + GDF[name]] = 0
        for name in ('InvPageAtlasSize', 'InvCoverageAtlasSize'):
            for c in range(3):
                self.di_values[base + GDF[name] + 4 * c] = 0
        black = self.dev.texture3d(np.zeros((1, 1, 1, 4), np.uint16), (1, 1, 1))
        for name in ('PageAtlasTexture', 'CoverageAtlasTexture', 'PageTableTexture', 'MipTexture'):
            self.di_views[base + GDF[name]] = black
        clamp = self.dev.sampler_axes(FILTER_LINEAR, ADDRESS_CLAMP, ADDRESS_CLAMP, ADDRESS_CLAMP)
        for name in ('CoverageSampler', 'PageAtlasSampler', 'MipSampler'):
            self.di_views[base + GDF[name]] = clamp

    def _no_landscape(self, base):
        """UNiagaraDataInterfaceLandscape::SetShaderParameters with no landscape: every *_Default setter (black textures, identity transforms,
        all sources disabled); queries report invalid and zero height."""
        for off in range(0, DI_STRUCT_SIZE[LANDSCAPE], 4):
            if off not in LANDSCAPE_TEXTURES and off not in LANDSCAPE_SAMPLERS:
                self.di_values[base + off] = 0
        for m in LANDSCAPE_IDENTITY:
            for c in range(4):
                self.di_values[base + m + 20 * c] = f32w(1.0)
        black = {'half4': self.dev.texture2d(np.array([0, 0, 0, 0x3C00], np.uint16), (1, 1), 'half4'),   # GBlackTexture(WithSRV)
                 'uint4': self.dev.texture2d(np.zeros(4, np.uint32), (1, 1), 'uint4')}                    # GBlackUintTexture
        samplers = {'bilinear': self.dev.sampler(FILTER_LINEAR, ADDRESS_CLAMP), 'point': self.dev.sampler(FILTER_POINT, ADDRESS_CLAMP)}
        for off, fmt in LANDSCAPE_TEXTURES.items():
            self.di_views[base + off] = black[fmt]
        for off, kind in LANDSCAPE_SAMPLERS.items():
            self.di_views[base + off] = samplers[kind]

    def _flat_landscape(self, base, height):
        """Landscape DI over an infinite flat ground at world height (UE cm), through the cached-height source (virtual textures stay
        disabled): a 4x4 R32F of the height whose UV is pinned to a texel centre (UvScaleBias (0, 0, 0.375, 0.375)), so GetHeight is valid
        and exact everywhere and the gathered GetWorldNormal is +Z through the identity UvToWorld; physical material index 0."""
        self._no_landscape(base)
        n = 4
        self.di_views[base + 552] = self.dev.texture2d(np.full((n, n), height, np.float32), (n, n), 'float')
        self.di_values.update({base + 568: 1, base + 720: f32w(0.0), base + 724: f32w(0.0), base + 728: f32w(0.375), base + 732: f32w(0.375),
                               base + 736: f32w(100.0), base + 740: f32w(100.0), base + 744: n, base + 748: n})

    def _gbuffer(self, base):
        """UNiagaraDataInterfaceGBuffer::SetShaderParameters: no velocity texture is produced for the bake view, so the black dummy with a
        point clamp sampler. The G-buffer itself is read through the SceneTexturesStruct uniform buffer (dispatch_tick, scene_views)."""
        self.di_views[base] = self.dev.texture2d(np.zeros((1, 1, 4), np.float16), (1, 1), 'half4')
        self.di_views[base + 8] = self.dev.sampler(FILTER_POINT, ADDRESS_CLAMP)

    def _particle_read(self, base, props, meta):
        """GPU particle read: CreateShaderStorage keeps one attribute slot per generated function (NAME_None for GetLocalSpace etc.);
        source data binds per dispatch. Returns the DI's parameter size (struct + both loose arrays)."""
        slots = []
        for f in meta.get('GeneratedFunctions') or []:
            spec = {s.get('Item1'): s.get('Item2') for s in f.get('Specifiers') or []}
            name = str(f.get('DefinitionName', ''))
            value = name[4:].rsplit(' By ', 1)[0] if name.startswith('Get ') and (name.endswith(' By Index') or name.endswith(' By ID')) else None
            slots.append((spec.get('Attribute'), PR_VALUE_TYPES.get(value)) if spec.get('Attribute') else (None, None))
        n4 = max(1, -(-len(slots) // 4))
        self.particle_reads.append(SimpleNamespace(base=base, source=str((props.get('EmitterBinding') or {}).get('EmitterName', '')), slots=slots, n4=n4))
        return 80 + 32 * n4

    def _sprite_info(self, base, doc, props):
        """FNDIProxy ShaderParameters_RT from the referenced sprite renderer (raw cooked enums; class defaults when absent)."""
        ref = props.get('SpriteRenderer')
        rp = (doc[gpu__ref_index(ref)].get('Properties') or {}) if isinstance(ref, dict) else None
        r = rp or {}
        enum = lambda key, default: SPRITE_ENUMS[key].index(str(r.get(key, default)).rsplit('::', 1)[-1])
        size = r.get('SubImageSize') or {}
        self.di_values.update({base: 1 if rp is not None else 0, base + 4: enum('SourceMode', 'Particles'), base + 8: enum('Alignment', 'Automatic'),
                               base + 12: enum('FacingMode', 'Automatic'), base + 16: 1 if r.get('bSubImageBlend', True) else 0,
                               base + 24: f32w(size.get('X', 1.0)), base + 28: f32w(size.get('Y', 1.0))})

    def constant_buffer(self, values):
        cb = np.zeros(self.cb_words, np.uint32)
        for p in self.bindings['parameters']:
            for w in range(p['byteSize'] // 4):
                cpp = p['byteOffset'] + 4 * w
                v = values.get(cpp, self.di_values.get(cpp))
                if v is None:
                    raise GpuError('gpu_parameter_unset:%d' % cpp)
                cb[p['baseIndex'] // 4 + w] = v & 0xFFFFFFFF
        return cb

    def slot(self, kind, name):
        return self.resources.get((kind, OFF[name]))


class GpuIdState:
    """GPU persistent IDs (NiagaraDataSet.cpp AllocateGPUFreeIDs, NiagaraInitFreeIDBuffer.usf, NiagaraComputeFreeIDs.usf)."""
    CHUNK = 1024

    def __init__(self):
        self.allocated = 0
        self.free_list = np.zeros(0, np.int32)
        self.table = np.zeros(0, np.int32)      # last ID -> index table (particle read by ID)

    def allocate(self, instances):
        want = -(-max(1, instances) // self.CHUNK) * self.CHUNK
        if want <= self.allocated:
            return
        fresh = np.arange(self.allocated, want, dtype=np.int32)   # new IDs first, then the existing buffer
        self.free_list = np.concatenate([fresh, self.free_list])[:want]
        self.allocated = want

    def compute_free(self, id_to_index):
        """Free IDs (table entry -1) ascending, written from the start of the list; later entries keep their old values."""
        self.table = np.asarray(id_to_index, np.int32)[:self.allocated].copy()
        free = np.nonzero(id_to_index[:self.allocated] == -1)[0].astype(np.int32)
        self.free_list = self.free_list.copy()
        self.free_list[:len(free)] = free


def struct_words(packed):
    return np.frombuffer(packed, dtype='<u4')


def put_block(values, base, packed_cur, packed_prev):
    for k, w in enumerate(np.concatenate([struct_words(packed_cur), struct_words(packed_prev)])):
        values[base + 4 * k] = int(w)


def particle_read_attributes(pr, layout):
    """NDIParticleRead_FindAttributeIndices: register start per slot (-1 when absent or mistyped; no half attributes), AcquireTag register."""
    indices = [-1] * (4 * pr.n4)
    for k, (name, value) in enumerate(pr.slots):
        var = layout.find(name) if name and value else None
        if var is not None and var.type.key in value[1]:
            indices[k] = var.int_start if value[0] else var.float_start
    var = layout.find('ID')
    return indices, (var.int_start + 1 if var is not None else -1)


def _bind_particle_reads(prog, values, views, counts, sources):
    """Per-dispatch ParticleRead parameters (UNiagaraDataInterfaceParticleRead::SetShaderParameters). A GPU source binds its current data
    (ticked earlier this frame); its instance count goes into this dispatch's count buffer at InstanceCountOffset. CPU or missing sources get the
    engine's error parameters."""
    dev = prog.dev
    dummy_i = dummy_f = None
    for k, pr in enumerate(prog.particle_reads):
        src = sources(pr.source) if sources is not None else None
        b = pr.base
        values[b] = 1 if src is not None and src.local_space else 0
        indices, compressed = [-1] * (4 * pr.n4), [0] * (4 * pr.n4)
        if src is None or not src.gpu:
            if dummy_i is None:
                dummy_i = dev.typed_buffer(np.zeros(1, np.uint32), 'sint')[1]
                dummy_f = dev.typed_buffer(np.zeros(1, np.uint32), 'float')[1]
            values.update({b + 4: 0, b + 8: 0, b + 12: INDEX_NONE, b + 16: 0, b + 20: 0, b + 24: 0, b + 28: INDEX_NONE})
            bound = dict(SpawnedIDsBuffer=dummy_i, IDToIndexTable=dummy_i, InputFloatBuffer=dummy_f, InputIntBuffer=dummy_i, InputHalfBuffer=dummy_f)
        else:
            n, lay = src.count, src.layout
            indices, acquire = particle_read_attributes(pr, lay)
            counts[2 + k] = n
            fl = np.concatenate([np.asarray(src.buffer['float', j][:n], np.uint32) for j in range(lay.total_float)]) if n and lay.total_float else np.zeros(1, np.uint32)
            it = np.concatenate([np.asarray(src.buffer['int32', j][:n], np.uint32) for j in range(lay.total_int)]) if n and lay.total_int else np.zeros(1, np.uint32)
            ids = src.ids
            free = ids.free_list.view(np.uint32) if ids is not None and ids.free_list.size else np.zeros(1, np.uint32)
            table = ids.table.view(np.uint32) if ids is not None and ids.table.size else np.zeros(1, np.uint32)
            values.update({b + 4: src.spawned & 0xFFFFFFFF, b + 8: src.acquire_tag & 0xFFFFFFFF, b + 12: 2 + k, b + 16: n, b + 20: n, b + 24: 0,
                           b + 28: acquire & 0xFFFFFFFF})
            bound = dict(SpawnedIDsBuffer=dev.typed_buffer(free, 'sint')[1], IDToIndexTable=dev.typed_buffer(table, 'sint')[1],
                         InputFloatBuffer=dev.typed_buffer(fl, 'float')[1], InputIntBuffer=dev.typed_buffer(it, 'sint')[1],
                         InputHalfBuffer=dev.typed_buffer(np.zeros(1, np.uint32), 'float')[1])
        for name, off in PR_SRVS.items():
            views[b + off] = bound[name]
        for w in range(4 * pr.n4):
            values[b + 80 + 4 * w] = indices[w] & 0xFFFFFFFF
            values[b + 80 + 16 * pr.n4 + 4 * w] = compressed[w]


def dispatch_tick(prog, layout, inputs_f, inputs_i, count, alloc, spawn_infos, active, reset, tick_counter, blocks, ids=None, ubs=None, sources=None,
                  scene=None):
    """One spawn+update dispatch. inputs_*: dict component -> uint32 array (len count); sources: emitter name -> current particle data of a
    ParticleRead source (count, layout, buffer, ids, spawned, acquire_tag, local_space, gpu) or None; scene: SceneTexturesStruct texels
    (flat_ground_scene) or None for the empty-scene fallbacks. Returns (new_count, float cols, int cols)."""
    dev = prog.dev
    to_spawn, offs, params = gpu_spawn_info(spawn_infos, active, cap=max(0, alloc - count))
    total = count + to_spawn
    nf, ni = layout.total_float, layout.total_int
    if total == 0:
        return 0, to_spawn, {j: np.zeros(0, np.uint32) for j in range(nf)}, {j: np.zeros(0, np.uint32) for j in range(ni)}
    mark = dev.mark()
    groups = (total + THREADS - 1) // THREADS
    gx, gy = (groups, 1) if groups <= MAX_GROUPS_X else (MAX_GROUPS_X, (groups + MAX_GROUPS_X - 1) // MAX_GROUPS_X)
    values = {OFF['ComponentBufferSizeRead']: count, OFF['ComponentBufferSizeWrite']: total, OFF['SimStart']: 1 if reset else 0,
              OFF['ReadInstanceCountOffset']: 0 if count > 0 else INDEX_NONE, OFF['WriteInstanceCountOffset']: 1,
              OFF['EmitterTickCounter']: tick_counter, OFF['NumSpawnedInstances']: to_spawn,
              OFF['IndirectDispatchArgsOffset']: 0}
    for k, w in enumerate([0xFFFFFFFF, 0, 0x00010000, 0x00010000]):  # SimulationStageIterationInfo: (INDEX_NONE, 0, 1 iteration, 1 loop)
        values[OFF['SimulationStageIterationInfo'] + 4 * k] = w
    for k, w in enumerate([0, 0, 0]):
        values[OFF['ParticleIterationStateInfo'] + 4 * k] = w
    for k, w in enumerate(offs):
        values[OFF['EmitterSpawnInfoOffsets'] + 4 * k] = w & 0xFFFFFFFF
    for k, (interval, start_dt, group, start) in enumerate(params):
        b = OFF['EmitterSpawnInfoParams'] + 16 * k
        values.update({b: f32w(interval), b + 4: f32w(start_dt), b + 8: group & 0xFFFFFFFF, b + 12: start & 0xFFFFFFFF})
    for k, w in enumerate([gx * THREADS, gy, 1]):
        values[OFF['DispatchThreadIdBounds'] + 4 * k] = w
    for k, w in enumerate([1, gx * THREADS, 0]):
        values[OFF['DispatchThreadIdToLinear'] + 4 * k] = w
    for name, (cur, prev) in blocks.items():
        put_block(values, OFF[name], cur, prev)
    counts = [count, 0] + [0] * max(2, len(prog.particle_reads))
    views = {}
    _bind_particle_reads(prog, values, views, counts, sources)
    cbs = []
    for k, name in enumerate(prog.uniform_buffers):
        if name == '$Globals':
            cbs.append(dev.constant_buffer(prog.constant_buffer(values)))
        elif ubs is not None and name in ubs:
            data = np.frombuffer(ubs[name] + b'\0' * (-len(ubs[name]) % 16), dtype='<u4') if ubs[name] else np.zeros(4, np.uint32)
            cbs.append(dev.constant_buffer(data))
        elif name == 'SceneTexturesStruct':
            cbs.append(dev.constant_buffer(np.zeros(4, np.uint32)))   # resources only: bound through the resource table below
        else:
            raise GpuError('gpu_uniform_buffer_unsupported:' + name)
    in_f = np.concatenate([inputs_f[j] for j in range(nf)]) if count and nf else np.zeros(4, np.uint32)
    in_i = np.concatenate([inputs_i[j] for j in range(ni)]) if count and ni else np.zeros(4, np.uint32)
    _, f_srv, _ = dev.typed_buffer(in_f, 'float')
    _, i_srv, _ = dev.typed_buffer(in_i, 'sint')
    of_buf, _, of_uav = dev.typed_buffer(np.zeros(max(1, nf * total), np.uint32), 'float', uav=True)
    oi_buf, _, oi_uav = dev.typed_buffer(np.zeros(max(1, ni * total), np.uint32), 'sint', uav=True)
    cnt_buf, _, cnt_uav = dev.typed_buffer(np.array(counts, np.uint32), 'uint', uav=True)
    id_buf = None
    if ids is not None:
        ids.allocate(alloc)
        _, free_srv, _ = dev.typed_buffer(ids.free_list.view(np.uint32), 'sint')
        id_buf, _, id_uav = dev.typed_buffer(np.full(ids.allocated, 0xFFFFFFFF, np.uint32), 'sint', uav=True)
    srvs, uavs = [None] * 32, [None] * 8
    if ids is not None:
        s = prog.slot('UBMT_SRV', 'FreeIDList')
        if s is not None:
            srvs[s] = free_srv
        s = prog.slot('UBMT_UAV', 'RWIDToIndexTable')
        if s is not None:
            uavs[s] = id_uav
    for name, view in (('InputFloat', f_srv), ('InputInt', i_srv), ('StaticInputFloat', prog.static_srv)):
        s = prog.slot('UBMT_SRV', name)
        if s is not None:
            srvs[s] = view
    for name, view in (('RWOutputFloat', of_uav), ('RWOutputInt', oi_uav), ('RWInstanceCounts', cnt_uav)):
        s = prog.slot('UBMT_UAV', name)
        if s is not None:
            uavs[s] = view
    samplers = [None] * 16
    for (kind, off), slot in prog.resources.items():
        if off >= STATIC_PARAMETERS_SIZE:
            view = views.get(off, prog.di_views.get(off))
            if view is None:
                raise GpuError('gpu_resource_unsupported:%s@%d' % (kind, off))
            if kind == 'UBMT_SAMPLER':
                samplers[slot] = view
            else:
                srvs[slot] = view
        elif off in (OFF['FreeIDList'], OFF['RWIDToIndexTable']) and ids is None:
            raise GpuError('gpu_resource_unsupported:%s@%d' % (kind, off))
    if prog.scene_resources:
        tex = scene_views(dev, scene, [member for member, _, _ in prog.scene_resources])
        for member, kind, slot in prog.scene_resources:
            table = samplers if kind == 'sampler' else srvs
            if table[slot] is not None:
                raise GpuError('gpu_slot_conflict:%s:%d' % (kind, slot))
            table[slot] = tex[member]
    last_s = max([i for i, v in enumerate(srvs) if v is not None], default=-1) + 1
    last_u = max([i for i, v in enumerate(uavs) if v is not None], default=-1) + 1
    last_m = max([i for i, v in enumerate(samplers) if v is not None], default=-1) + 1
    dev.dispatch(prog.cs, (gx, gy, 1), cbs=cbs, srvs=srvs[:last_s], uavs=uavs[:last_u], samplers=samplers[:last_m])
    new_count = int(dev.read(cnt_buf, 16)[1])
    outf = dev.read(of_buf, max(1, nf * total) * 4)
    outi = dev.read(oi_buf, max(1, ni * total) * 4)
    fcols = {j: outf[j * total:j * total + new_count].copy() for j in range(nf)}
    icols = {j: outi[j * total:j * total + new_count].copy() for j in range(ni)}
    if id_buf is not None:
        ids.compute_free(dev.read(id_buf, ids.allocated * 4).view(np.int32))
    dev.release_since(mark)
    return new_count, to_spawn, fcols, icols


def gpu_script_index(doc, emitter_export):
    props = emitter_export.get('Properties') or {}
    sel, _ = select_emitter_version(props)
    ref = props['VersionData'][sel].get('GPUComputeScript')
    if not isinstance(ref, dict):
        raise GpuError('gpu_script_missing')
    return gpu__ref_index(ref)

# ---- niagara_vm_numpy.py ----
# Raw-word numpy VectorVM backend; scalar-compatible IDs, external ABI and taints.
@dataclass
class NpDataset:
    inputs:dict=field(default_factory=dict)
    output_capacities:dict=field(default_factory=dict)
    instance_offset:int=0
    access_index:int=-1
@dataclass
class NpExecutionResult:
    registers:dict
    outputs:dict
    written:dict
    access_indices:dict
    instruction_count:int
    taints:dict
    output_taints:dict
    dataset_taints:dict
    id_states:dict
    ext_calls:list

def _elementwise(name,sources,backend,shim,fma,rng,n):
    u=[np.asarray(s,dtype=np.uint32) for s in sources];f=[s.view(np.float32) for s in u];i=[s.view(np.int32) for s in u];a=u[0];fa=f[0];ia=i[0];b=u[1] if len(u)>1 else None;fb=f[1] if len(u)>1 else None;ib=i[1] if len(u)>1 else None;c=u[2] if len(u)>2 else None;fc=f[2] if len(u)>2 else None;eps=np.float32(1e-8);one=np.float32(1)
    if name in ('fasi','iasf'):return a.copy()
    if name=='select':return (a&b)|(~a&c)
    if name=='abs':return a&np.uint32(0x7fffffff)
    if name=='b2f':return a&np.uint32(0x3f800000)
    if name=='b2i':return a&np.uint32(1)
    if name in ('f2b','i2b'):return np.where((fa if name=='f2b' else ia)>0,np.uint32(0xffffffff),np.uint32(0))
    if name=='f2i':
        out=np.full(n,0x80000000,dtype=np.uint32);valid=np.isfinite(fa)&(fa.astype(np.float64)>=-2147483648)&(fa.astype(np.float64)<2147483648);out[valid]=np.trunc(fa[valid].astype(np.float64)).astype(np.int32).view(np.uint32);return out
    if name=='i2f':return ia.astype(np.float32).view(np.uint32)
    if name.startswith('cmp'):
        integer=name.endswith('i');base=name[:-1] if integer else name;x,y=(ia,ib) if integer else (fa,fb);fn={'cmplt':np.less,'cmple':np.less_equal,'cmpgt':np.greater,'cmpge':np.greater_equal,'cmpeq':np.equal,'cmpneq':np.not_equal}[base];return np.where(fn(x,y),np.uint32(0xffffffff),np.uint32(0))
    if name in ('bit_and','logic_and'):return a&b
    if name in ('bit_or','logic_or'):return a|b
    if name in ('bit_xor','logic_xor'):return a^b
    if name in ('bit_not','logic_not'):return ~a
    if name in ('bit_lshift','bit_rshift'):
        counts=np.where(b>31,np.uint32(0),b);v=a<<counts if name=='bit_lshift' else a>>counts;return np.where(b>31,np.uint32(0),v)
    if name in ('addi','subi','muli','negi'):return {'addi':lambda:a+b,'subi':lambda:a-b,'muli':lambda:a*b,'negi':lambda:np.uint32(0)-a}[name]()
    if name=='absi':return np.abs(ia).view(np.uint32)
    if name in ('mini','maxi','clampi'):
        return np.asarray(np.minimum(ia,ib) if name=='mini' else np.maximum(ia,ib) if name=='maxi' else np.minimum(np.maximum(ia,ib),i[2]),dtype=np.int32).view(np.uint32)
    if name=='divi':
        x=ia.astype(np.int64);y=ib.astype(np.int64);v=np.where(y==0,0,(np.abs(x)//np.where(y==0,1,np.abs(y)))*np.where((x<0)!=(y<0),-1,1));v=np.where((x==-2147483648)&(y==-1),2147483647,v);return v.astype(np.int32).view(np.uint32)
    if name in _SVML57 or name=='rcp':
        x,y=fa,fb;bn=name
        if name=='acos':
            p=(one-fa)*(one+fa);x=np.where(p>eps,np.sqrt(p),np.float32(0));y=fa;bn='atan2'
        v=np.asarray(_backend57(bn,x.tolist(),y.tolist() if y is not None else None,backend,shim),dtype=np.float32)
        if name=='rcp':v=np.where(np.abs(fa)>eps,v,np.float32(0))
        if name=='pow':v=np.where(fa>eps,v,np.float32(0))
        if name=='fmod':v=np.where(np.abs(fb)<=eps,np.float32(0),v)
    elif name=='random':v=np.asarray(_rng57_values(rng,n),dtype=np.float32)*fa
    elif name in ('add','sub','mul'):v={'add':lambda:fa+fb,'sub':lambda:fa-fb,'mul':lambda:fa*fb}[name]()
    elif name=='div':v=np.where(np.abs(fb)>eps,fa/fb,np.float32(0))
    elif name=='neg':v=np.float32(0)-fa
    elif name in ('min','max'):v=np.where(fa<fb,fa,fb) if name=='min' else np.where(fa>fb,fa,fb)
    elif name=='clamp':
        t=np.where(fa>fb,fa,fb);v=np.where(t<fc,t,fc)
    elif name in ('sqrt','rsq'):v=np.where(fa>eps,np.sqrt(fa) if name=='sqrt' else one/np.sqrt(fa),np.float32(0))
    elif name in ('floor','ceil','trunc','round'):v={'floor':np.floor,'ceil':np.ceil,'trunc':np.trunc,'round':np.rint}[name](fa)
    elif name=='frac':v=fa-np.trunc(fa)
    elif name=='sign':v=np.where(fa>=0,np.float32(1),np.float32(-1))
    elif name in ('mad','lerp'):
        x,y,z=(fa,fb,fc) if name=='mad' else (fb,fc,fa*(one-fc))
        v=np.asarray([_fma57(float(a),float(b),float(c)) for a,b,c in zip(x,y,z)],dtype=np.float32) if fma=='fused' else x*y+z
    else:raise ExecutionError('unimplemented operation '+name)
    return np.asarray(v,dtype=np.float32).view(np.uint32)

def execute_np(bytecode=None, *, program=None, num_instances, constant_tables=(), datasets=None,
             initial_registers=None, start_instance=0, external_functions=(), profile='pioneer-2.1.0',
             math_backend='reference', svml_shim_path=None, fma_mode='unfused', external_handler=None,
             id_states=None, rng_seed=None, max_instances=4_000_000, max_instructions=1_000_000):
    def bounded_int(value, minimum, maximum, label):
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ExecutionError(f'invalid {label}: {value!r}')
        return value

    bounded_int(num_instances, 0, max_instances, 'num_instances')
    bounded_int(start_instance, 0, 0x7fffffff - num_instances, 'start_instance')
    max_storage_words=10**12
    if len(bytecode or b'') > max_storage_words * 4:
        raise ExecutionError('bytecode storage budget exceeded')
    if program is None: program = vm_disassemble(bytecode, external_functions, profile=profile)
    if len(program.instructions) > max_instructions:
        raise ExecutionError('instruction/lane work budget exceeded')
    for ins in program.instructions:
        if ins.name not in SUPPORTED_OPS_57:
            raise ExecutionError(f'byte {ins.offset}: unsupported operation {ins.name}')

    if math_backend not in ('reference', 'svml_shim') or fma_mode not in ('unfused', 'fused'):
        raise ExecutionError('invalid math backend or fma mode')
    if math_backend == 'svml_shim' and svml_shim_path is None:
        raise ExecutionError('svml_shim requires svml_shim_path')
    names = {i.name for i in program.instructions}
    if 'random' in names and rng_seed is None:
        raise ExecutionError('random requires rng_seed')
    if 'external_func_call' in names:
        if external_handler != 'gate_v_stub' and not callable(external_handler):
            raise ExecutionError('external_func_call requires gate_v_stub or a handler')
        if not isinstance(external_functions, list):
            raise ExecutionError('external_functions must be a list of signature dicts')
    for ins in program.instructions:
        if ins.name in ('acquire_id', 'update_id') and ins.operands[0].value not in (id_states or {}):
            raise ExecutionError('ID operation requires id_states')
    working_ids = {d: IdState(list(s.id_table), list(s.free_ids), s.num_free_ids,
        s.free_capacity, s.max_used_id, s.num_spawned_ids, list(s.spawned_ids), s.id_acquire_tag)
        for d, s in (id_states or {}).items()}
    taints, output_taints, dataset_taints, ext_calls = {}, {}, {}, []
    rng = rng_seed_state(*rng_seed) if rng_seed is not None else None
    tables = tuple(bytes(table) for table in constant_tables)
    if sum(map(len, tables)) > max_storage_words * 4:
        raise ExecutionError('constant storage budget exceeded')
    datasets = dict(datasets or {})
    registers = {}
    outputs = {}
    written = {}
    counters = {}
    storage = 0

    def allocate(words):
        nonlocal storage
        storage += words
        if storage > max_storage_words:
            raise ExecutionError('register/output storage budget exceeded')

    for index, ds in datasets.items():
        bounded_int(index, 0, 65535, 'dataset index')
        bounded_int(ds.instance_offset, 0, max_storage_words, 'instance offset')
        bounded_int(ds.access_index, -1, 0x7ffffffe, 'access index')
        counters[index] = ds.access_index
        for key, capacity in ds.output_capacities.items():
            if not isinstance(key, tuple) or len(key) != 2 or key[0] not in ('float', 'int32'):
                raise ExecutionError('invalid output attribute key')
            bounded_int(key[1], 0, 65535, 'output attribute')
            bounded_int(capacity, 0, max_storage_words, 'output capacity')
            allocate(capacity)
            outputs[index, key] = np.full(capacity,0xdeadbeef,dtype=np.uint32)
            written[index,key] = np.zeros(capacity,dtype=bool)
    for index, values in (initial_registers or {}).items():
        bounded_int(index, 0, 65535, 'register index')
        if len(values) != num_instances:
            raise ExecutionError('initial register lane count mismatch')
        allocate(num_instances)
        registers[index] = np.array([bounded_int(int(v), 0, 0xffffffff, 'raw register word') for v in values],dtype=np.uint32)

    def store(index, values, taint=frozenset()):
        if index not in registers:
            allocate(num_instances)
        if isinstance(values, np.ndarray) and values.ndim == 1 and values.dtype.kind in 'biu':
            registers[index] = (values.astype(np.int64) & 0xffffffff).astype(np.uint32)
        else:
            registers[index] = np.asarray([int(v) & 0xffffffff for v in values],dtype=np.uint32)
        taints[index] = frozenset(taint)

    def source(operand):
        if operand.kind == 'constant':
            offset = operand.value
            for table in tables:
                if offset < len(table):
                    if offset + 4 > len(table):
                        raise ExecutionError('constant crosses table boundary')
                    word = struct.unpack_from('<I', table, offset)[0]
                    return np.full(num_instances,word,dtype=np.uint32)
                offset -= len(table)
            raise ExecutionError(f'missing constant byte offset {operand.value}')
        if operand.value not in registers:
            raise ExecutionError(f'uninitialized register {operand.value}')
        return registers[operand.value]

    def source_taint(operand):
        return taints.get(operand.value, frozenset()) if operand.kind != 'constant' else frozenset()

    for ins in program.instructions:
        name = ins.name
        op = ins.operands
        if name == 'done':
            break
        if name in ('enter_stat_scope', 'exit_stat_scope'):
            continue  # Profiling only; no simulation state.
        try:
            if name == 'exec_index':
                store(op[0].value, np.arange(start_instance, start_instance + num_instances, dtype=np.int64))
            elif name.startswith('inputdata_'):
                ds = datasets[op[0].value]
                kind = 'int32' if name.endswith('int32') else 'float'
                values = ds.inputs[kind, op[1].value]
                noadvance = 'noadvance' in name
                if isinstance(values, np.ndarray) and values.ndim == 1 and values.dtype == np.uint32 and ds.instance_offset + (1 if noadvance else num_instances) <= len(values):
                    selected = np.full(num_instances, values[ds.instance_offset], dtype=np.uint32) if noadvance else values[ds.instance_offset:ds.instance_offset + num_instances]
                else:
                    selected = [values[ds.instance_offset + (0 if noadvance else lane)] for lane in range(num_instances)]
                store(op[2].value, selected)
            elif name == 'acquireindex':
                index = op[0].value
                if index not in counters:
                    raise ExecutionError(f'missing dataset {index}')
                keep = (source(op[1]) & 0x80000000) != 0
                kept = int(np.count_nonzero(keep))
                if kept and counters[index] + kept - 1 >= 0x7ffffffe:
                    raise ExecutionError('dataset counter overflow')
                acquired = np.where(keep, counters[index] + np.cumsum(keep, dtype=np.int64), 0xffffffff)
                counters[index] += kept
                condition_taint = source_taint(op[1])
                store(op[2].value, acquired, condition_taint)
                dataset_taints[index] = dataset_taints.get(index, frozenset()) | condition_taint
            elif name.startswith('outputdata_'):
                index = op[0].value
                ds = datasets[index]
                kind = 'int32' if name.endswith('int32') else 'float'
                target = outputs[index, (kind, op[3].value)]
                indices = source(op[1])
                values = source(op[2])
                dest = indices.view(np.int32).astype(np.int64)
                live = dest != -1
                dest = dest[live] + ds.instance_offset
                if dest.size and (int(dest.min()) < ds.instance_offset or int(dest.max()) >= len(target)):
                    raise ExecutionError('output index outside provided capacity')
                rows, last = np.unique(dest[::-1], return_index=True)
                target[rows] = values[live][dest.size - 1 - last]
                written[index,(kind,op[3].value)][rows] = True
                key = (index, (kind, op[3].value))
                output_taints[key] = output_taints.get(key, frozenset()) | source_taint(op[1]) | source_taint(op[2])
            elif name == 'acquire_id':
                d, dest, tag = (o.value for o in op)
                state = working_ids[d]
                if state.num_free_ids < num_instances:
                    raise ExecutionError('insufficient free IDs')
                acquired = [state.free_ids[state.num_free_ids - 1 - i] for i in range(num_instances)]
                state.num_free_ids -= num_instances
                state.free_ids = state.free_ids[:state.num_free_ids]
                state.num_spawned_ids += num_instances
                state.spawned_ids = acquired + state.spawned_ids
                store(dest, acquired, dataset_taints.get(d, frozenset()))
                store(tag, [state.id_acquire_tag] * num_instances, dataset_taints.get(d, frozenset()))
            elif name == 'update_id':
                d, id_reg, index_reg = (o.value for o in op)
                state = working_ids[d]
                ids, indices = registers[id_reg], registers[index_reg]
                surviving = [i for i, w in enumerate(indices) if w != 0xffffffff]
                offset = datasets[d].instance_offset if d in datasets else 0
                for i in surviving:
                    state.id_table[int(ids[i])] = word_int(int(indices[i])) + offset
                dead = [int(ids[i]) for i, w in enumerate(indices) if w == 0xffffffff]
                state.free_ids += dead
                state.num_free_ids += len(dead)
                if surviving:
                    state.max_used_id = max(state.max_used_id, max(word_int(int(w)) for w in ids[:len(surviving)]))
                dataset_taints[d] = dataset_taints.get(d, frozenset()) | taints.get(id_reg, frozenset()) | taints.get(index_reg, frozenset())
            elif name == 'external_func_call':
                fn = op[0].value
                signature = external_functions[fn]
                ni, no = len(signature['InputParamLocations']), signature['NumOutputs']
                args, arg_taints = [], []
                for operand in op[1:1 + ni]:
                    w = operand.value
                    if w == 0xffff:
                        raise ExecutionError('invalid external input operand')
                    arg = Operand('register', w & 0x7fff) if w & 0x8000 else Operand('constant', w)
                    args.append(source(arg)); arg_taints.append(source_taint(arg))
                destinations = [o.value for o in op[1 + ni:]]
                if any(w != 0xffff and w & 0x8000 for w in destinations):
                    raise ExecutionError('invalid external output operand')
                if callable(external_handler):
                    outs = external_handler(fn, [np.asarray(values, dtype=np.uint32) for values in args], num_instances, start_instance)
                    if len(outs) != no or any(len(values) != num_instances for values in outs):
                        raise ExecutionError('external handler output shape')
                    combined = frozenset().union(*arg_taints)
                    for dest, values in zip(destinations, outs):
                        if dest != 0xffff: store(dest, np.asarray(values, dtype=np.uint32), combined)
                    ext_calls.append({'fn': fn, 'start': start_instance, 'count': num_instances,
                        'input_taints': [sorted(t) for t in arg_taints]})
                    continue
                result = [[] for _ in range(no)]; inputs = []
                for lane in range(num_instances):
                    h = (0x811c9dc5 ^ fn) & 0xffffffff
                    for values in args:
                        w = int(values[lane]); inputs.append(w); h = ((h ^ w) * 0x01000193) & 0xffffffff
                    for out_index in range(no):
                        hk = ((h ^ ((0x9e3779b9 + out_index) & 0xffffffff)) * 0x01000193) & 0xffffffff
                        result[out_index].append(float_word(word_float((hk >> 9) | 0x3f800000) - 1.0))
                combined = frozenset().union(*arg_taints)
                for dest, values in zip(destinations, result):
                    if dest != 0xffff: store(dest, values, combined)
                ext_calls.append({'fn': fn, 'start': start_instance, 'count': num_instances,
                    'inputs': inputs, 'input_taints': [sorted(t) for t in arg_taints]})
            else:
                sources = [source(o) for o in op[:-1]]
                own = ({'rng'} if name == 'random' else {'svml'} if name in _SVML57 else
                    {'hwest'} if name == 'rcp' else {'fma'} if name in ('mad', 'lerp') else set())
                result_taint = frozenset(own).union(*(source_taint(o) for o in op[:-1]))
                with np.errstate(all='ignore'):
                    result = _elementwise(name,sources,math_backend,svml_shim_path,fma_mode,rng,num_instances)
                store(op[-1].value, result, result_taint)
        except (KeyError, IndexError, TypeError, OverflowError) as exc:
            raise ExecutionError(f'byte {ins.offset} ({name}): invalid or missing execution input: {exc}') from exc
    return NpExecutionResult(registers,outputs,written,counters,len(program.instructions),taints,output_taints,dataset_taints,working_ids,ext_calls)

# ---- niagara_scheduler.py ----
# Offline per-tick cooked Niagara CPU scheduler (Wave N3).

class BakeError(ValueError):pass
@dataclass
class BakeInputs:
    ticks:int
    dt:float=1/60
    dt_list:tuple=None
    quality_level:int=3
    max_delta_time:float=.125
    random_seed_offset:int=0
    bake_seed:int=0
    owner_translation:tuple=(0.,0.,0.)
    owner_translation_list:tuple=None
    owner_rotation:tuple=(0.,0.,0.,1.)
    owner_rotation_list:tuple=None
    owner_actor_velocity:tuple=(0.,0.,0.)
    world_time0:float=0.
    lod_distance:float=0.
    max_lod_distance:float=3.4028234663852886e38
    deactivate_tick:int=None
    di_mode:str='none'
    di_unimplemented:str='error'
    di_fma_mode:str='unfused'
    vector_fields:str=None
    di_vf_fma_mode:str='unfused'
    di_vf_half_decode:str='fast'
    vm_backend:str='numpy'
    math_backend:str='svml_shim'
    svml_shim_path:str=None
    record_buffers:bool=False
    camera:tuple=None
    camera_list:tuple=None
    collision_mesh:str=None
    gpu_bindings:str=None
    scene_view_header:str=None
    gpu_shaders:str=None
    ground_height:float=None
    ground_base_color:tuple=(.16,.15,.11)
    parameter_overrides:dict=None

def f32(x):return word_float(float_word(float(x)))
def hexfloat(x):return format(float_word(x),'08X')
def flip_indices(cur_frame,params_valid):
    cur_frame^=1
    if cur_frame==1:params_valid=True
    return cur_frame,params_valid,cur_frame,1 if (params_valid^(cur_frame==1)) else 0

def allocate_ids(id_to_index,free,max_used,alloc):
    required=max(alloc,max_used+1);length=len(id_to_index);free=list(free)
    if required>length:free.extend(range(required-1,length-1,-1))
    return [-1]*max(required,length),free,-1

def delta_step(dt_in,max_delta_time):
    dt_in32=f32(dt_in);dt=min(dt_in32,f32(max_delta_time));return dt_in32,dt,f32(dt/dt_in32)
def _pack(kind,d):
    size,fields=LAYOUTS[kind];data=bytearray(size)
    for name,(offset,fmt,count) in fields.items():
        if name not in d:continue
        values=[d[name]] if count==1 else d[name]
        if fmt=='f':values=[f32(v) for v in values]
        else:values=[word_int(int(v)&0xffffffff) if fmt=='i' else int(v)&0xffffffff for v in values]
        struct.pack_into('<'+fmt*count,data,offset,*values)
    return bytes(data)
def pack_global(d):return _pack('global',d)
def pack_system(d):return _pack('system',d)
def pack_emitter(d):return _pack('emitter',d)
def pack_owner(d):return _pack('owner',d)
IDENTITY_QUAT=(0.,0.,0.,1.)
def quat_axes(q):
    """UE FQuat (x,y,z,w) -> rotated X/Y/Z axes as rows (FQuatRotationTranslationMatrix, row-vector convention)."""
    x,y,z,w=(float(v) for v in q);x2,y2,z2=x+x,y+y,z+z;xx,xy,xz,yy,yz,zz,wx,wy,wz=x*x2,x*y2,x*z2,y*y2,y*z2,z*z2,w*x2,w*y2,w*z2
    return ((1-(yy+zz),xy+wz,xz-wy),(xy-wz,1-(xx+zz),yz+wx),(xz+wy,yz-wx,1-(xx+yy)))
def owner_fields(location,velocity,tile=2097152,rotation=IDENTITY_QUAT):
    tx,ty,tz=location
    if tuple(rotation)==IDENTITY_QUAT:
        local=[1,0,0,0,0,1,0,0,0,0,1,0,tx,ty,tz,1];world=[1,0,0,0,0,1,0,0,0,0,1,0,-tx,-ty,-tz,1];axes=((1,0,0),(0,1,0),(0,0,1));rotation=(0,0,0,1)
    else:
        axes=quat_axes(rotation);r=np.array(axes,dtype=np.float64);t=np.array([tx,ty,tz],dtype=np.float64);m=np.eye(4);m[:3,:3]=r;m[3,:3]=t;inv=np.eye(4);inv[:3,:3]=r.T;inv[3,:3]=-(t@r.T)
        local=m.ravel().tolist();world=inv.ravel().tolist();rotation=tuple(float(v) for v in rotation)
    return dict(EngineLocalToWorld=local,EngineWorldToLocal=world,EngineLocalToWorldTransposed=np.array(local).reshape(4,4).T.ravel().tolist(),EngineWorldToLocalTransposed=np.array(world).reshape(4,4).T.ravel().tolist(),EngineLocalToWorldNoScale=local,EngineWorldToLocalNoScale=world,EngineRotation=rotation,EnginePosition=location,EngineVelocity=velocity,EngineXAxis=axes[0],EngineYAxis=axes[1],EngineZAxis=axes[2],EngineScale=(1,1,1),EngineLWCTile=(0,0,0,tile))
def plan_spawn_batches(spawn_infos,remaining,active):
    if not active:return []
    batches=[]
    for count,start,interval,group in spawn_infos:
        c=min(count,remaining)
        if c>0:batches.append((c,interval,start,group))
        remaining-=c
    return batches

def event_spawn_counts(n_events,max_events_per_frame,spawn_number,active):
    if not active or spawn_number<=0:return []
    n=min(n_events,max_events_per_frame) if max_events_per_frame>0 else n_events
    return [spawn_number]*n

def prev_copy(store):
    if store.interpolated:store.data[store.size:2*store.size]=store.data[:store.size]
def copy_by_name(src_entries,src_bytes,dst_store):
    for name,(offset,t) in src_entries.items():
        if name.startswith('PREV_') or name not in dst_store.entries:continue
        target,dt=dst_store.entries[name]
        if dt.key!=t.key or target>=dst_store.size:continue
        for b in t.float_offsets+t.int_offsets:
            dst_store.data[target+b:target+b+4]=src_bytes[offset+b:offset+b+4]
def row_to_store(var_layout,float_words,int_words,store):
    v=var_layout
    if v.name.startswith('PREV_') or v.name not in store.entries:return
    offset,t=store.entries[v.name]
    if t.key!=v.type.key or offset>=store.size:return
    for offsets,words,start in [(t.float_offsets,float_words,v.float_start),(t.int_offsets,int_words,v.int_start)]:
        for j,b in enumerate(offsets):struct.pack_into('<I',store.data,offset+b,int(words[start+j])&0xffffffff)
def set_named(store,name,words):
    if name not in store.entries or name.startswith('PREV_'):return
    offset,t=store.entries[name]
    if offset>=store.size:return
    for b,w in zip(sorted(t.float_offsets+t.int_offsets),words):struct.pack_into('<I',store.data,offset+b,int(w)&0xffffffff)
def override_words(value):
    vals=value if isinstance(value,(list,tuple)) else [value]
    return [v&0xffffffff if isinstance(v,int) and not isinstance(v,bool) else float_word(f32(float(v))) for v in vals]
def apply_overrides(store,overrides):
    """User./NPC. values from a placed component or parameter collection; interpolated stores get both halves."""
    for name,value in (overrides or {}).items():
        words=override_words(value);set_named(store,name,words)
        if store.interpolated and name in store.entries:
            offset,t=store.entries[name]
            for b,w in zip(sorted(t.float_offsets+t.int_offsets),words):struct.pack_into('<I',store.data,store.size+offset+b,int(w)&0xffffffff)
def _columns(layout,count=0):return {(kind,j):np.zeros(count,dtype=np.uint32) for kind,n in [('float',layout.total_float),('int32',layout.total_int)] for j in range(n)}
def _extend(columns,count):
    return {k:np.pad(v,(0,max(0,count-len(v))))[:count].copy() for k,v in columns.items()}
def _hash(text):return hashlib.sha256(text.encode('utf-8')).digest()
def rand31(text):return int.from_bytes(_hash(text)[:4],'little')&0x7fffffff

class Scheduler:
    def __init__(self,model,inputs):
        self.model=model;self.inputs=inputs
        if model.unsupported:raise BakeError('unsupported:'+','.join(model.unsupported))
        gpu=[e for e in model.emitters if e.enabled and e.sim_target!='CPUSim']
        if gpu and (inputs.gpu_bindings is None or inputs.gpu_shaders is None):raise BakeError('gpu_emitter')
        self.gpu_programs={}
        if gpu:
            pass
            self.gpu_device=Device(warp=True);doc=json.loads(Path(model.path).read_text(encoding='utf-8-sig'))
            try:gpu_vf=load_vector_fields(inputs.vector_fields) if inputs.vector_fields else {}
            except (OSError,ValueError,KeyError) as exc:raise BakeError('vector_fields:'+str(exc)) from exc
            for e in gpu:
                try:self.gpu_programs[e.index]=GpuEmitterProgram(self.gpu_device,doc,doc[e.gpu.emitter_index],e.gpu.script_index,inputs.gpu_bindings,inputs.gpu_shaders,Path(model.path).stem,gpu_vf,inputs.ground_height)
                except GpuError as exc:raise BakeError(str(exc)) from exc
        if inputs.dt_list is not None and len(inputs.dt_list)!=inputs.ticks:raise BakeError('dt_list_length')
        if inputs.owner_translation_list is not None and len(inputs.owner_translation_list)!=inputs.ticks:raise BakeError('owner_translation_list_length')
        if inputs.owner_rotation_list is not None and len(inputs.owner_rotation_list)!=inputs.ticks:raise BakeError('owner_rotation_list_length')
        if any(len(q)!=4 or not abs(sum(float(v)*float(v) for v in q)-1)<1e-3 for q in [inputs.owner_rotation]+list(inputs.owner_rotation_list or ())):raise BakeError('owner_rotation')
        if any(not float(dt)>0 for dt in (inputs.dt_list if inputs.dt_list is not None else [inputs.dt])):raise BakeError('dt')
        if any(not abs(float(x))<1048576 for loc in [inputs.owner_translation]+list(inputs.owner_translation_list or ()) for x in loc):raise BakeError('owner_translation')
        if inputs.ground_height is not None and not abs(float(inputs.ground_height))<1048576:raise BakeError('ground_height')
        if inputs.math_backend=='svml_shim' and inputs.svml_shim_path is None:raise BakeError('svml_shim_path')
        if inputs.di_mode not in ('none','stub','real'):raise BakeError('di_mode')
        self.di_handlers={};self.di_unimplemented=[];self.di_state=DIInstanceState()
        if inputs.camera_list is not None and len(inputs.camera_list)!=inputs.ticks:raise BakeError('camera_list_length')
        if inputs.collision_mesh:
            try:self.di_state.collision=CollisionWorld.load(inputs.collision_mesh)
            except (OSError,ValueError,KeyError) as exc:raise BakeError('collision_mesh:'+str(exc)) from exc
        if inputs.di_mode=='real':
            try:vector_fields=load_vector_fields(inputs.vector_fields) if inputs.vector_fields else {}
            except (OSError,ValueError,KeyError) as exc:raise BakeError('vector_fields:'+str(exc)) from exc
            exports=exports_from_document(json.loads(Path(model.path).read_text(encoding='utf-8-sig')))
            scripts=[model.system_spawn,model.system_update]+[s for e in model.emitters if e.enabled for s in [e.spawn,e.update]+[h.script for h in e.event_handlers if not h.dead]]
            for script in scripts:
                if script is None or not script.has_ext or script.export_index in self.di_handlers:continue
                try:handler=script_handler(exports,bind_script(exports,script.export_index),fma_mode=inputs.di_fma_mode,unimplemented=inputs.di_unimplemented,state=self.di_state,vector_fields=vector_fields,vf_fma_mode=inputs.di_vf_fma_mode,vf_half_decode=inputs.di_vf_half_decode)
                except DIError as exc:raise BakeError(str(exc)) from exc
                self.di_handlers[script.export_index]=handler;self.di_unimplemented=sorted(set(self.di_unimplemented)|set(handler.unimplemented_functions))
        self.records=[];self.activated=False
    def _particle_source(self,r):
        return lambda:(r.count,r.model.particle_layout,r.buffer,r.id_to_index,getattr(r.model,'gpu',None) is not None) if r.model.enabled else None
    def activate(self):
        m,a=self.model,self.inputs;self.age=0.;self.tick_count=0;self.global_tick=0;self.normal_tick=0;self.actual=0;self.requested=0;self.prev_location=None;self.world_time=a.world_time0;self.cur_frame=1;self.params_valid=False;self.pending=True
        self.random_seed=m.random_seed if m.determinism else rand31(f'{a.bake_seed}|{m.package}|system_seed')
        self.global_params=[{},{}];self.system_params=[{},{}];self.owner_params=[owner_fields((0,0,0),(0,0,0),0),owner_fields((0,0,0),(0,0,0),0)];self.emitter_params=[[{'EmitterSpawnCountScale':1.},{'EmitterSpawnCountScale':1.}] for e in m.emitters];self.instance_store=copy.deepcopy(m.instance_params);self.system_stores={key:copy.deepcopy(s.param_store) for key,s in [('spawn',m.system_spawn),('update',m.system_update)]};self.main=_columns(m.main_layout,1);self.events={};self.emitters=[]
        for e in m.emitters:
            r=SimpleNamespace(model=e,state=0 if e.enabled else 4,age=0.,tick_count=0,total=0,count=0,buffer={},id_to_index=[],free=[],max_used=-1,max_alloc=0,estimate=0,spawn_infos=[],stores={},spawned=0,killed_update=0,killed_spawn=0,event_spawned=0,orig=0,spawn_total=0,event_requested=0,alloc=0)
            if e.enabled and getattr(e,'gpu',None) is not None:
                pass
                r.buffer=_columns(e.particle_layout);r.stores={'gpu':copy.deepcopy(e.gpu.param_store)};r.gpu_reset=True;r.gpu_ids=GpuIdState() if e.requires_persistent_ids else None
                r.random_seed=word_int((e.random_seed+a.random_seed_offset)&0xffffffff);r.instance_seed=rand31(f'{a.bake_seed}|{m.package}|instance_seed|{e.index}|0')
            elif e.enabled:
                r.buffer=_columns(e.particle_layout);r.stores={'spawn':copy.deepcopy(e.spawn.param_store),'update':copy.deepcopy(e.update.param_store)}
                for j,h in enumerate(e.event_handlers):
                    if not h.dead:r.stores['event'+str(j)]=copy.deepcopy(h.script.param_store)
                r.random_seed=word_int((e.random_seed+a.random_seed_offset)&0xffffffff);r.instance_seed=rand31(f'{a.bake_seed}|{m.package}|instance_seed|{e.index}|0')
                for g in e.spawn_generators+e.update_generators:self.events[e.id_name,g.event_id]=SimpleNamespace(layout=g.layout,columns=_columns(g.layout),count=0)
            self.emitters.append(r)
        for store in [self.instance_store,*self.system_stores.values(),*(st for r in self.emitters for st in r.stores.values())]:apply_overrides(store,a.parameter_overrides)
        for r in self.emitters:
            for key in {r.model.unique_name,r.model.handle_name}:
                if key:self.di_state.particle_sources[key]=self._particle_source(r)
        self.records=[];self.activated=True
        if m.warmup_tick_count>0 and m.warmup_tick_delta>1e-8:
            for _ in range(m.warmup_tick_count):self._tick(m.warmup_tick_delta,a.owner_translation,'warmup',a.owner_rotation)
        self.age=0.;self.tick_count=0;self.activation_state={'age':self.age,'tick_count':self.tick_count}
        return self
    def _rng(self,scope,usage,event,batch):
        h=_hash(f'{self.inputs.bake_seed}|{self.model.package}|{scope}|{usage}|{event}|{self.global_tick}|{batch}')
        return int.from_bytes(h[:8],'little'),int.from_bytes(h[8:16],'little')|1
    def _vm(self,script,n,datasets,tables,scope,usage,event=-1,batch=0,ids=None):
        if script.has_ext and self.inputs.di_mode=='none':raise BakeError('external_func_call')
        kw=dict(num_instances=n,constant_tables=tables,external_functions=script.ext_functions,profile='pioneer-2.1.0',math_backend=self.inputs.math_backend,svml_shim_path=self.inputs.svml_shim_path,fma_mode='unfused',external_handler='gate_v_stub' if self.inputs.di_mode=='stub' else self.di_handlers.get(script.export_index) if self.inputs.di_mode=='real' else None,id_states=ids)
        if any(i.name=='random' for i in script.program.instructions):kw['rng_seed']=self._rng(scope,usage,event,batch)
        if self.inputs.vm_backend=='numpy':return execute_np(program=script.program,datasets=datasets,**kw)
        scalar={d:Dataset(inputs={k:[word_float(int(w)) if k[0]=='float' else word_int(int(w)) for w in v] for k,v in s.inputs.items()},output_capacities=s.output_capacities,instance_offset=s.instance_offset,access_index=s.access_index) for d,s in datasets.items()}
        r=execute(script.bytecode,datasets=scalar,x86_semantics=True,max_instances=4_000_000,max_instructions=1_000_000,max_lane_operations=10**12,max_storage_words=10**12,**kw);written={};out={}
        for key,v in r.outputs.items():written[key]=np.array([x is not None for x in v]);out[key]=np.array([0xdeadbeef if x is None else float_word(x) if key[1][0]=='float' else int(x)&0xffffffff for x in v],dtype=np.uint32)
        r.outputs=out;r.written=written;return r
    def _params_row(self,layout,phase):
        row=_columns(layout,1)
        for v in layout.variables:
            if v.name not in self.instance_store.entries:continue
            offset,t=self.instance_store.entries[v.name]
            if t.key!=v.type.key or v.name.startswith('PREV_'):continue
            for kind,offsets,start in [('float',t.float_offsets,v.float_start),('int32',t.int_offsets,v.int_start)]:
                for j,b in enumerate(offsets):row[kind,start+j][0]=struct.unpack_from('<I',self.instance_store.data,offset+b)[0]
        for group,packed in [('global',pack_global(self.global_params[self.cur])),('system',pack_system(self.system_params[self.cur])),('owner',pack_owner(self.owner_params[self.cur]))]+[('emitter',pack_emitter(v[self.cur])) for v in self.emitter_params]:
            if group=='emitter':continue
            for kind,offset,comp in self.model.bindings.get((phase,group),()):row[kind,comp][0]=struct.unpack_from('<I',packed,offset)[0]
        for i,values in enumerate(self.emitter_params):
            packed=pack_emitter(values[self.cur])
            for kind,offset,comp in self.model.bindings.get((phase,'emitter',i),()):row[kind,comp][0]=struct.unpack_from('<I',packed,offset)[0]
        return row
    def _system(self,phase,row,usage,global_override=None):
        script=self.model.system_spawn if phase=='spawn' else self.model.system_update;store=self.system_stores[phase];ds={0:NpDataset(self.main,{k:1 for k in self.main}),1:NpDataset(row,{})};r=self._vm(script,1,ds,[pack_global(global_override or self.global_params[self.cur]),bytes(store.data[:store.size]),script.literals],'system',usage)
        for key,words in self.main.items():
            if r.written[0,key][0]:words[0]=r.outputs[0,key][0]
            else:
                name=next(v.name for v in self.model.main_layout.variables if (v.float_start<=key[1]<v.float_start+len(v.type.float_offsets) if key[0]=='float' else v.int_start<=key[1]<v.int_start+len(v.type.int_offsets)))
                self.diagnostics.append('system_unwritten:'+name)
    def _row_value(self,name):
        v=self.model.main_layout.find(name)
        if v is None:return None
        f=[word_float(int(self.main['float',v.float_start+j][0])) for j in range(len(v.type.float_offsets))];i=[word_int(int(self.main['int32',v.int_start+j][0])) for j in range(len(v.type.int_offsets))];return f,i
    def gpu_view_words(self):
        """View UB constants (5.7 layout) for the current DI camera (spec N-O4 section 4); cached per camera value."""
        pass
        cam=self.di_state.camera
        if getattr(self,'_view_key',object())!=cam:
            vb=ViewBuffer(self.inputs.scene_view_header or DEFAULT_HEADER);camera_view(vb,cam)
            self._view_key=cam;self._view_words=vb.words.tobytes()
        return self._view_words
    def gpu_scene(self):
        """SceneTexturesStruct texels of the synthetic flat ground (BakeInputs.ground_height) seen by the current DI camera; None (empty-scene
        fallbacks) without a ground. Cached per camera value, like the View UB."""
        pass
        if self.inputs.ground_height is None:return None
        cam=self.di_state.camera
        if getattr(self,'_scene_key',object())!=cam:
            self._scene_key=cam;self._scene=flat_ground_scene(cam,self.inputs.ground_height,self.inputs.ground_base_color)
        return self._scene
    def _emitter_tick_gpu(self,r,dt):
        """Spec N-O4: one spawn+update dispatch of the emitter's own compute shader (WARP); counts and buffers read back."""
        pass
        e=r.model;prog=self.gpu_programs[e.index];r.orig=r.count
        r.spawn_total=sum(max(v[0],0) for v in r.spawn_infos) if r.state==0 else 0;required=r.orig+r.spawn_total
        estimate=e.pre_allocation_count if e.allocation_mode in ['ManualEstimate','FixedCount'] else r.estimate;r.alloc=max(estimate,required)
        if r.alloc>r.max_alloc:r.max_alloc=r.alloc;r.estimate=r.max_alloc
        params=self.emitter_params[e.index][self.cur];params.update(EmitterNumParticles=r.count,EmitterTotalSpawnedParticles=r.total,EmitterAge=r.age,EmitterRandomSeed=r.random_seed,EmitterInstanceSeed=r.instance_seed)
        i=e.index;blocks={'Global':(pack_global(self.global_params[self.cur]),pack_global(self.global_params[self.prev])),'System':(pack_system(self.system_params[self.cur]),pack_system(self.system_params[self.prev])),
                          'Owner':(pack_owner(self.owner_params[self.cur]),pack_owner(self.owner_params[self.prev])),'Emitter':(pack_emitter(self.emitter_params[i][self.cur]),pack_emitter(self.emitter_params[i][self.prev]))}
        lay=e.particle_layout;store=r.stores['gpu']
        ub={'FNiagaraExternalParameters':bytes(store.data[:store.size]),'PREV_FNiagaraExternalParameters':bytes(store.data[store.size:2*store.size]) if store.interpolated else bytes(store.data[:store.size]),
            'View':self.gpu_view_words()}
        try:count,spawned,fcols,icols=dispatch_tick(prog,lay,{j:r.buffer['float',j][:r.count] for j in range(lay.total_float)},{j:r.buffer['int32',j][:r.count] for j in range(lay.total_int)},
                                                                r.count,r.alloc,r.spawn_infos,r.state==0,r.gpu_reset,self.global_tick,blocks,r.gpu_ids,ub,self._gpu_read_source,
                                                                self.gpu_scene() if prog.scene_resources else None)
        except GpuError as exc:raise BakeError(str(exc)) from exc
        r.gpu_reset=False;r.gpu_tag=self.global_tick;r.spawned=spawned;r.total=word_int((r.total+spawned)&0xffffffff);r.killed_update=r.orig+spawned-count;r.count=count
        r.buffer={**{('float',j):fcols[j] for j in range(lay.total_float)},**{('int32',j):icols[j] for j in range(lay.total_int)}}
    def _gpu_read_source(self,name):
        """GPU ParticleRead source by emitter name: its current data (ticked earlier this frame by execution order) or None."""
        r=next((x for x in self.emitters if name and x.model.enabled and name in (x.model.unique_name,x.model.handle_name)),None)
        if r is None:return None
        e=r.model;gpu=getattr(e,'gpu',None) is not None
        return SimpleNamespace(gpu=gpu,local_space=e.local_space,count=r.count,layout=e.particle_layout,buffer=r.buffer,ids=getattr(r,'gpu_ids',None),
                               spawned=r.spawned,acquire_tag=getattr(r,'gpu_tag',0))
    def _tables(self,r,script,store):
        i=r.model.index;tables=[pack_global(self.global_params[self.cur]),pack_system(self.system_params[self.cur]),pack_owner(self.owner_params[self.cur]),pack_emitter(self.emitter_params[i][self.cur]),bytes(store.data[:store.size])]
        if store.interpolated:tables += [pack_global(self.global_params[self.prev]),pack_system(self.system_params[self.prev]),pack_owner(self.owner_params[self.prev]),pack_emitter(self.emitter_params[i][self.prev]),bytes(store.data[store.size:2*store.size])]
        return tables+[script.literals]
    def _ids(self,r):
        return {0:IdState(list(r.id_to_index),list(r.free),len(r.free),len(r.free)+r.alloc+64,r.max_used,0,[],r.tick_count)} if r.model.requires_persistent_ids else None
    def _read_ids(self,r,result):
        if r.model.requires_persistent_ids:
            s=result.id_states[0];r.id_to_index=s.id_table;r.free=s.free_ids;r.max_used=s.max_used_id
    def _clear(self,r):
        r.count=0;r.buffer=_columns(r.model.particle_layout) if r.model.enabled else {};r.id_to_index=[];r.free=[];r.max_used=-1
        if r.model.enabled:
            for g in r.model.spawn_generators+r.model.update_generators:
                self.events[r.model.id_name,g.event_id]=SimpleNamespace(layout=g.layout,columns=_columns(g.layout),count=0)
    def _particle_result(self,r,result,start,count,base):
        out={k:v.copy() for k,v in base.items()}
        for k in base:
            out[k][start:start+count]=result.outputs[0,k][start:start+count]
            if not np.all(result.written[0,k][start:start+count]):self.diagnostics.append('particle_unwritten')
        return out
    def _generator_sets(self,r,generators,capacity):
        ds={}
        for j,g in enumerate(generators):
            ev=self.events[r.model.id_name,g.event_id];cap=capacity(g);ds[j+1]=NpDataset(ev.columns,{k:cap for k in ev.columns},instance_offset=ev.count)
        return ds
    def _read_generators(self,r,generators,ds,result):
        for j,g in enumerate(generators):
            ev=self.events[r.model.id_name,g.event_id];d=j+1;offset=ds[d].instance_offset;count=offset+result.access_indices[d]+1;columns=_extend(ev.columns,max(count,len(next(iter(result.outputs.values()),[]))))
            for k in ev.columns:
                arr=result.outputs[d,k];columns[k]=_extend({k:ev.columns[k]},len(arr))[k];mask=result.written[d,k];columns[k][mask]=arr[mask];columns[k]=columns[k][:count]
            ev.columns=columns;ev.count=count
    def _emitter_tick(self,r,dt):
        e=r.model
        if r.state==3:self._clear(r);return
        if r.state==4:return
        r.age=f32(r.age+dt)
        if r.state==2:self._clear(r);r.state=1;return
        if r.count==0 and r.state!=0:self._clear(r);return
        if getattr(e,'gpu',None) is not None:return self._emitter_tick_gpu(r,dt)
        counts=[]
        for h in e.event_handlers:
            if h.dead:counts.append([]);continue
            if h.random_spawn_number:raise BakeError('random_spawn_number')
            source=next(x for x in self.model.emitters if x.handle_id==h.source_emitter_id);ev=self.events.get((source.id_name,h.source_event_name));counts.append(event_spawn_counts(ev.count if ev else 0,h.max_events_per_frame,h.spawn_number,r.state==0))
        r.spawn_total=sum(max(v[0],0) for v in r.spawn_infos) if r.state==0 else 0;r.orig=r.count;r.event_requested=sum(sum(v) for v in counts);required=r.orig+r.spawn_total+r.event_requested;estimate=e.pre_allocation_count if e.allocation_mode in ['ManualEstimate','FixedCount'] else r.estimate;r.alloc=max(estimate,required)
        if r.alloc>r.max_alloc:r.max_alloc=r.alloc;r.estimate=r.max_alloc
        if r.alloc>1_000_000:r.estimate=0;r.alloc=r.orig;r.spawn_total=0;r.event_requested=0;counts=[[] for _ in counts]
        params=self.emitter_params[e.index][self.cur];params.update(EmitterTotalSpawnedParticles=r.total,EmitterAge=r.age,EmitterRandomSeed=r.random_seed,EmitterInstanceSeed=r.instance_seed)
        if e.requires_persistent_ids:r.id_to_index,r.free,r.max_used=allocate_ids(r.id_to_index,r.free,r.max_used,r.alloc)
        for g in e.spawn_generators+e.update_generators:self.events[e.id_name,g.event_id]=SimpleNamespace(layout=g.layout,columns=_columns(g.layout),count=0)
        old={k:v.copy() for k,v in r.buffer.items()};buffer=_extend(old,r.alloc)
        if r.orig>0:
            store=r.stores['update'];set_named(store,'Engine.ExecutionCount',[r.orig]);ds={0:NpDataset(old,{k:r.orig for k in old})};ds.update(self._generator_sets(r,e.update_generators,lambda g:r.orig+r.spawn_total+r.event_requested if g.shared else r.orig));result=self._vm(e.update,r.orig,ds,self._tables(r,e.update,store),e.index,'ParticleUpdate',ids=self._ids(r));r.count=result.access_indices[0]+1;r.killed_update=r.orig-r.count;buffer=self._particle_result(r,result,0,r.count,buffer);self._read_ids(r,result);self._read_generators(r,e.update_generators,ds,result)
        remaining=r.alloc-r.orig;batch=0
        def spawn(c,interval,start_dt,group):
            nonlocal buffer,batch
            store=r.stores['spawn'];set_named(store,e.names['spawn_interval'],[float_word(interval)]);set_named(store,e.names['interp_spawn_start_dt'],[float_word(start_dt)]);set_named(store,e.names['spawn_group'],[group]);set_named(store,'Engine.ExecutionCount',[c]);params['EmitterTotalSpawnedParticles']=r.total;r.total=word_int((r.total+c)&0xffffffff);start=r.count;ds={0:NpDataset(_extend(old,r.alloc),{k:r.alloc for k in old},instance_offset=start)};ds.update(self._generator_sets(r,e.spawn_generators,lambda g:r.alloc if g.shared else r.spawn_total+r.event_requested));result=self._vm(e.spawn,c,ds,self._tables(r,e.spawn,store),e.index,'ParticleSpawn',batch=batch,ids=self._ids(r));batch+=1;actual=result.access_indices[0]+1;buffer=self._particle_result(r,result,start,actual,buffer);r.count+=actual;r.killed_spawn+=c-actual;self._read_ids(r,result);self._read_generators(r,e.spawn_generators,ds,result);return actual
        if r.spawn_total+r.event_requested>0:
            batches=plan_spawn_batches(r.spawn_infos,remaining,r.state==0)
            for c,interval,start_dt,group in batches:r.spawned+=spawn(c,interval,start_dt,group)
            # Remaining follows the planner even for non-positive Count entries.
            for count,_,_,_ in r.spawn_infos:remaining-=min(count,remaining)
            event_start=r.count
            for j,per_event in enumerate(counts):
                for k,n in enumerate(per_event):
                    c=min(n,remaining);actual=spawn(c,0,f32(dt*.5),0) if c>0 else 0;counts[j][k]=actual;remaining-=actual;r.event_spawned+=actual
            if sum(sum(v) for v in counts)>0:
                source_buffer={k:v.copy() for k,v in buffer.items()};dest={k:v.copy() for k,v in buffer.items()};r.count=event_start
                for j,h in enumerate(e.event_handlers):
                    if h.dead:continue
                    source=next(x for x in self.model.emitters if x.handle_id==h.source_emitter_id);ev=self.events[source.id_name,h.source_event_name]
                    for k,n in enumerate(counts[j]):
                        if n<=0:continue
                        store=r.stores['event'+str(j)];set_named(store,'Engine.ExecutionCount',[n]);ds={0:NpDataset(source_buffer,{key:r.alloc for key in source_buffer},instance_offset=event_start),1:NpDataset(ev.columns,{},instance_offset=k)};result=self._vm(h.script,n,ds,self._tables(r,h.script,store),e.index,'ParticleEvent',event=j,batch=k,ids=self._ids(r));actual=result.access_indices[0]+1;dest=self._particle_result(r,result,event_start,actual,dest);r.count=event_start+actual;event_start=r.count;self._read_ids(r,result)
                buffer=dest
        r.buffer={k:v[:r.count].copy() for k,v in buffer.items()}
        for store in r.stores.values():prev_copy(store)
    def _tick(self,dt_in,loc,phase,rot=IDENTITY_QUAT):
        a=self.inputs;self.di_state.camera=a.camera_list[self.normal_tick-1] if (phase=='normal' and a.camera_list is not None) else a.camera
        self.di_state.pre_tick();record=self._tick_body(dt_in,loc,phase,rot);self.di_state.post_tick();return record
    def _tick_body(self,dt_in,loc,phase,rot=IDENTITY_QUAT):
        self.global_tick+=1;self.diagnostics=[]
        for r in self.emitters:r.spawned=r.killed_update=r.killed_spawn=r.event_spawned=r.spawn_total=r.event_requested=0;r.orig=r.count
        if self.actual==3:return self._record(phase,dt_in,None)
        dt_in32,dt,fraction=delta_step(dt_in,self.inputs.max_delta_time)
        velocity=(0,0,0) if abs(self.age)<=1e-8 and not self.model.initial_owner_velocity_from_actor else tuple((x-y)/dt for x,y in zip(loc,self.prev_location)) if self.prev_location is not None else self.inputs.owner_actor_velocity;self.prev_location=loc
        self.cur_frame,self.params_valid,self.cur,self.prev=flip_indices(self.cur_frame,self.params_valid);sys=self.system_params[self.cur];sys.update(EngineSystemAge=self.age,EngineTickCount=self.tick_count,EngineTimeSinceRendered=0,EngineExecutionState=self.requested,EngineLodDistance=self.inputs.lod_distance,EngineLodDistanceFraction=f32(f32(self.inputs.lod_distance)/f32(self.inputs.max_lod_distance)),SignificanceIndex=0,RandomSeed=word_int((self.random_seed+self.inputs.random_seed_offset)&0xffffffff),CurrentTimeStep=0,NumTimeSteps=1,TimeStepFraction=fraction,NumParticles=sum(r.count for r in self.emitters if r.state!=4))
        alive=0
        for r in self.emitters:
            p=self.emitter_params[r.model.index][self.cur]
            if r.state!=4:p.update(EmitterNumParticles=r.count,EmitterTotalSpawnedParticles=r.total,EmitterRandomSeed=r.random_seed,EmitterInstanceSeed=r.instance_seed,EmitterSpawnCountScale=1.);alive+=1
            else:p['EmitterNumParticles']=0
        self.age=f32(self.age+dt);self.tick_count+=1;self.owner_params[self.cur]=owner_fields(loc,velocity,rotation=rot);sys.update(EngineEmitterCount=len(self.emitters),EngineAliveEmitterCount=alive)
        if phase=='normal':self.world_time+=dt_in32
        self.global_params[self.cur]=dict(WorldDeltaTime=dt_in32,EngineDeltaTime=dt,EngineInvDeltaTime=f32(1/dt),EngineTime=f32(self.world_time),EngineRealTime=f32(self.world_time),QualityLevel=self.inputs.quality_level)
        for r in self.emitters:
            if not r.model.enabled:continue
            for store in r.stores.values():copy_by_name(self.instance_store.entries,self.instance_store.data,store)
        spawn_row=self._params_row(self.model.spawn_params_layout,'spawn');update_row=self._params_row(self.model.update_params_layout,'update')
        if self.pending:
            self._system('spawn',spawn_row,'SystemSpawn');override=dict(self.global_params[self.cur],EngineDeltaTime=.0001,EngineInvDeltaTime=10000);self._system('update',update_row,'SystemUpdateOnSpawn',override);self.pending=False
        else:self._system('update',update_row,'SystemUpdate')
        previous=self.actual;self.actual=self._row_value('System.ExecutionState')[1][0]
        if self.actual!=previous and previous!=4 and self.actual==0:
            for r in self.emitters:
                if r.state!=4:r.state=0
        if self.actual!=4:
            main_f=[self.main['float',j][0] for j in range(self.model.main_layout.total_float)];main_i=[self.main['int32',j][0] for j in range(self.model.main_layout.total_int)]
            for r in self.emitters:
                if r.state in [3,4]:continue
                value=self._row_value(r.model.names['execution_state']);state=value[1][0] if value is not None else 4;r.state=state if state in range(5) else 1;r.spawn_infos=[]
                for name in r.model.spawn_attributes:
                    f,i=self._row_value(name);r.spawn_infos.append((i[0],f[0],f[1],i[1]))
                for store in r.stores.values():
                    for v in self.model.main_layout.variables:row_to_store(v,main_f,main_i,store)
        ticking=[self.emitters[i] for i in self.model.execution_order if self.emitters[i].state==0 or self.emitters[i].count>0]
        if dt>=1e-8:
            for r in ticking:
                if r.tick_count==0:
                    for store in r.stores.values():prev_copy(store)
                r.tick_count+=1
            for r in ticking:self._emitter_tick(r,dt)
        for r in self.emitters:
            if r.state==3:self._clear(r)
        if self.actual==3 or all(r.state in [3,4] for r in self.emitters):
            self.actual=3
            for r in self.emitters:
                if r.state!=4:r.state=3
                self._clear(r)
        return self._record(phase,dt_in32,dt)
    def _record(self,phase,dt_in,dt):
        record=dict(tick=self.global_tick,phase=phase,dt_in=hexfloat(dt_in),dt=hexfloat(dt) if dt is not None else None,system=dict(age=hexfloat(self.age),tick_count=self.tick_count,requested=self.requested,actual=self.actual),emitters=[dict(index=r.model.index,state=r.state,age=hexfloat(r.age),tick_count=r.tick_count,count=r.count,total_spawned=r.total,spawned=r.spawned,killed_update=r.killed_update,killed_spawn=r.killed_spawn,event_spawned=r.event_spawned,alloc=r.alloc,id_table_len=len(r.id_to_index),num_free=len(r.free)) for r in self.emitters],diagnostics=self.diagnostics);self.records.append(record);return record
    def tick(self):
        if not self.activated:self.activate()
        self.normal_tick+=1;a=self.inputs;t=self.normal_tick;self.requested=1 if a.deactivate_tick is not None and t>=a.deactivate_tick else 0;dt=a.dt_list[t-1] if a.dt_list is not None else a.dt;loc=a.owner_translation_list[t-1] if a.owner_translation_list is not None else a.owner_translation;rot=a.owner_rotation_list[t-1] if a.owner_rotation_list is not None else a.owner_rotation;return self._tick(dt,loc,'normal',rot)
    def run(self):
        if not self.activated:self.activate()
        while self.normal_tick<self.inputs.ticks:self.tick()
        return self.records

def save_bake(scheduler,out_dir):
    s=scheduler;out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    def write(name,v):(out/name).write_text(json.dumps(v,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    write('manifest.json',dict(inputs=asdict(s.inputs),package=s.model.package,model=dict(emitters=len(s.model.emitters),execution_order=s.model.execution_order,unsupported=s.model.unsupported),di_stubbed_functions=s.di_unimplemented,di_events=dict(audio_plays=sum(e['plays'] for e in s.di_state.audio_log),fixed_bounds_calls=len(s.di_state.bounds_log))))
    ticks=''.join(json.dumps(v,sort_keys=True)+'\n' for v in s.records);(out/'ticks.jsonl').write_text(ticks,encoding='utf-8');arrays={f'e{r.model.index}_{"f" if k[0]=="float" else "i"}{k[1]}':v for r in s.emitters if r.model.enabled for k,v in r.buffer.items()};np.savez(out/'final_buffers.npz',**arrays);hashes={'ticks.jsonl':hashlib.sha256(ticks.encode('utf-8')).hexdigest().upper(),'arrays':{k:hashlib.sha256(v.tobytes()).hexdigest().upper() for k,v in arrays.items()}};write('hashes.json',hashes);return hashes

def bake(model,inputs,out_dir):
    s=Scheduler(model,inputs);s.run();return save_bake(s,out_dir)

# ---- niagara_cache.py ----
# Niagara playback cache (spec N-O5): renderer binding resolution, per-tick capture and binary PLY streams.
#
# Capture drives Scheduler.tick() from outside and only reads scheduler state, so bakes stay byte-identical (Gate C0).
# Positions and vectors are converted to Blender space here (UE (x,y,z) cm -> (x,-y,z) m); see spec N-O5 section 1.

SCHEMA='dr.niagara-cache/1'
cache_UNIT=0.01
KINDS={'NiagaraSpriteRendererProperties':'sprite','NiagaraMeshRendererProperties':'mesh','NiagaraLightRendererProperties':'light','NiagaraRibbonRendererProperties':'ribbon'}
CAPTURED=('sprite','mesh','light','ribbon')
DYN=[('DynamicMaterialBinding','DynamicMaterialParameter','dyn0'),('DynamicMaterial1Binding','DynamicMaterialParameter1','dyn1'),('DynamicMaterial2Binding','DynamicMaterialParameter2','dyn2'),('DynamicMaterial3Binding','DynamicMaterialParameter3','dyn3')]
# (binding property, default particle attribute, cache prefix, components, conversion, default value or a renderer-property key)
SPRITE=[('PositionBinding','Position','pos',3,'pos',None),('ColorBinding','Color','col',4,'raw',(1,1,1,1)),('VelocityBinding','Velocity','vel',3,'vel',(0,0,0)),
        ('SpriteRotationBinding','SpriteRotation','rot',1,'raw',(0,)),('SpriteSizeBinding','SpriteSize','size',2,'len',(50,50)),
        ('SpriteFacingBinding','SpriteFacing','facing',3,'dir',(1,0,0)),('SpriteAlignmentBinding','SpriteAlignment','align',3,'dir',(1,0,0)),
        ('SubImageIndexBinding','SubImageIndex','subimg',1,'raw',(0,))]+[(b,a,p,4,'raw',(1,1,1,1)) for b,a,p in DYN]+[
        ('CameraOffsetBinding','CameraOffset','camoff',1,'len',(0,)),('UVScaleBinding','UVScale','uvs',2,'raw',(1,1)),('PivotOffsetBinding','PivotOffset','pivot',2,'raw','PivotInUVSpace'),
        ('MaterialRandomBinding','MaterialRandom','matrand',1,'raw',(0,)),('NormalizedAgeBinding','NormalizedAge','nage',1,'raw',(0,)),
        ('RendererVisibilityTagBinding','VisibilityTag','vistag',1,'raw','RendererVisibility'),('CustomSortingBinding','Age','sortkey',1,'raw',(0,))]
MESH=[('PositionBinding','Position','pos',3,'pos',None),('ColorBinding','Color','col',4,'raw',(1,1,1,1)),('VelocityBinding','Velocity','vel',3,'vel',(0,0,0)),
      ('SubImageIndexBinding','SubImageIndex','subimg',1,'raw',(0,))]+[(b,a,p,4,'raw',(1,1,1,1)) for b,a,p in DYN]+[
      ('MeshOrientationBinding','MeshOrientation','quat',4,'quat',(0,0,0,1)),('ScaleBinding','Scale','scale',3,'raw',(1,1,1)),
      ('MaterialRandomBinding','MaterialRandom','matrand',1,'raw',(0,)),('NormalizedAgeBinding','NormalizedAge','nage',1,'raw',(0,)),
      ('CameraOffsetBinding','CameraOffset','camoff',1,'len',(0,)),('RendererVisibilityTagBinding','VisibilityTag','vistag',1,'raw','RendererVisibility'),
      ('MeshIndexBinding','MeshIndex','meshidx',1,'raw',(0,)),('CustomSortingBinding','Age','sortkey',1,'raw',(0,))]
LIGHT=[('PositionBinding','Position','pos',3,'pos',None),('ColorBinding','Color','col',4,'raw',(1,1,1,1)),('RadiusBinding','LightRadius','radius',1,'len',(100,)),
       ('LightExponentBinding','LightExponent','exponent',1,'raw','DefaultExponent'),('LightRenderingEnabledBinding','LightEnabled','enabled',1,'bool',(1,)),
       ('VolumetricScatteringBinding','LightVolumetricScattering','volscat',1,'raw',(0,)),('RendererVisibilityTagBinding','VisibilityTag','vistag',1,'raw','RendererVisibility'),
       ('SpecularScaleBinding','LightSpecularScale','spec',1,'raw','SpecularScale'),('DiffuseScaleBinding','LightDiffuseScale','diff',1,'raw','DiffuseScale')]
RIBBON=[('PositionBinding','Position','pos',3,'pos',None),('ColorBinding','Color','col',4,'raw',(1,1,1,1)),('VelocityBinding','Velocity','vel',3,'vel',(0,0,0)),
        ('NormalizedAgeBinding','NormalizedAge','nage',1,'raw',(0,)),('RibbonTwistBinding','RibbonTwist','twist',1,'raw',(0,)),('RibbonWidthBinding','RibbonWidth','width',1,'len',(1,)),
        ('RibbonFacingBinding','RibbonFacing','facing',3,'dir',(0,0,1)),('RibbonIdBinding','RibbonID','rid',2,'raw',(0,0)),('RibbonLinkOrderBinding','RibbonLinkOrder','linkorder',1,'raw',(0,)),
        ('MaterialRandomBinding','MaterialRandom','matrand',1,'raw',(0,))]+[(b,a,p,4,'raw',(1,1,1,1)) for b,a,p in DYN]+[
        ('RibbonUVDistance','RibbonUVDistance','uvdist',1,'len',(0,)),('U0OverrideBinding','RibbonU0Override','u0ovr',1,'raw',(0,)),('V0RangeOverrideBinding','RibbonV0RangeOverride','v0rng',2,'raw',(0,1)),
        ('U1OverrideBinding','RibbonU1Override','u1ovr',1,'raw',(0,)),('V1RangeOverrideBinding','RibbonV1RangeOverride','v1rng',2,'raw',(0,1))]
TABLES={'sprite':SPRITE,'mesh':MESH,'light':LIGHT,'ribbon':RIBBON}
DEFAULTS={'sprite':{'Alignment':'Automatic','FacingMode':'Automatic','SortMode':'None','PivotInUVSpace':(0.5,0.5),'SubImageSize':(1.0,1.0),'bSubImageBlend':True,
                    'RendererVisibility':0,'SourceMode':'Particles','MinFacingCameraBlendDistance':0.0,'MaxFacingCameraBlendDistance':0.0,'bRemoveHMDRollInVR':False},
          'mesh':{'FacingMode':'Default','bLockedAxisEnable':False,'LockedAxis':(0.0,0.0,1.0),'LockedAxisSpace':'Simulation','SortMode':'None','RendererVisibility':0,
                  'SourceMode':'Particles','bOverrideMaterials':False},
          'light':{'bUseInverseSquaredFalloff':True,'bAffectsTranslucency':False,'bAlphaScalesBrightness':False,'RadiusScale':1.0,'DefaultExponent':1.0,
                   'ColorAdd':(0.0,0.0,0.0),'SpecularScale':1.0,'DiffuseScale':1.0,'RendererVisibility':0,'SourceMode':'Particles'},
          'ribbon':{'FacingMode':'Screen','Shape':'Plane','WidthSegmentationCount':1,'MultiPlaneCount':2,'TubeSubdivisions':3,'bEnableAccurateGeometry':False,
                    'bUseGeometryNormals':True,'TessellationMode':'Automatic','CurveTension':0.0,'TessellationFactor':16,'TessellationAngle':15.0,'bUseConstantFactor':False,
                    'bScreenSpaceTessellation':True,'bLinkOrderUseUniqueID':True,'SourceMode':'Particles','DrawDirection':'BackToFront'}}
UV_KEYS=('DistributionMode','LeadingEdgeMode','TrailingEdgeMode','bEnablePerParticleUOverride','bEnablePerParticleVRangeOverride','TilingLength')

class CacheError(ValueError):pass

def props_of(kind):return ['x','y','z','nx_valid']+[f'{p}_{c}' if n>1 else p for _,_,p,n,_,_ in TABLES[kind] if p!='pos' for c in range(n)]+(['uid'] if kind in ('light','ribbon') else [])
def to_b(v):return (float(v[0]),-float(v[1]),float(v[2]))
def rotator_quat(r):
    p,y,ro=(math.radians(float(r.get(k,0.0)))*0.5 for k in ('Pitch','Yaw','Roll'));sp,cp,sy,cy,sr,cr=math.sin(p),math.cos(p),math.sin(y),math.cos(y),math.sin(ro),math.cos(ro)
    return (cr*sp*sy-sr*cp*cy,-cr*sp*cy-sr*cp*sy,cr*cp*sy-sr*sp*cy,cr*cp*cy+sr*sp*sy)
def quat_b(q):return (-float(q[0]),float(q[1]),-float(q[2]),float(q[3]))
def _vec(v,keys):return tuple(float(v.get(k,0.0)) for k in keys) if isinstance(v,dict) else v
def cache__ref_index(ref):return int(ref['ObjectPath'].rsplit('.',1)[1])
DEFAULT_NAME=object()
def binding_name(value):
    """Cooked binding structs are delta-serialized: a struct without a variable name keeps the class-default binding."""
    if not isinstance(value,dict):return DEFAULT_NAME
    for key in ('ParamMapVariable','BoundVariable','ResolvedParameter'):
        name=(value.get(key) or {}).get('Name')
        if name:return None if name=='None' else name
    return DEFAULT_NAME

def renderer_records(doc,model):
    """Renderer exports per enabled CPU emitter, from the scheduler-selected emitter version, in RendererProperties order."""
    system=next(e for e in doc if e.get('Type')=='NiagaraSystem');handles=system['Properties'].get('EmitterHandles',[]);out=[]
    for e in model.emitters:
        if not e.enabled or e.sim_target not in ('CPUSim','GPUComputeSim'):continue
        em=doc[cache__ref_index(handles[e.index]['VersionedInstance']['Emitter'])]['Properties'];selected,_=select_emitter_version(em)
        for k,ref in enumerate(em['VersionData'][selected].get('RendererProperties') or []):
            if isinstance(ref,dict) and ref.get('ObjectPath'):
                x=doc[cache__ref_index(ref)];out.append((e,k,cache__ref_index(ref),x.get('Type',''),x.get('Properties') or {}))
    return out

def resolve_renderer(e,k,export_index,typ,p):
    kind=KINDS.get(typ,'other');info={'emitter':e.index,'ordinal':k,'export_index':export_index,'type':typ,'kind':kind,'enabled':p.get('bIsEnabled',True)}
    if kind not in CAPTURED:return info
    d=dict(DEFAULTS[kind])
    for key in d:
        if key in p:
            v=p[key];d[key]=short(v) if isinstance(v,str) and '::' in v else _vec(v,('X','Y','Z')[:len(d[key])]) if isinstance(d[key],tuple) else v
    info['props']=d;mode=d['SourceMode'];info['source_mode']=mode;bindings={};notes=[]
    for bkey,attr,prefix,n,conv,default in TABLES[kind]:
        name=binding_name(p.get(bkey))
        if name is DEFAULT_NAME:name='Particles.'+attr if mode=='Particles' else e.unique_name+'.'+attr
        if name and name.startswith('Emitter.'):name=e.unique_name+name[len('Emitter'):]
        bindings[prefix]={'binding':bkey,'name':name,'components':n,'conversion':conv,'default':list(d[default]) if isinstance(default,str) and isinstance(d[default],tuple) else [d[default]] if isinstance(default,str) else list(default) if default else None,
                          'exists_on_source_flag':(p.get(bkey) or {}).get('bBindingExistsOnSource') if isinstance(p.get(bkey),dict) else None}
    info['bindings']=bindings
    if kind=='ribbon':
        for key in ('UV0Settings','UV1Settings'):
            u=p.get(key) or {};uv={k:(short(u[k]) if isinstance(u[k],str) and '::' in u[k] else u[k]) for k in UV_KEYS if k in u}
            if isinstance(u.get('Offset'),dict):uv['Offset']=[float(u['Offset'].get('X',0.0)),float(u['Offset'].get('Y',0.0))]
            if isinstance(u.get('Scale'),dict):uv['Scale']=[float(u['Scale'].get('X',1.0)),float(u['Scale'].get('Y',1.0))]
            d[key]=uv
        mat=p.get('Material');info['material']=mat.get('ObjectPath') if isinstance(mat,dict) else None
    if kind=='sprite':
        mat=p.get('Material');info['material']=mat.get('ObjectPath') if isinstance(mat,dict) else None
        info['cutout_vertices']=bool(((p.get('DerivedData') or {}).get('BoundingGeometry')))
    if kind=='mesh':
        meshes=[]
        for m in p.get('Meshes') or []:
            ref=m.get('Mesh');meshes.append({'object_path':ref.get('ObjectPath') if isinstance(ref,dict) else None,'scale':list(_vec(m.get('Scale',{'X':1,'Y':1,'Z':1}),('X','Y','Z'))),
                'rotation_quat_ue':list(rotator_quat(m.get('Rotation') or {})),'pivot_offset':list(_vec(m.get('PivotOffset',{}),('X','Y','Z'))),'pivot_space':short(m.get('PivotOffsetSpace','Mesh'))})
        info['meshes']=meshes;info['override_materials']=[(x.get('ExplicitMat') or {}).get('ObjectPath') if isinstance(x,dict) else None for x in p.get('OverrideMaterials') or []]
    info['notes']=notes;return info

class cache_Resolver:
    """Resolves one renderer's bindings against a live scheduler emitter (spec N-O5 section 2.2)."""
    def __init__(self,info,model,emitter):
        self.info=info;self.e=emitter;self.layout=emitter.particle_layout;self.source={}
        for prefix,b in info['bindings'].items():
            name=b['name'];n=b['components']
            if name is None:self.source[prefix]=('default',None);continue
            if info['source_mode']=='Particles' and name.startswith('Particles.'):
                v=self.layout.find(name[len('Particles.'):])
                if v is None:self.source[prefix]=('default',None);continue
                nf,ni=len(v.type.float_offsets),len(v.type.int_offsets)
                if nf==n and ni==0:self.source[prefix]=('float',v)
                elif nf==0 and ni==n or (prefix=='rid' and nf==0 and ni==1):self.source[prefix]=('int',v)  # RibbonID: FNiagaraID (2 ints) or simple int
                else:self.source[prefix]=('type_mismatch',None)
            else:
                stores=[s.param_store for s in (emitter.update,emitter.spawn) if s is not None]
                self.source[prefix]=('param',name) if model.main_layout.find(name) is not None or any(name in s.entries for s in stores) else ('param_missing',None)
        b=info['bindings'];self.resolved={p:(s[0] in ('float','int','param')) for p,s in self.source.items()}
        if info['kind']=='sprite':
            pr=info['props']
            pr['Alignment_resolved']=pr['Alignment'] if pr['Alignment']!='Automatic' else ('CustomAlignment' if self.resolved['align'] else 'Unaligned')
            pr['FacingMode_resolved']=pr['FacingMode'] if pr['FacingMode']!='Automatic' else ('CustomFacingVector' if self.resolved['facing'] else 'FaceCamera')
        if info['kind']=='mesh':info['mesh_index_bound']=self.resolved['meshidx']
        if info['kind']=='ribbon':
            pr=info['props'];uid=self.layout.find('UniqueID')
            pr['twist_bound']=self.resolved['twist'];pr['ribbon_id_bound']=self.resolved['rid'];pr['uvdist_bound']=self.resolved['uvdist']
            for k in (0,1):pr['u%d_override_bound'%k]=self.resolved['u%dovr'%k];pr['v%d_range_bound'%k]=self.resolved['v%drng'%k]
            pr['link_order_source']='RibbonLinkOrder' if self.source['linkorder'][0]=='float' else 'UniqueID' if pr['bLinkOrderUseUniqueID'] and uid is not None and len(uid.type.int_offsets)==1 else 'NormalizedAge'
        info['flag_disagreements']=sorted(p for p,b in info['bindings'].items() if bool(b['exists_on_source_flag'])!=self.resolved[p])
        info['binding_sources']={p:(s[0] if s[0]!='float' and s[0]!='int' else 'particle:'+s[1].name) if s[0]!='param' else 'param:'+s[1] for p,s in self.source.items()}
    def param_value(self,sched,r,name,n):
        row=sched._row_value(name)
        if row is not None:f,i=row;vals=f if f else [float(x) for x in i];return vals[:n] if len(vals)>=n else None
        for key in ('update','spawn'):
            store=r.stores.get(key)
            if store is None or name not in store.entries:continue
            offset,t=store.entries[name];words=[struct.unpack_from('<I',store.data,offset+b)[0] for b in sorted(t.float_offsets+t.int_offsets)]
            vals=[word_float(w) for w in words] if t.float_offsets else [float(word_int(w)) for w in words];return vals[:n] if len(vals)>=n else None
        return None
    def column(self,sched,r,prefix,count):
        """float64 array [count, n] in UE units for one binding (defaults filled)."""
        b=self.info['bindings'][prefix];n=b['components'];kind,src=self.source[prefix]
        if kind=='float':return np.stack([r.buffer['float',src.float_start+j][:count].view(np.float32).astype(np.float64) for j in range(n)],axis=1)
        if kind=='int':
            cols=[r.buffer['int32',src.int_start+j][:count].view(np.int32).astype(np.float64) for j in range(len(src.type.int_offsets))]
            return np.stack(cols+[np.zeros(count)]*(n-len(cols)),axis=1)
        if kind=='param':
            vals=self.param_value(sched,r,src,n)
            if vals is not None:return np.tile(np.asarray(vals,dtype=np.float64),(count,1))
        if b['default'] is None:return None
        return np.tile(np.asarray(b['default'],dtype=np.float64),(count,1))

def convert(conv,values,origin_ue,owner_ue,local_space,owner_rot=None):
    """UE -> Blender for one binding; local-space emitters are moved into world space by the owner transform (rotation, then translation)."""
    v=values.copy();rot=owner_rot if (local_space and owner_rot is not None and tuple(owner_rot)!=IDENTITY_QUAT) else None
    axes=np.array(quat_axes(rot)) if rot is not None and conv in ('pos','vel','dir') else None
    if conv=='pos':
        if local_space:v=(v@axes if axes is not None else v)+np.asarray(owner_ue)
        v=(v-np.asarray(origin_ue))*cache_UNIT;v[:,1]*=-1
    elif conv=='vel':
        if axes is not None:v=v@axes
        v=v*cache_UNIT;v[:,1]*=-1
    elif conv=='dir':
        if axes is not None:v=v@axes
        v[:,1]*=-1
    elif conv=='len':v=v*cache_UNIT
    elif conv=='quat':
        if rot is not None:v=quat_mul(np.asarray(rot,dtype=np.float64)[None,:],v)  # owner * particle: particle rotation first
        v[:,0]*=-1;v[:,2]*=-1
    elif conv=='bool':v=(v!=0).astype(np.float64)
    return v

def frame_rows(sched,r,res,origin_ue,owner_ue,owner_rot=None):
    """Rows [N, len(props)] float32 for one renderer stream at the current tick, or None for an empty frame."""
    info=res.info;kind=info['kind'];emitter_mode=info['source_mode']=='Emitter'
    if emitter_mode:count=1 if r.state in (0,1) else 0
    else:count=r.count if r.state!=4 else 0
    if count==0:return None
    cols={}
    for bkey,attr,prefix,n,conv,default in TABLES[kind]:
        c=res.column(sched,r,prefix,count)
        if c is None:c=np.zeros((count,n)) if r.model.local_space else np.tile(np.asarray(owner_ue,dtype=np.float64),(count,1))  # Position default: sim origin
        cols[prefix]=convert(conv,c,origin_ue,owner_ue,r.model.local_space,owner_rot)
    out=[cols['pos'],np.ones((count,1))]+[cols[p] for _,_,p,_,_,_ in TABLES[kind] if p!='pos']
    if kind in ('light','ribbon'):
        v=res.layout.find('UniqueID');uid=r.buffer['int32',v.int_start][:count].view(np.int32).astype(np.float64) if (v is not None and not emitter_mode and len(v.type.int_offsets)==1) else np.arange(count,dtype=np.float64)
        if np.any(np.abs(uid)>2**24):raise CacheError('uid_overflow')
        out.append(uid.reshape(count,1))
    rows=np.concatenate(out,axis=1)
    for prefix in ('meshidx','vistag','rid'):
        if prefix in cols and np.any(np.abs(cols[prefix])>2**24):raise CacheError('int_overflow:'+prefix)
    mode=info['props'].get('SortMode','None')
    if mode in ('CustomAscending','CustomDecending'):
        key=cols['sortkey'][:,0];order=np.argsort(key if mode=='CustomAscending' else -key,kind='stable');rows=rows[order]
    return rows.astype(np.float32)

def ply_bytes(props,rows):
    if rows is None:rows=np.zeros((1,len(props)),dtype=np.float32)
    head=('ply\nformat binary_little_endian 1.0\nelement vertex %d\n'%len(rows)+''.join('property float %s\n'%p for p in props)+'end_header\n').encode('ascii')
    return head+np.ascontiguousarray(rows,dtype='<f4').tobytes()

def stream_name(info):return 'e%d_r%d'%(info['emitter'],info['ordinal'])

def bake_cache(system_path,out_dir,inputs,frame_start=1,fps=60.0,fps_base=1.0,bake_out=None):
    """Bake one system with per-tick capture; writes <out_dir>/cache_manifest.json and one PLY per stream per frame."""
    model=load_system(system_path);doc=json.loads(Path(system_path).read_text(encoding='utf-8-sig'));out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    sched=Scheduler(model,inputs);sched.activate()
    infos=[resolve_renderer(*rec) for rec in renderer_records(doc,model)]
    by_index={r.model.index:r for r in sched.emitters};streams=[]
    for info in infos:
        if info['kind'] in CAPTURED and info['enabled']:
            res=cache_Resolver(info,model,by_index[info['emitter']].model);d=out/stream_name(info);d.mkdir(exist_ok=True);streams.append((info,res,d,props_of(info['kind'])))
    origin=tuple(inputs.owner_translation_list[0]) if inputs.owner_translation_list else tuple(inputs.owner_translation)
    stats={'max_count':{},'empty_frames':{},'frames':inputs.ticks+1,'ribbon_subsegments':{}};smoothing={stream_name(i):Smoothing() for i,_,_,_ in streams if i['kind']=='ribbon'}
    def write_frame(frame,rows_by):
        for info,res,d,props in streams:
            rows=rows_by.get(stream_name(info));(d/('f_%04d.ply'%frame)).write_bytes(ply_bytes(props,rows));key=stream_name(info)
            if info['kind']=='ribbon':
                mesh=generate({p:rows[:,i] for i,p in enumerate(props)},info['props'],smoothing[key]) if rows is not None else None
                if rows is None:smoothing[key].__init__()
                (d/('m_%04d.ply'%frame)).write_bytes(mesh_ply_bytes(mesh));stats['ribbon_subsegments'].setdefault(key,[]).append(0 if mesh is None else int(mesh['nsub']))
            stats['max_count'][key]=max(stats['max_count'].get(key,0),0 if rows is None else len(rows));stats['empty_frames'][key]=stats['empty_frames'].get(key,0)+(rows is None)
    write_frame(frame_start,{})
    while sched.normal_tick<inputs.ticks:
        sched.tick();t=sched.normal_tick;owner=tuple(inputs.owner_translation_list[t-1]) if inputs.owner_translation_list else tuple(inputs.owner_translation)
        rot=tuple(inputs.owner_rotation_list[t-1]) if inputs.owner_rotation_list else tuple(inputs.owner_rotation)
        write_frame(frame_start+t,{stream_name(info):frame_rows(sched,by_index[info['emitter']],res,origin,owner,rot) for info,res,d,props in streams})
    hashes=save_bake(sched,bake_out) if bake_out else None
    manifest={'schema':SCHEMA,'system':model.package.rsplit('/',1)[-1].split('.')[0],'package':model.package,'source_path':str(system_path),'bake_hashes':hashes,
              'inputs':{k:v for k,v in vars(inputs).items() if k not in ('svml_shim_path','vector_fields')},'frame_start':frame_start,'frame_end':frame_start+inputs.ticks,
              'fps':fps,'fps_base':fps_base,'bake_origin_ue':list(origin),'bake_origin_b':[o*cache_UNIT for o in to_b(origin)],
              'emitters':[{'index':e.index,'unique_name':e.unique_name,'local_space':e.local_space,'enabled':e.enabled,'sim_target':e.sim_target} for e in model.emitters],
              'renderers':infos,'streams':[{'name':stream_name(i),'kind':i['kind'],'emitter':i['emitter'],'ordinal':i['ordinal'],'ply_properties':p} for i,_,_,p in streams],'stats':stats}
    (out/'cache_manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=1,default=list)+'\n',encoding='utf-8')
    return manifest

def tree_hashes(root):
    root=Path(root);return {str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest().upper() for p in sorted(root.rglob('*')) if p.is_file()}

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='cmd',required=True)
    b=sub.add_parser('bake');b.add_argument('--system',required=True);b.add_argument('--out',required=True);b.add_argument('--ticks',type=int,default=120)
    b.add_argument('--fps',type=float,default=60.0);b.add_argument('--fps-base',type=float,default=1.0);b.add_argument('--frame-start',type=int,default=1)
    b.add_argument('--shim',required=True);b.add_argument('--vector-fields',required=True);b.add_argument('--bake-out');b.add_argument('--no-dump-params',action='store_true')
    a=ap.parse_args(argv)
    pass
    inputs=BakeInputs(ticks=a.ticks,dt=a.fps_base/a.fps,di_mode='real',svml_shim_path=a.shim,vector_fields=a.vector_fields,parameter_overrides=None if a.no_dump_params else overrides_for(a.system) or None)
    m=bake_cache(a.system,a.out,inputs,a.frame_start,a.fps,a.fps_base,a.bake_out)
    print(json.dumps({'system':m['system'],'streams':len(m['streams']),'max_count':m['stats']['max_count']},sort_keys=True))

def _main_cache():
    main()

# ---- prop_fx.py ----
# Authored prop effects from a Blueprint export: lights, beam cones, looping animation tracks.
#
# Reads the BP dump that ``prop_assemble`` already uses and builds, under the assembled
# prop root (same pass-through Unreal-centimetre frame as the meshes):
#
# * ``SpotLightComponent`` / ``PointLightComponent`` / ``RectLightComponent`` -> Blender lights
#   (UE light units converted to watts; see ``ue_light_watts``)
# * lights with ``VolumetricScatteringIntensity`` > 0 and a cone -> optional volume beam cone
# * ``SimpleAnimationSequenceComponent`` tracks in ``Loop`` mode -> cyclic keyframes from the
#   ``CurveTranslateRotate`` asset (translate XYZ = FloatCurves[0..2], rotate roll/pitch/yaw = [3..5])
# * ``NiagaraComponent`` -> marker empty carrying the system path (bake with the Niagara player)
#
# Static data only. Values the BP leaves to runtime (e.g. a near-black authored LightColor on
# tagged indicator lights) are flagged on the object instead of guessed silently.


try:
    from ..Maps.prop_assemble import _rot, _scale, _ue_entries, _vec, unreal_to_blender_xyz_radians
except ImportError:  # tests import modules top-level
    from Maps.prop_assemble import _rot, _scale, _ue_entries, _vec, unreal_to_blender_xyz_radians

LIGHT_TYPES = {'SpotLightComponent': 'SPOT', 'PointLightComponent': 'POINT', 'RectLightComponent': 'AREA'}
NIAGARA_TYPES = {'NiagaraComponent'}
FX_TAG = 'arc_prop_fx'
# Blender spot/point irradiance at 1 m per watt (measured, N-O5 §15): e1 = 1/(4*pi).
_E1 = 1.0 / (4.0 * math.pi)
# Non-inverse-square UE lights have no 1/d^2; match Blender's inverse-square falloff at this
# fraction of AttenuationRadius (approximation, flagged on the light).
_NON_INV_SQ_MATCH = 0.3
# Volume beam density per unit VolumetricScatteringIntensity (1/m). Approximation: UE's beam
# visibility depends on the level's height fog, which a prop export does not carry.
_BEAM_DENSITY = 0.12
# Exported PSK vertices are Y-negated relative to UE (CUE4Parse writes -Y), so BP component
# transforms are mirrored to sit on the mesh: loc (x,-y,z); rotator -> (roll, -pitch, -yaw).
# Measured on SM_ExtractionElevator_01_A: warning lights land 0.07-0.46 m from the body vs 0.45-1.77 m unmirrored.
MIRROR_Y = True


def ue_loc(v):
    return (v[0], -v[1], v[2]) if MIRROR_Y else tuple(v)


def ue_euler(pitch, yaw, roll):
    if MIRROR_Y:
        return (math.radians(roll), math.radians(-pitch), math.radians(-yaw))
    return unreal_to_blender_xyz_radians(pitch, yaw, roll)


def _ref_name(ref) -> str:
    if not isinstance(ref, dict):
        return ''
    on = str(ref.get('ObjectName') or '')
    if ':' in on:
        on = on.split(':', 1)[1]
    return on.strip("'").split("'")[-1] if "'" in on else on


def propfx__export_index(ref) -> int | None:
    try:
        return int(str((ref or {}).get('ObjectPath', '')).rsplit('.', 1)[1])
    except (IndexError, ValueError):
        return None


def _color(props, key='LightColor'):
    c = props.get(key)
    if not isinstance(c, dict):
        return (1.0, 1.0, 1.0)
    # FColor bytes are sRGB; UE converts with FLinearColor(FColor) (sRGB curve).
    def lin(b):
        v = float(b) / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return (lin(c.get('R', 255)), lin(c.get('G', 255)), lin(c.get('B', 255)))


def ue_light_watts(props: dict, kind: str) -> float:
    """Blender watts for a UE local light (LocalLightComponent / SpotLightComponent unit rules)."""
    intensity = float(props.get('Intensity', 5000.0 if kind != 'AREA' else 1000.0))
    if not props.get('bUseInverseSquaredFalloff', True):
        # UE uses Intensity as-is here (no unit conversion, no x16, no cm^2 scale) with radial falloff
        # (1 - (d/R)^2)^exp and no 1/d^2. Match Blender's inverse-square at d = 0.3 R:
        # E(d) = I * falloff(d), watts = E(d) * d^2 / e1.
        d = _NON_INV_SQ_MATCH * float(props.get('AttenuationRadius', 1000.0)) * 0.01
        falloff = (1.0 - _NON_INV_SQ_MATCH ** 2) ** float(props.get('LightFalloffExponent', 8.0))
        return intensity * falloff * d * d / _E1
    units = str(props.get('IntensityUnits') or 'ELightUnits::Unitless').split('::')[-1]
    outer = math.radians(float(props.get('OuterConeAngle', 44.0))) if kind == 'SPOT' else math.pi
    if units == 'Candelas':
        brightness = intensity * 1e4
    elif units == 'Lumens':
        solid = 2.0 * math.pi * (1.0 - math.cos(outer)) if kind == 'SPOT' else 4.0 * math.pi
        brightness = intensity * 1e4 / solid
    elif units == 'EV':
        brightness = (2.0 ** intensity) * 1.25 * 1e4  # EV100 -> nits ~ candela/m^2 (rare on props)
    else:
        brightness = intensity * 16.0
    # brightness is in UE "candela x 1e4" scale (cm^2); Blender power gives irradiance P*e1 at 1 m.
    return brightness / 1e4 / _E1


def parse_bp_fx(bp_json_path: str) -> dict:
    """Light / Niagara components with SCS parent var, plus looping animation tracks."""
    out = {'lights': [], 'niagara': [], 'tracks': []}
    if not bp_json_path or not os.path.isfile(bp_json_path):
        return out
    with open(bp_json_path, 'r', encoding='utf-8') as fh:
        entries = _ue_entries(json.load(fh))
    by_index = dict(enumerate(entries))
    templates = {str(e.get('Name') or ''): e for e in entries}
    # SCS tree: child export index -> parent var
    node_var = {}
    tree_parent = {}
    for i, e in by_index.items():
        if e.get('Type') == 'SCS_Node':
            node_var[i] = str((e.get('Properties') or {}).get('InternalVariableName') or '')
    for i, e in by_index.items():
        if e.get('Type') != 'SCS_Node':
            continue
        for c in (e.get('Properties') or {}).get('ChildNodes') or []:
            ci = propfx__export_index(c)
            if ci is not None:
                tree_parent[ci] = node_var.get(i, '')
    for i, e in by_index.items():
        if e.get('Type') != 'SCS_Node':
            continue
        props = e.get('Properties') or {}
        tmpl = templates.get(_ref_name(props.get('ComponentTemplate'))) or {}
        ctype = tmpl.get('Type')
        if ctype not in LIGHT_TYPES and ctype not in NIAGARA_TYPES:
            continue
        tp = tmpl.get('Properties') or {}
        row = {
            'var': node_var[i],
            'type': ctype,
            'parent': str(props.get('ParentComponentOrVariableName') or tree_parent.get(i) or ''),
            'attach': str(props.get('AttachToName') or ''),
            'location': _vec(tp, 'RelativeLocation'),
            'rotation': _rot(tp),
            'scale': _scale(tp),
            'props': tp,
        }
        (out['lights'] if ctype in LIGHT_TYPES else out['niagara']).append(row)
    for e in entries:
        if e.get('Type') != 'SimpleAnimationSequenceComponent':
            continue
        props = e.get('Properties') or {}
        if not str(props.get('AnimationPlaybackMode') or '').endswith('Loop'):
            continue  # event-driven (doors, barriers): not an always-on effect
        for t in props.get('AnimationTracks') or []:
            comp = (t.get('ComponentPicker') or {}).get('SanitizedComponentName') or ''
            curve = (t.get('CurveTranslateRotate') or {}).get('ObjectPath') or ''
            if comp and curve:
                out['tracks'].append({'component': comp, 'curve': curve, 'owner': str(e.get('Name') or ''),
                                      'mode': str(t.get('AnimationTrackApplicationMode') or '').split('::')[-1],
                                      'play_rate': float(props.get('PlayRate', 1.0) or 1.0)})
    return out


def resolve_game_path(game_path: str, pioneer_root: str, bp_json_path: str = '') -> str:
    """'/Game/Pioneer/X/Y.0' -> '<pioneer_root>/X/Y.json' (falls back to the BP's folder)."""
    rel = game_path.split('.', 1)[0]
    for prefix in ('/Game/Pioneer/', '/Game/'):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    cands = []
    if pioneer_root:
        cands.append(os.path.join(pioneer_root, *rel.split('/')) + '.json')
    if bp_json_path:
        cands.append(os.path.join(os.path.dirname(bp_json_path), rel.split('/')[-1] + '.json'))
    return next((c for c in cands if os.path.isfile(c)), '')


def load_translate_rotate(path: str) -> dict[int, list[tuple[float, float, str]]]:
    """FloatCurves index -> [(time, value, interp)]."""
    curves = {}
    if not path:
        return curves
    with open(path, 'r', encoding='utf-8') as fh:
        for e in _ue_entries(json.load(fh)):
            for k, v in (e.get('Properties') or {}).items():
                # CUE4Parse writes static-array element 0 under the bare name
                if (k == 'FloatCurves' or k.startswith('FloatCurves[')) and isinstance(v, dict):
                    idx = 0 if k == 'FloatCurves' else int(k[len('FloatCurves['):-1])
                    curves[idx] = [(float(x.get('Time', 0.0)), float(x.get('Value', 0.0)),
                                    str(x.get('InterpMode') or 'RCIM_Linear')) for x in v.get('Keys') or []]
    return curves


def _new_object(name, data, collection, parent):
    import bpy
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    obj.parent = parent
    if parent is not None:
        obj.matrix_parent_inverse.identity()
    obj[FX_TAG] = 1
    return obj


def _beam_cone(name, length_cm, half_angle, density, color, collection, parent):
    """Cone along local +X (UE forward), apex at origin, in parent's cm space."""
    import bmesh
    import bpy
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    r = length_cm * math.tan(half_angle)
    segs = 24
    apex = bm.verts.new((0.0, 0.0, 0.0))
    ring = [bm.verts.new((length_cm, r * math.cos(2 * math.pi * i / segs), r * math.sin(2 * math.pi * i / segs)))
            for i in range(segs)]
    for i in range(segs):
        bm.faces.new((apex, ring[(i + 1) % segs], ring[i]))
    bm.faces.new(ring)
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    mat = bpy.data.materials.new(name + '_Beam')
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    outn = nt.nodes.new('ShaderNodeOutputMaterial')
    vol = nt.nodes.new('ShaderNodeVolumeScatter')
    vol.inputs['Color'].default_value = (*color, 1.0)
    vol.inputs['Density'].default_value = density
    nt.links.new(vol.outputs['Volume'], outn.inputs['Volume'])
    me.materials.append(mat)
    obj = _new_object(name, me, collection, parent)
    obj.display_type = 'WIRE'
    obj.visible_shadow = False
    return obj


def _keyframe_track(obj, curves, fps, play_rate):
    """AdditiveLocal track on a dedicated child empty: rotation_euler / location keyframes, cyclic."""
    import bpy
    paths = []
    span = 0.0
    for idx, keys in sorted(curves.items()):
        if not keys or idx > 5:
            continue
        if idx < 3:
            prop, axis, sign = 'location', idx, (-1.0 if idx == 1 and MIRROR_Y else 1.0)
        else:
            # roll/pitch/yaw, same mapping as ue_euler
            prop, axis = 'rotation_euler', idx - 3
            sign = (1.0, -1.0, -1.0)[axis] if MIRROR_Y else (-1.0, -1.0, 1.0)[axis]
        for t, v, interp in keys:
            val = sign * (math.radians(v) if prop == 'rotation_euler' else v)
            getattr(obj, prop)[axis] = val
            obj.keyframe_insert(prop, index=axis, frame=1.0 + t * fps / play_rate)
            span = max(span, t)
        paths.append((prop, axis, [k[2] for k in keys]))
    act = obj.animation_data.action if obj.animation_data else None
    if act is None:
        return 0.0
    for fc in _action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
        fc.modifiers.new('CYCLES')
    return span / play_rate


def _action_fcurves(act):
    fcs = getattr(act, 'fcurves', None)
    if fcs is not None:
        return list(fcs)
    out = []  # Blender 4.4+ layered actions
    for layer in getattr(act, 'layers', []):
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', []):
                out.extend(bag.fcurves)
    return out


def build_prop_fx(root_obj, bp_json_path: str, *, pioneer_root: str = '', activated: bool = True,
                  beams: bool = True, collection=None) -> dict:
    """Create authored lights / beams / looping tracks / Niagara markers under *root_obj*.

    ``activated`` shows runtime-toggled lights (``bVisible`` false in the BP, e.g. elevator warning
    lights tagged ``ExtractionActivatedIndicator``) and normalises a near-black authored colour to its
    hue, since the BP export carries no runtime colour. Both cases are flagged on the light object.
    """
    import bpy
    from mathutils import Euler, Vector

    stats = {'lights': 0, 'beams': 0, 'tracks': 0, 'niagara': 0, 'flags': [], 'error': ''}
    fx = parse_bp_fx(bp_json_path)
    if collection is None:
        collection = root_obj.users_collection[0] if root_obj.users_collection else bpy.context.scene.collection
    clear_prop_fx(root_obj)
    by_var = {str(o.get('arc_prop_attach_var') or ''): o for o in bpy.data.objects
              if o.get('arc_prop_attach_var') and _is_descendant(o, root_obj)}
    fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
    track_by_comp = {t['component']: t for t in fx['tracks']}
    curve_cache = {}

    def pose_empty(row):
        parent = by_var.get(row['parent']) or root_obj
        emp = _new_object(f"{root_obj.name}.{row['var']}", None, collection, parent)
        emp.empty_display_type = 'ARROWS'
        emp.empty_display_size = 30.0
        emp.rotation_mode = 'XYZ'
        emp.location = Vector(ue_loc(row['location']))
        emp.rotation_euler = Euler(ue_euler(*row['rotation']), 'XYZ')
        emp.scale = Vector(row['scale'])
        emp['arc_prop_fx_var'] = row['var']
        emp['arc_prop_fx_parent'] = row['parent'] if row['parent'] in by_var else 'root'
        track = track_by_comp.get(row['var'])
        if track is None:
            return emp
        path = track['curve']
        if path not in curve_cache:
            curve_cache[path] = load_translate_rotate(resolve_game_path(path, pioneer_root, bp_json_path))
        curves = curve_cache[path]
        if not curves:
            stats['flags'].append(f"{row['var']}: curve not exported ({path.split('/')[-1]})")
            return emp
        anim = _new_object(emp.name + '.Anim', None, collection, emp)
        anim.empty_display_size = 20.0
        anim['arc_prop_fx_track'] = path
        anim['arc_prop_fx_track_mode'] = track['mode']
        period = _keyframe_track(anim, curves, fps, track['play_rate'])
        anim['arc_prop_fx_period_s'] = period
        stats['tracks'] += 1
        return anim

    for row in fx['lights']:
        p = row['props']
        kind = LIGHT_TYPES[row['type']]
        holder = pose_empty(row)
        ld = bpy.data.lights.new(f"{root_obj.name}.{row['var']}", kind)
        color = _color(p)
        flags = []
        if max(color) < 0.01:
            flags.append('authored LightColor near black (runtime-set); hue kept, normalised')
            m = max(color) or 1.0
            color = tuple(c / m for c in color) if max(color) > 0 else (1.0, 1.0, 1.0)
        ld.color = color
        ld.energy = ue_light_watts(p, kind)
        if kind == 'SPOT':
            outer = math.radians(float(p.get('OuterConeAngle', 44.0)))
            inner = math.radians(float(p.get('InnerConeAngle', 0.0)))
            ld.spot_size = min(math.pi, 2.0 * outer)
            ld.spot_blend = max(0.0, min(1.0, (outer - inner) / outer)) if outer > 0 else 0.0
        if kind == 'AREA':
            ld.shape = 'RECTANGLE'
            # area size scales with object scale (root carries the 0.01 UE unit scale)
            bpy.context.view_layer.update()
            ws = max(1e-9, abs(holder.matrix_world.to_scale().x))
            ld.size = float(p.get('SourceWidth', 64.0)) * 0.01 / ws
            ld.size_y = float(p.get('SourceHeight', 64.0)) * 0.01 / ws
        else:
            ld.shadow_soft_size = float(p.get('SourceRadius', 0.0)) * 0.01
        ld.use_shadow = bool(p.get('CastShadows', True))
        if hasattr(ld, 'use_custom_distance'):
            ld.use_custom_distance = True
            ld.cutoff_distance = float(p.get('AttenuationRadius', 1000.0)) * 0.01
        if not p.get('bUseInverseSquaredFalloff', True):
            flags.append(f"non-inverse-square falloff (exp {p.get('LightFalloffExponent', 8.0)}) matched at "
                         f"{_NON_INV_SQ_MATCH:g} x radius")
        if hasattr(ld, 'volume_factor'):
            ld.volume_factor = float(p.get('VolumetricScatteringIntensity', 1.0))
        lo = _new_object(ld.name + '.Light', ld, collection, holder)
        # Blender lights aim down local -Z; UE components aim down +X.
        lo.rotation_euler = Euler((0.0, -math.pi / 2.0, 0.0), 'XYZ')
        lo['arc_prop_fx_units'] = str(p.get('IntensityUnits') or 'ELightUnits::Unitless')
        lo['arc_prop_fx_intensity'] = float(p.get('Intensity', 0.0))
        tags = p.get('ComponentTags') or []
        if tags:
            lo['arc_prop_fx_tags'] = ','.join(map(str, tags))
        hidden = p.get('bVisible') is False
        if hidden:
            lo['arc_prop_fx_runtime_toggled'] = 1
            flags.append('bVisible false in BP (runtime-activated)')
        off = hidden and not activated
        lo.hide_render = lo.hide_viewport = off
        vsi = float(p.get('VolumetricScatteringIntensity', 1.0))
        if beams and kind == 'SPOT' and 'VolumetricScatteringIntensity' in p and vsi > 0:
            cone = _beam_cone(f"{ld.name}.Beam", float(p.get('AttenuationRadius', 1000.0)),
                              math.radians(float(p.get('OuterConeAngle', 44.0))), _BEAM_DENSITY * vsi,
                              color, collection, holder)
            cone.hide_render = cone.hide_viewport = off
            cone['arc_prop_fx_beam'] = 1
            stats['beams'] += 1
        if flags:
            lo['arc_prop_fx_flags'] = '; '.join(flags)
            stats['flags'].append(f"{row['var']}: " + '; '.join(flags))
        stats['lights'] += 1

    for row in fx['niagara']:
        emp = pose_empty(row)
        asset = (row['props'].get('Asset') or {}).get('ObjectPath', '')
        emp['arc_ns_asset'] = asset
        emp['arc_ns_json'] = resolve_game_path(asset, pioneer_root, bp_json_path) if asset else ''
        emp['arc_ns_auto_activate'] = int(row['props'].get('bAutoActivate', True) is not False)
        stats['niagara'] += 1
    root_obj['arc_prop_fx'] = json.dumps({k: stats[k] for k in ('lights', 'beams', 'tracks', 'niagara')})
    return stats


def _is_descendant(obj, root) -> bool:
    p = obj.parent
    while p is not None:
        if p == root:
            return True
        p = p.parent
    return False


def clear_prop_fx(root_obj) -> int:
    """Remove FX objects previously built under *root_obj* (idempotent re-run)."""
    import bpy
    doomed = [o for o in bpy.data.objects if o.get(FX_TAG) and _is_descendant(o, root_obj)]
    for o in doomed:
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)
            elif isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
    return len(doomed)

# ---- niagara_fx.py ----
# Import a cooked Niagara system (NS_*) into the scene as inspectable data.
#
# Reads one exported ``NS_*.json`` and builds what the cook actually states: a root
# empty per system, one empty per emitter, one object per renderer with the real
# ``.pskx`` where a mesh is named, and every binding fact attached as an ``nsir_*``
# custom property.
#
# Two things are deliberately different from a Niagara playback:
#
# *   **No invented motion.** Particle motion is not executed here. Compiled
#     VectorVM evidence is retained for offline recovery; GPU programs require
#     shader extraction.
# *   **Real curves.** Curve data interfaces carry the baked ``ShaderLUT`` the GPU
#     sampled, so those become genuine F-curves (see ``niagara_curves``).
#
# The JSON must come from a property export that resolves Niagara data-interface
# classes from mappings. FModel's own export silently drops curve tables; dump with::
#
#     CUE4Parse.Example niagara-ns-dump --out-dir <dir>
#
# What each system is still missing is reported back to the caller rather than
# papered over.




RENDERER_TYPES = {
    "NiagaraSpriteRendererProperties": "sprite",
    "NiagaraMeshRendererProperties": "mesh",
    "NiagaraRibbonRendererProperties": "ribbon",
    "NiagaraLightRendererProperties": "light",
    "NiagaraComponentRendererProperties": "component",
    "NiagaraDecalRendererProperties": "decal",
}

SIM_TARGET = {0: "CPUSim", 1: "GPUComputeSim"}

# Byte width, little-endian format and component count for the POD parameter
# types that appear in InstanceParamStore.ParameterData.
PARAM_CODEC = {
    "NiagaraFloat": (4, "<f", 1),
    "NiagaraInt32": (4, "<i", 1),
    "NiagaraBool": (4, "<i", 1),
    "Vector2f": (8, "<2f", 2),
    "Vector3f": (12, "<3f", 3),
    "NiagaraPosition": (12, "<3f", 3),
    "Vector4f": (16, "<4f", 4),
    "Quat4f": (16, "<4f", 4),
    "LinearColor": (16, "<4f", 4),
}

# Data-interface fields that carry real payload rather than editor state.
DI_FIELDS = (
    "Field", "bTileX", "bTileY", "bTileZ",
    "EmitterName", "AttributeName",
    "MaxNeighborsPerCell", "NumCells", "CellSize",
    "FloatData", "InternalFloatData", "BoolData",
    "IntData", "ColorData", "PositionData",
    "StaticMesh", "SourceMode", "Texture",
    "SplineUserParameter", "EmitterProperties",
    "ShaderLUT", "LUTMinTime", "LUTMaxTime", "LUTInvTimeRange",
    "LUTNumSamplesMinusOne", "bUseLUT",
    "SpriteRendererName", "MeshRendererName",
)

# Unreal centimetres to Blender metres.
UE_TO_M = 0.01


# --------------------------------------------------------------------------- #
# Cooked JSON -> intermediate representation
# --------------------------------------------------------------------------- #

def load_exports(path):
    """FModel writes either {Exports:[...], Metadata:{}} or a bare export list."""
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
    if isinstance(doc, list):
        return [e for e in doc if isinstance(e, dict)]
    if isinstance(doc, dict) and isinstance(doc.get("Exports"), list):
        return [e for e in doc["Exports"] if isinstance(e, dict)]
    return []


def obj_ref(value):
    """Normalise an object reference to {class, name, path}."""
    if not isinstance(value, dict):
        return None
    name = value.get("ObjectName")
    path = value.get("ObjectPath")
    if not isinstance(name, str) and not isinstance(path, str):
        return None
    cls = None
    if isinstance(name, str) and "'" in name:
        head, _, tail = name.partition("'")
        cls, name = head, tail.rstrip("'")
    return {"class": cls, "name": name, "path": path}


def ref_leaf(value):
    """Short object name, dropping any Package:Sub prefix."""
    ref = obj_ref(value)
    if not ref or not isinstance(ref.get("name"), str):
        return None
    return ref["name"].split(":")[-1]


def owning_emitter(export):
    """Emitter export that owns this export, following one script hop.

    Data interfaces are outered to a script (``<emitter>.SpawnScript``), so the
    emitter is the segment before the last dot.
    """
    leaf = ref_leaf(export.get("Outer"))
    if not leaf:
        return None
    return leaf.rsplit(".", 1)[0] if "." in leaf else leaf


def renderer_ir(export):
    props = export.get("Properties") or {}
    kind = RENDERER_TYPES.get(export.get("Type"), "unknown")

    meshes = []
    for entry in props.get("Meshes") or []:
        if not isinstance(entry, dict):
            continue
        ref = obj_ref(entry.get("Mesh"))
        param = (((entry.get("MeshParameterBinding") or {})
                  .get("ResolvedParameter") or {}).get("Name"))
        meshes.append({
            "mesh": ref["name"] if ref else None,
            "path": ref["path"] if ref else None,
            "meshParam": None if param in (None, "None") else param,
            "scale": entry.get("Scale"),
            "pivotOffset": entry.get("PivotOffset"),
        })

    materials = []
    main = obj_ref(props.get("Material"))
    if main and main.get("name"):
        materials.append({"material": main["name"], "path": main["path"],
                          "source": "Material"})
    for entry in props.get("OverrideMaterials") or []:
        if not isinstance(entry, dict):
            continue
        ref = obj_ref(entry.get("ExplicitMat"))
        if ref and ref.get("name"):
            materials.append({"material": ref["name"], "path": ref["path"],
                              "source": "OverrideMaterials"})
        bound = (((entry.get("UserParamBinding") or {}).get("Parameter") or {})
                 .get("Name"))
        if bound and bound != "None":
            materials.append({"materialParam": bound,
                              "source": "UserParamBinding"})

    bindings = {}
    for key, value in props.items():
        if not key.endswith("Binding") or not isinstance(value, dict):
            continue
        bound = ((value.get("BoundVariable") or {}).get("Name")
                 or (value.get("ResolvedParameter") or {}).get("Name")
                 or value.get("Name"))
        if key == "MeshIndexBinding":
            bound = bound or (value.get("ParamMapVariable") or {}).get("Name")
        if bound and bound != "None":
            bindings[key] = bound

    return {
        "kind": kind,
        "type": export.get("Type"),
        "name": export.get("Name"),
        "meshes": meshes,
        "materials": materials,
        "bindings": bindings,
        "scalars": {k: v for k, v in props.items()
                    if isinstance(v, (int, float, str, bool))
                    and not k.endswith("Binding")},
    }


def di_ir(export):
    props = export.get("Properties") or {}
    payload = {}
    for key in DI_FIELDS:
        if key not in props:
            continue
        value = props[key]
        ref = obj_ref(value) if isinstance(value, dict) else None
        if ref:
            payload[key] = {"asset": ref["name"], "path": ref["path"]}
        elif isinstance(value, dict):
            nested = ((value.get("Parameter") or {}).get("Name")
                      or value.get("Name"))
            payload[key] = nested if nested else value
        else:
            payload[key] = value
    return {
        "class": export.get("Type"),
        "name": export.get("Name"),
        "payload": payload,
    }


def emitter_ir(export, renderers_by_outer):
    props = export.get("Properties") or {}
    versions = props.get("VersionData") or []
    selected, selection = select_emitter_version(props)
    version = versions[selected] if selected is not None else {}

    stages = [leaf for leaf in
              (ref_leaf(s) for s in version.get("SimulationStages") or []) if leaf]

    return {
        "name": props.get("UniqueEmitterName") or export.get("Name"),
        "export": export.get("Name"),
        "simTarget": normalize_enum(version.get("SimTarget"), SIM_TARGET),
        "selectedVersionIndex": selected,
        "versionSelection": selection,
        "localSpace": version.get("bLocalSpace", False),
        "determinism": version.get("bDeterminism", False),
        "randomSeed": version.get("RandomSeed"),
        "fixedBounds": version.get("FixedBounds"),
        "allocationMode": version.get("AllocationMode"),
        "preAllocationCount": version.get("PreAllocationCount"),
        "simulationStages": stages,
        "renderers": renderers_by_outer.get(export.get("Name"), []),
    }


def decode_param(data, offset, type_name):
    """Decode one exposed-parameter default out of the ParameterData blob."""
    codec = PARAM_CODEC.get(type_name)
    if codec is None or not isinstance(offset, int):
        return None
    width, fmt, count = codec
    if offset < 0 or offset + width > len(data):
        return None
    values = struct.unpack_from(fmt, data, offset)
    if type_name == "NiagaraBool":
        return bool(values[0])
    return values[0] if count == 1 else list(values)


def user_params(system_props):
    """User.* parameters with defaults decoded from the cooked byte blob."""
    store = ((system_props.get("SystemCompiledData") or {})
             .get("InstanceParamStore") or {})
    raw = store.get("ParameterData")
    data = bytes(v & 0xFF for v in raw) if isinstance(raw, list) else b""

    objects = []
    for entry in store.get("UObjects") or []:
        ref = obj_ref(entry)
        objects.append(ref["name"] if ref else None)

    params = []
    for entry in store.get("SortedParameterOffsets") or []:
        if not isinstance(entry, dict):
            continue
        type_ref = obj_ref((entry.get("TypeDef") or {}).get("ClassStructOrEnum"))
        type_name = type_ref["name"] if type_ref else None
        offset = entry.get("Offset")
        object_default = None
        if (type_name not in PARAM_CODEC and isinstance(offset, int)
                and 0 <= offset < len(objects)):
            object_default = objects[offset]
        params.append({
            "name": entry.get("Name"),
            "type": type_name,
            "default": decode_param(data, offset, type_name),
            "objectDefault": object_default,
        })
    return params


def system_ir(json_path):
    """Binding-level description of one cooked Niagara system."""
    evidence = extract_file(json_path)
    exports = load_exports(json_path)
    if not exports:
        raise ValueError(f"no exports in {Path(json_path).name}")

    renderers_by_outer = {}
    di_by_emitter = {}
    system_export = None
    emitter_exports = []

    for export in exports:
        kind = export.get("Type") or ""
        if kind == "NiagaraSystem":
            system_export = export
        elif kind == "NiagaraEmitter":
            emitter_exports.append(export)
        elif kind in RENDERER_TYPES:
            renderers_by_outer.setdefault(ref_leaf(export.get("Outer")), []) \
                .append(renderer_ir(export))
        elif "DataInterface" in kind:
            owner = owning_emitter(export)
            if owner:
                detail = di_ir(export)
                if detail["payload"]:
                    di_by_emitter.setdefault(owner, []).append(detail)

    emitters = [emitter_ir(e, renderers_by_outer) for e in emitter_exports]
    for emitter, export in zip(emitters, emitter_exports):
        payloads = di_by_emitter.get(export.get("Name"), [])
        emitter["diPayloads"] = payloads
        emitter["readsEmitters"] = sorted({
            p["payload"]["EmitterName"] for p in payloads
            if isinstance(p["payload"].get("EmitterName"), str)
        })

    known = {e.get("Name") for e in emitter_exports}
    orphans = [r for outer, group in renderers_by_outer.items() if outer not in known
               for r in group]

    return {
        "system": Path(json_path).stem,
        "sourceJson": str(json_path),
        "compiledEvidence": evidence,
        "emitters": emitters,
        "orphanRenderers": orphans,
        "userParams": user_params((system_export or {}).get("Properties") or {}),
    }


# --------------------------------------------------------------------------- #
# IR -> Blender scene
# --------------------------------------------------------------------------- #

def set_props(obj, mapping, prefix="nsir"):
    for key, value in mapping.items():
        if value is None:
            continue
        name = f"{prefix}_{key}"
        if isinstance(value, (int, float, str, bool)):
            obj[name] = value
        else:
            obj[name] = json.dumps(value)


def new_empty(name, parent=None, display="PLAIN_AXES", size=0.25):
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = display
    obj.empty_display_size = size
    bpy.context.scene.collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
    return obj


def curve_prop_name(index, payload):
    """Custom-property name for one curve payload slot.

    Indexed by slot because data-interface export names repeat within a single
    emitter's payload list; keying on the bare name overwrites earlier curves.
    """
    return f"nsir_curve_{index:03d}_{payload.get('name') or payload.get('class')}"


def action_fcurves(action):
    """F-curves of an action across legacy and slotted-action APIs.

    Blender 4.4+ moved them into layers/strips/channelbags; 5.x dropped
    ``Action.fcurves`` entirely.
    """
    if action is None:
        return []
    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        return list(legacy)
    found = []
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for bag in getattr(strip, "channelbags", ()):
                found.extend(bag.fcurves)
    return found


def bake_curves(obj, payloads, stats):
    """Bake cooked curve LUTs onto obj as keyframed custom properties.

    Interpolation is LINEAR: the LUT is already a dense resample of the source
    curve, so Bezier smoothing would invent overshoot the GPU never produced.
    """
    fps = (bpy.context.scene.render.fps or 24) / (bpy.context.scene.render.fps_base or 1.0)
    baked = 0

    for index, payload in enumerate(payloads or ()):
        cls = payload.get("class")
        if not is_curve_interface(cls):
            continue
        curve = decode_curve(cls, payload.get("payload") or {})
        if curve is None:
            continue

        prop = curve_prop_name(index, payload)
        channels = curve["channels"]
        obj[f"{prop}_info"] = curve_summary(curve)

        if curve["constant"]:
            flat = [ch[0] for ch in curve["values"]]
            obj[prop] = flat if channels > 1 else flat[0]
            stats["curvesConstant"] = stats.get("curvesConstant", 0) + 1
            baked += 1
            continue

        obj[prop] = [0.0] * channels if channels > 1 else 0.0
        path = f'["{prop}"]'
        for i, t in enumerate(curve["times"]):
            if channels > 1:
                obj[prop] = [curve["values"][c][i] for c in range(channels)]
            else:
                obj[prop] = curve["values"][0][i]
            obj.keyframe_insert(data_path=path, frame=1.0 + t * fps)

        for fcurve in action_fcurves(getattr(obj.animation_data, "action", None)):
            if fcurve.data_path != path:
                continue
            for point in fcurve.keyframe_points:
                point.interpolation = "LINEAR"

        stats["curveKeys"] = stats.get("curveKeys", 0) + curve["samples"] * channels
        stats["curvesAnimated"] = stats.get("curvesAnimated", 0) + 1
        baked += 1

    stats["curves"] = stats.get("curves", 0) + baked
    return baked


def find_mesh_file(asset_root, mesh_name, cache):
    """Locate <mesh_name>.pskx/.psk under the export root."""
    if not asset_root or not mesh_name:
        return None
    if mesh_name in cache:
        return cache[mesh_name]
    hit = None
    for ext in (".pskx", ".psk"):
        for candidate in Path(asset_root).rglob(f"{mesh_name}{ext}"):
            hit = candidate
            break
        if hit:
            break
    cache[mesh_name] = hit
    return hit


def placeholder(name, kind):
    """Named stand-in so a renderer is visible without inventing geometry."""
    mesh = bpy.data.meshes.new(f"{name}_placeholder")
    if kind == "sprite":
        verts = [(-0.05, -0.05, 0), (0.05, -0.05, 0), (0.05, 0.05, 0), (-0.05, 0.05, 0)]
        mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    elif kind == "ribbon":
        mesh.from_pydata([(0, 0, 0), (0.2, 0, 0)], [(0, 1)], [])
    else:
        mesh.from_pydata([(0, 0, 0)], [], [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def ensure_material(name, path):
    """Material named after the cooked MI, with its source path recorded.

    Not a shader rebuild: FX materials are a separate pixel path, so this only
    carries identity so the right MI can be resolved later.
    """
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing
    mat = bpy.data.materials.new(name)
    mat["nsir_source_path"] = path or ""
    return mat


def build_renderer(rend, emitter_obj, asset_root, cache, stats):
    kind = rend["kind"]
    base = f"{emitter_obj.name}.{rend['name'] or kind}"

    if kind == "light":
        data = bpy.data.lights.new(base, type="POINT")
        obj = bpy.data.objects.new(base, data)
        bpy.context.scene.collection.objects.link(obj)
        obj.parent = emitter_obj
        set_props(obj, {"kind": kind, "bindings": rend["bindings"],
                        "scalars": rend["scalars"]})
        stats["light"] = stats.get("light", 0) + 1
        return obj

    obj = None
    if kind == "mesh":
        for entry in rend["meshes"]:
            path = find_mesh_file(asset_root, entry.get("mesh"), cache)
            if path is None:
                continue
            try:
                imported = [o for o in importing_import_psk(str(path))
                            if o and o.type == "MESH"]
            except Exception as exc:  # noqa: BLE001 - report, keep building
                stats["pskFailed"] = stats.get("pskFailed", 0) + 1
                print(f"Arc Raiders: psk import failed for {path.name}: {exc}")
                continue
            if not imported:
                continue
            obj = imported[0]
            obj.name = base
            for extra in imported[1:]:
                extra.parent = emitter_obj
            scale = entry.get("scale") or {}
            if isinstance(scale, dict):
                obj.scale = (scale.get("X", 1.0),
                             scale.get("Y", 1.0),
                             scale.get("Z", 1.0))
            pivot = entry.get("pivotOffset") or {}
            if isinstance(pivot, dict):
                obj.location = (pivot.get("X", 0.0) * UE_TO_M,
                                pivot.get("Y", 0.0) * UE_TO_M,
                                pivot.get("Z", 0.0) * UE_TO_M)
            stats["meshImported"] = stats.get("meshImported", 0) + 1
            break

    if obj is None:
        obj = placeholder(base, kind)
        stats[f"{kind}Placeholder"] = stats.get(f"{kind}Placeholder", 0) + 1

    obj.parent = emitter_obj
    for entry in rend["materials"]:
        if entry.get("material"):
            obj.data.materials.append(
                ensure_material(entry["material"], entry.get("path")))
    set_props(obj, {
        "kind": kind,
        "rendererType": rend["type"],
        "bindings": rend["bindings"],
        "scalars": rend["scalars"],
        "materials": rend["materials"],
        "meshes": rend["meshes"],
    })
    return obj


def importing_import_psk(filepath):
    """Import a PSK via the addon's own importer."""
    from ..Shared import importing
    return importing.import_psk(filepath)


def psk_available():
    try:
        from ..Shared import importing
        return importing.psk_reader_available()
    except Exception:  # noqa: BLE001
        return False


def bounds_box(name, fixed_bounds, parent):
    """Wireframe box for the emitter's cooked FixedBounds."""
    if not isinstance(fixed_bounds, dict):
        return None
    lo = fixed_bounds.get("Min") or {}
    hi = fixed_bounds.get("Max") or {}
    try:
        x0, y0, z0 = (float(lo["X"]) * UE_TO_M, float(lo["Y"]) * UE_TO_M,
                      float(lo["Z"]) * UE_TO_M)
        x1, y1, z1 = (float(hi["X"]) * UE_TO_M, float(hi["Y"]) * UE_TO_M,
                      float(hi["Z"]) * UE_TO_M)
    except (KeyError, TypeError, ValueError):
        return None
    if not all(map(math.isfinite, (x0, y0, z0, x1, y1, z1))):
        return None

    verts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
             (0, 4), (1, 5), (2, 6), (3, 7)]
    mesh = bpy.data.meshes.new(f"{name}_bounds")
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    obj = bpy.data.objects.new(f"{name}_bounds", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    obj.display_type = "WIRE"
    obj.hide_render = True
    return obj


def fx_build_system(ir, asset_root=None, bake=True, stats=None):
    """Build one system's IR into the scene. Returns (root object, missing)."""
    stats = stats if stats is not None else {}
    cache = {}
    name = ir["system"]
    root = new_empty(name, display="ARROWS", size=1.0)

    defaults = {p["name"]: p["default"] for p in ir.get("userParams") or []
                if p.get("default") is not None}
    set_props(root, {
        "sourceJson": ir.get("sourceJson"),
        "userParamDefaults": defaults,
        "userParamNames": [p["name"] for p in ir.get("userParams") or []],
        "sourceSha256": (ir.get("compiledEvidence", {}).get("source") or {}).get("sha256"),
        "scriptCount": len(ir.get("compiledEvidence", {}).get("scripts", [])),
        "simulationStatus": "not-executed",
    })

    for emitter in ir["emitters"]:
        obj = new_empty(f"{name}.{emitter['name']}", parent=root, display="SPHERE")
        set_props(obj, {
            "simTarget": emitter.get("simTarget"),
            "localSpace": emitter.get("localSpace"),
            "determinism": emitter.get("determinism"),
            "randomSeed": emitter.get("randomSeed"),
            "allocationMode": emitter.get("allocationMode"),
            "preAllocationCount": emitter.get("preAllocationCount"),
            "simulationStages": emitter.get("simulationStages"),
            "readsEmitters": emitter.get("readsEmitters"),
        })
        bounds_box(obj.name, emitter.get("fixedBounds"), obj)
        if bake:
            bake_curves(obj, emitter.get("diPayloads"), stats)
        for rend in emitter.get("renderers") or []:
            build_renderer(rend, obj, asset_root, cache, stats)
            stats["renderers"] = stats.get("renderers", 0) + 1
        stats["emitters"] = stats.get("emitters", 0) + 1

    for rend in ir.get("orphanRenderers") or []:
        build_renderer(rend, root, asset_root, cache, stats)
        stats["renderers"] = stats.get("renderers", 0) + 1

    scripts = ir.get("compiledEvidence", {}).get("scripts", [])
    stats["scriptsRetained"] = len(scripts)
    stats["vmProgramsRetained"] = sum(s["bytecode"]["state"] == "present" for s in scripts)
    missing = ["particle simulation not executed (compiled evidence retained for offline recovery)"]
    if not defaults:
        missing.append("no decoded User.* defaults")
    if any(e.get("simulationStages") for e in ir["emitters"]):
        missing.append("simulation stages (GPU permutations, need DXBC disassembly)")
    if not bake:
        missing.append("curve LUTs not baked")
    return root, missing


def import_ns_json(json_path, asset_root=None, bake=True):
    """Read a cooked NS json and build it. Returns (root, stats, missing)."""
    ir = system_ir(json_path)
    stats = {}
    root, missing = fx_build_system(ir, asset_root=asset_root, bake=bake, stats=stats)
    return root, stats, missing


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #

# ---- niagara_playback.py ----
# Niagara playback in the addon (spec N-O5 §13, N6b): bake a cooked system with the offline CPU scheduler and build GN rigs.
#
# The bake, rig and dump-parameter helpers are merged into this module, so the bake entry points are read from this module.



ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Dev data root (the scratch .local folder). Unset means the DEFAULT_* paths below are empty.
_DEV_ROOT = os.environ.get('ARC_NIAGARA_DEV_ROOT', '')
DEFAULT_SHIM = os.path.join(_DEV_ROOT, 'niagara-v2', 'wave2', 'build', 'svml_shim', 'svml_shim.dll') if _DEV_ROOT else ''
DEFAULT_VECTOR_FIELDS = os.path.join(_DEV_ROOT, 'niagara-v2', 'data', 'vector_fields.json') if _DEV_ROOT else ''
DEFAULT_GPU_BINDINGS = os.path.join(_DEV_ROOT, 'niagara-v2', 'data', 'gpu_bindings') if _DEV_ROOT else ''
DEFAULT_GPU_SHADERS = os.path.join(_DEV_ROOT, 'niagara-v2', 'wave1', 'out', 'NW4') if _DEV_ROOT else ''
PROPERTY_NAMES = ('arc_ns_play_ticks', 'arc_ns_play_cache_dir', 'arc_ns_play_shim', 'arc_ns_play_vector_fields', 'arc_ns_play_at_cursor',
                  'arc_ns_play_collision')
MAX_COLLISION_TRIANGLES = 2_000_000


def _bake_modules():
    if ADDON_DIR not in sys.path:
        sys.path.append(ADDON_DIR)
    mod = sys.modules[__name__]
    return mod, mod


def register_properties(assign):
    assign('arc_ns_play_ticks', IntProperty(
        name='Preview Frames', description="Preview Frames: how many frames of the effect to simulate", default=120, min=1, max=3000))
    assign('arc_ns_play_cache_dir', StringProperty(
        name='Cache Folder', description="",
        default='//niagara_cache/', subtype='DIR_PATH'))
    assign('arc_ns_play_shim', StringProperty(
        name='SVML Shim', description="",
        default='', subtype='FILE_PATH'))
    assign('arc_ns_play_vector_fields', StringProperty(
        name='Vector Fields', description="",
        default='', subtype='FILE_PATH'))
    assign('arc_ns_play_collision', BoolProperty(
        name='Scene Collision', description="",
        default=True))
    assign('arc_ns_play_at_cursor', BoolProperty(
        name='Spawn at 3D Cursor', description="", default=True))

def _dump_overrides(path):
    return overrides_for(path) or None


def _existing(path, fallback):
    p = bpy.path.abspath(path or '') if path else ''
    if p and os.path.isfile(p):
        return p
    return fallback if fallback and os.path.isfile(fallback) else None


def camera_input(scene):
    """Active camera as the Camera DI scene input: UE world position (cm), FRotator (pitch, yaw, roll) degrees, horizontal FOV."""
    import math
    cam = scene.camera
    if cam is None or cam.type != 'CAMERA':
        return None
    m = cam.matrix_world
    loc = m.translation
    to_ue = lambda v: (v[0], -v[1], v[2])
    fwd = to_ue(-m.col[2].xyz.normalized())
    up = to_ue(m.col[1].xyz.normalized())
    pitch = math.degrees(math.atan2(fwd[2], math.hypot(fwd[0], fwd[1])))
    yaw = math.degrees(math.atan2(fwd[1], fwd[0]))
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    y0, z0 = (-sy, cy, 0.0), (-sp * cy, -sp * sy, cp)
    roll = math.degrees(math.atan2(sum(a * b for a, b in zip(up, y0)), sum(a * b for a, b in zip(up, z0))))
    fov = math.degrees(cam.data.angle_x) if cam.data.type == 'PERSP' else 90.0
    return (loc[0] * 100.0, -loc[1] * 100.0, loc[2] * 100.0, pitch, yaw, roll, fov)


def export_collision(context, path, exclude):
    """Visible scene mesh triangles in UE world cm (x, -y, z) * 100 -> npz['triangles']; returns the triangle count."""
    import numpy as np
    dg = context.evaluated_depsgraph_get()
    chunks, total = [], 0
    for obj in context.scene.objects:
        if obj.type != 'MESH' or obj.name in exclude or obj.hide_render or not obj.visible_get():
            continue
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            me.calc_loop_triangles()
            n = len(me.loop_triangles)
            if not n:
                continue
            co = np.zeros(len(me.vertices) * 3, dtype=np.float64)
            me.vertices.foreach_get('co', co)
            co = co.reshape(-1, 3)
            idx = np.zeros(n * 3, dtype=np.int64)
            me.loop_triangles.foreach_get('vertices', idx)
            mw = np.array(obj.matrix_world, dtype=np.float64)
            w = co @ mw[:3, :3].T + mw[:3, 3]
            tri = w[idx.reshape(-1, 3)] * np.array([100.0, -100.0, 100.0])
            chunks.append(tri)
            total += n
        finally:
            ev.to_mesh_clear()
        if total > MAX_COLLISION_TRIANGLES:
            break
    tris = np.concatenate(chunks) if chunks else np.zeros((0, 3, 3))
    np.savez(path, triangles=tris[:MAX_COLLISION_TRIANGLES])
    return min(total, MAX_COLLISION_TRIANGLES)


def _ui_targets():
    try:
        from ..Shared import ui_targets
        return ui_targets
    except Exception:  # noqa: BLE001
        return None


def _selected_ns_path(context):
    pass
    scene = context.scene
    entry = get_entry(getattr(scene, 'arc_ns_selected', '') or '', getattr(scene, 'arc_ns_catalog_root', '') or '')
    selected_path = resolve_entry_path(entry, scene=scene)
    if selected_path:
        return selected_path
    if getattr(scene, 'arc_ns_selected', '') not in ('', 'NONE'):
        return None
    p = bpy.path.abspath(getattr(scene, 'arc_ns_json_path', '') or '')
    return p if os.path.isfile(p) else None


def _mesh_objects(manifest, asset_root, collection):
    """Import each mesh renderer's cooked mesh once (hidden source objects for GN instancing).

    The rigs instance the source's original geometry (Object Info, ORIGINAL space), so the import's unit scale is baked
    into the mesh data; the PSK's material names are kept for the renderer material callback (materials are not imported).
    """
    from mathutils import Matrix

    pass
    objects, cache = {}, {}
    for r in manifest['renderers']:
        for m in r.get('meshes', []):
            path = m.get('object_path')
            if not path or path in objects:
                continue
            existing = next((o for o in bpy.data.objects if o.type == 'MESH' and o.get('dr_nx_mesh_source') == path), None)
            if existing is not None:  # another system (or socket) already imported it
                objects[path] = existing
                continue
            name = path.rsplit('/', 1)[-1].split('.')[0]
            hit = find_mesh_file(asset_root, name, cache) if asset_root else None
            if hit is None:
                continue
            try:
                every = [o for o in importing_import_psk(str(hit)) if o]
            except Exception as exc:  # noqa: BLE001 - placeholder instead
                print(f'Arc Raiders: NX mesh import failed for {hit}: {exc}')
                continue
            imported = [o for o in every if o.type == 'MESH']
            if imported:
                src = imported[0]
                bpy.context.view_layer.update()
                scale = src.matrix_world.to_scale()  # the unit scale sits on the import root (an armature for PSK)
                src.parent = None
                src.matrix_world = Matrix.Identity(4)
                src.data.transform(Matrix.Diagonal(tuple(scale) + (1.0,)))
                for mod in [m for m in src.modifiers if m.type == 'ARMATURE']:
                    src.modifiers.remove(mod)
                for extra in every:
                    if extra is not src and extra.type == 'ARMATURE' and not extra.children:
                        bpy.data.objects.remove(extra)
                psk = importing.read_psk_safe(str(hit))
                src['dr_nx_slot_materials'] = [m.name.decode('utf-8', 'replace') for m in getattr(psk, 'materials', None) or []]
                _bake_modules()[1].hide_instance_source(src)
                src['dr_nx_mesh_source'] = path
                objects[path] = src
    return objects


def material_roots(asset_root):
    """Extraction roots searched for FX material JSON and textures: the NS asset root, then the pak extract caches."""
    from ..Shared import utils
    return [r for r in [asset_root] + list(getattr(utils, '_pak_cache_roots', [])) if r]


PREVIEW_CONFIRM_FRAMES = 100


def _busy_text(targets):
    """Message shown while another import job runs (D5)."""
    try:
        return targets.job_busy_message() or 'Another import is running'
    except Exception:  # noqa: BLE001
        return 'Another import is running'


class ARC_OUTFITS_OT_PlayNiagaraFX(Operator):
    """Simulate the selected cooked Niagara system (CPU emitters) and build Geometry Nodes playback rigs.

    Particle data comes from the game's compiled VectorVM bytecode run offline; materials are previews until FX material
    translation lands. GPU emitters are not simulated yet.
    """
    bl_idname = 'arc_outfits.play_niagara_fx'
    bl_label = 'Bake Scene Preview'
    bl_description = 'Simulate the chosen effect and play it in the scene. Blender is busy until the bake finishes'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        targets = _ui_targets()
        if targets is not None and targets.job_active():
            cls.poll_message_set(_busy_text(targets))
            return False
        scene = getattr(context, 'scene', None)
        if scene is None:
            return False
        if (getattr(scene, 'arc_ns_selected', '') or '') in ('', 'NONE') and not (getattr(scene, 'arc_ns_json_path', '') or ''):
            cls.poll_message_set('Choose an effect first')
            return False
        return True

    def invoke(self, context, event):
        targets = _ui_targets()
        if targets is not None and targets.job_active():
            self.report({'WARNING'}, _busy_text(targets))
            return {'CANCELLED'}
        frames = int(getattr(context.scene, 'arc_ns_play_ticks', 0) or 0)
        if frames > PREVIEW_CONFIRM_FRAMES:
            try:
                return context.window_manager.invoke_confirm(self, event, title='Bake Scene Preview',
                                                             message=f'Baking {frames} frames blocks Blender until it finishes',
                                                             confirm_text='Bake')
            except TypeError:
                return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        scene = context.scene
        path = _selected_ns_path(context)
        if not path:
            self.report({'ERROR'}, 'Choose an effect first')
            return {'CANCELLED'}
        targets = _ui_targets()
        name = Path(path).stem
        if targets is not None:
            if targets.job_active():
                self.report({'WARNING'}, _busy_text(targets))
                return {'CANCELLED'}
            name = targets.clean_name(name) or name
            try:
                began = targets.job_begin('effect', 'Baking ' + name, 1, '', cancellable=False)
            except TypeError:
                began = targets.job_begin('effect', 'Baking ' + name, 1, '')
            if not began:
                self.report({'WARNING'}, _busy_text(targets))
                return {'CANCELLED'}
        from ..Shared import ui_lists
        before = ui_lists.snapshot()
        result, error = {'CANCELLED'}, ''
        try:
            result, error = self._preview(context, path, name)
            return result
        finally:
            ok = 'FINISHED' in result
            if ok:
                try:
                    ui_lists.record_import(name, before)
                    ui_lists.record_recent('fx', getattr(scene, 'arc_ns_selected', ''))
                except Exception as exc:  # noqa: BLE001
                    print(f'Arc Raiders effects: history not recorded for {name}: {exc}')
            if targets is not None:
                lines = [] if ok else [f'Failed: {name}: {error or "preview failed"}']
                try:
                    targets.job_end(1 if ok else 0, 0 if ok else 1, 0, lines, status='completed' if ok else 'failed')
                except TypeError:
                    targets.job_end(1 if ok else 0, 0 if ok else 1, 0, lines)

    def _preview(self, context, path, name):
        scene = context.scene
        try:
            nc, rigs = _bake_modules()
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, f'Effect preview is unavailable: {exc}')
            return {'CANCELLED'}, str(exc)
        shim = _existing(scene.arc_ns_play_shim, DEFAULT_SHIM)
        vf = _existing(scene.arc_ns_play_vector_fields, DEFAULT_VECTOR_FIELDS)
        fps = scene.render.fps / scene.render.fps_base
        cur = scene.cursor.location if scene.arc_ns_play_at_cursor else (0.0, 0.0, 0.0)
        owner = (cur[0] * 100.0, -cur[1] * 100.0, cur[2] * 100.0)
        stem = Path(path).stem
        cache_dir = Path(bpy.path.abspath(scene.arc_ns_play_cache_dir or '//niagara_cache/')) / stem
        collision = None
        if scene.arc_ns_play_collision:
            cache_dir.mkdir(parents=True, exist_ok=True)
            collision = str(cache_dir / 'collision_world.npz')
            exclude = {o.name for o in scene.objects if o.get('dr_nx_stream') or o.get('dr_nx_mesh_source') or o.name.startswith('NX_')}
            export_collision(context, collision, exclude)
        inputs = nc.BakeInputs(ticks=int(scene.arc_ns_play_ticks), dt=1.0 / fps, di_mode='real', math_backend='svml_shim' if shim else 'reference',
                               svml_shim_path=shim, vector_fields=vf, owner_translation=owner, camera=camera_input(scene), collision_mesh=collision,
                               gpu_bindings=DEFAULT_GPU_BINDINGS if os.path.isdir(DEFAULT_GPU_BINDINGS) else None,
                               gpu_shaders=DEFAULT_GPU_SHADERS if os.path.isdir(DEFAULT_GPU_SHADERS) else None,
                               parameter_overrides=_dump_overrides(path))
        t0 = time.perf_counter()
        try:
            manifest = nc.bake_cache(path, cache_dir, inputs, frame_start=scene.frame_current, fps=float(scene.render.fps), fps_base=float(scene.render.fps_base))
        except Exception as exc:  # noqa: BLE001 - BakeError reasons are user-facing (gpu_emitter, di_unimplemented:...)
            self.report({'ERROR'}, f'{name}: preview failed: {exc}')
            return {'CANCELLED'}, str(exc)
        pass
        asset_root = resolved_asset_root(bpy.path.abspath(getattr(scene, 'arc_ns_asset_root', '') or '') or '') or None
        collection = bpy.data.collections.new('NX_' + stem)
        scene.collection.children.link(collection)
        pass
        built = rigs.build_system(cache_dir, collection, mesh_objects=_mesh_objects(manifest, asset_root, collection),
                                  materials=renderer_materials(material_roots(asset_root)))
        built['root']['dr_nx_math'] = 'svml_shim' if shim else 'reference (approximate)'
        targets = _ui_targets()
        if targets is not None:
            try:
                targets.stamp([built['root'], *collection.all_objects], targets.new_import_id(), 'effect', name)
            except Exception as exc:  # noqa: BLE001
                print(f'Arc Raiders effects: could not tag {name}: {exc}')
        scene.frame_end = max(scene.frame_end, manifest['frame_end'])
        kinds = {}
        for r in manifest['renderers']:
            kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
        msg = '%s: %d streams (%s), frames %d-%d, %.1fs%s' % (stem, len(manifest['streams']), ', '.join('%s %d' % kv for kv in sorted(kinds.items())),
                                                              manifest['frame_start'], manifest['frame_end'], time.perf_counter() - t0,
                                                              '' if shim else ', reference math')
        scene['arc_ns_last_report'] = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}, ''

