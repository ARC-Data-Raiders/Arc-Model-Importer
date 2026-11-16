"""Weapon browser UI and lifecycle, isolated from outfits/accessories."""
import json
from pathlib import Path
import bpy
from bpy.props import BoolProperty,EnumProperty,StringProperty
from . import weapon_assets as assets,weapon_catalog as catalog,weapon_import,weapon_position

_ENUMS={}
_PREVIEWS=None
_GUN_TEXTURES={}
_GUN_PENDING=set()
_GUN_DISPLAY={}
_GUN_FAILED=set()
_ARC_PENDING=set()
_ACTIVE_BATCH=None
_ASCENDED_ENUM_READY=False
_ASCENDED_ENUM_PENDING=False
_LEGACY_ENUMS={}
_PROPS=('arc_weapon_browser_key','arc_weapon_ascended','arc_weapon_include_ascended','arc_weapon_position','arc_weapon_last_assembly','arc_weapon_tier')
# Item QualityLevel 0..3 is shown in game as tier I..IV.
TIERS=(('0','I','Tier I, least worn: almost no wear'),('1','II','Tier II, light wear'),('2','III','Tier III, heavy wear'),('3','IV','Tier IV, most worn: the most wear'))
_TIER_CAPTIONS={'0':'Tier I: least worn','1':'Tier II: light wear','2':'Tier III: heavy wear','3':'Tier IV: most worn'}

def tier_caption(key):
    """Caption line for the chosen tier, shown under the tier row."""
    return _TIER_CAPTIONS.get(str(key),'')

def display_label(key):
    """Clean weapon name for a catalog key, for search pickers."""
    return catalog.display_label(key)

def _targets():
    try:
        from ..Shared import ui_targets
        return ui_targets
    except Exception:
        return None

def browser_key_get(self):
    key=getattr(self,'arc_weapon_key','NONE')
    return next((i for i,item in enumerate(catalog.weapon_enum_items()) if item[0]==key),0)

def browser_key_set(self,value):
    items=catalog.weapon_enum_items()
    # Clear the previous gun's variant while its enum is still valid. Reading
    # it after changing the gun can leave RNA resolving an obsolete value.
    if hasattr(bpy.types.Scene,'arc_weapon_ascended'):self.arc_weapon_ascended='OFF'
    if hasattr(bpy.types.Scene,'arc_weapon_skin'):self.arc_weapon_skin='NONE'
    self.arc_weapon_key=items[value][0] if 0<=value<len(items) else 'NONE'

def ascended_items(self,context):
    scene=self if hasattr(self,'arc_weapon_key') else getattr(context,'scene',None)
    entry=catalog.weapon_by_key(getattr(scene,'arc_weapon_key','NONE'))
    variants=assets.bundled_variants(entry) if entry else []
    key=(getattr(scene,'arc_weapon_key','NONE'),assets._REVISION)
    if key not in _ENUMS:
        items=[('OFF','Standard','The regular weapon, no ascended variant',0)]
        items.extend((v['id'],variant_label(v),'',assets.enum_number(v['id'])) for v in variants)
        _ENUMS[key]=items
    return _ENUMS[key]

def variant_label(variant):
    label=variant['label']
    return 'Ascended' if label.split()[0]=='Default' else label

def variant_changed(self,context):
    wm=getattr(context,'window_manager',None)
    if wm:
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type=='VIEW_3D':area.tag_redraw()

def legacy_ascended_items(self,context):
    # During registration only, decode existing saved selections including the
    # rounded values written by the old UI. Never expose aliases in the menu.
    import hashlib,struct
    items=ascended_items(self,context)
    key=tuple((item[0],item[-1]) for item in items)
    if key not in _LEGACY_ENUMS:
        legacy=list(items)
        numbers={item[-1] for item in items}
        for item in items[1:]:
            old=int(hashlib.sha256(item[0].encode()).hexdigest()[:7],16)+1
            rounded=int(struct.unpack('f',struct.pack('f',old))[0])
            for number in [old,rounded]:
                if number not in numbers:
                    legacy.append((*item[:3],number));numbers.add(number)
        _LEGACY_ENUMS[key]=legacy
    return _LEGACY_ENUMS[key]

