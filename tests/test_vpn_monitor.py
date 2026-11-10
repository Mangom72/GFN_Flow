import subprocess
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from detection import login


class VpnMonitorTests(unittest.TestCase):
    def setUp(self):
        default = patch.object(login, 'direct_disconnect_report', return_value=None)
        default.start()
        self.addCleanup(default.stop)
    def test_game_detection_keeps_both_backends_connected(self):
        for backend in ('split', 'nordvpn'):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                game = login.Window('a', 'GeForce NOW Palworld', 'GeForceNOW', 0, 0, 1920, 1080)
                with patch.object(login, 'STATE_DIR', state), \
                     patch.object(login, 'MONITOR_LOCK', state / 'monitor.lock'), \
                     patch.object(login, 'find_monitored_window', return_value=game), \
                     patch.object(login.time, 'monotonic', side_effect=[0, 1, 2]), \
                     patch.object(login.time, 'sleep') as sleep, \
                     patch.object(login, 'run') as run, \
                     patch.object(login, 'log'), \
                     patch.object(login, 'show_widget') as widget:
                    login.vpn_monitor('a', 'GeForceNOW', [], backend)
                    run.assert_not_called()
                    sleep.assert_not_called()
                    self.assertEqual(widget.call_args.args[0], 'GFN VPN 연결 유지')
                    self.assertTrue(all(call.kwargs['visible'] is False for call in widget.call_args_list))

    def test_only_split_backend_uses_the_direct_winner_countdown(self):
        for backend in ('split', 'nordvpn'):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                game = login.Window('a', 'GeForce NOW Palworld', 'GeForceNOW', 0, 0, 1920, 1080)
                with patch.object(login, 'STATE_DIR', state), patch.object(login, 'MONITOR_LOCK', state / 'monitor.lock'), \
                     patch.object(login, 'direct_disconnect_report', return_value=direct_report()), \
                     patch.object(login, 'disconnect_if_direct_is_fastest') as countdown, \
                     patch.object(login, 'find_monitored_window', return_value=game), \
                     patch.object(login, 'show_widget'), patch.object(login, 'log'):
                    login.vpn_monitor('a', 'GeForceNOW', [], backend)
                    if backend == 'split':
                        countdown.assert_called_once_with('a', 'GeForceNOW', game.title)
                    else:
                        countdown.assert_not_called()

    def test_waiting_beyond_ten_minutes_never_disconnects_either_backend(self):
        for backend in ('split', 'nordvpn'):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                launcher = login.Window('a', 'GeForce NOW', 'GeForceNOW', 0, 0, 1920, 1080)
                game = login.Window('a', 'GeForce NOW Super Raft Boat Classic', 'GeForceNOW', 0, 0, 1920, 1080)
                with patch.object(login, 'STATE_DIR', state), \
                     patch.object(login, 'MONITOR_LOCK', state / 'monitor.lock'), \
                     patch.object(login, 'find_monitored_window', side_effect=[launcher, launcher, game]), \
                     patch.object(login.time, 'monotonic', side_effect=[0, 601, 3600]), \
                     patch.object(login.time, 'sleep') as sleep, \
                     patch.object(login, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run, \
                     patch.object(login, 'split_connection_active', return_value=True), \
                     patch.object(login, 'nordvpn_status', return_value='Status: Connected'), \
                     patch.object(login, 'nordvpn_connected', return_value=True), \
                     patch.object(login, 'log'), patch.object(login, 'show_widget') as widget:
                    login.vpn_monitor('a', 'GeForceNOW', [], backend)
                    run.assert_not_called()
                    self.assertEqual(sleep.call_count, 2)
                    self.assertNotIn('10:00', str(widget.call_args_list))
                    self.assertEqual(login.SESSION_GAME_TITLE, game.title)

    def test_closed_window_or_manually_disconnected_vpn_ends_monitor_without_changes(self):
        for backend in ('split', 'nordvpn'):
            for window in (None, login.Window('a', 'GeForce NOW', 'GeForceNOW', 0, 0, 1920, 1080)):
                with self.subTest(backend=backend, window=window), tempfile.TemporaryDirectory() as directory:
                    state = Path(directory)
                    with patch.object(login, 'STATE_DIR', state), \
                         patch.object(login, 'MONITOR_LOCK', state / 'monitor.lock'), \
                         patch.object(login, 'find_monitored_window', return_value=window), \
                         patch.object(login, 'split_connection_active', return_value=False), \
                         patch.object(login, 'nordvpn_status', return_value='Status: Disconnected'), \
                         patch.object(login, 'run') as run, patch.object(login, 'log'), patch.object(login, 'show_widget'):
                        login.vpn_monitor('a', 'GeForceNOW', [], backend)
                        run.assert_not_called()


def direct_report(latency=12):
    direct = dict(kind='direct', samples=8, failures=0, latency=latency, variation=1,
                  interface='eth0', dev='eth0', gateway='192.0.2.1', prefsrc='192.0.2.2')
    vpn = dict(kind='vpn', samples=8, failures=0, latency=30, variation=2)
    return dict(version=2, backend='split', method='tcp-connect', target='66.22.144.44:443',
                direct=direct, results=[direct, vpn], tested_at=1000)


class DirectDisconnectTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.game = login.Window('a', 'GeForce NOW Palworld', 'GeForceNOW', 0, 0, 1920, 1080)
        self.clock = 0
        for target, kwargs in (
                ('direct_disconnect_report', dict(return_value=direct_report())),
                ('split_active_identity', dict(return_value='uuid:/active/1')),
                ('find_monitored_window', dict(return_value=self.game)),
                ('show_widget', {}), ('log', {})):
            mock = patch.object(login, target, **kwargs)
            setattr(self, target, mock.start())
            self.addCleanup(mock.stop)
        env = patch.dict(os.environ, XDG_RUNTIME_DIR=temp.name)
        env.start()
        self.addCleanup(env.stop)

    def advance(self, seconds):
        self.clock += seconds

    def countdown(self):
        with patch.object(login.time, 'monotonic', side_effect=lambda: self.clock), \
             patch.object(login.time, 'time', return_value=1000), \
             patch.object(login.time, 'sleep', side_effect=self.advance):
            login.disconnect_if_direct_is_fastest('a', 'GeForceNOW', self.game.title)

    def test_disconnects_only_after_ten_seconds_and_shows_native_countdown(self):
        moments = []
        def disconnect(*args, **kwargs):
            moments.append(self.clock)
            return subprocess.CompletedProcess(args[0], 0)
        with patch.object(login, 'run', side_effect=disconnect) as run:
            self.countdown()
        self.assertEqual(moments, [10])
        run.assert_called_once_with(['nmcli', 'connection', 'down', 'gfn-jp'], check=False)
        self.assertEqual(self.show_widget.call_args_list[0].kwargs['countdown_until'], 1010)
        self.assertTrue(all(call.kwargs['visible'] is False for call in self.show_widget.call_args_list))

    def test_no_direct_winner_does_not_start_a_timer(self):
        self.direct_disconnect_report.return_value = None
        with patch.object(login, 'run') as run:
            self.countdown()
        run.assert_not_called()
        self.show_widget.assert_not_called()
        self.assertEqual(self.clock, 0)

    def test_game_exit_reconnection_or_changed_comparison_cancels(self):
        changes = [(self.find_monitored_window, [None]),
                   (self.split_active_identity, ['uuid:/active/1', 'uuid:/active/2']),
                   (self.direct_disconnect_report, [direct_report(), None])]
        for mock, values in changes:
            with self.subTest(mock=mock):
                mock.side_effect = values
                with patch.object(login, 'run') as run:
                    self.countdown()
                    run.assert_not_called()
                mock.side_effect = None

    def test_other_vpn_action_wins_over_automatic_disconnect(self):
        from tool_status import vpn_lock
        with vpn_lock(), patch.object(login, 'run') as run:
            self.countdown()
        run.assert_not_called()

    def test_disconnect_failure_clears_the_countdown_and_shows_error(self):
        with patch.object(login, 'run', return_value=subprocess.CompletedProcess([], 1)):
            self.countdown()
        self.assertEqual(self.show_widget.call_args.kwargs['tone'], 'error')
        self.assertEqual(self.show_widget.call_args.kwargs.get('countdown_until', 0), 0)
        self.assertEqual(self.show_widget.call_args.kwargs['expires'], 3)


class DirectComparisonValidationTests(unittest.TestCase):
    def test_fresh_control_endpoint_reports_retain_direct_winner_validation(self):
        report = direct_report()
        report.update(target='66.22.131.132:443', target_host=login.CONTROL_HOSTS[0])
        self.assertTrue(login.direct_connection_is_fastest(report))
        for target in (None, '127.0.0.1:443', '66.22.131.132:80', 'invalid'):
            report['target'] = target
            self.assertFalse(login.direct_connection_is_fastest(report))

    def test_direct_must_be_strictly_faster_and_have_no_failed_connections(self):
        self.assertTrue(login.direct_connection_is_fastest(direct_report()))
        for value in (30, 40, float('nan'), -1):
            self.assertFalse(login.direct_connection_is_fastest(direct_report(value)))
        report = direct_report()
        report['direct']['failures'] = 1
        self.assertFalse(login.direct_connection_is_fastest(report))
        self.assertFalse(login.direct_connection_is_fastest({'version': 1}))
        report = direct_report()
        report['direct']['variation'] = 100
        self.assertFalse(login.direct_connection_is_fastest(report))
        for report in (None, [], {'version': 2, 'backend': 'split', 'method': 'tcp-connect',
                                 'target': '66.22.144.44:443', 'direct': None, 'results': []}):
            self.assertFalse(login.direct_connection_is_fastest(report))

    def test_old_result_or_network_change_cannot_schedule_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            report = direct_report()
            path.write_text(json.dumps(report))
            route = dict(dev='eth0', gateway='192.0.2.1', prefsrc='192.0.2.2')
            with patch.object(login, 'VPN_RESULT_FILE', path), \
                 patch.object(login.time, 'time', return_value=1001), \
                 patch.object(login.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps([route]), '')):
                self.assertIsNotNone(login.direct_disconnect_report())
                with patch.object(login.time, 'time', return_value=1000 + 86401):
                    self.assertIsNone(login.direct_disconnect_report())
                route['gateway'] = '192.0.2.9'
                with patch.object(login.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps([route]), '')):
                    self.assertIsNone(login.direct_disconnect_report())
                route['gateway'] = '192.0.2.1'
                responses = [subprocess.CompletedProcess([], 0, json.dumps([{'dev': 'wlan0'}]), ''),
                             subprocess.CompletedProcess([], 0, json.dumps([route]), '')]
                with patch.object(login.subprocess, 'run', side_effect=responses) as read:
                    self.assertIsNone(login.direct_disconnect_report())
                    self.assertEqual(read.call_count, 1)


if __name__ == '__main__':
    unittest.main()
