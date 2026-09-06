"""Color-benchmark inspection materials (no ArcTexturer).

Builds a flat, left-to-right node graph for B1–B8 so cooked scheme / assemble /
overlay / ColorTex / wear stages are readable without ArcTexturer nesting or
hand-dragging overlaps.

Primary albedo path follows cooked D027/D031/D034/D044:
  OutX = lerp(ColorX, ColorX2, ColorMask.r)
  fac  = amt × BaseColor × ColorMaskSwatch
  col  = lerp(1, OutA, fac.r) → OutB → OutC
  col *= BaseColorOverlay × ColorTex_blend
  wear = Edge/Crease overlays biased by ColorMask.b/.g (labeled)

A side branch shows the Arc-safe Swatch-only fac (no BaseColor) for comparison.
"""
from __future__ import annotations

import os
from typing import Any

import bpy

from . import common as _common
from .. import textures, utils, importing, palette_calibration

# Column X centers (Blender node editor units). Wide gaps → no overlap.
_COL_X = {
    "textures": -2400.0,
    "separates": -1800.0,
    "palette": -1200.0,
    "scheme": -600.0,
    "assemble": 200.0,
    "post": 1000.0,
    "output": 1700.0,
}
_ROW_DY = 160.0
_STAGE_GAP = 100.0


# Repo-relative MI paths under Characters/Assets/… (see docs/OUTFIT_COLOR_BENCHMARK.md).
COLOR_BENCHMARKS: dict[str, dict[str, str]] = {
    "B1": {
        "label": "Abyss Lowerbody Cotton",
        "character": "Abyss",
        "part": "Lowerbody",
        "colorway": "Cotton",
        "mi_rel": "Abyss/Lowerbody/Skins/Cotton/MI_Abyss_Lowerbody_Cotton.json",
    },
    "B2": {
        "label": "Abyss Lowerbody Cotton_YellowBlack",
        "character": "Abyss",
        "part": "Lowerbody",
        "colorway": "Cotton_YellowBlack",
        "mi_rel": (
            "Abyss/Lowerbody/Skins/Cotton_YellowBlack/"
            "MI_Abyss_Lowerbody_Cotton_YellowBlack.json"
        ),
    },
    "B3": {
        "label": "Moonball Upperbody Polyester",
        "character": "Moonball",
        "part": "Upperbody",
        "colorway": "Polyester",
        "mi_rel": "Moonball/Upperbody/Skins/Polyester/MI_Moonball_UpperBody_Polyester.json",
    },
    "B4": {
        "label": "Moonball Lowerbody Cotton",
        "character": "Moonball",
        "part": "Lowerbody",
        "colorway": "Cotton",
        "mi_rel": "Moonball/Lowerbody/Skins/Cotton/MI_Moonball_LowerBody_Cotton.json",
    },
    "B5": {
        "label": "Horns Upperbody Leather_Black",
        "character": "Horns",
        "part": "Upperbody",
        "colorway": "Leather_Black",
        "mi_rel": "Horns/Upperbody/Skins/Leather_Black/MI_Horns_Upperbody_Leather_Black.json",
    },
    "B6": {
        "label": "Horns Upperbody Leather",
        "character": "Horns",
        "part": "Upperbody",
        "colorway": "Leather",
        "mi_rel": "Horns/Upperbody/Skins/Leather/MI_Horns_Upperbody_Leather.json",
    },
    "B7": {
        "label": "Dweller Lowerbody Leather_Yellow",
        "character": "Dweller",
        "part": "Lowerbody",
        "colorway": "Leather_Yellow",
        "mi_rel": (
            "Dweller/Lowerbody/Skins/Leather_Yellow/"
            "MI_Dweller_Lowerbody_Leather_Yellow.json"
        ),
    },
    "B8": {
        "label": "Batter Upperbody Quilted_Blue",
        "character": "Batter",
        "part": "Upperbody",
        "colorway": "Quilted_Blue",
        "mi_rel": "Batter/Upperbody/Skins/Quilted_Blue/MI_Batter_Upperbody_Quilted_Blue.json",
    },
}

BENCHMARK_IDS = tuple(COLOR_BENCHMARKS.keys())


