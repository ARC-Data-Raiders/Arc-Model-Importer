"""Bounded four-entry appearance imports; Blender work stays on its main thread."""
from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty
from bpy.types import Operator

BATCH_SIZE = 4
PARTS = {
    'BODY': ('Body', 'Bodies', 'arc_appearance_body_skin'),
    'HEAD': ('Head', 'Heads', 'arc_appearance_head'),
    'HAIR': ('Hair', 'Hair', 'arc_appearance_hair'),
    'FACIAL_HAIR': ('FacialHair', 'Facial Hair', 'arc_appearance_facial_hair'),
    'BACKPACK': ('Backpack', 'Backpacks', 'arc_appearance_backpack'),
    'ATTACHMENT': ('Attachment', 'Attachments', 'arc_appearance_attachment'),
    'CHARM': ('Charm', 'Charms', 'arc_appearance_charm'),
    'RAIDER_TOOL': ('RaiderTool', 'Raider Tools', 'arc_appearance_raider_tool'),
}
_ACTIVE = None


def _source_signature(scene):
    return tuple(str(getattr(scene, name, '') or '') for name in ('arc_pak_dir', 'arc_usmap_path', 'arc_pioneer_root'))


def catalog_jobs(part, scene):
    from . import appearance, pak_catalog, properties; from ..Shared import pak_extract
    role, _plural, prop = PARTS[part]
    if part == 'BODY':
        choices = [{'id': key, 'label': label, 'rel_path': 'Characters/Shared/Bodies/SK_Body', 'has_mesh': True}
                   for key, label, _tip in properties.BODY_VARIANTS if key != 'NONE']
    elif part == 'HEAD':
        choices = appearance.list_head_choices()
    elif part == 'HAIR':
        choices = appearance.list_hair_choices()
    elif part == 'FACIAL_HAIR':
        choices = appearance.list_facial_hair_choices()
    else:
        choices = appearance.list_gear_choices(part.casefold())
    game = pak_extract.configured_dir(scene)
    index = pak_extract.load_pak_index(scene) if game else None
    # Head meshes use Head_* names; the generic browser's SK_/SM_ filter is
    # intentionally insufficient for appearance catalogs.
    available = {pak_catalog.canonical_path(path).casefold() for path in index['paths']} if index else None
    jobs, skipped, seen = [], [], set()
    for choice in choices:
        cid = choice.get('id')
        if not cid or cid == 'NONE' or cid in seen:
            continue
        seen.add(cid)
        rel = choice.get('rel_path') or ''
        if game:
            # Older bundled indexes omit Head_* packages entirely. Their
            # authored catalog paths must be attempted, not treated as absent.
            usable = bool(rel) and (part == 'HEAD' or available is None or pak_catalog.canonical_path(pak_extract.game_path(rel)).casefold() in available)
        elif part == 'BODY':
            usable = bool(appearance.list_body_mesh())
        else:
            usable = appearance._mesh_file_usable(choice.get('path') or appearance.resolve_choice_path(cid))
        if not usable:
            skipped.append(choice.get('label') or cid)
            continue
        jobs.append({'id': cid, 'label': choice.get('label') or cid, 'rel': rel, 'role': role, 'part': part, 'prop': prop})
    return jobs, skipped


