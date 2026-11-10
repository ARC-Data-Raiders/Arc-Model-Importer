"""Gate R0 Blender side (run inside Blender 5.1, --background --factory-startup). Builds rigs from caches and dumps evaluated data.

blender ... --python niagara_r0_blender.py -- <job.json>
job: {"mode": "dump"|"smoke"|"subuv"|"probe", "repo": REPO, "out": DIR, ...}; writes <out>/... npz/json and prints R0_BLENDER_DONE.
"""
import json
import math
import sys
import traceback
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

job = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text(encoding='utf-8'))
sys.path.insert(0, job['repo'])
import Niagara.niagara as rigs  # noqa: E402

OUT = Path(job['out'])
OUT.mkdir(parents=True, exist_ok=True)
TEST_MESH_VERTS = [(0.1, 0.0, 0.0), (0.0, 0.2, 0.0), (0.0, 0.0, 0.3), (0.05, 0.07, 0.11)]


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return bpy.context.scene


def add_camera(scene, cfg):
    cam = bpy.data.objects.new('R0Cam', bpy.data.cameras.new('R0Cam'))
    scene.collection.objects.link(cam)
    cam.location = cfg['location']
    if cfg.get('look_at') is not None:
        cam.rotation_mode = 'QUATERNION'
        cam.rotation_quaternion = (Vector(cfg['look_at']) - Vector(cfg['location'])).to_track_quat('-Z', 'Y')
    else:
        cam.rotation_euler = [math.radians(a) for a in cfg['rotation_deg']]
    cam.data.type = cfg.get('type', 'PERSP')
    if cam.data.type == 'ORTHO':
        cam.data.ortho_scale = cfg.get('ortho_scale', 6.0)
    cam.data.lens = cfg.get('lens', 35.0)
    scene.camera = cam
    return cam


def test_mesh_object():
    mesh = bpy.data.meshes.new('R0Mesh')
    mesh.from_pydata(TEST_MESH_VERTS, [], [(0, 1, 2), (0, 1, 3), (1, 2, 3), (2, 0, 3)])
    return bpy.data.objects.new('R0Mesh', mesh)


