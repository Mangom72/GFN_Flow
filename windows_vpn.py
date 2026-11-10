"""WireGuard for Windows backend for the original split-tunnel controls."""
import base64
import configparser
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
import ipaddress
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

WIREGUARD = Path(os.environ['ProgramFiles']) / 'WireGuard' / 'wireguard.exe'
PROFILE = CONFIG / 'vpn' / 'gfn-jp.conf'
MANAGED = PROFILE.with_name('MacroStudio-GfnJp.conf')
PROBE = PROFILE.with_name('MacroStudio-GfnProbe.conf')
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
        state = service('query')
        if state == 4:
            return True
        if state in (0, 1) and WIREGUARD.is_file() and PROFILE.is_file():
            return False
    except (OSError, ValueError):
        pass
    return None


def read_profile(path=PROFILE):
    config = configparser.ConfigParser(interpolation=None, strict=True)
    config.optionxform = str
    try:
        with path.open(encoding='utf-8-sig') as stream:
            config.read_file(stream)
        if set(config.sections()) != {'Interface', 'Peer'} or config.defaults():
            raise ValueError
        interface, peer = config['Interface'], config['Peer']
        if not set(interface) <= {'Address', 'PrivateKey', 'ListenPort', 'MTU', 'DNS'}:
            raise ValueError
        if not set(peer) <= {'PublicKey', 'PresharedKey', 'Endpoint', 'AllowedIPs', 'PersistentKeepalive'}:
            raise ValueError
        addresses = [ipaddress.ip_interface(value.strip()) for value in interface['Address'].split(',')]
        if not addresses or any(address.version != 4 for address in addresses):
            raise ValueError
        for value in (interface['PrivateKey'], peer['PublicKey'], peer.get('PresharedKey')):
            if value is not None and len(base64.b64decode(value, validate=True)) != 32:
                raise ValueError
        endpoint, port = peer['Endpoint'].rsplit(':', 1)
        if not (re.fullmatch(r'jp\d+\.nordvpn\.com', endpoint) or ipaddress.IPv4Address(endpoint).is_global):
            raise ValueError
        if not 0 < int(port) < 65536:
            raise ValueError
        if any('\n' in value or '\r' in value for section in (interface, peer) for value in section.values()):
            raise ValueError
        return config
    except (OSError, KeyError, ValueError, configparser.Error):
        raise ValueError('Japan WireGuard profile is missing or invalid.') from None


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
    config['Interface'].pop('DNS', None)
    config['Interface'].pop('ListenPort', None)
    config['Peer']['AllowedIPs'] = ', '.join(routes)
    if peer:
        config['Peer']['Endpoint'] = peer.address + ':51820'
        config['Peer']['PublicKey'] = peer.public_key
    save_profile(config, path)


class ShellExec(c.Structure):
    _fields_ = [('size', w.DWORD), ('mask', c.c_ulong), ('window', w.HWND),
                ('verb', w.LPCWSTR), ('file', w.LPCWSTR), ('parameters', w.LPCWSTR),
                ('directory', w.LPCWSTR), ('show', c.c_int), ('instance', w.HINSTANCE),
                ('idl', c.c_void_p), ('klass', w.LPCWSTR), ('key', w.HKEY), ('hotkey', w.DWORD),
                ('icon', w.HANDLE), ('process', w.HANDLE)]


def is_elevated():
    return bool(api(c.WinDLL('shell32'), 'IsUserAnAdmin', w.BOOL)())


def account_sid():
    result = subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-Command',
                             '[Security.Principal.WindowsIdentity]::GetCurrent().User.Value'],
                            capture_output=True, text=True, check=True, timeout=8,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    sid = result.stdout.strip()
    if not re.fullmatch(r'S-1-(?:\d+-){1,12}\d+', sid):
        raise ValueError('Could not verify the Windows account for the Japan tunnel.')
    return sid


@contextmanager
def elevated_process(executable, args):
    from windows_input import close_handle
    info = ShellExec(size=c.sizeof(ShellExec), mask=0x40, verb='runas', file=str(executable),
                     parameters=subprocess.list2cmdline(args), show=0)
    call = api(c.WinDLL('shell32', use_last_error=True), 'ShellExecuteExW', w.BOOL, c.POINTER(ShellExec))
    if not call(c.byref(info)) or not info.process:
        raise ValueError('Windows did not authorize the WireGuard tunnel action.')
    try:
        yield info.process
    finally:
        close_handle(info.process)


def process_finished(handle, milliseconds):
    from windows_input import kernel
    result = api(kernel, 'WaitForSingleObject', w.DWORD, w.HANDLE, w.DWORD)(handle, milliseconds)
    if result not in (0, 258):
        raise OSError('Could not wait for the WireGuard process.')
    return result == 0


