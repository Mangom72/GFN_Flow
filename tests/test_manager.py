from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from auto_macro import Picker
from detection import Sessions, login
from macro_library import MacroLibrary, new_macro
from macro_manager import Manager


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.secrets = {}
        for name, side_effect in [('store_secret', self.secrets.__setitem__), ('get_secret', self.secrets.__getitem__)]:
            mock = patch.object(login, name, side_effect=side_effect)
            mock.start()
            self.addCleanup(mock.stop)
        self.profile = login.Profile('Demo', 'Demo', 'example-account', 'demo-key', 1920, 1080, ['id', 'tab', 'pw', 'enter'])
        self.library = MacroLibrary(Path(self.tmp.name) / 'library.json', [self.profile])
        self.picker = Picker(preview=True, library=self.library)
        self.picker.legacy_profiles = [self.profile]
        self.picker.profiles = [self.profile]
        self.picker.scan = lambda: None
        self.manager = Manager(self.picker, offscreen=True, width=360)
        self.manager.show()

    def tearDown(self):
        self.manager.window.destroy()
        self.picker.window.destroy()
        self.tmp.cleanup()

    def select(self, identity):
        for group in self.manager.rows:
            for row in group.iterchildren():
                if row[0] == identity:
                    self.manager.tree.expand_to_path(row.path)
                    self.manager.tree.get_selection().select_path(row.path)
                    return
        self.fail('Macro not present in current view')

    def click(self, accessible_name):
        def visit(widget):
            if isinstance(widget, self.picker.Gtk.Button) and widget.get_accessible().get_name() == accessible_name:
                widget.emit('clicked')
                return True
            if isinstance(widget, self.picker.Gtk.Container):
                return any(visit(child) for child in widget.get_children())
            return False
        self.assertTrue(visit(self.manager.window), accessible_name)

    def test_editor_and_file_operations_are_context_views_not_tabs(self):
        def tab_names():
            return [self.manager.tabs.get_tab_label(self.manager.tabs.get_nth_page(index)).get_accessible().get_name()
                    for index in range(self.manager.tabs.get_n_pages())]
        self.assertEqual(tab_names(), ['Library', 'History', 'Visual Workflows', 'Login Credentials', 'Blacklist'])
        self.select(self.library.macros[0]['id'])
        self.click('Edit')
        self.assertEqual(self.manager.views.get_visible_child_name(), 'editor')
        self.assertEqual(self.manager.editor_title.get_text(), 'Edit macro')
        self.click('Back to library')
        self.assertEqual(self.manager.views.get_visible_child_name(), 'browse')
        self.manager.edit(new_macro('normal'))
        self.assertEqual(self.manager.editor_title.get_text(), 'New macro')
        self.manager.choose_file(True)
        self.assertEqual(self.manager.views.get_visible_child_name(), 'files')
        self.assertEqual(tab_names(), ['Library', 'History', 'Visual Workflows', 'Login Credentials', 'Blacklist'])

    def test_blacklist_tab_can_delete_restore_and_remove_only(self):
        self.manager.tabs.set_current_page(4)
        page = self.manager.game_blacklist
        page.detected.select_row(page.detected.get_row_at_index(0))
        self.click('Delete detected game')
        self.assertEqual(self.library.games, [])
        self.assertEqual([game['name'] for game in self.library.blacklist], ['Demo'])
        self.assertEqual(len(self.library.macros), 1)
        page.blocked.select_row(page.blocked.get_row_at_index(0))
        self.click('Unblock and restore')
        self.assertEqual([game['name'] for game in self.library.games], ['Demo'])
        self.assertEqual(self.library.blacklist, [])
        page.detected.select_row(page.detected.get_row_at_index(0))
        self.click('Delete detected game')
        page.blocked.select_row(page.blocked.get_row_at_index(0))
        self.click('Remove only')
        self.assertEqual(self.library.blacklist, [])
        self.assertEqual(self.library.games, [])
        reloaded = MacroLibrary(self.library.path, [self.profile])
        self.assertEqual(reloaded.game_options([self.profile]), [])
        active = login.Window('game', 'GeForce NOW Demo', 'GeForceNOW', 0, 0, 1920, 1080)
        self.assertTrue(reloaded.remember_detected([(active, self.profile)]))
        self.assertEqual(reloaded.game_options([self.profile]), [('Demo', 'Demo')])

    def test_keyboard_selects_tab_and_only_its_title_is_visible(self):
        from gi.repository import Gdk
        from types import SimpleNamespace

        tabs = self.manager.tabs
        for index in range(5):
            event = SimpleNamespace(keyval=Gdk.KEY_1 + index, state=Gdk.ModifierType.CONTROL_MASK)
            self.assertTrue(self.manager.keyboard(self.manager.window, event))
            self.assertEqual(tabs.get_current_page(), index)
            self.assertEqual([tabs.get_tab_label(tabs.get_nth_page(i)).title_reveal.get_reveal_child() for i in range(5)],
                             [i == index for i in range(5)])
            selected = tabs.get_tab_label(tabs.get_nth_page(index))
            self.assertTrue(selected.title.get_visible() and selected.title.get_child_visible())

    def test_login_tab_registers_credentials_without_detaching_legacy_actions(self):
        macro = self.library.macros[0]
        self.manager.tabs.set_current_page(3)
        page = self.manager.login_page
        self.assertEqual(page.target.get_active_id(), macro['id'])
        self.assertEqual(page.account.get_text(), self.profile.user)
        self.assertFalse(page.password.get_visibility())
        page.account.set_text('test-account')
        self.click('Save credentials')
        self.assertEqual(self.manager.notice.get_text(), 'Enter a password to register these credentials.')
        page.password.set_text('test-password')
        self.click('Save credentials')
        self.assertEqual(page.password.get_text(), '')
        saved = self.library.auto_for('Demo')
        self.assertEqual(saved['source_profile'], macro['source_profile'])
        self.assertEqual(saved['steps'], macro['steps'])
        profile = self.library.resolved_profile(saved, [self.profile])
        self.assertEqual(profile.user, 'test-account')
        self.assertEqual(login.get_secret(profile.credential_key), 'test-password')
        self.assertNotIn('test-account', self.library.path.read_text(encoding='utf-8'))
        self.assertNotIn('test-password', self.library.path.read_text(encoding='utf-8'))
        page.account.set_text('replacement-account')
        self.click('Save credentials')
        self.assertEqual(self.library.auto_for('Demo')['credential_key'], profile.credential_key)

    def test_login_tab_adds_edits_and_removes_a_second_account(self):
        self.manager.tabs.set_current_page(3)
        page = self.manager.login_page
        macro = self.library.macros[0]
        self.assertFalse(page.profile_field.get_visible())
        self.click('Add profile')
        self.assertTrue(page.alias_field.get_visible())
        self.assertEqual(page.account.get_text(), '')
        page.alias.set_text('Weekend')
        page.account.set_text('second-id')
        page.password.set_text('second-password')
        self.click('Save credentials')
        choice = page.profile.get_active_id()
        self.assertTrue(page.profile_field.get_visible())
        self.assertEqual(page.account.get_text(), 'second-id')
        page.reload_profiles(None)
        self.assertEqual(page.profile.get_active_id(), 'primary')
        self.assertEqual(page.account.get_text(), self.profile.user)
        page.profile.set_active_id(choice)
        self.assertEqual(page.password.get_text(), '')
        self.assertEqual(self.library.get(macro['id'])['steps'], macro['steps'])
        profile = self.library.resolved_profile(self.library.get(macro['id']), [self.profile], account_id=choice)
        self.assertEqual(login.get_secret(profile.credential_key), 'second-password')
        self.assertEqual(self.library.resolved_profile(self.library.get(macro['id']), [self.profile]).user, self.profile.user)
        exported = Path(self.tmp.name) / 'export.json'
        self.library.export_file(exported)
        self.assertNotIn('Weekend', exported.read_text(encoding='utf-8'))
        self.assertNotIn('second-id', self.library.path.read_text(encoding='utf-8'))
        page.alias.set_text('Alt')
        with patch.object(login, 'delete_secret', side_effect=self.secrets.pop):
            self.click('Save credentials')
            self.assertEqual(self.library.get(macro['id'])['login_accounts'][0]['label'], 'Alt')
            self.click('Remove profile')
            self.assertTrue(page.profile_field.get_visible())
            self.click('Remove profile')
        self.assertFalse(page.profile_field.get_visible())
        self.assertEqual(page.account.get_text(), self.profile.user)
        self.assertNotIn(profile.credential_key, self.secrets)

    def test_delete_in_context_editor_never_deletes_the_library_selection(self):
        from gi.repository import Gdk
        from types import SimpleNamespace
        macro = self.library.macros[0]
        self.select(macro['id'])
        self.manager.edit(macro)
        event = SimpleNamespace(keyval=Gdk.KEY_Delete, state=0)
        self.assertFalse(self.manager.keyboard(self.manager.window, event))
        self.assertIsNotNone(self.library.get(macro['id']))

    def test_editor_add_save_hide_delete_restore_workflow(self):
        self.manager.edit(new_macro('sequence'))
        self.manager.fields['name'].set_text('Confirm menu')
        self.manager.fields['game'].set_text('Demo')
        self.manager.fields['title_re'].set_text('Demo')
        self.manager.action.set_active(2)  # key
        self.manager.inputs.key.set_active_id('ENTER')
        self.click('Add action')
        self.click('Save macro')
        macro = next(m for m in self.library.macros if m['name'] == 'Confirm menu')
        self.assertEqual(macro['steps'], ['wait:500', 'key:ENTER'])
        self.select(macro['id'])
        self.click('Hide / Show')
        self.assertTrue(self.library.get(macro['id'])['hidden'])
        self.manager.filter.set_active(1)
        self.select(macro['id'])
        self.click('Delete')
        self.assertIsNone(self.library.get(macro['id']))
        self.manager.filter.set_active(2)
        self.select(macro['id'])
        self.click('Restore')
        self.assertIsNotNone(self.library.get(macro['id']))

    def test_edited_login_becomes_independent_and_preserves_original(self):
        macro = self.library.macros[0]
        self.manager.edit(macro)
        self.manager.fields['name'].set_text('My login')
        self.manager.fields['user'].set_text('different-account')
        self.click('Save macro')
        saved = self.library.get(macro['id'])
        self.assertEqual(saved['source_profile'], '')
        self.assertEqual(saved['user'], '')
        self.assertEqual(self.secrets[saved['account_key']], 'different-account')
        self.assertNotIn('different-account', self.library.path.read_text())
        self.assertEqual(self.profile.user, 'example-account')

    def test_hidden_login_never_runs_on_f9(self):
        self.library.hide(self.library.macros[0]['id'])
        self.picker.sessions = Sessions()
        active = login.Window('a', 'Demo', 'GeForceNOW', 0, 0, 1920, 1080)
        self.picker.sessions.update([active], [self.profile])
        with patch.object(login, 'active_window', return_value=active), patch.object(self.picker, 'run_login') as run:
            self.picker.open()
            run.assert_not_called()
        self.assertFalse(self.picker.sessions.attempted)

    def test_queue_button_does_not_send_input(self):
        macro = self.library.macros[0]
        self.select(macro['id'])
        with patch.object(login, 'run') as run:
            self.click('Queue for F9')
            run.assert_not_called()
        self.assertEqual(self.picker.pending, macro['id'])
        self.assertFalse(self.manager.window.get_visible())

    def test_click_capture_scaled_and_never_clicks(self):
        self.manager.edit(self.library.macros[0])
        active = login.Window('a', 'Demo', 'GeForceNOW', 100, 100, 960, 540)
        before = len(self.manager.steps)
        with patch.object(login, 'cursor_position', return_value=(350, 300)), patch.object(login, 'run') as run:
            self.assertTrue(self.manager.capture_from(active))
            run.assert_not_called()
        self.assertEqual(len(self.manager.steps), before + 1)
        self.assertEqual(self.manager.steps[-1][0], 'click:500,400')

    def test_embedded_navigation_and_save_reuse_same_widget(self):
        embedded = Manager(self.picker)
        self.picker.manager = embedded
        embedded.show()
        self.assertIs(embedded.window, self.picker.window)
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'manager')
        embedded.edit(self.library.macros[0])
        embedded.fields['name'].set_text('Edited inline')
        embedded.save_draft()
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'manager')
        embedded.choose_file(True)
        self.assertEqual(embedded.views.get_visible_child_name(), "files")
        self.assertIs(embedded.window, self.picker.window)
        embedded.close()
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'picker')
        self.assertTrue(self.picker.window.get_visible())


if __name__ == '__main__':
    unittest.main()
