"""WireGuard for Windows backend for the original split-tunnel controls."""
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import threading
import time

from detection import login
from windows_host import CONFIG
from windows_input import api
from windows_vpn_profiles import FILE_LIMIT, endpoint_ipv4, parse_config

WIREGUARD = Path(os.environ['ProgramFiles']) / 'WireGuard' / 'wireguard.exe'
PROFILE = CONFIG / 'vpn' / 'gfn-jp.conf'
MANAGED = PROFILE.with_name('GFNFlow-Japan.conf')
PROBE = PROFILE.with_name('GFNFlow-Probe.conf')
SERVICE_WORKER = False
_operation = threading.RLock()
POWERSHELL = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


class ServiceStatus(c.Structure):
    _fields_ = [(key, w.DWORD) for key in
                ('type', 'state', 'accepted', 'exit', 'specific', 'checkpoint', 'wait')]


def service(action, path=MANAGED):
    advapi = c.WinDLL('advapi32', use_last_error=True)
    scm = api(advapi, 'OpenSCManagerW', w.HANDLE, w.LPCWSTR, w.LPCWSTR, w.DWORD)(None, None, 1)
    if not scm:
        raise ValueError('Windows Service Control Manager is unavailable.')
    close = api(advapi, 'CloseServiceHandle', w.BOOL, w.HANDLE)
    try:
        name = 'WireGuardTunnel$' + path.stem
        handle = api(advapi, 'OpenServiceW', w.HANDLE, w.HANDLE, w.LPCWSTR, w.DWORD)(
            scm, name, 4 if action == 'query' else 0x20)
        if not handle:
            if c.get_last_error() == 1060:
                return 0
            raise PermissionError('Access to the managed WireGuard tunnel was denied.')
        try:
            result = ServiceStatus()
            if action == 'query':
                success = api(advapi, 'QueryServiceStatus', w.BOOL, w.HANDLE, c.POINTER(ServiceStatus))(
                    handle, c.byref(result))
            else:
                success = api(advapi, 'ControlService', w.BOOL, w.HANDLE, w.DWORD, c.POINTER(ServiceStatus))(
                    handle, 1, c.byref(result))
            if not success:
                raise PermissionError('Windows did not permit the managed tunnel action.')
            return result.state
        finally:
            close(handle)
    finally:
        close(scm)


def status():
    try:
        if not SERVICE_WORKER:
            from windows_vpn_broker import ready
            if not ready():
                return None
        state = service('query')
        if state == 4:
            return True
        if state in (0, 1) and WIREGUARD.is_file() and (not SERVICE_WORKER or PROFILE.is_file()):
            return False
    except (OSError, ValueError):
        pass
    return None


def read_profile(path=None):
    try:
        with (path or PROFILE).open(encoding='utf-8-sig') as stream:
            return parse_config(stream.read(FILE_LIMIT + 1))
    except OSError:
        raise ValueError('Import a WireGuard profile in VPN settings first.') from None


def save_profile(config, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp')
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as stream:
            config.write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def managed_routes():
    addresses = {
        row[4][0] + '/32'
        for hostname in (login.LIBRARY_HOST, *login.CONTROL_HOSTS)
        for row in socket.getaddrinfo(hostname, 443, socket.AF_INET)
    }
    return [login.SPLIT_SUBNET, *login.validate_library_routes(sorted(addresses))]


def tunnel_config(path, routes, *, peer=None):
    config = read_profile()
    config['Peer']['AllowedIPs'] = ', '.join(routes)
    if peer:
        config['Peer']['Endpoint'] = peer.address + ':51820'
        config['Peer']['PublicKey'] = peer.public_key
        config['Peer'].pop('PresharedKey', None)
    config['Peer']['Endpoint'] = endpoint_ipv4(config['Peer']['Endpoint'])
    save_profile(config, path)


def account_sid():
    result = subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-Command',
                             '[Security.Principal.WindowsIdentity]::GetCurrent().User.Value'],
                            capture_output=True, text=True, check=True, timeout=8,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    sid = result.stdout.strip()
    if not re.fullmatch(r'S-1-(?:\d+-){1,12}\d+', sid):
        raise ValueError('Could not verify the Windows account for the Japan tunnel.')
    return sid


