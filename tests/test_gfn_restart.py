"""Mock GFN/network operations; an owned Python child verifies lock inheritance."""
from pathlib import Path
import subprocess
import tempfile
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from auto_macro import Picker
from gfn_tools import APP_ID, force_restart_gfn, restart_lock, gfn_running


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict('os.environ', XDG_RUNTIME_DIR=self.tmp.name)
        env.start(); self.addCleanup(env.stop)
        guard = patch('gfn_tools.macro_input_active', return_value=False)
        guard.start(); self.addCleanup(guard.stop)
        self.run_patch = patch('gfn_tools.subprocess.run', return_value=SimpleNamespace(returncode=0))
        self.run = self.run_patch.start(); self.addCleanup(self.run_patch.stop)
        start = patch('gfn_tools.subprocess.Popen')
        self.start = start.start(); self.addCleanup(start.stop)
        self.start.return_value.poll.return_value = None
        self.start.return_value.wait.return_value = 0

    def test_kills_only_gfn_then_waits_for_stop_before_relaunch(self):
        with patch('gfn_tools.gfn_running', side_effect=[True, True, False, False, True]), patch('gfn_tools.time.sleep'):
            self.assertTrue(force_restart_gfn())
        self.assertEqual([call.args[0] for call in self.run.call_args_list],
                         [['flatpak', 'info', APP_ID], ['flatpak', 'kill', APP_ID]])
        self.assertEqual([call.args[0] for call in self.start.call_args_list],
                         [[str(Path.home() / '.local/bin/gfn-login'), 'vpn-prepare'],
                          ['flatpak', 'run', APP_ID], [str(Path.home() / '.local/bin/gfn-login'), 'vpn-watch']])
        self.assertTrue(all(call.kwargs == dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, start_new_session=True) for call in self.start.call_args_list[1:]))

    def test_gfn_already_closed_just_reopens_it(self):
        with patch('gfn_tools.gfn_running', side_effect=[False, True]):
            self.assertTrue(force_restart_gfn())
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(self.start.call_count, 3)

    def test_vpn_is_ready_before_gfn_is_killed_and_launched(self):
        events = Mock()
        events.attach_mock(self.run, 'run')
        events.attach_mock(self.start, 'start')
        with patch('gfn_tools.gfn_running', side_effect=[True, False, True]):
            force_restart_gfn()
        commands = [call.args[0] for call in events.mock_calls if call[0] in ('run', 'start')]
        self.assertEqual(commands, [['flatpak', 'info', APP_ID],
            [str(Path.home() / '.local/bin/gfn-login'), 'vpn-prepare'],
            ['flatpak', 'kill', APP_ID], ['flatpak', 'run', APP_ID],
            [str(Path.home() / '.local/bin/gfn-login'), 'vpn-watch']])
        preparation = self.start.call_args_list[0]
        self.assertEqual(len(preparation.kwargs['pass_fds']), 1)
        self.assertIsInstance(preparation.kwargs['pass_fds'][0], int)
        self.start.return_value.wait.assert_called_once_with(timeout=120)

    def test_vpn_failure_cancels_without_closing_gfn_or_launching(self):
        self.start.return_value.wait.return_value = 1
        with patch('gfn_tools.gfn_running') as running, \
             self.assertRaisesRegex(ValueError, 'Japan VPN.*cancelled'):
            force_restart_gfn()
        running.assert_not_called()
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(self.start.call_count, 1)  # Only VPN helper, no GFN.

    def test_vpn_timeout_keeps_helper_alive_with_inherited_lock_and_gfn_open(self):
        self.start.return_value.wait.side_effect = subprocess.TimeoutExpired('gfn-login', 120)
        with patch('gfn_tools.gfn_running') as running, \
             self.assertRaisesRegex(ValueError, 'still finishing.*left open'):
            force_restart_gfn()
        running.assert_not_called()
        self.start.assert_called_once()
        self.assertTrue(self.start.call_args.kwargs['pass_fds'])
        self.start.return_value.kill.assert_not_called()
        self.start.return_value.terminate.assert_not_called()

    def test_vpn_helper_start_error_preserves_gfn(self):
        self.start.side_effect = OSError('cannot start helper')
        with patch('gfn_tools.gfn_running') as running, \
             self.assertRaisesRegex(ValueError, 'Japan VPN.*cancelled'):
            force_restart_gfn()
        running.assert_not_called()
        self.assertEqual(self.run.call_count, 1)

    def test_other_vpn_actions_block_restart_before_any_side_effect(self):
        from tool_status import vpn_lock
        with vpn_lock(), self.assertRaisesRegex(ValueError, 'Another VPN action'):
            force_restart_gfn()
        self.run.assert_not_called()
        self.start.assert_not_called()

    def test_missing_install_does_not_kill_or_launch(self):
        self.run.return_value.returncode = 1
        with self.assertRaisesRegex(ValueError, 'not installed'): force_restart_gfn()
        self.assertEqual(self.run.call_count, 1)
        self.start.assert_not_called()

    def test_active_recording_and_duplicate_requests_do_nothing(self):
        with patch('gfn_tools.macro_input_active', return_value=True), self.assertRaises(ValueError):
            force_restart_gfn()
        self.run.assert_not_called()
        with restart_lock(), self.assertRaisesRegex(ValueError, 'already running'):
            force_restart_gfn()
        self.start.assert_not_called()

    def test_failed_stop_never_starts_a_second_instance(self):
        self.run.side_effect = [SimpleNamespace(returncode=0), SimpleNamespace(returncode=1)]
        with patch('gfn_tools.gfn_running', return_value=True), self.assertRaisesRegex(ValueError, 'Could not stop'):
            force_restart_gfn()
        self.assertEqual(self.start.call_count, 1)  # Only VPN preparation.

    def test_stop_and_start_timeouts_are_bounded(self):
        with patch('gfn_tools.gfn_running', return_value=True), \
             patch('gfn_tools.time.monotonic', side_effect=[0, 6]), self.assertRaisesRegex(ValueError, 'Could not stop'):
            force_restart_gfn()
        self.assertEqual(self.start.call_count, 1)
        with patch('gfn_tools.gfn_running', return_value=False), \
             patch('gfn_tools.time.monotonic', side_effect=[0, 11]), self.assertRaisesRegex(ValueError, 'did not start'):
            force_restart_gfn()

    def test_successful_launcher_exit_still_waits_for_new_instance(self):
        self.start.return_value.poll.return_value = 0
        with patch('gfn_tools.gfn_running', side_effect=[False, False, True]), patch('gfn_tools.time.sleep'):
            self.assertTrue(force_restart_gfn())

    def test_failed_launch_is_reported(self):
        self.start.return_value.poll.return_value = 1
        with patch('gfn_tools.gfn_running', return_value=False), self.assertRaisesRegex(ValueError, 'did not start'):
            force_restart_gfn()

    def test_running_probe_matches_exact_flatpak_id(self):
        for output, expected in [('com.nvidia.geforcenow\n', True), ('other.app\n', False), ('com.nvidia.geforcenow.beta\n', False)]:
            self.run.return_value.stdout = output
            self.assertIs(gfn_running(), expected)