def _mi_abs(root: str, mi_rel: str) -> str:
    """Resolve Characters/Assets/<mi_rel> under Pioneer Content."""
    parts = ["Characters", "Assets"] + [p for p in mi_rel.replace("\\", "/").split("/") if p]
    folder_parts = parts[:-1]
    fname = parts[-1]
    part_dir = utils.find_relative_dir(root, folder_parts)
    if part_dir:
        candidate = os.path.join(part_dir, fname)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    for content in utils.get_content_dirs() or []:
        cand = os.path.join(content, "Pioneer", *parts)
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return ""


def resolve_benchmark(root: str, bench_id: str) -> dict[str, Any]:
    """Resolve PSK + MI + part folder for a benchmark id. Raises KeyError if unknown."""
    meta = COLOR_BENCHMARKS[bench_id]
    mi_path = _mi_abs(root, meta["mi_rel"])
    part_folder = utils.find_relative_dir(
        root, ["Characters", "Assets", meta["character"], meta["part"]]
    )
    psk = importing.find_psk_in_specific_folder(part_folder) if part_folder else None
    skins_folder = os.path.dirname(mi_path) if mi_path else ""
    return {
        "id": bench_id,
        "label": meta["label"],
        "character": meta["character"],
        "part": meta["part"],
        "colorway": meta["colorway"],
        "mi_path": mi_path,
        "psk_path": psk or "",
        "part_folder": part_folder or "",
        "skins_folder": skins_folder,
        "ok": bool(psk and mi_path),
    }


def layout_benchmark_nodes(nodes, stage_of: dict[str, str] | None = None) -> int:
    """Place nodes on a non-overlapping column/row grid by stage tag."""
    stage_of = stage_of or {}
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass

    buckets: dict[str, list] = {k: [] for k in _COL_X}
    for node in list(nodes):
        if getattr(node, "bl_idname", "") == "NodeFrame" or getattr(node, "type", "") == "FRAME":
            continue
        stage = stage_of.get(node.name)
        if not stage:
            try:
                stage = node.get("arc_bench_stage")
            except Exception:
                stage = None
        if stage not in buckets:
            continue
        buckets[stage].append(node)

    placed = 0
    for stage, col_nodes in buckets.items():
        if not col_nodes:
            continue
        col_nodes.sort(key=lambda n: (-float(getattr(n, "location", (0, 0))[1]), n.name))
        x = float(_COL_X[stage])
        y = 600.0
        for node in col_nodes:
            try:
                node.hide = False
            except Exception:
                pass
            try:
                node.parent = None
            except Exception:
                pass
            try:
                h = float(node.dimensions.y) if getattr(node, "dimensions", None) else 0.0
            except Exception:
                h = 0.0
            if h < 40.0:
                h = _ROW_DY
            node.location = (x, y)
            y -= h + _STAGE_GAP
            placed += 1
            try:
                node["arc_bench_stage"] = stage
            except Exception:
                pass
    return placed


def _tag(node, stage: str):
    try:
        node["arc_bench_stage"] = stage
    except Exception:
        pass
    return node


def _rgb_node(nodes, name: str, label: str, rgba, location=(0.0, 0.0)):
    nd = nodes.new("ShaderNodeRGB")
    nd.name = name
    nd.label = label
    r, g, b = float(rgba[0]), float(rgba[1]), float(rgba[2])
    a = float(rgba[3]) if len(rgba) > 3 else 1.0
    nd.outputs[0].default_value = (r, g, b, a)
    nd.location = location
    return nd


def _new_mix_rgba(nodes, name: str, label: str, location=(0.0, 0.0)):
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MIX"
    mix.name = name
    mix.label = label
    mix.location = location
    mix.hide = False
    return mix


def _new_mul_rgba(nodes, name: str, label: str, location=(0.0, 0.0)):
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.name = name
    mix.label = label
    mix.location = location
    mix.hide = False
    try:
        mix.inputs[0].default_value = 1.0
    except Exception:
        pass
    return mix


def _find_role_png(folder: str, role: str) -> str:
    if not folder or not os.path.isdir(folder):
        return ""
    try:
        names = os.listdir(folder)
    except OSError:
        return ""
    for fname in sorted(names):
        if not fname.lower().endswith(".png"):
            continue
        if textures.identify_texture(fname) == role:
            return os.path.join(folder, fname)
    return ""


