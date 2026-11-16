"""Retained offline R16f DistanceTexture playback for the exact VisionCone plan."""
import hashlib
import json
import math
from pathlib import Path

import numpy as np

MARKER = 'dr_nx_visioncone_runtime_handler'
OWNER = __name__
# Preserve the exact pending callback across reload, including cleared handlers.
_pending_restore_timer = globals().get('_pending_restore_timer')


def snapshot(cache, frames, frame):
    if not frames or not math.isfinite(frame):
        raise ValueError('Invalid runtime texture timeline')
    timeline, ticks = [r['frame'] for r in frames], [r['tick'] for r in frames]
    if any(type(v) is not int for v in timeline + ticks) or any(b <= a for a,b in zip(timeline,timeline[1:])) or any(b <= a for a,b in zip(ticks,ticks[1:])):
        raise ValueError('Invalid runtime texture frame/tick association')
    selected = frames[max(0, sum(v <= frame for v in timeline)-1)]
    root = Path(cache).resolve()
    path = (root / selected['file']).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Runtime texture snapshot outside cache')
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != selected['sha256']:
        raise ValueError('Runtime texture snapshot hash mismatch')
    import io
    values = np.load(io.BytesIO(payload), allow_pickle=False)
    width, height = selected['size']
    if (type(width) is not int or type(height) is not int or min(width,height) <= 0 or selected['format'] != 'R16f' or values.dtype != np.dtype('<f2') or values.shape != (height,width) or not np.isfinite(values).all()):
        raise ValueError('Unsupported runtime texture snapshot format')
    pixels = np.zeros((height,width,4),np.float32)
    pixels[...,0], pixels[...,3] = values, 1.
    return selected,pixels



def particle_snapshot(cache, frames, particles, frame):
    if (not isinstance(particles, list) or not particles or
            [row.get('frame') for row in particles] != [row['frame'] for row in frames] or
            any(type(row.get('frame')) is not int for row in particles)):
        raise ValueError('Incomplete source particle frame coverage')
    selected = particles[max(0, sum(row['frame'] <= frame for row in particles)-1)]
    root = Path(cache).resolve()
    path = (root / selected['file']).resolve()
    if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != selected['sha256']:
        raise ValueError('Source particle/dynamic data changed after admission')
    return selected


def prepare(runtime, view):
    manifest = runtime['manifest']
    frames = manifest['render_target_snapshots']['System.Render Target 2D']['frames']
    if [r['frame'] for r in frames] != list(range(manifest['frame_start'],manifest['frame_end']+1)) or [r['tick'] for r in frames] != list(range(len(frames))):
        raise ValueError('Incomplete runtime texture frame/tick coverage')
    particles = runtime['geometry_context']['particles']['files']
    for record in frames:
        if record.get('filter') != 'TextureFilter::TF_Bilinear':
            raise ValueError('Unsupported runtime texture filter')
        snapshot(runtime['cache'],frames,record['frame'])
        particle_snapshot(runtime['cache'],frames,particles,record['frame'])
    return dict(cache=str(Path(runtime['cache']).resolve()), frames=frames,
                variable='System.Render Target 2D', child='RenderTarget', parameter='DistanceTexture',
                sampling='bilinear_clamp', slot=8, sampler=3, view=view,
                particles=particles,
                classification='offline_emulator_gpu_compute_readback', production_parity='unverified')


def invalidate(image, error):
    width,height = image.size
    image.pixels.foreach_set(np.zeros(width*height*4,np.float32))
    image.update()
    image['dr_nx_runtime_frame'] = -1
    image['dr_nx_runtime_tick'] = -1
    image['dr_nx_runtime_sha256'] = ''
    image['dr_nx_runtime_error'] = str(error)


def update_image(image, record, frame, scene=None):
    try:
        if 'view' in record:
            import bpy
            from .visioncone_vf import validate_view
            validate_view(record['view'], scene if scene is not None else bpy.context.scene)
            particle_snapshot(record['cache'],record['frames'],record['particles'],frame)
        selected,pixels = snapshot(record['cache'],record['frames'],frame)
        width,height = selected['size']
        if tuple(image.size) != (width,height):
            image.scale(width,height)
        image.pixels.foreach_set(np.flipud(pixels).ravel())
        image.update()
        image['dr_nx_runtime_frame'], image['dr_nx_runtime_tick'] = selected['frame'],selected['tick']
        image['dr_nx_runtime_sha256'] = selected['sha256']
        image['dr_nx_runtime_error'] = ''
    except Exception as error:
        invalidate(image,error)
        raise


