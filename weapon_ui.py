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
TIERS=(('3','IV',''),('2','III',''),('1','II',''),('0','I',''))

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
        items=[('OFF','Off','',0)]
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
    bpy.types.Scene.arc_weapon_ascended=EnumProperty(name='Ascended Varient',items=legacy_ascended_items,default=0)
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
        bpy.types.Scene.arc_weapon_ascended=EnumProperty(name='Ascended Varient',items=ascended_items,default=0,update=variant_changed)
        for scene,selection in saved:
            valid={item[0] for item in ascended_items(scene,None)}
            scene.arc_weapon_ascended=selection if selection in valid else 'OFF'
        _ASCENDED_ENUM_READY=True
    finally:
        _ASCENDED_ENUM_PENDING=False
    return None

def preview_id(path):
    global _PREVIEWS
    if path is None or not Path(path).is_file():return 0
    if _PREVIEWS is None:
        from bpy.utils import previews
        _PREVIEWS=previews.new()
    key=str(Path(path).resolve())
    if key not in _PREVIEWS:
        preview=_PREVIEWS.load(key,key,'IMAGE')
        _crop_preview(preview)
    return _PREVIEWS[key].icon_id

def _crop_preview(preview):
    # Work on the UI thumbnail only; keep the original game PNG untouched.
    # Alpha bounds preserve even faint authored edges/shadows. Leave a small
    # transparent border so the silhouette does not touch the sidebar edges.
    import numpy as np
    width,height=preview.image_size
    if not width or not height:return
    pixels=np.asarray(preview.image_pixels_float[:],dtype=np.float32).reshape(height,width,4)
    ys,xs=np.nonzero(pixels[:,:,3]>0)
    if not len(xs):return
    pad=max(1,round(max(xs.max()-xs.min()+1,ys.max()-ys.min()+1)*.02))
    left=max(0,int(xs.min())-pad);right=min(width,int(xs.max())+pad+1)
    bottom=max(0,int(ys.min())-pad);top=min(height,int(ys.max())+pad+1)
    if (left,bottom,right,top)==(0,0,width,height):return
    cropped=np.ascontiguousarray(pixels[bottom:top,left:right])
    preview.image_size=(right-left,top-bottom)
    preview.image_pixels_float=cropped.ravel()

def arc_preview_id(path):
    if path is None:return 0
    path=str(Path(path).resolve())
    key='arc-5x4-512:'+path
    if _PREVIEWS is not None and key in _PREVIEWS:return _PREVIEWS[key].icon_id
    if not Path(path).is_file():return 0
    _ARC_PENDING.add(path)
    if not bpy.app.timers.is_registered(_prepare_arc_previews):
        bpy.app.timers.register(_prepare_arc_previews,first_interval=0.0)
    return 0

def _prepare_arc_previews():
    if getattr(bpy.data,'images',None) is None:return 0.1
    if not _ARC_PENDING:return None
    path=next(iter(_ARC_PENDING))
    _ARC_PENDING.discard(path)
    _load_arc_preview(path)
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

def draw_arc_preview(layout,context,path):
    icon=arc_preview_id(path)
    if not icon:return
    prefs=context.preferences.system
    region=context.region
    width=max(64,(region.width if region else 320)-48)
    scale=max(1,width/(20*max(prefs.ui_scale,.5)))
    row=layout.row();row.alignment='CENTER';row.template_icon(icon_value=icon,scale=scale)

def gun_preview_id(path):
    """Cache an alpha-preserving 5:2 UI icon, capped at 512 by 205 pixels."""
    global _PREVIEWS
    if path is None or not Path(path).is_file():return 0
    if _PREVIEWS is None:
        from bpy.utils import previews
        _PREVIEWS=previews.new()
    key='gun:'+str(Path(path).resolve())
    if key in _PREVIEWS:return _PREVIEWS[key].icon_id
    import numpy as np
    image=bpy.data.images.load(str(path),check_existing=False)
    try:
        sw,sh=image.size
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
            tmp.pixels.foreach_set(pixels.ravel());tmp.scale(rw,rh)
            resized=np.empty(rw*rh*4,dtype=np.float32);tmp.pixels.foreach_get(resized)
            bpy.data.images.remove(tmp);pixels=resized.reshape(rh,rw,4)
        canvas=np.zeros((205,512,4),dtype=np.float32)
        x=(512-rw)//2;y=(205-rh)//2;canvas[y:y+rh,x:x+rw]=pixels
    finally:bpy.data.images.remove(image)
    preview=_PREVIEWS.load(key,str(path),'IMAGE')
    preview.image_size=(512,205)
    preview.image_pixels_float=canvas.ravel()
    return preview.icon_id


def draw_preview(layout,context,path,width_fraction=1.0):
    icon=preview_id(path)
    if icon:
        prefs=context.preferences.system
        # template_icon respects the source aspect within its image area.
        # Scale with the actual sidebar content width, including UI zoom/DPI.
        region=context.region
        width=max(64,(region.width if region else 320)-48)
        scale=max(1,width*width_fraction/(20*max(prefs.ui_scale,.5)))
        row=layout.row();row.alignment='CENTER';row.template_icon(icon_value=icon,scale=scale)