class VpnLockInheritanceTests(unittest.TestCase):
    def test_child_retains_lock_after_parent_leaves_until_child_exits(self):
        # A tiny owned Python fixture: no desktop, GFN, signals or networking.
        from tool_status import vpn_lock
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, XDG_RUNTIME_DIR=directory):
            process = None
            try:
                with vpn_lock() as guard:
                    process = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read(1)'],
                        pass_fds=(guard.fileno(),), stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                with self.assertRaisesRegex(ValueError, 'Another VPN action'):
                    with vpn_lock():
                        self.fail('Child must keep the action locked until preparation finishes.')
            finally:
                if process:
                    process.stdin.write(b'x'); process.stdin.flush(); process.stdin.close()
                    process.wait(timeout=5)
            with vpn_lock():
                pass


class RestartUiTests(unittest.TestCase):
    def setUp(self):
        self.picker = Picker(preview=True, width=360)
        self.picker.render()
        self.launch = patch.object(self.picker, 'launch').start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        self.picker.dismiss_restart_confirmation()
        self.picker.window.destroy()
        self.picker.preview_directory.cleanup()

    def test_open_cancel_escape_and_hide_never_restart(self):
        from gi.repository import Gdk
        self.picker.request_gfn_restart()
        popup = self.picker.restart_confirmation
        self.assertIsNotNone(popup)
        self.assertIs(self.picker.window.get_focus(), popup.cancel_button)
        self.launch.assert_not_called()
        popup.cancel_button.emit('clicked')
        self.assertIsNone(self.picker.restart_confirmation)
        self.picker.request_gfn_restart()
        self.picker.settings_key(self.picker.window, SimpleNamespace(keyval=Gdk.KEY_Escape, state=0))
        self.assertIsNone(self.picker.restart_confirmation)
        self.picker.request_gfn_restart()
        self.picker.hide()
        self.assertIsNone(self.picker.restart_confirmation)
        self.launch.assert_not_called()

    def test_f9_dismisses_confirmation_without_auto_login_or_execution(self):
        self.picker.request_gfn_restart()
        with patch.object(self.picker, 'run_login') as login, patch.object(self.picker, 'scan') as scan:
            self.picker.open(auto=True)
        self.assertIsNone(self.picker.restart_confirmation)
        self.assertFalse(self.picker.window.get_visible())
        login.assert_not_called()
        scan.assert_not_called()
        self.launch.assert_not_called()

    def test_navigation_cancels_existing_confirmation(self):
        self.picker.request_gfn_restart()
        self.picker.show_settings()
        self.assertIsNone(self.picker.restart_confirmation)
        self.launch.assert_not_called()

    def test_only_explicit_confirm_launches_once(self):
        from gfn_tools import force_restart_gfn
        self.picker.request_gfn_restart()
        popup = self.picker.restart_confirmation
        popup.confirm_button.emit('clicked')
        self.launch.assert_called_once_with(force_restart_gfn, 'GeForce NOW restarted.', reopen_on_success=False)
        self.assertIsNone(self.picker.restart_confirmation)
        popup.confirm_button.emit('clicked')
        self.assertEqual(self.launch.call_count, 1)

    def test_busy_states_block_open_and_confirmation_is_rechecked(self):
        for field in ('busy', 'vpn_testing', 'pending_record', 'pending', 'pending_capture', 'recorder'):
            with self.subTest(field=field):
                old = getattr(self.picker, field)
                setattr(self.picker, field, True)
                self.picker.request_gfn_restart()
                self.assertIsNone(self.picker.restart_confirmation)
                setattr(self.picker, field, old)
        self.picker.request_gfn_restart()
        self.picker.busy = True
        self.picker.restart_confirmation.confirm_button.emit('clicked')
        self.launch.assert_not_called()

    def test_confirmation_translates_and_does_not_change_picker_page(self):
        self.picker.localizer.set_language('ko')
        self.picker.request_gfn_restart()
        self.assertEqual(self.picker.restart_confirmation.cancel_button.get_child().get_text(), '취소')
        self.assertEqual(self.picker.restart_confirmation.confirm_button.get_child().get_text(), '강제 재시작')
        self.assertEqual(self.picker.stack.get_visible_child_name(), 'picker')

    def test_keyboard_gamepad_confirm_requires_leaving_default_cancel(self):
        from gi.repository import Gdk
        from widget_controls import GamepadInput
        import time
        focus = Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE)
        focus.window = self.picker.window.get_window()
        focus.in_ = True
        focus.set_device(self.picker.window.get_display().get_default_seat().get_keyboard())
        self.picker.Gtk.main_do_event(focus)
        controls = GamepadInput(self.picker)
        def pump():
            deadline = time.monotonic() + .35
            while time.monotonic() < deadline:
                while self.picker.Gtk.events_pending(): self.picker.Gtk.main_iteration_do(False)
                time.sleep(.005)
        self.picker.request_gfn_restart()
        controls.dispatch('Return')
        pump()
        self.assertIsNone(self.picker.restart_confirmation)
        self.launch.assert_not_called()
        self.picker.request_gfn_restart()
        popup = self.picker.restart_confirmation
        controls.dispatch('Tab')
        self.assertIs(self.picker.window.get_focus(), popup.confirm_button)
        controls.dispatch('Return')
        pump()
        self.launch.assert_called_once()

    def test_outside_dialog_click_cancels(self):
        self.picker.request_gfn_restart()
        popup = self.picker.restart_confirmation
        while self.picker.Gtk.events_pending(): self.picker.Gtk.main_iteration_do(False)
        popup.emit('button-press-event', self._outside_event(popup))
        self.assertIsNone(self.picker.restart_confirmation)
        self.launch.assert_not_called()

    def test_clicking_warning_does_not_confirm_or_cancel(self):
        self.picker.request_gfn_restart()
        popup = self.picker.restart_confirmation
        while self.picker.Gtk.events_pending(): self.picker.Gtk.main_iteration_do(False)
        x, y = popup.get_child().translate_coordinates(popup, 0, 0)
        event = self._outside_event(popup)
        event.x, event.y = x + 5, y + 5
        popup.emit('button-press-event', event)
        self.assertIs(self.picker.restart_confirmation, popup)
        self.launch.assert_not_called()

    def _outside_event(self, popup):
        from gi.repository import Gdk
        event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        event.window = popup.get_window()
        event.x, event.y, event.button = 1, 1, 1
        return event

    def test_successful_restart_completion_leaves_widget_hidden(self):
        self.picker.hide()
        self.picker.busy = True
        with patch.object(self.picker, 'scan'), patch.object(self.picker, 'render') as render:
            self.picker.finished('GeForce NOW restarted.', reopen=False)
        self.assertFalse(self.picker.window.get_visible())
        self.assertFalse(self.picker.busy)
        render.assert_not_called()

    def test_restart_worker_requests_reopen_only_after_failure(self):
        for failure, expected in ((False, False), (True, True)):
            with self.subTest(failure=failure):
                self.picker.busy = False
                self.picker.preview = False
                work = Mock(side_effect=ValueError('Test failure') if failure else None)
                layer = SimpleNamespace(KeyboardMode=SimpleNamespace(NONE='none'), set_keyboard_mode=Mock())
                try:
                    with patch.object(self.picker, 'Layer', layer), \
                         patch.object(self.picker.window, 'get_display', return_value=SimpleNamespace(sync=Mock())), \
                         patch('auto_macro.threading.Thread') as thread, patch.object(self.picker.GLib, 'idle_add') as idle:
                        Picker.launch(self.picker, work, 'GeForce NOW restarted.', reopen_on_success=False)
                        thread.call_args.kwargs['target']()
                        self.assertEqual(idle.call_args.args[2], expected)
                finally:
                    self.picker.preview = True

    def test_translated_dialog_teardown_exits_cleanly(self):
        script = """
from auto_macro import Picker
from ui_qa import capture
from pathlib import Path
import tempfile
p = Picker(preview=True, width=360, height=420)
p.launch = lambda *_: None
p.render()
p.request_gfn_restart()
with tempfile.TemporaryDirectory() as tmp:
    capture(p.window, p.GLib, Path(tmp) / 'en.png')
    p.localizer.set_language('ko')
    capture(p.window, p.GLib, Path(tmp) / 'ko.png')
p.dismiss_restart_confirmation()
p.window.destroy()
p.preview_directory.cleanup()
"""
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                                timeout=15, env={**os.environ, 'PYTHONWARNINGS': 'ignore'})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_modal_keyboard_does_not_navigate_underlying_list(self):
        from gi.repository import Gdk
        self.picker.request_gfn_restart()
        self.assertTrue(self.picker.settings_key(self.picker.window, SimpleNamespace(keyval=Gdk.KEY_Down, state=0)))
        self.assertIsNone(self.picker.quick_selected)
        self.launch.assert_not_called()


if __name__ == '__main__': unittest.main()