def register_ascended_property():
    global _ASCENDED_ENUM_READY,_ASCENDED_ENUM_PENDING
    if _ASCENDED_ENUM_READY and hasattr(bpy.types.Scene,'arc_weapon_ascended'):return
    bpy.types.Scene.arc_weapon_ascended=EnumProperty(name='Ascended Variant',description='Alternate skin of the weapon to import',items=legacy_ascended_items,default=0)
    # addon_utils.enable() runs register() with bpy.data restricted. Defer
    # scene migration until Blender returns to its normal event loop.
    _ASCENDED_ENUM_PENDING=True
    if not bpy.app.timers.is_registered(_finish_ascended_registration):
        bpy.app.timers.register(_finish_ascended_registration,first_interval=0.1)

def _finish_ascended_registration():
    global _ASCENDED_ENUM_READY,_ASCENDED_ENUM_PENDING
    scenes=getattr(bpy.data,'scenes',None)
    if scenes is None:return 0.1
    try:
        saved=[(scene,scene.arc_weapon_ascended or 'OFF') for scene in scenes]
        bpy.types.Scene.arc_weapon_ascended=EnumProperty(name='Ascended Variant',description='Alternate skin of the weapon to import',items=ascended_items,default=0,update=variant_changed)
        for scene,selection in saved:
            valid={item[0] for item in ascended_items(scene,None)}
            scene.arc_weapon_ascended=selection if selection in valid else 'OFF'
        _ASCENDED_ENUM_READY=True
    finally:
        _ASCENDED_ENUM_PENDING=False
    return None

def preview_id(path):
    if path is None:return 0
    from ..Outfit_Accessories import appearance
    return appearance._preview_icon_id_for_path(str(path))

def arc_preview_id(path):
    if path is None:return 0
    from ..Outfit_Accessories import preview_assets, appearance
    original=str(Path(path).resolve())
    path=str(preview_assets.source_preview_path(original))
    key='arc-5x4-512:'+path
    if _PREVIEWS is not None and key in _PREVIEWS:return _PREVIEWS[key].icon_id
    if not Path(path).is_file():return 0
    _ARC_PENDING.add(path)
    if not bpy.app.timers.is_registered(_prepare_arc_previews):
        bpy.app.timers.register(_prepare_arc_previews,first_interval=0.0)
    return appearance._low_resolution_icon_id(original)

def _prepare_arc_previews():
    if getattr(bpy.data,'images',None) is None:return 0.1
    if not _ARC_PENDING:return None
    path=next(iter(_ARC_PENDING))
    _ARC_PENDING.discard(path)
    _load_arc_preview(path)
    from ..Outfit_Accessories import appearance
    appearance._PREVIEW_GENERATION += 1
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type=='VIEW_3D':area.tag_redraw()
    return 0.0 if _ARC_PENDING else None

def _load_arc_preview(path):
    """Cache ARC art as a true 5:4, 512x410 transparent preview."""
    global _PREVIEWS
    if path is None or not Path(path).is_file():return 0
    if _PREVIEWS is None:
        from bpy.utils import previews
        _PREVIEWS=previews.new()
    key='arc-5x4-512:'+str(Path(path).resolve())
    if key in _PREVIEWS:return _PREVIEWS[key].icon_id
    import numpy as np
    preview=_PREVIEWS.load(key,str(path),'IMAGE')
    width,height=preview.image_size
    if not width or not height:return preview.icon_id
    pixels=np.asarray(preview.image_pixels_float[:],dtype=np.float32).reshape(height,width,4)
    ys,xs=np.nonzero(pixels[:,:,3]>0)
    if len(xs):
        left,right=int(xs.min()),int(xs.max())+1
        bottom,top=int(ys.min()),int(ys.max())+1
    else:
        left,bottom,right,top=0,0,width,height
    pixels=pixels[bottom:top,left:right]
    ch,cw=pixels.shape[:2]
    # Center-crop the alpha bounds to 5:4 so there are no letterboxed margins.
    if cw/ch>1.25:
        crop_w=max(1,round(ch*1.25));left=(cw-crop_w)//2;pixels=pixels[:,left:left+crop_w]
    elif cw/ch<1.25:
        crop_h=max(1,round(cw/1.25));bottom=(ch-crop_h)//2;pixels=pixels[bottom:bottom+crop_h,:]
    ch,cw=pixels.shape[:2]
    temp=bpy.data.images.new('.arc_portrait_crop',width=cw,height=ch,alpha=True,float_buffer=True)
    try:
        temp.pixels.foreach_set(np.ascontiguousarray(pixels).ravel())
        temp.scale(512,410)
        resized=np.empty(512*410*4,dtype=np.float32);temp.pixels.foreach_get(resized)
    finally:
        bpy.data.images.remove(temp)
    preview.image_size=(512,410)
    preview.image_pixels_float=resized
    return preview.icon_id


