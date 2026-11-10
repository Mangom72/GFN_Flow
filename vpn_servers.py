"""Bounded NordVPN JP route tests. No credential exports or global VPN changes."""
import base64
from contextlib import ExitStack
from dataclasses import asdict, dataclass
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid

from detection import candidates, login
from input_activity import macro_input_active
from macro_library import MacroLibrary
from tool_status import vpn_lock

TARGET = '66.22.144.44'
SUBNET = '66.22.144.0/24'
SAMPLES = 8
API = 'https://api.nordvpn.com/v1/servers/'
STATE = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'auto-macro'
if sys.platform == 'win32':
    from windows_host import STATE
REPORT = STATE / 'vpn-servers.json'


class SearchCancelled(Exception):
    pass


@dataclass(frozen=True)
class Server:
    hostname: str
    address: str
    public_key: str
    city: str = ''
    load: int = 0


def api_json(path, query=None):
    url = API + path + ('?' + urllib.parse.urlencode(query) if query else '')
    request = urllib.request.Request(url, headers={'User-Agent': 'MacroStudio/1.0'})
    with urllib.request.urlopen(request, timeout=12) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError('VPN server response is too large.')
    return json.loads(data)


def parse_server(row):
    """Reject incomplete/non-Japanese/unsafe API data before it reaches NM."""
    try:
        country = row['locations'][0]['country']
        technology = next(t for t in row['technologies'] if t['identifier'] == 'wireguard_udp')
        key = next(m['value'] for m in technology['metadata'] if m['name'] == 'public_key')
        address = str(ipaddress.IPv4Address(row['station']))
        if (country['code'] != 'JP' or row['status'] != 'online'
                or technology.get('pivot', {}).get('status') != 'online'
                or not re.fullmatch(r'jp\d+\.nordvpn\.com', row['hostname'])
                or len(base64.b64decode(key, validate=True)) != 32
                or not ipaddress.ip_address(address).is_global):
            return None
        return Server(row['hostname'], address, key, country['city']['name'], int(row['load']))
    except (KeyError, IndexError, StopIteration, TypeError, ValueError):
        return None


def recommendations():
    japan = next(c for c in api_json('countries') if c.get('code') == 'JP')
    # City-id filters are silently ignored. Coordinate-based recommendations
    # work; also verify each returned city rather than trusting API filtering.
    servers = []
    for city in japan['cities']:
        if city.get('identifier') not in ('jp_tokyo', 'jp_osaka'):
            continue
        rows = api_json('recommendations', {
            'filters[country_id]': japan['id'],
            'filters[servers_technologies][identifier]': 'wireguard_udp',
            'coordinates[latitude]': city['latitude'],
            'coordinates[longitude]': city['longitude'], 'limit': 8})
        pool = [server for row in rows if (server := parse_server(row))]
        group = [s for s in pool if s.city == city['name']]
        servers.extend(group[:2])
        if not group:
            raise ValueError('No Japanese VPN servers are available.')
    if {s.city for s in servers} != {'Tokyo', 'Osaka'}:
        raise ValueError('No Japanese VPN servers are available.')
    return servers


def ensure_idle(profiles, *, allow_in_game=False):
    if macro_input_active():
        raise ValueError('Finish recording or playback before testing VPN servers.')
    # Interactive callers warn before opting into temporary in-game test routes.
    if not allow_in_game and candidates(login.all_windows(), profiles):
        raise ValueError('Close the GFN game before testing VPN servers.')


def check_cancel(cancel):
    if cancel.is_set():
        raise SearchCancelled()


def route_info(address, interface=None):
    command = ['ip', '-j', 'route', 'get', address]
    if interface:
        command.extend(['oif', interface])
    result = subprocess.run(command, check=True,
                            capture_output=True, text=True, timeout=3)
    return json.loads(result.stdout)[0]


def route_device(address, interface=None):
    if sys.platform == 'win32':
        from windows_vpn import interface_for
        return interface_for(address, interface)
    return route_info(address, interface)['dev']


def resolve_probe_target():
    """Snapshot one current production-control IPv4 for a complete measurement."""
    host = login.CONTROL_HOSTS[0]
    if sys.platform == 'win32':
        try:
            return login.validate_library_routes(sorted({
                row[4][0] + '/32' for row in socket.getaddrinfo(host, 443, socket.AF_INET)
            }))[0].split('/')[0]
        except (OSError, IndexError, login.LoginError):
            raise ValueError('Could not resolve the GFN control endpoint.') from None
    try:
        result = subprocess.run(['getent', 'ahostsv4', host], capture_output=True,
                                text=True, timeout=8, check=True)
        routes = login.validate_library_routes(list({line.split()[0] + '/32'
                                                    for line in result.stdout.splitlines() if line.strip()}))
        return routes[0].split('/')[0]
    except (OSError, subprocess.SubprocessError, login.LoginError):
        raise ValueError('Could not resolve the GFN control endpoint.') from None


