"""Bounded restart of the installed GFN Flatpak, invoked only after UI confirmation."""
from contextlib import contextmanager
import sys
if sys.platform != 'win32':
    import fcntl
import os
from pathlib import Path
import subprocess
import time

from input_activity import macro_input_active
from tool_status import vpn_lock

APP_ID = 'com.nvidia.geforcenow'


@contextmanager
def restart_lock():
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'auto-macro'
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (runtime / 'gfn-restart.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('GeForce NOW restart is already running.') from None
        yield


def gfn_running():
    result = subprocess.run(['flatpak', 'ps', '--columns=application'],
                            capture_output=True, text=True, check=True, timeout=3)
    return APP_ID in result.stdout.splitlines()


def force_restart_gfn():
    if sys.platform == 'win32':
        from windows_tools import restart_gfn
        return restart_gfn()
    with restart_lock(), vpn_lock() as vpn_guard:
        if macro_input_active():
            raise ValueError('Finish recording or playback before restarting GeForce NOW.')
        installed = subprocess.run(['flatpak', 'info', APP_ID], capture_output=True, timeout=5)
        if installed.returncode:
            raise ValueError('GeForce NOW Flatpak is not installed.')
        try:
            # Idempotent connect, without binding a monitor to the old game or
            # whichever background window is focused after the widget hides.
            preparation = subprocess.Popen(
                [str(Path.home() / '.local/bin/gfn-login'), 'vpn-prepare'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, pass_fds=(vpn_guard.fileno(),), start_new_session=True)
            # The helper inherits ownership of the same flock. If waiting times
            # out, let it finish with that lock instead of killing it and leaving
            # a live VPN client/service operation free to race the next action.
            if preparation.wait(timeout=120):
                raise ValueError('Could not connect the Japan VPN. Restart was cancelled.')
        except subprocess.TimeoutExpired:
            raise ValueError('VPN preparation is still finishing. GeForce NOW was left open. Try again shortly.') from None
        except OSError:
            raise ValueError('Could not connect the Japan VPN. Restart was cancelled.') from None
        if gfn_running():
            stopped = subprocess.run(['flatpak', 'kill', APP_ID], capture_output=True, timeout=10)
            deadline = time.monotonic() + 5
            while gfn_running():
                if stopped.returncode or time.monotonic() >= deadline:
                    raise ValueError('Could not stop GeForce NOW. Restart was cancelled.')
                time.sleep(.1)
        process = subprocess.Popen(['flatpak', 'run', APP_ID], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        deadline = time.monotonic() + 10
        while not gfn_running():
            if process.poll() not in (None, 0) or time.monotonic() >= deadline:
                raise ValueError('GeForce NOW did not start. Try opening it from the launcher.')
            time.sleep(.1)
        # The helper waits for and binds the new native GFN window, replacing
        # the old monitor so eligible 10s post-game auto-off still works.
        subprocess.Popen([str(Path.home() / '.local/bin/gfn-login'), 'vpn-watch'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