def _tex_node(nodes, path: str, label: str, non_color: bool, location=(0.0, 0.0)):
    img = _common._load_image_cached(path) if path else None
    node = nodes.new("ShaderNodeTexImage")
    node.label = label
    # Blender node names can't be arbitrary long / special; keep stable id.
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in label)[:40]
    node.name = f"BenchTex_{safe}"
    node.location = location
    node.hide = False
    if img is not None:
        node.image = img
        try:
            if non_color:
                img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
        try:
            node.interpolation = "Cubic"
        except Exception:
            pass
    return node


def _sep_channel(sep, name: str, index: int):
    try:
        return sep.outputs[name]
    except Exception:
        return sep.outputs[index]


def _pick_hero_layer(colours: dict, zone_scalars: dict, ta_ids: dict) -> int:
    """Choose which MI layer drives the inspection assemble (D043 ColorTex wins)."""
    for key, val in (ta_ids or {}).items():
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        if key[1] != "ColorTextureID":
            continue
        try:
            if float(val) >= 1.0:
                return max(1, min(8, int(key[0])))
        except (TypeError, ValueError):
            continue

    strengths = palette_calibration.extract_base_color_mask_strengths(zone_scalars)
    if strengths:
        best_z, best_s = max(strengths.items(), key=lambda kv: float(kv[1]))
        if float(best_s) > 0.0:
            return int(best_z)

    for z in range(1, 9):
        sw = colours.get(f"{z}_ColorMaskSwatch")
        if sw is None:
            continue
        # Non-white / non-zero swatch is a useful hero.
        if any(abs(float(c) - 1.0) > 0.02 for c in sw[:3]) or any(float(c) < 0.98 for c in sw[:3]):
            return z
    return 1


def _scalar_for_layer(zone_scalars: dict, layer: int, suffix: str, default: float) -> float:
    for key, val in (zone_scalars or {}).items():
        zone_s = suffix_s = None
        if isinstance(key, tuple) and len(key) == 2:
            zone_s, suffix_s = key[0], key[1]
        elif isinstance(key, str) and key == f"{layer}_{suffix}":
            return float(val)
        if suffix_s == suffix and str(zone_s) == str(layer):
            try:
                return float(val)
            except (TypeError, ValueError):
                return default
    return default