def evaluated_arrays(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    me = obj.evaluated_get(dg).data
    n = len(me.vertices)
    pos = np.zeros(n * 3, dtype=np.float32)
    me.vertices.foreach_get('co', pos)
    out = {'positions': pos.reshape(n, 3)}
    for a in me.attributes:
        if not a.name.startswith('nx_') or a.domain != 'POINT':
            continue
        width = {'FLOAT': 1, 'FLOAT2': 2, 'FLOAT_VECTOR': 3, 'FLOAT_COLOR': 4, 'QUATERNION': 4}.get(a.data_type)
        if width is None:
            continue
        key = {'FLOAT': 'value', 'FLOAT2': 'vector', 'FLOAT_VECTOR': 'vector', 'FLOAT_COLOR': 'color', 'QUATERNION': 'value'}[a.data_type]
        buf = np.zeros(n * width, dtype=np.float32)
        a.data.foreach_get(key, buf)
        out['attr__' + a.name] = buf.reshape(n, width)
    return out


def dump(cache_dirs, cameras, frames_for):
    results = {}
    for cache in cache_dirs:
        cache = Path(cache)
        manifest = json.loads((cache / 'cache_manifest.json').read_text(encoding='utf-8'))
        for ci, cfg in enumerate(cameras):
            scene = reset()
            cam = add_camera(scene, cfg)
            mesh_obj = test_mesh_object()
            built = rigs.rigs_build_system(cache, scene.collection, mesh_objects={m['object_path']: mesh_obj for r in manifest['renderers'] for m in r.get('meshes', [])})
            for _, obj in built['rigs']:
                rigs.set_realize(obj, True)
            frames = frames_for(manifest)
            entry = {'camera': cfg, 'frames': {}}
            for f in frames:
                scene.frame_set(f)
                dg = bpy.context.evaluated_depsgraph_get()
                fr = {'cam_world': [list(r) for r in cam.matrix_world], 'streams': {}, 'lights': {}}
                for sname, obj in built['rigs']:
                    rig_world = [list(r) for r in obj.evaluated_get(dg).matrix_world]
                    path = OUT / ('%s__c%d__%s__f%04d.npz' % (manifest['system'], ci, sname, f))
                    np.savez(path, **evaluated_arrays(obj))
                    fr['streams'][sname] = {'npz': path.name, 'rig_world': rig_world}
                for sname, objs in built['lights']:
                    fr['lights'][sname] = [{'hidden': bool(o.hide_render), 'loc': list(o.location), 'color': list(o.data.color),
                                            'energy': o.data.energy, 'cutoff': o.data.cutoff_distance, 'volume': o.data.volume_factor,
                                            'specular': o.data.specular_factor, 'diffuse': o.data.diffuse_factor} for o in objs]
                entry['frames'][str(f)] = fr
            results['%s__c%d' % (manifest['system'], ci)] = dict(entry, cache=str(cache), system=manifest['system'])
    (OUT / 'dump_index.json').write_text(json.dumps(results, indent=1), encoding='utf-8')


def measure_e1():
    """Irradiance per Watt at 1 m of a Blender point light (Cycles): white emission-free Lambert plane, linear EXR pixel."""
    scene = reset()
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 64
    scene.cycles.max_bounces = 0
    scene.render.resolution_x = scene.render.resolution_y = 32
    scene.view_settings.view_transform = 'Standard'
    scene.world = bpy.data.worlds.new('black')
    scene.world.color = (0.0, 0.0, 0.0)
    bpy.ops.mesh.primitive_plane_add(size=0.2, location=(0, 0, 0))
    plane = bpy.context.active_object
    mat = bpy.data.materials.new('white')
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes['Principled BSDF']
    bsdf.inputs['Base Color'].default_value = (1, 1, 1, 1)
    bsdf.inputs['Roughness'].default_value = 1.0
    bsdf.inputs['Specular IOR Level'].default_value = 0.0
    plane.data.materials.append(mat)
    light = bpy.data.objects.new('L', bpy.data.lights.new('L', 'POINT'))
    light.data.energy = 1000.0
    light.data.shadow_soft_size = 0.0
    light.location = (0, 0, 1)
    scene.collection.objects.link(light)
    cam = add_camera(scene, {'location': (0, 0, 0.5), 'rotation_deg': (0, 0, 0), 'type': 'ORTHO', 'ortho_scale': 0.05})
    scene.render.image_settings.file_format = 'OPEN_EXR'
    path = OUT / 'e1.exr'
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(str(path))
    px = np.array(img.pixels[:], dtype=np.float64).reshape(img.size[1], img.size[0], 4)
    L = float(px[12:20, 12:20, 0].mean())
    return math.pi * L / 1.0 / 1000.0


def eevee_order():
    """Two overlapping alpha-0.5 sprites in one rig: report which colour EEVEE composites on top (instance-order check)."""
    scene = reset()
    cache = Path(job['order_cache'])
    add_camera(scene, {'location': (0, -5, 0), 'rotation_deg': (90, 0, 0), 'type': 'ORTHO', 'ortho_scale': 2.0})
    rigs.rigs_build_system(cache, scene.collection)
    scene.frame_set(1)
    res = {}
    for engine in ('BLENDER_EEVEE', 'CYCLES'):
        scene.render.engine = engine
        if engine == 'CYCLES':
            scene.cycles.device = 'CPU'
            scene.cycles.samples = 16
        scene.render.resolution_x = scene.render.resolution_y = 32
        scene.render.film_transparent = True
        scene.view_settings.view_transform = 'Standard'
        scene.render.image_settings.file_format = 'OPEN_EXR'
        path = OUT / ('order_%s.exr' % engine)
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(str(path))
        px = np.array(img.pixels[:], dtype=np.float64).reshape(img.size[1], img.size[0], 4)
        res[engine] = [round(float(v), 4) for v in px[16, 16]]
    return res


def smoke(cache_dirs, frame):
    report = {}
    for cache in cache_dirs:
        manifest = json.loads((Path(cache) / 'cache_manifest.json').read_text(encoding='utf-8'))
        for engine in ('CYCLES', 'BLENDER_EEVEE'):
            key = '%s__%s' % (manifest['system'], engine)
            try:
                scene = reset()
                add_camera(scene, {'location': (2.5, -3.5, 1.8), 'look_at': manifest['bake_origin_b']})
                rigs.rigs_build_system(cache, scene.collection)
                scene.render.engine = engine
                if engine == 'CYCLES':
                    scene.cycles.device = 'CPU'
                    scene.cycles.samples = 4
                scene.render.resolution_x, scene.render.resolution_y = 160, 90
                scene.render.film_transparent = True
                scene.render.image_settings.file_format = 'PNG'
                scene.render.image_settings.color_mode = 'RGBA'
                scene.frame_set(frame)
                path = OUT / ('smoke_%s.png' % key)
                scene.render.filepath = str(path)
                bpy.ops.render.render(write_still=True)
                img = bpy.data.images.load(str(path))
                alpha = np.array(img.pixels[:], dtype=np.float32).reshape(-1, 4)[:, 3]
                report[key] = {'ok': True, 'alpha_coverage': float(np.mean(alpha > 0.002))}
            except Exception as exc:  # noqa: BLE001
                report[key] = {'ok': False, 'error': repr(exc), 'traceback': traceback.format_exc()[-2000:]}
    return report


def subuv(cache, atlas_cells):
    """Render sprites over a flat-colour atlas (Cycles, linear EXR) and dump the image for the comparator."""
    scene = reset()
    add_camera(scene, job['subuv_camera'])
    rigs.rigs_build_system(cache, scene.collection)
    sx, sy = atlas_cells
    img = bpy.data.images['DR_NX_UVGrid']
    img.source = 'GENERATED'
    img.generated_type = 'BLANK'
    img.generated_width, img.generated_height = 64 * sx, 64 * sy
    img.colorspace_settings.name = 'Non-Color'
    w, h = img.size
    px = np.zeros((h, w, 4), dtype=np.float32)
    colours = np.array(job['atlas_colours'], dtype=np.float32)
    for cy in range(sy):
        for cx in range(sx):
            # UE row cy counts from the top; Blender image rows count from the bottom.
            px[h - (cy + 1) * 64:h - cy * 64, cx * 64:(cx + 1) * 64, :3] = colours[cy * sx + cx]
            px[h - (cy + 1) * 64:h - cy * 64, cx * 64:(cx + 1) * 64, 3] = 1.0
    img.pixels.foreach_set(px.ravel())
    img.pack()
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 16
    scene.cycles.filter_width = 0.01
    scene.render.resolution_x, scene.render.resolution_y = job['subuv_resolution']
    scene.view_settings.view_transform = 'Standard'
    scene.render.image_settings.file_format = 'OPEN_EXR'
    scene.frame_set(1)
    path = OUT / 'subuv.exr'
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    out = bpy.data.images.load(str(path))
    arr = np.array(out.pixels[:], dtype=np.float32).reshape(out.size[1], out.size[0], 4)
    np.save(OUT / 'subuv.npy', arr)


def m1(cache, blend_out):
    """M1: build playback for one system, count drawn instances on every cached frame, render one still, save the blend."""
    scene = reset()
    manifest = json.loads((Path(cache) / 'cache_manifest.json').read_text(encoding='utf-8'))
    add_camera(scene, {'location': [6.0, -9.0, 4.0], 'look_at': manifest['bake_origin_b']})
    built = rigs.rigs_build_system(cache, scene.collection)
    scene.frame_start, scene.frame_end = manifest['frame_start'], manifest['frame_end']
    counts = {}
    for _, obj in built['rigs']:
        rigs.set_realize(obj, True)
    for f in range(manifest['frame_start'], manifest['frame_end'] + 1):
        scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        counts[f] = sum(len(o.evaluated_get(dg).data.vertices) // 4 for _, o in built['rigs'])
    for _, obj in built['rigs']:
        rigs.set_realize(obj, False)
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x, scene.render.resolution_y = 480, 270
    scene.render.film_transparent = True
    scene.frame_set(manifest['frame_start'] + 60)
    scene.render.filepath = str(OUT / 'm1_frame.png')
    bpy.ops.render.render(write_still=True)
    scene.frame_set(manifest['frame_start'])
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_out))
    return {'system': manifest['system'], 'frames': len(counts), 'instances_per_frame': sorted(set(counts.values())),
            'empty_frames': [f for f, c in counts.items() if c == 0], 'blend': str(blend_out)}


report = {'blender': bpy.app.version_string, 'mode': job['mode']}
try:
    if job['mode'] == 'dump':
        cams = job['cameras']
        frames = job.get('frames')
        rel = job.get('frames_rel')
        dump(job['caches'], cams, lambda m: frames if frames else [m['frame_start'] + r for r in rel])
    elif job['mode'] == 'probe':
        report['e1'] = measure_e1()
        report['eevee_order'] = eevee_order()
    elif job['mode'] == 'smoke':
        report['smoke'] = smoke(job['caches'], job['frame'])
    elif job['mode'] == 'subuv':
        subuv(job['cache'], job['atlas_cells'])
    elif job['mode'] == 'm1':
        report['m1'] = m1(job['cache'], job['blend'])
    report['ok'] = True
except Exception as exc:  # noqa: BLE001
    report['ok'] = False
    report['error'] = repr(exc)
    report['traceback'] = traceback.format_exc()
(OUT / ('blender_%s.json' % job['mode'])).write_text(json.dumps(report, indent=1), encoding='utf-8')
print('R0_BLENDER_DONE' if report['ok'] else 'R0_BLENDER_FAILED')
