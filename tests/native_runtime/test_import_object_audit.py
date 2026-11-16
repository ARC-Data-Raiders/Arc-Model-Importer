import ast
import contextlib
import importlib
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

from .native_test_support import ROOT, PACKAGE, functions_module, namespace, temp_fixture

namespace()
audit = importlib.import_module(PACKAGE + '.Shared.materials.material_audit')


class Mesh(dict):
    type = 'MESH'
    name = 'ImportedMesh'
    dimensions = (1.0, 1.0, 1.0)
    material_slots = []
    data = types.SimpleNamespace(polygons=[], uv_layers=[], color_attributes=[])


class ImportObjectAudit(unittest.TestCase):
    def setUp(self):
        source = ROOT / 'Shared/operators.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        function = next(item for item in tree.body if isinstance(item, ast.FunctionDef)
                        and item.name == 'apply_materials_to_object')
        self.status = 'clothing'
        self.scope = {'__package__': PACKAGE + '.Shared', 'json': json, 'os': os,
                      'bpy': types.SimpleNamespace(data=types.SimpleNamespace(images=[])),
                      'utils': types.SimpleNamespace(timed=lambda label: contextlib.nullcontext()),
                      '_recover_saved_material_lookup': lambda obj, path: path,
                      '_isolate_material_controls': lambda obj: None,
                      '_apply_materials_to_object_impl': lambda *args, **kwargs: self.status}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), self.scope)
        self.apply = self.scope['apply_materials_to_object']

    def test_every_material_route_receives_basic_object_checks(self):
        for route in ('clothing', 'weapon', 'enemy', 'hair', 'unknown'):
            with self.subTest(route=route):
                self.status = route
                obj = Mesh()
                with mock.patch.object(audit, 'mesh_meta_for', return_value={'tris': 3}):
                    self.assertEqual(self.apply(obj, 'mesh.uemodel'), route)
                self.assertIn('arc_audit', obj)
                record = json.loads(obj['arc_audit'])
                self.assertEqual(record['kind'], 'object')
                self.assertIn('tri_count_mismatch', record['flags'])

    def test_existing_route_audit_is_not_overwritten(self):
        obj = Mesh(arc_audit='existing route record')
        with mock.patch.object(audit, 'audit_object') as run:
            self.apply(obj, 'mesh.uemodel')
        run.assert_not_called()
        self.assertEqual(obj['arc_audit'], 'existing route record')

    def test_nonmesh_does_not_receive_mesh_audit(self):
        obj = Mesh()
        obj.type = 'ARMATURE'
        with mock.patch.object(audit, 'audit_object') as run:
            self.apply(obj, 'mesh.uemodel')
        run.assert_not_called()

    def test_failed_material_builder_is_not_hidden(self):
        self.scope['_apply_materials_to_object_impl'] = mock.Mock(side_effect=RuntimeError('build failed'))
        obj = Mesh()
        with self.assertRaisesRegex(RuntimeError, 'build failed'):
            self.apply(obj, 'mesh.uemodel')
        self.assertNotIn('arc_audit', obj)


class SourceSectionAudit(unittest.TestCase):
    def test_source_identity_requires_successful_first_lod_parse(self):
        module = types.ModuleType(PACKAGE + '.Shared.uemodel_import')
        lod = types.SimpleNamespace(materials=[('Bark', '/Game/Bark.Bark', 0, 1)], vertices=[], indices=[0])
        module.parse_uemodel = mock.Mock(return_value=('Tree', 1, [lod]))
        with mock.patch.dict(sys.modules, {module.__name__: module}):
            metadata = audit.mesh_meta_for(Mesh(), 'X:/fixture/Tree.uemodel')
            self.assertEqual(metadata['path'], 'X:/fixture/Tree.uemodel')
            self.assertEqual(metadata['lod_index'], 0)
            self.assertEqual(metadata['section_tris'], [1])
            module.parse_uemodel.side_effect = ValueError('broken uemodel')
            self.assertNotIn('path', audit.mesh_meta_for(Mesh(), 'X:/fixture/Broken.uemodel'))
        audit.drain_swallowed()

    def test_object_records_exact_source_and_imported_sections(self):
        obj = Mesh()
        obj.material_slots = [types.SimpleNamespace(material=types.SimpleNamespace(name='Bark.002', get=lambda key: '/Game/Bark' if key == 'arc_package' else None))]
        obj.data = types.SimpleNamespace(polygons=[types.SimpleNamespace(vertices=[0, 1, 2], material_index=0)])
        metadata = {'path': 'X:/fixture/Tree.uemodel', 'lod_index': 0,
                    'slots': [['Bark', '/Game/Bark.Bark']], 'section_tris': [1], 'tris': 1,
                    'dims': [1.0, 1.0, 1.0]}
        record = audit.audit_object(obj, metadata)
        self.assertEqual(record['source_mesh']['path'], metadata['path'])
        self.assertEqual(record['source_mesh']['imported_section_tris'], [1])
        self.assertEqual(record['source_mesh']['imported_tris'], 1)
        self.assertEqual(record['source_mesh']['imported_materials'], ['/Game/Bark'])
        self.assertEqual(metadata.get('imported_tris'), None)

    def test_unproven_source_does_not_gain_identity(self):
        self.assertNotIn('source_mesh', audit.audit_object(Mesh(), {'tris': 0}))