def wireguard(action, path):
    if not WIREGUARD.is_file():
        raise ValueError('Install WireGuard for Windows first.')
    args = [action, str(path) if action == '/installtunnelservice' else path.stem]
    if not SERVICE_WORKER:
        raise ValueError('The VPN service is required for this operation.')
    result = subprocess.run([str(WIREGUARD), *args], timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode:
        raise ValueError(f'WireGuard could not complete the tunnel action ({result.returncode}).')
    if action == '/installtunnelservice' and path == MANAGED:
        sc = Path(os.environ['SystemRoot']) / 'System32/sc.exe'
        try:
            subprocess.run([str(sc), 'config', 'WireGuardTunnel$' + path.stem, 'start=', 'demand'],
                           check=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            wireguard('/uninstalltunnelservice', path)
            raise

def wait_state(path, accepted):
    deadline = time.monotonic() + 8
    while service('query', path) not in accepted:
        if time.monotonic() >= deadline:
            raise ValueError('The WireGuard tunnel did not reach the expected state.')
        time.sleep(.2)


def verify_gfn_route(routes):
    interface = named_interface(MANAGED.stem)
    deadline = time.monotonic() + 6
    targets = [route.split('/')[0] for route in routes if route.endswith('/32')]
    while targets and time.monotonic() < deadline:
        for target in targets:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                if interface_for(target) == interface:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
                        connection.setsockopt(socket.IPPROTO_IP, 31, struct.pack('!I', interface))
                        connection.settimeout(min(1.5, remaining))
                        connection.connect((target, 443))
                        return
            except OSError:
                continue
        time.sleep(.2)
    raise ValueError('The VPN could not reach GeForce NOW. Check the selected WireGuard profile.')


def set_connected(connected, *, peer=None):
    if not SERVICE_WORKER:
        from windows_vpn_broker import command
        if not command(b'1' if connected else b'0'):
            raise ValueError('The VPN service did not complete the request.')
        return
    with _operation:
        state = service('query')
        if connected:
            if state == 4:
                return
            if state not in (0, 1):
                raise ValueError('The VPN tunnel is already changing state.')
            if state == 1:
                wireguard('/uninstalltunnelservice', MANAGED)
                wait_state(MANAGED, (0,))
            routes = managed_routes()
            tunnel_config(MANAGED, routes, peer=peer)
            wireguard('/installtunnelservice', MANAGED)
            try:
                wait_state(MANAGED, (4,))
                verify_gfn_route(routes)
            except (ValueError, OSError):
                wireguard('/uninstalltunnelservice', MANAGED)
                wait_state(MANAGED, (0,))
                MANAGED.unlink(missing_ok=True)
                raise
        elif state not in (0, 1):
            try:
                service('stop')
            except PermissionError:
                wireguard('/uninstalltunnelservice', MANAGED)
            wait_state(MANAGED, (0, 1))


def interface_for(address, interface=None):
    if interface is not None:
        # The Winsock connection is pinned using IP_UNICAST_IF.
        return int(interface)
    result = w.DWORD()
    value, = struct.unpack('<I', socket.inet_aton(address))
    call = api(c.WinDLL('iphlpapi'), 'GetBestInterface', w.DWORD, w.DWORD, c.POINTER(w.DWORD))
    if call(value, c.byref(result)):
        raise OSError('No IPv4 route to the GFN control endpoint.')
    return result.value


def named_interface(name):
    iphelper = c.WinDLL('iphlpapi')
    luid, index = c.c_uint64(), w.DWORD()
    convert = api(iphelper, 'ConvertInterfaceAliasToLuid', w.DWORD, w.LPCWSTR, c.POINTER(c.c_uint64))
    index_for = api(iphelper, 'ConvertInterfaceLuidToIndex', w.DWORD, c.POINTER(c.c_uint64), c.POINTER(w.DWORD))
    if convert(name, c.byref(luid)) or index_for(c.byref(luid), c.byref(index)):
        raise ValueError('The WireGuard interface is unavailable.')
    return index.value


def network_identity():
    index = interface_for('1.1.1.1')
    command = (
        f"$i=Get-NetIPConfiguration -InterfaceIndex {index};"
        f"$a=Get-NetAdapter | Where-Object ifIndex -eq {index} | Select-Object -First 1;"
        "if(-not $a.HardwareInterface -or -not $i.IPv4DefaultGateway){exit 2};"
        "[pscustomobject]@{adapter=[string]$a.InterfaceGuid;"
        "gateway=[string]$i.IPv4DefaultGateway.NextHop;"
        "address=[string]$i.IPv4Address.IPAddress}|ConvertTo-Json -Compress"
    )
    output = subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, check=True, timeout=8,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    return {'interface': index, **json.loads(output.stdout)}


class SplitTunnel:
    """A single explicit probe tunnel, with a route for the probe IPv4 only."""
    def __init__(self, *, target):
        self.target = target
        self.interface = None
        self.created = False

    def current(self):
        from vpn_servers import Server
        peer = read_profile()['Peer']
        host = peer['Endpoint'].rsplit(':', 1)[0]
        address = socket.gethostbyname(host)
        return Server(host, address, peer['PublicKey'])

    def direct_route(self):
        return network_identity()

    def prepare(self):
        if service('query', PROBE):
            raise ValueError('A previous VPN probe tunnel still exists.')
        self.baseline = interface_for(self.target)

    def activate(self, server):
        self.cleanup()
        tunnel_config(PROBE, [self.target + '/32'], peer=server)
        self.created = True
        wireguard('/installtunnelservice', PROBE)
        wait_state(PROBE, (4,))
        sc = Path(os.environ['SystemRoot']) / 'System32/sc.exe'
        subprocess.run([str(sc), 'config', 'WireGuardTunnel$' + PROBE.stem, 'start=', 'demand'],
                       check=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.interface = named_interface(PROBE.stem)
        # When the regular split tunnel is already active, the probe must win
        # only its single /32 route. Changing its metric affects no other route.
        if self.interface != self.baseline and interface_for(self.target) != self.interface:
            command = (f'Set-NetIPInterface -InterfaceIndex {self.interface} '
                       '-AddressFamily IPv4 -AutomaticMetric Disabled -InterfaceMetric 1 -ErrorAction Stop')
            subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-Command', command],
                           check=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if self.interface == self.baseline or interface_for(self.target) != self.interface:
            raise ValueError('The temporary VPN tunnel did not receive the test route.')

    def cleanup(self):
        if self.created:
            if service('query', PROBE):
                wireguard('/uninstalltunnelservice', PROBE)
                wait_state(PROBE, (0,))
            self.created = False
            PROBE.unlink(missing_ok=True)

    def save(self, server):
        config = read_profile()
        config['Peer'].pop('PresharedKey', None)
        config['Peer']['Endpoint'] = server.address + ':51820'
        config['Peer']['PublicKey'] = server.public_key
        save_profile(config, PROFILE)