def probe(interface, cancel, guard, samples=SAMPLES, bound=False, *, target=TARGET):
    """TCP handshake timings, not ICMP/UDP packet loss or streaming throughput."""
    times = []
    for _ in range(samples):
        check_cancel(cancel)
        guard()
        if route_device(target, interface if bound else None) != interface:
            raise ValueError('The test route did not use the VPN tunnel.')
        try:
            if bound:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
                    try:
                        if sys.platform == 'win32':
                            import struct
                            connection.setsockopt(socket.IPPROTO_IP, 31, struct.pack('!I', int(interface)))
                        else:
                            connection.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b'\0')
                    except OSError:
                        raise ValueError('Direct connection could not be measured.') from None
                    connection.settimeout(1.5)
                    started = time.monotonic()
                    connection.connect((target, 443))
                    times.append((time.monotonic() - started) * 1000)
            else:
                started = time.monotonic()
                with socket.create_connection((target, 443), timeout=1.5):
                    times.append((time.monotonic() - started) * 1000)
        except OSError:
            pass
        if route_device(target, interface if bound else None) != interface:
            raise ValueError('The VPN test route changed during measurement.')
        if cancel.wait(.15):
            raise SearchCancelled()
    return dict(samples=samples, failures=samples - len(times),
                latency=round(statistics.median(times), 1) if times else None,
                variation=round(statistics.pstdev(times), 1) if times else None)


def rank(row):
    return row['failures'] / row['samples'], row['latency'] + row['variation'], row['latency']


def current_latency(cancel):
    """Measure the current GFN route only; never create or switch a tunnel."""
    check_cancel(cancel)
    target = resolve_probe_target()
    interface = route_device(target)
    if isinstance(interface, str) and interface.startswith('gfnprobe'):
        raise ValueError('GFN latency unavailable')
    metrics = probe(interface, cancel, lambda: None, samples=3, target=target)
    if metrics['latency'] is None:
        raise ValueError('GFN latency unavailable')
    return metrics['latency']


