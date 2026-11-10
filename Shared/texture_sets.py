"""Texture sets: switch a blend between original and optimized (re-encoded, downscaled) textures.

Optimized files are built on demand with Blender's own image writer (no extra deps) into
``<ArcPak cache>/texture_sets/<settings key>/`` and reused when already present and newer
than the source.  Each relinked image keeps its original path in ``image["arc_tex_original"]``
so switching back to ORIGINAL restores it.

Notes:
- Blender 5.1 exposes AVIF quality and colour depth (8/10/12) but no encoder speed; the speed
  knob is therefore not available, colour depth is the closest exposed AVIF setting.
- WEBP lossless maps to quality 100 (Blender's WEBP writer encodes lossless at 100).
- Images with alpha never go to JPEG (no alpha channel); they are written as PNG instead.
- Float (EXR/HDR) and tiled/generated/missing images are left on their original file.
- Colour space and alpha mode of every image datablock are preserved across switches.
"""

import hashlib
import os

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty

ORIGINAL_KEY = "arc_tex_original"
SET_KEY = "arc_tex_set_key"

_EXT = {"AVIF": ".avif", "PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
_RES_DIV = {"HALF": 2, "QUARTER": 4, "EIGHTH": 8}


def cache_root() -> str:
    try:
        from . import pak_extract
        base = pak_extract.cache_dir()
    except Exception:
        base = bpy.utils.user_resource("CONFIG", path="dataraiders_cache", create=True)
    path = os.path.join(base, "texture_sets")
    os.makedirs(path, exist_ok=True)
    return path


def settings_key(scene) -> str:
    fmt = scene.arc_tex_format
    if fmt == "AVIF":
        enc = f"q{scene.arc_tex_avif_quality}_d{scene.arc_tex_avif_depth}"
    elif fmt == "PNG":
        enc = f"c{scene.arc_tex_png_compression}"
    elif fmt == "JPEG":
        enc = f"q{scene.arc_tex_jpeg_quality}"
    else:
        enc = "lossless" if scene.arc_tex_webp_lossless else f"q{scene.arc_tex_webp_quality}"
    res = scene.arc_tex_res
    res_tag = f"max{scene.arc_tex_custom_px}" if res == "CUSTOM" else f"div{_RES_DIV[res]}"
    return f"{fmt}_{enc}_{res_tag}"


def _target_size(scene, w: int, h: int):
    if scene.arc_tex_res == "CUSTOM":
        cap = max(1, int(scene.arc_tex_custom_px))
        f = min(1.0, cap / float(max(w, h, 1)))
        return max(1, int(round(w * f))), max(1, int(round(h * f)))
    d = _RES_DIV[scene.arc_tex_res]
    return max(1, w // d), max(1, h // d)


def _has_alpha(img) -> bool:
    return img.depth in (32, 64, 128) and img.alpha_mode != "NONE"


def _original_path(img) -> str:
    p = img.get(ORIGINAL_KEY)
    if p:
        return p
    return os.path.normpath(bpy.path.abspath(img.filepath, library=img.library))


def _eligible(img) -> bool:
    return img.source == "FILE" and img.type == "IMAGE" and bool(img.filepath or img.get(ORIGINAL_KEY))


def _encode(scene, src, dst: str, fmt: str, alpha: bool) -> None:
    """Write ``src`` (already scaled) to ``dst`` in ``fmt``."""
    if fmt == "PNG":
        s = scene.render.image_settings
        saved = {k: getattr(s, k) for k in ("file_format", "color_mode", "color_depth", "compression", "color_management")}
        try:
            s.file_format = "PNG"
            s.color_mode = "RGBA" if alpha else "RGB"
            s.color_depth = "8"
            s.compression = int(scene.arc_tex_png_compression)
            s.color_management = "OVERRIDE"
            try:
                s.view_settings.view_transform = "Standard"
                s.view_settings.look = "None"
                s.view_settings.exposure = 0.0
                s.view_settings.gamma = 1.0
            except Exception:
                pass
            src.save_render(dst, scene=scene)
        finally:
            for k, v in saved.items():
                try:
                    setattr(s, k, v)
                except Exception:
                    pass
        return
    if fmt == "AVIF":
        q = int(scene.arc_tex_avif_quality)
    elif fmt == "JPEG":
        q = int(scene.arc_tex_jpeg_quality)
    else:
        q = 100 if scene.arc_tex_webp_lossless else int(scene.arc_tex_webp_quality)
    src.file_format = fmt
    src.save(filepath=dst, quality=q)


def build_optimized(scene, img, stats=None) -> str:
    """Return the optimized file path for ``img`` (building it if needed) or '' to keep original."""
    orig = _original_path(img)
    if not orig or not os.path.isfile(orig):
        return ""
    fmt = scene.arc_tex_format
    tmp = bpy.data.images.load(orig, check_existing=False)
    try:
        if tmp.is_float or tmp.size[0] == 0:
            return ""
        alpha = _has_alpha(tmp)
        out_fmt = "PNG" if (fmt == "JPEG" and alpha) else fmt
        tag = hashlib.sha1(os.path.normcase(orig).encode("utf-8")).hexdigest()[:10]
        stem = os.path.splitext(os.path.basename(orig))[0]
        folder = os.path.join(cache_root(), settings_key(scene))
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, f"{stem}_{tag}{_EXT[out_fmt]}")
        if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(orig):
            if stats is not None:
                stats["reused"] = stats.get("reused", 0) + 1
            return dst
        w, h = _target_size(scene, *tmp.size)
        if (w, h) != tuple(tmp.size):
            tmp.scale(w, h)
        _encode(scene, tmp, dst, out_fmt, alpha)
        if stats is not None:
            stats["built"] = stats.get("built", 0) + 1
        return dst
    finally:
        bpy.data.images.remove(tmp)


def _relink(img, path: str) -> None:
    cs = img.colorspace_settings.name
    am = img.alpha_mode
    if img.packed_file:
        img.unpack(method="REMOVE")
    img.filepath = path
    try:
        img.colorspace_settings.name = cs
    except Exception:
        pass
    img.alpha_mode = am
    img.reload()


def switch_set(scene, target: str = None) -> dict:
    target = target or scene.arc_tex_set
    stats = {"switched": 0, "skipped": 0}
    key = settings_key(scene)
    for img in list(bpy.data.images):
        if not _eligible(img) or img.library:
            continue
        if target == "ORIGINAL":
            orig = img.get(ORIGINAL_KEY)
            if not orig:
                continue
            _relink(img, orig)
            del img[ORIGINAL_KEY]
            if SET_KEY in img:
                del img[SET_KEY]
            stats["switched"] += 1
            continue
        if img.get(SET_KEY) == key and img.get(ORIGINAL_KEY):
            continue
        orig = _original_path(img)
        dst = build_optimized(scene, img, stats)
        if not dst:
            stats["skipped"] += 1
            continue
        img[ORIGINAL_KEY] = orig
        img[SET_KEY] = key
        _relink(img, dst)
        stats["switched"] += 1
    scene.arc_tex_set = target
    return stats


class ARC_OT_textures_switch_set(bpy.types.Operator):
    bl_idname = "arc.textures_switch_set"
    bl_label = "Switch textures"
    bl_description = "Relink every file image in this blend to the selected texture set"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = switch_set(context.scene)
        self.report({"INFO"}, f"Texture set {context.scene.arc_tex_set}: {st['switched']} switched, "
                    f"{st['skipped']} kept original, {st.get('built', 0)} built, {st.get('reused', 0)} reused")
        return {"FINISHED"}


class ARC_OT_textures_pack_blend(bpy.types.Operator):
    bl_idname = "arc.textures_pack_blend"
    bl_label = "Pack blend"
    bl_description = "Switch to the selected texture set, then pack all images into the blend"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = switch_set(context.scene)
        packed = 0
        for img in bpy.data.images:
            if img.source in {"FILE", "SEQUENCE", "TILED"} and not img.packed_file and not img.library:
                try:
                    img.pack()
                    packed += 1
                except Exception as e:
                    print(f"Arc texture sets: pack failed for {img.name}: {e}")
        self.report({"INFO"}, f"Switched {st['switched']}, packed {packed} images")
        return {"FINISHED"}


def draw_settings(layout, context):
    sc = context.scene
    col = layout.column(align=True)
    col.row(align=True).prop(sc, "arc_tex_set", expand=True)
    col.prop(sc, "arc_tex_format", text="Format")
    fmt = sc.arc_tex_format
    if fmt == "AVIF":
        col.prop(sc, "arc_tex_avif_quality")
        col.prop(sc, "arc_tex_avif_depth")
    elif fmt == "PNG":
        col.prop(sc, "arc_tex_png_compression")
    elif fmt == "JPEG":
        col.prop(sc, "arc_tex_jpeg_quality")
    else:
        col.prop(sc, "arc_tex_webp_lossless")
        sub = col.row()
        sub.enabled = not sc.arc_tex_webp_lossless
        sub.prop(sc, "arc_tex_webp_quality")
    col.prop(sc, "arc_tex_res", text="Resolution")
    if sc.arc_tex_res == "CUSTOM":
        col.prop(sc, "arc_tex_custom_px")
    row = layout.row(align=True)
    row.operator(ARC_OT_textures_switch_set.bl_idname, icon="FILE_REFRESH")
    row.operator(ARC_OT_textures_pack_blend.bl_idname, icon="PACKAGE")


_PROPS = {
    "arc_tex_set": EnumProperty(name="Texture set", items=[
        ("ORIGINAL", "Original", "Use the original texture files"),
        ("OPTIMIZED", "Optimized", "Use re-encoded, downscaled texture files")], default="ORIGINAL"),
    "arc_tex_format": EnumProperty(name="Format", items=[
        ("AVIF", "AVIF", ""), ("PNG", "PNG", ""), ("JPEG", "JPEG", ""), ("WEBP", "WEBP", "")], default="AVIF"),
    "arc_tex_avif_quality": IntProperty(name="Quality", default=70, min=0, max=100),
    "arc_tex_avif_depth": EnumProperty(name="Bit depth", description="AVIF colour depth (Blender exposes no AVIF encoder speed)",
        items=[("8", "8", ""), ("10", "10", ""), ("12", "12", "")], default="8"),
    "arc_tex_png_compression": IntProperty(name="Compression", default=15, min=0, max=100, subtype="PERCENTAGE"),
    "arc_tex_jpeg_quality": IntProperty(name="Quality", default=90, min=0, max=100),
    "arc_tex_webp_quality": IntProperty(name="Quality", default=90, min=0, max=100),
    "arc_tex_webp_lossless": BoolProperty(name="Lossless", default=False),
    "arc_tex_res": EnumProperty(name="Resolution", items=[
        ("HALF", "1/2", ""), ("QUARTER", "1/4", ""), ("EIGHTH", "1/8", ""),
        ("CUSTOM", "Custom", "Fit the longest side to a maximum pixel size")], default="HALF"),
    "arc_tex_custom_px": IntProperty(name="Max px", default=1024, min=1, max=16384),
}

classes = (ARC_OT_textures_switch_set, ARC_OT_textures_pack_blend)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            bpy.utils.unregister_class(cls)
            bpy.utils.register_class(cls)
    for name, prop in _PROPS.items():
        setattr(bpy.types.Scene, name, prop)


def unregister():
    for name in _PROPS:
        if hasattr(bpy.types.Scene, name):
            try:
                delattr(bpy.types.Scene, name)
            except Exception:
                pass
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
