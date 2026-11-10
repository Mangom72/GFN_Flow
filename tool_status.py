"""Read-only, bounded status checks for the compact tool bar."""
import os
import subprocess
import sys
if sys.platform != 'win32':
    import fcntl
from contextlib import contextmanager
from pathlib import Path

from detection import login
from input_activity import macro_input_active


def read(command):
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=2,
                                env={**os.environ, 'LC_ALL': 'C'}, check=False)
        return result.returncode, result.stdout
    except FileNotFoundError:
        return 127, ''
    except (OSError, subprocess.SubprocessError):
        return None, ''


def vpn_status():
    if sys.platform == 'win32':
        from windows_vpn import status
        return status()
    code, text = read(['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show', '--active'])
    split_available = code != 127
    split = None if code not in (0, 127) else any(line.rsplit(':', 1) == [login.SPLIT_CONNECTION, 'wireguard']
                                      for line in text.splitlines())
    code, text = read(['nordvpn', 'status'])
    nord = False if code == 127 else None
    if code == 0:
        if login.nordvpn_connected(text):
            nord = True
        elif any(line.strip().casefold() == 'status: disconnected' for line in text.splitlines()):
            nord = False
    if split is True or nord is True:
        return True
    if split is False and nord is False and (split_available or code != 127):
        return False
    return None


def read_tool_status():
    vpn = vpn_status()
    if sys.platform == 'win32':
        from windows_tools import afk_active
        return dict(vpn=vpn, afk=afk_active())
    code, _ = read(['systemctl', '--user', 'is-active', '--quiet', 'gfn-anti-afk.service'])
    return dict(vpn=vpn, afk=True if code == 0 else False if code in (3, 4) else None)


@contextmanager
def vpn_lock():
    """Serialize VPN actions; yield the owner FD for controlled inheritance."""
    if sys.platform == 'win32':
        from windows_host import RUNTIME, file_lock
        try:
            with file_lock(RUNTIME / 'vpn-toggle.lock') as lock:
                yield lock
        except BlockingIOError:
            raise ValueError('Another VPN action is running.') from None
        return
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'auto-macro'
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (runtime / 'vpn-toggle.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another VPN action is running.') from None
        # Closing this descriptor (rather than LOCK_UN) lets an explicitly
        # inherited child keep ownership until its VPN preparation finishes.
        yield lock


def toggle_vpn():
    """Use fresh status for the global shortcut; ignore repeated presses in flight."""
    try:
        with vpn_lock():
            return _toggle_vpn()
    except ValueError as error:
        if str(error) == 'Another VPN action is running.':
            return False
        raise


def _toggle_vpn():
    if macro_input_active():
        return False
    connected = vpn_status()
    if connected is None:
        raise ValueError('VPN status unavailable. Try again shortly.')
    if sys.platform == 'win32':
        from windows_vpn import set_connected
        set_connected(not connected)
        return True
    command = 'vpn-disconnect' if connected else 'vpn'
    subprocess.run([str(Path.home() / '.local/bin/gfn-login'), command], check=True, timeout=120)
    return True
