"""Viewport Mask Debug — in-place inject, dual zone sources, lean digits.

Layer order (base → top): Arc Texturer → Mode Overlay → ColorMask → Digits.
Sources: Procedural (OCM Blue ColorRamp at BANDS), Baked (ZoneIndex PNG), Mismatch.
"""
from __future__ import annotations

import json
import os

import bpy

from . import palette_calibration
from .materials import clothing as _clothing

_ZONE_COUNT = 8
_BANDS = tuple(palette_calibration.BANDS)

_INJECT_FLAG = "arc_mask_debug_inject"
_INJECT_SURFACE_JSON = "arc_mask_debug_inject_surface"
_INJECT_SIG = "arc_mask_debug_sig"
_NODE_PFX = "ArcMaskDebug/"
_MAP_DIGIT_NAME = "ArcMaskDebug/DigitMapping"
_MAP_CHECKER_NAME = "ArcMaskDebug/CheckerMapping"
_COLOR_N_WANT_NAME = "ArcMaskDebug/ColorNWant"
_CHECKER_TEX_NAME = "ArcMaskDebug/CheckerTex"
_LEGACY_MAT_PREFIX = ".arc_mask_debug_"
_LEGACY_BACKUP_KEY = "arc_mask_debug_backup"
_LEGACY_CURV_BACKUP = "arc_mask_debug_curv_backup"
_LEGACY_INJECT_FLAG = "arc_mask_debug_passthrough_inject"
_LEGACY_INJECT_JSON = "arc_mask_debug_inject_surface"
_LEGACY_INJECT_PFX = "MaskDebugInject/"

_DIGIT_ATLAS_NAME = "CurvatureID_DebugDigits_v5"
_DIGIT_ATLAS_ASSET = "assets/CurvatureID_DebugDigits_v5.png"
_DIGIT_ATLAS_MIN_W = 1280
_DIGIT_ATLAS_COLS = 10
_DIGIT_ATLAS_LEGACY = (
    "CurvatureID_DebugDigits",
    "CurvatureID_DebugDigits_v2",
    "CurvatureID_DebugDigits_v3",
    "CurvatureID_DebugDigits_v4",
    "CurvatureID_DebugDigits_v6",
)

_CHECKER_IMG = "MaskDebug_MagentaBlack_Checker"
_CHECKER_BASE_SCALE = 50.0
# Digit tiling is auto-fit per object: this many glyph rows across the model's
# largest local dimension at Grid Scale 1. Works whether the mesh is authored in
# metres (~2 units) or UE centimetres (~190 units).
_DIGIT_ROWS_AT_GS1 = 24.0
_DIGIT_FALLBACK_EXTENT = 2.0
_DIGIT_CELL_ASPECT_FALLBACK = 0.8
# Node types this inject never creates — never tag or delete them.
_NEVER_INJECT_TYPES = frozenset({
    "OUTPUT_MATERIAL",
    "GROUP",
    "BSDF_PRINCIPLED",
    "BSDF_DIFFUSE",
    "BSDF_GLOSSY",
    "BSDF_GLASS",
    "BSDF_TRANSPARENT",
    "BSDF_TRANSLUCENT",
    "SUBSURFACE_SCATTERING",
    "NORMAL_MAP",
    "BUMP",
    "DISPLACEMENT",
    "ATTRIBUTE",
    "UVMAP",
    "VERTEX_COLOR",
    "FRAME",
    "REROUTE",
})
# Keep inject nodes near the existing graph (large Y made the editor look empty).
_DEBUG_Y = 400
_DEBUG_X = 2400

_ZONE_TINTS = (
    (0.95, 0.20, 0.20),
    (0.95, 0.55, 0.10),
    (0.95, 0.90, 0.15),
    (0.25, 0.85, 0.25),
    (0.15, 0.85, 0.85),
    (0.20, 0.40, 0.95),
    (0.55, 0.25, 0.95),
    (0.95, 0.25, 0.75),
)

_DIGIT_GLYPHS = {
    0: ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    1: ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    2: ("01110", "10001", "00001", "00110", "01000", "10000", "11111"),
    3: ("01110", "10001", "00001", "00110", "00001", "10001", "01110"),
    4: ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    5: ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    6: ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    7: ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    8: ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    9: ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
}


# ---------------------------------------------------------------------------
# Node helpers
# ---------------------------------------------------------------------------

def _dy(y: float) -> float:
    return float(y) + _DEBUG_Y


def _dx(x: float) -> float:
    return float(x) + _DEBUG_X


def _math(nodes, op, loc, label="", clamp=False):
    n = nodes.new("ShaderNodeMath")
    n.operation = op
    n.location = loc
    n.use_clamp = clamp
    if label:
        n.label = label
    return n


def _vec_math(nodes, op, loc, label=""):
    n = nodes.new("ShaderNodeVectorMath")
    n.operation = op
    n.location = loc
    if label:
        n.label = label
    return n


def _value(nodes, name, value, loc, label=""):
    n = nodes.new("ShaderNodeValue")
    n.name = name
    n.label = label or name
    n.outputs[0].default_value = float(value)
    n.location = loc
    return n


def _mix_rgba(nodes, links, fac, col_a, col_b, loc, label=""):
    m = nodes.new("ShaderNodeMix")
    m.data_type = "RGBA"
    m.blend_type = "MIX"
    m.clamp_factor = True
    m.label = label or "Mix"
    m.location = loc
    # Blender 4+/5 socket layout
    fac_in = m.inputs.get("Factor") or m.inputs[0]
    a_in = None
    b_in = None
    for sock in m.inputs:
        if sock.type == "RGBA" and a_in is None:
            a_in = sock
        elif sock.type == "RGBA" and b_in is None:
            b_in = sock
    if a_in is None:
        a_in = m.inputs[6] if len(m.inputs) > 6 else m.inputs[1]
    if b_in is None:
        b_in = m.inputs[7] if len(m.inputs) > 7 else m.inputs[2]
    if isinstance(fac, (int, float)):
        fac_in.default_value = float(fac)
    else:
        links.new(fac, fac_in)
    if isinstance(col_a, tuple):
        a_in.default_value = (*col_a[:3], 1.0)
    else:
        links.new(col_a, a_in)
    if isinstance(col_b, tuple):
        b_in.default_value = (*col_b[:3], 1.0)
    else:
        links.new(col_b, b_in)
    out = m.outputs.get("Result")
    if out is None:
        for o in m.outputs:
            if o.type == "RGBA":
                out = o
                break
    if out is None:
        out = m.outputs[0]
    return out


def _mix_shader_sockets(mix):
    fac = mix.inputs.get("Factor") or mix.inputs.get("Fac") or mix.inputs[0]
    shaders = [s for s in mix.inputs if getattr(s, "enabled", True) and s.type == "SHADER"]
    if len(shaders) >= 2:
        sh_a, sh_b = shaders[0], shaders[1]
    else:
        sh_a, sh_b = mix.inputs[1], mix.inputs[2]
    out = mix.outputs.get("Shader") or mix.outputs[0]
    return fac, sh_a, sh_b, out


def _tag(nodes, before_names):
    """Prefix the nodes this inject added.

    Identity is by node *name*: Blender returns a fresh Python wrapper on every
    access, so id() values are recycled and would mis-tag (and later delete) the
    real clothing nodes. Names are unique per tree and stable.
    """
    for n in list(nodes):
        if (getattr(n, "name", "") or "") in before_names:
            continue
        if _is_structural_node(n):
            continue
        try:
            if not (n.name or "").startswith(_NODE_PFX):
                n.name = f"{_NODE_PFX}{n.name}"[:63]
            if not (n.label or "").startswith(_NODE_PFX):
                n.label = f"{_NODE_PFX}{n.label or n.name}"
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Discover maps / legacy helpers (palette operators still import these)
# ---------------------------------------------------------------------------

def _is_structural_node(n) -> bool:
    """Clothing-graph nodes that must never be tagged or deleted as debug nodes."""
    ntype = getattr(n, "type", "") or ""
    if ntype in _NEVER_INJECT_TYPES:
        return True
    if ntype == "GROUP":
        tree = getattr(n, "node_tree", None)
        name = (getattr(tree, "name", "") or "").replace(" ", "")
        if name.startswith("ArcTexturer"):
            return True
    return False


def _find_arc_group(nodes):
    for node in nodes:
        if getattr(node, "type", "") != "GROUP":
            continue
        tree = getattr(node, "node_tree", None)
        name = (getattr(tree, "name", "") or "").replace(" ", "")
        if name == "ArcTexturer" or name.startswith("ArcTexturer"):
            return node
    return None


def discover_mask_maps(mat) -> dict:
    """Return {ocm, colormask, normal, basecolor} Image|None."""
    out = {"ocm": None, "colormask": None, "normal": None, "basecolor": None}
    if mat is None or _is_debug_mat(mat):
        return out
    if not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return out
    nodes = mat.node_tree.nodes
    group = _find_arc_group(nodes)
    try:
        ocm_n = _clothing._find_ocm_image_node(nodes, group)
        if ocm_n is not None and ocm_n.image:
            out["ocm"] = ocm_n.image
    except Exception:
        pass
    try:
        cm_n = _clothing._find_colormask_image_node(nodes, group)
        if cm_n is not None and cm_n.image:
            out["colormask"] = cm_n.image
    except Exception:
        pass
    try:
        nrm_n = _clothing._find_arc_role_image_node(nodes, group, "normal")
        if nrm_n is not None and nrm_n.image:
            out["normal"] = nrm_n.image
    except Exception:
        pass
    try:
        bc_n = _clothing._find_arc_role_image_node(nodes, group, "basecolor")
        if bc_n is not None and bc_n.image:
            out["basecolor"] = bc_n.image
    except Exception:
        pass
    # Name scan fallback
    for node in nodes:
        if node.type != "TEX_IMAGE" or node.image is None:
            continue
        blob = f"{node.image.name} {node.label or ''}".lower().replace(" ", "")
        if out["ocm"] is None and (
            "occlusioncurvaturematerialid" in blob
            or ("occlusion" in blob and "curvature" in blob)
        ):
            out["ocm"] = node.image
        if out["colormask"] is None and (
            "colormask" in blob or "colourmask" in blob
        ) and "occlusion" not in blob:
            out["colormask"] = node.image
        if out["basecolor"] is None and (
            "basecolor" in blob or "base_color" in blob
        ):
            out["basecolor"] = node.image
    return out


