"""
Processing logic for PSK imports and model texturing
"""

import os
import bpy
import bpy_extras
import mathutils
from bpy.props import StringProperty
from bpy.types import Operator

from . import utils
from . import textures
from . import materials
from . import importing
from . import rig

def process_entry(entry) -> tuple:
    """Process a single PSK entry: import and set up materials."""
    psk_path = bpy.path.abspath(entry.psk_path)
    json_path = "" if entry.skin_choice == 'NONE' else bpy.path.abspath(entry.skin_choice)
    if not json_path:
        json_path = textures.get_base_skin_json(psk_path, entry.manual_skins_folder)
    body_variant = entry.body_choice if hasattr(entry, 'body_choice') else 'NONE'
    
    if not os.path.isfile(psk_path):
        return False, f"PSK not found: {psk_path}", []
    
    folder = os.path.dirname(psk_path)
    model_type = textures.detect_model_type(psk_path)
    
    try:
        new_objects = importing.import_psk(psk_path)
    except RuntimeError as e:
        return False, str(e), []
    
    mesh_objects = [o for o in new_objects if o.type == "MESH"]
    
    if model_type == "face":
        for obj in mesh_objects:
            materials.setup_face_material(obj, psk_path)
        return True, f"Imported (face): {os.path.basename(psk_path)}", new_objects
    
    elif model_type == "body":
        for obj in mesh_objects:
            materials.setup_body_material(obj, psk_path, body_variant)
        label = body_variant if body_variant != 'NONE' else 'no skin'
        return True, f"Imported (body, {label}): {os.path.basename(psk_path)}", new_objects
    
    elif model_type == "hair":
        hair_json = entry.hair_mi if hasattr(entry, 'hair_mi') and entry.hair_mi != 'NONE' else ""
        for obj in mesh_objects:
            materials.setup_hair_material(obj, hair_json)
        return True, f"Imported (hair): {os.path.basename(psk_path)}", new_objects
    
    elif model_type == "weapon":
        for obj in mesh_objects:
            materials.setup_weapon_material(obj, psk_path)
        return True, f"Imported (weapon): {os.path.basename(psk_path)}", new_objects
    
    elif model_type == "clothing":
        decal_folder = utils.get_decal_folder()
        colours = textures.parse_skin_colours(json_path) if json_path else {}
        selected_skin_name = ""
        if entry.skin_choice and entry.skin_choice != "NONE":
            skin_dir_name = os.path.basename(os.path.dirname(bpy.path.abspath(entry.skin_choice)))
            selected_skin_name = skin_dir_name
        for obj in mesh_objects:
            materials.setup_arc_texturer_material(
                obj, folder, colours, psk_path,
                json_path=json_path, decal_folder=decal_folder,
                selected_skin_name=selected_skin_name,
                manual_skins_folder=entry.manual_skins_folder
            )
        label = "with skin colours" if colours else "default skin"
        return True, f"Imported ({label}): {os.path.basename(psk_path)}", new_objects
    
    elif model_type == "misc":
        for obj in mesh_objects:
            materials.setup_misc_material(obj, psk_path)
        return True, f"Imported (misc): {os.path.basename(psk_path)}", new_objects
    
    else:
        tex_folder = os.path.join(folder, "Textures")
        if os.path.isdir(tex_folder):
            for obj in mesh_objects:
                mat = bpy.data.materials.new(name=obj.name + "_Mat")
                mat.use_nodes = True
                obj.active_material = mat
                nodes = mat.node_tree.nodes
                row = 400
                try:
                    for fname in sorted(f for f in os.listdir(tex_folder) if f.lower().endswith(".png")):
                        img = bpy.data.images.load(os.path.join(tex_folder, fname), check_existing=True)
                        node = nodes.new("ShaderNodeTexImage")
                        node.image = img
                        node.label = fname
                        node.interpolation = "Cubic"
                        node.location = (-600, row)
                        row -= 300
                except OSError:
                    pass
        return True, f"Imported (unknown type): {os.path.basename(psk_path)}", new_objects