class Material(dict):
    name = 'BuiltClothing'


class FlatMaterialIdentity(unittest.TestCase):
    def setUp(self):
        self.temp = temp_fixture()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'MI_Exact.json'
        self.path.write_text('{}')
        self.entry = {'Name': 'MI_Exact', 'Package': '/Game/Exact/MI_Exact'}
        self.parser = functions_module('common', ['_parse_flat_mi_json'])
        self.parser.textures = types.SimpleNamespace(
            _fs_abspath=str, material_lineage_signature=lambda path: (),
            load_mi_properties_local=lambda path: {}, _material_ref_path=lambda ref: '',
            _material_source=lambda path: (self.entry, None),
            _is_thin_fmodel_mi_data=lambda data: False,
            _material_lineage=lambda path: {'sources': [], 'unresolved': []})
        self.parser._FLAT_MI_CACHE = {}
        self.parser._norm_path_key = str
        self.parser._flat_named_mi_parameters = lambda props: {
            'textures': {}, 'colours': {}, 'switches': {}, 'scalars': {}}
        self.parser._parent_name_from_object = lambda ref: ''
        for name in ('_apply_bpo_flags', '_enrich_proptrim_inherited_textures',
                     '_enrich_parent_material_textures', '_enrich_weapon_stencil_parent',
                     '_enrich_broken_glass_default_textures'):
            setattr(self.parser, name, lambda *args: None)

    def test_exact_export_package_survives_flat_parse(self):
        for package in ('/Game/Exact/MI_Exact', '/Engine/Exact/MI_Exact', '/Plugin/Exact/MI_Exact'):
            with self.subTest(package=package):
                self.parser._FLAT_MI_CACHE.clear()
                self.entry['Package'] = package
                result = self.parser._parse_flat_mi_json(str(self.path))
                self.assertEqual(result.get('package'), package)

    def test_absent_export_identity_is_not_inferred_from_name(self):
        for entry in (None, {'Name': 'MI_Exact'}, {'Name': 'MI_Exact', 'Package': None}):
            with self.subTest(entry=entry):
                self.parser._FLAT_MI_CACHE.clear()
                self.entry = entry
                self.assertFalse(self.parser._parse_flat_mi_json(str(self.path)).get('package'))

    def test_flat_identity_reaches_imported_section_provenance(self):
        material = Material()
        obj = Mesh()
        obj.material_slots = [types.SimpleNamespace(material=material)]
        metadata = {'path': 'X:/fixture/Mesh.uemodel', 'lod_index': 0,
                    'slots': [['MI_Exact', '/Game/Exact/MI_Exact.MI_Exact']],
                    'section_tris': [0], 'tris': 0}
        parsed = self.parser._parse_flat_mi_json(str(self.path))
        with mock.patch.object(audit, 'graph_facts', return_value={'name': material.name}):
            audit.audit_material(material, parsed)
        record = audit.audit_object(obj, metadata)
        self.assertEqual(record['source_mesh']['imported_materials'], ['/Game/Exact/MI_Exact'])

    def read_exact_export(self, entry):
        self.path.write_text(json.dumps([entry]))
        source = ROOT / 'Shared/textures.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        functions = [item for item in tree.body if isinstance(item, ast.FunctionDef)
                     and item.name in {'_material_source', '_mi_entry_stem', '_material_ref_path'}]
        scope = {'os': os, '_fs_abspath': str, '_MATERIAL_SOURCE_CACHE': {},
                 '_material_file_stamp': lambda path: (path, Path(path).stat().st_mtime_ns),
                 '_load_mi_json_data': lambda path: json.loads(Path(path).read_text()),
                 '_is_thin_fmodel_mi_data': lambda data: False,
                 'utils': types.SimpleNamespace(ue_export_entries=lambda data: data)}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), scope)
        self.parser.textures._material_source = scope['_material_source']
        self.parser.textures._material_ref_path = scope['_material_ref_path']
        return self.parser._parse_flat_mi_json(str(self.path))

    def test_exact_source_reader_preserves_authored_package(self):
        parsed = self.read_exact_export({'Type': 'MaterialInstanceConstant', 'Name': 'MI_Exact',
                                        'Package': '/Game/Exact/MI_Exact', 'Properties': {}})
        self.assertEqual(parsed.get('package'), '/Game/Exact/MI_Exact')

    def test_rejected_export_cannot_supply_parent_package(self):
        parsed = self.read_exact_export({'Type': 'Material', 'Name': 'M_Parent',
                                        'Package': '/Game/Exact/M_Parent', 'Properties': {}})
        self.assertFalse(parsed.get('package'))

    def test_companion_identity_requires_exact_mounted_package(self):
        self.path = Path(self.temp.name) / 'PioneerGame/Content/Exact/MI_Exact.json'
        self.path.parent.mkdir(parents=True)
        companion = self.path.with_suffix('.arc-material.json')
        for package, name, expected in (
                ('/Game/Exact/MI_Exact', 'MI_Exact', '/Game/Exact/MI_Exact'),
                ('/Game/Other/MI_Exact', 'MI_Exact', None),
                ('/Engine/Exact/MI_Exact', 'MI_Exact', None),
                ('/Game/Exact/M_Parent', 'M_Parent', None)):
            with self.subTest(package=package):
                self.parser._FLAT_MI_CACHE.clear()
                companion.write_text(json.dumps([{'Type': 'MaterialInstanceConstant',
                    'Name': name, 'Package': package, 'Properties': {}}]))
                parsed = self.read_exact_export({'Type': 'MaterialInstanceConstant',
                                                'Name': 'MI_Exact', 'Properties': {}})
                self.assertEqual(parsed.get('package'), expected)

    def test_unmounted_companion_identity_stays_unknown(self):
        companion = self.path.with_suffix('.arc-material.json')
        companion.write_text(json.dumps([{'Type': 'MaterialInstanceConstant',
            'Name': 'MI_Exact', 'Package': '/Game/Exact/MI_Exact', 'Properties': {}}]))
        parsed = self.read_exact_export({'Type': 'MaterialInstanceConstant',
                                        'Name': 'MI_Exact', 'Properties': {}})
        self.assertFalse(parsed.get('package'))


