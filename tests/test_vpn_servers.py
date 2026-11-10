"""Route ranking, cancellation and rollback. Never activate a real VPN here."""
import base64
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

import vpn_servers as vpn
from detection import login

KEY = base64.b64encode(b'x' * 32).decode()
CURRENT = vpn.Server('jp756.nordvpn.com', '93.118.43.10', KEY, 'Current')
TOKYO = vpn.Server('jp1000.nordvpn.com', '93.118.43.11', KEY, 'Tokyo', 10)
OSAKA = vpn.Server('jp1001.nordvpn.com', '93.118.43.12', KEY, 'Osaka', 20)


def measurement(latency=40, variation=2, failures=0):
    return dict(samples=8, failures=failures, latency=latency, variation=variation)


class Backend:
    interface = 'test-vpn'
    def __init__(self):
        self.events = []
    def current(self):
        return CURRENT
    def direct_route(self):
        return dict(interface='eth0', dev='eth0', gateway='192.0.2.1', prefsrc='192.0.2.2')
    def prepare(self):
        self.events.append('prepare')
    def activate(self, server):
        self.events.append(('activate', server))
    def cleanup(self):
        self.events.append('cleanup')
    def save(self, server):
        self.events.append(('save', server))


class DynamicProbeTests(unittest.TestCase):
    def test_probe_target_is_a_fresh_control_address(self):
        result = __import__('subprocess').CompletedProcess([], 0,
            '66.22.131.132 STREAM\n66.22.131.132 DGRAM\n', '')
        with patch.object(login.subprocess, 'run', return_value=result):
            self.assertEqual(vpn.resolve_probe_target(), '66.22.131.132')

    def test_probe_connection_and_route_check_use_the_same_snapshot(self):
        cancel = threading.Event()
        with patch.object(vpn, 'route_device', return_value='gfn-jp') as route, \
             patch.object(vpn.socket, 'create_connection') as connection:
            vpn.probe('gfn-jp', cancel, lambda: None, samples=1, target='66.22.131.132')
        connection.assert_called_once_with(('66.22.131.132', 443), timeout=1.5)
        self.assertTrue(all(call.args[0] == '66.22.131.132' for call in route.call_args_list))


class ServerSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'report.json'
        environment = patch.dict(os.environ, XDG_RUNTIME_DIR=self.temp.name)
        environment.start()
        self.addCleanup(environment.stop)
        self.idle_guard = vpn.ensure_idle
        guard = patch.object(vpn, 'ensure_idle')
        self.guard = guard.start()
        self.addCleanup(guard.stop)
        self.backend = Backend()

    def run_search(self, metrics, direct=None, **kwargs):
        iterator = None if callable(metrics) else iter(metrics)
        def measure(*args, **options):
            if options.get('bound'):
                return direct or measurement(100)
            return metrics(*args) if callable(metrics) else next(iterator)
        with patch.object(vpn, 'probe', side_effect=measure):
            return vpn.optimize([], backend=self.backend, provider=lambda: [TOKYO, OSAKA],
                                report_path=self.path, **kwargs)

    def test_ranks_real_route_metrics_and_saves_only_winner_after_cleanup(self):
        report = self.run_search([measurement(50), measurement(20, failures=1), measurement(25)])
        self.assertEqual(report['best']['hostname'], OSAKA.hostname)
        self.assertEqual(self.backend.events[-2:], ['cleanup', ('save', OSAKA)])
        self.assertEqual(vpn.load_report(self.path), report)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(report['method'], 'tcp-connect')
        self.assertEqual(report['target'], vpn.TARGET + ':443')

    def test_production_scan_uses_one_resolved_target_for_routes_and_measurements(self):
        target = '66.22.131.132'
        with patch.object(vpn, 'resolve_probe_target', return_value=target), \
             patch.object(vpn, 'SplitTunnel', return_value=self.backend) as constructor, \
             patch.object(vpn, 'probe', return_value=measurement()) as probe:
            report = vpn.optimize([], provider=lambda: [TOKYO], report_path=self.path)
        constructor.assert_called_once_with(target=target)
        self.assertTrue(all(call.kwargs['target'] == target for call in probe.call_args_list))
        self.assertEqual(report['target'], target + ':443')
        self.assertEqual(report['target_host'], login.CONTROL_HOSTS[0])

    def test_variation_affects_ranking_and_current_server_can_win(self):
        report = self.run_search([measurement(30, 1), measurement(25, 20), measurement(40)])
        self.assertEqual(report['best']['hostname'], CURRENT.hostname)

    def test_in_game_search_completes_without_requiring_game_to_close(self):
        self.guard.side_effect = self.idle_guard
        game = login.Window('a', 'GeForce NOW Unknown game', 'GeForceNOW', 0, 0, 800, 600)
        with patch.object(vpn, 'macro_input_active', return_value=False), \
             patch.object(login, 'all_windows', return_value=[game]) as windows:
            report = self.run_search([measurement(50), measurement(20), measurement(40)], allow_in_game=True)
        windows.assert_not_called()  # No mid-scan game guard can cancel the opted-in scan.
        self.assertEqual(report['best']['hostname'], TOKYO.hostname)
        self.assertEqual(self.backend.events[-2:], ['cleanup', ('save', TOKYO)])

    def test_in_game_search_still_blocks_active_macro_input_before_network_changes(self):
        self.guard.side_effect = self.idle_guard
        with patch.object(vpn, 'macro_input_active', return_value=True), \
             patch.object(vpn, 'api_json') as api:
            with self.assertRaisesRegex(ValueError, 'Finish recording or playback'):
                self.run_search([measurement()] * 3, allow_in_game=True)
        api.assert_not_called()
        self.assertEqual(self.backend.events, [])
        self.assertFalse(self.path.exists())

    def test_macro_input_starting_during_in_game_search_cleans_up_without_saving(self):
        self.guard.side_effect = self.idle_guard
        with patch.object(vpn, 'macro_input_active', side_effect=[False, False, False, True]):
            with self.assertRaisesRegex(ValueError, 'Finish recording or playback'):
                self.run_search([measurement()] * 3, allow_in_game=True)
        self.assertEqual(self.backend.events[-1], 'cleanup')
        self.assertFalse(self.path.exists())

    def test_in_game_search_cancellation_cleans_up_without_saving(self):
        cancel = threading.Event()
        def cancel_probe(*_):
            cancel.set()
            vpn.check_cancel(cancel)
        with self.assertRaises(vpn.SearchCancelled):
            self.run_search(cancel_probe, cancel=cancel, allow_in_game=True)
        self.assertEqual(self.backend.events[-1], 'cleanup')
        self.assertFalse(self.path.exists())

    def test_direct_result_is_ranked_but_vpn_winner_is_still_saved(self):
        report = self.run_search([measurement(50), measurement(20), measurement(40)], direct=measurement(10, 1))
        self.assertEqual(report['version'], 2)
        self.assertTrue(report['direct_fastest'])
        self.assertEqual(report['results'][0]['kind'], 'direct')
        self.assertEqual(report['direct']['latency'], 10)
        self.assertEqual(report['best']['hostname'], TOKYO.hostname)
        self.assertEqual(self.backend.events[-1], ('save', TOKYO))

    def test_slow_failed_or_equal_direct_result_never_enables_auto_disconnect(self):
        for direct in (measurement(80), measurement(None, None, 8), measurement(20), measurement(10, 1, 1)):
            with self.subTest(direct=direct):
                report = self.run_search([measurement(50), measurement(20), measurement(40)], direct=direct)
                self.assertFalse(report['direct_fastest'])

    def test_unavailable_physical_route_keeps_direct_result_unavailable(self):
        with patch.object(self.backend, 'direct_route', side_effect=ValueError('unavailable')):
            report = self.run_search([measurement()] * 3)
        self.assertIsNone(report['direct']['latency'])
        self.assertFalse(report['direct_fastest'])

    def test_corrupt_direct_cache_is_rejected_without_losing_legacy_compatibility(self):
        report = self.run_search([measurement()] * 3)
        report['direct'] = None
        self.path.write_text(json.dumps(report))
        self.assertEqual(vpn.load_report(self.path), {})
        report['version'] = 1
        del report['direct']
        self.path.write_text(json.dumps(report))
        self.assertEqual(vpn.load_report(self.path), report)

    def test_all_failed_connections_keep_previous_server_and_report(self):
        self.path.write_text('previous')
        with self.assertRaisesRegex(ValueError, 'No VPN server passed'):
            self.run_search([measurement(None, None, 8)] * 3)
        self.assertEqual(self.backend.events[-1], 'cleanup')
        self.assertEqual(self.path.read_text(), 'previous')

    def test_cancel_during_measurement_cleans_up_without_saving(self):
        cancel = threading.Event()
        def cancel_probe(*_):
            cancel.set()
            vpn.check_cancel(cancel)
        with patch.object(self.backend, 'close', create=True) as close:
            with self.assertRaises(vpn.SearchCancelled):
                self.run_search(cancel_probe, cancel=cancel)
        close.assert_called_once_with()
        self.assertEqual(self.backend.events[-1], 'cleanup')
        self.assertFalse(self.path.exists())

    def test_cancel_during_final_profile_read_does_not_commit(self):
        cancel = threading.Event()
        calls = iter((False, True))
        def current():
            if next(calls):
                cancel.set()
            return CURRENT
        with patch.object(self.backend, 'current', side_effect=current):
            with self.assertRaises(vpn.SearchCancelled):
                self.run_search([measurement()] * 3, cancel=cancel)
        self.assertEqual(self.backend.events[-1], 'cleanup')
        self.assertFalse(self.path.exists())

    def test_cancel_during_save_restores_original_server(self):
        cancel = threading.Event()
        original_save = self.backend.save
        def save(server):
            original_save(server)
            cancel.set()
        with patch.object(self.backend, 'save', side_effect=save):
            with self.assertRaises(vpn.SearchCancelled):
                self.run_search([measurement(60), measurement(20), measurement(40)], cancel=cancel)
        self.assertEqual(self.backend.events[-2:], [('save', TOKYO), ('save', CURRENT)])
        self.assertFalse(self.path.exists())

    def test_activation_failure_and_cleanup_failure_never_save(self):
        for operation in ('activate', 'cleanup'):
            with self.subTest(operation=operation), patch.object(self.backend, operation, side_effect=ValueError('failed')):
                with self.assertRaisesRegex(ValueError, 'failed'):
                    self.run_search([measurement()] * 3)
                self.assertFalse(self.path.exists())
                self.assertFalse(any(isinstance(event, tuple) and event[0] == 'save' for event in self.backend.events))

    def test_report_write_failure_restores_original_public_peer(self):
        with patch.object(vpn.MacroLibrary, 'atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.run_search([measurement(60), measurement(20), measurement(40)])
        self.assertEqual(self.backend.events[-2:], [('save', TOKYO), ('save', CURRENT)])

    def test_detected_game_blocks_before_api_or_network_changes(self):
        self.guard.side_effect = ValueError('Close the GFN game')
        with self.assertRaisesRegex(ValueError, 'Close the GFN game'):
            self.run_search([])
        self.assertEqual(self.backend.events, [])

    def test_game_appearing_mid_test_aborts_and_cleans_up(self):
        self.guard.side_effect = [None, None, None, ValueError('game started')]
        with self.assertRaisesRegex(ValueError, 'game started'):
            self.run_search([measurement()])
        self.assertEqual(self.backend.events[-1], 'cleanup')
        self.assertFalse(self.path.exists())

    def test_shared_lock_blocks_server_test_and_f10(self):
        from tool_status import vpn_lock, toggle_vpn
        with vpn_lock(), patch('tool_status.vpn_status') as status:
            with self.assertRaisesRegex(ValueError, 'Another VPN action'):
                self.run_search([])
            self.assertFalse(toggle_vpn())
            status.assert_not_called()
        self.assertEqual(self.backend.events, [])


class ProviderTests(unittest.TestCase):
    def row(self):
        return dict(hostname=TOKYO.hostname, station=TOKYO.address, status='online', load=10,
                    locations=[{'country': {'code': 'JP', 'city': {'name': 'Tokyo'}}}],
                    technologies=[dict(identifier='wireguard_udp', metadata=[dict(name='public_key', value=KEY)],
                                       pivot={'status': 'online'})])

    def test_only_valid_japanese_wireguard_servers_are_accepted(self):
        self.assertEqual(vpn.parse_server(self.row()), TOKYO)
        for key, value in [('station', '127.0.0.1'), ('station', 'invalid'), ('status', 'offline'),
                           ('hostname', 'malicious.example'), ('technologies', []), ('locations', [])]:
            with self.subTest(key=key, value=value):
                row = self.row()
                row[key] = value
                self.assertIsNone(vpn.parse_server(row))

    def test_recommendations_include_both_cities_not_only_the_first_country_results(self):
        osaka = self.row()
        osaka.update(hostname=OSAKA.hostname, station=OSAKA.address, load=20)
        osaka['locations'][0]['country']['city']['name'] = 'Osaka'
        cities = [dict(identifier='jp_tokyo', name='Tokyo', latitude=35, longitude=139),
                  dict(identifier='jp_osaka', name='Osaka', latitude=34, longitude=135)]
        with patch.object(vpn, 'api_json', side_effect=[[dict(code='JP', id=108, cities=cities)],
                                                       [self.row()], [self.row(), osaka]]) as api:
            self.assertEqual(vpn.recommendations(), [TOKYO, OSAKA])
            self.assertEqual([call.args[1]['coordinates[latitude]'] for call in api.call_args_list[1:]], [35, 34])

    def test_idle_guard_checks_all_windows_and_unknown_native_games(self):
        game = login.Window('a', 'GeForce NOW Unknown game', 'GeForceNOW', 0, 0, 800, 600)
        with patch.object(vpn, 'macro_input_active', return_value=False), \
             patch.object(login, 'all_windows', return_value=[game]):
            with self.assertRaisesRegex(ValueError, 'Close the GFN game'):
                vpn.ensure_idle([])
        with patch.object(vpn, 'macro_input_active', return_value=False), \
             patch.object(login, 'all_windows', return_value=[login.Window('a', 'GeForce NOW', 'GeForceNOW', 0, 0, 800, 600)]):
            vpn.ensure_idle([])

    def test_tcp_probe_reports_connection_failures_and_refuses_wrong_route(self):
        cancel = threading.Event()
        with patch.object(vpn, 'route_device', return_value='probe'), \
             patch.object(vpn.socket, 'create_connection', side_effect=[OSError(), unittest.mock.MagicMock()]), \
             patch.object(vpn.time, 'monotonic', side_effect=[0, 1, 1.04]), \
             patch.object(cancel, 'wait', return_value=False):
            result = vpn.probe('probe', cancel, lambda: None, samples=2)
        self.assertEqual(result, dict(samples=2, failures=1, latency=40.0, variation=0.0))
        with patch.object(vpn, 'route_device', return_value='ethernet'), patch.object(vpn.socket, 'create_connection') as connect:
            with self.assertRaisesRegex(ValueError, 'did not use the VPN'):
                vpn.probe('probe', cancel, lambda: None)
            connect.assert_not_called()

    def test_direct_probe_binds_physical_interface_and_checks_its_route(self):
        cancel = threading.Event()
        with patch.object(vpn, 'route_device', return_value='eth0') as route, \
             patch.object(vpn.socket, 'socket') as socket_factory, \
             patch.object(vpn.time, 'monotonic', side_effect=[0, .02]), \
             patch.object(cancel, 'wait', return_value=False):
            result = vpn.probe('eth0', cancel, lambda: None, samples=1, bound=True)
        connection = socket_factory.return_value.__enter__.return_value
        connection.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'eth0\0')
        connection.connect.assert_called_once_with((vpn.TARGET, 443))
        self.assertEqual(route.call_args.args, (vpn.TARGET, 'eth0'))
        self.assertEqual(result['latency'], 20)

    def test_invalid_report_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            for value in ('{bad', '[]', '{"version":0}'):
                path.write_text(value)
                self.assertEqual(vpn.load_report(path), {})