def batch_import_instances(context, selected_presets) -> tuple:
    """Import one full model instance per selected outfit colourway."""
    entries = list(context.scene.arc_psk_entries)
    if not entries:
        return 0, 0
    
    types = [textures.detect_model_type(bpy.path.abspath(e.psk_path)) for e in entries]
    any_clothing = any(t == "clothing" for t in types)
    dominant_type = "weapon" if all(t == "weapon" for t in types) else ""
    
    scene = context.scene
    x_cursor = 0.0
    margin_frac = 0.25
    instances = 0
    parts_total = 0
    
    for preset_name, preset_path in selected_presets:
        importing.apply_outfit_preset(context, preset_path)
        
        inst_objs = []
        for entry in entries:
            ok, msg, new_objs = process_entry(entry)
            inst_objs.extend(new_objs)
            if ok:
                parts_total += 1
                print(f"    [{preset_name}] {msg}")
            else:
                print(f"    [{preset_name}] FAILED: {msg}")
        
        if not inst_objs:
            continue
        
        rig.fix_rig_all(inst_objs, merge=any_clothing, model_type=dominant_type)
        
        def _still_alive(o):
            try:
                o.name
                return True
            except ReferenceError:
                return False
        inst_objs = [o for o in inst_objs if _still_alive(o)]
        
        coll_name = f"{preset_name}"
        coll = bpy.data.collections.new(coll_name)
        scene.collection.children.link(coll)
        for o in inst_objs:
            for c in list(o.users_collection):
                c.objects.unlink(o)
            coll.objects.link(o)
        
        context.view_layer.update()
        minx = maxx = None
        for o in inst_objs:
            if o.type != 'MESH':
                continue
            for corner in o.bound_box:
                wx = (o.matrix_world @ mathutils.Vector(corner)).x
                minx = wx if minx is None else min(minx, wx)
                maxx = wx if maxx is None else max(maxx, wx)
        
        if minx is not None:
            width = max(maxx - minx, 1e-4)
            shift = x_cursor - minx
            for o in inst_objs:
                if o.parent is None:
                    o.location.x += shift
            x_cursor += width * (1.0 + margin_frac)
        
        instances += 1
    
    return instances, parts_total

def texture_existing_model(obj, model_name: str, manual_folder: str = "") -> bool:
    """Apply Arc Raiders texturing to an existing model."""
    psk_path = find_psk_for_model(model_name, manual_folder)
    if not psk_path:
        print(f"Arc Raiders: Could not find PSK for '{model_name}'")
        return False
    
    temp_entry = None
    for entry in bpy.context.scene.arc_psk_entries:
        if entry.display_name == model_name:
            temp_entry = entry
            break
    
    if not temp_entry:
        temp_entry = bpy.context.scene.arc_psk_entries.add()
        temp_entry.psk_path = psk_path
        temp_entry.display_name = model_name
    
    bpy.ops.arc.confirm_psk_import('INVOKE_DEFAULT')
    return True

def find_psk_for_model(model_name: str, manual_folder: str = "") -> str:
    """Find a PSK file matching the model name."""
    if manual_folder and os.path.isdir(manual_folder):
        psks = utils.find_psks_in_folder(manual_folder)
        for psk in psks:
            if model_name.lower() in os.path.basename(psk).lower():
                return psk
        for ext in ['.psk', '.pskx']:
            candidate = os.path.join(manual_folder, f"{model_name}{ext}")
            if os.path.isfile(candidate):
                return candidate
        return ""
    
    root = utils.get_pioneer_root()
    if root:
        assets_dir = utils.find_relative_dir(root, ["Characters", "Assets"])
        if assets_dir:
            try:
                for char_dir in os.listdir(assets_dir):
                    char_path = utils.find_relative_dir(root, ["Characters", "Assets", char_dir])
                    if not char_path:
                        continue
                    try:
                        for part_dir in os.listdir(char_path):
                            part_path = os.path.join(char_path, part_dir)
                            if os.path.isdir(part_path):
                                psks = utils.find_psks_in_folder(part_path)
                                for psk in psks:
                                    if model_name.lower() in os.path.basename(psk).lower():
                                        return psk
                    except OSError:
                        continue
            except OSError:
                pass
        
        weapons_path = utils.find_relative_dir(root, ["Items", "Firearms"])
        if weapons_path:
            try:
                for subdir in os.listdir(weapons_path):
                    sub_path = os.path.join(weapons_path, subdir)
                    if os.path.isdir(sub_path):
                        psks = utils.find_psks_in_folder(sub_path)
                        for psk in psks:
                            if model_name.lower() in os.path.basename(psk).lower():
                                return psk
            except OSError:
                pass
    
    return ""


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class ARC_OT_SelectHairMI(Operator):
    """Select a hair MI JSON for this entry."""
    bl_idname = "arc.select_hair_mi"
    bl_label = "Select Hair MI"
    psk_path: bpy.props.StringProperty(options={'HIDDEN'})
    mi_path: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        for entry in context.scene.arc_psk_entries:
            if bpy.path.abspath(entry.psk_path) == bpy.path.abspath(self.psk_path):
                entry.hair_mi = self.mi_path
                break
        return {'FINISHED'}


