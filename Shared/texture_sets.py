"""Texture sets: switch a blend between original and optimized (re-encoded, downscaled) textures.

Optimized files are built on demand with Blender's own image writer (no extra deps) into
``<ArcPak cache>/texture_sets/<settings key>/`` and reused when already present and newer
than the source.  Each relinked image keeps its original path in ``image["arc_tex_original"]``
so switching back to ORIGINAL restores it.

Notes:
- AVIF is written with Image.save(quality=...), which takes no colour depth or encoder speed,
  so AVIF output is always 8-bit and only quality is offered.
- Apply always covers every local file image in the blend (no per-object scope).
- Pack packs what the blend uses now; it does not switch the texture set.
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
APPLIED_KEY = "arc_tex_applied"  # scene: "ORIGINAL" or "OPTIMIZED:<settings key>" last applied
SKIP_KEY = "arc_tex_kept_key"  # image: settings key under which it could not be optimized

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
        enc = f"q{scene.arc_tex_avif_quality}_d8"  # always 8-bit: Image.save() takes no depth
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


def _reload(img) -> None:
    """Reload hook (tests patch it to inject a failure)."""
    img.reload()


def _set_path(img, path, cs, am) -> None:
    img.filepath = path
    try:
        img.colorspace_settings.name = cs
    except Exception:
        pass
    img.alpha_mode = am


def _relink(img, path: str) -> None:
    """Point ``img`` at ``path``; on any failure restore the old path and packed data, then raise."""
    cs = img.colorspace_settings.name
    am = img.alpha_mode
    old_path = img.filepath
    packed = bytes(img.packed_file.data) if img.packed_file else None
    try:
        if not os.path.isfile(bpy.path.abspath(path)):
            raise FileNotFoundError(f"file not found: {path}")
        if packed is not None:
            img.unpack(method="REMOVE")
        _set_path(img, path, cs, am)
        _reload(img)
        if tuple(img.size) == (0, 0):
            raise RuntimeError(f"could not read {os.path.basename(path)}")
    except Exception:
        try:
            _set_path(img, old_path, cs, am)
            if packed is not None and not img.packed_file:
                img.pack(data=packed, data_len=len(packed))
            img.reload()
        except Exception as exc:
            print("Arc textures: rollback of", img.name, "failed:", exc)
        raise


def switch_set(scene, target: str = None, images=None) -> dict:
    """Relink every local file image to ``target``. Returns counts plus ``failed`` [(image, error)].

    Tags are written only after a relink succeeds, so a failed image stays pending and is retried.
    """
    target = target or scene.arc_tex_set
    stats = {"switched": 0, "skipped": 0, "failed": []}
    key = settings_key(scene)
    for img in list(bpy.data.images if images is None else images):
        if not _eligible(img) or img.library:
            continue
        if target == "ORIGINAL":
            if SKIP_KEY in img:
                del img[SKIP_KEY]
            orig = img.get(ORIGINAL_KEY)
            if not orig:
                continue
            try:
                _relink(img, orig)
            except Exception as exc:
                stats["failed"].append((img.name, str(exc)))
                continue
            del img[ORIGINAL_KEY]
            if SET_KEY in img:
                del img[SET_KEY]
            stats["switched"] += 1
            continue
        if img.get(SET_KEY) == key and img.get(ORIGINAL_KEY):
            continue
        orig = _original_path(img)
        try:
            dst = build_optimized(scene, img, stats)
        except Exception as exc:
            stats["failed"].append((img.name, str(exc)))
            continue
        if not dst:
            img[SKIP_KEY] = key
            stats["skipped"] += 1
            continue
        try:
            _relink(img, dst)
        except Exception as exc:
            stats["failed"].append((img.name, str(exc)))
            continue
        img[ORIGINAL_KEY] = orig
        img[SET_KEY] = key
        if SKIP_KEY in img:
            del img[SKIP_KEY]
        stats["switched"] += 1
    scene.arc_tex_set = target
    scene[APPLIED_KEY] = "ORIGINAL" if target == "ORIGINAL" else f"{target}:{key}"
    return stats


def count_images() -> int:
    """File images Apply would relink (local, file-backed)."""
    return sum(1 for img in bpy.data.images if _eligible(img) and not img.library)


def texture_state(scene, images=None) -> dict:
    """What the file's images use now, read from the images themselves. No IO.

    Returns {"label": "Original" | "Optimized" | "Mixed: N of M optimized", "optimized": N,
    "total": M, "current": K, "pending": bool}. M counts local file images that can be
    optimized (images kept original for the current settings are left out); K counts optimized
    images built with the current settings. pending: Apply would change something."""
    try:
        key = settings_key(scene) if scene is not None else ""
    except Exception:
        key = ""
    pool = bpy.data.images if images is None else images
    optimized = total = current = 0
    for img in pool:
        try:
            if not _eligible(img) or img.library:
                continue
            is_opt = bool(img.get(ORIGINAL_KEY))
            if not is_opt and key and img.get(SKIP_KEY) == key:
                continue
            total += 1
            if is_opt:
                optimized += 1
                if img.get(SET_KEY) == key:
                    current += 1
        except Exception:
            continue
    if optimized == 0:
        label = "Original"
    elif optimized == total:
        label = "Optimized"
    else:
        label = f"Mixed: {optimized} of {total} optimized"
    chosen = getattr(scene, "arc_tex_set", "ORIGINAL") if scene is not None else "ORIGINAL"
    if chosen == "ORIGINAL":
        pending = optimized > 0
    else:
        pending = current < total
    return {"label": label, "optimized": optimized, "total": total, "current": current,
            "pending": bool(pending)}


def _report_error(title, detail):
    try:
        from . import ui_targets
        ui_targets.set_error(title, detail)
    except Exception:
        pass


def _clear_error():
    try:
        from . import ui_targets
        ui_targets.clear_error()
    except Exception:
        pass


class ARC_OT_textures_switch_set(bpy.types.Operator):
    bl_idname = "arc.textures_switch_set"
    bl_label = "Apply to All Textures in File"
    bl_description = "Relink every image file in this blend to the selected texture set. Optimized files are built on first use"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        count = count_images()
        target = "original" if context.scene.arc_tex_set == "ORIGINAL" else "optimized"
        return context.window_manager.invoke_confirm(
            self, event, title=f"Switch {count} images to the {target} textures?", confirm_text="Apply")

    def execute(self, context):
        try:
            st = switch_set(context.scene)
        except Exception as exc:
            _report_error("Textures were not switched", str(exc))
            self.report({"ERROR"}, f"Textures were not switched: {exc}")
            return {"CANCELLED"}
        failed = st.get("failed") or []
        if failed:
            names = ", ".join(name for name, _err in failed[:5])
            more = f" and {len(failed) - 5} more" if len(failed) > 5 else ""
            _report_error(f"{len(failed)} textures were not switched",
                          f"{names}{more}. First error: {failed[0][1]}")
            self.report({"WARNING"}, f"Textures: {st['switched']} switched, {len(failed)} failed "
                        f"({names}{more})")
            return {"FINISHED"}
        _clear_error()
        self.report({"INFO"}, f"Textures: {st['switched']} switched, {st['skipped']} kept original, "
                    f"{st.get('built', 0)} built, {st.get('reused', 0)} reused")
        return {"FINISHED"}


class ARC_OT_textures_pack_blend(bpy.types.Operator):
    bl_idname = "arc.textures_pack_blend"
    bl_label = "Pack Textures into Blend File"
    bl_description = "Pack every image the blend uses now into the blend file. Apply a texture set first to pack those files"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        packed = 0
        failed = []
        for img in bpy.data.images:
            if img.source in {"FILE", "SEQUENCE", "TILED"} and not img.packed_file and not img.library:
                try:
                    img.pack()
                    packed += 1
                except Exception as e:
                    failed.append((img.name, str(e)))
                    print(f"Arc texture sets: pack failed for {img.name}: {e}")
        if failed:
            names = ", ".join(name for name, _err in failed[:5])
            more = f" and {len(failed) - 5} more" if len(failed) > 5 else ""
            _report_error(f"{len(failed)} images were not packed", f"{names}{more}. First error: {failed[0][1]}")
            self.report({"WARNING"}, f"Packed {packed} images, {len(failed)} failed ({names}{more})")
            return {"FINISHED"}
        _clear_error()
        self.report({"INFO"}, f"Packed {packed} images")
        return {"FINISHED"}


def draw_settings(layout, context):
    sc = context.scene
    layout.row(align=True).prop(sc, "arc_tex_set", expand=True)
    col = layout.column(align=True)
    col.use_property_split = True
    col.use_property_decorate = False
    # Original textures have no format or size to choose.
    if sc.arc_tex_set == "ORIGINAL":
        layout.label(text="Original textures are used unchanged", icon="INFO")
    else:
        _draw_format(col, sc)
    # Pack into Blend File lives in Settings > Scene Tools.
    state = texture_state(sc)
    # The choice above already says what the file uses; name the current state only while it differs.
    if state["pending"]:
        layout.label(text="Textures now: " + state["label"], icon="IMAGE_DATA")
        layout.label(text="Changes not applied yet", icon="INFO")
    layout.operator(ARC_OT_textures_switch_set.bl_idname, text="Apply to All Textures in File", icon="FILE_REFRESH")


def _draw_format(col, sc):
    col.prop(sc, "arc_tex_format", text="Format")
    fmt = sc.arc_tex_format
    if fmt == "AVIF":
        col.prop(sc, "arc_tex_avif_quality")
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


_PROPS = {
    "arc_tex_set": EnumProperty(name="Texture Set", description="Which texture files the blend uses", items=[
        ("ORIGINAL", "Original", "Use the original texture files"),
        ("OPTIMIZED", "Optimized", "Use re-encoded, downscaled texture files")], default="ORIGINAL"),
    "arc_tex_format": EnumProperty(name="Format", items=[
        ("AVIF", "AVIF", "Small files, slower to build"), ("PNG", "PNG", "Lossless, large files"),
        ("JPEG", "JPEG", "Small files, no transparency (images with transparency stay PNG)"),
        ("WEBP", "WEBP", "Small files, optional lossless")], description="File format of the optimized textures", default="AVIF"),
    "arc_tex_avif_quality": IntProperty(name="Quality", description="Higher keeps more detail and makes larger files", default=70, min=0, max=100),
    "arc_tex_png_compression": IntProperty(name="Compression", description="Higher makes smaller files and builds slower. Quality is unchanged", default=15, min=0, max=100, subtype="PERCENTAGE"),
    "arc_tex_jpeg_quality": IntProperty(name="Quality", description="Higher keeps more detail and makes larger files", default=90, min=0, max=100),
    "arc_tex_webp_quality": IntProperty(name="Quality", description="Higher keeps more detail and makes larger files", default=90, min=0, max=100),
    "arc_tex_webp_lossless": BoolProperty(name="Lossless", description="Keep every pixel exact. Files are larger", default=False),
    "arc_tex_res": EnumProperty(name="Resolution", description="Size of the optimized textures compared with the originals", items=[
        ("HALF", "1/2", "Half width and height"), ("QUARTER", "1/4", "Quarter width and height"),
        ("EIGHTH", "1/8", "One eighth width and height"),
        ("CUSTOM", "Custom", "Fit the longest side to a maximum pixel size")], default="HALF"),
    "arc_tex_custom_px": IntProperty(name="Max Size", description="Longest side of each texture in pixels", default=1024, min=1, max=16384),
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
