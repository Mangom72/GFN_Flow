"""Type migration, grouped native UI, first-F9 isolation and private credentials."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from auto_macro import Picker
from detection import Sessions, login
from macro_engine import MacroRunner
from macro_library import MacroLibrary, new_macro, validate
from macro_manager import Manager


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'library.json'

    def macro(self, kind, name):
        macro = new_macro(kind)
        macro.update(name=name, game='Demo', title_re='Demo')
        return macro

    def test_v1_migrates_explicit_types_and_trash_without_name_inference(self):
        normal = self.macro('normal', 'Login password helper')
        normal['kind'] = 'sequence'
        normal['hidden'] = True
        login_macro = self.macro('login', 'Wait and click')
        login_macro['auto'] = True
        deleted = copy.deepcopy(normal)
        deleted['id'] = 'deleted'
        original = dict(version=1, macros=[normal, login_macro], trash=[deleted])
        self.path.write_text(json.dumps(original))
        library = MacroLibrary(self.path)
        self.assertEqual(library.data['version'], 2)
        self.assertEqual([m['kind'] for m in library.macros], ['normal', 'login'])
        self.assertEqual(library.data['trash'][0]['kind'], 'normal')
        expected = copy.deepcopy(original)
        expected['version'] = 2
        expected['macros'][0]['kind'] = expected['trash'][0]['kind'] = 'normal'
        expected['games'] = [dict(name='Demo', title_re='Demo')]
        self.assertEqual(library.data, expected)
        self.assertEqual(json.loads(self.path.with_suffix('.v1.bak').read_text()), original)
        self.assertEqual(self.path.with_suffix('.v1.bak').stat().st_mode & 0o777, 0o600)
        self.assertEqual(MacroLibrary(self.path).data, expected)

    def test_invalid_migration_preserves_original_file(self):
        macro = self.macro('normal', 'Login')
        macro.pop('kind')
        contents = json.dumps(dict(version=1, macros=[macro], trash=[]))
        self.path.write_text(contents)
        with self.assertRaises(ValueError):
            MacroLibrary(self.path)
        self.assertEqual(self.path.read_text(), contents)
        self.assertFalse(self.path.with_suffix('.v1.bak').exists())

    def test_old_import_new_export_and_duplication_keep_types(self):
        library = MacroLibrary(self.path)
        normal = self.macro('normal', 'Login')
        normal['kind'] = 'sequence'
        login_macro = self.macro('login', 'Manual name')
        login_macro.update(auto=True, user='private-account', account_key='private-account-key')
        incoming = self.path.with_name('incoming.json')
        incoming.write_text(json.dumps(dict(version=1, macros=[normal, login_macro])))
        self.assertEqual(library.import_file(incoming), 2)
        self.assertEqual([m['kind'] for m in library.macros], ['normal', 'login'])
        self.assertTrue(all(not m['auto'] for m in library.macros))
        self.assertTrue(all(m['id'] not in (normal['id'], login_macro['id']) for m in library.macros))
        for macro in library.macros[:]:
            self.assertEqual(library.duplicate(macro['id'])['kind'], macro['kind'])
        exported = self.path.with_name('export.json')
        with patch.object(login, 'get_secret') as get_secret:
            library.export_file(exported)
            get_secret.assert_not_called()
        self.assertNotIn('private-account', exported.read_text())
        other = MacroLibrary(self.path.with_name('other.json'))
        self.assertEqual(other.import_file(exported), 4)
        self.assertEqual([m['kind'] for m in other.macros], ['normal', 'login', 'normal', 'login'])

    def test_normal_auto_is_rejected_and_unknown_types_are_rejected(self):
        macro = self.macro('normal', 'Auto login')
        macro['auto'] = True
        with self.assertRaises(ValueError):
            validate(macro)
        macro.update(kind='sequence', auto=False)
        with self.assertRaises(ValueError):
            validate(macro)
        self.assertEqual(new_macro('sequence')['kind'], 'normal')


class TypeUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profile = login.Profile('Demo', 'Demo', 'old-account', 'legacy-password', 1920, 1080,
                                     ['id', 'tab', 'pw', 'enter'])
        self.library = MacroLibrary(Path(self.tmp.name) / 'library.json', [self.profile])
        self.secrets = {'legacy-password': 'old-password'}
        for name, effect in [('store_secret', self.secrets.__setitem__), ('get_secret', self.secrets.__getitem__)]:
            mock = patch.object(login, name, side_effect=effect)
            mock.start()
            self.addCleanup(mock.stop)
        with patch.object(login, 'load_profiles', return_value=({}, [self.profile])):
            self.picker = Picker(preview=True, library=self.library)
        self.picker.sessions = Sessions()
        self.picker.persist = lambda: None
        self.picker.scan = lambda: None
        self.active = login.Window('game', 'Demo', 'GeForceNOW', 0, 0, 1920, 1080)
        self.picker.sessions.update([self.active], self.picker.profiles)
        self.picker.game = (self.active, self.profile)
        self.manager = Manager(self.picker, offscreen=True, width=360)
        self.manager.show()
        self.addCleanup(self.picker.window.destroy)
        self.addCleanup(self.manager.window.destroy)

    def add_normal(self, name='Confirm menu'):
        macro = new_macro('normal')
        macro.update(name=name, game='Demo', title_re='Demo', steps=['key:Enter'])
        return self.library.put(macro)

    def test_groups_collapse_search_and_selection_context(self):
        self.add_normal()
        self.manager.refresh()
        group_path = self.picker.Gtk.TreePath.new_from_string('0')
        self.assertEqual([row[1] for row in self.manager.rows], ['Login Macros', 'Normal Macros'])
        self.assertEqual([len(list(row.iterchildren())) for row in self.manager.rows], [1, 1])
        self.manager.tree.get_selection().select_path('0')
        self.assertFalse(self.manager.context.get_visible())
        self.manager.tree.get_selection().select_path('0:0')
        self.assertTrue(self.manager.context.get_visible())
        self.manager.refresh()
        self.assertTrue(self.manager.context.get_visible())
        selected_model, selected = self.manager.tree.get_selection().get_selected()
        self.assertEqual(selected_model[selected][0], self.library.macros[0]['id'])
        self.manager.tree.collapse_row(group_path)
        self.assertFalse(self.manager.context.get_visible())
        self.manager.refresh()
        self.assertFalse(self.manager.tree.row_expanded(group_path))
        self.manager.search.set_text('Demo')
        self.manager.refresh()
        self.assertTrue(self.manager.tree.row_expanded(group_path))
        self.assertEqual(self.manager.shown_count, 2)
        self.manager.tree.get_selection().select_path('0:0')
        self.manager.search.set_text('')
        self.manager.refresh()
        self.assertFalse(self.manager.context.get_visible())
        self.manager.search.set_text('Confirm')
        self.manager.refresh()
        self.assertEqual(self.manager.shown_count, 1)
        self.assertEqual([row[2] for row in self.manager.rows[0].iterchildren()], ['empty'])
        self.manager.search.set_text('')
        self.manager.refresh()
        self.assertFalse(self.manager.tree.row_expanded(group_path))

    def test_picker_groups_single_selection_and_collapse_survive_refresh(self):
        self.add_normal()
        self.picker.render()
        self.assertEqual(set(self.picker.quick_sections), {'login', 'normal'})
        login_rows, normal_rows = self.picker.quick_lists.values()
        login_rows.select_row(login_rows.get_row_at_index(0))
        normal_rows.select_row(normal_rows.get_row_at_index(0))
        self.assertIsNone(login_rows.get_selected_row())
        self.assertEqual(self.picker.quick_selected.macro['kind'], 'normal')
        self.picker.quick_sections['normal'].set_expanded(False)
        self.assertIsNone(self.picker.quick_selected)
        self.picker.render()
        self.assertFalse(self.picker.quick_sections['normal'].get_expanded())

    def test_edit_login_to_normal_preserves_actions_and_disarms_first_f9(self):
        original = copy.deepcopy(self.library.macros[0])
        self.manager.edit(original)
        self.manager.fields['name'].set_text('Changed without losing actions')
        self.manager.kind.set_active_id('normal')
        self.assertFalse(self.manager.auto.get_active())
        self.assertFalse(self.manager.auto.get_visible())
        self.manager.save_draft()
        saved = self.library.get(original['id'])
        self.assertEqual(saved['kind'], 'normal')
        self.assertEqual(saved['steps'], original['steps'])
        self.assertEqual(saved['login_limit'], 0)
        self.assertEqual(saved['name'], 'Changed without losing actions')
        with patch.object(login, 'active_window', return_value=self.active), patch.object(self.picker, 'run_login') as run:
            self.picker.open()
            run.assert_not_called()
        self.assertFalse(self.picker.sessions.attempted)
        self.manager.edit(saved)
        self.manager.kind.set_active_id('login')
        self.assertTrue(self.manager.auto.get_visible())
        self.assertFalse(self.manager.auto.get_active())
        self.manager.auto.set_active(True)
        self.manager.save_draft()
        self.assertEqual(self.library.auto_for('Demo')['id'], original['id'])
        with patch.object(login, 'active_window', return_value=self.active), patch.object(self.picker, 'run_login') as run:
            self.picker.open()
            self.picker.open()
            run.assert_called_once_with(full=False)

    def test_create_normal_then_change_to_login_without_losing_fields(self):
        self.manager.edit(new_macro('normal'))
        self.manager.fields['name'].set_text('First login')
        self.manager.fields['game'].set_text('Demo')
        self.manager.fields['title_re'].set_text('Demo')
        self.manager.kind.set_active_id('login')
        self.manager.fields['user'].set_text('new-account')
        self.manager.fields['password'].set_text('new-password')
        self.manager.save_draft()
        saved = self.library.macros[-1]
        self.assertEqual(saved['kind'], 'login')
        self.assertEqual(saved['name'], 'First login')
        self.assertFalse(saved['auto'])
        self.assertEqual(self.library.resolved_profile(saved, []).user, 'new-account')
        self.assertEqual(self.secrets[saved['credential_key']], 'new-password')

    def test_normal_cannot_auto_run_even_if_flag_is_stale(self):
        normal = self.add_normal('Login Macro')
        self.library.hide(self.library.macros[0]['id'])
        normal['auto'] = True  # Defensive gate also checks type, not just the flag.
        with patch.object(login, 'active_window', return_value=self.active), patch.object(self.picker, 'run_login') as run:
            self.picker.open()
            run.assert_not_called()
        self.assertFalse(self.picker.sessions.attempted)

    def test_account_and_password_are_editable_masked_and_not_serialized(self):
        original = self.library.macros[0]
        self.manager.edit(original)
        password = self.manager.fields['password']
        self.assertFalse(password.get_visibility())
        self.assertEqual(password.get_text(), '')
        self.manager.fields['user'].set_text('private-new-account')
        password.set_text('private-new-password')
        self.manager.save_draft()
        saved = self.library.get(original['id'])
        self.assertEqual(saved['source_profile'], '')
        self.assertEqual(saved['user'], '')
        self.assertEqual(self.secrets[saved['account_key']], 'private-new-account')
        self.assertEqual(self.secrets[saved['credential_key']], 'private-new-password')
        self.assertEqual(self.secrets['legacy-password'], 'old-password')
        self.assertEqual(self.profile.user, 'old-account')
        self.assertNotIn('private-new-account', self.library.path.read_text())
        self.assertNotIn('private-new-password', self.library.path.read_text())
        self.assertEqual(password.get_text(), '')
        self.manager.edit(saved)
        self.assertEqual(self.manager.fields['user'].get_text(), 'private-new-account')
        self.assertEqual(self.manager.fields['password'].get_text(), '')

    def test_blank_password_preserves_reference_without_retrieving_password(self):
        original = self.library.macros[0]
        self.manager.edit(original)
        self.manager.fields['user'].set_text('updated-account')
        with patch.object(login, 'get_secret') as read:
            self.manager.save_draft()
            read.assert_not_called()
        saved = self.library.get(original['id'])
        self.assertEqual(saved['credential_key'], 'legacy-password')
        self.assertEqual(self.secrets['legacy-password'], 'old-password')
        self.assertEqual(self.secrets[saved['account_key']], 'updated-account')

    def test_changing_original_credentials_does_not_change_duplicate(self):
        self.manager.edit(self.library.macros[0])
        self.manager.fields['password'].set_text('first-password')
        self.manager.save_draft()
        original = copy.deepcopy(self.library.macros[0])
        duplicate = self.library.duplicate(original['id'])
        self.manager.edit(original)
        self.manager.fields['user'].set_text('second-account')
        self.manager.fields['password'].set_text('second-password')
        self.manager.save_draft()
        self.assertEqual(self.library.resolved_profile(duplicate, []).user, 'old-account')
        self.assertEqual(self.secrets[duplicate['credential_key']], 'first-password')
        self.manager.edit(duplicate)
        self.manager.fields['user'].set_text('duplicate-account')
        self.manager.fields['password'].set_text('duplicate-password')
        self.manager.save_draft()
        current = self.library.get(original['id'])
        self.assertEqual(self.library.resolved_profile(current, []).user, 'second-account')
        self.assertEqual(self.secrets[current['credential_key']], 'second-password')

    def test_locked_keyring_does_not_modify_library_or_clear_password(self):
        original = copy.deepcopy(self.library.macros[0])
        before = self.library.path.read_bytes()
        self.manager.edit(original)
        self.manager.fields['password'].set_text('unsaved-password')
        with patch.object(login, 'store_secret', side_effect=login.LoginError('Keyring is locked')):
            self.manager.safe(self.manager.save_draft)
        self.assertEqual(self.library.get(original['id']), original)
        self.assertEqual(self.library.path.read_bytes(), before)
        self.assertEqual(self.manager.fields['password'].get_text(), 'unsaved-password')
        self.assertNotIn('unsaved-password', self.manager.notice.get_text())

    def test_normal_manual_playback_keeps_credential_actions_and_ignores_login_cutoff(self):
        self.manager.edit(self.library.macros[0])
        self.manager.kind.set_active_id('normal')
        self.manager.save_draft()
        macro = copy.deepcopy(self.library.macros[0])
        macro.update(login_limit=1, step_delay=0)
        runner = MacroRunner(self.library, lambda: self.active)
        with patch.object(login, 'type_text') as typed, patch.object(login, 'clear_field'), patch.object(login, 'run'):
            runner.run(macro, [], {})
        self.assertEqual(runner.completed, 4)
        self.assertEqual([call.args[0] for call in typed.call_args_list], ['old-account', 'old-password'])

    def test_normal_without_credential_actions_does_not_require_unlocked_keyring(self):
        macro = self.add_normal()
        macro['account_key'] = 'unused-account'
        with patch.object(login, 'get_secret', side_effect=login.LoginError('Locked')) as secret, patch.object(login, 'run') as run:
            MacroRunner(self.library, lambda: self.active).run(macro, [], {})
        secret.assert_not_called()
        run.assert_called_once()

    def test_missing_saved_account_can_be_replaced_in_editor(self):
        self.manager.edit(self.library.macros[0])
        self.manager.save_draft()
        macro = copy.deepcopy(self.library.macros[0])
        with patch.object(login, 'get_secret', side_effect=login.LoginError('Account missing')):
            self.manager.edit(macro)
        self.assertEqual(self.manager.fields['user'].get_text(), '')
        self.assertIn('replacement account', self.manager.notice.get_text())
        self.manager.fields['user'].set_text('replacement-account')
        self.manager.save_draft()
        self.assertEqual(self.library.resolved_profile(self.library.get(macro['id']), []).user, 'replacement-account')

    def test_login_cutoff_does_not_read_an_unused_account(self):
        macro = new_macro('login')
        macro.update(name='Limited login', game='Demo', title_re='Demo', steps=['key:Enter', 'id'],
                     login_limit=1, account_key='missing-account', step_delay=0)
        with patch.object(login, 'get_secret', side_effect=login.LoginError('Missing')) as secret, patch.object(login, 'run') as run:
            MacroRunner(self.library, lambda: self.active).run(macro, [], {})
            secret.assert_not_called()
            run.assert_called_once()
            with self.assertRaises(login.LoginError):
                MacroRunner(self.library, lambda: self.active).run(macro, [], {}, full=True)
            secret.assert_called_once_with('missing-account')


if __name__ == '__main__':
    unittest.main()