def _ta_id_for_layer(ta_ids: dict, layer: int, suffix: str) -> float:
    for key, val in (ta_ids or {}).items():
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        if str(key[0]) == str(layer) and key[1] == suffix:
            try:
                return float(val)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def setup_benchmark_inspection_material(
    obj,
    *,
    bench_id: str,
    mi_path: str,
    part_folder: str,
    psk_path: str = "",
) -> bpy.types.Material | None:
    """Build a flat cooked-inspection material (no ArcTexturer) on ``obj``."""
    if obj is None or obj.type != "MESH":
        return None
    mi_data = textures.parse_clothing_mi(mi_path) if mi_path else {}
    colours = mi_data.get("colours") or {}
    zone_scalars = mi_data.get("zone_scalars") or {}
    ta_ids = mi_data.get("ta_ids") or {}

    hero = _pick_hero_layer(colours, zone_scalars, ta_ids)
    amt = _scalar_for_layer(zone_scalars, hero, "BaseColorMaskStrength", 1.0)
    amt = max(0.0, min(1.0, float(amt)))
    tex_strength = _scalar_for_layer(zone_scalars, hero, "BaseTextureStrength", 0.0)
    colortex_id = _ta_id_for_layer(ta_ids, hero, "ColorTextureID")

    swatch = (
        colours.get(f"{hero}_ColorMaskSwatch")
        or colours.get("N_ColorMaskSwatch")
        or (1.0, 1.0, 1.0, 1.0)
    )
    overlay = (
        colours.get(f"{hero}_BaseColorOverlay")
        or colours.get("N_BaseColorOverlay")
        or (1.0, 1.0, 1.0, 1.0)
    )
    edge_ov = colours.get(f"{hero}_EdgeColorOverlay") or (0.0, 0.0, 0.0, 0.0)
    crease_ov = colours.get(f"{hero}_CreaseColorOverlay") or (0.0, 0.0, 0.0, 0.0)
    edge_amt = _scalar_for_layer(zone_scalars, hero, "EdgeAmount", 0.0)
    crease_amt = _scalar_for_layer(zone_scalars, hero, "CreaseAmount", 0.0)

    mat_name = f"Bench_{bench_id}_{obj.name}"[:63]
    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    try:
        mat["arc_benchmark_id"] = bench_id
        mat["arc_mi_path"] = os.path.abspath(mi_path) if mi_path else ""
        mat["arc_outfit_color_pipeline"] = "benchmark_inspection"
        mat["arc_inspection"] = 1
        mat["arc_bench_hero_layer"] = int(hero)
    except Exception:
        pass

    try:
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)
    except Exception:
        obj.active_material = mat

    nt = mat.node_tree
    nodes = nt.nodes
    links = nt.links
    nodes.clear()

    stage_of: dict[str, str] = {}

    def add(node, stage: str):
        _tag(node, stage)
        stage_of[node.name] = stage
        return node

    # --- Textures ---
    ocm_path = _find_role_png(part_folder, "occlusion")
    cm_path = _find_role_png(part_folder, "colormask")
    bc_path = _find_role_png(part_folder, "basecolor")
    nrm_path = _find_role_png(part_folder, "normal")

    tex_ocm = add(
        _tex_node(nodes, ocm_path, "OCM OcclusionCurvatureMaterialID", True),
        "textures",
    )
    tex_cm = add(_tex_node(nodes, cm_path, "ColorMask", True), "textures")
    tex_bc = add(_tex_node(nodes, bc_path, "BaseColor", False), "textures")
    tex_nrm = None
    if nrm_path:
        tex_nrm = add(_tex_node(nodes, nrm_path, "Normal", True), "textures")

    # ColorTex slice (D043): only when authored ID >= 1
    tex_colortex = None
    colortex_path = ""
    if colortex_id >= 1.0 and psk_path:
        try:
            base_pngs = textures.scan_base_skin_textures(psk_path) or []
            slice_map = textures.build_slice_png_map(base_pngs)
            # Prefer "other" / colortexture-like stems; fall back to any _N slice.
            idx = int(round(colortex_id))
            colortex_path = slice_map.get(idx) or textures.find_slice_png(base_pngs, idx)
            if not colortex_path:
                # Also search part folder for *_N.png
                colortex_path = textures.find_slice_png(
                    [
                        os.path.join(part_folder, f)
                        for f in (os.listdir(part_folder) if part_folder and os.path.isdir(part_folder) else [])
                        if f.lower().endswith(".png")
                    ],
                    idx,
                )
        except Exception:
            colortex_path = ""
        if colortex_path:
            tex_colortex = add(
                _tex_node(
                    nodes,
                    colortex_path,
                    f"ColorTex slice ID={int(round(colortex_id))}",
                    False,
                ),
                "textures",
            )

    # --- Separates ---
    sep_cm = add(nodes.new("ShaderNodeSeparateColor"), "separates")
    sep_cm.name = "Bench_ColorMask_Sep"
    sep_cm.label = "ColorMask.rgb (r=scheme; g=crease; b=edge)"
    if tex_cm.image:
        links.new(tex_cm.outputs["Color"], sep_cm.inputs[0])

    sep_ocm = add(nodes.new("ShaderNodeSeparateColor"), "separates")
    sep_ocm.name = "Bench_OCM_Sep"
    sep_ocm.label = "OCM.rgb (B=MID zone / G=curvature)"
    if tex_ocm.image:
        links.new(tex_ocm.outputs["Color"], sep_ocm.inputs[0])

    sep_bc = add(nodes.new("ShaderNodeSeparateColor"), "separates")
    sep_bc.name = "Bench_BaseColor_Sep"
    sep_bc.label = "BaseColor.rgb (cooked fac channel)"
    if tex_bc.image:
        links.new(tex_bc.outputs["Color"], sep_bc.inputs[0])

    # --- Palette ---
    palette_nodes = {}
    for key in ("ColorA", "ColorB", "ColorC", "ColorA2", "ColorB2", "ColorC2"):
        rgba = colours.get(key) or (0.5, 0.5, 0.5, 1.0)
        palette_nodes[key] = add(
            _rgb_node(nodes, f"Bench_{key}", key, rgba),
            "palette",
        )

    sw_nd = add(
        _rgb_node(nodes, "Bench_Swatch", f"{hero}_ColorMaskSwatch", swatch),
        "palette",
    )
    ov_nd = add(
        _rgb_node(nodes, "Bench_Overlay", f"{hero}_BaseColorOverlay", overlay),
        "palette",
    )
    edge_nd = add(
        _rgb_node(nodes, "Bench_EdgeOverlay", f"{hero}_EdgeColorOverlay", edge_ov),
        "palette",
    )
    crease_nd = add(
        _rgb_node(nodes, "Bench_CreaseOverlay", f"{hero}_CreaseColorOverlay", crease_ov),
        "palette",
    )

    # Expose other layers' swatches for reading (not wired into albedo).
    for z in range(1, 9):
        if z == hero:
            continue
        sw = colours.get(f"{z}_ColorMaskSwatch")
        if sw is None:
            continue
        add(
            _rgb_node(nodes, f"Bench_Swatch_L{z}", f"{z}_ColorMaskSwatch (inspect)", sw),
            "palette",
        )

    bits = palette_calibration.extract_layer_mask_bits(zone_scalars)
    if bits:
        bit_note = add(nodes.new("ShaderNodeRGB"), "palette")
        bit_note.name = "Bench_LayerMask_Bits"
        bit_note.label = f"LayerMask bits L{hero} hero | " + ",".join(
            f"L{k}={v}" for k, v in sorted(bits.items())
        )
        bit_note.outputs[0].default_value = (0.2, 0.2, 0.2, 1.0)
        bit_note.hide = True

    # --- Scheme: OutX = lerp(ColorX, ColorX2, ColorMask.r) ---
    scheme = {}
    for out_k, pri, sec in (
        ("OutA", "ColorA", "ColorA2"),
        ("OutB", "ColorB", "ColorB2"),
        ("OutC", "ColorC", "ColorC2"),
    ):
        mix = add(
            _new_mix_rgba(nodes, f"Bench_Scheme_{pri}", f"lerp({pri},{sec},ColorMask.r)"),
            "scheme",
        )
        links.new(_sep_channel(sep_cm, "Red", 0), mix.inputs[0])
        links.new(palette_nodes[pri].outputs[0], mix.inputs[6])
        links.new(palette_nodes[sec].outputs[0], mix.inputs[7])
        scheme[out_k] = mix

    # --- Assemble (COOKED primary): fac = amt × BaseColor × Swatch ---
    white = add(_rgb_node(nodes, "Bench_White", "canvas white", (1, 1, 1, 1)), "assemble")
    sw_sep = add(nodes.new("ShaderNodeSeparateColor"), "assemble")
    sw_sep.name = "Bench_Swatch_Sep"
    sw_sep.label = f"Swatch.rgb (hero L{hero})"
    links.new(sw_nd.outputs[0], sw_sep.inputs[0])

    amt_rgb = add(
        _rgb_node(nodes, "Bench_Amt", f"amt=BaseColorMaskStrength L{hero}={amt:.3f}", (amt, amt, amt, 1.0)),
        "assemble",
    )

    # fac = BaseColor × Swatch
    fac_bs = add(
        _new_mul_rgba(nodes, "Bench_Fac_BaseSw", "fac0 = BaseColor × Swatch"),
        "assemble",
    )
    if tex_bc.image:
        links.new(tex_bc.outputs["Color"], fac_bs.inputs[6])
    else:
        fac_bs.inputs[6].default_value = (1, 1, 1, 1)
    links.new(sw_nd.outputs[0], fac_bs.inputs[7])

    # fac = fac0 × amt
    fac_final = add(
        _new_mul_rgba(nodes, "Bench_Fac_Amt", f"fac = amt({amt:.3f}) × BaseColor × Swatch"),
        "assemble",
    )
    links.new(fac_bs.outputs[2], fac_final.inputs[6])
    links.new(amt_rgb.outputs[0], fac_final.inputs[7])

    fac_sep = add(nodes.new("ShaderNodeSeparateColor"), "assemble")
    fac_sep.name = "Bench_Fac_Sep"
    fac_sep.label = "fac.rgb → assemble weights"
    links.new(fac_final.outputs[2], fac_sep.inputs[0])

    # col = lerp(1, OutA, fac.r)
    mix_a = add(
        _new_mix_rgba(nodes, "Bench_Cooked_A", "COOKED lerp(1,OutA,fac.r)"),
        "assemble",
    )
    links.new(_sep_channel(fac_sep, "Red", 0), mix_a.inputs[0])
    links.new(white.outputs[0], mix_a.inputs[6])
    links.new(scheme["OutA"].outputs[2], mix_a.inputs[7])

    mix_b = add(
        _new_mix_rgba(nodes, "Bench_Cooked_B", "COOKED lerp(col,OutB,fac.g)"),
        "assemble",
    )
    links.new(_sep_channel(fac_sep, "Green", 1), mix_b.inputs[0])
    links.new(mix_a.outputs[2], mix_b.inputs[6])
    links.new(scheme["OutB"].outputs[2], mix_b.inputs[7])

    mix_c = add(
        _new_mix_rgba(nodes, "Bench_Cooked_C", "COOKED lerp(col,OutC,fac.b)"),
        "assemble",
    )
    links.new(_sep_channel(fac_sep, "Blue", 2), mix_c.inputs[0])
    links.new(mix_b.outputs[2], mix_c.inputs[6])
    links.new(scheme["OutC"].outputs[2], mix_c.inputs[7])

    # Arc-safe comparison branch (Swatch×amt only — no BaseColor in fac)
    arc_note = add(nodes.new("ShaderNodeRGB"), "assemble")
    arc_note.name = "Bench_ArcSafe_Note"
    arc_note.label = "ARC-SAFE side branch (Sw×amt only; not fed to Principled)"
    arc_note.outputs[0].default_value = (0.1, 0.1, 0.4, 1.0)
    arc_note.hide = True

    arc_gated = {}
    for i, (out_k, ch_name) in enumerate(
        (("OutA", "Red"), ("OutB", "Green"), ("OutC", "Blue"))
    ):
        canvas = add(
            _new_mix_rgba(
                nodes,
                f"Bench_ArcCanvas_{out_k}",
                f"ARC lerp(1,{out_k},Sw.{ch_name[0]}×amt)",
            ),
            "assemble",
        )
        links.new(white.outputs[0], canvas.inputs[6])
        links.new(scheme[out_k].outputs[2], canvas.inputs[7])
        fac_in = canvas.inputs.get("Factor") or canvas.inputs[0]
        ch_out = _sep_channel(sw_sep, ch_name, i)
        if amt >= 0.999:
            links.new(ch_out, fac_in)
        else:
            mul = add(nodes.new("ShaderNodeMath"), "assemble")
            mul.operation = "MULTIPLY"
            mul.name = f"Bench_ArcFac_{out_k}"
            mul.label = f"ARC Sw×amt {out_k}"
            links.new(ch_out, mul.inputs[0])
            mul.inputs[1].default_value = amt
            links.new(mul.outputs[0], fac_in)
        arc_gated[out_k] = canvas

    arc_ab = add(
        _new_mix_rgba(nodes, "Bench_Arc_AB", "ARC assemble A→B (Sw.g)"),
        "assemble",
    )
    links.new(_sep_channel(sw_sep, "Green", 1), arc_ab.inputs[0])
    links.new(arc_gated["OutA"].outputs[2], arc_ab.inputs[6])
    links.new(arc_gated["OutB"].outputs[2], arc_ab.inputs[7])
    arc_c = add(
        _new_mix_rgba(nodes, "Bench_Arc_C", "ARC assemble →C (Sw.b) [side]"),
        "assemble",
    )
    links.new(_sep_channel(sw_sep, "Blue", 2), arc_c.inputs[0])
    links.new(arc_ab.outputs[2], arc_c.inputs[6])
    links.new(arc_gated["OutC"].outputs[2], arc_c.inputs[7])

    # --- Post: Overlay × ColorTex × wear ---
    ov_mul = add(
        _new_mul_rgba(nodes, "Bench_Overlay_Mul", "× BaseColorOverlay (D034)"),
        "post",
    )
    links.new(mix_c.outputs[2], ov_mul.inputs[6])
    links.new(ov_nd.outputs[0], ov_mul.inputs[7])

    # ColorTex band_lerp: tex = lerp(1, ColorTex, BaseTextureStrength)
    if tex_colortex is not None and tex_colortex.image and tex_strength > 0.0:
        tex_blend = add(
            _new_mix_rgba(
                nodes,
                "Bench_ColorTex_Blend",
                f"tex=lerp(1,ColorTex,str={tex_strength:.3f}) D043",
            ),
            "post",
        )
        tex_blend.inputs[0].default_value = float(max(0.0, min(1.0, tex_strength)))
        links.new(white.outputs[0], tex_blend.inputs[6])
        links.new(tex_colortex.outputs["Color"], tex_blend.inputs[7])
        tex_mul = add(
            _new_mul_rgba(nodes, "Bench_ColorTex_Mul", "× ColorTex_blend"),
            "post",
        )
        links.new(ov_mul.outputs[2], tex_mul.inputs[6])
        links.new(tex_blend.outputs[2], tex_mul.inputs[7])
        post_col = tex_mul.outputs[2]
    else:
        stub = add(
            _rgb_node(
                nodes,
                "Bench_ColorTex_Stub",
                (
                    f"ColorTex stub=1 (ID={colortex_id:.0f}, str={tex_strength:.3f}; "
                    "refuse ID-0 / missing slice)"
                ),
                (1, 1, 1, 1),
            ),
            "post",
        )
        tex_mul = add(
            _new_mul_rgba(nodes, "Bench_ColorTex_Mul", "× ColorTex_blend (stub)"),
            "post",
        )
        links.new(ov_mul.outputs[2], tex_mul.inputs[6])
        links.new(stub.outputs[0], tex_mul.inputs[7])
        post_col = tex_mul.outputs[2]

    # Wear: crease (mask.g) then edge (mask.b) — Amount×overlay.a gate (simplified)
    crease_a = float(crease_ov[3]) if len(crease_ov) > 3 else 0.0
    edge_a = float(edge_ov[3]) if len(edge_ov) > 3 else 0.0
    crease_w = max(0.0, min(1.0, float(crease_amt) * crease_a))
    edge_w = max(0.0, min(1.0, float(edge_amt) * edge_a))

    wear_col = post_col
    if crease_w > 1e-4:
        # Fac = mask.g × crease_w
        crease_fac = add(nodes.new("ShaderNodeMath"), "post")
        crease_fac.operation = "MULTIPLY"
        crease_fac.name = "Bench_CreaseFac"
        crease_fac.label = f"Crease Fac = mask.g × {crease_w:.3f}"
        links.new(_sep_channel(sep_cm, "Green", 1), crease_fac.inputs[0])
        crease_fac.inputs[1].default_value = crease_w
        crease_mix = add(
            _new_mix_rgba(nodes, "Bench_CreaseWear", "wear lerp CreaseColorOverlay"),
            "post",
        )
        links.new(crease_fac.outputs[0], crease_mix.inputs[0])
        links.new(wear_col, crease_mix.inputs[6])
        links.new(crease_nd.outputs[0], crease_mix.inputs[7])
        wear_col = crease_mix.outputs[2]

    if edge_w > 1e-4:
        edge_fac = add(nodes.new("ShaderNodeMath"), "post")
        edge_fac.operation = "MULTIPLY"
        edge_fac.name = "Bench_EdgeFac"
        edge_fac.label = f"Edge Fac = mask.b × {edge_w:.3f}"
        links.new(_sep_channel(sep_cm, "Blue", 2), edge_fac.inputs[0])
        edge_fac.inputs[1].default_value = edge_w
        edge_mix = add(
            _new_mix_rgba(nodes, "Bench_EdgeWear", "wear lerp EdgeColorOverlay"),
            "post",
        )
        links.new(edge_fac.outputs[0], edge_mix.inputs[0])
        links.new(wear_col, edge_mix.inputs[6])
        links.new(edge_nd.outputs[0], edge_mix.inputs[7])
        wear_col = edge_mix.outputs[2]

    if crease_w <= 1e-4 and edge_w <= 1e-4:
        wear_note = add(
            _rgb_node(
                nodes,
                "Bench_Wear_Note",
                "Wear inert (Edge/Crease Amount×a ≈ 0) — mask.g/b still ≠ ABC",
                (0.7, 0.7, 0.7, 1),
            ),
            "post",
        )
        wear_note.hide = True

    # --- Output ---
    bsdf = add(nodes.new("ShaderNodeBsdfPrincipled"), "output")
    bsdf.name = "Bench_Principled"
    bsdf.label = f"Benchmark {bench_id} COOKED albedo (hero L{hero})"
    links.new(wear_col, bsdf.inputs["Base Color"])

    if tex_nrm is not None and tex_nrm.image:
        nmap = add(nodes.new("ShaderNodeNormalMap"), "output")
        nmap.name = "Bench_NormalMap"
        nmap.label = "Normal Map"
        links.new(tex_nrm.outputs["Color"], nmap.inputs["Color"])
        try:
            links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        except Exception:
            pass

    out = add(nodes.new("ShaderNodeOutputMaterial"), "output")
    out.name = "Bench_Output"
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    # Stamp object custom props
    try:
        obj["arc_benchmark_id"] = bench_id
        obj["arc_mi_path"] = os.path.abspath(mi_path) if mi_path else ""
        obj["arc_psk_path"] = os.path.abspath(psk_path) if psk_path else ""
        obj["arc_inspection"] = 1
        obj["arc_bench_hero_layer"] = int(hero)
        obj["arc_amt_l1"] = float(amt)
        obj["arc_swatch_l1"] = str(tuple(float(x) for x in swatch[:3]))
        obj["arc_overlay_l1"] = str(tuple(float(x) for x in overlay[:3]))
        if bits:
            obj["arc_layer_mask_bits"] = str(dict(bits))
        if colortex_id >= 1.0:
            obj["arc_colortex_id"] = float(colortex_id)
        if tex_strength > 0.0:
            obj["arc_colortex_strength"] = float(tex_strength)
        if colortex_path:
            obj["arc_colortex_path"] = os.path.abspath(colortex_path)
    except Exception:
        pass

    layout_benchmark_nodes(nodes, stage_of=stage_of)
    layout_benchmark_nodes(nodes, stage_of=stage_of)
    return mat