def process_exit_code(handle):
    from windows_input import kernel
    code = w.DWORD()
    if not api(kernel, 'GetExitCodeProcess', w.BOOL, w.HANDLE, c.POINTER(w.DWORD))(handle, c.byref(code)):
        raise ValueError('WireGuard did not return a tunnel result.')
    return code.value


def wireguard(action, path):
    if not WIREGUARD.is_file():
        raise ValueError('Install WireGuard and configure the Japan tunnel first.')
    args = [action, str(path) if action == '/installtunnelservice' else path.stem]
    executable = WIREGUARD
    if action == '/installtunnelservice' and path == MANAGED:
        executable, args = managed_install_command(path)
    if path == PROBE:
        if not is_elevated():
            raise ValueError('Start the server comparison from Macro Studio to approve it once.')
        result = subprocess.run([str(executable), *args], timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        code = result.returncode
    else:
        with elevated_process(executable, args) as handle:
            if not process_finished(handle, 30000):
                raise ValueError('WireGuard did not finish the tunnel action.')
            code = process_exit_code(handle)
    if code:
        raise ValueError(f'WireGuard could not complete the tunnel action ({code}).')


def managed_install_command(path):
    # Give only the initiating user STOP permission on this new, app-owned
    # service. The 10-second monitor never opens UAC during gameplay.
    sid = account_sid()
    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"
    script = rf'''
$ErrorActionPreference = 'Stop'
$wireguard = {literal(WIREGUARD)}
$path = {literal(path)}
$name = {literal('WireGuardTunnel$' + path.stem)}
$sc = Join-Path $env:SystemRoot 'System32/sc.exe'
$existing = Get-Service -Name $name -ErrorAction SilentlyContinue
if ($existing) {{
    if ($existing.Status -ne 'Stopped') {{ exit 10 }}
    $remove = Start-Process -FilePath $wireguard -ArgumentList @('/uninstalltunnelservice', {literal(path.stem)}) -WindowStyle Hidden -Wait -PassThru
    if ($remove.ExitCode -ne 0) {{ exit 13 }}
}}
$worker = Start-Process -FilePath $wireguard -ArgumentList @('/installtunnelservice', ('"' + $path + '"')) -WindowStyle Hidden -Wait -PassThru
if ($worker.ExitCode -ne 0) {{ exit 11 }}
try {{
    & $sc config $name 'start=' 'demand' | Out-Null
    if ($LASTEXITCODE) {{ throw 'Service start mode could not be set' }}
    $sddl = (& $sc sdshow $name | Where-Object {{ $_ -match '^\s*[OGDS]:' }} | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -or !$sddl) {{ throw 'Service descriptor unavailable' }}
    $descriptor = [System.Security.AccessControl.RawSecurityDescriptor]::new($sddl)
    if (!$descriptor.DiscretionaryAcl) {{ throw 'Service DACL unavailable' }}
    $account = [System.Security.Principal.SecurityIdentifier]::new({literal(sid)})
    $ace = [System.Security.AccessControl.CommonAce]::new([System.Security.AccessControl.AceFlags]::None,
        [System.Security.AccessControl.AceQualifier]::AccessAllowed, 36, $account, $false, $null)
    $index = 0
    while ($index -lt $descriptor.DiscretionaryAcl.Count -and
           $descriptor.DiscretionaryAcl[$index].AceQualifier -eq [System.Security.AccessControl.AceQualifier]::AccessDenied) {{ $index++ }}
    $descriptor.DiscretionaryAcl.InsertAce($index, $ace)
    & $sc sdset $name $descriptor.GetSddlForm([System.Security.AccessControl.AccessControlSections]::All) | Out-Null
    if ($LASTEXITCODE) {{ throw 'Service permission could not be set' }}
}} catch {{
    Start-Process -FilePath $wireguard -ArgumentList @('/uninstalltunnelservice', {literal(path.stem)}) -WindowStyle Hidden -Wait
    exit 12
}}
exit 0
'''
    return POWERSHELL, ['-NoProfile', '-NonInteractive', '-EncodedCommand',
                        base64.b64encode(script.encode('utf-16-le')).decode('ascii')]


def wait_state(path, accepted):
    deadline = time.monotonic() + 8
    while service('query', path) not in accepted:
        if time.monotonic() >= deadline:
            raise ValueError('The WireGuard tunnel did not reach the expected state.')
        time.sleep(.2)


def set_connected(connected, *, peer=None):
    with _operation:
        state = service('query')
        if connected:
            if state == 4:
                return
            if state not in (0, 1):
                raise ValueError('The Japan tunnel is already changing state.')
            tunnel_config(MANAGED, managed_routes(), peer=peer)
            wireguard('/installtunnelservice', MANAGED)
            wait_state(MANAGED, (4,))
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
        config['Peer']['Endpoint'] = server.address + ':51820'
        config['Peer']['PublicKey'] = server.public_key
        save_profile(config, PROFILE)