class SplitTunnel:
    """Test on one temporary /32 route; keep the original tunnel running."""
    target = TARGET  # Explicit default for isolated callers and existing tests.

    def __init__(self, *, target=TARGET):
        self.target = str(ipaddress.IPv4Address(target))
        if not ipaddress.IPv4Address(self.target).is_global:
            raise ValueError('Invalid GFN control endpoint.')
        import gi
        gi.require_version('NM', '1.0')
        from gi.repository import NM, GLib, Gio
        self.NM, self.GLib, self.Gio = NM, GLib, Gio
        self.context = GLib.MainContext.new()
        self.clients = []
        self.watchers = []
        self.connection = login.SPLIT_CONNECTION
        self.name = 'auto-macro-vpn-probe-' + uuid.uuid4().hex[:8]
        self.interface = 'gfnprobe' + uuid.uuid4().hex[:5]
        self.created = False
        self.original_uuid = None
        self.original_was_active = False

    def new_client(self):
        # A worker must never borrow GTK's running, main-thread-only context.
        self.context.push_thread_default()
        try:
            self.client = self.NM.Client.new(None)
            # RemoteConnection objects only weakly reference their NMClient.
            # Earlier profiles remain in use while later lookups create clients.
            self.clients.append(self.client)
            self.watchers.append(self.client.get_context_busy_watcher().weak_ref())
            while self.context.pending():
                self.context.iteration(False)
            return self.client
        finally:
            self.context.pop_thread_default()

    def close(self):
        self.context.push_thread_default()
        try:
            self.client = None
            self.clients.clear()
            deadline = time.monotonic() + 2
            while self.context.pending() or any(watcher() is not None for watcher in self.watchers):
                if time.monotonic() >= deadline:
                    break
                if self.context.pending():
                    self.context.iteration(False)
                else:
                    time.sleep(.001)
            self.watchers.clear()
        finally:
            self.context.pop_thread_default()

    def command(self, *args, check=True):
        result = subprocess.run(['nmcli', '-w', '15', *args], text=True,
                                capture_output=True, timeout=20)
        if check and result.returncode:
            raise ValueError('NetworkManager could not update the VPN connection.')
        return result

    def remote(self, name):
        self.new_client()
        remote = next((c for c in self.client.get_connections() if c.get_id() == name), None)
        if remote is None:
            raise ValueError('The GFN split VPN profile is unavailable.')
        return remote

    def current(self):
        remote = self.remote(self.connection)
        setting = remote.get_setting(self.NM.SettingWireGuard)
        ip4 = remote.get_setting_ip4_config()
        ip6 = remote.get_setting_ip6_config()
        if (not setting or setting.get_peers_len() != 1 or not ip4.get_never_default()
                or ip6 and not ip6.get_never_default()):
            raise ValueError('A dedicated GFN split VPN profile is required.')
        peer = setting.get_peer(0)
        allowed = [peer.get_allowed_ip(i) for i in range(peer.get_allowed_ips_len())]
        if not login.supported_split_routes(allowed, login.library_routes_from_profile(remote)):
            raise ValueError('The GFN VPN routes differ from the supported split profile.')
        endpoint = peer.get_endpoint()
        address, port = endpoint.rsplit(':', 1)
        if port != '51820' or not ipaddress.ip_address(address).is_global:
            raise ValueError('The current VPN endpoint is unsupported.')
        saved = load_report()
        best = saved.get('best', {})
        hostname = best.get('hostname', address) if best.get('address') == address else address
        # Read only the nonsecret server comment, never credentials from the file.
        if hostname == address and login.SPLIT_CONFIG.exists():
            match = re.search(r'\bjp\d+\.nordvpn\.com\b', login.SPLIT_CONFIG.read_text())
            if match:
                hostname = match.group()
        return Server(hostname, address, peer.get_public_key(), 'Current')

    def direct_route(self):
        self.new_client()
        result = subprocess.run(['ip', '-j', 'route', 'show', 'default'], check=True,
                                capture_output=True, text=True, timeout=3)
        routes = sorted(json.loads(result.stdout), key=lambda row: row.get('metric', 0))
        for default in routes:
            interface = default.get('dev')
            device = self.client.get_device_by_iface(interface) if interface else None
            if device and device.get_device_type() in (self.NM.DeviceType.ETHERNET, self.NM.DeviceType.WIFI):
                route = route_info(self.target, interface)
                return {key: route.get(key, '') for key in ('dev', 'gateway', 'prefsrc')} | {'interface': interface}
        raise ValueError('Direct connection could not be measured.')

    def candidate(self, remote, server, *, probe_route=False):
        NM = self.NM
        # Work on a detached snapshot. D-Bus cache notifications can replace a
        # RemoteConnection's settings while a synchronous call dispatches events.
        candidate = NM.SimpleConnection.new_from_dbus(remote.to_dbus(NM.ConnectionSerializationFlags.ALL))
        # Explicitly merge existing secrets so updating public peer data cannot
        # clear the saved private key. Secrets never enter argv, reports or logs.
        secrets = remote.get_secrets('wireguard', None)
        if secrets is not None:
            candidate.update_secrets('wireguard', secrets)
        setting = candidate.get_setting(NM.SettingWireGuard)
        if not setting.get_private_key():
            raise ValueError('The VPN private key is unavailable.')
        peer = NM.WireGuardPeer.new_clone(setting.get_peer(0), True)
        peer.set_public_key(server.public_key, False)
        peer.set_endpoint(server.address + ':51820', False)
        if probe_route:
            peer.clear_allowed_ips()
            peer.append_allowed_ip(self.target + '/32', False)
            user = candidate.get_setting(NM.SettingUser)
            if user:
                user.set_data(login.LIBRARY_ROUTE_KEY, None)
            connection = candidate.get_setting_connection()
            connection.set_property('interface-name', self.interface)
            connection.set_property('autoconnect', False)
            ip4 = candidate.get_setting_ip4_config()
            ip4.clear_routes()
            # Production now also has a /32 for this endpoint. A lower metric
            # makes the isolated probe win without removing that live route.
            ip4.set_property('route-metric', 1)
            ip4.set_property('never-default', True)
            ip4.set_property('ignore-auto-dns', True)
            ip4.clear_dns()
            ip4.clear_dns_searches()
            addresses = [ip4.get_address(i).get_address() for i in range(ip4.get_num_addresses())]
            ip4.clear_addresses()
            for address in addresses:
                ip4.add_address(NM.IPAddress.new(socket.AF_INET, address, 32))
            ip6 = candidate.get_setting_ip6_config()
            if ip6:
                ip6.set_property('method', 'disabled')
                ip6.set_property('never-default', True)
                ip6.clear_routes()
                ip6.clear_dns()
        setting.set_peer(peer, 0)
        return candidate

    def write_connection(self, path, method, candidate, flags):
        GLib, Gio = self.GLib, self.Gio
        settings = candidate.to_dbus(self.NM.ConnectionSerializationFlags.ALL)
        parameters = GLib.Variant.new_tuple(settings, GLib.Variant('u', int(flags)), GLib.Variant('a{sv}', {}))
        reply = '(oa{sv})' if method == 'AddConnection2' else '(a{sv})'
        try:
            self.new_client().get_dbus_connection().call_sync(
                'org.freedesktop.NetworkManager', path,
                'org.freedesktop.NetworkManager.Settings' + ('' if method == 'AddConnection2' else '.Connection'),
                method, parameters, GLib.VariantType.new(reply), Gio.DBusCallFlags.NONE, 15000, None)
        except self.GLib.Error:
            raise ValueError('NetworkManager could not configure the VPN connection.') from None

    def change_peer(self, remote, server, *, probe_route=False, persistent=False):
        candidate = self.candidate(remote, server, probe_route=probe_route)
        flags = self.NM.SettingsUpdate2Flags.TO_DISK if persistent else self.NM.SettingsUpdate2Flags.IN_MEMORY
        if probe_route:
            flags |= self.NM.SettingsUpdate2Flags.BLOCK_AUTOCONNECT
        self.write_connection(remote.get_path(), 'Update2', candidate, flags)

    def profile_active(self, identity):
        return any(active.get_uuid() == identity and active.get_state() == self.NM.ActiveConnectionState.ACTIVATED
                   for active in self.new_client().get_active_connections())

    def prepare(self):
        original = self.remote(self.connection)
        self.original_uuid = original.get_uuid()
        self.original_was_active = self.profile_active(self.original_uuid)
        self.cleanup_stale()
        # Export a fully isolated profile in one operation. A raw clone inherits
        # gfn-jp's interface/autoconnect and can replace the user's active VPN.
        candidate = self.candidate(original, self.current(), probe_route=True)
        connection = candidate.get_setting_connection()
        connection.set_property('id', self.name)
        connection.set_property('uuid', str(uuid.uuid4()))
        flags = self.NM.SettingsAddConnection2Flags.IN_MEMORY | self.NM.SettingsAddConnection2Flags.BLOCK_AUTOCONNECT
        self.write_connection('/org/freedesktop/NetworkManager/Settings', 'AddConnection2', candidate, flags)
        self.created = True

    def cleanup_stale(self):
        self.new_client()
        for remote in self.client.get_connections():
            connection = remote.get_setting_connection()
            if (re.fullmatch(r'auto-macro-vpn-probe-[0-9a-f]{8}', remote.get_id())
                    and remote.get_connection_type() == 'wireguard'
                    and re.fullmatch(r'gfnprobe[0-9a-f]{5}', connection.get_interface_name() or '')):
                self.command('connection', 'delete', 'uuid', remote.get_uuid())

    def activate(self, server):
        self.command('connection', 'down', 'id', self.name, check=False)
        self.change_peer(self.remote(self.name), server, probe_route=True)
        self.verify_probe(self.remote(self.name), server)
        normal = route_device('1.1.1.1')
        self.command('connection', 'up', 'id', self.name)
        if route_device(self.target) != self.interface or route_device('1.1.1.1') != normal:
            raise ValueError('VPN split-route verification failed.')

    def verify_probe(self, remote, server):
        connection = remote.get_setting_connection()
        setting = remote.get_setting(self.NM.SettingWireGuard)
        ip4 = remote.get_setting_ip4_config()
        peer = setting.get_peer(0) if setting and setting.get_peers_len() == 1 else None
        if (connection.get_interface_name() != self.interface or connection.get_autoconnect()
                or not ip4 or not ip4.get_never_default() or not peer
                or peer.get_endpoint() != server.address + ':51820' or peer.get_public_key() != server.public_key
                or [peer.get_allowed_ip(i) for i in range(peer.get_allowed_ips_len())] != [self.target + '/32']):
            raise ValueError('The VPN test tunnel was not isolated. Activation was cancelled.')

    def cleanup(self):
        try:
            if self.created:
                # Deleting also deactivates. Failure is surfaced; never claim cleanup.
                self.command('connection', 'delete', 'id', self.name)
                self.created = False
                deadline = time.monotonic() + 3
                while route_device(self.target) == self.interface:
                    if time.monotonic() >= deadline:
                        raise ValueError('The temporary VPN route could not be removed.')
                    time.sleep(.05)
        finally:
            # Preserve pre-scan VPN-on state on success, cancellation and failure.
            # Never connect a VPN that was off when the user started the scan.
            if self.original_was_active and not self.profile_active(self.original_uuid):
                self.command('connection', 'up', 'uuid', self.original_uuid)
                if not self.profile_active(self.original_uuid):
                    raise ValueError('Could not restore the previous VPN connection.')

    def save(self, server):
        self.change_peer(self.remote(self.connection), server, persistent=True)


