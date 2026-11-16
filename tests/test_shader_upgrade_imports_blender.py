"""Exercise actual PAK imports and the shader families changed by this task."""
import importlib
import json
import sys
import time
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT.parent))
addon = importlib.import_module(ROOT.name)
addon.register()
api = importlib.import_module(ROOT.name + '.Outfit_Accessories.appearance')
pak = importlib.import_module(ROOT.name + '.Shared.pak_extract')
scene = bpy.context.scene
scene.arc_pak_dir = 'C:/Program Files (x86)/Steam/steamapps/common/Arc Raiders/PioneerGame/Content/Paks'
scene.arc_usmap_path = str(ROOT / 'ArcPak/mappings/ArcRaiders_2.3.0_UE5.7.4_CL1438640.usmap')
scene.arc_import_source = 'PAK'
scene.arc_include_reference = False
CASES = {
    'bulb': 'PioneerGame/Content/Pioneer/Environment/POI/Minor/SupportBuildingSmall_02/SM_SupportBuildingSmall_02_Lamps_01',
    'off_bulb': 'PioneerGame/Content/Pioneer/Environment/Props/_Generic/ConstructionLight_01/SM_ConstructionLight_01_A',
    'glass': 'PioneerGame/Content/Pioneer/Environment/Architecture/BuildingModules/WindowFrames/SM_WindowGlass_01_200cm_WindowSlit_A_Fbx',
    'fur': 'PioneerGame/Content/Pioneer/Characters/Assets/GWRanger/UpperBody/SK_GWRanger_UpperBody',
    'charm': 'PioneerGame/Content/Pioneer/Characters/Backpacks/Charms/Lizzie_01/SM_Lizzie_01_A',
}
reports = []
mode = sys.argv[sys.argv.index('--') + 1] if '--' in sys.argv else 'shader-imports'
requested = mode.removeprefix('shader-imports-').split(',') if mode.startswith('shader-imports-') else list(CASES)
assert set(requested) <= set(CASES), 'Unknown import case'
try:
    for kind, reference in CASES.items():
        if kind not in requested:
            continue
        started = time.perf_counter()
        code, log, meshes = pak.extract_paths(pak.configured_dir(scene), pak.resolve_usmap(scene), [reference])
        assert code == 0 and meshes, kind + ': extraction failed: ' + str(log)[-900:]
        exact = [mesh for mesh in meshes if Path(mesh).stem == reference.rsplit('/', 1)[-1]]
        assert len(exact) == 1, kind + ': expected exactly one requested mesh'
        ok, message, objects = api.import_appearance_mesh(exact[0])
        assert ok and objects, kind + ': import failed: ' + str(message)
        materials = {slot.material for obj in objects for slot in getattr(obj, 'material_slots', ()) if slot.material}
        assert materials, kind + ': no materials'
        assert any(obj.type == 'MESH' and len(obj.data.vertices) for obj in objects), kind + ': empty geometry'
        material_reports = []
        for material in materials:
            assert material.use_nodes and material.node_tree, material.name + ': no graph'
            build = json.loads(material.get('arc_build_report', '{}'))
            material_reports.append({'name': material.name, 'family': build.get('family'), 'warnings': build.get('warnings', []), 'missing': build.get('missing_textures', [])})
        fur_modifiers = [mod for obj in objects for mod in obj.modifiers if mod.name in ('ARC Native Fur Shell Offset', 'ARC Weapon Fur Shell Offset')]
        if kind == 'fur':
            assert fur_modifiers, 'Fur source did not receive its authored offset modifier'
            for modifier in fur_modifiers:
                socket = next(item for item in modifier.node_group.interface.items_tree if item.name == 'Length Scale')
                assert modifier[socket.identifier] == 1., 'Default fur length changed during import'
        families = {entry['family'] for entry in material_reports}
        if kind in ('bulb', 'off_bulb'):
            assert 'lightbulb' in families, str(families)
        if kind == 'glass':
            assert 'translucent_glass' in families, str(families)
        result = {'kind': kind, 'source': reference, 'seconds': time.perf_counter() - started, 'objects': len(objects), 'materials': material_reports, 'fur_modifiers': len(fur_modifiers), 'status': 'PASS'}
        reports.append(result)
        print('SHADER_IMPORT ' + json.dumps(result), flush=True)
finally:
    destination = ROOT / ('temp/shader-upgrade/actual-imports-' + '-'.join(requested) + '.json')
    destination.write_text(json.dumps({'blender': bpy.app.version_string, 'cases': reports}, indent=2) + '\n', encoding='utf-8')
    addon.unregister()
assert len(reports) == len(requested), 'Actual import sweep incomplete'