def image(record):
    import bpy
    selected,_ = snapshot(record['cache'],record['frames'],bpy.context.scene.frame_current_final)
    result = bpy.data.images.new('NX_VisionCone_DistanceTexture_t8',width=selected['size'][0],height=selected['size'][1],alpha=True,float_buffer=True)
    result.colorspace_settings.name = 'Non-Color'
    result.alpha_mode = 'CHANNEL_PACKED'
    result['dr_nx_runtime_texture'] = json.dumps(record,sort_keys=True)
    update_image(result,record,bpy.context.scene.frame_current_final)
    register()
    return result


def refresh(scene, unused=None):
    import bpy
    from .visioncone_vf import validate_view
    # A missing image or removed record must not leave a previously enabled gate.
    for material in bpy.data.materials:
        if 'dr_nx_runtime_image' in material and material.use_nodes:
            node = material.node_tree.nodes.get('NX runtime valid')
            if node:
                node.outputs[0].default_value = 0.
    for target in bpy.data.images:
        raw = target.get('dr_nx_runtime_texture')
        if not raw:
            continue
        valid = False
        try:
            record = json.loads(raw)
            validate_view(record['view'],scene)
            update_image(target,record,scene.frame_current_final,scene=scene)
            valid = True
        except Exception as error:
            invalidate(target,error)
        for material in bpy.data.materials:
            if material.get('dr_nx_runtime_image') == target.name and material.use_nodes:
                material_valid = valid and not any(node.type == 'TEX_IMAGE' and node.image and node.image.get('dr_nx_static_error') for node in material.node_tree.nodes)
                node = material.node_tree.nodes.get('NX runtime valid')
                if node:
                    node.outputs[0].default_value = float(material_valid)


def load_update(unused):
    import bpy
    from .visioncone import _pinned, _dds_dimensions
    for target in bpy.data.images:
        raw = target.get('dr_nx_visioncone_static')
        if not raw:
            continue
        try:
            record = json.loads(raw)
            resource = record['resource']
            payload = _pinned(dict(file=resource['file'], sha256=record['sha256']))
            width,height,depth = _dds_dimensions(resource,payload)
            target.scale(width,height*depth)
            values = np.frombuffer(payload,'<f4',offset=148).reshape(height*depth,width,4)
            target.pixels.foreach_set(np.flipud(values).ravel())
            target.update()
            target['dr_nx_static_error'] = ''
        except Exception as error:
            invalidate(target,error)
            target['dr_nx_static_error'] = str(error)
    refresh(bpy.context.scene)


def _owned(callback):
    marker = getattr(callback, MARKER, None)
    return marker == OWNER or (marker is True and getattr(callback, '__module__', None) == OWNER)


def unregister():
    """Remove only this installed package's callbacks, including stale reload objects."""
    import bpy
    global _pending_restore_timer
    if _pending_restore_timer is not None and bpy.app.timers.is_registered(_pending_restore_timer):
        bpy.app.timers.unregister(_pending_restore_timer)
    _pending_restore_timer = None
    for name in ('frame_change_post', 'render_pre', 'load_post'):
        handlers = getattr(bpy.app.handlers, name)
        for old in list(handlers):
            if _owned(old):
                timer = getattr(old, '_dr_nx_restore_timer', None)
                if timer is not None and bpy.app.timers.is_registered(timer):
                    bpy.app.timers.unregister(timer)
                handlers.remove(old)


def restore_retained():
    """Run after Blender removes the restricted-data registration context."""
    load_update(None)
    return None


def register():
    import bpy
    global _pending_restore_timer
    unregister()
    for name,callback in dict(frame_change_post=refresh,render_pre=refresh,load_post=load_update).items():
        handlers = getattr(bpy.app.handlers,name)
        setattr(callback,MARKER,OWNER)
        handlers.append(bpy.app.handlers.persistent(callback))
    if hasattr(bpy.data, 'images'):
        restore_retained()
    else:
        # Keep a reference on the owned handler so a reload/disable can cancel it.
        load_update._dr_nx_restore_timer = restore_retained
        _pending_restore_timer = restore_retained
        bpy.app.timers.register(restore_retained, first_interval=0.0)