def _run_export(command):
    """Worker has only immutable strings and subprocess IO; never bpy."""
    proc = subprocess.run(command, capture_output=True, text=True, timeout=600,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return proc.returncode, ((proc.stdout or '') + '\n' + (proc.stderr or '')).strip()


def _run_export_commands(commands):
    """Sequential immutable command plan runs outside Blender on the IO executor."""
    logs = []
    for command in commands:
        code, log = _run_export(command)
        logs.append(log)
        if code:
            return code, "\n".join(logs)
    return 0, "\n".join(logs)


class BatchQueue:
    """Start the next batch only after every current import has returned fully."""
    def __init__(self, jobs, prepare, import_one, finalize=lambda _batch, _value: None):
        self.jobs = list(jobs)
        self.prepare = prepare
        self.import_one = import_one
        self.finalize = finalize
        self.records = []
        self.batches = []
        self.batch = None
        self.future = None
        self.position = 0
        self.cancel_requested = False
        self.done = False
        self.phase = 'Queued'

    def step(self):
        if self.done:
            return True
        if self.future is not None:
            if not self.future.done():
                self.phase = 'Stopping after extraction' if self.cancel_requested else 'Extracting current batch'
                return False
            try:
                value = self.future.result()
                followup = self.finalize(self.batch, value)
                if followup is not None:
                    self.future = followup
                    self.phase = "Extracting authored materials for current batch"
                    return False
            except Exception as exc:
                for job in self.batch:
                    job['prepare_error'] = str(exc)
            self.future = None
        if self.cancel_requested:
            self.done = True
            self.phase = 'Cancelled'
            return True
        if self.batch is None:
            if len(self.records) >= len(self.jobs):
                self.done = True
                self.phase = 'Finished'
                return True
            self.batch = self.jobs[len(self.records):len(self.records) + BATCH_SIZE]
            self.position = 0
            self.batches.append([job['id'] for job in self.batch])
            self.future = self.prepare(self.batch)
            self.phase = 'Preparing current batch'
            return False
        job = self.batch[self.position]
        self.phase = 'Importing ' + job['label']
        try:
            if job.get('prepare_error'):
                raise RuntimeError(job['prepare_error'])
            detail = self.import_one(job)
            record = {'id': job['id'], 'label': job['label'], 'ok': True, 'detail': detail}
        except Exception as exc:
            record = {'id': job['id'], 'label': job['label'], 'ok': False, 'error': str(exc)}
        self.records.append(record)
        self.position += 1
        if self.position == len(self.batch):
            self.batch = None
        return False


def _prepare_batch(owner, batch):
    from . import appearance; from ..Shared import pak_extract
    scene = owner._scene
    game = pak_extract.configured_dir(scene) if str(getattr(scene, "arc_import_source", "PAK")) == "PAK" else ""
    if not game:
        return None
    rels = [job['rel'] for job in batch]
    part = batch[0]['part']
    if part in {'BACKPACK', 'ATTACHMENT', 'CHARM', 'RAIDER_TOOL'}:
        rels.append(appearance._BACKPACK_STRAP_RELS[0])
    if part == 'BACKPACK':
        rels.append(appearance._BACKPACK_FRAME_RELS[0])
        for job in batch:
            rels.extend(appearance.backpack_extraction_rels(job['id'], getattr(owner, '_backpack_colorway', 'BASE')))
    if part == 'CHARM':
        rels.append(appearance._BACKPACK_CHARM_CONNECTOR_RELS[0])
    if owner._reference:
        from . import reference_planes
        for job in batch:
            package = reference_planes.choice_icon_package(appearance.resolve_choice(job['id']) or {}, scene)
            if package:
                rels.append(package)
    paths = []
    cached_models = []
    for rel in rels:
        path = pak_extract.game_path(rel)
        if '/T_UI_Icon_' in path:
            from ..Shared import textures
            if textures.find_asset_from_object_path('/Game/' + path.removeprefix('PioneerGame/Content/'), '.png'):
                continue
        cached = pak_extract._expected_uemodel(pak_extract.cache_dir(), path)
        if cached and appearance._mesh_file_usable(cached) and pak_extract.mesh_cache_is_current(cached, scene):
            cached_models.append(cached)
            continue
        if path not in paths:
            paths.append(path)
    owner._extract_plan = None
    metadata = pak_extract.material_metadata_plan(scene, cached_models)
    if not paths and metadata is None:
        raw = pak_extract.raw_material_metadata_plan(scene, model_paths=cached_models)
        if raw is None:
            return None
        owner._extract_plan = {'raw_plan': raw, 'out': pak_extract.cache_dir(), 'paths': []}
        return owner._executor.submit(_run_export, raw['command'])
    mapping = pak_extract.resolve_usmap(scene)
    if not mapping:
        raise RuntimeError('Select a usmap in Settings')
    out = pak_extract.cache_dir()
    commands = [metadata["command"]] if metadata else []
    if paths:
        commands.append(pak_extract.extraction_command(game, mapping, paths, out_dir=out))
    owner._extract_plan = {'out': out, 'paths': paths, 'revision': pak_extract._extraction_revision(scene),
                            'metadata_models': metadata['models'] if metadata else []}
    return owner._executor.submit(_run_export_commands, commands)


def _finalize_batch(owner, batch, result):
    from . import appearance; from ..Shared import pak_extract
    plan = owner._extract_plan
    code, log = result
    if plan.get('raw_plan'):
        pak_extract.complete_raw_material_metadata(owner._scene, plan['raw_plan'], code, log)
        return None
    pak_extract.complete_extraction(plan['out'], plan['paths'], code, log, plan['revision'])
    if code:
        raise RuntimeError('Pak preparation failed: ' + pak_extract._log_tail(log))
    missing_metadata = [path for path in plan.get('metadata_models', ()) if not pak_extract.mesh_material_metadata_is_current(path)]
    if missing_metadata:
        raise RuntimeError('Native metadata does not match cached mesh: ' + str(missing_metadata))
    if batch[0]['part'] == 'BACKPACK' and not plan.get('material_pass'):
        dependencies = []
        for job in batch:
            try:
                dependencies.extend(appearance.backpack_material_extraction_rels(job['id'], getattr(owner, '_backpack_colorway', 'BASE')))
            except Exception as exc:
                job['prepare_error'] = str(exc)
        dependencies = list(dict.fromkeys(dependencies))
        if dependencies:
            command = pak_extract.extraction_command(pak_extract.configured_dir(owner._scene),
                pak_extract.resolve_usmap(owner._scene), dependencies, out_dir=plan['out'])
            owner._extract_plan = {**plan, 'paths': dependencies, 'material_pass': True}
            return owner._executor.submit(_run_export, command)
    models = [pak_extract._expected_uemodel(plan['out'], pak_extract.game_path(job['rel'])) for job in batch]
    raw = pak_extract.raw_material_metadata_plan(owner._scene, model_paths=models)
    if raw is not None:
        owner._extract_plan = {**plan, 'paths': [], 'raw_plan': raw}
        return owner._executor.submit(_run_export, raw['command'])
    for job in batch:
        path = pak_extract._expected_uemodel(plan['out'], pak_extract.game_path(job['rel']))
        if not appearance._mesh_file_usable(path):
            job['prepare_error'] = 'No usable mesh after extraction: ' + job['rel']


def _import_one(owner, job):
    from . import appearance, import_layout; from ..Shared import operators
    scene = owner._scene
    before = set(bpy.data.objects)
    overrides = {job['prop']: job['id'], 'arc_import_slot_offset': True,
                 'arc_appearance_attach_to_outfit': False, 'arc_appearance_replace_selected': False,
                 'arc_include_reference': owner._reference, 'arc_appearance_hair_color': owner._hair_color}
    if job['part'] == 'BACKPACK':
        wanted = getattr(owner, '_backpack_colorway', 'BASE')
        valid = {c['id'] for c in appearance.list_backpack_colorways(job['id'])}
        overrides['arc_appearance_backpack_colorway'] = wanted if wanted in valid else 'BASE'
    saved = {key: getattr(scene, key) for key in overrides}
    try:
        for key, value in overrides.items():
            setattr(scene, key, value)
        slot = import_layout.allocate_slot(job['role'], scene=scene)
        with bpy.context.temp_override(window=owner._window, scene=scene, view_layer=owner._view_layer, collection=owner._collection):
            count, messages, objects = appearance.import_appearance_parts(
                bpy.context, roles={job['role']}, replace_selected=False, standalone=True, layout_slot=slot)
            roles = {str(obj.get('arc_assembly_role') or '') for obj in objects if obj.type == 'MESH'}
            primary = job['role'] in roles
            if not count or not primary:
                raise RuntimeError('; '.join(messages[-4:]) or 'Primary asset did not import')
            if job['role'] in {'Body', 'Head', 'Hair', 'FacialHair', 'Backpack', 'Attachment', 'Charm', 'RaiderTool'}:
                operators._maybe_import_references(bpy.context, objects, roles={job['role']})
            owner._view_layer.update()
        return {'slot': slot, 'meshes': count, 'objects': [obj.name for obj in objects], 'messages': messages}
    except Exception:
        # Standalone imports never reuse existing objects. Roll back only this
        # incomplete entry, retaining prior completed entries and the user's scene.
        for obj in list(bpy.data.objects):
            if obj not in before:
                bpy.data.objects.remove(obj, do_unlink=True)
        raise
    finally:
        for key, value in saved.items():
            setattr(scene, key, value)


class ARC_OUTFITS_OT_AddAllAppearance(Operator):
    bl_idname = 'arc_outfits.add_all_appearance'
    bl_label = 'Add All Appearance Assets'
    bl_description = 'Import every entry into the first slot missing its type, in completed batches of four'
    bl_options = {'REGISTER', 'UNDO'}
    part: EnumProperty(items=tuple((key, value[1], 'Import all ' + value[1].lower()) for key, value in PARTS.items()), default='HEAD')

    @classmethod
    def poll(cls, context):
        return _ACTIVE is None

    def execute(self, context):
        global _ACTIVE
        from ..Shared import utils, pak_extract; from . import appearance
        if not utils.get_pioneer_root() and not pak_extract.configured_dir(context.scene):
            self.report({'ERROR'}, 'Set PioneerGame Folder or Paks Folder in Settings')
            return {'CANCELLED'}
        try:
            jobs, skipped = catalog_jobs(self.part, context.scene)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if not jobs:
            self.report({'WARNING'}, 'No available assets for this type')
            return {'CANCELLED'}
        self._scene = context.scene
        self._source_state = _source_signature(context.scene)
        self._wm = context.window_manager
        self._window = context.window
        self._view_layer = context.view_layer
        self._collection = context.scene.collection
        self._reference = bool(context.scene.arc_include_reference)
        self._hair_color = context.scene.arc_appearance_hair_color
        self._backpack_colorway = getattr(context.scene, "arc_appearance_backpack_colorway", "BASE")
        self._selection = list(context.selected_objects)
        self._active_object = context.view_layer.objects.active
        self._skipped = skipped
        self._timer = None
        self._closed = False
        if self.part in {'HAIR', 'FACIAL_HAIR'} and self._hair_color != 'NONE':
            if not appearance.resolve_hair_color_path(self._hair_color, context.scene):
                self.report({'ERROR'}, 'Selected hair color could not be loaded')
                return {'CANCELLED'}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ArcAppearanceExtract')
        self._queue = BatchQueue(jobs, lambda batch: _prepare_batch(self, batch),
                                 lambda job: _import_one(self, job), lambda batch, value: _finalize_batch(self, batch, value))
        _ACTIVE = self
        self._wm.progress_begin(0, len(jobs))
        if bpy.app.background:
            try:
                while not self._queue.step():
                    time.sleep(.01)
            except Exception as exc:
                self._finish(error=str(exc));return {'CANCELLED'}
            self._finish();return {'FINISHED'}
        self._timer = self._wm.event_timer_add(.15, window=context.window)
        self._wm.modal_handler_add(self)
        self._redraw()
        return {'RUNNING_MODAL'}

    def _redraw(self):
        queue = self._queue
        self._wm.progress_update(len(queue.records))
        for area in self._window.screen.areas if self._window else ():
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    def modal(self, context, event):
        if self._closed:
            return {'CANCELLED'}
        if event.type == 'ESC':
            self._queue.cancel_requested = True
        if context.scene != self._scene or _source_signature(self._scene) != self._source_state:
            self._queue.cancel_requested = True
        if event.type == 'TIMER':
            try:
                done = self._queue.step()
                self._redraw()
                if done:
                    cancelled = self._queue.cancel_requested
                    self._finish()
                    # Retained completed imports need a single undo step even
                    # when the user stops the remaining queue.
                    return {'CANCELLED'} if cancelled and not self._queue.records else {'FINISHED'}
            except Exception as exc:
                self._finish(error=str(exc));return {'CANCELLED'}
        return {'PASS_THROUGH'}

    def _finish(self, error=''):
        global _ACTIVE
        if self._closed:
            return
        self._closed = True
        if self._timer:
            self._wm.event_timer_remove(self._timer)
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._wm.progress_end()
        records = self._queue.records
        success = sum(record['ok'] for record in records)
        report = {'part': self.part, 'total': len(self._queue.jobs), 'imported': success,
                  'failed': len(records)-success, 'cancelled': self._queue.cancel_requested,
                  'skipped': self._skipped, 'batches': self._queue.batches, 'items': records, 'error': error}
        try:
            self._scene['arc_add_all_last_report'] = json.dumps(report)
            bpy.ops.object.select_all(action='DESELECT')
            for obj in self._selection:
                if obj.name in bpy.data.objects:
                    obj.select_set(True)
            if self._active_object and self._active_object.name in bpy.data.objects:
                self._view_layer.objects.active = self._active_object
        except (ReferenceError, RuntimeError):
            pass
        _ACTIVE = None
        self._redraw()
        message = f'Imported {success}/{len(self._queue.jobs)} {PARTS[self.part][1].lower()}'
        if report['failed']:
            message += f"; {report['failed']} failed (see last queue report)"
        if error:
            message += '; ' + error
        self.report({'WARNING'} if error or report['failed'] else {'INFO'}, message)

    def cancel(self, context):
        self._queue.cancel_requested = True
        self._finish()


class ARC_OUTFITS_OT_CancelAppearanceQueue(Operator):
    bl_idname = 'arc_outfits.cancel_appearance_queue'
    bl_label = 'Cancel Add All'
    def execute(self, context):
        if _ACTIVE:
            _ACTIVE._queue.cancel_requested = True
        return {'FINISHED'}


def queue_status():
    if _ACTIVE is None:
        return None
    queue = _ACTIVE._queue
    return {'part': _ACTIVE.part, 'done': len(queue.records), 'total': len(queue.jobs),
            'batch': len(queue.batches), 'phase': queue.phase}


@persistent
def _stop_for_load(_unused):
    if _ACTIVE:
        _ACTIVE.cancel(None)


def register():
    for old in list(bpy.app.handlers.load_pre):
        if old.__module__ == __name__:
            bpy.app.handlers.load_pre.remove(old)
    bpy.app.handlers.load_pre.append(_stop_for_load)


def unregister():
    _stop_for_load(None)
    for old in list(bpy.app.handlers.load_pre):
        if old.__module__ == __name__:
            bpy.app.handlers.load_pre.remove(old)