def _is_debug_mat(mat) -> bool:
    name = mat.name or "" if mat is not None else ""
    return name.startswith(_LEGACY_MAT_PREFIX)


def _source_materials_for_object(obj) -> list:
    """Clothing materials on *obj*, skipping legacy debug slot copies."""
    out = []
    seen = set()
    for slot in getattr(obj, "material_slots", []) or []:
        mat = slot.material
        if mat is None or _is_debug_mat(mat):
            continue
        if mat.as_pointer() in seen:
            continue
        seen.add(mat.as_pointer())
        out.append(mat)
    # Also resolve via legacy backup names if present
    raw = obj.get(_LEGACY_BACKUP_KEY) if obj is not None else None
    if raw:
        try:
            names = json.loads(raw)
            for name in names:
                if not name:
                    continue
                mat = bpy.data.materials.get(name)
                if mat is None or _is_debug_mat(mat):
                    continue
                if mat.as_pointer() in seen:
                    continue
                seen.add(mat.as_pointer())
                out.append(mat)
        except Exception:
            pass
    return out


def _slot_source_material(obj, slot_index: int):
    raw = obj.get(_LEGACY_BACKUP_KEY) if obj is not None else None
    if raw:
        try:
            names = json.loads(raw)
            if 0 <= slot_index < len(names) and names[slot_index]:
                mat = bpy.data.materials.get(names[slot_index])
                if mat is not None:
                    return mat
        except Exception:
            pass
    slots = getattr(obj, "material_slots", None)
    if slots is None or slot_index < 0 or slot_index >= len(slots):
        return None
    mat = slots[slot_index].material
    return None if _is_debug_mat(mat) else mat


def _sync_palette_rgb_defaults(src_mat, dst_mat) -> int:
    """No-op under in-place inject (kept for operators.py callers)."""
    del src_mat, dst_mat
    return 0


# ---------------------------------------------------------------------------
# Digit atlas
# ---------------------------------------------------------------------------

def _atlas_glyph_stats(image) -> tuple[int, int]:
    """Return (width, nonzero_alpha_count). Avoids full soft-lock scans when possible."""
    if image is None:
        return 0, 0
    try:
        w, h = int(image.size[0]), int(image.size[1])
    except Exception:
        return 0, 0
    if w < 1 or h < 1:
        return w, 0
    # Sparse sample — enough to detect empty/corrupt atlases
    try:
        n = w * h
        step = max(1, n // 4096)
        pix = image.pixels
        nonzero = 0
        for i in range(0, n, step):
            if float(pix[i * 4 + 3]) > 0.01:
                nonzero += 1
        return w, nonzero
    except Exception:
        return w, 0


def _purge_legacy_digit_atlases():
    for name in _DIGIT_ATLAS_LEGACY:
        img = bpy.data.images.get(name)
        if img is not None:
            try:
                bpy.data.images.remove(img)
            except Exception:
                pass


def _digit_atlas_asset_path() -> str:
    root = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, _DIGIT_ATLAS_ASSET.replace("/", os.sep))