def load_report(path=None):
    try:
        report = json.loads((path or REPORT).read_text())
        if (not isinstance(report, dict) or report.get('version') not in (1, 2)
                or not math.isfinite(report['tested_at']) or report['tested_at'] <= 0
                or not isinstance(report['results'], list) or len(report['results']) > 8):
            return {}
        if report['version'] == 2 and not isinstance(report.get('direct'), dict):
            return {}
        records = [report['best'], *report['results']]
        if report['version'] == 2:
            records.append(report['direct'])
        for row in records:
            if (not isinstance(row['hostname'], str) or not row['hostname']
                    or not isinstance(row['city'], str)
                    or not 0 < row['samples'] <= 100 or not 0 <= row['failures'] <= row['samples']
                    or any(value is not None and (not math.isfinite(value) or value < 0)
                           for value in (row['latency'], row['variation']))):
                return {}
        return report
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def optimize(profiles, progress=lambda *_: None, cancel=None, backend=None,
             provider=recommendations, report_path=None, *, allow_in_game=False):
    cancel = cancel or threading.Event()
    guard = lambda: ensure_idle(profiles, allow_in_game=allow_in_game)
    with vpn_lock(), ExitStack() as resources:
        guard()
        check_cancel(cancel)
        progress('fetch', 0, 0, '', [])
        servers = provider()
        check_cancel(cancel)
        guard()
        target = resolve_probe_target() if backend is None else getattr(backend, 'target', TARGET)
        if backend is None and sys.platform == 'win32':
            from windows_vpn import SplitTunnel as WindowsSplitTunnel
            backend = WindowsSplitTunnel(target=target)
        backend = backend or SplitTunnel(target=target)
        if close := getattr(backend, 'close', None):
            resources.callback(close)
        original = backend.current()
        unique = {original.address: original}
        for server in servers:
            unique.setdefault(server.address, server)
        servers = list(unique.values())
        rows = []
        total = len(servers) + 1
        progress('test', 1, total, 'Without VPN', [])
        check_cancel(cancel)
        direct = dict(kind='direct', hostname='Without VPN', address='', public_key='', city='Direct', load=0)
        try:
            direct.update(backend.direct_route())
            direct.update(probe(direct['interface'], cancel, guard, bound=True, target=target))
        except (ValueError, OSError, subprocess.SubprocessError):
            direct.update(samples=SAMPLES, failures=SAMPLES, latency=None, variation=None,
                          error='Direct connection could not be measured.')
        rows.append(direct)
        try:
            backend.prepare()
            for index, server in enumerate(servers, 2):
                check_cancel(cancel)
                guard()
                progress('test', index, total, server.hostname, rows[:])
                check_cancel(cancel)
                backend.activate(server)
                metrics = probe(backend.interface, cancel, guard, target=target)
                rows.append({**asdict(server), **metrics, 'kind': 'vpn'})
        finally:
            backend.cleanup()
        check_cancel(cancel)
        guard()
        valid = [r for r in rows if r['kind'] == 'vpn' and r['latency'] is not None and r['failures'] <= r['samples'] // 4]
        if not valid:
            raise ValueError('No VPN server passed the connection test. Previous server kept.')
        winner = min(valid, key=rank)
        report = dict(version=2, backend='split', tested_at=time.time(), target=target + ':443', target_host=login.CONTROL_HOSTS[0], method='tcp-connect', direct=direct,
                      best=winner, results=sorted(rows, key=lambda r: rank(r) if r['latency'] is not None else (1, float('inf'), float('inf'))))
        report['direct_fastest'] = login.direct_connection_is_fastest(report)
        current = backend.current()
        if (current.address, current.public_key) != (original.address, original.public_key):
            raise ValueError('VPN settings changed during the test. No server was saved.')
        check_cancel(cancel)
        guard()
        try:
            backend.save(Server(**{key: winner[key] for key in Server.__dataclass_fields__}))
            check_cancel(cancel)
            MacroLibrary.atomic_write(report_path or REPORT, report)
        except Exception:
            backend.save(original)
            raise
        progress('done', total, total, winner['hostname'], report['results'])
        return report
