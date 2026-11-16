"""One target resolver, import stamps and the batch job state (stub objects, no bpy)."""
import importlib.util
import os
import unittest
from pathlib import Path

ROOT = Path(os.environ.get('DATARAIDERS_TEST_REPOSITORY', Path(__file__).resolve().parents[1])).resolve()


def _load():
    spec = importlib.util.spec_from_file_location('_ui_targets_probe', ROOT / 'Shared/ui_targets.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Obj(dict):
    def __init__(self, name, type='MESH', parent=None, **props):
        super().__init__(props)
        self.name = name
        self.type = type
        self.parent = parent
        self.material_slots = ()
        self.children = []
        if parent is not None:
            parent.children.append(self)

    __hash__ = object.__hash__
    __eq__ = object.__eq__

    @property
    def children_recursive(self):
        out = []
        for child in self.children:
            out.append(child)
            out.extend(child.children_recursive)
        return out


class Objects(list):
    def get(self, name):
        return next((o for o in self if o.name == name), None)


class Scene(dict):
    def __init__(self, objects):
        super().__init__()
        self.name = 'Scene'
        self.objects = Objects(objects)


class Context:
    def __init__(self, scene, active=None, selected=()):
        self.scene = scene
        self.active_object = active
        self.selected_objects = list(selected)


class Targets(unittest.TestCase):
    def setUp(self):
        self.t = _load()
        self.rig = Obj('SK_Raider_Skeleton', 'ARMATURE', arc_assembly_role='Outfit')
        self.body = Obj('Body', parent=self.rig, arc_assembly_role='Outfit')
        self.hair = Obj('Hair_A', parent=self.rig, arc_assembly_role='Hair')
        self.beard = Obj('Beard', parent=self.rig, arc_assembly_role='FacialHair')
        self.pack = Obj('Pack', parent=self.rig, arc_assembly_role='Backpack')
        self.gun = Obj('Gun', 'EMPTY', arc_weapon_assembly=True, arc_assembly_role='Weapon')
        self.barrel = Obj('Barrel', parent=self.gun, arc_weapon_owner='Gun')
        self.enemy = Obj('Wasp', 'ARMATURE')
        self.enemy_mesh = Obj('Wasp_Mesh', parent=self.enemy, arc_codex_enemy_key='wasp', arc_codex_enemy_label='Wasp')
        self.cube = Obj('Cube')
        self.scene = Scene([self.rig, self.body, self.hair, self.beard, self.pack, self.gun, self.barrel,
                            self.enemy, self.enemy_mesh, self.cube])

    def ctx(self, active=None, selected=()):
        return Context(self.scene, active, selected)

    def test_roles_tuple(self):
        self.assertEqual(self.t.ROLES, ("character", "hair", "facial_hair", "backpack", "weapon", "enemy", "effect", "asset"))

    def test_character_from_child_mesh(self):
        hit = self.t.resolve(self.ctx(self.body), 'character')
        self.assertIs(hit.obj, self.rig)
        self.assertEqual(hit.source, 'selection')
        self.assertEqual(hit.name, 'Raider')

    def test_parts_from_character(self):
        c = self.ctx(self.rig)
        self.assertIs(self.t.resolve(c, 'hair').obj, self.hair)
        self.assertIs(self.t.resolve(c, 'facial_hair').obj, self.beard)
        self.assertIs(self.t.resolve(c, 'backpack').obj, self.pack)

    def test_parts_never_fall_back(self):
        for role in ('hair', 'facial_hair', 'backpack', 'effect', 'asset'):
            self.assertIsNone(self.t.resolve(self.ctx(), role), role)

    def test_weapon_from_part_and_fallback(self):
        self.assertIs(self.t.resolve(self.ctx(self.barrel), 'weapon').obj, self.gun)
        self.assertIsNone(self.t.resolve(self.ctx(self.cube), 'weapon'))
        self.scene['arc_weapon_last_assembly'] = 'Gun'
        hit = self.t.resolve(self.ctx(self.cube), 'weapon')
        self.assertIs(hit.obj, self.gun)
        self.assertEqual(hit.source, 'last_import')

    def test_enemy_name_prefers_catalog_label_over_rig_name(self):
        rig = Obj('SK_Bullcrab_01', 'ARMATURE')
        Obj('SK_Bullcrab_01_Mesh', parent=rig, arc_codex_enemy_key='bullcrab', arc_codex_enemy_label='Leaper (Bullcrab)')
        self.assertEqual(self.t.display_name(rig), 'Leaper')

    def test_animation_refusal_names_weapon(self):
        hit = self.t.resolve(self.ctx(self.gun), 'asset')
        self.assertTrue(self.t.animation_refusal(hit))
        self.assertEqual(self.t.animation_refusal(None), '')

    def test_enemy_and_asset(self):
        hit = self.t.resolve(self.ctx(self.enemy), 'enemy')
        self.assertIs(hit.obj, self.enemy)
        self.assertEqual(hit.name, 'Wasp')
        self.assertIs(self.t.resolve(self.ctx(self.barrel), 'asset').obj, self.gun)
        self.assertIs(self.t.resolve(self.ctx(self.rig), 'asset').obj, self.rig)
        self.assertIsNone(self.t.resolve(self.ctx(self.cube), 'asset'))
        self.assertIsNone(self.t.resolve(self.ctx(self.gun), 'enemy'))

    def test_stamp_entries_and_fallback(self):
        first = self.t.new_import_id()
        second = self.t.new_import_id()
        self.assertGreater(second, first)
        self.t.stamp([self.rig, self.body], first, 'character', 'Raider Scout')
        self.t.stamp([self.enemy, self.enemy_mesh], second, 'enemy', 'Wasp')
        entries = self.t.import_entries(self.scene)
        self.assertEqual([e['id'] for e in entries], [second, first])
        self.assertEqual(entries[1]['kind'], 'character')
        self.assertEqual(len(entries[1]['objects']), 2)
        self.assertEqual(self.t.objects_of(self.scene, first), [self.rig, self.body])
        self.assertEqual(self.t.display_name(self.body), 'Raider Scout')
        hit = self.t.resolve(self.ctx(), 'character')
        self.assertIs(hit.obj, self.rig)
        self.assertEqual(hit.source, 'last_import')
        # Something Arc but not a character is selected: no guess.
        self.assertIsNone(self.t.resolve(self.ctx(self.gun), 'character'))

    def test_fallback_scene_scoped(self):
        self.t.stamp([self.enemy], self.t.new_import_id(), 'enemy', 'Wasp')
        other = Scene([self.cube])
        self.assertIsNone(self.t.resolve(Context(other), 'enemy'))

    def test_display_name_cleanup(self):
        self.assertEqual(self.t.display_name(Obj('SK_Bastion_Rig.001')), 'Bastion')
        self.assertEqual(self.t.display_name(Obj('Ferro [WPN_Ferro_01]')), 'Ferro')
        self.assertEqual(self.t.poll_message('weapon'), 'Select an imported weapon first')

    def test_role_for_kind(self):
        self.assertEqual(self.t.role_for_kind('Outfit'), 'character')
        self.assertEqual(self.t.role_for_kind('FacialHair'), 'facial_hair')
        self.assertEqual(self.t.role_for_kind('Charm'), 'backpack')
        self.assertEqual(self.t.role_for_kind('Reference Image'), 'asset')

    def test_job_lifecycle(self):
        t = self.t
        self.assertTrue(t.job_begin('outfit', 'All Outfits', 3, 'arc_outfits.cancel'))
        self.assertFalse(t.job_begin('weapon', 'All Weapons', 2, ''))
        t.job_step(1, 'Scout')
        self.assertEqual(t.job_active(), {'kind': 'outfit', 'label': 'All Outfits', 'done': 1, 'total': 3,
                                          'current': 'Scout', 'cancel_idname': 'arc_outfits.cancel',
                                          'cancellable': True})
        t.job_end(2, 1, 0, ['Failed: scout_b: mesh missing'])
        self.assertIsNone(t.job_active())
        report = t.job_report()
        self.assertEqual((report['imported'], report['failed'], report['failed_keys']), (2, 1, ['scout_b']))
        self.assertIn('2 imported, 1 failed', t.report_text())
        t.job_clear_report()
        self.assertIsNone(t.job_report())

    def test_sol_bug1_shared_user_empty(self):
        group = Obj('Group', 'EMPTY')
        rig = Obj('SK_Scout_Rig', 'ARMATURE', parent=group, arc_assembly_role='Outfit')
        Obj('Scout_Body', parent=rig, arc_assembly_role='Outfit')
        wasp = Obj('Wasp2', 'ARMATURE', parent=group)
        wasp_mesh = Obj('Wasp2_Mesh', parent=wasp, arc_codex_enemy_key='wasp')
        rock = Obj('Rock', parent=group)
        scene = Scene([group, rig, wasp, wasp_mesh, rock] + list(group.children_recursive))
        c = Context(scene, rock)
        for role in ('character', 'enemy', 'asset', 'weapon', 'hair'):
            self.assertIsNone(self.t.resolve(c, role), role)
        self.assertIs(self.t.resolve(Context(scene, wasp_mesh), 'enemy').obj, wasp)
        self.assertIs(self.t.resolve(Context(scene, wasp_mesh), 'asset').obj, wasp)
        self.assertIsNone(self.t.resolve(Context(scene, wasp_mesh), 'character'))
        self.assertNotIn(rock, self.t.members(self.t.resolve(Context(scene, rig), 'character')))

    def test_members_and_held_weapon(self):
        held = Obj('Held', 'EMPTY', parent=self.rig, arc_weapon_assembly=True)
        part = Obj('Held_Part', parent=held, arc_weapon_owner='Held')
        got = self.t.members(self.t.resolve(self.ctx(self.body), 'character'))
        self.assertIn(self.hair, got)
        self.assertNotIn(held, got)
        self.assertNotIn(part, got)
        self.assertNotIn(self.cube, got)
        self.assertEqual(self.t.members(self.t.resolve(self.ctx(part), 'weapon')), [held, part])

    def test_remove_import(self):
        t = self.t
        removed = []
        iid = t.new_import_id()
        t.stamp([self.gun, self.barrel], iid, 'weapon', 'Ferro')
        gen = t.import_generation()
        self.assertEqual(t.remove_summary(self.scene, iid), ('Ferro', 2))
        self.assertEqual(t.remove_import(self.scene, iid, remover=removed.append), (2, 0))
        self.assertEqual(removed, [self.barrel, self.gun])
        self.assertGreater(t.import_generation(), gen)
        self.assertEqual(t.remove_import(self.scene, 'none', remover=removed.append), (0, 0))
        self.assertEqual(t.remove_summary(self.scene, 'none'), ('', 0))

    def test_remove_refuses_shared_collection(self):
        t = self.t

        class Coll:
            def __init__(self, name, children=()):
                self.name = name
                self.children = list(children)

            @property
            def children_recursive(self):
                out = []
                for child in self.children:
                    out.append(child)
                    out.extend(child.children_recursive)
                return out

        shared = Coll('Shared')
        other = Scene([])
        other.name = 'Other'
        other.collection = Coll('Other Master', [shared])
        self.scene.collection = Coll('Master', [shared])
        iid = t.new_import_id()
        t.stamp([self.gun, self.barrel], iid, 'weapon', 'Ferro')
        for obj in (self.gun, self.barrel):
            obj.users_scene = (self.scene, other)
            obj.users_collection = (shared,)
        removed = []
        gen = t.import_generation()
        self.assertEqual(t.remove_refusal(self.scene, iid), t.SHARED_COLLECTION_MESSAGE)
        self.assertEqual(t.remove_import(self.scene, iid, remover=removed.append), (0, 0))
        self.assertEqual(removed, [])
        self.assertEqual(t.import_generation(), gen)
        self.assertEqual(t.last_error()['detail'],
                         'This import is in a collection shared with another scene. '
                         'Make the collection scene-local before deleting')
        # Linked straight into the other scene's own collection: not shared, unlink allowed.
        for obj in (self.gun, self.barrel):
            obj.users_collection = (other.collection,)
        self.assertEqual(t.remove_refusal(self.scene, iid), '')

    def test_job_end_left_is_not_skipped(self):
        t = self.t
        t.job_begin('outfit', 'All Outfits', 40, 'x.cancel')
        t.job_end(12, 0, 3, [], status='cancelled', left=25)
        report = t.job_report()
        self.assertEqual((report['total'], report['skipped'], report['left']), (40, 3, 25))
        self.assertEqual(t.report_title(), 'Stopped: 12 imported, 25 left')
        self.assertIn('25 left', t.report_text())
        t.job_begin('outfit', 'All Outfits', 40, 'x.cancel')
        t.job_end(12, 0, 0, [], status='cancelled')
        self.assertEqual(t.job_report()['left'], 28)

    def test_batch_plan_count(self):
        t = self.t
        calls = []

        def planner(scene):
            calls.append(scene.name)
            return 7
        t.register_batch_planner('WEAPONS', planner)
        self.assertIsNone(t.batch_plan_count(None, 'WEAPONS'))
        self.assertEqual(t.batch_plan_count(self.scene, 'WEAPONS', now=True), 7)
        self.assertEqual(t.batch_plan_count(self.scene, 'WEAPONS'), 7)
        self.assertEqual(len(calls), 1)
        self.scene.arc_pak_dir = 'D:/other'
        self.assertEqual(t.batch_plan_count(self.scene, 'WEAPONS', now=True), 7)
        self.assertEqual(len(calls), 2)
        t.stamp([self.gun], t.new_import_id(), 'weapon', 'Ferro')
        t.batch_plan_count(self.scene, 'WEAPONS', now=True)
        self.assertEqual(len(calls), 3)

        def broken(scene):
            raise OSError('no game folder')
        t.register_batch_planner('ENEMIES', broken)
        self.assertIsNone(t.batch_plan_count(self.scene, 'ENEMIES', now=True))

    def test_asset_fallback_and_text(self):
        t = self.t
        t.stamp([self.rig], t.new_import_id(), 'character', 'Raider Scout')
        t.stamp([self.enemy], t.new_import_id(), 'enemy', 'Wasp')
        hit = t.resolve(self.ctx(), 'asset')
        self.assertIs(hit.obj, self.enemy)
        self.assertEqual(t.editing_text(hit), 'Editing last imported: Wasp')
        self.assertEqual(t.editing_text(t.resolve(self.ctx(self.body), 'character')), 'Editing: Raider Scout')

    def test_part_destination(self):
        t = self.t
        empty = Scene([self.cube])
        plan = t.part_destination(Context(empty), 'ADD')
        self.assertEqual(plan['error'], 'Import an outfit or body first, or switch to New Character')
        self.assertTrue(t.part_destination(Context(empty), 'NEW')['standalone'])
        t.stamp([self.rig], t.new_import_id(), 'character', 'Raider Scout')
        plan = t.part_destination(self.ctx(), 'REPLACE')
        self.assertEqual((plan['armature'], plan['replace'], plan['error']), (self.rig, True, ''))
        self.assertEqual(t.part_destination(self.ctx(), 'ADD')['text'], 'Adds to last imported: Raider Scout')
        self.assertEqual(t.part_button_text('NEW', 'Hair'), 'Import New Hair')
        self.assertEqual(t.part_button_text('ADD', 'Hair'), 'Add Hair to Character')
        self.assertEqual(t.part_button_text('REPLACE', 'Hair'), 'Replace Hair...')

    def test_job_status_titles(self):
        t = self.t
        t.job_begin('outfit', 'All Outfits', 10, '')
        self.assertFalse(t.job_active()['cancellable'])
        self.assertEqual(t.job_busy_message(), 'Wait for All Outfits to finish')
        t.job_end(4, 0, 0, [], status='cancelled')
        self.assertEqual(t.report_title(), 'Stopped: 4 imported, 6 left')
        self.assertEqual(t.job_busy_message(), '')
        t.job_begin('outfit', 'All Outfits', 40, 'x.cancel')
        t.job_end(39, 1, 0, ['Failed: a: b'])
        self.assertEqual(t.report_title(), 'Finished with 1 failure')
        self.assertFalse(t.report_is_error())
        t.job_begin('outfit', 'All Outfits', 2, 'x.cancel')
        t.job_end(0, 2, 0, [], status='failed')
        self.assertTrue(t.report_is_error())
        t.job_begin('outfit', 'All Outfits', 2, 'x.cancel')
        t.job_end(2, 0, 0, [])
        self.assertEqual(t.report_title(), 'Finished')

    def test_last_error(self):
        self.t.set_error('Import Outfit failed', 'mesh missing')
        self.assertEqual(self.t.last_error()['detail'], 'mesh missing')
        self.t.clear_error()
        self.assertIsNone(self.t.last_error())


if __name__ == '__main__':
    unittest.main()