def draw_gun_preview(layout,context,path):
    region=context.region
    display=region.as_pointer() if region else 0
    key=str(Path(path).resolve()) if path is not None else None
    texture=_GUN_TEXTURES.get(key)
    if texture is not None:
        try:texture.name
        except ReferenceError:
            _GUN_TEXTURES.pop(key,None);texture=None
    if texture is None and key is not None and key not in _GUN_FAILED:
        _GUN_PENDING.add(key)
        if not bpy.app.timers.is_registered(_prepare_gun_textures):
            bpy.app.timers.register(_prepare_gun_textures,first_interval=0.0)
        texture=_GUN_TEXTURES.get(_GUN_DISPLAY.get(display))
        if texture is not None:
            try:texture.name
            except ReferenceError:texture=None
    elif texture is not None:
        _GUN_DISPLAY[display]=key
    else:
        _GUN_DISPLAY.pop(display,None)
    # template_icon fits into a square even when its row is rectangular. A
    # native texture preview fills the row; keep its image stable on resize.
    width=max(64,(region.width if region else 320)-48)
    row=layout.row()
    row.use_property_split=False;row.use_property_decorate=False
    # The preview is display-only. Disable Blender's built-in resize grip.
    row.enabled=False
    if texture is None:
        row.scale_y=width*.4/(20*max(context.preferences.system.ui_scale,.5))
        row.label(text='')
        return
    row.scale_y=width*.4/(20*7.6*max(context.preferences.system.ui_scale,.5))
    row.template_preview(texture,show_buttons=False,preview_id='arc_weapon_gun_fixed_5x2')

def _prepare_gun_textures():
    if getattr(bpy.data,'images',None) is None:return 0.1
    for queued in tuple(_GUN_PENDING):
        _GUN_PENDING.discard(queued)
        key=str(Path(queued).resolve())
        if key in _GUN_TEXTURES:continue
        try:
            gun_preview_id(key)
            preview=_PREVIEWS.get('gun:'+key) if _PREVIEWS is not None else None
            if preview is None:
                _GUN_FAILED.add(key)
                continue
        except Exception as error:
            _GUN_FAILED.add(key)
            print('Weapon preview:',key,error)
            continue
        import numpy as np
        pixels=np.asarray(preview.image_pixels_float[:],dtype=np.float32).reshape(-1,4)
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

def _section(layout,id,label):
    header,body=layout.panel(id,default_closed=True);header.label(text=label);return body

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

def draw(layout,context):
    scene=context.scene
    if not ensure_registered():
        # Keep the original selector usable while deferred registration runs.
        layout.prop(scene,'arc_weapon_key',text='Gun')
        return
    layout.prop(scene,'arc_weapon_browser_key',text='Gun')
    entry=catalog.weapon_by_key(getattr(scene,'arc_weapon_key','NONE'))
    variant=getattr(scene,'arc_weapon_ascended','OFF') or 'OFF'
    levels=assets.bundled().get('weapons',{}).get(entry['stem'],{}).get('quality_levels',[0,1,2,3]) if entry else [0,1,2,3]
    tier=str(levels[0]) if variant=='OFF' and len(levels)==1 else getattr(scene,'arc_weapon_tier','3')
    if entry:draw_gun_preview(layout,context,assets.bundled_preview(entry,variant,tier=tier,stencil=getattr(scene,'arc_weapon_skin','NONE')))
    layout.operator('arc_outfits.import_weapon',text='Import Weapon',icon='IMPORT')
    layout.label(text='Gun Position')
    layout.prop(scene,'arc_weapon_position',text='')
    layout.label(text='Tier (wear)')
    row=layout.row(align=True);row.enabled=len(levels)>1;row.prop(scene,'arc_weapon_tier',expand=True)
    layout.operator('arc_outfits.set_weapon_tier',text='Set Tier on Selection',icon='MOD_NOISE')
    layout.operator('arc_outfits.update_weapon_position',text='Update Position',icon='CON_LOCLIKE')
    layout.operator('arc_outfits.add_all_weapons',text='Add All Weapons',icon='DUPLICATE')
    layout.prop(scene,'arc_weapon_include_ascended',text='Include Ascended Varients')
    layout.separator()
    layout.label(text='Ascended Varient')
    layout.prop(scene,'arc_weapon_ascended',text='')
    mods=_section(layout,'arc_weapon_mods_v2','Mods')
    if mods is not None:
        for kind,prop in [('Muzzle','arc_weapon_mod_muzzle'),('Stock','arc_weapon_mod_stock'),('Magazine','arc_weapon_mod_magazine'),('UnderBarrel','arc_weapon_mod_underbarrel')]:
            row=mods.row(align=True);row.prop(scene,prop,text='');op=row.operator('arc_outfits.import_weapon_mod',text='',icon='IMPORT');op.mod_type=kind
            mod=catalog.mod_by_key(getattr(scene,prop,'NONE'))
            if mod:draw_preview(mods,context,assets.bundled_preview(mod,mod=True),width_fraction=.5)
    skin=_section(layout,'arc_weapon_skin_v2','Skin')
    if skin is not None:
        skin.prop(scene,'arc_weapon_skin',text='Colourway')
        row=skin.row(align=True);row.operator('arc_outfits.apply_weapon_skin',text='Apply Skin',icon='CHECKMARK');row.operator('arc_outfits.clear_weapon_skin',text='Base',icon='X')