class ARC_OT_PickManualOutfitFolder(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse to the DA_OI_Outfit folder for this character."""
    bl_idname = "arc.pick_manual_outfit_folder"
    bl_label = "Select DA_OI_Outfit Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={'HIDDEN'})

    def invoke(self, context, event):
        root = utils.get_pioneer_root()
        default_dir = utils.find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"]) if root else ""
        if default_dir:
            self.filepath = default_dir.rstrip("/\\") + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        folder = os.path.dirname(bpy.path.abspath(self.filepath))
        if not folder:
            folder = bpy.path.abspath(self.filepath)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"Not a valid folder: {folder}")
            return {'CANCELLED'}
        context.scene.arc_manual_outfit_folder = folder
        found = len(importing.scan_outfit_presets_in_folder(folder))
        if found:
            self.report({'INFO'}, f"Found {found} outfit colourway(s) in '{os.path.basename(folder)}'.")
        else:
            self.report({'WARNING'}, f"No DA_OI_Outfit_..._Color_....json files found in '{os.path.basename(folder)}'.")
        importing.populate_outfit_selections(context)
        bpy.ops.arc.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OT_ClearManualOutfitFolder(Operator):
    """Clear the manual DA_OI_Outfit folder override."""
    bl_idname = "arc.clear_manual_outfit_folder"
    bl_label = "Clear Outfit Folder Override"

    def execute(self, context):
        context.scene.arc_manual_outfit_folder = ""
        importing.populate_outfit_selections(context)
        bpy.ops.arc.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OT_ImportSinglePSK(Operator):
    """Import a single .psk file."""
    bl_idname = "arc.import_single_psk"
    bl_label = "Import Single Model"
    bl_options = {'REGISTER', 'UNDO'}
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.psk;*.pskx", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        psk_path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(psk_path):
            self.report({'ERROR'}, f"File not found: {psk_path}")
            return {'CANCELLED'}
        scene = context.scene
        scene.arc_psk_entries.clear()
        entry = scene.arc_psk_entries.add()
        entry.psk_path = psk_path
        entry.display_name = os.path.basename(psk_path)
        bpy.ops.arc.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OT_ImportOutfitFolder(Operator):
    """Select a folder — PSKs are auto-discovered from subfolders."""
    bl_idname = "arc.import_outfit_folder"
    bl_label = "Import Folder"
    bl_options = {'REGISTER', 'UNDO'}
    directory: StringProperty(subtype='DIR_PATH')

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        folder = bpy.path.abspath(self.directory)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"Not a valid folder: {folder}")
            return {'CANCELLED'}
        psks = utils.find_psks_in_folder(folder)
        if not psks:
            self.report({'ERROR'}, f"No .psk/.pskx files found in folder or immediate subfolders of: {folder}")
            return {'CANCELLED'}
        scene = context.scene
        scene.arc_psk_entries.clear()
        for psk_path in psks:
            entry = scene.arc_psk_entries.add()
            entry.psk_path = psk_path
            entry.display_name = os.path.basename(psk_path)
        self.report({'INFO'}, f"Found {len(psks)} PSK(s).")
        bpy.ops.arc.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OT_PickOutfitCSV(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse for a different outfit_reference.csv."""
    bl_idname = "arc.pick_outfit_csv"
    bl_label = "Select Outfit CSV"
    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv", options={'HIDDEN'})

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({'ERROR'}, f"Not a valid file: {path}")
            return {'CANCELLED'}
        context.scene.arc_outfit_csv_path = path
        from .properties import rebuild_csv_outfit_map
        rebuild_csv_outfit_map(path)
        rows = importing.load_outfit_csv(path)
        self.report({'INFO'}, f"Loaded {len(rows)} row(s) from '{os.path.basename(path)}'.")
        return {'FINISHED'}


class ARC_OT_ClearOutfitCSV(Operator):
    """Go back to the CSV bundled with this addon."""
    bl_idname = "arc.clear_outfit_csv"
    bl_label = "Use Bundled CSV"

    def execute(self, context):
        context.scene.arc_outfit_csv_path = ""
        from .properties import rebuild_csv_outfit_map
        rebuild_csv_outfit_map("")
        return {'FINISHED'}


class ARC_OT_LoadOutfit(Operator):
    """Load all PSK parts for the selected outfit from the dropdown."""
    bl_idname = "arc.load_outfit"
    bl_label = "Load Outfit"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        root = utils.get_pioneer_root()
        if not root:
            self.report({'ERROR'}, "Set the PioneerGame root folder first.")
            return {'CANCELLED'}
        choice = scene.arc_selected_outfit
        if not choice:
            self.report({'ERROR'}, "No outfit selected — click one in the list first.")
            return {'CANCELLED'}
        rows = importing.load_outfit_csv(importing.get_outfit_csv_path(context))
        row = importing.find_outfit_row(rows, choice)
        if not row:
            self.report({'ERROR'}, "No outfit selected.")
            return {'CANCELLED'}
        item_ui_folders = [f.strip() for f in row.get("Item/UI Folder Name", "").split(";") if f.strip()]
        if not item_ui_folders:
            self.report({'ERROR'}, "Selected outfit has no Item/UI Folder Name to import from.")
            return {'CANCELLED'}
        psks = importing.collect_psks_for_outfit_row(root, item_ui_folders)
        if not psks:
            self.report({'ERROR'}, "No PSK files found for this outfit's DA_OI parts.")
            return {'CANCELLED'}
        scene.arc_psk_entries.clear()
        for psk_path in psks:
            entry = scene.arc_psk_entries.add()
            entry.psk_path = psk_path
            entry.display_name = os.path.basename(psk_path)
        self.report({'INFO'}, f"Queued {len(psks)} part(s) for '{row.get('Flavour') or row.get('ST')}'.")
        bpy.ops.arc.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OT_SelectOutfit(Operator):
    """Select an outfit from the searchable list."""
    bl_idname = "arc.select_outfit"
    bl_label = "Select Outfit"
    outfit_key: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        context.scene.arc_selected_outfit = self.outfit_key
        context.scene.arc_selected_outfit_browse = self.outfit_key
        return {'FINISHED'}


class ARC_OT_ClearOutfitSearch(Operator):
    """Clear the outfit search filter."""
    bl_idname = "arc.clear_outfit_search"
    bl_label = "Clear Search"

    def execute(self, context):
        context.scene.arc_outfit_search = ""
        return {'FINISHED'}


class ARC_OT_MergeSelectedArmatures(Operator):
    """Merge all selected armatures using Arc Raiders rig fix logic."""
    bl_idname = "arc.merge_selected_armatures"
    bl_label = "Merge Selected Armatures"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        armatures = [o for o in context.selected_objects if o.type == 'ARMATURE']
        if len(armatures) < 2:
            self.report({'WARNING'}, "Select at least two armatures to merge.")
            return {'CANCELLED'}
        rig.fix_rig_all(armatures, merge=True)
        self.report({'INFO'}, f"Merged {len(armatures)} armatures.")
        return {'FINISHED'}


class ARC_OT_ApplyOutfitPreset(Operator):
    """Apply the selected outfit preset's per-part skin colours."""
    bl_idname = "arc.apply_outfit_preset"
    bl_label = "Apply Outfit Preset"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        preset_path = context.scene.arc_outfit_preset
        if not preset_path or preset_path == 'NONE':
            self.report({'WARNING'}, "No outfit preset selected.")
            return {'CANCELLED'}
        updated = importing.apply_outfit_preset(context, preset_path)
        if updated:
            self.report({'INFO'}, f"Outfit preset applied to {updated} part(s).")
        else:
            self.report({'WARNING'}, "Outfit preset didn't match any queued parts.")
        return {'FINISHED'}


class ARC_OT_PickManualSkinsFolder(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse to a folder to use as this part's Skins source."""
    bl_idname = "arc.pick_manual_skins_folder"
    bl_label = "Select Skins Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={'HIDDEN'})
    entry_index: bpy.props.IntProperty(default=-1, options={'HIDDEN'})

    def execute(self, context):
        entries = context.scene.arc_psk_entries
        if not (0 <= self.entry_index < len(entries)):
            self.report({'ERROR'}, "Internal error: part entry not found.")
            return {'CANCELLED'}
        folder = os.path.dirname(bpy.path.abspath(self.filepath))
        if not folder:
            folder = bpy.path.abspath(self.filepath)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"Not a valid folder: {folder}")
            return {'CANCELLED'}
        entry = entries[self.entry_index]
        entry.manual_skins_folder = folder
        found = len(textures.scan_skins(entry.psk_path, folder))
        if found:
            self.report({'INFO'}, f"Found {found} skin(s) in '{os.path.basename(folder)}'.")
        else:
            self.report({'WARNING'}, f"No skin JSONs found in '{os.path.basename(folder)}'.")
        bpy.ops.arc.confirm_psk_import('INVOKE_DEFAULT')
        return {'FINISHED'}


class ARC_OT_ConfirmPSKImport(Operator):
    """Review parts, assign skins, then confirm import."""
    bl_idname = "arc.confirm_psk_import"
    bl_label = "PSKImporter_SIL_AI"
    bl_options = {'REGISTER', 'UNDO'}

    def _has_any_choices(self, context) -> bool:
        for entry in context.scene.arc_psk_entries:
            if textures.scan_skins(entry.psk_path, entry.manual_skins_folder):
                return True
            if textures.is_body(entry.psk_path):
                return True
            if textures.is_hair(entry.psk_path) and textures.scan_hair_mis(entry.psk_path):
                return True
            if textures.detect_model_type(bpy.path.abspath(entry.psk_path)) == "clothing":
                return True
        return False

    def invoke(self, context, event):
        importing.populate_outfit_selections(context)
        has_batch = len(context.scene.arc_outfit_selections) > 0
        if not self._has_any_choices(context) and not has_batch:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=640)

    def draw(self, context):
        layout = self.layout
        entries = context.scene.arc_psk_entries
        if not entries:
            layout.label(text="No PSK files queued.", icon='ERROR')
            return

        layout.label(text=f"{len(entries)} part(s) — assign skins then click OK:", icon='IMPORT')

        from .properties import make_outfit_preset_items
        outfit_items = make_outfit_preset_items(self, context)
        if len(outfit_items) > 1:
            preset_box = layout.box()
            row = preset_box.row(align=True)
            row.prop(context.scene, "arc_outfit_preset", text="Outfit")
            row.operator("arc.apply_outfit_preset", text="Apply", icon='CHECKMARK')

        sels = context.scene.arc_outfit_selections
        manual_outfit = getattr(context.scene, 'arc_manual_outfit_folder', '')
        bbox = layout.box()
        bbox.label(text="Batch import colourways — each becomes a separate model:", icon='DUPLICATE')

        if manual_outfit:
            mrow = bbox.row()
            mrow.label(text=f"Outfit source: {manual_outfit}", icon='FILE_FOLDER')
            mrow.operator("arc.pick_manual_outfit_folder", text="Change...", icon='FILEBROWSER')
            mrow.operator("arc.clear_manual_outfit_folder", text="", icon='X')

        if len(sels) > 0:
            grid = bbox.grid_flow(row_major=True, columns=3, even_columns=True)
            for s in sels:
                grid.prop(s, "selected", text=s.preset_name)
            n_sel = sum(1 for s in sels if s.selected)
            if n_sel:
                bbox.label(text=f"{n_sel} selected → {n_sel} separate instance(s)", icon='INFO')
            else:
                bbox.label(text="None ticked → single import using the choices below", icon='INFO')
        else:
            bbox.label(text="No outfit colourway data detected automatically.", icon='ERROR')
            bbox.label(text="The DA_OI_Outfit folder name may not match the character — pick it manually:")
            bbox.operator("arc.pick_manual_outfit_folder", text="Browse for DA_OI_Outfit Folder...", icon='FILEBROWSER')

        layout.separator()

        for idx, entry in enumerate(entries):
            skins = textures.scan_skins(entry.psk_path, entry.manual_skins_folder)
            is_body = textures.is_body(entry.psk_path)
            is_hair = textures.is_hair(entry.psk_path)
            is_clothing = textures.detect_model_type(bpy.path.abspath(entry.psk_path)) == "clothing"
            hair_mis = textures.scan_hair_mis(entry.psk_path) if is_hair else []
            if not skins and not is_body and not hair_mis and not is_clothing:
                continue
            box = layout.box()
            box.label(text=entry.display_name, icon='FILE')
            if skins:
                box.prop(entry, "skin_choice", text="Skin")
                if entry.manual_skins_folder:
                    mrow = box.row()
                    mrow.label(text=f"Skin source: {os.path.basename(entry.manual_skins_folder)}", icon='FILE_FOLDER')
                    mop = mrow.operator("arc.pick_manual_skins_folder", text="Change...", icon='FILEBROWSER')
                    mop.entry_index = idx
            elif is_clothing:
                warn = box.box()
                warn.label(text="No skin data detected for this part.", icon='ERROR')
                warn.label(text="The Skins folder name may not match — pick it manually:")
                wop = warn.operator("arc.pick_manual_skins_folder", text="Browse for Skins Folder...", icon='FILEBROWSER')
                wop.entry_index = idx
            if is_body:
                box.prop(entry, "body_choice", text="Body Skin")
            if is_hair and hair_mis:
                col = box.column(align=True)
                col.label(text="Hair MI — click to select:")
                for mi_name, mi_path in hair_mis:
                    is_sel = entry.hair_mi == mi_path
                    row = col.row()
                    row.alert = is_sel
                    icon = 'LAYER_ACTIVE' if is_sel else 'LAYER_USED'
                    op = row.operator("arc.select_hair_mi",
                                      text=("\u2713 " if is_sel else "  ") + mi_name,
                                      icon=icon)
                    op.psk_path = entry.psk_path
                    op.mi_path = mi_path

    def execute(self, context):
        from .properties import make_outfit_preset_items

        selected = [(s.preset_name, bpy.path.abspath(s.json_path))
                    for s in context.scene.arc_outfit_selections if s.selected]
        if selected:
            instances, parts = batch_import_instances(context, selected)
            self.report({'INFO'}, f"Arc Raiders: batch imported {instances} instance(s) ({parts} part(s) total).")
            context.scene.arc_psk_entries.clear()
            context.scene.arc_outfit_selections.clear()
            return {'FINISHED'}

        entries = context.scene.arc_psk_entries
        total = len(entries)
        ok_count = 0
        all_new_objs = []

        for entry in entries:
            ok, msg, new_objs = process_entry(entry)
            all_new_objs.extend(new_objs)
            if ok:
                ok_count += 1
                self.report({'INFO'}, msg)
            else:
                self.report({'WARNING'}, msg)

        any_clothing = any(
            textures.detect_model_type(bpy.path.abspath(e.psk_path)) == "clothing"
            for e in entries
        )
        types = [textures.detect_model_type(bpy.path.abspath(e.psk_path)) for e in entries]
        dominant_type = "weapon" if all(t == "weapon" for t in types) else ""
        rig.fix_rig_all(all_new_objs, merge=any_clothing, model_type=dominant_type)

        self.report({'INFO'}, f"Arc Raiders: {ok_count}/{total} part(s) imported.")
        context.scene.arc_psk_entries.clear()
        context.scene.arc_outfit_selections.clear()
        return {'FINISHED'}