def _paint_bitmap_digit_pixels(width, height, cell_w, cell_h):
    pixels = [0.0] * (width * height * 4)
    for digit, rows in _DIGIT_GLYPHS.items():
        ox = digit * cell_w
        for ry, row in enumerate(rows):
            for cx, ch in enumerate(row):
                if ch != "1":
                    continue
                # Scale 5x7 into cell with padding. Blender images are bottom-up,
                # so flip Y or the glyphs come out upside down.
                for dy in range(cell_h // 10):
                    for dx in range(cell_w // 8):
                        x = ox + (cx + 1) * (cell_w // 7) + dx
                        y = height - 1 - ((ry + 1) * (cell_h // 9) + dy)
                        if 0 <= x < width and 0 <= y < height:
                            i = (y * width + x) * 4
                            pixels[i : i + 4] = [1.0, 1.0, 1.0, 1.0]
    return pixels


def _ensure_digit_atlas():
    existing = bpy.data.images.get(_DIGIT_ATLAS_NAME)
    if existing is not None and existing.size[0] >= _DIGIT_ATLAS_MIN_W:
        _w, nz = _atlas_glyph_stats(existing)
        if nz > 0:
            try:
                existing.alpha_mode = "STRAIGHT"
            except Exception:
                pass
            return existing
        try:
            bpy.data.images.remove(existing)
        except Exception:
            pass

    _purge_legacy_digit_atlases()
    path = _digit_atlas_asset_path()
    if os.path.isfile(path):
        img = bpy.data.images.load(path, check_existing=False)
        try:
            img.name = _DIGIT_ATLAS_NAME
        except Exception:
            pass
        try:
            img.colorspace_settings.name = "sRGB"
            img.alpha_mode = "STRAIGHT"
            img.pack()
            img.use_fake_user = True
        except Exception:
            pass
        _w, nz = _atlas_glyph_stats(img)
        if nz > 0 and img.size[0] >= _DIGIT_ATLAS_MIN_W:
            return img

    # Procedural fallback
    cell_w, cell_h, cols = 128, 160, 10
    width, height = cell_w * cols, cell_h
    img = bpy.data.images.new(_DIGIT_ATLAS_NAME, width=width, height=height, alpha=True)
    pixels = _paint_bitmap_digit_pixels(width, height, cell_w, cell_h)
    try:
        img.pixels.foreach_set(pixels)
    except Exception:
        img.pixels = pixels
    try:
        img.colorspace_settings.name = "sRGB"
        img.alpha_mode = "STRAIGHT"
        img.pack()
        img.use_fake_user = True
    except Exception:
        pass
    return img


def _ensure_checker_image():
    """Legacy helper — Mode 1 now uses ShaderNodeTexChecker. Kept for old blends."""
    existing = bpy.data.images.get(_CHECKER_IMG)
    if existing is not None and existing.size[0] >= 2:
        # Recreate if the stored image is all-black (common after a failed write).
        try:
            pix = existing.pixels
            has_magenta = False
            n = min(len(pix), existing.size[0] * existing.size[1] * 4)
            for i in range(0, n, 4):
                if float(pix[i]) > 0.5 and float(pix[i + 2]) > 0.5:
                    has_magenta = True
                    break
            if has_magenta:
                return existing
            bpy.data.images.remove(existing)
        except Exception:
            pass
    img = bpy.data.images.new(_CHECKER_IMG, width=2, height=2, alpha=True)
    # Bottom row magenta|black, top row black|magenta (Blender Y-up pixels).
    pix = [
        1.0, 0.0, 1.0, 1.0,  0.0, 0.0, 0.0, 1.0,
        0.0, 0.0, 0.0, 1.0,  1.0, 0.0, 1.0, 1.0,
    ]
    try:
        img.pixels.foreach_set(pix)
    except Exception:
        img.pixels = pix
    try:
        img.colorspace_settings.name = "sRGB"
        img.pack()
        img.use_fake_user = True
    except Exception:
        pass
    return img


def _color_n_want(color_n: int) -> float:
    """Zone index 0..7 matching Color N 1..8."""
    return float(max(1, min(_ZONE_COUNT, int(color_n))) - 1)


# ---------------------------------------------------------------------------
# Zone sources → float zone 0..7
# ---------------------------------------------------------------------------

def _build_procedural_zone(nodes, links, ocm_image, origin):
    """OCM Blue → ColorRamp CONSTANT at BANDS → zone 0..7. Returns zone socket."""
    if ocm_image is None:
        return None
    try:
        ocm_image.colorspace_settings.name = "Non-Color"
    except Exception:
        pass
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = ocm_image
    tex.interpolation = "Closest"
    tex.extension = "EXTEND"
    tex.label = f"{_NODE_PFX}OCM"
    tex.location = origin
    tc = nodes.new("ShaderNodeTexCoord")
    tc.location = (origin[0] - 200, origin[1])
    links.new(tc.outputs["UV"], tex.inputs["Vector"])
    sep = nodes.new("ShaderNodeSeparateColor")
    sep.label = f"{_NODE_PFX}OCM→MID"
    sep.location = (origin[0] + 220, origin[1])
    links.new(tex.outputs["Color"], sep.inputs["Color"])
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.label = f"{_NODE_PFX}BandRamp"
    ramp.name = f"{_NODE_PFX}BandRamp"
    ramp.location = (origin[0] + 420, origin[1])
    links.new(sep.outputs["Blue"], ramp.inputs["Fac"])
    # 8 CONSTANT stops at band lower edges → gray = zone/7
    try:
        elems = ramp.color_ramp.elements
        ramp.color_ramp.interpolation = "CONSTANT"
        while len(elems) > 1:
            elems.remove(elems[-1])
        elems[0].position = 0.0
        elems[0].color = (0.0, 0.0, 0.0, 1.0)
        for z in range(1, _ZONE_COUNT):
            pos = float(_BANDS[z]) if z < len(_BANDS) else z / 7.0
            el = elems.new(max(0.0, min(1.0, pos)))
            v = z / 7.0
            el.color = (v, v, v, 1.0)
    except Exception as exc:
        print(f"Arc Raiders: BandRamp setup failed: {exc}")
    sep_r = nodes.new("ShaderNodeSeparateColor")
    sep_r.location = (origin[0] + 620, origin[1] - 80)
    links.new(ramp.outputs["Color"], sep_r.inputs["Color"])
    mul = _math(nodes, "MULTIPLY", (origin[0] + 800, origin[1]), f"{_NODE_PFX}×7")
    links.new(sep_r.outputs["Red"], mul.inputs[0])
    mul.inputs[1].default_value = 7.0
    return mul.outputs[0]


def _build_baked_zone(nodes, links, ocm_image, origin):
    """ZoneIndex PNG Closest → (R - 16/255) * (255/32) ≈ zone. Returns zone socket."""
    if ocm_image is None:
        return None
    from . import ocm_zone_cache

    idx = ocm_zone_cache.ensure_ocm_zone_index(ocm_image, force=False)
    if idx is None:
        idx = ocm_zone_cache.ensure_ocm_zone_index(ocm_image, force=True)
    if idx is None:
        return None
    try:
        idx.colorspace_settings.name = "Non-Color"
    except Exception:
        pass
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = idx
    tex.interpolation = "Closest"
    tex.extension = "EXTEND"
    tex.label = f"{_NODE_PFX}ZoneIndex"
    tex.location = origin
    tc = nodes.new("ShaderNodeTexCoord")
    tc.location = (origin[0] - 200, origin[1])
    links.new(tc.outputs["UV"], tex.inputs["Vector"])
    sep = nodes.new("ShaderNodeSeparateColor")
    sep.location = (origin[0] + 220, origin[1])
    links.new(tex.outputs["Color"], sep.inputs["Color"])
    # u8 = zone*32+16 → zone = (u8 - 16) / 32 = R*255/32 - 0.5
    # R is 0..1: zone = R * (255/32) - 0.5
    mul = _math(nodes, "MULTIPLY", (origin[0] + 400, origin[1]), f"{_NODE_PFX}×7.968")
    links.new(sep.outputs["Red"], mul.inputs[0])
    mul.inputs[1].default_value = 255.0 / 32.0
    sub = _math(nodes, "SUBTRACT", (origin[0] + 560, origin[1]), f"{_NODE_PFX}-0.5")
    links.new(mul.outputs[0], sub.inputs[0])
    sub.inputs[1].default_value = 0.5
    fl = _math(nodes, "FLOOR", (origin[0] + 720, origin[1]), f"{_NODE_PFX}floor")
    links.new(sub.outputs[0], fl.inputs[0])
    return fl.outputs[0]


def _build_zone(nodes, links, *, source: str, ocm_image, origin=(-2000, _dy(200))):
    """Return (zone_sock, mismatch_overlay_rgb|None)."""
    src = (source or "procedural").lower()
    if src == "baked":
        z = _build_baked_zone(nodes, links, ocm_image, origin)
        return z, None
    if src == "mismatch":
        zp = _build_procedural_zone(nodes, links, ocm_image, (origin[0], origin[1] + 200))
        zb = _build_baked_zone(nodes, links, ocm_image, (origin[0], origin[1] - 200))
        if zp is None and zb is None:
            return None, None
        if zp is None:
            return zb, None
        if zb is None:
            return zp, None
        # abs(p - b) > 0.5 → warning colour
        sub = _math(nodes, "SUBTRACT", (origin[0] + 900, origin[1]), f"{_NODE_PFX}p-b")
        links.new(zp, sub.inputs[0])
        links.new(zb, sub.inputs[1])
        ab = _math(nodes, "ABSOLUTE", (origin[0] + 1060, origin[1]), f"{_NODE_PFX}|diff|")
        links.new(sub.outputs[0], ab.inputs[0])
        gt = _math(nodes, "GREATER_THAN", (origin[0] + 1220, origin[1]), f"{_NODE_PFX}disagree")
        links.new(ab.outputs[0], gt.inputs[0])
        gt.inputs[1].default_value = 0.5
        warn = _mix_rgba(
            nodes, links, gt.outputs[0],
            (0.05, 0.05, 0.05), (1.0, 0.15, 0.0),
            (origin[0] + 1400, origin[1]), f"{_NODE_PFX}Mismatch",
        )
        return zp, warn
    # procedural (default)
    return _build_procedural_zone(nodes, links, ocm_image, origin), None


# ---------------------------------------------------------------------------
# Mode overlays
# ---------------------------------------------------------------------------

def _mode1_overlay(nodes, links, zone_sock, color_n: int, grid_scale: float, origin):
    """Magenta/black checker gated to selected Color N. Returns (rgb, coverage)."""
    want = _color_n_want(color_n)
    # Dedicated Value node so Color N can soft-update live without rebuilding.
    want_n = _value(
        nodes, _COLOR_N_WANT_NAME, want,
        (origin[0] - 180, origin[1] + 40), f"{_NODE_PFX}ColorNWant",
    )
    sub = _math(nodes, "SUBTRACT", (origin[0], origin[1] + 40), f"{_NODE_PFX}z-cn")
    links.new(zone_sock, sub.inputs[0])
    links.new(want_n.outputs[0], sub.inputs[1])
    ab = _math(nodes, "ABSOLUTE", (origin[0] + 160, origin[1] + 40), f"{_NODE_PFX}|z-cn|")
    links.new(sub.outputs[0], ab.inputs[0])
    lt = _math(nodes, "LESS_THAN", (origin[0] + 320, origin[1] + 40), f"{_NODE_PFX}inZone", clamp=True)
    links.new(ab.outputs[0], lt.inputs[0])
    lt.inputs[1].default_value = 0.5

    tc = nodes.new("ShaderNodeTexCoord")
    tc.location = (origin[0], origin[1] - 120)
    mapn = nodes.new("ShaderNodeMapping")
    mapn.name = _MAP_CHECKER_NAME
    mapn.label = f"{_NODE_PFX}CheckerMapping"
    mapn.location = (origin[0] + 180, origin[1] - 120)
    gs = max(0.5, float(grid_scale)) * _CHECKER_BASE_SCALE
    mapn.inputs["Scale"].default_value = (gs, gs, gs)
    links.new(tc.outputs["UV"], mapn.inputs["Vector"])
    # Procedural checker — never depends on a stored Image that can go all-black.
    chk = nodes.new("ShaderNodeTexChecker")
    chk.name = _CHECKER_TEX_NAME
    chk.label = f"{_NODE_PFX}Checker"
    chk.location = (origin[0] + 400, origin[1] - 120)
    try:
        chk.inputs["Color1"].default_value = (1.0, 0.0, 1.0, 1.0)
        chk.inputs["Color2"].default_value = (0.0, 0.0, 0.0, 1.0)
        chk.inputs["Scale"].default_value = 1.0
    except Exception:
        pass
    links.new(mapn.outputs["Vector"], chk.inputs["Vector"])
    return chk.outputs["Color"], lt.outputs[0]


def _mode2_overlay(nodes, links, ocm_image, origin):
    """Raw OCM with contrast + saturation boost. Returns (rgb, coverage=1)."""
    if ocm_image is None:
        return (0.2, 0.2, 0.2), None
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = ocm_image
    tex.interpolation = "Closest"
    tex.extension = "EXTEND"
    tex.label = f"{_NODE_PFX}OCMOverlay"
    tex.location = origin
    tc = nodes.new("ShaderNodeTexCoord")
    tc.location = (origin[0] - 200, origin[1])
    links.new(tc.outputs["UV"], tex.inputs["Vector"])

    # Match the hand-tuned Brightness/Contrast → Hue/Saturation stack.
    bc = nodes.new("ShaderNodeBrightContrast")
    bc.label = f"{_NODE_PFX}OCMContrast"
    bc.location = (origin[0] + 220, origin[1])
    links.new(tex.outputs["Color"], bc.inputs["Color"])
    try:
        bc.inputs["Bright"].default_value = -0.1
        bc.inputs["Contrast"].default_value = 0.5
    except Exception:
        pass
    hsv = nodes.new("ShaderNodeHueSaturation")
    hsv.label = f"{_NODE_PFX}OCMSat"
    hsv.location = (origin[0] + 440, origin[1])
    links.new(bc.outputs["Color"], hsv.inputs["Color"])
    try:
        hsv.inputs["Hue"].default_value = 0.5
        hsv.inputs["Saturation"].default_value = 1.25
        hsv.inputs["Value"].default_value = 0.75
        hsv.inputs["Fac"].default_value = 1.0
    except Exception:
        pass
    return hsv.outputs["Color"], None


def _mode3_overlay(nodes, links, zone_sock, origin):
    """False colour ColorRamp CONSTANT. Returns (rgb, coverage=1)."""
    div = _math(nodes, "DIVIDE", (origin[0], origin[1]), f"{_NODE_PFX}z/7")
    links.new(zone_sock, div.inputs[0])
    div.inputs[1].default_value = 7.0
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.label = f"{_NODE_PFX}FalseColor"
    ramp.location = (origin[0] + 180, origin[1])
    links.new(div.outputs[0], ramp.inputs["Fac"])
    try:
        elems = ramp.color_ramp.elements
        ramp.color_ramp.interpolation = "CONSTANT"
        while len(elems) > 1:
            elems.remove(elems[-1])
        for z in range(_ZONE_COUNT):
            pos = z / 7.0
            if z == 0:
                el = elems[0]
                el.position = 0.0
            else:
                el = elems.new(pos)
            t = _ZONE_TINTS[z]
            el.color = (t[0], t[1], t[2], 1.0)
    except Exception as exc:
        print(f"Arc Raiders: FalseColor ramp failed: {exc}")
    return ramp.outputs["Color"], None


# ---------------------------------------------------------------------------
# Lean digits
# ---------------------------------------------------------------------------

def _digit_cell_aspect() -> float:
    """Atlas cell width/height (cells are taller than wide, ~0.8)."""
    img = bpy.data.images.get(_DIGIT_ATLAS_NAME)
    try:
        w, h = int(img.size[0]), int(img.size[1])
        if w > 0 and h > 0:
            return (float(w) / float(_DIGIT_ATLAS_COLS)) / float(h)
    except Exception:
        pass
    return _DIGIT_CELL_ASPECT_FALLBACK


def _object_local_extent(obj) -> float:
    """Largest local bounding-box dimension (ignores object scale / placement)."""
    try:
        corners = [tuple(c) for c in obj.bound_box]
    except Exception:
        return 0.0
    if not corners:
        return 0.0
    ext = 0.0
    for axis in range(3):
        vals = [c[axis] for c in corners]
        ext = max(ext, float(max(vals) - min(vals)))
    return ext


def _digit_tile_scale(grid_scale: float, extent: float) -> tuple[float, float]:
    """Mapping Scale (X, Y) so glyphs auto-fit the model at any unit scale.

    V repeats _DIGIT_ROWS_AT_GS1 x Grid Scale times across *extent*; U is derived
    from the atlas cell aspect so glyphs are not stretched.
    """
    gs = max(0.5, float(grid_scale))
    ext = float(extent) if extent and extent > 0.0 else _DIGIT_FALLBACK_EXTENT
    sy = (_DIGIT_ROWS_AT_GS1 * gs) / ext
    sx = sy / max(0.05, _digit_cell_aspect())
    return sx, sy


def _build_object_digit_uv(nodes, links, origin):
    """Hard dominant-axis object-space UV — upright, unmirrored glyphs.

    Object space (not world) keeps digits glued to the mesh when it moves.
    |Nx| -> (Y*sign(Nx), Z); |Ny| -> (-X*sign(Ny), Z); else -> (X*sign(Nz), Y).
    Exclusive SCALE+ADD selection (a soft Mix blend skews the UVs).
    """
    tc = nodes.new("ShaderNodeTexCoord")
    tc.label = f"{_NODE_PFX}DigitCoord"
    tc.location = origin

    sep_n = nodes.new("ShaderNodeSeparateXYZ")
    sep_n.location = (origin[0] + 160, origin[1] + 80)
    links.new(tc.outputs["Normal"], sep_n.inputs["Vector"])
    sep_p = nodes.new("ShaderNodeSeparateXYZ")
    sep_p.location = (origin[0] + 160, origin[1] - 80)
    links.new(tc.outputs["Object"], sep_p.inputs["Vector"])

    ax = _math(nodes, "ABSOLUTE", (origin[0] + 320, origin[1] + 120), f"{_NODE_PFX}|Nx|")
    links.new(sep_n.outputs["X"], ax.inputs[0])
    ay = _math(nodes, "ABSOLUTE", (origin[0] + 320, origin[1] + 40), f"{_NODE_PFX}|Ny|")
    links.new(sep_n.outputs["Y"], ay.inputs[0])
    az = _math(nodes, "ABSOLUTE", (origin[0] + 320, origin[1] - 40), f"{_NODE_PFX}|Nz|")
    links.new(sep_n.outputs["Z"], az.inputs[0])

    # Exclusive: x_dom, else y_dom, else z_dom
    ny_gt_nx = _math(nodes, "GREATER_THAN", (origin[0] + 480, origin[1] + 100), f"{_NODE_PFX}Ny>Nx")
    links.new(ay.outputs[0], ny_gt_nx.inputs[0])
    links.new(ax.outputs[0], ny_gt_nx.inputs[1])
    one_m_ny = _math(nodes, "SUBTRACT", (origin[0] + 640, origin[1] + 100), f"{_NODE_PFX}!Ny>Nx")
    one_m_ny.inputs[0].default_value = 1.0
    links.new(ny_gt_nx.outputs[0], one_m_ny.inputs[1])
    nz_gt_nx = _math(nodes, "GREATER_THAN", (origin[0] + 480, origin[1] + 40), f"{_NODE_PFX}Nz>Nx")
    links.new(az.outputs[0], nz_gt_nx.inputs[0])
    links.new(ax.outputs[0], nz_gt_nx.inputs[1])
    one_m_nzx = _math(nodes, "SUBTRACT", (origin[0] + 640, origin[1] + 40), f"{_NODE_PFX}!Nz>Nx")
    one_m_nzx.inputs[0].default_value = 1.0
    links.new(nz_gt_nx.outputs[0], one_m_nzx.inputs[1])
    x_dom = _math(nodes, "MULTIPLY", (origin[0] + 800, origin[1] + 80), f"{_NODE_PFX}xDom", clamp=True)
    links.new(one_m_ny.outputs[0], x_dom.inputs[0])
    links.new(one_m_nzx.outputs[0], x_dom.inputs[1])

    nz_gt_ny = _math(nodes, "GREATER_THAN", (origin[0] + 480, origin[1] - 40), f"{_NODE_PFX}Nz>Ny")
    links.new(az.outputs[0], nz_gt_ny.inputs[0])
    links.new(ay.outputs[0], nz_gt_ny.inputs[1])
    one_m_nzy = _math(nodes, "SUBTRACT", (origin[0] + 640, origin[1] - 40), f"{_NODE_PFX}!Nz>Ny")
    one_m_nzy.inputs[0].default_value = 1.0
    links.new(nz_gt_ny.outputs[0], one_m_nzy.inputs[1])
    not_x = _math(nodes, "SUBTRACT", (origin[0] + 800, origin[1] + 20), f"{_NODE_PFX}!xDom")
    not_x.inputs[0].default_value = 1.0
    links.new(x_dom.outputs[0], not_x.inputs[1])
    y_dom = _math(nodes, "MULTIPLY", (origin[0] + 960, origin[1] + 20), f"{_NODE_PFX}yDom", clamp=True)
    links.new(not_x.outputs[0], y_dom.inputs[0])
    links.new(one_m_nzy.outputs[0], y_dom.inputs[1])

    not_y = _math(nodes, "SUBTRACT", (origin[0] + 960, origin[1] - 40), f"{_NODE_PFX}!yDom")
    not_y.inputs[0].default_value = 1.0
    links.new(y_dom.outputs[0], not_y.inputs[1])
    z_dom = _math(nodes, "MULTIPLY", (origin[0] + 1120, origin[1] - 40), f"{_NODE_PFX}zDom", clamp=True)
    links.new(not_x.outputs[0], z_dom.inputs[0])
    links.new(not_y.outputs[0], z_dom.inputs[1])

    # Facing sign: without it the projection mirrors on -X / -Y / -Z surfaces.
    sgn_x = _math(nodes, "SIGN", (origin[0] + 320, origin[1] - 120), f"{_NODE_PFX}sgnNx")
    links.new(sep_n.outputs["X"], sgn_x.inputs[0])
    sgn_y = _math(nodes, "SIGN", (origin[0] + 320, origin[1] - 200), f"{_NODE_PFX}sgnNy")
    links.new(sep_n.outputs["Y"], sgn_y.inputs[0])
    sgn_z = _math(nodes, "SIGN", (origin[0] + 320, origin[1] - 280), f"{_NODE_PFX}sgnNz")
    links.new(sep_n.outputs["Z"], sgn_z.inputs[0])
    neg_sgn_y = _math(nodes, "MULTIPLY", (origin[0] + 480, origin[1] - 200), f"{_NODE_PFX}-sgnNy")
    links.new(sgn_y.outputs[0], neg_sgn_y.inputs[0])
    neg_sgn_y.inputs[1].default_value = -1.0

    u_x = _math(nodes, "MULTIPLY", (origin[0] + 640, origin[1] - 120), f"{_NODE_PFX}uX")
    links.new(sep_p.outputs["Y"], u_x.inputs[0])
    links.new(sgn_x.outputs[0], u_x.inputs[1])
    u_y = _math(nodes, "MULTIPLY", (origin[0] + 640, origin[1] - 200), f"{_NODE_PFX}uY")
    links.new(sep_p.outputs["X"], u_y.inputs[0])
    links.new(neg_sgn_y.outputs[0], u_y.inputs[1])
    u_z = _math(nodes, "MULTIPLY", (origin[0] + 640, origin[1] - 280), f"{_NODE_PFX}uZ")
    links.new(sep_p.outputs["X"], u_z.inputs[0])
    links.new(sgn_z.outputs[0], u_z.inputs[1])

    uv_x = nodes.new("ShaderNodeCombineXYZ")  # side faces: V = local up
    uv_x.label = f"{_NODE_PFX}uvX"
    uv_x.location = (origin[0] + 800, origin[1] - 160)
    links.new(u_x.outputs[0], uv_x.inputs["X"])
    links.new(sep_p.outputs["Z"], uv_x.inputs["Y"])
    uv_x.inputs["Z"].default_value = 0.0
    uv_y = nodes.new("ShaderNodeCombineXYZ")  # front/back: V = local up
    uv_y.label = f"{_NODE_PFX}uvY"
    uv_y.location = (origin[0] + 800, origin[1] - 260)
    links.new(u_y.outputs[0], uv_y.inputs["X"])
    links.new(sep_p.outputs["Z"], uv_y.inputs["Y"])
    uv_y.inputs["Z"].default_value = 0.0
    uv_z = nodes.new("ShaderNodeCombineXYZ")  # top/bottom: no canonical up
    uv_z.label = f"{_NODE_PFX}uvZ"
    uv_z.location = (origin[0] + 800, origin[1] - 360)
    links.new(u_z.outputs[0], uv_z.inputs["X"])
    links.new(sep_p.outputs["Y"], uv_z.inputs["Y"])
    uv_z.inputs["Z"].default_value = 0.0

    def _scale_uv(vec_sock, weight_sock, loc, label):
        n = _vec_math(nodes, "SCALE", loc, label)
        links.new(vec_sock, n.inputs[0])
        # SCALE: vector × Scale; Scale is input[1] as float in Vector Math
        try:
            links.new(weight_sock, n.inputs["Scale"])
        except Exception:
            links.new(weight_sock, n.inputs[1])
        return n.outputs[0]

    sx = _scale_uv(uv_x.outputs["Vector"], x_dom.outputs[0], (origin[0] + 980, origin[1] - 160), f"{_NODE_PFX}×x")
    sy = _scale_uv(uv_y.outputs["Vector"], y_dom.outputs[0], (origin[0] + 980, origin[1] - 260), f"{_NODE_PFX}×y")
    sz = _scale_uv(uv_z.outputs["Vector"], z_dom.outputs[0], (origin[0] + 980, origin[1] - 360), f"{_NODE_PFX}×z")

    add_xy = _vec_math(nodes, "ADD", (origin[0] + 1160, origin[1] - 220), f"{_NODE_PFX}uv+xy")
    links.new(sx, add_xy.inputs[0])
    links.new(sy, add_xy.inputs[1])
    add_all = _vec_math(nodes, "ADD", (origin[0] + 1340, origin[1] - 280), f"{_NODE_PFX}uv+xyz")
    links.new(add_xy.outputs[0], add_all.inputs[0])
    links.new(sz, add_all.inputs[1])
    return add_all.outputs[0]


def _build_digits(
    nodes, links, zone_sock, albedo_image, grid_scale: float, origin, extent: float = 0.0
):
    """Lean digit atlas on object-space dominant-axis UVs; CLIP alpha coverage.

    Returns (rgb, coverage). Zone selects the atlas cell; *extent* is the model's
    largest local dimension, used to auto-fit glyph size at any unit scale.
    """
    atlas = _ensure_digit_atlas()
    if atlas is None or zone_sock is None:
        return None, None

    obj_uv = _build_object_digit_uv(nodes, links, origin)

    mapn = nodes.new("ShaderNodeMapping")
    mapn.name = _MAP_DIGIT_NAME
    mapn.label = f"{_NODE_PFX}DigitMapping"
    mapn.location = (origin[0] + 1520, origin[1])
    sx, sy = _digit_tile_scale(grid_scale, extent)
    mapn.inputs["Scale"].default_value = (sx, sy, 1.0)
    links.new(obj_uv, mapn.inputs["Vector"])

    fr = _vec_math(nodes, "FRACTION", (origin[0] + 1700, origin[1]), f"{_NODE_PFX}fract")
    links.new(mapn.outputs["Vector"], fr.inputs[0])

    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (origin[0] + 1860, origin[1])
    links.new(fr.outputs["Vector"], sep.inputs["Vector"])

    add_z = _math(nodes, "ADD", (origin[0] + 1860, origin[1] - 160), f"{_NODE_PFX}z+1")
    links.new(zone_sock, add_z.inputs[0])
    add_z.inputs[1].default_value = 1.0
    add_u = _math(nodes, "ADD", (origin[0] + 2020, origin[1] - 80), f"{_NODE_PFX}idx+u")
    links.new(add_z.outputs[0], add_u.inputs[0])
    links.new(sep.outputs["X"], add_u.inputs[1])
    div_u = _math(nodes, "DIVIDE", (origin[0] + 2180, origin[1] - 80), f"{_NODE_PFX}atlasU")
    links.new(add_u.outputs[0], div_u.inputs[0])
    div_u.inputs[1].default_value = float(_DIGIT_ATLAS_COLS)

    comb = nodes.new("ShaderNodeCombineXYZ")
    comb.location = (origin[0] + 2340, origin[1])
    links.new(div_u.outputs[0], comb.inputs["X"])
    links.new(sep.outputs["Y"], comb.inputs["Y"])
    comb.inputs["Z"].default_value = 0.0

    tex = nodes.new("ShaderNodeTexImage")
    tex.image = atlas
    tex.interpolation = "Closest"
    tex.extension = "CLIP"
    tex.label = f"{_NODE_PFX}Digits"
    tex.location = (origin[0] + 2520, origin[1])
    links.new(comb.outputs["Vector"], tex.inputs["Vector"])

    cov_gt = _math(
        nodes, "GREATER_THAN", (origin[0] + 2700, origin[1] + 40), f"{_NODE_PFX}glyph?", clamp=True
    )
    links.new(tex.outputs["Alpha"], cov_gt.inputs[0])
    cov_gt.inputs[1].default_value = 0.15
    cov = cov_gt.outputs[0]

    if albedo_image is not None:
        atex = nodes.new("ShaderNodeTexImage")
        atex.image = albedo_image
        atex.interpolation = "Linear"
        atex.extension = "EXTEND"
        atex.label = f"{_NODE_PFX}Albedo"
        atex.location = (origin[0] + 2520, origin[1] - 240)
        atc = nodes.new("ShaderNodeTexCoord")
        atc.location = (origin[0] + 2320, origin[1] - 240)
        links.new(atc.outputs["UV"], atex.inputs["Vector"])
        bw = nodes.new("ShaderNodeRGBToBW")
        bw.location = (origin[0] + 2700, origin[1] - 240)
        links.new(atex.outputs["Color"], bw.inputs["Color"])
        dark = _math(nodes, "LESS_THAN", (origin[0] + 2860, origin[1] - 240), f"{_NODE_PFX}dark?")
        links.new(bw.outputs["Val"], dark.inputs[0])
        dark.inputs[1].default_value = 0.45
        fill = _mix_rgba(
            nodes, links, dark.outputs[0],
            (0.05, 0.05, 0.05), (1.0, 1.0, 1.0),
            (origin[0] + 2700, origin[1]), f"{_NODE_PFX}glyph",
        )
    else:
        fill = (1.0, 1.0, 1.0)

    dig = _mix_rgba(
        nodes, links, cov, (0.0, 0.0, 0.0), fill,
        (origin[0] + 2900, origin[1]), f"{_NODE_PFX}digRGB",
    )
    return dig, cov


# ---------------------------------------------------------------------------
# Inject / strip
# ---------------------------------------------------------------------------

def _find_output(nodes):
    for n in nodes:
        if n.type == "OUTPUT_MATERIAL":
            return n
    return None


def _is_inject_node(n) -> bool:
    name = getattr(n, "name", "") or ""
    lab = getattr(n, "label", "") or ""
    return (
        name.startswith(_NODE_PFX)
        or lab.startswith(_NODE_PFX)
        or name.startswith(_LEGACY_INJECT_PFX)
        or lab.startswith(_LEGACY_INJECT_PFX)
    )


def _shader_sock_from_node(node, sock_name: str = ""):
    if node is None:
        return None
    if sock_name:
        cand = node.outputs.get(sock_name)
        if cand is not None and cand.type == "SHADER":
            return cand
    for sock in node.outputs:
        if sock.type == "SHADER":
            return sock
    return None


def _resolve_base_shader(mat, nodes, out, *, exclude=None):
    """Real clothing Surface chain — never an inject Mix, never Color/Float."""
    exclude = set(exclude or [])
    raw = None
    if mat is not None:
        raw = mat.get(_INJECT_SURFACE_JSON) or mat.get(_LEGACY_INJECT_JSON)
    if raw:
        try:
            info = json.loads(raw)
            node = nodes.get(info.get("node") or "")
            if node is not None and node not in exclude and not _is_inject_node(node):
                sock = _shader_sock_from_node(node, info.get("socket") or "")
                if sock is not None:
                    return sock
        except Exception:
            pass
    if out is not None and out.inputs["Surface"].is_linked:
        lnk = out.inputs["Surface"].links[0]
        if lnk.from_node not in exclude and not _is_inject_node(lnk.from_node):
            if lnk.from_socket is not None and lnk.from_socket.type == "SHADER":
                return lnk.from_socket
    return _find_any_surface_shader(nodes)


def _list_inject_nodes(nodes):
    return [n for n in list(nodes) if _is_inject_node(n)]


def _find_any_surface_shader(nodes):
    """Fallback shader socket only — never wire Color/Float into Surface."""
    group = _find_arc_group(nodes)
    if group is not None and group.outputs:
        for sock in group.outputs:
            if sock.type == "SHADER":
                return sock
    for n in nodes:
        if n.type in ("BSDF_PRINCIPLED", "EMISSION", "BSDF_DIFFUSE", "GROUP", "MIX_SHADER", "ADD_SHADER"):
            if (n.name or "").startswith(_NODE_PFX) or (n.label or "").startswith(_NODE_PFX):
                continue
            if (n.name or "").startswith(_LEGACY_INJECT_PFX) or (n.label or "").startswith(
                _LEGACY_INJECT_PFX
            ):
                continue
            for sock in n.outputs:
                if sock.type == "SHADER":
                    return sock
    return None


def _inject_signature(
    *,
    mode: int,
    source: str,
    color_n: int,
    show_numbers: bool,
    show_colormask: bool,
    overlay_opacity: float,
    colormask_opacity: float,
    numbers_opacity: float,
) -> str:
    """Topology signature — scale / Color N / opacities soft-update without rebuild."""
    del color_n, overlay_opacity, colormask_opacity, numbers_opacity
    return json.dumps(
        {
            "m": int(mode),
            "src": str(source or "procedural"),
            "n": int(bool(show_numbers)),
            "cm": int(bool(show_colormask)),
        },
        separators=(",", ":"),
    )


def _tag_mat_nodes_updated(mat) -> None:
    """Force EEVEE / Material Preview to pick up default_value edits."""
    try:
        tree = mat.node_tree
        if tree is not None:
            tree.update_tag()
    except Exception:
        pass
    try:
        mat.update_tag()
    except Exception:
        pass


def _soft_update_color_n(mat, color_n: int) -> bool:
    """Poke Color N Value (or legacy Math) in place. No strip / Surface touch."""
    if mat is None or not mat.get(_INJECT_FLAG):
        return False
    if not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return False
    want = _color_n_want(color_n)
    n_hit = 0
    nodes = mat.node_tree.nodes

    def _set_value_node(n) -> bool:
        try:
            n.outputs[0].default_value = want
            return True
        except Exception:
            return False

    def _set_math_b(n) -> bool:
        try:
            sock = n.inputs[1]
            if sock.is_linked:
                src = sock.links[0].from_node
                if getattr(src, "type", "") == "VALUE":
                    return _set_value_node(src)
                return False
            sock.default_value = want
            return True
        except Exception:
            return False

    for n in nodes:
        name = n.name or ""
        lab = n.label or ""
        ntype = getattr(n, "type", "") or ""
        blob = f"{name} {lab}"
        if "ColorNWant" not in blob and name != _COLOR_N_WANT_NAME and lab != _COLOR_N_WANT_NAME:
            continue
        if ntype == "VALUE" and _set_value_node(n):
            n_hit += 1
        elif ntype == "MATH" and _set_math_b(n):
            n_hit += 1

    if n_hit == 0:
        # Mode 1 zone gate: Math labeled z-cn (Value-fed or bare B input).
        for n in nodes:
            if getattr(n, "type", "") != "MATH":
                continue
            lab = n.label or ""
            name = n.name or ""
            if "z-cn" not in lab and "z-cn" not in name:
                continue
            if _set_math_b(n):
                n_hit += 1

    if n_hit > 0:
        _tag_mat_nodes_updated(mat)
    return n_hit > 0


_last_live_color_n = None


def soft_update_color_n_selected(context) -> int:
    """Live Color N poke on every material that already has Mask Debug inject."""
    global _last_live_color_n
    try:
        color_n = int(getattr(context.scene, "arc_mask_debug_color_n", 6) or 6)
    except Exception:
        color_n = 6
    _last_live_color_n = color_n
    n = 0
    seen = set()
    for mat in bpy.data.materials:
        if mat is None or not mat.get(_INJECT_FLAG):
            continue
        ptr = mat.as_pointer()
        if ptr in seen:
            continue
        seen.add(ptr)
        if _soft_update_color_n(mat, color_n):
            n += 1
    # Nudge 3D views so Material Preview refreshes immediately.
    if n > 0:
        try:
            wm = context.window_manager
            for window in wm.windows:
                screen = window.screen
                if screen is None:
                    continue
                for area in screen.areas:
                    if area.type == "VIEW_3D":
                        area.tag_redraw()
        except Exception:
            pass
    return n


def sync_color_n_from_ui(context) -> int:
    """Belt-and-suspenders live sync from panel draw when RNA update is stale."""
    global _last_live_color_n
    try:
        color_n = int(getattr(context.scene, "arc_mask_debug_color_n", 6) or 6)
    except Exception:
        return 0
    if _last_live_color_n is not None and int(_last_live_color_n) == color_n:
        return 0
    return soft_update_color_n_selected(context)


def _soft_update_opacities(
    mat,
    *,
    overlay_opacity: float,
    colormask_opacity: float,
    numbers_opacity: float,
) -> bool:
    """Poke Overlay / ColorMask / Numbers Value nodes in place."""
    if mat is None or not mat.get(_INJECT_FLAG):
        return False
    if not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return False
    targets = {
        "OverlayOpacity": float(overlay_opacity),
        "ColorMaskOpacity": float(colormask_opacity),
        "NumbersOpacity": float(numbers_opacity),
    }
    n_hit = 0
    for n in mat.node_tree.nodes:
        if getattr(n, "type", "") != "VALUE":
            continue
        name = n.name or ""
        lab = n.label or ""
        for key, val in targets.items():
            if key in name or key in lab or key.replace("Opacity", " Opacity") in lab:
                try:
                    n.outputs[0].default_value = val
                    n_hit += 1
                except Exception:
                    pass
                break
    if n_hit > 0:
        _tag_mat_nodes_updated(mat)
    return n_hit > 0


def _soft_update_grid_scale(mat, grid_scale: float, extent: float = 0.0) -> bool:
    """Poke Digit/Checker Mapping Scale in place. No strip / Surface touch."""
    if mat is None or not mat.get(_INJECT_FLAG):
        return False
    if not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return False
    gs = max(0.5, float(grid_scale))
    dig_sx, dig_sy = _digit_tile_scale(grid_scale, extent)
    chk_s = gs * _CHECKER_BASE_SCALE
    n_hit = 0
    for n in mat.node_tree.nodes:
        if getattr(n, "type", "") != "MAPPING":
            continue
        name = (n.name or "")
        lab = (n.label or "")
        if not (
            name.startswith(_NODE_PFX)
            or lab.startswith(_NODE_PFX)
            or name in (_MAP_DIGIT_NAME, _MAP_CHECKER_NAME)
        ):
            continue
        try:
            if name == _MAP_DIGIT_NAME or "DigitMapping" in name or "DigitMapping" in lab:
                n.inputs["Scale"].default_value = (dig_sx, dig_sy, 1.0)
                n_hit += 1
            elif name == _MAP_CHECKER_NAME or "CheckerMapping" in name or "CheckerMapping" in lab:
                n.inputs["Scale"].default_value = (chk_s, chk_s, chk_s)
                n_hit += 1
            elif "Digit" in lab or "digit" in name.lower():
                n.inputs["Scale"].default_value = (dig_sx, dig_sy, 1.0)
                n_hit += 1
            elif "Checker" in lab or "checker" in name.lower():
                n.inputs["Scale"].default_value = (chk_s, chk_s, chk_s)
                n_hit += 1
        except Exception:
            pass
    return n_hit > 0


def _protected_pointers(nodes) -> set:
    """Material Output + ArcTexturer group + everything feeding that group.

    Inject nodes only ever feed the Output chain, never the group's inputs, so
    this can never protect a debug node.
    """
    keep = set()
    out = _find_output(nodes)
    if out is not None:
        keep.add(out.as_pointer())
    group = _find_arc_group(nodes)
    if group is None:
        return keep
    keep.add(group.as_pointer())
    frontier = [group]
    while frontier:
        node = frontier.pop()
        for sock in getattr(node, "inputs", []) or []:
            for lnk in getattr(sock, "links", []) or []:
                src = getattr(lnk, "from_node", None)
                if src is None:
                    continue
                ptr = src.as_pointer()
                if ptr in keep:
                    continue
                keep.add(ptr)
                frontier.append(src)
    return keep


def _unmangle_node(n) -> bool:
    """Drop a bogus ArcMaskDebug/ prefix left by the old id()-based tagger."""
    changed = False
    for attr in ("name", "label"):
        try:
            val = getattr(n, attr, "") or ""
        except Exception:
            continue
        for pfx in (_NODE_PFX, _LEGACY_INJECT_PFX):
            if val.startswith(pfx):
                try:
                    setattr(n, attr, val[len(pfx):])
                    changed = True
                except Exception:
                    pass
                break
    return changed


def _strip_inject(mat) -> bool:
    """Remove ArcMaskDebug/ (and legacy) nodes; restore Surface *before* deletes."""
    if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return False
    has_flag = bool(mat.get(_INJECT_FLAG) or mat.get(_LEGACY_INJECT_FLAG))
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Repair first: older builds mis-tagged real clothing nodes, and those would
    # otherwise be deleted below.
    protected = _protected_pointers(nodes)
    repaired = 0
    for n in list(nodes):
        if n.as_pointer() in protected or _is_structural_node(n):
            if _unmangle_node(n):
                repaired += 1
    if repaired:
        print(
            f"Arc Raiders Mask Debug: un-mangled {repaired} clothing node(s) "
            f"in '{mat.name}'"
        )

    tagged = []
    for n in list(nodes):
        if n.as_pointer() in protected or _is_structural_node(n):
            continue
        if _is_inject_node(n):
            tagged.append(n)
    if not tagged and not has_flag:
        return bool(repaired)

    out = _find_output(nodes)
    raw = mat.get(_INJECT_SURFACE_JSON) or mat.get(_LEGACY_INJECT_JSON)

    # 1) Reconnect Surface to the original shader WHILE inject nodes still exist.
    restored = False
    if out is not None:
        from_sock = None
        if raw:
            try:
                info = json.loads(raw)
                from_node = nodes.get(info.get("node") or "")
                sock_name = info.get("socket") or ""
                if from_node is not None and from_node not in tagged:
                    cand = from_node.outputs.get(sock_name) if sock_name else None
                    if cand is not None and cand.type == "SHADER":
                        from_sock = cand
                    else:
                        for sock in from_node.outputs:
                            if sock.type == "SHADER":
                                from_sock = sock
                                break
            except Exception:
                from_sock = None
        if from_sock is None:
            from_sock = _find_any_surface_shader(nodes)
        if from_sock is not None and from_sock.type == "SHADER":
            try:
                while out.inputs["Surface"].is_linked:
                    links.remove(out.inputs["Surface"].links[0])
                links.new(from_sock, out.inputs["Surface"])
                restored = True
            except Exception:
                pass

    # 2) Now safe to delete inject nodes (Output no longer depends on them).
    for n in tagged:
        try:
            nodes.remove(n)
        except Exception:
            pass

    if out is not None and not out.inputs["Surface"].is_linked:
        fallback = _find_any_surface_shader(nodes)
        if fallback is not None and fallback.type == "SHADER":
            try:
                links.new(fallback, out.inputs["Surface"])
                restored = True
            except Exception:
                pass

    for key in (
        _INJECT_FLAG,
        _INJECT_SURFACE_JSON,
        _INJECT_SIG,
        _LEGACY_INJECT_FLAG,
        _LEGACY_INJECT_JSON,
    ):
        try:
            del mat[key]
        except Exception:
            pass
    return True


def _restore_legacy_slot_copies(obj) -> bool:
    """Restore original slots if an older install left .arc_mask_debug_* copies."""
    raw = obj.get(_LEGACY_BACKUP_KEY)
    if not raw:
        # Still drop any debug mats sitting in slots
        changed = False
        for slot in obj.material_slots:
            if slot.material and _is_debug_mat(slot.material):
                slot.material = None
                changed = True
        return changed
    try:
        names = json.loads(raw)
    except Exception:
        return False
    for i, slot in enumerate(obj.material_slots):
        name = names[i] if i < len(names) else ""
        if name:
            slot.material = bpy.data.materials.get(name)
        else:
            slot.material = None
    try:
        del obj[_LEGACY_BACKUP_KEY]
    except Exception:
        pass
    # Remove unused legacy debug mats
    for mat in list(bpy.data.materials):
        if _is_debug_mat(mat) and mat.users == 0:
            try:
                bpy.data.materials.remove(mat)
            except Exception:
                pass
    return True


def _node_world_xy(n):
    x, y = float(n.location.x), float(n.location.y)
    p = getattr(n, "parent", None)
    while p is not None:
        x += float(p.location.x)
        y += float(p.location.y)
        p = getattr(p, "parent", None)
    return x, y


def _debug_cluster_origin(nodes):
    """Shirt 002: debug stack sits left/below Principled, not at (+2400, +400)."""
    nd = nodes.get("GT_Principled")
    if nd is None:
        nd = nodes.get("GT_MaterialOutput")
    if nd is None:
        for n in nodes:
            if getattr(n, "type", "") == "BSDF_PRINCIPLED" and not _is_inject_node(n):
                nd = n
                break
    if nd is None:
        return (_dx(-800), _dy(200))
    wx, wy = _node_world_xy(nd)
    return (wx - 2200.0, wy - 840.0)


def _inject_mask_debug(
    mat,
    *,
    mode: int,
    source: str,
    color_n: int,
    grid_scale: float,
    show_numbers: bool,
    show_colormask: bool,
    overlay_opacity: float,
    colormask_opacity: float,
    numbers_opacity: float,
    extent: float = 0.0,
) -> str:
    """Strip prior inject and build the debug stack. Returns status or error.

    Never leaves Material Output Surface unlinked (that blacks the mesh and makes
    the shader editor look empty).
    """
    if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return "no nodes"
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    out = _find_output(nodes)
    if out is None:
        return "no output"

    # Resolve the real clothing Surface *before* strip (Surface may still point at DigitsMix).
    tagged = _list_inject_nodes(nodes)
    orig_shader = _resolve_base_shader(mat, nodes, out, exclude=tagged)
    if orig_shader is None or orig_shader.type != "SHADER":
        return "no surface"
    orig_node_name = getattr(orig_shader.node, "name", "") or ""
    orig_sock_name = getattr(orig_shader, "name", "") or ""
    surface_meta = json.dumps({"node": orig_node_name, "socket": orig_sock_name})

    _strip_inject(mat)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    out = _find_output(nodes)
    if out is None:
        return "no output"

    # Re-bind after strip (JSON was cleared); prefer live Surface, else saved names.
    orig_shader = _resolve_base_shader(mat, nodes, out)
    if orig_shader is None or orig_shader.type != "SHADER":
        node = nodes.get(orig_node_name)
        orig_shader = _shader_sock_from_node(node, orig_sock_name)
    if orig_shader is None or orig_shader.type != "SHADER":
        return "no surface"
    orig_node_name = getattr(orig_shader.node, "name", "") or ""
    orig_sock_name = getattr(orig_shader, "name", "") or ""
    surface_meta = json.dumps({"node": orig_node_name, "socket": orig_sock_name})

    def _reconnect_base():
        try:
            while out.inputs["Surface"].is_linked:
                links.remove(out.inputs["Surface"].links[0])
            if orig_shader is not None and orig_shader.type == "SHADER":
                links.new(orig_shader, out.inputs["Surface"])
                return
        except Exception:
            pass
        fb = _find_any_surface_shader(nodes)
        if fb is not None and fb.type == "SHADER":
            try:
                while out.inputs["Surface"].is_linked:
                    links.remove(out.inputs["Surface"].links[0])
                links.new(fb, out.inputs["Surface"])
            except Exception:
                pass

    # Detach only after we know we can put it back. Do NOT set FLAG until success.
    if out.inputs["Surface"].is_linked:
        links.remove(out.inputs["Surface"].links[0])

    before_names = {(n.name or "") for n in nodes}
    status = "ok"
    built_ok = False
    try:
        maps = discover_mask_maps(mat)
        ocm = maps.get("ocm")
        cm = maps.get("colormask")
        albedo = maps.get("basecolor")
        ox, oy = _debug_cluster_origin(nodes)

        def dxy(x, y):
            return (ox + (float(x) - (-800.0)), oy + (float(y) - 200.0))

        zone_sock, mismatch_rgb = _build_zone(
            nodes, links, source=source, ocm_image=ocm, origin=dxy(-800, 200)
        )
        if zone_sock is None and mode not in (0, 2):
            status = "zone source failed (no OCM / ZoneIndex)"
            return status

        current = orig_shader
        bits = [f"src={source}", f"mode={mode}"]
        if _find_arc_group(nodes) is None:
            bits.append("arc-group-missing")

        overlay_rgb = None
        overlay_cov = None
        if (source or "").lower() == "mismatch" and mismatch_rgb is not None:
            overlay_rgb = mismatch_rgb
            overlay_cov = None
            bits.append("mismatch")
        elif mode == 1:
            if zone_sock is None:
                status = "zone required for Mode 1"
                return status
            overlay_rgb, overlay_cov = _mode1_overlay(
                nodes, links, zone_sock, color_n, grid_scale, dxy(0, 400)
            )
        elif mode == 2:
            overlay_rgb, overlay_cov = _mode2_overlay(
                nodes, links, ocm, dxy(0, 400)
            )
        elif mode == 3:
            if zone_sock is None:
                status = "zone required for Mode 3"
                return status
            overlay_rgb, overlay_cov = _mode3_overlay(
                nodes, links, zone_sock, dxy(0, 400)
            )

        if overlay_rgb is not None and (
            mode != 0 or (source or "").lower() == "mismatch"
        ):
            opac = _value(
                nodes, f"{_NODE_PFX}OverlayOpacity", overlay_opacity,
                dxy(1400, 520), "Overlay Opacity",
            )
            if overlay_cov is not None:
                fac_m = _math(
                    nodes, "MULTIPLY", dxy(1560, 520), f"{_NODE_PFX}ov×cov", clamp=True
                )
                links.new(opac.outputs[0], fac_m.inputs[0])
                links.new(overlay_cov, fac_m.inputs[1])
                fac = fac_m.outputs[0]
            else:
                fac = opac.outputs[0]
            em = nodes.new("ShaderNodeEmission")
            em.label = f"{_NODE_PFX}OverlayEm"
            em.location = dxy(1560, 400)
            em.inputs["Strength"].default_value = 1.0
            if isinstance(overlay_rgb, tuple):
                em.inputs["Color"].default_value = (*overlay_rgb[:3], 1.0)
            else:
                links.new(overlay_rgb, em.inputs["Color"])
            mix = nodes.new("ShaderNodeMixShader")
            mix.label = f"{_NODE_PFX}OverlayMix"
            mix.location = dxy(1760, 300)
            fac_in, sh_a, sh_b, sh_out = _mix_shader_sockets(mix)
            links.new(fac, fac_in)
            links.new(current, sh_a)
            links.new(em.outputs["Emission"], sh_b)
            current = sh_out

        if show_colormask and cm is not None:
            cm_op = _value(
                nodes, f"{_NODE_PFX}ColorMaskOpacity", colormask_opacity,
                dxy(1400, 200), "ColorMask Opacity",
            )
            cm_tex = nodes.new("ShaderNodeTexImage")
            cm_tex.image = cm
            cm_tex.interpolation = "Closest"
            cm_tex.extension = "EXTEND"
            cm_tex.label = f"{_NODE_PFX}ColorMask"
            cm_tex.location = dxy(1200, 120)
            cm_tc = nodes.new("ShaderNodeTexCoord")
            cm_tc.location = dxy(1000, 120)
            links.new(cm_tc.outputs["UV"], cm_tex.inputs["Vector"])
            # Use alpha if present else luminance of color as coverage
            cov = cm_tex.outputs.get("Alpha") or cm_tex.outputs["Color"]
            fac_m = _math(
                nodes, "MULTIPLY", dxy(1560, 200), f"{_NODE_PFX}cm×op", clamp=True
            )
            links.new(cm_op.outputs[0], fac_m.inputs[0])
            if cov.type == "RGBA":
                bw = nodes.new("ShaderNodeRGBToBW")
                bw.location = dxy(1400, 120)
                links.new(cov, bw.inputs["Color"])
                links.new(bw.outputs["Val"], fac_m.inputs[1])
            else:
                links.new(cov, fac_m.inputs[1])
            em = nodes.new("ShaderNodeEmission")
            em.label = f"{_NODE_PFX}ColorMaskEm"
            em.location = dxy(1560, 80)
            em.inputs["Strength"].default_value = 1.0
            links.new(cm_tex.outputs["Color"], em.inputs["Color"])
            mix = nodes.new("ShaderNodeMixShader")
            mix.label = f"{_NODE_PFX}ColorMaskMix"
            mix.location = dxy(1920, 160)
            fac_in, sh_a, sh_b, sh_out = _mix_shader_sockets(mix)
            links.new(fac_m.outputs[0], fac_in)
            links.new(current, sh_a)
            links.new(em.outputs["Emission"], sh_b)
            current = sh_out
            bits.append("colormask")

        if show_numbers and zone_sock is not None:
            dig_rgb, dig_cov = _build_digits(
                nodes, links, zone_sock, albedo, grid_scale,
                dxy(-2600, -400), extent,
            )
            if dig_rgb is not None and dig_cov is not None:
                num_op = _value(
                    nodes, f"{_NODE_PFX}NumbersOpacity", numbers_opacity,
                    dxy(1400, -40), "Numbers Opacity",
                )
                fac_m = _math(
                    nodes, "MULTIPLY", dxy(1560, -40), f"{_NODE_PFX}dig×op", clamp=True
                )
                links.new(num_op.outputs[0], fac_m.inputs[0])
                links.new(dig_cov, fac_m.inputs[1])
                em = nodes.new("ShaderNodeEmission")
                em.label = f"{_NODE_PFX}DigitsEm"
                em.location = dxy(1560, -160)
                em.inputs["Strength"].default_value = 1.0
                links.new(dig_rgb, em.inputs["Color"])
                mix = nodes.new("ShaderNodeMixShader")
                mix.label = f"{_NODE_PFX}DigitsMix"
                mix.location = dxy(2080, 80)
                fac_in, sh_a, sh_b, sh_out = _mix_shader_sockets(mix)
                links.new(fac_m.outputs[0], fac_in)
                links.new(current, sh_a)
                links.new(em.outputs["Emission"], sh_b)
                current = sh_out
                bits.append("digits")
            else:
                bits.append("digits-fail")

        links.new(current, out.inputs["Surface"])
        _tag(nodes, before_names)
        try:
            mat[_INJECT_SURFACE_JSON] = surface_meta
            mat[_INJECT_FLAG] = 1
            mat[_INJECT_SIG] = _inject_signature(
                mode=mode,
                source=source,
                color_n=color_n,
                show_numbers=show_numbers,
                show_colormask=show_colormask,
                overlay_opacity=overlay_opacity,
                colormask_opacity=colormask_opacity,
                numbers_opacity=numbers_opacity,
            )
        except Exception:
            pass
        built_ok = True
        status = "; ".join(bits)
        return status
    except Exception as exc:
        status = f"inject-error:{exc}"
        print(f"Arc Raiders Mask Debug inject failed: {exc}")
        return status
    finally:
        # Guaranteed: never leave Surface unlinked; roll back failed builds.
        if not built_ok:
            try:
                _reconnect_base()
            except Exception:
                pass
            try:
                _strip_inject(mat)
            except Exception:
                pass
        elif out is not None and not out.inputs["Surface"].is_linked:
            _reconnect_base()


# ---------------------------------------------------------------------------
# Public apply / clear
# ---------------------------------------------------------------------------

def apply_mask_debug_to_object(
    obj,
    mode: int,
    *,
    source: str = "procedural",
    color_n: int = 6,
    grid_scale: float = 1.0,
    show_colormask: bool = False,
    show_numbers: bool = True,
    overlay_opacity: float = 0.5,
    colormask_opacity: float = 0.5,
    numbers_opacity: float = 1.0,
) -> str:
    if obj is None or getattr(obj, "type", "") != "MESH":
        return "skip non-mesh"
    mode = int(mode)
    # Legacy cleanup first
    _restore_legacy_slot_copies(obj)
    try:
        del obj[_LEGACY_CURV_BACKUP]
    except Exception:
        pass

    if mode <= 0 and not show_numbers and not show_colormask:
        for slot in obj.material_slots:
            if slot.material:
                _strip_inject(slot.material)
        for src in _source_materials_for_object(obj):
            _strip_inject(src)
        return "restored"

    if not obj.material_slots:
        return "no materials"

    # Auto-fit glyph size to this mesh (local bbox, so cm- and m-scale both work).
    extent = _object_local_extent(obj)

    want_sig = _inject_signature(
        mode=mode,
        source=source,
        color_n=color_n,
        show_numbers=show_numbers,
        show_colormask=show_colormask,
        overlay_opacity=overlay_opacity,
        colormask_opacity=colormask_opacity,
        numbers_opacity=numbers_opacity,
    )

    # Soft path: same topology signature + inject already present → poke Mapping Scale only.
    soft_mats = []
    force_rebuild = False
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or _is_debug_mat(mat):
            continue
        if not mat.get(_INJECT_FLAG):
            force_rebuild = True
            break
        if str(mat.get(_INJECT_SIG) or "") != want_sig:
            force_rebuild = True
            break
        soft_mats.append(mat)
    if not force_rebuild and soft_mats:
        soft_ok = 0
        for mat in soft_mats:
            _soft_update_grid_scale(mat, grid_scale, extent)
            _soft_update_color_n(mat, color_n)
            _soft_update_opacities(
                mat,
                overlay_opacity=overlay_opacity,
                colormask_opacity=colormask_opacity,
                numbers_opacity=numbers_opacity,
            )
            soft_ok += 1
        return f"soft-update={soft_ok}"

    applied = 0
    last = ""
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or _is_debug_mat(mat):
            continue
        last = _inject_mask_debug(
            mat,
            mode=mode,
            source=source,
            color_n=color_n,
            grid_scale=grid_scale,
            show_numbers=show_numbers,
            show_colormask=show_colormask,
            overlay_opacity=overlay_opacity,
            colormask_opacity=colormask_opacity,
            numbers_opacity=numbers_opacity,
            extent=extent,
        )
        if not last.startswith("no ") and "failed" not in last:
            applied += 1
    return f"inject={applied}; {last}"


def apply_mask_debug_selected(context, mode=None) -> tuple[int, int, str]:
    scene = context.scene
    if mode is None:
        try:
            mode = int(getattr(scene, "arc_mask_debug_mode", 0) or 0)
        except Exception:
            mode = 0
    source = str(getattr(scene, "arc_mask_debug_source", "procedural") or "procedural")
    show_cm = bool(getattr(scene, "arc_mask_debug_show_colormask", False))
    show_nums = bool(getattr(scene, "arc_mask_debug_show_numbers", True))
    color_n = int(getattr(scene, "arc_mask_debug_color_n", 6) or 6)
    grid_scale = max(0.5, min(10.0, float(getattr(scene, "arc_mask_debug_grid_scale", 1.0) or 1.0)))
    overlay_opacity = float(getattr(scene, "arc_mask_debug_overlay_opacity",
                                    getattr(scene, "arc_mask_debug_opacity", 0.5)))
    if overlay_opacity != overlay_opacity:
        overlay_opacity = 0.5
    colormask_opacity = float(getattr(scene, "arc_mask_debug_colormask_opacity", 0.5))
    if colormask_opacity != colormask_opacity:
        colormask_opacity = 0.5
    numbers_opacity = float(getattr(scene, "arc_mask_debug_numbers_opacity", 1.0))
    if numbers_opacity != numbers_opacity:
        numbers_opacity = 1.0

    ok = skip = 0
    last_status = ""
    for obj in context.selected_objects:
        if getattr(obj, "type", "") != "MESH":
            skip += 1
            continue
        last_status = apply_mask_debug_to_object(
            obj,
            mode,
            source=source,
            color_n=color_n,
            grid_scale=grid_scale,
            show_colormask=show_cm,
            show_numbers=show_nums,
            overlay_opacity=overlay_opacity,
            colormask_opacity=colormask_opacity,
            numbers_opacity=numbers_opacity,
        )
        ok += 1
    return ok, skip, last_status


def clear_mask_debug_selected(context) -> int:
    n = 0
    for obj in context.selected_objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        restored = _restore_legacy_slot_copies(obj)
        for slot in obj.material_slots:
            if slot.material and _strip_inject(slot.material):
                restored = True
        for src in _source_materials_for_object(obj):
            if _strip_inject(src):
                restored = True
        try:
            del obj[_LEGACY_CURV_BACKUP]
        except Exception:
            pass
        if restored:
            n += 1
    return n


def read_tuned_bands(mat) -> list[float] | None:
    """Optional: read BandRamp stop positions from an injected material."""
    if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return None
    ramp = mat.node_tree.nodes.get(f"{_NODE_PFX}BandRamp")
    if ramp is None:
        for n in mat.node_tree.nodes:
            if (n.label or "") == f"{_NODE_PFX}BandRamp" or (n.name or "").endswith("BandRamp"):
                ramp = n
                break
    if ramp is None or not hasattr(ramp, "color_ramp"):
        return None
    try:
        positions = sorted(float(el.position) for el in ramp.color_ramp.elements)
        # Return as BANDS-style list ending with 1.01
        if positions[-1] < 1.0:
            positions.append(1.01)
        return positions
    except Exception:
        return None
