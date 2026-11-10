"""GFN restart, calibration and the scoped utility controls on Windows."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import subprocess
import threading
import time

from detection import login
from input_activity import macro_input_active
from windows_host import RUNTIME, STATE, file_lock
from windows_input import GFN_EXE, active_window, all_windows, api, cursor_position, move_cursor, user

_afk_due = None
_watching_vpn = False


def afk_active():
    return _afk_due is not None


def run_utility(program, command):
    global _afk_due
    if (program, command) == ('gfn-anti-afk', 'toggle'):
        _afk_due = time.monotonic() if _afk_due is None else None
    elif (program, command) == ('gfn-login', 'calibrate'):
        _, profiles = login.load_profiles()
        login.calibrate_profile(profiles)
    else:
        raise ValueError('This utility is unavailable on Windows.')


def toggle_hotspot():
    from windows_vpn import POWERSHELL
    with file_lock(RUNTIME / 'hotspot.lock'):
        if macro_input_active():
            raise ValueError('Finish macro recording or playback first.')
        script = Path(__file__).with_name('windows_hotspot.ps1')
        result = subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                capture_output=True, check=False, timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise ValueError('Windows could not switch Mobile Hotspot.')


def capture_calibration(picker):
    try:
        login.capture_calibration_point(picker.legacy_profiles)
        picker.scan()
    except login.LoginError as error:
        picker.message = str(error)
        picker.render()


def configure_calibration():
    login.STATE_DIR = STATE
    login.CALIBRATION_STATE = STATE / 'calibration.json'


def calibration_status(picker):
    try:
        state = login.read_calibration_state()
        index, total = len(state['new_points']), len(state['old_points'])
    except (login.LoginError, KeyError, TypeError):
        return None
    labels = login.CALIBRATION_LABELS.get(state['profile'], ())
    label = labels[index] if index < len(labels) else f'#{index + 1}'
    return f'{state["profile"]} 좌표 보정 {index+1}/{total} · {label} · Ctrl+Alt+Space'


def restart_gfn():
    from tool_status import vpn_lock
    from windows_vpn import set_connected
    with file_lock(RUNTIME / 'restart.lock'), vpn_lock():
        if not GFN_EXE.is_file():
            raise ValueError('GeForce NOW is unavailable.')
        if macro_input_active():
            raise ValueError('Finish macro recording or playback first.')
        set_connected(True)  # A VPN failure leaves the current GFN session alone.
        post = api(user, 'PostMessageW', w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        for window in all_windows():
            hwnd = int(window.address.split('.')[0], 16)
            if not post(hwnd, 0x10, 0, 0):
                raise ValueError('GeForce NOW could not be closed.')
        deadline = time.monotonic() + 8
        while all_windows():
            if time.monotonic() >= deadline:
                raise ValueError('GeForce NOW did not close. Restart was cancelled.')
            time.sleep(.15)
        subprocess.Popen([str(GFN_EXE)], cwd=GFN_EXE.parent)


def maybe_afk(picker, now):
    global _afk_due
    if _afk_due is None or now < _afk_due:
        return
    _afk_due = now + 180
    if picker.busy or picker.recorder or picker.pending_record or picker.visual.active:
        return
    window = active_window()
    if not login.is_gfn_session(window):
        if not all_windows():
            _afk_due = None
        return
    x, y = cursor_position()
    if not (window.x+20 < x < window.x+window.width-20 and window.y+20 < y < window.y+window.height-20):
        return
    move_cursor(x+8, y)
    def restore():
        if active_window().address == window.address and cursor_position() == (x+8, y):
            move_cursor(x, y)
        return False
    picker.GLib.timeout_add(150, restore)


def monitor_vpn(picker, window):
    global _watching_vpn
    from tool_status import vpn_lock
    from vpn_servers import load_report
    from windows_vpn import MANAGED, network_identity, service, status, wait_state

    def post(message):
        def update():
            picker.message = message
            picker.refresh_tool_status()
            return False
        picker.GLib.idle_add(update)

    def same(report):
        direct = report['direct']
        return all(direct.get(key) == value for key, value in network_identity().items())

    try:
        report = load_report()
        if (not status() or not login.direct_connection_is_fastest(report)
                or not 0 <= time.time() - report['tested_at'] <= 86400 or not same(report)):
            return
        post('Without VPN is faster · disconnecting in 10s')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            time.sleep(.5)
            current = active_window()
            if ((current.address, current.title) != (window.address, window.title)
                    or not status() or load_report().get('tested_at') != report['tested_at']):
                return
        with vpn_lock():
            current = active_window()
            if ((current.address, current.title) != (window.address, window.title)
                    or not same(report) or not status() or macro_input_active()):
                return
            # A background countdown never prompts for elevation during a game.
            service('stop')
            wait_state(MANAGED, (0, 1))
        post('Disconnected · without VPN was faster')
    except PermissionError:
        post('Automatic VPN disconnect needs Windows service permission.')
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        post('Automatic VPN disconnect cancelled.')
    finally:
        _watching_vpn = False


def tick(picker):
    global _watching_vpn
    try:
        maybe_afk(picker, time.monotonic())
        calibration = calibration_status(picker)
        if calibration:
            picker.tray.set_tooltip_text(calibration)
        elif getattr(picker, '_calibration_hint', False):
            picker.tray.set_tooltip_text('Macro Studio · F9')
        picker._calibration_hint = bool(calibration)
        if _watching_vpn:
            return
        window = active_window()
        key = (window.address, window.title)
        if login.is_gfn_idle(window):
            picker._last_vpn_game = None
        if login.is_gfn_session(window) and key != getattr(picker, '_last_vpn_game', None):
            picker._last_vpn_game = key
            _watching_vpn = True
            threading.Thread(target=monitor_vpn, args=(picker, window), daemon=True).start()
    except (OSError, login.LoginError):
        pass