class ARC_OT_PickPioneerRoot(Operator, bpy_extras.io_utils.ImportHelper):
    """Browse to the PioneerGame root folder."""
    bl_idname = "arc.pick_pioneer_root"
    bl_label = "Select PioneerGame Folder"
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={'HIDDEN'})

    def execute(self, context):
        folder = os.path.dirname(bpy.path.abspath(self.filepath))
        if not folder:
            folder = bpy.path.abspath(self.filepath)
        context.scene.arc_pioneer_root = folder
        return {'FINISHED'}


class ARC_OT_ClearPioneerRoot(Operator):
    """Clear the PioneerGame root folder."""
    bl_idname = "arc.clear_pioneer_root"
    bl_label = "Clear PioneerGame Folder"

    def execute(self, context):
        context.scene.arc_pioneer_root = ""
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    ARC_OT_SelectHairMI,
    ARC_OT_ImportSinglePSK,
    ARC_OT_ImportOutfitFolder,
    ARC_OT_PickOutfitCSV,
    ARC_OT_ClearOutfitCSV,
    ARC_OT_LoadOutfit,
    ARC_OT_SelectOutfit,
    ARC_OT_ClearOutfitSearch,
    ARC_OT_MergeSelectedArmatures,
    ARC_OT_ApplyOutfitPreset,
    ARC_OT_PickManualSkinsFolder,
    ARC_OT_PickManualOutfitFolder,
    ARC_OT_ClearManualOutfitFolder,
    ARC_OT_ConfirmPSKImport,
    ARC_OT_PickPioneerRoot,
    ARC_OT_ClearPioneerRoot,
)
