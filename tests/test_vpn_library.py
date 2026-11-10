"""Library routing uses only catalog host routes; never change real networking."""
import json
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from detection import login


class LibraryRouteTests(unittest.TestCase):
    def test_resolves_catalog_and_session_control_hosts_with_timeouts(self):
        hosts = {
            'apps.gxn.nvidia.com': ['3.163.218.4', '3.163.218.14'],
            'prod.cloudmatchbeta.nvidiagrid.net': ['66.22.131.132'],
            'ap-japan.cloudmatchbeta.nvidiagrid.net': ['66.22.147.39'],
            'ap-india.cloudmatchbeta.nvidiagrid.net': ['66.22.139.37'],
        }
        def resolve(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0,
                ''.join(ip + ' STREAM name\n' + ip + ' DGRAM\n' for ip in hosts[argv[-1]]), '')
        with patch.object(login.subprocess, 'run', side_effect=resolve) as run:
            self.assertEqual(login.resolve_library_routes(),
                             login.validate_library_routes([ip + '/32' for ips in hosts.values() for ip in ips]))
        self.assertEqual({call.args[0][-1] for call in run.call_args_list}, set(hosts))
        self.assertTrue(all(call.kwargs['timeout'] == 8 for call in run.call_args_list))

    def test_control_dns_failure_leaves_profile_untouched(self):
        def resolve(argv, **kwargs):
            if argv[-1] != 'apps.gxn.nvidia.com':
                raise subprocess.TimeoutExpired(argv, 8)
            return subprocess.CompletedProcess(argv, 0, '3.163.218.4 STREAM\n', '')
        from gi.repository import NM
        with patch.object(login.subprocess, 'run', side_effect=resolve), \
             patch.object(NM.Client, 'new') as client, self.assertRaises(login.LoginError):
            login.refresh_library_routes()
        client.assert_not_called()

    def test_rejects_empty_private_ipv6_broad_or_excessive_routes(self):
        invalid = [[], ['127.0.0.1/32'], ['10.0.0.1/32'], ['3.163.218.4/24'],
                   ['2600:140b::1/128'], ['0.0.0.0/0'], ['224.0.0.1/32'], ['3.163.218.4'], ['3.163.218.4/32'] * 65]
        for routes in invalid:
            with self.subTest(routes=routes), self.assertRaises(login.LoginError):
                login.validate_library_routes(routes)

    def test_unavailable_dns_does_not_mutate_any_profile(self):
        for error in (OSError(), subprocess.TimeoutExpired('getent', 8)):
            with patch.object(login.subprocess, 'run', side_effect=error), self.assertRaises(login.LoginError):
                login.refresh_library_routes()

    def test_supports_original_profile_and_exactly_managed_host_routes(self):
        self.assertTrue(login.supported_split_routes(['66.22.144.0/24'], []))
        routes = ['3.163.218.4/32', '3.163.218.14/32']
        self.assertTrue(login.supported_split_routes(['66.22.144.0/24', *reversed(routes)], routes))
        for allowed in (['0.0.0.0/0'], ['66.22.144.0/24', '8.8.8.8/32'],
                        ['66.22.144.0/24', '3.163.218.0/24'], routes):
            self.assertFalse(login.supported_split_routes(allowed, routes))

    def test_metadata_is_bounded_and_missing_means_original_profile(self):
        remote = MagicMock()
        remote.get_setting.return_value = None
        self.assertEqual(login.library_routes_from_profile(remote), [])
        setting = remote.get_setting.return_value = MagicMock()
        setting.get_data.return_value = json.dumps(['3.163.218.4/32'])
        self.assertEqual(login.library_routes_from_profile(remote), ['3.163.218.4/32'])
        for value in ('invalid', '{"bad": 1}', '["0.0.0.0/0"]'):
            setting.get_data.return_value = value
            with self.assertRaises(login.LoginError):
                login.library_routes_from_profile(remote)

    def test_connect_refreshes_routes_before_activation_and_checks_each_host(self):
        window = login.Window('a', 'GeForce NOW', 'GeForceNOW', 0, 0, 1920, 1080)
        for active in (True, False):
            with self.subTest(active=active), \
                 patch.object(login, 'require_commands'), \
                 patch.object(login, 'choose_vpn_backend', return_value='split'), \
                 patch.object(login, 'split_connection_active', side_effect=[active, True]), \
                 patch.object(login, 'refresh_library_routes', return_value=['3.163.218.4/32']) as refresh, \
                 patch.object(login, 'route_device', side_effect=lambda ip: 'eth0' if ip == '1.1.1.1' else 'gfn-jp') as route, \
                 patch.object(login, 'vpn_monitor_running', return_value=True), \
                 patch.object(login, 'current_ssid', return_value=''), \
                 patch.object(login, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run, \
                 patch.object(login, 'start_vpn_monitor'), \
                 patch.object(login, 'show_widget'), patch.object(login, 'log'):
                login.connect_japan_vpn(window, [])
                refresh.assert_called_once_with(active=active)
                self.assertIn(('3.163.218.4',), [call.args for call in route.call_args_list])
                if active:
                    run.assert_not_called()
                else:
                    run.assert_called_once_with(['nmcli', 'connection', 'up', 'gfn-jp'], check=False)

    def test_dns_failure_prevents_activation_without_disconnect(self):
        with patch.object(login, 'require_commands'), \
             patch.object(login, 'choose_vpn_backend', return_value='split'), \
             patch.object(login, 'split_connection_active', return_value=False), \
             patch.object(login, 'refresh_library_routes', side_effect=login.LoginError('DNS unavailable')), \
             patch.object(login, 'run') as run, self.assertRaises(login.LoginError):
            login.connect_japan_vpn(None, [])
        run.assert_not_called()

    def test_restart_vpn_prepare_does_not_require_focus_or_attach_old_session(self):
        for active in (True, False):
            with self.subTest(active=active), \
                 patch.object(login, 'require_commands'), \
                 patch.object(login, 'choose_vpn_backend', return_value='split'), \
                 patch.object(login, 'split_connection_active', side_effect=[active, True]), \
                 patch.object(login, 'refresh_library_routes', return_value=['3.163.218.4/32']) as refresh, \
                 patch.object(login, 'route_device', side_effect=lambda ip: 'eth0' if ip == '1.1.1.1' else 'gfn-jp'), \
                 patch.object(login, 'current_ssid', return_value=''), \
                 patch.object(login, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')), \
                 patch.object(login, 'start_vpn_monitor') as monitor, \
                 patch.object(login, 'active_window') as focused, \
                 patch.object(login, 'show_widget'), patch.object(login, 'log'):
                login.connect_japan_vpn(None, [], monitor=False)
                refresh.assert_called_once_with(active=active)
                monitor.assert_not_called()
                focused.assert_not_called()

    def test_prepare_cli_uses_the_monitorless_idempotent_connection_path(self):
        with patch('sys.argv', ['gfn-login', 'vpn-prepare']), \
             patch.object(login, 'load_profiles', return_value=({}, [])), \
             patch.object(login, 'connect_japan_vpn') as connect, \
             patch.object(login, 'active_window') as active:
            self.assertEqual(login.main(), 0)
        connect.assert_called_once_with(None, [], force=False, monitor=False)
        active.assert_not_called()

    def test_restart_rejects_an_already_connected_non_japan_nord_server(self):
        for country, valid in (('Japan', True), ('United States', False), ('', False)):
            with self.subTest(country=country), patch.object(login, 'require_commands'), \
                 patch.object(login, 'choose_vpn_backend', return_value='nordvpn'), \
                 patch.object(login, 'nordvpn_status', return_value='Status: Connected\nCountry: ' + country), \
                 patch.object(login, 'start_vpn_monitor') as monitor, patch.object(login, 'notify'), \
                 patch.object(login, 'log'), patch.object(login, 'run') as run:
                if valid:
                    login.connect_japan_vpn(None, [], monitor=False)
                else:
                    with self.assertRaisesRegex(login.LoginError, 'Japan'):
                        login.connect_japan_vpn(None, [], monitor=False)
                monitor.assert_not_called()
                run.assert_not_called()

    def test_restart_verifies_japan_after_a_new_nord_connection(self):
        for country in ('Japan', 'United States'):
            with self.subTest(country=country), patch.object(login, 'require_commands'), \
                 patch.object(login, 'choose_vpn_backend', return_value='nordvpn'), \
                 patch.object(login, 'nordvpn_status', side_effect=['Status: Disconnected', 'Status: Connected\nCountry: ' + country]), \
                 patch.object(login, 'current_ssid', return_value=''), \
                 patch.object(login, 'show_widget'), patch.object(login, 'log'), \
                 patch.object(login, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')), \
                 patch.object(login, 'start_vpn_monitor') as monitor:
                if country == 'Japan':
                    login.connect_japan_vpn(None, [], monitor=False)
                else:
                    with self.assertRaises(login.LoginError):
                        login.connect_japan_vpn(None, [], monitor=False)
                monitor.assert_not_called()

    def test_watch_cli_attaches_a_fresh_monitor_without_reading_foreground(self):
        with patch('sys.argv', ['gfn-login', 'vpn-watch']), \
             patch.object(login, 'load_profiles', return_value=({}, [])), \
             patch.object(login, 'choose_vpn_backend', return_value='split'), \
             patch.object(login, 'vpn_monitor') as monitor, patch.object(login, 'active_window') as active:
            self.assertEqual(login.main(), 0)
        monitor.assert_called_once_with('', 'GeForceNOW', [], 'split', restart=True)
        active.assert_not_called()


class LibraryProfileTests(unittest.TestCase):
    """Real libNM settings with fake client/device; no system bus mutations."""
    def setUp(self):
        import base64
        import gi
        gi.require_version('NM', '1.0')
        from gi.repository import NM
        self.NM = NM
        self.routes = ['3.163.218.4/32', '3.163.218.14/32']
        connection = NM.SimpleConnection.new()
        setting = NM.SettingConnection.new()
        setting.set_property('id', 'gfn-jp')
        setting.set_property('type', 'wireguard')
        setting.set_property('interface-name', 'gfn-jp')
        connection.add_setting(setting)
        ip4 = NM.SettingIP4Config.new()
        ip4.set_property('method', 'manual')
        ip4.set_property('never-default', True)
        connection.add_setting(ip4)
        ip6 = NM.SettingIP6Config.new()
        ip6.set_property('method', 'disabled')
        ip6.set_property('never-default', True)
        connection.add_setting(ip6)
        wg = NM.SettingWireGuard.new()
        wg.set_property('private-key', base64.b64encode(b'x' * 32).decode())
        peer = NM.WireGuardPeer.new()
        peer.set_public_key(base64.b64encode(b'y' * 32).decode(), False)
        peer.set_endpoint('93.118.43.10:51820', False)
        peer.append_allowed_ip(login.SPLIT_SUBNET, False)
        wg.append_peer(peer)
        connection.add_setting(wg)
        self.remote = connection
        self.remote.get_secrets = MagicMock(return_value=None)
        self.remote.update_secrets = MagicMock()
        self.remote.commit_changes = MagicMock(return_value=True)
        self.client = MagicMock()
        self.client.get_connections.return_value = [self.remote]
        self.applied = NM.SimpleConnection.new_clone(connection)
        self.device = self.client.get_device_by_iface.return_value
        self.device.get_applied_connection.return_value = (self.applied, 5)
        self.device.reapply.return_value = True
        p = patch.object(login, 'resolve_library_routes', return_value=self.routes)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(NM.Client, 'new', return_value=self.client)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(login, 'log')
        p.start()
        self.addCleanup(p.stop)

    def allowed(self, connection):
        peer = connection.get_setting(self.NM.SettingWireGuard).get_peer(0)
        return [peer.get_allowed_ip(i) for i in range(peer.get_allowed_ips_len())]

    def test_persists_exact_routes_and_metadata_without_changing_private_key(self):
        self.assertEqual(login.refresh_library_routes(), self.routes)
        self.assertEqual(self.allowed(self.remote), [login.SPLIT_SUBNET, *self.routes])
        self.assertEqual(login.library_routes_from_profile(self.remote), self.routes)
        self.remote.update_secrets.assert_called_once()
        self.remote.commit_changes.assert_called_once_with(True, None)
        self.device.reapply.assert_not_called()
        self.assertEqual(self.remote.get_setting(self.NM.SettingWireGuard).get_property('private-key'),
                         self.applied.get_setting(self.NM.SettingWireGuard).get_property('private-key'))

    def test_live_reapply_preserves_active_endpoint_when_saved_server_differs(self):
        setting = self.applied.get_setting(self.NM.SettingWireGuard)
        peer = self.NM.WireGuardPeer.new_clone(setting.get_peer(0), True)
        peer.set_endpoint('93.118.43.11:51820', False)
        setting.set_peer(peer, 0)
        login.refresh_library_routes(active=True)
        sent, version, flags, cancellable = self.device.reapply.call_args.args
        self.assertEqual(sent.get_setting(self.NM.SettingWireGuard).get_peer(0).get_endpoint(),
                         '93.118.43.11:51820')
        self.assertEqual(self.allowed(sent), [login.SPLIT_SUBNET, *self.routes])
        self.assertEqual((version, flags), (5, 0))
        self.assertEqual(self.remote.get_setting(self.NM.SettingWireGuard).get_peer(0).get_endpoint(),
                         '93.118.43.10:51820')

    def test_active_refresh_retains_cached_service_addresses_until_reconnect(self):
        login.refresh_library_routes()
        old = set(self.routes)
        self.applied = self.NM.SimpleConnection.new_clone(self.remote)
        self.device.get_applied_connection.return_value = (self.applied, 5)
        self.routes[:] = ['66.22.131.132/32']
        routes = login.refresh_library_routes(active=True)
        self.assertEqual(set(routes), old | {'66.22.131.132/32'})
        self.assertEqual(set(self.allowed(self.remote)), {login.SPLIT_SUBNET, *routes})
        self.assertEqual(set(self.allowed(self.device.reapply.call_args.args[0])),
                         {login.SPLIT_SUBNET, *routes})

    def test_saved_and_live_dns_generations_are_validated_separately(self):
        login.refresh_library_routes()
        self.applied = self.NM.SimpleConnection.new_clone(self.remote)
        self.device.get_applied_connection.return_value = (self.applied, 5)
        old = set(self.routes)
        self.routes[:] = ['66.22.131.132/32']
        login.refresh_library_routes()  # A saved update without live reapply.
        routes = login.refresh_library_routes(active=True)
        self.assertEqual(set(routes), old | {'66.22.131.132/32'})
        live = self.device.reapply.call_args.args[0]
        self.assertEqual(login.library_routes_from_profile(live), routes)
        self.assertEqual(set(self.allowed(live)), {login.SPLIT_SUBNET, *routes})

    def test_failed_reapply_restores_saved_and_live_routes(self):
        self.device.reapply.side_effect = [RuntimeError('failure'), True]
        with self.assertRaisesRegex(login.LoginError, 'previous settings restored'):
            login.refresh_library_routes(active=True)
        self.assertEqual(self.allowed(self.remote), [login.SPLIT_SUBNET])
        self.assertEqual(login.library_routes_from_profile(self.remote), [])
        self.assertEqual(self.remote.commit_changes.call_count, 2)
        restored = self.device.reapply.call_args.args[0]
        self.assertEqual(self.allowed(restored), [login.SPLIT_SUBNET])

    def test_commit_failure_rolls_back_without_leaving_new_routes(self):
        self.remote.commit_changes.side_effect = [RuntimeError('failure'), True]
        with self.assertRaisesRegex(login.LoginError, 'previous settings restored'):
            login.refresh_library_routes()
        self.assertEqual(self.allowed(self.remote), [login.SPLIT_SUBNET])

    def test_changed_dns_replaces_old_catalog_addresses(self):
        login.refresh_library_routes()
        old = set(self.routes)
        self.routes[:] = ['3.163.218.55/32']
        login.refresh_library_routes()
        self.assertEqual(self.allowed(self.remote), [login.SPLIT_SUBNET, *self.routes])
        self.assertTrue(old.isdisjoint(self.allowed(self.remote)))
        self.assertEqual(login.library_routes_from_profile(self.remote), self.routes)

    def test_repeated_same_addresses_are_a_noop(self):
        login.refresh_library_routes()
        self.remote.commit_changes.reset_mock()
        login.refresh_library_routes()
        self.remote.commit_changes.assert_not_called()

    def test_unknown_routes_rejected_without_commit_or_reapply(self):
        setting = self.remote.get_setting(self.NM.SettingWireGuard)
        peer = self.NM.WireGuardPeer.new_clone(setting.get_peer(0), True)
        peer.append_allowed_ip('0.0.0.0/0', False)
        setting.set_peer(peer, 0)
        with self.assertRaisesRegex(login.LoginError, 'supported split profile'):
            login.refresh_library_routes(active=True)
        self.remote.commit_changes.assert_not_called()
        self.device.reapply.assert_not_called()

    def test_server_tester_accepts_catalog_routes_and_probe_excludes_them(self):
        import vpn_servers
        login.refresh_library_routes()
        backend = vpn_servers.SplitTunnel()
        with patch.object(backend, 'remote', return_value=self.remote):
            server = backend.current()
        self.assertEqual(server.address, '93.118.43.10')
        self.remote.get_setting_ip4_config().add_address(self.NM.IPAddress.new(2, '10.0.0.2', 32))
        candidate = backend.candidate(self.remote, server, probe_route=True)
        self.assertEqual(self.allowed(candidate), [vpn_servers.TARGET + '/32'])
        self.assertEqual(login.library_routes_from_profile(candidate), [])
        self.assertEqual(self.allowed(self.remote), [login.SPLIT_SUBNET, *self.routes])


if __name__ == "__main__":
    unittest.main()
