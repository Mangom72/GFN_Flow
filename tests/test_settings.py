"""Preferences, in-place language changes and wheel-safe native form controls."""
import json
import gc
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from auto_macro import Picker
from detection import login
from macro_library import MacroLibrary, new_macro
from macro_manager import Manager
from preferences import DEFAULTS, Preferences
from ui_input_scroll import protect_inputs
from ui_motion import Motion


class PreferenceTests(unittest.TestCase):
    def test_persistence_validation_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            preferences = Preferences(path)
            self.assertEqual(preferences.data, DEFAULTS)
            for key, value in [('language', 'ko'), ('animations', False), ('size', 'compact')]:
                preferences.set(key, value)
            self.assertEqual(Preferences(path).data, {**DEFAULTS, 'language': 'ko', 'animations': False, 'size': 'compact'})
            if sys.platform != 'win32':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            before = path.read_text()
            for key, value in [('language', 'xx'), ('animations', 1), ('size', []), ('password', 'anything')]:
                with self.assertRaises(ValueError):
                    preferences.set(key, value)
            self.assertEqual(path.read_text(), before)
            path.write_text('{bad json')
            self.assertEqual(Preferences(path).data, DEFAULTS)
            path.write_text(json.dumps(dict(language='ko', size=[], animations='yes', unknown=True)))
            self.assertEqual(Preferences(path).data, {**DEFAULTS, 'language': 'ko'})


class SettingsUiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.profile = login.Profile('Demo', 'Demo', 'fake-user', 'fake-key', 1920, 1080, ['enter'])
        self.library = MacroLibrary(Path(directory.name) / 'library.json', [self.profile])
        with patch.object(login, 'load_profiles', return_value=({}, [self.profile])):
            self.picker = Picker(preview=True, width=360, height=420, library=self.library)
        self.addCleanup(self.picker.window.destroy)
        self.picker.library_height = 420
        self.picker.scan = lambda: None
        self.picker.render()
        self.manager = Manager(self.picker)
        self.picker.manager = self.manager
        self.manager.show()
        self.macro = new_macro('normal')
        self.macro.update(name='Settings', game='Demo', title_re='Demo', steps=['wait:500', 'key:Enter'])
        self.manager.edit(self.macro)
        self.picker.show_settings()

    def pump(self):
        loop = self.picker.GLib.MainLoop()
        self.picker.GLib.timeout_add(80, lambda: (loop.quit(), False)[1])
        loop.run()

    def scroll(self, widget):
        from gi.repository import Gdk
        event = Gdk.Event.new(Gdk.EventType.SCROLL)
        event.direction = Gdk.ScrollDirection.DOWN
        return widget.emit('scroll-event', event)

    def test_language_changes_live_without_changing_draft_or_technical_values(self):
        field = self.manager.fields['name']
        field.set_text('Settings')
        before = [row[0] for row in self.manager.steps]
        self.manager.steps_view.get_selection().select_path(1)
        self.picker.settings_page.language.set_active_id('ko')
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'settings')
        self.assertIs(self.manager.fields['name'], field)
        self.assertEqual(field.get_text(), 'Settings')
        self.assertEqual(self.manager.fields['game'].get_text(), 'Demo')
        self.assertEqual([row[0] for row in self.manager.steps], before)
        self.assertEqual(self.manager.editor_title.get_text(), '새 매크로')
        tabs = self.manager.editor.get_children()[0]
        self.assertEqual([tabs.get_tab_label(tabs.get_nth_page(i)).get_text() for i in range(2)], ['설정', '동작'])
        self.assertEqual(self.manager.inputs.value(), 'key:Enter')
        self.picker.settings_page.language.set_active_id('en')
        self.assertEqual(self.manager.editor_title.get_text(), 'New macro')
        self.assertEqual(Preferences(self.picker.preferences.path).data['language'], 'en')
        self.picker.leave_settings()
        self.assertEqual(self.manager.views.get_visible_child_name(), 'editor')
        self.assertEqual(self.manager.fields['name'].get_text(), 'Settings')

    def test_vpn_search_cancel_results_and_language_stay_inside_settings(self):
        from ui_qa import vpn_fixture
        panel = self.picker.settings_page.vpn
        with patch('vpn_servers.optimize') as search:
            panel.action.clicked()  # Offscreen previews never change networking.
            search.assert_not_called()
        self.picker.vpn_testing = True
        self.picker.vpn_test_state = dict(stage='test', index=2, total=5, server='jp1000.nordvpn.com', rows=[])
        self.picker.refresh_vpn_search()
        self.assertIn('jp1000', panel.status.get_text())
        self.assertEqual(panel.action.get_accessible().get_name(), 'Cancel server test')
        panel.action.clicked()
        self.assertTrue(self.picker.vpn_cancel.is_set())
        report = vpn_fixture()
        self.picker.vpn_search_finished(report, '')
        self.assertFalse(self.picker.vpn_testing)
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'settings')
        self.assertIn(report['best']['hostname'], panel.status.get_text())
        self.picker.settings_page.language.set_active_id('ko')
        self.assertIn('다음 연결', panel.status.get_text())
        self.assertEqual(panel.action.get_accessible().get_name(), '최적 일본 서버 찾기')
        self.picker.settings_page.language.set_active_id('en')
        self.assertIn('Next connection:', panel.status.get_text())

    def test_direct_winner_is_visible_and_translates_the_conditional_timer_hint(self):
        from ui_qa import vpn_fixture
        self.picker.vpn_search_finished(vpn_fixture(True), '')
        panel = self.picker.settings_page.vpn
        self.assertIn('Without VPN is fastest', panel.status.get_text())
        self.assertIn('Without VPN', [label.get_text() for label in panel.results.get_children()])
        self.picker.settings_page.language.set_active_id('ko')
        self.assertIn('10초', panel.status.get_text())

    def test_failed_scan_identifies_saved_results_and_translates_their_timestamp(self):
        from ui_qa import vpn_fixture
        report = vpn_fixture()
        self.picker.vpn_report = report
        self.picker.vpn_search_finished(None, 'VPN server test failed. Check the VPN connection.')
        panel = self.picker.settings_page.vpn
        self.assertTrue(panel.previous.get_visible())
        self.assertIn('Previous results', panel.previous.get_text())
        self.assertIs(self.picker.vpn_report, report)
        self.picker.settings_page.language.set_active_id('ko')
        self.assertIn('이전 결과', panel.previous.get_text())
        self.picker.vpn_search_finished(report, '')
        self.assertFalse(panel.previous.get_visible())
        self.picker.vpn_report = {}
        self.picker.vpn_search_finished(None, 'VPN server test failed. Check the VPN connection.')
        self.assertFalse(panel.previous.get_visible())

    def test_in_game_vpn_warning_translates_and_does_not_block_search(self):
        panel = self.picker.settings_page.vpn
        self.assertFalse(panel.warning.get_visible())
        game = (login.Window('game', 'GeForce NOW Demo', 'GeForceNOW', 0, 0, 800, 600), self.profile)
        self.picker.sessions.games = {'game': game}
        self.picker.show_settings()
        self.assertTrue(panel.warning.get_visible())
        self.assertTrue(panel.action.get_sensitive())
        self.assertIn('May interrupt gameplay', [child.get_text() for child in panel.warning.get_children()
                                                if isinstance(child, self.picker.Gtk.Label)])
        self.picker.settings_page.language.set_active_id('ko')
        self.assertIn('게임', panel.warning.get_tooltip_text())
        self.assertIn('게임이 중단될 수 있습니다', [child.get_text() for child in panel.warning.get_children()
                                                 if isinstance(child, self.picker.Gtk.Label)])
        self.picker.sessions.games = {}
        self.picker.vpn_testing = True
        panel.refresh()
        self.assertTrue(panel.warning.get_visible())
        self.assertTrue(panel.action.get_sensitive())  # Cancel remains available.
        self.picker.vpn_testing = False
        panel.refresh()
        self.assertFalse(panel.warning.get_visible())

    def test_vpn_results_describe_server_addresses_and_translate_current_and_city(self):
        from ui_qa import vpn_fixture
        report = vpn_fixture()
        before = json.dumps(report)
        self.picker.vpn_search_finished(report, '')
        panel = self.picker.settings_page.vpn
        def names():
            return [label.get_text() for label in panel.results.get_children()
                    if panel.results.child_get_property(label, 'left-attach') == 0]
        self.assertIn('93.118.43.10 · Current', names())
        self.assertIn('93.118.43.11 · Tokyo', names())
        self.assertIn('93.118.43.12 · Osaka', names())
        self.assertIn('Without VPN', names())
        address = next(label for label in panel.results.get_children() if label.get_text() == '93.118.43.10 · Current')
        self.assertIn('jp756.nordvpn.com', address.get_tooltip_text())
        self.picker.settings_page.language.set_active_id('ko')
        self.assertIn('93.118.43.10 · 현재', names())
        self.assertIn('93.118.43.11 · 도쿄', names())
        self.picker.settings_page.language.set_active_id('en')
        self.picker.vpn_testing = True
        self.picker.vpn_test_state = dict(stage='test', index=3, total=5, server='jp1001.nordvpn.com', rows=report['results'])
        panel.refresh()
        self.assertIn('93.118.43.10 · Current', names())
        self.assertEqual(json.dumps(report), before)

    def test_legacy_vpn_results_without_addresses_keep_hostname_description(self):
        from ui_qa import vpn_fixture
        report = vpn_fixture()
        for row in report['results']:
            row.pop('address', None)
        self.picker.vpn_search_finished(report, '')
        labels = [label.get_text() for label in self.picker.settings_page.vpn.results.get_children()]
        self.assertIn('jp756 · Current', labels)
        self.assertIn('Without VPN', labels)

    def test_vpn_search_blocks_macro_playback_and_first_f9(self):
        self.picker.vpn_testing = True
        with patch.object(self.picker, 'launch') as launch:
            self.picker.run_macro(self.macro)
            launch.assert_not_called()
        with patch.object(login, 'active_window') as active, patch.object(self.picker, 'run_login') as run:
            self.picker.open(auto=True)
            active.assert_not_called()
            run.assert_not_called()
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'settings')

    def test_vpn_worker_completion_preserves_outside_dismissal_and_draft(self):
        from ui_qa import vpn_fixture
        report = vpn_fixture()
        field = self.manager.fields['name']
        self.picker.game = (login.Window('game', 'GeForce NOW Demo', 'GeForceNOW', 0, 0, 800, 600), self.profile)
        windows = sys.platform == 'win32'
        backend = 'windows_vpn_scan.scan' if windows else 'vpn_servers.optimize'
        status = patch('windows_vpn.status', return_value=False) if windows else patch('auto_macro.WINDOWS', False)
        with status, patch.object(self.picker, 'preview', False), \
             patch.object(self.picker, 'refresh_tool_status'), \
             patch('auto_macro.threading.Thread') as thread, \
             patch(backend, return_value=report) as search:
            self.picker.start_vpn_search()
            self.assertTrue(self.picker.vpn_testing)
            thread.return_value.start.assert_called_once()
            self.assertFalse(thread.call_args.kwargs['daemon'])
            self.picker.hide()
            thread.call_args.kwargs['target']()
            self.pump()
            self.assertIs(search.call_args.args[-1], self.picker.vpn_cancel)
            self.assertEqual(search.call_args.kwargs, {} if windows else {'allow_in_game': True})
            self.assertFalse(self.picker.vpn_testing)
            self.assertEqual(self.picker.vpn_report, report)
            self.assertFalse(self.picker.window.get_visible())
            self.assertIs(self.manager.fields['name'], field)

    def test_settings_resume_after_hide_and_explicit_close_resets(self):
        self.picker.hide()
        with patch.object(login, 'active_window', return_value=login.Window('terminal', 'Terminal', 'kitty', 0, 0, 800, 600)):
            self.picker.open(auto=False)
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'settings')
        self.picker.close()
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'picker')

    def test_cold_settings_open_has_a_valid_picker_to_return_to(self):
        picker = Picker(preview=True, library=self.library)
        self.addCleanup(picker.window.destroy)
        picker.show_settings()
        self.assertTrue(picker.panel.get_children())
        picker.leave_settings()
        self.assertEqual(picker.stack.get_visible_child_name(), 'picker')

    def test_disabled_animations_are_applied_when_preferences_are_loaded(self):
        self.picker.preferences.set('animations', False)
        picker = Picker(preview=True, library=self.library)
        self.addCleanup(picker.window.destroy)
        self.assertTrue(picker.window.get_style_context().has_class('reduced-motion'))
        self.assertFalse(picker.motion.enabled)

    def test_widget_size_updates_in_place_without_rebuilding_editor(self):
        field = self.manager.fields['name']
        self.picker.preferences.data['size'] = 'compact'
        with patch.object(self.picker, 'preview', False), patch.object(self.picker, 'publish_playback'), \
             patch.object(self.picker, 'resize_view') as resize:
            self.picker.apply_preferences()
        self.assertEqual((self.picker.width, self.picker.height, self.picker.library_height), (360, 480, 520))
        self.assertEqual((self.manager.view_width, self.manager.view_height), (360, 520))
        self.assertIs(self.manager.fields['name'], field)
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'settings')
        resize.assert_called_once_with(520)

    def test_status_description_and_fixed_game_options_follow_language(self):
        self.manager.say('Select a macro first.')
        self.picker.settings_page.language.set_active_id('ko')
        self.assertEqual(self.manager.notice.get_accessible().get_description(), '먼저 매크로를 선택하십시오.')
        self.picker.leave_settings()
        self.manager.editor.get_children()[0].set_current_page(0)
        choice = self.manager.target.choice
        for identity, expected in [('none', '게임 선택…'), ('0', 'Demo'), ('custom', '직접 지정…')]:
            choice.set_active_id(identity)
            self.pump()
            self.assertEqual(choice.get_cells()[0].get_property('text'), expected)

    def test_switches_persist_and_failed_save_restores_control(self):
        page = self.picker.settings_page
        page.animations.set_active(False)
        page.size.set_active_id('compact')
        self.assertEqual(Preferences(self.picker.preferences.path).data['size'], 'compact')
        self.assertFalse(self.picker.preferences.data['animations'])
        with patch.object(self.picker.preferences, 'set', side_effect=OSError('write failed')) as save:
            page.language.set_active_id('ko')
            self.assertEqual(save.call_count, 1)
        self.assertEqual(page.language.get_active_id(), 'en')
        self.assertEqual(self.picker.preferences.data['language'], 'en')
        self.assertTrue(page.notice.get_visible())

    def test_f9_controls_persist_and_failed_save_restores_scope(self):
        page = self.picker.settings_page
        page.f9_scope.set_active_id('game')
        page.first_f9.set_active_id('full')
        page.reopen_after_macro.set_active(False)
        saved = Preferences(self.picker.preferences.path).data
        self.assertEqual((saved['f9_scope'], saved['first_f9'], saved['reopen_after_macro']), ('game', 'full', False))
        with patch.object(self.picker.preferences, 'set', side_effect=OSError('write failed')):
            page.f9_scope.set_active_id('focused')
        self.assertEqual(page.f9_scope.get_active_id(), 'game')

    def test_wheel_never_changes_number_dropdown_or_entry_and_scrolls_page(self):
        self.picker.leave_settings()
        self.manager.editor.get_children()[0].set_current_page(0)
        self.pump()
        page = self.manager.editor.get_children()[0].get_nth_page(0)
        adjustment = page.get_vadjustment()
        controls = [self.manager.fields['repeat'], self.manager.kind, self.manager.target.choice, self.manager.fields['name']]
        for control in controls:
            with self.subTest(control=type(control).__name__):
                adjustment.set_value(0)
                value = control.get_active_id() if isinstance(control, self.picker.Gtk.ComboBox) else control.get_text()
                self.assertTrue(self.scroll(control))
                after = control.get_active_id() if isinstance(control, self.picker.Gtk.ComboBox) else control.get_text()
                self.assertEqual(value, after)
                self.assertGreater(adjustment.get_value(), 0)

    def test_text_input_wheel_bypasses_its_own_scroll_box(self):
        Gtk = self.picker.Gtk
        page = Gtk.ScrolledWindow()
        inner = Gtk.ScrolledWindow()
        inner._input_scroll = True
        text = Gtk.TextView()
        inner.add(text)
        page.add(inner)
        protect_inputs(page, Gtk)
        outer = page.get_vadjustment()
        outer.configure(0, 0, 1000, 10, 100, 100)
        inside = inner.get_vadjustment()
        inside.configure(0, 0, 1000, 10, 100, 100)
        self.assertTrue(self.scroll(text))
        self.assertGreater(outer.get_value(), 0)
        self.assertEqual(inside.get_value(), 0)
        page.destroy()

    def test_motion_preference_updates_existing_native_stacks(self):
        motion = Motion(self.picker.Gtk)
        stack = motion.stack(self.picker.Gtk.Stack())
        self.assertGreater(stack.get_transition_duration(), 0)
        motion.set_enabled(False)
        self.assertEqual(stack.get_transition_duration(), 0)
        motion.set_enabled(True)
        self.assertGreater(stack.get_transition_duration(), 0)
        stack.destroy()

    def test_wheel_over_action_controls_or_preferences_never_edits_values(self):
        self.manager.steps_view.get_selection().select_path(0)
        before = [row[0] for row in self.manager.steps]
        for control, value in [(self.manager.inputs.delay, '500'), (self.manager.inputs.action, 'wait'),
                               (self.picker.settings_page.language, 'en'), (self.picker.settings_page.size, 'standard')]:
            self.assertTrue(self.scroll(control))
            current = control.get_active_id() if isinstance(control, self.picker.Gtk.ComboBox) else control.get_text()
            self.assertEqual(current, value)
        self.assertEqual([row[0] for row in self.manager.steps], before)

    def test_macro_names_and_tooltips_are_never_translated(self):
        macro = self.library.put(self.macro)
        self.picker.render()
        self.picker.show_settings()
        self.picker.settings_page.language.set_active_id('ko')
        row = self.picker.quick_lists['normal'].get_row_at_index(0)
        label = row.get_child().get_children()[0]
        self.assertEqual(label.get_text(), 'Demo\n동작 2개')
        self.assertEqual(label.get_tooltip_text(), 'Settings')
        self.assertEqual(self.library.get(macro['id'])['name'], 'Settings')

    def test_every_settings_label_switches_after_gc_and_bindings_release_on_destroy(self):
        picker = Picker(preview=True, library=self.library)
        picker.show_settings()
        def labels(widget):
            if isinstance(widget, picker.Gtk.Label):
                yield widget.get_text()
            if isinstance(widget, picker.Gtk.Container):
                for child in widget.get_children():
                    yield from labels(child)
        try:
            for language, expected in [('ko', ['설정', '화면', '언어', '화면 전환 애니메이션', '위젯 크기', '단축키']),
                                       ('en', ['Settings', 'Appearance', 'Language', 'Page animations', 'Widget size', 'Shortcuts']),
                                       ('ko', ['설정', '화면', '언어', '화면 전환 애니메이션', '위젯 크기', '단축키']),
                                       ('en', ['Settings', 'Appearance', 'Language', 'Page animations', 'Widget size', 'Shortcuts'])]:
                picker.settings_page.language.set_active_id(language)
                gc.collect()
                loop = picker.GLib.MainLoop()
                picker.GLib.timeout_add(80, lambda: (loop.quit(), False)[1])
                loop.run()
                rendered = list(labels(picker.settings_page.root))
                for text in expected:
                    self.assertIn(text, rendered)
        finally:
            picker.window.destroy()
        self.assertFalse(picker.localizer.bindings)
        self.assertFalse(picker.localizer.drawings)

    def test_action_heading_stays_localized_when_reordering(self):
        self.manager.steps_view.get_selection().select_path(0)
        self.picker.settings_page.language.set_active_id('ko')
        self.manager.move_step(1)
        self.assertEqual(self.manager.action_title.get_text(), '동작 2 편집')
        self.picker.settings_page.language.set_active_id('en')
        self.assertEqual(self.manager.action_title.get_text(), 'Edit action 2')

