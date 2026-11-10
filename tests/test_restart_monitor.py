"""Restart monitor handoff is mocked; never signal processes or change VPN state."""
from pathlib import Path
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch

from detection import login


class MonitorOwnershipTests(unittest.TestCase):
    def test_normal_duplicate_exits_without_signalling_anything(self):
        with patch.object(login.fcntl, 'flock', side_effect=BlockingIOError), \
             patch.object(login.os, 'pidfd_open') as open_pid, \
             patch.object(login.signal, 'pidfd_send_signal') as signal_pid:
            self.assertFalse(login.claim_vpn_monitor(Mock()))
        open_pid.assert_not_called()
        signal_pid.assert_not_called()

    def replace(self, script=None, command=b'vpn-monitor', uid=None, timeout=False):
        lock = Mock()
        lock.read.return_value = '987654'
        process = MagicMock()
        script = script if script is not None else str(Path(login.__file__).resolve()).encode()
        process.__truediv__.return_value.read_bytes.return_value = b'/usr/bin/python3\0' + script + b'\0' + command + b'\0'
        process.stat.return_value = SimpleNamespace(st_uid=os.getuid() if uid is None else uid)
        proc = MagicMock()
        proc.__truediv__.return_value = process
        real_path = login.Path
        def path(value):
            return proc if value == '/proc' else real_path(value)
        with patch.object(login, 'Path', side_effect=path), \
             patch.object(login.fcntl, 'flock', side_effect=BlockingIOError if timeout else [BlockingIOError(), None]), \
             patch.object(login.os, 'pidfd_open', return_value=123) as open_pid, \
             patch.object(login.os, 'close') as close_pid, \
             patch.object(login.signal, 'pidfd_send_signal') as signal_pid, \
             patch.object(login.time, 'sleep'), \
             patch.object(login.time, 'monotonic', side_effect=[0, 4 if timeout else 0]):
            if timeout:
                with self.assertRaisesRegex(login.LoginError, 'Could not replace'):
                    login.claim_vpn_monitor(lock, replace=True)
            else:
                self.assertTrue(login.claim_vpn_monitor(lock, replace=True))
        open_pid.assert_called_once_with(987654)
        close_pid.assert_called_once_with(123)
        return signal_pid

    def test_only_same_user_exact_helper_monitor_is_replaced_via_pidfd(self):
        for command in (b'vpn-monitor', b'vpn-watch'):
            signal_pid = self.replace(command=command)
            signal_pid.assert_called_once_with(123, login.signal.SIGTERM)

    def test_unrelated_process_wrong_mode_or_different_owner_are_not_signalled(self):
        for kwargs in (dict(script=b'/other/program.py'), dict(command=b'auto'), dict(uid=os.getuid() + 1)):
            with self.subTest(kwargs=kwargs):
                self.replace(**kwargs).assert_not_called()

    def test_unknown_monitor_owner_times_out_without_signalling(self):
        self.replace(script=b'/other/program.py', timeout=True).assert_not_called()


class RestartMonitorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        state = Path(temp.name)
        for name, value in (('STATE_DIR', state), ('MONITOR_LOCK', state / 'monitor.lock')):
            p = patch.object(login, name, value)
            p.start(); self.addCleanup(p.stop)
        for name in ('log', 'show_widget', 'claim_vpn_monitor'):
            p = patch.object(login, name, return_value=True)
            p.start(); self.addCleanup(p.stop)

    def test_waits_for_new_native_window_then_preserves_eligible_auto_off(self):
        launcher = login.Window('new', 'GeForce NOW', 'GeForceNOW', 0, 0, 1280, 720)
        game = login.Window('new', 'GeForce NOW Demo', 'GeForceNOW', 0, 0, 1280, 720)
        with patch.object(login, 'find_monitored_window', side_effect=[None, launcher, game]) as find, \
             patch.object(login, 'split_connection_active', return_value=True), \
             patch.object(login, 'direct_disconnect_report', return_value={'direct_fastest': True}), \
             patch.object(login, 'disconnect_if_direct_is_fastest') as countdown, \
             patch.object(login.time, 'monotonic', return_value=0), patch.object(login.time, 'sleep'):
            login.vpn_monitor('', 'GeForceNOW', [], 'split', restart=True)
        self.assertEqual(find.call_args.args, ('new', 'GeForceNOW'))
        countdown.assert_called_once_with('new', 'GeForceNOW', game.title)

    def test_manual_disconnect_during_window_wait_is_respected(self):
        with patch.object(login, 'split_connection_active', return_value=False), \
             patch.object(login, 'find_monitored_window') as find, \
             patch.object(login, 'run') as run:
            login.vpn_monitor('', 'GeForceNOW', [], 'split', restart=True)
        find.assert_not_called()
        run.assert_not_called()

    def test_missing_new_window_has_bounded_wait(self):
        with patch.object(login, 'split_connection_active', return_value=True), \
             patch.object(login, 'find_monitored_window', return_value=None), \
             patch.object(login.time, 'monotonic', side_effect=[0, 16]), \
             self.assertRaisesRegex(login.LoginError, 'could not find'):
            login.vpn_monitor('', 'GeForceNOW', [], 'split', restart=True)


if __name__ == '__main__':
    unittest.main()
