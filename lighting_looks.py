"""Lighting Look discovery + World HDRI / compositor Kodak LUT apply.

Looks are scanned from Pioneer ``Lighting/LUTs/RGBTable16x1_Kodak5218*.png``.
Optional HDRI / white-temp / exposure pairings live in ``assets/lighting_looks.json``.

UE applies ColorGradingLUT after tonemap (display-referred). The compositor path
encodes scene-linear→sRGB, samples the RGBTable16x1 strip, then converts back, and
switches View Transform to Standard while the LUT is active.

HDRIs: 2:1 studio maps use Environment Equirectangular. Square ``Hemi`` /
``hdri_sky`` / ``*_Sphere`` dumps are polar hemisphere (zenith center) — sampled
in-shader via Image Texture polar UV (not Mirror Ball; equirect bake was black).
Z rotation follows UE ``SourceCubemapAngle`` (~227.5° typical).
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any

import bpy

from . import utils

_LUT_PREFIX = "RGBTable16x1_Kodak5218"
_LUT_RE = re.compile(r"^RGBTable16x1_Kodak5218(?:_(.+))?$", re.IGNORECASE)
_GROUP_NAME = "ArcLightingLook"
_STACK_VERSION = 7  # v7: Kodak via CurveRGB gray-ramp (MapUV strip was black in B5)
# Common Pioneer SkyLight / reflection SourceCubemapAngle (degrees) — not Blender-default Z.
_DEFAULT_HDRI_ROTATION_Z_DEG = 227.52
_PAIRINGS_CACHE: dict[str, Any] | None = None
_ENUM_ITEMS: list[tuple[str, str, str, int]] = [
    ("_NONE_", "(set Pioneer root)", "Point Settings → PioneerGame Folder at the dump", 0)
]
_HDRI_ENUM_ITEMS: list[tuple[str, str, str, int]] = [
    ("_NONE_", "(set Pioneer root)", "Point Settings → PioneerGame Folder at the dump", 0)
]
_HDRI_EXTS = (".hdr", ".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff")


# ---------------------------------------------------------------------------
# Paths / catalog
# ---------------------------------------------------------------------------


def find_lighting_root(pioneer_root: str | None = None) -> str:
    root = (pioneer_root or "").strip() or utils.get_pioneer_root()
    if not root:
        return ""
    return utils.find_relative_dir(root, ["Lighting"])


def lut_cache_dir() -> str:
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or os.path.expanduser("~")
    addon_folder = os.path.basename(os.path.dirname(__file__)) or "DataRaiders-Outfits"
    path = os.path.join(local, addon_folder, "lighting_luts")
    os.makedirs(path, exist_ok=True)
    return path


def _pairings_json_path() -> str:
    return os.path.join(os.path.dirname(__file__), "assets", "lighting_looks.json")


def load_look_pairings(force: bool = False) -> dict[str, Any]:
    global _PAIRINGS_CACHE
    if _PAIRINGS_CACHE is not None and not force:
        return _PAIRINGS_CACHE
    path = _pairings_json_path()
    data: dict[str, Any] = {"looks": {}}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                looks = raw.get("looks")
                if isinstance(looks, dict):
                    data["looks"] = looks
                else:
                    # Allow flat id → entry map
                    data["looks"] = {
                        k: v for k, v in raw.items() if isinstance(v, dict) and k != "format"
                    }
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Arc Lighting Look: failed to read pairings JSON ({path}): {exc}")
    _PAIRINGS_CACHE = data
    return data


def _look_id_from_stem(stem: str) -> str:
    m = _LUT_RE.match(stem)
    if not m:
        return ""
    suffix = (m.group(1) or "").strip()
    return suffix or "Default"


def scan_looks(lighting_root: str | None = None) -> list[dict[str, Any]]:
    """Return look entries discovered under ``Lighting/LUTs``."""
    root = (lighting_root or "").strip() or find_lighting_root()
    if not root or not os.path.isdir(root):
        return []
    lut_dir = os.path.join(root, "LUTs")
    if not os.path.isdir(lut_dir):
        return []
    pairings = load_look_pairings().get("looks") or {}
    items: list[dict[str, Any]] = []
    for name in sorted(os.listdir(lut_dir)):
        if not name.lower().endswith(".png"):
            continue
        stem, _ext = os.path.splitext(name)
        if not stem.upper().startswith(_LUT_PREFIX.upper()):
            continue
        look_id = _look_id_from_stem(stem)
        if not look_id:
            continue
        meta = pairings.get(look_id) if isinstance(pairings.get(look_id), dict) else {}
        hdri_name = str(meta.get("hdri") or "").strip()
        hdri_path = ""
        if hdri_name:
            cand = os.path.join(root, "HDRI", hdri_name)
            if os.path.isfile(cand):
                hdri_path = cand
        items.append(
            {
                "id": look_id,
                "label": look_id.replace("_", " "),
                "lut_path": os.path.join(lut_dir, name),
                "lut_filename": name,
                "hdri_path": hdri_path,
                "hdri_filename": hdri_name,
                "white_temp": float(meta["white_temp"]) if meta.get("white_temp") is not None else None,
                "exposure_ev": float(meta["exposure_ev"]) if meta.get("exposure_ev") is not None else 0.0,
                "hdri_rotation_z": (
                    float(meta["hdri_rotation_z"])
                    if meta.get("hdri_rotation_z") is not None
                    else _DEFAULT_HDRI_ROTATION_Z_DEG
                ),
                "hdri_flip_x": bool(meta.get("hdri_flip_x", False)),
            }
        )
    return items


def get_look(look_id: str, lighting_root: str | None = None) -> dict[str, Any] | None:
    want = (look_id or "").strip()
    if not want or want == "_NONE_":
        return None
    for item in scan_looks(lighting_root):
        if item["id"] == want:
            return item
    return None


def scan_hdris(lighting_root: str | None = None) -> list[dict[str, str]]:
    """List HDRI files under ``Lighting/HDRI`` (filenames only)."""
    root = (lighting_root or "").strip() or find_lighting_root()
    if not root:
        return []
    hdri_dir = os.path.join(root, "HDRI")
    if not os.path.isdir(hdri_dir):
        return []
    items: list[dict[str, str]] = []
    for name in sorted(os.listdir(hdri_dir)):
        low = name.lower()
        if not low.endswith(_HDRI_EXTS):
            continue
        if "_equirect." in low:
            continue
        path = os.path.join(hdri_dir, name)
        if os.path.isfile(path):
            items.append({"id": name, "label": name, "path": path})
    return items


def resolve_hdri_path(hdri_id: str, lighting_root: str | None = None) -> str:
    """Resolve enum id / filename to an absolute path under Lighting/HDRI."""
    want = (hdri_id or "").strip()
    if not want or want == "_NONE_":
        return ""
    root = (lighting_root or "").strip() or find_lighting_root()
    if not root:
        return ""
    # Already absolute?
    if os.path.isfile(want):
        return want
    cand = os.path.join(root, "HDRI", want)
    return cand if os.path.isfile(cand) else ""


def make_lighting_look_items(self, context):
    """Dynamic EnumProperty callback — keep a stable list object for RNA."""
    del self  # unused (Blender EnumProperty signature)
    root = ""
    if context and getattr(context, "scene", None):
        root = getattr(context.scene, "arc_pioneer_root", "") or ""
    lighting = find_lighting_root(root) if root else find_lighting_root()
    looks = scan_looks(lighting) if lighting else []
    items: list[tuple[str, str, str, int]] = []
    if not looks:
        items.append(
            (
                "_NONE_",
                "(no Kodak LUTs found)",
                "Set PioneerGame Folder so Lighting/LUTs/RGBTable16x1_Kodak5218*.png resolve",
                0,
            )
        )
    else:
        for i, look in enumerate(looks):
            tip = look["lut_filename"]
            if look.get("hdri_filename"):
                tip = f"{tip} + {look['hdri_filename']}"
            items.append((look["id"], look["label"], tip, i))
    _ENUM_ITEMS[:] = items
    return _ENUM_ITEMS


def make_lighting_hdri_items(self, context):
    """Dynamic EnumProperty for Lighting/HDRI files."""
    del self
    root = ""
    if context and getattr(context, "scene", None):
        root = getattr(context.scene, "arc_pioneer_root", "") or ""
    lighting = find_lighting_root(root) if root else find_lighting_root()
    hdris = scan_hdris(lighting) if lighting else []
    items: list[tuple[str, str, str, int]] = [
        ("_NONE_", "(none)", "No World HDRI", 0),
    ]
    for i, h in enumerate(hdris):
        items.append((h["id"], h["label"], h["path"], i + 1))
    _HDRI_ENUM_ITEMS[:] = items
    return _HDRI_ENUM_ITEMS


# ---------------------------------------------------------------------------
# UE RGBTable16x1 → IRIDAS .cube
# ---------------------------------------------------------------------------


def _load_png_rgba_pixels(png_path: str) -> tuple[int, int, list[float]]:
    """Return (width, height, flat RGBA floats 0–1) via Blender image load."""
    img = bpy.data.images.load(png_path, check_existing=True)
    try:
        img.reload()
    except Exception:
        pass
    w, h = int(img.size[0]), int(img.size[1])
    # Copy so we can remove ephemeral loads safely later if desired
    pixels = list(img.pixels)
    return w, h, pixels


def ue_rgbtable16x1_to_cube(png_path: str, cube_path: str, size: int = 16) -> str:
    """Decode UE RGBTable16x1 strip PNG into an IRIDAS ``.cube`` LUT.

    Layout: width = size*size, height = size. Blue selects the horizontal
    16×16 tile; within a tile X=R, Y=G. Cube order: R slowest, B fastest.
    """
    if not os.path.isfile(png_path):
        raise FileNotFoundError(png_path)
    w, h, pixels = _load_png_rgba_pixels(png_path)
    expect_w, expect_h = size * size, size
    if w != expect_w or h != expect_h:
        raise ValueError(
            f"Expected RGBTable{size}x1 strip {expect_w}x{expect_h}, got {w}x{h} ({png_path})"
        )

    def sample(r: int, g: int, b: int) -> tuple[float, float, float]:
        x = r + b * size
        y = g
        # Blender pixels: bottom-up rows
        row = (h - 1 - y) * w + x
        i = row * 4
        return pixels[i], pixels[i + 1], pixels[i + 2]

    os.makedirs(os.path.dirname(cube_path) or ".", exist_ok=True)
    title = os.path.splitext(os.path.basename(png_path))[0]
    lines = [
        f'TITLE "{title}"',
        f"LUT_3D_SIZE {size}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    for r in range(size):
        for g in range(size):
            for b in range(size):
                rr, gg, bb = sample(r, g, b)
                lines.append(f"{rr:.6f} {gg:.6f} {bb:.6f}")
    with open(cube_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return cube_path


def ensure_cube_for_lut(lut_png_path: str) -> str:
    """Convert LUT PNG to cached ``.cube`` (skip rebuild when newer cache exists)."""
    base = os.path.splitext(os.path.basename(lut_png_path))[0]
    cube_path = os.path.join(lut_cache_dir(), f"{base}.cube")
    try:
        src_mtime = os.path.getmtime(lut_png_path)
        if os.path.isfile(cube_path) and os.path.getmtime(cube_path) >= src_mtime:
            return cube_path
    except OSError:
        pass
    return ue_rgbtable16x1_to_cube(lut_png_path, cube_path)


# ---------------------------------------------------------------------------
# Apply — World HDRI + compositor strip LUT
# ---------------------------------------------------------------------------


def _kelvin_to_rgb(kelvin: float) -> tuple[float, float, float]:
    """Approximate Planckian locus RGB (sRGB-ish) for World tint."""
    k = max(1000.0, min(40000.0, float(kelvin))) / 100.0
    if k <= 66.0:
        r = 1.0
        g = max(0.0, min(1.0, (99.4708025861 * math.log(k) - 161.1195681661) / 255.0))
        b = 0.0 if k <= 19.0 else max(
            0.0,
            min(1.0, (138.5177312231 * math.log(k - 10.0) - 305.0447927307) / 255.0),
        )
    else:
        r = max(0.0, min(1.0, (329.698727446 * ((k - 60.0) ** -0.1332047592)) / 255.0))
        g = max(0.0, min(1.0, (288.1221695283 * ((k - 60.0) ** -0.0755148492)) / 255.0))
        b = 1.0
    return r, g, b


def _find_node(nodes, *, type_name: str = "", label: str = "", name: str = ""):
    for node in nodes:
        if label and node.label == label:
            return node
        if name and node.name == name:
            return node
        if type_name and node.bl_idname == type_name:
            return node
    return None


def _clamp(nodes, loc, *, label: str = ""):
    n = nodes.new("ShaderNodeClamp")
    n.location = loc
    if label:
        n.label = label
    n.inputs["Min"].default_value = 0.0
    n.inputs["Max"].default_value = 1.0
    return n


def hdri_cache_dir() -> str:
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or os.path.expanduser("~")
    addon_folder = os.path.basename(os.path.dirname(__file__)) or "DataRaiders-Outfits"
    path = os.path.join(local, addon_folder, "lighting_hdri")
    os.makedirs(path, exist_ok=True)
    return path


def _is_polar_hemi_hdri(hdri_path: str, img: bpy.types.Image | None = None) -> bool:
    """True for Pioneer square polar-hemisphere skies (zenith at center, horizon at rim).

    These are NOT Blender Mirror Ball (full-sphere angular) and NOT 2:1 equirect.
    Using either as-is stretches clouds and puts poles on the horizon.
    """
    name = os.path.basename(hdri_path or "").lower()
    if "studio_small" in name or "starmap" in name:
        return False
    w = h = 0
    if img is not None:
        try:
            w, h = int(img.size[0]), int(img.size[1])
        except Exception:
            w = h = 0
    if w and h:
        ratio = float(w) / float(max(h, 1))
        if abs(ratio - 2.0) < 0.25:
            return False
        if abs(ratio - 1.0) < 0.2:
            return True
    return any(k in name for k in ("hemi", "sphere", "hdri_sky", "vhri", "vhdri"))


def _sample_rgba(pixels: list[float], w: int, h: int, u: float, v: float) -> tuple[float, float, float, float]:
    """Nearest sample; u/v in 0–1, v=0 at bottom (Blender pixel layout)."""
    x = int(max(0, min(w - 1, round(u * (w - 1)))))
    y = int(max(0, min(h - 1, round(v * (h - 1)))))
    i = (y * w + x) * 4
    return pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3]


def polar_hemi_to_equirect(
    src_path: str,
    dst_path: str,
    *,
    height: int = 384,
) -> str:
    """Bake polar-hemisphere sky (zenith center) → 2:1 equirectangular EXR.

    Upper hemisphere samples the disk; below horizon repeats the rim (no fake ground plate).
    """
    import array

    img = bpy.data.images.load(src_path, check_existing=True)
    try:
        img.reload()
    except Exception:
        pass
    sw, sh = int(img.size[0]), int(img.size[1])
    n = sw * sh * 4
    src_buf = array.array("f", [0.0] * n)
    img.pixels.foreach_get(src_buf)
    pixels = src_buf  # indexable
    dw = height * 2
    dh = height
    out = array.array("f", [0.0] * (dw * dh * 4))
    rim_acc = [0.0, 0.0, 0.0]
    rim_n = 0
    for i in range(32):
        ang = (i / 32.0) * math.tau
        u = 0.5 + 0.5 * 0.98 * math.cos(ang)
        v = 0.5 + 0.5 * 0.98 * math.sin(ang)
        r, g, b, _a = _sample_rgba(pixels, sw, sh, u, v)
        rim_acc[0] += r
        rim_acc[1] += g
        rim_acc[2] += b
        rim_n += 1
    rim = tuple(c / max(rim_n, 1) for c in rim_acc)

    inv_dh = 1.0 / dh
    inv_dw = 1.0 / dw
    half_pi = math.pi * 0.5
    for y in range(dh):
        lat = ((y + 0.5) * inv_dh - 0.5) * math.pi
        cos_lat = math.cos(lat)
        sin_lat = math.sin(lat)
        row = y * dw * 4
        for x in range(dw):
            lon = ((x + 0.5) * inv_dw) * math.tau - math.pi
            dz = sin_lat
            i = row + x * 4
            if dz <= 1e-6:
                out[i] = rim[0]
                out[i + 1] = rim[1]
                out[i + 2] = rim[2]
                out[i + 3] = 1.0
                continue
            r = math.acos(max(-1.0, min(1.0, dz))) / half_pi
            if r > 1.0:
                out[i] = rim[0]
                out[i + 1] = rim[1]
                out[i + 2] = rim[2]
                out[i + 3] = 1.0
                continue
            dx = cos_lat * math.cos(lon)
            dy = cos_lat * math.sin(lon)
            theta = math.atan2(dy, dx)
            su = 0.5 + 0.5 * r * math.cos(theta)
            sv = 0.5 + 0.5 * r * math.sin(theta)
            rr, gg, bb, aa = _sample_rgba(pixels, sw, sh, su, sv)
            out[i] = rr
            out[i + 1] = gg
            out[i + 2] = bb
            out[i + 3] = 1.0 if aa <= 0.0 else aa

    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    name = os.path.splitext(os.path.basename(dst_path))[0]
    old = bpy.data.images.get(name)
    if old is not None:
        bpy.data.images.remove(old)
    baked = bpy.data.images.new(name=name, width=dw, height=dh, alpha=True, float_buffer=True)
    baked.pixels.foreach_set(out)
    baked.update()
    baked.filepath_raw = dst_path
    baked.file_format = "OPEN_EXR" if dst_path.lower().endswith(".exr") else "HDR"
    try:
        baked.colorspace_settings.name = "Linear Rec.709"
    except Exception:
        pass
    baked.save()
    bpy.data.images.remove(baked)
    return dst_path


def _shader_math(nodes, operation: str, loc, *, label: str = "", value: float | None = None):
    n = nodes.new("ShaderNodeMath")
    n.operation = operation
    n.location = loc
    if label:
        n.label = label
    if value is not None and len(n.inputs) > 1:
        n.inputs[1].default_value = value
    return n


def _clear_polar_hdri_nodes(nodes) -> None:
    for n in list(nodes):
        if n.label.startswith("ArcLightingPolar") or n.label == "ArcLightingHemiImage":
            nodes.remove(n)


def _ensure_polar_uv_nodes(nodes, links, mapping):
    """Mapping.Vector (world dir) → polar UV for zenith-center hemisphere textures."""
    # Always rebuild — partial graphs from prior applies were a common failure mode.
    for n in list(nodes):
        if n.label.startswith("ArcLightingPolar"):
            nodes.remove(n)

    x0, y0 = 0, -320
    norm = nodes.new("ShaderNodeVectorMath")
    norm.operation = "NORMALIZE"
    norm.label = "ArcLightingPolarNorm"
    norm.location = (x0, y0)

    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.label = "ArcLightingPolarSep"
    sep.location = (x0 + 180, y0)

    acos = _shader_math(nodes, "ARCCOSINE", (x0 + 360, y0 + 80), label="ArcLightingPolarAcos")
    # r = acos(z) / (pi/2) = acos(z) * 2/pi ; clamp to [0,1] so below-horizon hits rim
    r = _shader_math(
        nodes, "MULTIPLY", (x0 + 540, y0 + 80), label="ArcLightingPolarR", value=2.0 / math.pi
    )
    rclamp = _shader_math(
        nodes, "MINIMUM", (x0 + 720, y0 + 80), label="ArcLightingPolarRClamp", value=1.0
    )
    atan = _shader_math(nodes, "ARCTAN2", (x0 + 360, y0 - 80), label="ArcLightingPolarAtan")
    cos_t = _shader_math(nodes, "COSINE", (x0 + 540, y0 - 40), label="ArcLightingPolarCos")
    sin_t = _shader_math(nodes, "SINE", (x0 + 540, y0 - 120), label="ArcLightingPolarSin")
    rcos = _shader_math(nodes, "MULTIPLY", (x0 + 720, y0 - 40), label="ArcLightingPolarRCos")
    rsin = _shader_math(nodes, "MULTIPLY", (x0 + 720, y0 - 120), label="ArcLightingPolarRSin")
    u_scale = _shader_math(
        nodes, "MULTIPLY", (x0 + 900, y0 + 40), label="ArcLightingPolarUScale", value=0.5
    )
    v_scale = _shader_math(
        nodes, "MULTIPLY", (x0 + 900, y0 - 160), label="ArcLightingPolarVScale", value=0.5
    )
    u = _shader_math(nodes, "ADD", (x0 + 1080, y0 - 40), label="ArcLightingPolarU", value=0.5)
    v = _shader_math(nodes, "ADD", (x0 + 1080, y0 - 120), label="ArcLightingPolarV", value=0.5)
    comb = nodes.new("ShaderNodeCombineXYZ")
    comb.label = "ArcLightingPolarUV"
    comb.location = (x0 + 1260, y0 - 40)

    links.new(mapping.outputs["Vector"], norm.inputs[0])
    norm_out = norm.outputs.get("Vector") or norm.outputs[0]
    links.new(norm_out, sep.inputs["Vector"])
    links.new(sep.outputs["Z"], acos.inputs[0])
    links.new(acos.outputs[0], r.inputs[0])
    links.new(r.outputs[0], rclamp.inputs[0])
    # Blender ARCTAN2: Value=Y, Value_001=X
    links.new(sep.outputs["Y"], atan.inputs[0])
    links.new(sep.outputs["X"], atan.inputs[1])
    links.new(atan.outputs[0], cos_t.inputs[0])
    links.new(atan.outputs[0], sin_t.inputs[0])
    links.new(rclamp.outputs[0], rcos.inputs[0])
    links.new(cos_t.outputs[0], rcos.inputs[1])
    links.new(rclamp.outputs[0], rsin.inputs[0])
    links.new(sin_t.outputs[0], rsin.inputs[1])
    links.new(rcos.outputs[0], u_scale.inputs[0])
    links.new(rsin.outputs[0], v_scale.inputs[0])
    links.new(u_scale.outputs[0], u.inputs[0])
    links.new(v_scale.outputs[0], v.inputs[0])
    links.new(u.outputs[0], comb.inputs["X"])
    links.new(v.outputs[0], comb.inputs["Y"])
    return comb


def _apply_ue_color_management(scene: bpy.types.Scene, *, enable_lut: bool) -> str:
    """Match UE PostProcess ColorGradingLUT domain: display-referred, not AgX HDR.

    Kodak RGBTable LUTs are applied after the UE tonemapper. AgX + linear LUT UVs
    clip to black. Switch to Standard while LUT is active.
    """
    vs = scene.view_settings
    ds = scene.display_settings
    try:
        scene["arc_lighting_cm_view"] = vs.view_transform
        scene["arc_lighting_cm_look"] = vs.look
    except Exception:
        pass
    if enable_lut:
        try:
            vs.view_transform = "Standard"
        except Exception:
            try:
                vs.view_transform = "sRGB"
            except Exception:
                pass
        try:
            vs.look = "None"
        except Exception:
            pass
        try:
            ds.display_device = "sRGB"
        except Exception:
            pass
        return "CM=Standard"
    return "CM=unchanged"


def _ensure_world_hdri(
    scene: bpy.types.Scene,
    hdri_path: str,
    strength: float,
    white_temp: float | None,
    *,
    rotation_z_deg: float = _DEFAULT_HDRI_ROTATION_Z_DEG,
    flip_x: bool = False,
) -> str:
    """Load World HDRI. True 2:1 equirect → Environment Texture; square polar hemi → Image Texture."""
    del white_temp  # view white-balance applied by caller when set on the look
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nodes, links = nt.nodes, nt.links

    out = _find_node(nodes, type_name="ShaderNodeOutputWorld") or nodes.new("ShaderNodeOutputWorld")
    out.location = (1600, 0)
    bg = _find_node(nodes, label="ArcLightingBackground") or _find_node(
        nodes, type_name="ShaderNodeBackground"
    )
    if bg is None:
        bg = nodes.new("ShaderNodeBackground")
    bg.label = "ArcLightingBackground"
    bg.location = (1400, 0)

    texcoord = _find_node(nodes, label="ArcLightingHDRICoord")
    if texcoord is None:
        texcoord = nodes.new("ShaderNodeTexCoord")
        texcoord.label = "ArcLightingHDRICoord"
    texcoord.location = (-400, 0)

    mapping = _find_node(nodes, label="ArcLightingHDRIMapping")
    if mapping is None:
        mapping = nodes.new("ShaderNodeMapping")
        mapping.label = "ArcLightingHDRIMapping"
    mapping.location = (-200, 0)
    mapping.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(float(rotation_z_deg)))
    mapping.inputs["Scale"].default_value = (-1.0 if flip_x else 1.0, 1.0, 1.0)

    env = _find_node(nodes, label="ArcLightingHDRI")
    if env is None:
        env = nodes.new("ShaderNodeTexEnvironment")
        env.label = "ArcLightingHDRI"
    env.location = (200, 120)

    use_polar = False
    status = "(no HDRI)"
    img = None
    if hdri_path and os.path.isfile(hdri_path):
        # Skip leftover black equirect bake files from the failed cache path
        base = os.path.basename(hdri_path).lower()
        if "_equirect." in base:
            status = f"{os.path.basename(hdri_path)} [ignored bad cache]"
        else:
            img = bpy.data.images.load(hdri_path, check_existing=True)
            try:
                img.reload()
            except Exception:
                pass
            use_polar = _is_polar_hemi_hdri(hdri_path, img)
            if hdri_path.lower().endswith((".hdr", ".exr")):
                try:
                    img.colorspace_settings.name = "Linear Rec.709"
                except Exception:
                    pass
            mode = "polar-hemi" if use_polar else "equirect"
            status = f"{os.path.basename(hdri_path)} [{mode} Z={rotation_z_deg:.1f}°]"

    bg.inputs["Strength"].default_value = float(strength)
    bg.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)

    # Detach prior surface / vector links we own
    sockets_to_clear = [
        mapping.inputs["Vector"],
        env.inputs["Vector"],
        bg.inputs["Color"],
        out.inputs["Surface"],
    ]
    while True:
        victim = None
        for link in list(world.node_tree.links):
            if link.to_socket in sockets_to_clear:
                victim = link
                break
            if link.to_node is not None and (
                link.to_node.label.startswith("ArcLightingPolar")
                or link.to_node.label == "ArcLightingHemiImage"
            ):
                victim = link
                break
        if victim is None:
            break
        world.node_tree.links.remove(victim)

    links.new(texcoord.outputs["Generated"], mapping.inputs["Vector"])

    if img is None:
        _clear_polar_hdri_nodes(nodes)
        env.image = None
        env.mute = True
        links.new(bg.outputs["Background"], out.inputs["Surface"])
        return status

    if use_polar:
        env.image = None
        env.mute = True
        _clear_polar_hdri_nodes(nodes)
        comb = _ensure_polar_uv_nodes(nodes, links, mapping)
        img_tex = nodes.new("ShaderNodeTexImage")
        img_tex.label = "ArcLightingHemiImage"
        img_tex.location = (200, -200)
        img_tex.image = img
        try:
            img_tex.extension = "EXTEND"
        except Exception:
            pass
        try:
            img_tex.interpolation = "Smart"
        except Exception:
            pass
        links.new(comb.outputs["Vector"], img_tex.inputs["Vector"])
        links.new(img_tex.outputs["Color"], bg.inputs["Color"])
    else:
        _clear_polar_hdri_nodes(nodes)
        env.mute = False
        env.image = img
        try:
            env.projection = "EQUIRECTANGULAR"
        except Exception:
            pass
        links.new(mapping.outputs["Vector"], env.inputs["Vector"])
        links.new(env.outputs["Color"], bg.inputs["Color"])

    links.new(bg.outputs["Background"], out.inputs["Surface"])
    _clear_world_volume_socket(world)
    return status

_FOG_OBJ_NAME = "ArcLightingFogVolume"
_FOG_MAT_NAME = "ArcLightingFogVolume"
# Defaults for fog box full size (Blender units). World Volume is never used —
# infinite path length blacks the HDRI at any density > 0.
_FOG_SIZE_DEFAULT = (2000.0, 2000.0, 500.0)


def _fog_box_sizes(scene: bpy.types.Scene) -> tuple[float, float, float]:
    """Full XYZ extents of ``ArcLightingFogVolume`` (not half-extents)."""
    sx = float(getattr(scene, "arc_lighting_fog_size_x", _FOG_SIZE_DEFAULT[0]) or _FOG_SIZE_DEFAULT[0])
    sy = float(getattr(scene, "arc_lighting_fog_size_y", _FOG_SIZE_DEFAULT[1]) or _FOG_SIZE_DEFAULT[1])
    sz = float(getattr(scene, "arc_lighting_fog_size_z", _FOG_SIZE_DEFAULT[2]) or _FOG_SIZE_DEFAULT[2])
    return (max(0.1, sx), max(0.1, sy), max(0.1, sz))


def _fog_volume_density(atmos: dict[str, Any], *, path_length: float) -> float:
    """Map UE ExponentialHeightFog density → Principled Volume density on a finite box.

    Target optical depth across the box ≈ fog_density * 4 (UE 0.2 → OD≈0.8).
    """
    density = float(atmos.get("fog_density") or 0.0)
    path = max(float(path_length), 1.0)
    od = max(0.0, density) * 4.0
    return max(0.0, min(0.05, od / path))


def _clear_world_volume_socket(world: bpy.types.World) -> None:
    """Detach World Volume — infinite volumes black out Environment/HDRI."""
    if world is None or not getattr(world, "use_nodes", False) or world.node_tree is None:
        return
    out = _find_node(world.node_tree.nodes, type_name="ShaderNodeOutputWorld")
    if out is None or "Volume" not in out.inputs:
        return
    vol_sock = out.inputs["Volume"]
    while True:
        victim = None
        for link in world.node_tree.links:
            if link.to_socket == vol_sock:
                victim = link
                break
        if victim is None:
            break
        world.node_tree.links.remove(victim)
    vol = _find_node(world.node_tree.nodes, label="ArcLightingVolumeFog")
    if vol is not None:
        vol.mute = True
        if "Density" in vol.inputs:
            vol.inputs["Density"].default_value = 0.0


def _ensure_fog_volume_material(color: tuple[float, float, float, float], dens: float) -> bpy.types.Material:
    mat = bpy.data.materials.get(_FOG_MAT_NAME)
    if mat is None:
        mat = bpy.data.materials.new(_FOG_MAT_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    vol = nodes.new("ShaderNodeVolumePrincipled")
    vol.label = "ArcLightingVolumeFog"
    vol.location = (0, 0)
    if "Color" in vol.inputs:
        vol.inputs["Color"].default_value = color
    if "Density" in vol.inputs:
        vol.inputs["Density"].default_value = dens
    if "Anisotropy" in vol.inputs:
        vol.inputs["Anisotropy"].default_value = 0.0
    # No Surface link — mesh is invisible; only volume scatters.
    links.new(vol.outputs["Volume"], out.inputs["Volume"])
    return mat


def _tune_eevee_volumes(scene: bpy.types.Scene, *, max_extent: float) -> None:
    """Raise EEVEE volume end distance so the fog box is sampled."""
    eevee = getattr(scene, "eevee", None)
    if eevee is None:
        return
    end = max(50.0, float(max_extent) * 1.5)
    for attr, value in (
        ("volumetric_end", end),
        ("volume_end", end),
        ("volumetric_sample_distribution", 0.8),
    ):
        if hasattr(eevee, attr):
            try:
                setattr(eevee, attr, value)
            except Exception:
                pass
    if hasattr(eevee, "use_volumetric_shadows"):
        try:
            eevee.use_volumetric_shadows = True
        except Exception:
            pass


def _ensure_world_volume_fog(
    scene: bpy.types.Scene,
    atmos: dict[str, Any],
    *,
    enabled: bool,
) -> str:
    """Apply ExponentialHeightFog as a sized Principled Volume cube (not World Volume).

    World Volume + any density>0 blacks the HDRI (infinite optical depth). Size comes from
    ``arc_lighting_fog_size_{x,y,z}`` (full extents in Blender units).
    """
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        scene.world = world
    world.use_nodes = True
    _clear_world_volume_socket(world)
    try:
        world.mist_settings.use_mist = False
    except Exception:
        pass

    size_x, size_y, size_z = _fog_box_sizes(scene)
    # Unit cube is -1..1 on each axis → scale = half-extent
    scale = (size_x * 0.5, size_y * 0.5, size_z * 0.5)
    path_len = max(size_x, size_y, size_z)

    obj = bpy.data.objects.get(_FOG_OBJ_NAME)
    if not enabled:
        if obj is not None:
            obj.hide_viewport = True
            obj.hide_render = True
        return "fog:off"

    fc = atmos.get("fog_color") or (0.75, 0.82, 0.9)
    color = (float(fc[0]), float(fc[1]), float(fc[2]), 1.0)
    dens = _fog_volume_density(atmos, path_length=path_len)
    mat = _ensure_fog_volume_material(color, dens)

    if obj is None:
        mesh = bpy.data.meshes.get(_FOG_OBJ_NAME)
        if mesh is None:
            mesh = bpy.data.meshes.new(_FOG_OBJ_NAME)
            verts = [
                (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
            ]
            faces = [
                (0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
                (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
            ]
            mesh.from_pydata(verts, [], faces)
            mesh.update()
        obj = bpy.data.objects.new(_FOG_OBJ_NAME, mesh)
        scene.collection.objects.link(obj)
    obj.hide_viewport = False
    obj.hide_render = False
    obj.display_type = "WIRE"
    obj.show_wire = True
    try:
        obj.visible_camera = True
        obj.visible_diffuse = True
        obj.visible_glossy = True
        obj.visible_transmission = True
        obj.visible_volume_scatter = True
        obj.visible_shadow = False
    except Exception:
        pass
    # Center on origin in XY; lift so the box sits on Z=0.
    obj.location = (0.0, 0.0, size_z * 0.5)
    obj.scale = scale
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

    _tune_eevee_volumes(scene, max_extent=path_len)
    return f"fog:box dens={dens:.5f} size={size_x:.0f}x{size_y:.0f}x{size_z:.0f}"



def _math(nodes, operation: str, loc, *, value: float | None = None, label: str = ""):
    n = nodes.new("ShaderNodeMath")
    n.operation = operation
    n.location = loc
    if label:
        n.label = label
    if value is not None:
        n.inputs[1].default_value = value
    return n


def _group_output_socket(go):
    out_in = go.inputs[0]
    for sock in go.inputs:
        if sock.name == "Image" or "Socket" in sock.identifier:
            out_in = sock
            break
    return out_in


def _set_fac(node, value: float) -> None:
    if node is None:
        return
    fac = max(0.0, min(1.0, float(value)))
    sock = node.inputs.get("Fac") or node.inputs.get("Factor_Float") or node.inputs.get("Factor")
    if sock is not None:
        sock.default_value = fac
        return
    for candidate in node.inputs:
        if candidate.identifier in ("Fac", "Factor_Float", "Factor") or candidate.name in (
            "Fac",
            "Factor",
        ):
            candidate.default_value = fac
            return


def _enable_viewport_compositor() -> None:
    """Turn on viewport compositor so Kodak LUT is visible without F12."""
    try:
        for window in bpy.context.window_manager.windows:
            screen = window.screen
            if screen is None:
                continue
            for area in screen.areas:
                if area.type != "VIEW_3D":
                    continue
                for space in area.spaces:
                    if space.type == "VIEW_3D" and hasattr(space.shading, "use_compositor"):
                        # ALWAYS: Material + Rendered previews; CAMERA only when looking through camera.
                        space.shading.use_compositor = "ALWAYS"
    except Exception as exc:
        print(f"Arc Lighting Look: could not enable viewport compositor ({exc})")


def _enable_scene_world() -> None:
    """Toggle viewport Scene World so World HDRI is visible in Material/Rendered."""
    try:
        for window in bpy.context.window_manager.windows:
            screen = window.screen
            if screen is None:
                continue
            for area in screen.areas:
                if area.type != "VIEW_3D":
                    continue
                for space in area.spaces:
                    if space.type != "VIEW_3D":
                        continue
                    shading = space.shading
                    if hasattr(shading, "use_scene_world"):
                        shading.use_scene_world = True
    except Exception as exc:
        print(f"Arc Lighting Look: could not enable Scene World ({exc})")


def sync_sidebar_from_scenario(scene: bpy.types.Scene) -> str:
    """Update LUT Look + HDRI File enums from Map/Scenario (no Apply). Skips if Override.

    Returns a short status string, or empty if nothing changed.
    """
    from . import lighting_atmosphere as latm

    if bool(getattr(scene, "arc_lighting_override", False)):
        return ""
    map_id = getattr(scene, "arc_lighting_map", "") or ""
    if map_id in ("", "_NONE_") and getattr(scene, "arc_placement_map", ""):
        map_id = scene.arc_placement_map
    scenario = getattr(scene, "arc_lighting_scenario", "") or ""
    if not map_id or map_id == "_NONE_" or not scenario or scenario == "_NONE_":
        return ""

    pioneer = getattr(scene, "arc_pioneer_root", "") or utils.get_pioneer_root()
    lighting = find_lighting_root(pioneer) if pioneer else find_lighting_root()
    atmos: dict[str, Any] = {}
    if pioneer:
        try:
            atmos = latm.resolve_atmosphere(map_id, scenario, pioneer)
        except Exception:
            atmos = {}

    suggested = ""
    cgi_lut = atmos.get("color_grading_lut") if atmos else None
    if cgi_lut:
        suggested = latm.look_id_from_color_grading_lut(str(cgi_lut))
    if not suggested:
        suggested = latm.suggest_lut_for_tag(scenario)

    parts: list[str] = []
    if suggested and hasattr(scene, "arc_lighting_look"):
        try:
            if getattr(scene, "arc_lighting_look", "") != suggested:
                scene.arc_lighting_look = suggested
            parts.append(suggested)
        except Exception:
            pass

    hdri_pick = latm.suggest_hdri_for_tag(scenario)
    if not hdri_pick and suggested and lighting:
        look_tmp = get_look(suggested, lighting)
        if look_tmp and look_tmp.get("hdri_filename"):
            hdri_pick = look_tmp["hdri_filename"]
    if hdri_pick and hasattr(scene, "arc_lighting_hdri"):
        try:
            if getattr(scene, "arc_lighting_hdri", "") != hdri_pick:
                scene.arc_lighting_hdri = hdri_pick
            parts.append(hdri_pick)
        except Exception:
            pass
    return " · ".join(parts)


def on_lighting_scenario_update(scene, context) -> None:
    """EnumProperty update: refresh sidebar LUT/HDRI when Scenario changes."""
    del context
    try:
        sync_sidebar_from_scenario(scene)
    except Exception as exc:
        print(f"Arc Lighting Look: scenario sync failed ({exc})")


def on_lighting_map_update(scene, context) -> None:
    """EnumProperty update: when Map changes, sync if a Scenario is already selected."""
    del context
    try:
        sync_sidebar_from_scenario(scene)
    except Exception as exc:
        print(f"Arc Lighting Look: map sync failed ({exc})")


def sample_lut_gray_ramp(png_path: str, size: int = 16) -> tuple[list[tuple[float, float]], list[tuple[float, float]], list[tuple[float, float]]]:
    """Sample RGBTable16x1 along the gray diagonal (i,i,i) → per-channel curve points.

    Full 3D MapUV sampling goes black in Blender 5's viewport compositor.
    Film LUTs are largely a tone curve + mild channel split — gray-axis curves are a
    stable viewport stand-in.
    """
    w, h, pixels = _load_png_rgba_pixels(png_path)
    expect_w, expect_h = size * size, size
    if w != expect_w or h != expect_h:
        raise ValueError(
            f"Expected RGBTable{size}x1 strip {expect_w}x{expect_h}, got {w}x{h}"
        )
    r_pts: list[tuple[float, float]] = []
    g_pts: list[tuple[float, float]] = []
    b_pts: list[tuple[float, float]] = []
    denom = float(max(size - 1, 1))
    for i in range(size):
        x = i + i * size
        y = i
        row = (h - 1 - y) * w + x
        idx = row * 4
        t = i / denom
        r_pts.append((t, float(pixels[idx])))
        g_pts.append((t, float(pixels[idx + 1])))
        b_pts.append((t, float(pixels[idx + 2])))
    return r_pts, g_pts, b_pts


def _set_rgb_curve_points(curve_node, r_pts, g_pts, b_pts) -> None:
    """Write gray-ramp points into CompositorNodeCurveRGB (R/G/B curves)."""
    mapping = getattr(curve_node, "mapping", None)
    if mapping is None:
        return
    # curves[0]=C (combined), [1]=R, [2]=G, [3]=B
    for idx, pts in ((1, r_pts), (2, g_pts), (3, b_pts)):
        if idx >= len(mapping.curves) or len(pts) < 2:
            continue
        curve = mapping.curves[idx]
        while len(curve.points) > 2:
            try:
                curve.points.remove(curve.points[1])
            except Exception:
                break
        curve.points[0].location = (float(pts[0][0]), float(pts[0][1]))
        curve.points[1].location = (float(pts[-1][0]), float(pts[-1][1]))
        for t, v in pts[1:-1]:
            try:
                curve.points.new(float(t), float(v))
            except Exception:
                pass
    try:
        mapping.update()
    except Exception:
        pass


def _ensure_compositor_stack(
    scene: bpy.types.Scene,
    *,
    lut_png_path: str = "",
    lut_strength: float = 1.0,
    atmos: dict[str, Any] | None = None,
    apply_lut: bool = True,
    apply_bloom: bool = False,
    size: int = 16,
) -> None:
    """Build/reuse ``ArcLightingLook``: RL → bloom → Kodak CurveRGB → Mix → out.

    Blender 5 viewport compositor blacks out MapUV+ShaderNodeMath strip sampling.
    Gray-axis RGB Curves from the Kodak RGBTable are used instead (strength-mixable).
    """
    atmos = atmos or {}
    nt = bpy.data.node_groups.get(_GROUP_NAME)
    if nt is None or nt.bl_idname != "CompositorNodeTree":
        if nt is not None:
            bpy.data.node_groups.remove(nt)
        nt = bpy.data.node_groups.new(_GROUP_NAME, "CompositorNodeTree")

    out_sock = None
    for item in nt.interface.items_tree:
        if getattr(item, "in_out", "") == "OUTPUT" and getattr(item, "socket_type", "") == "NodeSocketColor":
            out_sock = item
            break
    if out_sock is None:
        nt.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")

    nodes, links = nt.nodes, nt.links
    ver = int(nt.get("arc_stack_version", 0) or 0)
    need_build = (
        ver < _STACK_VERSION
        or _find_node(nodes, label="ArcLightingRenderLayers") is None
        or _find_node(nodes, label="ArcLightingLutMix") is None
        or _find_node(nodes, label="ArcLightingLutCurves") is None
        or _find_node(nodes, label="ArcLightingBloom") is None
    )
    if need_build:
        nodes.clear()
        x0 = -400
        rl = nodes.new("CompositorNodeRLayers")
        rl.label = "ArcLightingRenderLayers"
        rl.location = (x0, 0)

        bloom = nodes.new("CompositorNodeGlare")
        bloom.label = "ArcLightingBloom"
        bloom.location = (x0 + 260, 0)
        try:
            bloom.inputs["Type"].default_value = "Fog Glow"
        except Exception:
            pass

        curves = nodes.new("CompositorNodeCurveRGB")
        curves.label = "ArcLightingLutCurves"
        curves.location = (x0 + 520, 80)

        lut_mix = nodes.new("ShaderNodeMix")
        lut_mix.label = "ArcLightingLutMix"
        lut_mix.location = (x0 + 780, 0)
        try:
            lut_mix.data_type = "RGBA"
            lut_mix.clamp_factor = True
        except Exception:
            pass
        _set_fac(lut_mix, 1.0)

        go = nodes.new("NodeGroupOutput")
        go.location = (x0 + 1040, 0)

        viewer = nodes.new("CompositorNodeViewer")
        viewer.label = "ArcLightingViewer"
        viewer.location = (x0 + 1040, -160)

        links.new(rl.outputs["Image"], bloom.inputs["Image"])
        curve_in = curves.inputs.get("Image") or curves.inputs[0]
        links.new(bloom.outputs["Image"], curve_in)
        curve_out = curves.outputs.get("Image") or curves.outputs[0]

        a_sock = lut_mix.inputs.get("A_Color") or lut_mix.inputs.get("A")
        b_sock = lut_mix.inputs.get("B_Color") or lut_mix.inputs.get("B")
        if a_sock is not None:
            links.new(bloom.outputs["Image"], a_sock)
        if b_sock is not None:
            links.new(curve_out, b_sock)

        result = lut_mix.outputs.get("Result_Color") or lut_mix.outputs.get("Result") or lut_mix.outputs[0]
        out_in = _group_output_socket(go)
        links.new(result, out_in)
        links.new(result, viewer.inputs["Image"])

        nt["arc_stack_version"] = _STACK_VERSION

    bloom = _find_node(nodes, label="ArcLightingBloom")
    lut_mix = _find_node(nodes, label="ArcLightingLutMix")
    curves = _find_node(nodes, label="ArcLightingLutCurves")

    if bloom is not None:
        # Mute alone is unreliable for Glare in B5 viewport compositor — zero Strength too.
        if apply_bloom:
            bloom.mute = False
            thr = float(atmos.get("bloom_threshold") or 1.0)
            strength = float(atmos.get("bloom_intensity") or 1.0)
            try:
                bloom.inputs["Type"].default_value = "Fog Glow"
            except Exception:
                pass
            if "Highlights Threshold" in bloom.inputs:
                bloom.inputs["Highlights Threshold"].default_value = max(0.0, thr)
            if "Strength" in bloom.inputs:
                bloom.inputs["Strength"].default_value = max(0.0, strength)
            if "Mix" in bloom.inputs:
                bloom.inputs["Mix"].default_value = 1.0
        else:
            bloom.mute = True
            if "Strength" in bloom.inputs:
                bloom.inputs["Strength"].default_value = 0.0
            if "Mix" in bloom.inputs:
                bloom.inputs["Mix"].default_value = 0.0

    if curves is not None and apply_lut and lut_png_path and os.path.isfile(lut_png_path):
        try:
            r_pts, g_pts, b_pts = sample_lut_gray_ramp(lut_png_path, size=size)
            _set_rgb_curve_points(curves, r_pts, g_pts, b_pts)
            curves.mute = False
        except Exception as exc:
            print(f"Arc Lighting Look: LUT curve sample failed ({exc})")
            curves.mute = True
    elif curves is not None:
        curves.mute = True

    if lut_mix is not None:
        if apply_lut and lut_png_path and curves is not None and not curves.mute:
            _set_fac(lut_mix, lut_strength)
            lut_mix.mute = False
        else:
            _set_fac(lut_mix, 0.0)

    scene.compositing_node_group = nt
    try:
        scene.render.use_compositing = True
    except Exception:
        pass
    try:
        scene.use_nodes = True
    except Exception:
        pass
    _enable_viewport_compositor()



def apply_lighting_look(
    scene: bpy.types.Scene,
    look_id: str | None = None,
    *,
    apply_lut: bool | None = None,
    apply_hdri: bool | None = None,
    lut_strength: float | None = None,
    hdri_strength: float | None = None,
) -> tuple[bool, str]:
    """Apply LUT/HDRI + map/scenario atmosphere effects. Returns (ok, message)."""
    from . import lighting_atmosphere as latm

    look_id = (look_id if look_id is not None else getattr(scene, "arc_lighting_look", "")) or ""
    if apply_lut is None:
        apply_lut = bool(getattr(scene, "arc_lighting_apply_lut", True))
    if apply_hdri is None:
        apply_hdri = bool(getattr(scene, "arc_lighting_apply_hdri", True))
    if lut_strength is None:
        lut_strength = float(getattr(scene, "arc_lighting_lut_strength", 0.5))
    if hdri_strength is None:
        hdri_strength = float(getattr(scene, "arc_lighting_hdri_strength", 1.0))

    apply_bloom = bool(getattr(scene, "arc_lighting_apply_bloom", True))
    apply_fog = bool(getattr(scene, "arc_lighting_apply_fog", True))
    override = bool(getattr(scene, "arc_lighting_override", False))

    pioneer = getattr(scene, "arc_pioneer_root", "") or utils.get_pioneer_root()
    if not pioneer or not os.path.isdir(pioneer):
        return False, "Set PioneerGame Folder in Settings first"

    lighting = find_lighting_root(pioneer)
    map_id = getattr(scene, "arc_lighting_map", "") or ""
    if map_id in ("", "_NONE_") and getattr(scene, "arc_placement_map", ""):
        map_id = scene.arc_placement_map
    scenario = getattr(scene, "arc_lighting_scenario", "") or ""

    atmos_skipped = ""
    atmos: dict[str, Any] = {}
    have_map_scenario = bool(
        map_id and map_id != "_NONE_" and scenario and scenario != "_NONE_"
    )
    # Always resolve atmosphere when Map+Scenario are set (drives fog/bloom/CGI/LUT hint).
    if have_map_scenario:
        atmos = latm.resolve_atmosphere(map_id, scenario, pioneer)
    elif apply_bloom or apply_fog:
        atmos_skipped = "bloom/fog skipped (pick Map+Scenario)"
        apply_bloom = False
        apply_fog = False

    # Sidebar LUT/HDRI are updated when Scenario changes (unless Override).
    # Apply uses whatever is currently shown — does not re-sync enums here.
    look = None
    if apply_lut or apply_hdri:
        if not lighting:
            return False, "Lighting folder not found under Pioneer root"
        look = get_look(look_id, lighting)
        if look is None and apply_lut and not (apply_bloom or apply_fog or atmos or apply_hdri):
            return False, f"Unknown lighting look: {look_id or '(empty)'}"
        if look is None and apply_lut:
            apply_lut = False

    hdri_id = getattr(scene, "arc_lighting_hdri", "") or ""
    hdri_path = resolve_hdri_path(hdri_id, lighting) if lighting else ""
    if apply_hdri and not hdri_path and look and look.get("hdri_path"):
        hdri_path = look["hdri_path"]
        if look.get("hdri_filename") and hasattr(scene, "arc_lighting_hdri"):
            try:
                scene.arc_lighting_hdri = look["hdri_filename"]
            except Exception:
                pass
    if apply_hdri and not hdri_path:
        apply_hdri = False

    if not (apply_lut or apply_hdri or apply_bloom or apply_fog):
        return False, "Enable LUT, HDRI, and/or atmosphere effects"

    parts: list[str] = []
    if override:
        parts.append("override")
    if map_id and map_id != "_NONE_":
        parts.append(map_id)
    if scenario and scenario != "_NONE_":
        parts.append(scenario)

    lut_path = ""
    if apply_lut and look:
        if not os.path.isfile(look["lut_path"]):
            return False, f"LUT missing: {look['lut_filename']}"
        try:
            cube_path = ensure_cube_for_lut(look["lut_path"])
            parts.append(f"cube:{os.path.basename(cube_path)}")
        except Exception as exc:
            print(f"Arc Lighting Look: .cube cache failed ({exc}); continuing with strip PNG")
        lut_path = look["lut_path"]
        parts.append(look["lut_filename"])

    # UE: Out = lerp(In, LUT(In), ColorGradingIntensity). Prefer dump intensity when present.
    cgi = atmos.get("color_grading_intensity") if atmos else None
    if apply_lut and cgi is not None:
        try:
            lut_strength = max(0.0, min(1.0, float(cgi)))
            scene.arc_lighting_lut_strength = lut_strength
            parts.append(f"CGI={lut_strength:.3f}")
        except (TypeError, ValueError):
            pass

    # Compositor: bloom + LUT (always refresh if stack exists so bloom/LUT toggles stick)
    stack_exists = bpy.data.node_groups.get(_GROUP_NAME) is not None
    if apply_lut or apply_bloom or stack_exists:
        cm = _apply_ue_color_management(scene, enable_lut=bool(apply_lut and lut_path))
        if apply_lut and lut_path:
            parts.append(cm)
        _ensure_compositor_stack(
            scene,
            lut_png_path=lut_path,
            lut_strength=lut_strength if apply_lut else 0.0,
            atmos=atmos,
            apply_lut=bool(apply_lut and lut_path),
            apply_bloom=apply_bloom,
        )
        if not apply_bloom:
            parts.append("bloom:off")

    fog_status = _ensure_world_volume_fog(scene, atmos, enabled=apply_fog and bool(atmos))
    if apply_fog:
        parts.append(fog_status)

    fx = []
    if apply_fog:
        fx.append("fog-box")
    if apply_bloom:
        fx.append("bloom")
    if fx:
        src = atmos.get("source", "?") if atmos else "?"
        parts.append("+".join(fx) + f" (source={src})")
    if atmos_skipped:
        parts.append(atmos_skipped)

    if apply_hdri:
        exposure_ev = float(look.get("exposure_ev") or 0.0) if look else 0.0
        strength = float(hdri_strength) * (2.0 ** exposure_ev)
        # Always respect the HDRI Rot Z slider — do not reset from dump/look defaults on Apply.
        rot_z = float(getattr(scene, "arc_lighting_hdri_rotation_z", _DEFAULT_HDRI_ROTATION_Z_DEG))
        flip_x = bool(getattr(scene, "arc_lighting_hdri_flip_x", False))
        white_temp = look.get("white_temp") if look else None
        hdri_status = _ensure_world_hdri(
            scene,
            hdri_path,
            strength,
            white_temp,
            rotation_z_deg=rot_z,
            flip_x=flip_x,
        )
        parts.append(hdri_status)
        if hdri_path and white_temp is not None:
            try:
                vs = scene.view_settings
                vs.use_white_balance = True
                vs.white_balance_temperature = float(white_temp)
            except Exception:
                pass
        _enable_scene_world()

    status = " · ".join(parts) if parts else look_id or scenario
    if hasattr(scene, "arc_lighting_look_applied"):
        scene.arc_lighting_look_applied = status[:240]
    return True, f"Applied: {status}"


# ---------------------------------------------------------------------------
# UI helper (Settings + Map Placement)
# ---------------------------------------------------------------------------


def draw_lighting_look_box(layout, context) -> None:
    scene = context.scene
    # Parent panel already titles "Lighting Look"; keep content in a simple column.
    box = layout.column()
    if not hasattr(scene, "arc_lighting_look"):
        box.label(text="Lighting props missing — reload add-on", icon="ERROR")
        return
    pioneer = getattr(scene, "arc_pioneer_root", "") or ""
    if not pioneer:
        box.label(text="Set PioneerGame Folder first", icon="INFO")

    if hasattr(scene, "arc_lighting_map"):
        box.prop(scene, "arc_lighting_map", text="Map")
    if hasattr(scene, "arc_lighting_scenario"):
        box.prop(scene, "arc_lighting_scenario", text="Scenario")

    if hasattr(scene, "arc_lighting_override"):
        box.prop(scene, "arc_lighting_override", text="Override (custom LUT + HDRI)")
        if getattr(scene, "arc_lighting_override", False):
            box.label(text="Manual LUT + HDRI kept on Apply", icon="INFO")
        else:
            box.label(text="Scenario updates LUT + HDRI in the panel; Apply enables them", icon="INFO")

    box.prop(scene, "arc_lighting_look", text="LUT Look")
    row = box.row(align=True)
    row.prop(scene, "arc_lighting_apply_lut", text="LUT")
    row.prop(scene, "arc_lighting_lut_strength", text="Strength")

    if hasattr(scene, "arc_lighting_hdri"):
        box.prop(scene, "arc_lighting_hdri", text="HDRI File")
    row = box.row(align=True)
    row.prop(scene, "arc_lighting_apply_hdri", text="Apply HDRI")
    row.prop(scene, "arc_lighting_hdri_strength", text="Strength")
    if hasattr(scene, "arc_lighting_hdri_rotation_z"):
        row = box.row(align=True)
        row.prop(scene, "arc_lighting_hdri_rotation_z", text="HDRI Rot Z")
        if hasattr(scene, "arc_lighting_hdri_flip_x"):
            row.prop(scene, "arc_lighting_hdri_flip_x", text="Flip X")

    if hasattr(scene, "arc_lighting_apply_bloom"):
        box.label(text="Atmosphere")
        row = box.row(align=True)
        row.prop(scene, "arc_lighting_apply_bloom", text="Bloom")
        row.prop(scene, "arc_lighting_apply_fog", text="Fog Cube")
        if getattr(scene, "arc_lighting_apply_fog", False) and hasattr(
            scene, "arc_lighting_fog_size_x"
        ):
            row = box.row(align=True)
            row.prop(scene, "arc_lighting_fog_size_x", text="X")
            row.prop(scene, "arc_lighting_fog_size_y", text="Y")
            row.prop(scene, "arc_lighting_fog_size_z", text="Z")

    box.operator("arc_outfits.apply_lighting_look", icon="CHECKMARK")
    applied = getattr(scene, "arc_lighting_look_applied", "") or ""
    if applied:
        box.label(text=f"Last: {applied[:72]}", icon="INFO")