def gun_preview_id(path):
    """Cache an alpha-preserving 5:2 UI icon, capped at 512 by 205 pixels."""
    global _PREVIEWS
    if path is None or not Path(path).is_file():return 0
    if _PREVIEWS is None:
        from bpy.utils import previews
        _PREVIEWS=previews.new()
    key='gun:'+str(Path(path).resolve())
    if key in _PREVIEWS:return _PREVIEWS[key].icon_id
    canvas=_gun_preview_pixels(path)
    preview=_PREVIEWS.load(key,str(path),'IMAGE')
    preview.image_size=(512,205)
    preview.image_pixels_float=canvas.ravel()
    return preview.icon_id


def _gun_preview_pixels(path):
    import numpy as np
    image=bpy.data.images.load(str(path),check_existing=False)
    try:
        sw,sh=image.size
        if not sw or not sh:raise ValueError('Weapon preview has no decoded pixels')
        pixels=np.empty(sw*sh*4,dtype=np.float32);image.pixels.foreach_get(pixels)
        pixels=pixels.reshape(sh,sw,4)
        ys,xs=np.nonzero(pixels[:,:,3]>0)
        if len(xs):
            pad=max(1,round(max(xs.max()-xs.min()+1,ys.max()-ys.min()+1)*.02))
            left=max(0,int(xs.min())-pad);right=min(sw,int(xs.max())+pad+1)
            bottom=max(0,int(ys.min())-pad);top=min(sh,int(ys.max())+pad+1)
            pixels=pixels[bottom:top,left:right]
        ch,cw=pixels.shape[:2]
        factor=min(512/max(cw,1),205/max(ch,1),1.0)
        rw=max(1,round(cw*factor));rh=max(1,round(ch*factor))
        if (rw,rh)!=(cw,ch):
            tmp=bpy.data.images.new('.arc_weapon_icon_resize',width=cw,height=ch,alpha=True,float_buffer=True)
            try:
                tmp.pixels.foreach_set(pixels.ravel());tmp.scale(rw,rh)
                resized=np.empty(rw*rh*4,dtype=np.float32);tmp.pixels.foreach_get(resized)
                pixels=resized.reshape(rh,rw,4)
            finally:bpy.data.images.remove(tmp)
        canvas=np.zeros((205,512,4),dtype=np.float32)
        x=(512-rw)//2;y=(205-rh)//2;canvas[y:y+rh,x:x+rw]=pixels
    finally:bpy.data.images.remove(image)
    return canvas




def _prepare_gun_textures():
    if getattr(bpy.data,'images',None) is None:return 0.1
    for queued in tuple(_GUN_PENDING):
        _GUN_PENDING.discard(queued)
        key=str(Path(queued).resolve())
        if key in _GUN_TEXTURES:continue
        try:
            if not Path(key).is_file():
                _GUN_FAILED.add(key)
                continue
            pixels=_gun_preview_pixels(key).reshape(-1,4)
        except Exception as error:
            _GUN_FAILED.add(key)
            print('Weapon preview:',key,error)
            continue
        import numpy as np
        pixels=np.floor(np.clip(pixels,0,1)*255+.5)/255
        background=np.asarray(bpy.context.preferences.themes[0].user_interface.panel_back[:3],dtype=np.float32)
        alpha=pixels[:,3:4]
        pixels[:,:3]=pixels[:,:3]*alpha+background*(1-alpha)
        pixels[:,3]=1
        image=bpy.data.images.new('.arc_weapon_sidebar_image',width=512,height=205,alpha=True)
        image.pixels.foreach_set(pixels.ravel())
        texture=bpy.data.textures.new('.arc_weapon_sidebar_texture',type='IMAGE')
        texture.image=image;texture.use_preview_alpha=True
        # Blender's texture preview uses square UV coordinates based on its
        # height. Compensate for our fixed 5:2 rectangle instead of tiling.
        texture.extension='EXTEND';texture.crop_max_x=.4
        _GUN_TEXTURES[key]=texture
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type=='VIEW_3D':area.tag_redraw()
    return None

def set_gun_preview_save_handler(enabled=False):
    for handler in tuple(bpy.app.handlers.save_pre):
        if getattr(handler,'__module__','')==__name__ and getattr(handler,'__name__','')=='gun_preview_save_pre':
            bpy.app.handlers.save_pre.remove(handler)


