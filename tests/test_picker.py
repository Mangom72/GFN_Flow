"""Real GTK picker transitions with fake game windows; no input or secrets."""
import unittest
from unittest.mock import patch
from auto_macro import Picker
from detection import Sessions, login


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.picker = Picker(preview=True)
        self.picker.sessions = Sessions()
        self.picker.scan = lambda: self.picker.sessions.update(self.windows, self.picker.profiles)
        self.picker.persist = lambda: None
        self.profile = self.picker.profiles[0]
        # Exact profile name is not always a title alias; use installed regex
        # with a known localized game title for the actual detector.
        self.active = login.Window('fake-game', 'Honkai: Star Rail', 'GeForceNOW', 0, 0, 1920, 1080)
        self.windows = [self.active]
        self.assertIsNotNone(login.detect_profile(self.picker.profiles, self.active.title))

    def tearDown(self):
        self.picker.window.destroy()
        self.picker.preview_directory.cleanup()

    def test_widget_accepts_keyboard_focus(self):
        self.assertTrue(self.picker.window.get_accept_focus())

    def test_picker_keyboard_selects_across_types_and_never_runs_on_navigation(self):
        from types import SimpleNamespace
        from gi.repository import Gdk
        from macro_library import new_macro
        normal = new_macro('normal')
        normal.update(name='Test', game='Test normal', title_re='Test', steps=['enter'])
        self.picker.library.put(normal)
        self.picker.render()
        event = lambda key: SimpleNamespace(keyval=Gdk.keyval_from_name(key), state=0)
        with patch.object(self.picker, 'run_macro') as run:
            self.assertTrue(self.picker.settings_key(self.picker.window, event('Down')))
            self.assertIsNotNone(self.picker.quick_selected)
            self.assertTrue(self.picker.settings_key(self.picker.window, event('End')))
            self.assertEqual(self.picker.quick_selected.macro['kind'], 'normal')
            self.picker.settings_key(self.picker.window, event('Left'))
            self.assertFalse(self.picker.quick_sections['normal'].get_expanded())
            self.picker.settings_key(self.picker.window, event('Right'))
            self.assertTrue(self.picker.quick_sections['normal'].get_expanded())
            run.assert_not_called()
            self.picker.settings_key(self.picker.window, event('End'))
            self.picker.game = (self.active, self.profile)
            self.picker.settings_key(self.picker.window, event('Return'))
            run.assert_called_once_with(self.picker.quick_selected.macro)
            self.assertEqual(run.call_args.args[0]['kind'], 'normal')
            self.picker.settings_key(self.picker.window, event('Escape'))
            self.assertFalse(self.picker.window.get_visible())

    def test_category_arrows_follow_focused_header_over_selected_macro(self):
        from types import SimpleNamespace
        from gi.repository import Gdk
        self.picker.render()
        login_row = self.picker.quick_lists['login'].get_row_at_index(0)
        self.picker.quick_lists['login'].select_row(login_row)
        normal = self.picker.quick_sections['normal']
        normal.grab_focus()
        event = lambda key: SimpleNamespace(keyval=Gdk.keyval_from_name(key), state=0)
        self.picker.settings_key(self.picker.window, event('Left'))
        self.assertFalse(normal.get_expanded())
        self.assertTrue(self.picker.quick_sections['login'].get_expanded())
        self.picker.settings_key(self.picker.window, event('Right'))
        self.assertTrue(normal.get_expanded())

    def test_first_f9_runs_once_then_picker_toggles(self):
        with patch.object(login, 'active_window', side_effect=lambda: self.active), \
             patch.object(self.picker, 'run_login') as run:
            self.picker.open()
            run.assert_called_once_with(full=False)
            self.picker.open()
            self.assertTrue(self.picker.window.get_visible())
            self.picker.open()
            self.assertFalse(self.picker.window.get_visible())
            run.assert_called_once()

    def test_first_f9_login_stays_hidden_until_next_f9(self):
        # Drive the real F9/run/finish path without injecting game input.
        def complete(work, message, *, reopen_on_success=True):
            self.picker.hide()
            self.picker.busy = True
            work()
            self.picker.finished(message, reopen_on_success)

        with patch.object(login, 'active_window', return_value=self.active), \
             patch.object(self.picker, 'launch', side_effect=complete), \
             patch('auto_macro.MacroRunner.run') as run:
            self.picker.open()
            run.assert_called_once()
            self.assertFalse(self.picker.busy)
            self.assertFalse(self.picker.window.get_visible())
            self.picker.open()
            self.assertTrue(self.picker.window.get_visible())
            self.picker.open()
            self.assertFalse(self.picker.window.get_visible())
            run.assert_called_once()

    def test_normal_macro_keeps_completion_reopening(self):
        from macro_library import new_macro
        macro = new_macro('normal')
        self.picker.game = (self.active, self.profile)
        with patch.object(self.picker, 'launch') as launch:
            self.picker.run_macro(macro)
            self.assertTrue(launch.call_args.kwargs.get('reopen_on_success', True))

    def test_background_game_never_auto_types(self):
        with patch.object(login, 'active_window', return_value=login.Window('terminal', 'Terminal', 'kitty', 0, 0, 900, 600)), \
             patch.object(self.picker, 'run_login') as run:
            self.picker.open()
            run.assert_not_called()
            self.assertTrue(self.picker.window.get_visible())

    def test_first_f9_after_detection_with_picker_already_open(self):
        self.picker.render()
        with patch.object(login, 'active_window', return_value=self.active), \
             patch.object(self.picker, 'run_login') as run:
            self.picker.open()
            run.assert_called_once()

    def test_original_input_worker_pinned_to_session(self):
        self.picker.game = (self.active, login.detect_profile(self.picker.profiles, self.active.title))
        self.picker.launch = lambda work, _, **kwargs: work()
        original = login.active_window
        wrong = login.Window('other-window', 'Honkai: Star Rail', 'chrome', 0, 0, 1920, 1080)
        def execute(*args, **kwargs):
            with self.assertRaises(login.LoginError):
                login.active_window()
        with patch.object(login, 'active_window', side_effect=[self.active, self.active, wrong]), \
             patch.object(login, 'execute_profile', side_effect=execute) as worker:
            self.picker.run_login()
            worker.assert_called_once()
        self.assertIs(login.active_window, original)


if __name__ == '__main__':
    unittest.main()
