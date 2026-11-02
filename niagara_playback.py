"""Niagara playback in the addon (spec N-O5 §13, N6b): bake a cooked system with the offline CPU scheduler and build GN rigs.

The bake modules (niagara_cache, niagara_scheduler, ... ) use absolute imports so they also run as plain scripts; the addon
folder is put on sys.path before importing them.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy.types import Operator

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SHIM = r'Z:\Datamining\Arc Raiders\CodexScratch\DataRaiders-BlenderImporter\.local\niagara-v2\wave2\build\svml_shim\svml_shim.dll'
DEFAULT_VECTOR_FIELDS = r'Z:\Datamining\Arc Raiders\CodexScratch\DataRaiders-BlenderImporter\.local\niagara-v2\data\vector_fields.json'
DEFAULT_GPU_BINDINGS = r'Z:\Datamining\Arc Raiders\CodexScratch\DataRaiders-BlenderImporter\.local\niagara-v2\data\gpu_bindings'
DEFAULT_GPU_SHADERS = r'Z:\Datamining\Arc Raiders\CodexScratch\DataRaiders-BlenderImporter\.local\niagara-v2\wave1\out\NW4'
PROPERTY_NAMES = ('arc_ns_play_ticks', 'arc_ns_play_cache_dir', 'arc_ns_play_shim', 'arc_ns_play_vector_fields', 'arc_ns_play_at_cursor',
                  'arc_ns_play_collision')
MAX_COLLISION_TRIANGLES = 2_000_000


def _bake_modules():
    if ADDON_DIR not in sys.path:
        sys.path.append(ADDON_DIR)
    import niagara_cache
    import niagara_rigs
    return niagara_cache, niagara_rigs


def register_properties(assign):
    assign('arc_ns_play_ticks', IntProperty(
        name='Ticks', description="", default=120, min=1, max=3000))
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
    try:
        from . import niagara_dump_params
    except ImportError:
        import niagara_dump_params
    return niagara_dump_params.overrides_for(path) or None


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


def _selected_ns_path(context):
    from . import niagara_catalog as ncat
    scene = context.scene
    entry = ncat.get_entry(getattr(scene, 'arc_ns_selected', '') or '', getattr(scene, 'arc_ns_catalog_root', '') or '')
    selected_path = ncat.resolve_entry_path(entry, scene=scene)
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

    from . import importing, niagara_fx
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
            hit = niagara_fx.find_mesh_file(asset_root, name, cache) if asset_root else None
            if hit is None:
                continue
            try:
                every = [o for o in niagara_fx.importing_import_psk(str(hit)) if o]
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
    from . import utils
    return [r for r in [asset_root] + list(getattr(utils, '_pak_cache_roots', [])) if r]


class ARC_OUTFITS_OT_PlayNiagaraFX(Operator):
    """Simulate the selected cooked Niagara system (CPU emitters) and build Geometry Nodes playback rigs.

    Particle data comes from the game's compiled VectorVM bytecode run offline; materials are previews until FX material
    translation lands. GPU emitters are not simulated yet.
    """
    bl_idname = 'arc_outfits.play_niagara_fx'
    bl_label = 'Bake & Play NS Effect'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        path = _selected_ns_path(context)
        if not path:
            self.report({'ERROR'}, 'Select a Niagara system in the catalog (or set NS JSON) first')
            return {'CANCELLED'}
        try:
            nc, rigs = _bake_modules()
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, f'Niagara bake modules unavailable: {exc}')
            return {'CANCELLED'}
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
            self.report({'ERROR'}, f'{stem}: bake failed: {exc}')
            return {'CANCELLED'}
        from . import niagara_catalog as ncat
        asset_root = ncat.resolved_asset_root(bpy.path.abspath(getattr(scene, 'arc_ns_asset_root', '') or '') or '') or None
        collection = bpy.data.collections.new('NX_' + stem)
        scene.collection.children.link(collection)
        from . import niagara_fx_materials
        built = rigs.build_system(cache_dir, collection, mesh_objects=_mesh_objects(manifest, asset_root, collection),
                                  materials=niagara_fx_materials.renderer_materials(material_roots(asset_root)))
        built['root']['dr_nx_math'] = 'svml_shim' if shim else 'reference (approximate)'
        scene.frame_end = max(scene.frame_end, manifest['frame_end'])
        kinds = {}
        for r in manifest['renderers']:
            kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
        msg = '%s: %d streams (%s), frames %d-%d, %.1fs%s' % (stem, len(manifest['streams']), ', '.join('%s %d' % kv for kv in sorted(kinds.items())),
                                                              manifest['frame_start'], manifest['frame_end'], time.perf_counter() - t0,
                                                              '' if shim else ', reference math')
        scene['arc_ns_last_report'] = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def draw_playback_box(layout, context):
    scene = context.scene
    if not hasattr(scene, 'arc_ns_play_ticks'):
        return
    box = layout.box()
    col = box.column(align=True)
    col.prop(scene, 'arc_ns_play_ticks')
    col.prop(scene, 'arc_ns_play_at_cursor')
    col.prop(scene, 'arc_ns_play_collision')
    box.operator(ARC_OUTFITS_OT_PlayNiagaraFX.bl_idname, icon='PLAY')
