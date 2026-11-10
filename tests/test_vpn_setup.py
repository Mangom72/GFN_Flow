"""Replay a cold NM cache refresh without touching actual network settings."""
import base64
import unittest
import weakref
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gi
gi.require_version('NM', '1.0')
from gi.repository import NM, GLib, Gio
import vpn_servers as vpn

KEY = base64.b64encode(b'x' * 32).decode()
SECRET = base64.b64encode(b'y' * 32).decode()
SERVER = vpn.Server('jp1000.nordvpn.com', '93.118.43.11', KEY, 'Tokyo')


def profile():
    result = NM.SimpleConnection.new()
    connection = NM.SettingConnection.new()
    connection.set_property('id', 'test-profile')
    connection.set_property('type', 'wireguard')
    connection.set_property('uuid', '11111111-2222-4333-8444-555555555555')
    connection.set_property('interface-name', 'gfn-jp')
    connection.set_property('autoconnect', True)
    result.add_setting(connection)
    wireguard = NM.SettingWireGuard.new()
    peer = NM.WireGuardPeer.new()
    peer.set_public_key(KEY, False)
    peer.set_endpoint('93.118.43.10:51820', False)
    peer.append_allowed_ip(vpn.SUBNET, False)
    wireguard.append_peer(peer)
    result.add_setting(wireguard)
    ip4 = NM.SettingIP4Config.new()
    ip4.set_property('method', 'manual')
    ip4.set_property('never-default', True)
    ip4.add_address(NM.IPAddress.new(2, '10.0.0.2', 32))
    result.add_setting(ip4)
    ip6 = NM.SettingIP6Config.new()
    ip6.set_property('method', 'disabled')
    ip6.set_property('never-default', True)
    result.add_setting(ip6)
    return result


class CachedRemote:
    """Deliver an old cache signal just as a synchronous commit pumps events."""
    def __init__(self, refresh=True):
        self.data = profile()
        self.original = NM.SimpleConnection.new_clone(self.data)
        self.saved = NM.SimpleConnection.new_clone(self.data)
        self.refresh = refresh

    def get_setting(self, kind):
        return self.data.get_setting(kind)

    def get_setting_connection(self):
        return self.data.get_setting_connection()

    def get_setting_ip4_config(self):
        return self.data.get_setting_ip4_config()

    def get_setting_ip6_config(self):
        return self.data.get_setting_ip6_config()

    def get_secrets(self, *_):
        return GLib.Variant('a{sa{sv}}', {'wireguard': {'private-key': GLib.Variant('s', SECRET)}})

    def to_dbus(self, flags):
        return self.data.to_dbus(flags)

    def update_secrets(self, *args):
        return self.data.update_secrets(*args)

    def commit_changes(self, *_):
        if self.refresh:
            self.data = NM.SimpleConnection.new_clone(self.original)
            self.refresh = False
        self.saved = NM.SimpleConnection.new_clone(self.data)
        return True

    def get_path(self):
        return '/org/freedesktop/NetworkManager/Settings/999'

    def get_uuid(self):
        return self.data.get_uuid()


class FakeBus:
    def __init__(self, remote):
        self.remote, self.calls = remote, []

    def call_sync(self, *args):
        self.calls.append(args)
        # A serialized candidate stays intact even when the remote cache changes.
        self.remote.data = NM.SimpleConnection.new_clone(self.remote.original)
        self.remote.saved = NM.SimpleConnection.new_from_dbus(args[4].get_child_value(0))
        self.remote.data = NM.SimpleConnection.new_clone(self.remote.saved)
        return (GLib.Variant('(oa{sv})', (self.remote.get_path(), {})) if args[3] == 'AddConnection2'
                else GLib.Variant('(a{sv})', ({},)))