class ARC_OT_update_weapon_position(bpy.types.Operator):
    bl_idname='arc_outfits.update_weapon_position'
    bl_label='Update Position'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        roots=weapon_position.roots_for_selection(context)
        if not roots:self.report({'ERROR'},'Select an imported weapon assembly');return {'CANCELLED'}
        for root in roots:weapon_position.apply(root,context.scene.arc_weapon_position)
        self.report({'INFO'},'Updated '+str(len(roots))+' weapon position(s)');return {'FINISHED'}

class ARC_OT_set_weapon_tier(bpy.types.Operator):
    """Apply the selected tier's wear (CustomPrimitiveData 1) to selected weapon assemblies"""
    bl_idname='arc_outfits.set_weapon_tier'
    bl_label='Set Weapon Tier'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        return weapon_import.apply_tier(self,context,int(context.scene.arc_weapon_tier))

class ARC_OT_add_all_weapons(bpy.types.Operator):
    bl_idname='arc_outfits.add_all_weapons'
    bl_label='Add All Weapons'
    bl_options={'REGISTER','UNDO'}
    _timer=None
    @classmethod
    def poll(cls,context):return _ACTIVE_BATCH is None
    def _step(self,context):
        entry,variant=self._queue.pop(0)
        try:
            root=weapon_import.import_weapon(context,entry,variant,skin='')
            self._added.append(root.name)
        except Exception as exc:self._errors.append({'weapon':entry['name'],'variant':variant,'error':str(exc)})
        context.window_manager.progress_update(len(self._added)+len(self._errors))
    def _finish(self,context,cancelled=False):
        global _ACTIVE_BATCH
        _ACTIVE_BATCH=None
        if self._timer:context.window_manager.event_timer_remove(self._timer);self._timer=None
        context.window_manager.progress_end()
        report={'added':self._added,'errors':self._errors,'cancelled':cancelled}
        context.scene['arc_weapon_batch_report']=json.dumps(report)
        text=bpy.data.texts.get('Weapon Batch Report') or bpy.data.texts.new('Weapon Batch Report');text.clear();text.write(json.dumps(report,indent=2))
        self.report({'WARNING'} if self._errors else {'INFO'},f"Added {len(self._added)} weapon assemblies; {len(self._errors)} unavailable"+(' (stopped)' if cancelled else ''))
        return {'FINISHED'}
    def execute(self,context):
        global _ACTIVE_BATCH
        _ACTIVE_BATCH=self
        self._queue=[];self._added=[];self._errors=[]
        roots=assets.pioneer_roots(context.scene)
        for entry in catalog.weapons():
            self._queue.append((entry,'OFF'))
            if context.scene.arc_weapon_include_ascended:
                self._queue.extend((entry,v['id']) for v in assets.variants(entry,roots))
        context.window_manager.progress_begin(0,len(self._queue))
        if bpy.app.background:
            while self._queue:self._step(context)
            return self._finish(context)
        self._timer=context.window_manager.event_timer_add(.05,window=context.window)
        context.window_manager.modal_handler_add(self);return {'RUNNING_MODAL'}
    def modal(self,context,event):
        if event.type=='ESC':return self._finish(context,True)
        if event.type=='TIMER' and event.timer==self._timer:
            if self._queue:self._step(context)
            if not self._queue:return self._finish(context)
            if context.area:context.area.tag_redraw()
        return {'RUNNING_MODAL'}
    def cancel(self,context):self._finish(context,True)

CLASSES=(ARC_OT_update_weapon_position,ARC_OT_set_weapon_tier,ARC_OT_add_all_weapons)

def register():
    for cls in CLASSES:
        old=bpy.types.Operator.bl_rna_get_subclass_py('ARC_OUTFITS_OT_'+cls.bl_idname.split('.')[1])
        if old is cls:continue
        if old:bpy.utils.unregister_class(old)
        bpy.utils.register_class(cls)
    register_ascended_property()
    set_gun_preview_save_handler(False)
    for name,prop in [('arc_weapon_browser_key',EnumProperty(name='Weapon',items=catalog.weapon_enum_items,get=browser_key_get,set=browser_key_set)),('arc_weapon_include_ascended',BoolProperty(name='Include Ascended Varients',default=False)),('arc_weapon_position',EnumProperty(name='Gun Position',items=weapon_position.POSITIONS,default='HORIZONTAL')),('arc_weapon_last_assembly',StringProperty(options={'HIDDEN'})),('arc_weapon_tier',EnumProperty(name='Weapon Tier',items=TIERS,default='3'))]:
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
