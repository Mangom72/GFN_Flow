"""Read-only provider checks and real GTK tool controls; never change networking."""
import subprocess
import tempfile
import fcntl
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from auto_macro import Picker, main
from detection import login
from tool_status import read, read_tool_status, vpn_status, toggle_vpn


class ToolStatusTests(unittest.TestCase):
    def setUp(self):
        guard = patch('tool_status.macro_input_active', return_value=False)
        guard.start()
        self.addCleanup(guard.stop)

    def test_global_vpn_is_paused_while_macro_input_is_active(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', XDG_RUNTIME_DIR=directory), \
             patch('tool_status.macro_input_active', return_value=True), \
             patch('tool_status.vpn_status') as probe, patch('tool_status.subprocess.run') as run:
            self.assertFalse(toggle_vpn())
        probe.assert_not_called()
        run.assert_not_called()

    def test_provider_states_and_missing_optional_providers(self):
        cases = [
            ((0, f'{login.SPLIT_CONNECTION}:wireguard\n'), (1, ''), True),
            ((1, ''), (0, 'Status: Connected\n'), True),
            ((0, 'Other:wireguard\n'), (0, 'Status: Disconnected\n'), False),
            ((0, ''), (127, ''), False),
            ((127, ''), (0, 'Status: Disconnected\n'), False),
            ((127, ''), (127, ''), None),
            ((None, ''), (0, 'Status: Disconnected\n'), None),
            ((0, ''), (0, 'Status: Connecting\n'), None),
            ((0, ''), (1, 'Unavailable'), None),
        ]
        for split, nord, expected in cases:
            with self.subTest(split=split, nord=nord), patch('tool_status.read', side_effect=[split, nord]) as probe:
                self.assertIs(vpn_status(), expected)
                self.assertEqual(probe.call_args_list[0].args[0][-2:], ['show', '--active'])
                self.assertEqual(probe.call_args_list[1].args[0], ['nordvpn', 'status'])

    def test_reads_are_bounded_and_errors_are_unknown(self):
        with patch('tool_status.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='safe')) as run:
            self.assertEqual(read(['nordvpn', 'status']), (0, 'safe'))
            self.assertEqual(run.call_args.kwargs['timeout'], 2)
            self.assertEqual(run.call_args.kwargs['env']['LC_ALL'], 'C')
        for error, expected in [(FileNotFoundError(), 127), (OSError(), None),
                                (subprocess.TimeoutExpired('status', 2), None)]:
            with patch('tool_status.subprocess.run', side_effect=error):
                self.assertEqual(read(['status']), (expected, ''))

    def test_anti_afk_states(self):
        for code, expected in [(0, True), (3, False), (4, False), (1, None), (None, None)]:
            with patch('tool_status.vpn_status', return_value=False), patch('tool_status.read', return_value=(code, '')) as probe:
                self.assertEqual(read_tool_status(), dict(vpn=False, afk=expected))
                self.assertEqual(probe.call_args.args[0],
                                 ['systemctl', '--user', 'is-active', '--quiet', 'gfn-anti-afk.service'])

    def test_shortcut_uses_fresh_status_to_connect_or_disconnect(self):
        for connected, command in [(False, 'vpn'), (True, 'vpn-disconnect')]:
            with tempfile.TemporaryDirectory() as directory, \
                 patch.dict('os.environ', {'XDG_RUNTIME_DIR': directory}), \
                 patch('tool_status.vpn_status', return_value=connected), \
                 patch('tool_status.subprocess.run') as run:
                self.assertTrue(toggle_vpn())
                self.assertEqual(run.call_args.args[0][-1], command)
                self.assertEqual(run.call_args.kwargs, dict(check=True, timeout=120))

    def test_shortcut_never_guesses_unknown_state_or_runs_twice_in_flight(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict('os.environ', {'XDG_RUNTIME_DIR': directory}), \
             patch('tool_status.vpn_status', return_value=None) as probe, \
             patch('tool_status.subprocess.run') as run:
            with self.assertRaises(ValueError):
                toggle_vpn()
            run.assert_not_called()
            probe.reset_mock()
            with (Path(directory) / 'auto-macro/vpn-toggle.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertFalse(toggle_vpn())
                probe.assert_not_called()
                run.assert_not_called()

    def test_shortcut_cli_dispatches_without_starting_or_opening_the_picker(self):
        with patch('sys.argv', ['macro-studio', 'vpn-toggle']), \
             patch('tool_status.toggle_vpn') as toggle, patch('auto_macro.request') as request:
            main()
            toggle.assert_called_once_with()
            request.assert_not_called()

    def test_unknown_shortcut_state_reports_error_without_changing_network(self):
        with patch('sys.argv', ['macro-studio', 'vpn-toggle']), \
             patch('tool_status.toggle_vpn', side_effect=ValueError('VPN status unavailable. Try again shortly.')), \
             patch.object(login, 'show_widget') as status:
            with self.assertRaises(SystemExit) as stopped:
                main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertEqual(status.call_args.kwargs['tone'], 'error')
            self.assertFalse(status.call_args.kwargs['visible'])


class ToolUiTests(unittest.TestCase):
    def setUp(self):
        self.picker = Picker(preview=True, width=360)
        self.picker.render()

    def tearDown(self):
        self.picker.window.destroy()
        self.picker.preview_directory.cleanup()

    def test_tools_are_always_visible_icons_with_accessible_names(self):
        tools = self.picker.tool_buttons['vpn'].get_parent()
        self.assertIsInstance(tools, self.picker.Gtk.Box)
        self.assertTrue(tools.get_visible())
        self.assertEqual(len(tools.get_children()), 5)
        for button in tools.get_children():
            self.assertTrue(button.get_visible())
            self.assertFalse(button.get_label())
            self.assertTrue(button.get_tooltip_text())
            self.assertTrue(button.get_accessible().get_name())

    def test_single_vpn_action_tracks_state_and_preserves_list_selection(self):
        rows = self.picker.quick_lists['login']
        row = rows.get_row_at_index(0)
        rows.select_row(row)
        button = self.picker.tool_buttons['vpn']
        for state, name, enabled in [(False, 'Connect Japan VPN', True), (True, 'Disconnect VPN', True),
                                     (None, 'Checking VPN status…', False)]:
            with self.subTest(state=state), patch.object(self.picker, 'render') as render:
                self.picker.apply_tool_status(dict(vpn=state, afk=False))
                self.assertEqual(button.get_accessible().get_name(), name)
                if state is not None:
                    self.assertIn('F10', button.get_tooltip_text())
                self.assertEqual(button.get_sensitive(), enabled)
                self.assertEqual(button.get_style_context().has_class('tool-active'), state is True)
                self.assertIs(rows.get_selected_row(), row)
                render.assert_not_called()

    def test_vpn_dispatches_only_current_action_with_existing_focus_guard(self):
        for state, command, idle in [(False, 'vpn', True), (True, 'vpn-disconnect', False)]:
            self.picker.tool_status['vpn'] = state
            with patch.object(self.picker, 'run_command') as run:
                self.picker.vpn_action()
                self.assertEqual(run.call_args.args[0][-1], command)
                self.assertEqual(run.call_args.kwargs, dict(idle=idle, vpn=True))
                self.assertIsNone(self.picker.tool_status['vpn'])
                self.assertFalse(self.picker.tool_buttons['vpn'].get_sensitive())
        with patch.object(self.picker, 'run_command') as run:
            self.picker.vpn_action()
            run.assert_not_called()

    def test_anti_afk_can_stop_without_game_but_never_start_without_game(self):
        for afk, expected in [(True, True), (False, False), (None, False)]:
            self.picker.apply_tool_status(dict(vpn=False, afk=afk))
            self.assertEqual(self.picker.tool_buttons['afk'].get_sensitive(), expected)

    def test_widget_vpn_action_cannot_bypass_server_test_lock(self):
        from tool_status import vpn_lock
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', XDG_RUNTIME_DIR=directory), \
             patch.object(self.picker, 'launch') as launch, patch('auto_macro.subprocess.run') as run:
            self.picker.run_command(['gfn-login', 'vpn-disconnect'], 'Done', vpn=True)
            work = launch.call_args.args[0]
            with vpn_lock():
                with self.assertRaisesRegex(ValueError, 'Another VPN action'):
                    work()
            run.assert_not_called()

    def test_status_errors_remain_visible_as_warning_and_tooltip(self):
        self.picker.message = 'Action stopped: Test failure'
        self.picker.render()
        self.assertTrue(self.picker.status_button.get_visible())
        self.assertTrue(self.picker.status_button.get_style_context().has_class('danger'))
        self.assertIn(self.picker.message, self.picker.status_button.get_tooltip_text())

    def test_in_flight_status_does_not_overwrite_controls_during_work(self):
        self.picker.busy = True
        self.picker.tool_polling = True
        self.picker.tool_status = dict(vpn=None, afk=None)
        self.picker.apply_tool_status(dict(vpn=True, afk=True))
        self.assertEqual(self.picker.tool_status, dict(vpn=None, afk=None))
        self.assertFalse(self.picker.tool_polling)