class ColdSetupTests(unittest.TestCase):
    def backend(self, refresh=True):
        remote = CachedRemote(refresh)
        backend = object.__new__(vpn.SplitTunnel)
        backend.NM, backend.GLib, backend.Gio = NM, GLib, Gio
        backend.interface, backend.name = 'gfnprobe12345', 'auto-macro-vpn-probe-12345678'
        backend.connection = 'gfn-jp'
        backend.context = GLib.MainContext.new()
        bus = FakeBus(remote)
        backend.client = SimpleNamespace(get_dbus_connection=lambda: bus)
        backend.new_client = lambda: backend.client
        backend.remote = lambda _: remote
        return backend, remote, bus

    def test_first_activation_keeps_original_vpn_and_uses_separate_interface(self):
        backend, remote, _ = self.backend()
        active = {'interface': 'gfn-jp', 'original_vpn': True}
        def command(*args, **kwargs):
            if args[:2] == ('connection', 'up'):
                active['interface'] = remote.saved.get_setting_connection().get_interface_name()
                if active['interface'] == 'gfn-jp':
                    active['original_vpn'] = False
        backend.command = command
        with patch.object(vpn, 'route_device', side_effect=lambda target: (
                'eth0' if target == '1.1.1.1' else active['interface'])):
            backend.activate(SERVER)
        self.assertTrue(active['original_vpn'])
        self.assertEqual(active['interface'], backend.interface)

    def test_candidate_keeps_secrets_but_only_adds_test_host_route(self):
        backend, remote, bus = self.backend()
        backend.change_peer(remote, SERVER, probe_route=True)
        saved = remote.saved
        self.assertEqual(saved.get_setting_connection().get_interface_name(), backend.interface)
        self.assertFalse(saved.get_setting_connection().get_autoconnect())
        wg = saved.get_setting(NM.SettingWireGuard)
        self.assertEqual(wg.get_private_key(), SECRET)
        peer = wg.get_peer(0)
        self.assertEqual(peer.get_endpoint(), SERVER.address + ':51820')
        self.assertEqual([peer.get_allowed_ip(i) for i in range(peer.get_allowed_ips_len())], [vpn.TARGET + '/32'])
        self.assertEqual(saved.get_setting_ip4_config().get_route_metric(), 1)
        self.assertTrue(saved.get_setting_ip4_config().get_never_default())
        self.assertEqual(bus.calls[0][3], 'Update2')
        backend, remote, bus = self.backend()
        backend.change_peer(remote, SERVER, persistent=True)
        self.assertEqual(remote.saved.get_setting_connection().get_interface_name(), 'gfn-jp')
        self.assertTrue(remote.saved.get_setting_connection().get_autoconnect())
        wg = remote.saved.get_setting(NM.SettingWireGuard)
        self.assertEqual(wg.get_private_key(), SECRET)
        self.assertEqual(wg.get_peer(0).get_allowed_ip(0), vpn.SUBNET)
        self.assertEqual(bus.calls[0][4].get_child_value(1).get_uint32(), int(NM.SettingsUpdate2Flags.TO_DISK))

    def test_create_profile_is_atomic_and_remembers_whether_original_vpn_was_on(self):
        for was_active in (False, True):
            backend, remote, bus = self.backend()
            backend.created = False
            backend.current = lambda: SERVER
            backend.profile_active = Mock(return_value=was_active)
            backend.cleanup_stale = Mock()
            backend.command = Mock()
            backend.prepare()
            self.assertEqual(backend.original_was_active, was_active)
            self.assertEqual(backend.original_uuid, '11111111-2222-4333-8444-555555555555')
            self.assertEqual(bus.calls[0][3], 'AddConnection2')
            saved = remote.saved.get_setting_connection()
            self.assertEqual(saved.get_id(), backend.name)
            self.assertEqual(saved.get_interface_name(), backend.interface)
            self.assertFalse(saved.get_autoconnect())
            self.assertTrue(backend.created)
            backend.command.assert_not_called()

    def test_invalid_profile_is_rejected_before_any_activation(self):
        backend, remote, _ = self.backend()
        backend.change_peer = Mock()  # Replay the observed unchanged clone.
        backend.command = Mock()
        with self.assertRaisesRegex(ValueError, 'not isolated'):
            backend.activate(SERVER)
        self.assertFalse(any(call.args[:2] == ('connection', 'up') for call in backend.command.call_args_list))


class VpnPreservationTests(unittest.TestCase):
    def backend(self, was_active, active):
        backend = object.__new__(vpn.SplitTunnel)
        backend.interface, backend.name = 'gfnprobe12345', 'auto-macro-vpn-probe-12345678'
        backend.created = True
        backend.original_uuid = 'original-uuid'
        backend.original_was_active = was_active
        backend.profile_active = Mock(side_effect=active)
        backend.command = Mock()
        return backend

    def test_active_vpn_is_kept_without_reconnect_and_off_vpn_is_not_connected(self):
        for was_active in (False, True):
            backend = self.backend(was_active, [True])
            with patch.object(vpn, 'route_device', return_value='eth0'):
                backend.cleanup()
            backend.command.assert_called_once_with('connection', 'delete', 'id', backend.name)

    def test_lost_preexisting_vpn_is_restored_after_probe_cleanup(self):
        backend = self.backend(True, [False, True])
        with patch.object(vpn, 'route_device', return_value='eth0'):
            backend.cleanup()
        self.assertEqual(backend.command.call_args_list[-1].args, ('connection', 'up', 'uuid', 'original-uuid'))


    def test_cleanup_failure_still_restores_preexisting_vpn(self):
        backend = self.backend(True, [False, True])
        backend.command.side_effect = [ValueError('delete failed'), None]
        with self.assertRaisesRegex(ValueError, 'delete failed'):
            backend.cleanup()
        self.assertEqual(backend.command.call_args_list[-1].args, ('connection', 'up', 'uuid', 'original-uuid'))

class ClientLifetimeTests(unittest.TestCase):
    def test_prepare_keeps_original_profile_client_alive_until_closed(self):
        class OwnedRemote(CachedRemote):
            def __init__(self, client):
                super().__init__()
                self.owner = weakref.ref(client)

            def get_id(self):
                return 'gfn-jp'

            def get_secrets(self, *args):
                if self.owner() is None:
                    raise RuntimeError('NMClient owner was released')
                return super().get_secrets(*args)

        class Client:
            def __init__(self):
                self.remote = OwnedRemote(self)

            def get_connections(self):
                return [self.remote]

            def get_active_connections(self):
                return []

            def get_context_busy_watcher(self):
                return SimpleNamespace(weak_ref=lambda: lambda: None)

        backend = vpn.SplitTunnel()
        backend.cleanup_stale = Mock()
        def current():
            backend.remote('gfn-jp')  # Replaces self.client before the original is used.
            return SERVER
        backend.current = current
        backend.write_connection = Mock()
        references = []
        def create_client(*_):
            client = Client()
            references.append(weakref.ref(client))
            return client
        try:
            with patch.object(NM.Client, 'new', side_effect=create_client):
                backend.prepare()
            backend.write_connection.assert_called_once()
        finally:
            backend.close()
        self.assertTrue(all(reference() is None for reference in references))