def _registration_ready():
    return (_ASCENDED_ENUM_READY and all(hasattr(bpy.types.Scene,name) for name in _PROPS)
            and all(bpy.types.Operator.bl_rna_get_subclass_py(
                'ARC_OUTFITS_OT_'+cls.bl_idname.split('.')[1]) is not None for cls in CLASSES))

def _repair_registration():
    # RNA cannot be changed inside a Panel.draw callback. Repair on the next
    # event-loop tick after an install/hot reload skipped this module's register.
    register()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type=='VIEW_3D':area.tag_redraw()
    return None

def ensure_registered():
    if _registration_ready():return True
    if not bpy.app.timers.is_registered(_repair_registration):
        bpy.app.timers.register(_repair_registration,first_interval=0.0)
    return False


def has_weapon_selection(context):
    """Cheap check for buttons that edit an imported weapon (selection, else last import)."""
    targets=_targets()
    if targets is not None:return targets.resolve(context,'weapon') is not None
    selected=context.selected_objects
    if not selected:
        o=context.scene.objects.get(getattr(context.scene,'arc_weapon_last_assembly','') or '')
        return bool(o and o.get('arc_weapon_assembly'))
    for o in selected[:64]:
        while o is not None:
            if o.get('arc_weapon_assembly'):return True
            o=o.parent
    return False

def _weapon_poll(cls,context):
    if has_weapon_selection(context):return True
    targets=_targets()
    cls.poll_message_set(targets.poll_message('weapon') if targets else 'Select an imported weapon first')
    return False

def _job_active():
    targets=_targets()
    return bool(targets and targets.job_active())

class ARC_OT_update_weapon_position(bpy.types.Operator):
    """Move the selected weapon to the position chosen above"""
    bl_idname='arc_outfits.update_weapon_position'
    bl_label='Apply Position'
    bl_description='Move the selected weapon to the position chosen above'
    bl_options={'REGISTER','UNDO'}
    poll=classmethod(_weapon_poll)
    def execute(self,context):
        roots=weapon_position.roots_for_selection(context)
        if not roots:self.report({'ERROR'},'Select an imported weapon first');return {'CANCELLED'}
        mode=context.scene.arc_weapon_position
        for root in roots:weapon_position.apply(root,mode)
        missed=[r.name for r in roots if r.get('arc_weapon_position')!=mode]
        if missed:self.report({'WARNING'},'No icon capture data for '+', '.join(missed)+'; left horizontal');return {'FINISHED'}
        self.report({'INFO'},'Updated position of '+roots[0].name);return {'FINISHED'}

class ARC_OT_set_weapon_tier(bpy.types.Operator):
    """Apply the selected tier's wear (CustomPrimitiveData 1) to selected weapon assemblies"""
    bl_idname='arc_outfits.set_weapon_tier'
    bl_label='Apply Wear Tier'
    bl_description='Show the chosen wear tier on the selected weapon'
    bl_options={'REGISTER','UNDO'}
    poll=classmethod(_weapon_poll)
    def execute(self,context):
        return weapon_import.apply_tier(self,context,int(context.scene.arc_weapon_tier))

class ARC_OT_apply_weapon_wear_position(bpy.types.Operator):
    """Apply the chosen wear tier and position to the weapon being edited"""
    bl_idname='arc_outfits.apply_weapon_wear_position'
    bl_label='Apply Wear and Position'
    bl_description='Show the chosen wear tier and move the weapon to the chosen position'
    bl_options={'REGISTER','UNDO'}
    poll=classmethod(_weapon_poll)
    def execute(self,context):
        roots=weapon_position.roots_for_selection(context)
        if not roots:self.report({'ERROR'},'Select an imported weapon first');return {'CANCELLED'}
        level=int(context.scene.arc_weapon_tier)
        mode=context.scene.arc_weapon_position
        for root in roots:
            weapon_import.set_assembly_wear(root,level)
            weapon_position.apply(root,mode)
        tier=weapon_import.TIER_LABELS[max(0,min(3,level))]
        missed=[r.name for r in roots if r.get('arc_weapon_position')!=mode]
        if missed:
            self.report({'WARNING'},'Tier '+tier+' applied. No icon capture data for '+', '.join(missed)+'; left horizontal')
            return {'FINISHED'}
        self.report({'INFO'},'Tier '+tier+' and position applied to '+roots[0].name)
        return {'FINISHED'}

def _redraw_sidebars(context):
    try:
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type=='VIEW_3D':area.tag_redraw()
    except Exception:
        pass