def import_benchmark(context, bench_id: str) -> tuple[bool, str, list]:
    """Import one benchmark body part with inspection material (no ArcTexturer)."""
    root = utils.get_pioneer_root()
    if not root or not os.path.isdir(root):
        return False, "Set PioneerGame Folder in Settings first", []
    if bench_id not in COLOR_BENCHMARKS:
        return False, f"Unknown benchmark id: {bench_id}", []

    info = resolve_benchmark(root, bench_id)
    if not info["psk_path"]:
        return (
            False,
            f"{bench_id}: PSK missing under {info['character']}/{info['part']}",
            [],
        )
    if not info["mi_path"]:
        return False, f"{bench_id}: MI JSON missing ({COLOR_BENCHMARKS[bench_id]['mi_rel']})", []

    try:
        new_objects = importing.import_psk(info["psk_path"])
    except RuntimeError as e:
        return False, str(e), []

    meshes = [o for o in new_objects if o.type == "MESH"]
    if not meshes:
        return False, f"{bench_id}: import produced no mesh", new_objects

    for obj in meshes:
        try:
            obj.name = f"{bench_id}_{obj.name}"[:63]
            obj["arc_model_type"] = "clothing"
            obj["arc_benchmark_id"] = bench_id
        except Exception:
            pass
        setup_benchmark_inspection_material(
            obj,
            bench_id=bench_id,
            mi_path=info["mi_path"],
            part_folder=info["part_folder"],
            psk_path=info["psk_path"],
        )

    return True, f"Imported {bench_id}: {info['label']}", new_objects


def import_benchmarks(context, bench_ids: list[str] | None = None) -> tuple[int, list[str]]:
    """Import several benchmarks. Returns (ok_count, messages)."""
    ids = list(bench_ids) if bench_ids else list(BENCHMARK_IDS)
    ok_n = 0
    msgs: list[str] = []
    for bid in ids:
        ok, msg, _ = import_benchmark(context, bid)
        msgs.append(msg)
        if ok:
            ok_n += 1
    return ok_n, msgs