class ImportedMaterialAudit(unittest.TestCase):
    def setUp(self):
        self.temp = temp_fixture()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'MI_Clothing.json'
        self.path.write_text('{}')
        self.mat = Material(arc_mi_path=str(self.path))
        self.obj = Mesh()
        self.obj.material_slots = [types.SimpleNamespace(material=self.mat)]
        self.record = {'package': '/Game/MI_Clothing', 'master': '/Game/M_Clothing', 'textures': {}}
        self.parse = mock.Mock(return_value=self.record)
        common = types.ModuleType(PACKAGE + '.Shared.materials.common')
        common._parse_flat_mi_json = self.parse
        self.modules = mock.patch.dict(sys.modules, {common.__name__: common})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.graph = mock.patch.object(audit, 'graph_facts', return_value={'name': self.mat.name, 'output_linked': True})
        self.graph.start()
        self.addCleanup(self.graph.stop)
        self.metadata = mock.patch.object(audit, 'mesh_meta_for', return_value={'tris': 0})
        self.metadata.start()
        self.addCleanup(self.metadata.stop)

    def test_exact_selected_source_is_audited(self):
        with mock.patch.object(audit, 'master_for_flat', return_value=(None, 'lineage unresolved')):
            audit.audit_imported_object(self.obj, 'mesh.uemodel')
        self.parse.assert_called_once_with(str(self.path))
        record = json.loads(self.mat['arc_audit'])
        self.assertEqual(record['package'], '/Game/MI_Clothing')
        self.assertEqual(record['kind'], 'material')
        self.assertIn('arc_audit', self.obj)

    def test_missing_source_remains_unaudited(self):
        self.mat['arc_mi_path'] = str(self.path.with_name('absent.json'))
        audit.audit_imported_object(self.obj, 'mesh.uemodel')
        self.parse.assert_not_called()
        self.assertNotIn('arc_audit', self.mat)

    def test_unstamped_material_remains_unaudited(self):
        del self.mat['arc_mi_path']
        audit.audit_imported_object(self.obj, 'mesh.uemodel')
        self.parse.assert_not_called()
        self.assertNotIn('arc_audit', self.mat)

    def test_existing_material_record_is_preserved(self):
        self.mat['arc_audit'] = 'existing material record'
        audit.audit_imported_object(self.obj, 'mesh.uemodel')
        self.parse.assert_not_called()
        self.assertEqual(self.mat['arc_audit'], 'existing material record')

    def test_parser_failure_stays_visible_without_changing_graph(self):
        self.parse.side_effect = ValueError('invalid exact MI')
        audit.drain_swallowed()
        audit.audit_imported_object(self.obj, 'mesh.uemodel')
        self.assertNotIn('arc_audit', self.mat)
        errors = audit.drain_swallowed()
        self.assertTrue(any('invalid exact MI' in item['error'] for item in errors))