class ARC_OT_add_all_weapons(bpy.types.Operator):
    bl_idname='arc_outfits.add_all_weapons'
    bl_label='Import All Weapons'
    bl_options={'REGISTER','UNDO'}
    _timer=None
    @classmethod
    def poll(cls,context):
        if _ACTIVE_BATCH is None and not _job_active():return True
        cls.poll_message_set('Another import is running')
        return False
    bl_description='Import every weapon in the list, one after another. Takes several minutes; Esc or Cancel stops it'
    def invoke(self,context,event):return context.window_manager.invoke_props_dialog(self,title='Import all %d weapons?'%len(catalog.weapons()),confirm_text='Import All Weapons')
    def draw(self,context):
        col=self.layout.column()
        col.label(text='Imports every weapon. This can take several minutes',icon='QUESTION')
        col.label(text='Press Esc or Cancel to stop')
        col.prop(context.scene,'arc_weapon_include_ascended',text='Include Ascended Variants')
    def _step(self,context):
        entry,variant=self._queue.pop(0)
        name=entry.get('name') or entry.get('key') or '?'
        targets=_targets()
        if targets:targets.job_step(self._total-len(self._queue)-1,name)
        try:
            root=weapon_import.import_weapon(context,entry,variant,skin='')
            self._added.append(root.name)
        except Exception as exc:self._errors.append({'weapon':name,'variant':variant,'error':str(exc)})
        done=len(self._added)+len(self._errors)
        if targets:targets.job_step(done,name)
        context.window_manager.progress_update(done)
    def _finish(self,context,cancelled=False):
        global _ACTIVE_BATCH
        if _ACTIVE_BATCH is not self:return {'CANCELLED'}
        _ACTIVE_BATCH=None
        try:
            if self._timer:context.window_manager.event_timer_remove(self._timer);self._timer=None
            context.window_manager.progress_end()
        finally:
            lines=['Failed: %s%s: %s'%(e['weapon'],'' if e['variant']=='OFF' else ' ('+e['variant']+')',e['error']) for e in self._errors]
            # R5: unattempted items are left, not skipped; job_end keeps the planned total.
            skipped=0
            if cancelled:lines.append('Stopped with %d left'%len(self._queue))
            targets=_targets()
            if targets:
                status='cancelled' if cancelled else ('failed' if self._errors and not self._added else 'completed')
                try:targets.job_end(len(self._added),len(self._errors),skipped,lines,status=status,left=len(self._queue) if cancelled else None)
                except TypeError:targets.job_end(len(self._added),len(self._errors),skipped,lines)
            if self._added and getattr(self,'_snap',None) is not None:
                try:
                    from ..Shared import ui_lists
                    ui_lists.record_import('All Weapons',self._snap)
                except Exception as exc:print('Arc import: history not recorded:',exc)
            _redraw_sidebars(context)
        self.report({'WARNING'} if self._errors or cancelled else {'INFO'},f"Imported {len(self._added)} weapons; {len(self._errors)} failed"+(' (stopped)' if cancelled else ''))
        return {'FINISHED'}
    def execute(self,context):
        global _ACTIVE_BATCH
        if _ACTIVE_BATCH is not None or _job_active():
            targets=_targets()
            self.report({'ERROR'},(targets.job_busy_message() if targets else '') or 'Another import is running');return {'CANCELLED'}
        self._queue=[];self._added=[];self._errors=[];self._cancel=False
        try:
            from ..Shared import ui_lists
            self._snap=ui_lists.snapshot()
        except Exception:self._snap=None
        roots=assets.pioneer_roots(context.scene)
        for entry in catalog.weapons():
            self._queue.append((entry,'OFF'))
            if context.scene.arc_weapon_include_ascended:
                self._queue.extend((entry,v['id']) for v in assets.variants(entry,roots))
        self._total=len(self._queue)
        targets=_targets()
        if targets and not targets.job_begin('weapon','Importing all weapons',self._total,'arc_outfits.cancel_add_all_weapons'):
            self.report({'ERROR'},'Another import is running');return {'CANCELLED'}
        _ACTIVE_BATCH=self
        try:
            context.window_manager.progress_begin(0,self._total)
            if bpy.app.background:
                while self._queue and not self._cancel:self._step(context)
                return self._finish(context,self._cancel)
            self._timer=context.window_manager.event_timer_add(.05,window=context.window)
            context.window_manager.modal_handler_add(self);return {'RUNNING_MODAL'}
        except BaseException:
            self._finish(context,True);raise
    def modal(self,context,event):
        if self._cancel or event.type=='ESC':return self._finish(context,True)
        if event.type=='TIMER' and event.timer is self._timer:
            try:
                if self._queue:self._step(context)
                if not self._queue:return self._finish(context)
            except BaseException:
                self._finish(context,True);raise
            _redraw_sidebars(context)
        return {'PASS_THROUGH'} if event.type!='TIMER' else {'RUNNING_MODAL'}
    def cancel(self,context):self._finish(context,True)

