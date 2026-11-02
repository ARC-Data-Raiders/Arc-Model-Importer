"""Check package-only browsing and per-asset controls in a private Blender profile."""
import importlib
from types import SimpleNamespace
from unittest.mock import patch


class Layout:
    def __init__(self, expanded=False):
        self.expanded = expanded
        self.errors = []

    def panel(self, identifier, default_closed=False):
        return self, self if self.expanded or not default_closed else None

    def operator(self, *arguments, **keywords):
        return SimpleNamespace()

    def prop(self, owner, name, **keywords):
        if not name.startswith('['):
            getattr(owner, name)

    def label(self, **keywords):
        text = keywords.get('text', '')
        if keywords.get('icon') == 'ERROR':
            self.errors.append(text)

    def __getattr__(self, name):
        return lambda *arguments, **keywords: self


def check_sidebar(module):
    import bpy
    from contextlib import ExitStack
    modules = {name: importlib.import_module(module.__name__ + '.' + name) for name in (
        'weapon_ui', 'weapon_catalog', 'appearance', 'backpack_catalog', 'animation_catalog',
        'niagara_catalog', 'lighting_catalog', 'lighting_looks', 'animation_import',
        'niagara_fx', 'niagara_playback', 'material_runtime.shader_parity.plans')}
    violations = []

    def forbidden(name):
        def reject(*arguments, **keywords):
            violations.append(name)
            raise AssertionError('Browse crossed action boundary: ' + name)
        return reject

    targets = [(module.utils, 'get_content_dirs'), (module.pak_extract, 'configured_dir'),
               (module.pak_extract, 'resolve_pak_folder'), (module.pak_extract, 'load_pak_index')]
    for name in ('root', 'index', 'defaults', 'find'):
        targets.append((modules['material_runtime.shader_parity.plans'], name))
    with ExitStack() as stack:
        stack.enter_context(patch('os.scandir', forbidden('os.scandir')))
        for owner, name in targets:
            if hasattr(owner, name):
                stack.enter_context(patch.object(owner, name, forbidden(owner.__name__ + '.' + name)))
        for expanded in (False, True):
            layout = Layout(expanded)
            module.ui.ARC_OUTFITS_PT_MainPanel.draw(SimpleNamespace(layout=layout), bpy.context)
            assert not layout.errors, layout.errors
        scene = bpy.context.scene
        modules['weapon_ui']._finish_ascended_registration()
        modules['weapon_ui'].ascended_items(scene, bpy.context)
        modules['weapon_catalog'].skin_enum_items(scene, bpy.context)
        appearance = modules['appearance']
        appearance.list_head_choices()
        appearance.list_hair_choices()
        appearance.list_facial_hair_choices()
        appearance.list_hair_color_choices()
        for kind in ('backpack', 'attachment', 'charm', 'raider_tool'):
            for choice in appearance.list_gear_choices(kind):
                if kind == 'backpack':
                    modules['backpack_catalog'].enum_items(choice)
        animation = modules['animation_catalog']
        animation.catalog_stats('')
        animation.filter_entries('idle', cache_dir='', limit=8)
        effects = modules['niagara_catalog']
        effects.catalog_stats('')
        effects.filter_entries('shield', catalog_root='', limit=10)
    assert not violations, violations
    for name in ('arc_weapon_key', 'arc_anim_search', 'arc_ns_json_path', 'arc_ns_play_ticks'):
        assert bpy.types.Scene.bl_rna.properties[name].description == '', name
    assert not hasattr(bpy.types, 'ARC_PT_shader_parity')
    print('SIDEBAR_BOUNDARY_CHECK_OK', module.__name__)


def check_fx_actions(module):
    import bpy
    catalog = importlib.import_module(module.__name__ + '.niagara_catalog')
    playback = importlib.import_module(module.__name__ + '.niagara_playback')
    operator = module.operators.ARC_OUTFITS_OT_ImportNiagaraFX
    scene = SimpleNamespace(arc_ns_selected='SelectedFX', arc_ns_catalog_root='',
                            arc_ns_json_path=__file__)
    context = SimpleNamespace(scene=scene)
    reports = []
    action = SimpleNamespace(use_selected=True, filepath='',
                             report=lambda level, message: reports.append(message))
    entry = {'key': 'SelectedFX'}
    with patch.object(catalog, 'get_entry', return_value=entry), patch.object(
            catalog, 'resolve_entry_path', return_value=''):
        assert operator.execute(action, context) == {'CANCELLED'}
        assert reports == ['NS json not found: SelectedFX']
        assert playback._selected_ns_path(context) is None
    action.use_selected = False
    action.execute = lambda context: {'FINISHED'}
    with patch.object(catalog, 'resolve_entry_path', side_effect=AssertionError('direct file resolved selection')):
        assert operator.invoke(action, context, None) == {'FINISHED'}
        assert action.filepath == bpy.path.abspath(scene.arc_ns_json_path)
    print('FX_ACTION_BOUNDARY_CHECK_OK', module.__name__)


def check_controls(module):
    import bpy
    core = importlib.import_module(module.__name__ + '.material_runtime.core')
    compiler = importlib.import_module(module.__name__ + '.material_runtime.compiler')
    props = importlib.import_module(module.__name__ + '.material_runtime.shader_parity.props')
    plan = core.MaterialPlan('/Game/Tests/ControlIsolation', 'test', {
        'roughness': core.literal(.5),
        'emission_strength': core.expr('preview_parameter', key='TestGain', default=1., label='Test gain')})
    material = bpy.data.materials.new('Private control check')
    compiler.compile_plan(plan, material, replace=True)
    values = {node.get('dr_preview_parameter'): node for node in material.node_tree.nodes if node.get('dr_preview_parameter')}
    assert 'TestGain' in values and 'dr_roughness_scale' in values
    mesh = bpy.data.meshes.new('Private control mesh')
    mesh.materials.append(material)
    objects = [bpy.data.objects.new(name, mesh) for name in ('Control original', 'Control copy')]
    for obj in objects:
        module.operators._isolate_material_controls(obj)
    materials = [obj.material_slots[0].material for obj in objects]
    original, copy = materials
    try:
        assert original != copy and original != material
        module.operators._isolate_material_controls(objects[0])
        assert objects[0].material_slots[0].material == original
        copy.node_tree.nodes[values['TestGain'].name].outputs[0].default_value = 3.
        assert values['TestGain'].outputs[0].default_value == 1.
        assert original.node_tree.nodes[values['TestGain'].name].outputs[0].default_value == 1.
        assert copy.node_tree.nodes['DR.Material'].node_tree == material.node_tree.nodes['DR.Material'].node_tree
        assert all(node.location.y > material.node_tree.nodes['DR.Material'].location.y for node in values.values())
        with patch.object(module.lighting_looks, 'apply_lighting_look',
                          side_effect=AssertionError('lighting selection changed scene')):
            module.lighting_looks.on_lighting_scenario_update(bpy.context.scene, bpy.context)
        props.activate()
        for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.depsgraph_update_post,
                         bpy.app.handlers.frame_change_post, bpy.app.handlers.render_pre):
            assert not any(getattr(handler, '__module__', '') == props.__name__ for handler in handlers)
    finally:
        group = material.node_tree.nodes['DR.Material'].node_tree
        for obj in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        for isolated in materials:
            bpy.data.materials.remove(isolated)
        bpy.data.materials.remove(material)
        if group.users == 0:
            bpy.data.node_groups.remove(group)
    print('CONTROL_ISOLATION_CHECK_OK', module.__name__)