class ARC_OT_cancel_add_all_weapons(bpy.types.Operator):
    bl_idname='arc_outfits.cancel_add_all_weapons'
    bl_label='Cancel'
    bl_description='Stop importing all weapons after the current one'
    bl_options={'INTERNAL'}
    @classmethod
    def poll(cls,context):return _ACTIVE_BATCH is not None
    def execute(self,context):
        if _ACTIVE_BATCH is not None:_ACTIVE_BATCH._cancel=True
        return {'FINISHED'}

CLASSES=(ARC_OT_update_weapon_position,ARC_OT_set_weapon_tier,ARC_OT_apply_weapon_wear_position,ARC_OT_add_all_weapons,ARC_OT_cancel_add_all_weapons)

def register():
    for cls in CLASSES:
        old=bpy.types.Operator.bl_rna_get_subclass_py('ARC_OUTFITS_OT_'+cls.bl_idname.split('.')[1])
        if old is cls:continue
        if old:bpy.utils.unregister_class(old)
        bpy.utils.register_class(cls)
    register_ascended_property()
    set_gun_preview_save_handler(False)
    for name,prop in [('arc_weapon_browser_key',EnumProperty(name='Weapon',description='Weapon to import',items=catalog.weapon_enum_items,get=browser_key_get,set=browser_key_set)),('arc_weapon_include_ascended',BoolProperty(name='Include Ascended Variants',description='Also import every alternate skin. Takes longer',default=False)),('arc_weapon_position',EnumProperty(name='Gun Position',description='How the weapon is placed: flat, on a mock body, or as in its icon',items=weapon_position.POSITIONS,default='HORIZONTAL')),('arc_weapon_last_assembly',StringProperty(options={'HIDDEN'})),('arc_weapon_tier',EnumProperty(name='Weapon Tier',description='How worn the weapon looks',items=TIERS,default='3'))]:
        if not hasattr(bpy.types.Scene,name):setattr(bpy.types.Scene,name,prop)

def unregister():
    global _PREVIEWS,_ASCENDED_ENUM_READY,_ASCENDED_ENUM_PENDING
    _ASCENDED_ENUM_READY=False
    _ASCENDED_ENUM_PENDING=False
    if bpy.app.timers.is_registered(_prepare_gun_textures):
        bpy.app.timers.unregister(_prepare_gun_textures)
    _GUN_PENDING.clear()
    _GUN_DISPLAY.clear()
    _GUN_FAILED.clear()
    if bpy.app.timers.is_registered(_prepare_arc_previews):
        bpy.app.timers.unregister(_prepare_arc_previews)
    _ARC_PENDING.clear()
    if getattr(bpy.data,'textures',None) is not None:
        for texture in _GUN_TEXTURES.values():
            try:image=texture.image
            except ReferenceError:continue
            bpy.data.textures.remove(texture)
            if image is not None and image.users==0:bpy.data.images.remove(image)
    _GUN_TEXTURES.clear()
    if bpy.app.timers.is_registered(_finish_ascended_registration):
        bpy.app.timers.unregister(_finish_ascended_registration)
    set_gun_preview_save_handler(False)
    if bpy.app.timers.is_registered(_repair_registration):
        bpy.app.timers.unregister(_repair_registration)
    if _ACTIVE_BATCH is not None:_ACTIVE_BATCH.cancel(bpy.context)
    for cls in reversed(CLASSES):
        old=bpy.types.Operator.bl_rna_get_subclass_py('ARC_OUTFITS_OT_'+cls.bl_idname.split('.')[1])
        if old:bpy.utils.unregister_class(old)
    for name in _PROPS:
        if hasattr(bpy.types.Scene,name):delattr(bpy.types.Scene,name)
    if _PREVIEWS is not None:
        from bpy.utils import previews
        previews.remove(_PREVIEWS);_PREVIEWS=None
    _ENUMS.clear();assets.invalidate()
