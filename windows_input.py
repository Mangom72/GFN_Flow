"""Win32 window, input and credential adapters for the recovered Linux engine."""
import ctypes as c
from ctypes import wintypes as w
from dataclasses import replace
import os
from pathlib import Path
import re
import struct
import subprocess
import time

import gfn_login as login

user = c.WinDLL('user32', use_last_error=True)
kernel = c.WinDLL('kernel32', use_last_error=True)
advapi = c.WinDLL('advapi32', use_last_error=True)
GFN_DIR = Path(os.environ['LOCALAPPDATA']) / 'NVIDIA Corporation/GeForceNOW/CEF'
GFN_EXE = GFN_DIR / 'GeForceNOW.exe'


def api(library, name, result, *args):
    method = getattr(library, name)
    method.restype, method.argtypes = result, args
    return method


foreground = api(user, 'GetForegroundWindow', w.HWND)
get_pid = api(user, 'GetWindowThreadProcessId', w.DWORD, w.HWND, c.POINTER(w.DWORD))
window_title = api(user, 'GetWindowTextW', c.c_int, w.HWND, w.LPWSTR, c.c_int)
client_rect = api(user, 'GetClientRect', w.BOOL, w.HWND, c.POINTER(w.RECT))
to_screen = api(user, 'ClientToScreen', w.BOOL, w.HWND, c.POINTER(w.POINT))
visible = api(user, 'IsWindowVisible', w.BOOL, w.HWND)
iconic = api(user, 'IsIconic', w.BOOL, w.HWND)
open_process = api(kernel, 'OpenProcess', w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
close_handle = api(kernel, 'CloseHandle', w.BOOL, w.HANDLE)
query_image = api(kernel, 'QueryFullProcessImageNameW', w.BOOL, w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD))
get_cursor = api(user, 'GetCursorPos', w.BOOL, c.POINTER(w.POINT))
set_cursor = api(user, 'SetCursorPos', w.BOOL, c.c_int, c.c_int)
key_state = api(user, 'GetAsyncKeyState', c.c_short, c.c_int)


def process_image(pid):
    handle = open_process(0x1000, False, pid)
    if not handle:
        return None
    try:
        buffer = c.create_unicode_buffer(32768)
        count = w.DWORD(len(buffer))
        return Path(buffer.value) if query_image(handle, 0, buffer, c.byref(count)) else None
    finally:
        close_handle(handle)


def as_window(hwnd):
    if not hwnd:
        return login.Window('', '', '', 0, 0, 0, 0)
    pid = w.DWORD()
    get_pid(hwnd, c.byref(pid))
    address = f'{int(hwnd):x}.{pid.value}'
    title = c.create_unicode_buffer(4096)
    window_title(hwnd, title, len(title))
    origin, rect = w.POINT(), w.RECT()
    if not client_rect(hwnd, c.byref(rect)) or not to_screen(hwnd, c.byref(origin)):
        return login.Window(address, title.value, '', 0, 0, 0, 0)
    image = process_image(pid.value)
    trusted = image in (GFN_EXE, GFN_DIR / 'GeForceNOWStreamer.exe')
    return login.Window(address, title.value, 'geforcenow' if trusted else '',
                        origin.x, origin.y, rect.right, rect.bottom)


def active_window():
    return as_window(foreground())


def all_windows():
    windows = []
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    @callback_type
    def append(hwnd, _):
        if visible(hwnd) and not iconic(hwnd):
            item = as_window(hwnd)
            if item.window_class:
                windows.append(item)
        return True

    api(user, 'EnumWindows', w.BOOL, callback_type, w.LPARAM)(append, 0)
    return windows


def cursor_position():
    point = w.POINT()
    if not get_cursor(c.byref(point)):
        raise login.LoginError('Could not read the cursor position.')
    return point.x, point.y


def move_cursor(x, y):
    if not set_cursor(x, y):
        raise login.LoginError('Windows rejected the cursor movement.')


class Keyboard(c.Structure):
    _fields_ = [('vk', w.WORD), ('scan', w.WORD), ('flags', w.DWORD),
                ('time', w.DWORD), ('extra', c.c_size_t)]


class Mouse(c.Structure):
    _fields_ = [('x', w.LONG), ('y', w.LONG), ('data', w.DWORD), ('flags', w.DWORD),
                ('time', w.DWORD), ('extra', c.c_size_t)]


class Payload(c.Union):
    _fields_ = [('keyboard', Keyboard), ('mouse', Mouse)]


class Input(c.Structure):
    _fields_ = [('kind', w.DWORD), ('payload', Payload)]


send_input = api(user, 'SendInput', w.UINT, w.UINT, c.POINTER(Input), c.c_int)
map_key = api(user, 'MapVirtualKeyW', w.UINT, w.UINT, w.UINT)
get_layout = api(user, 'GetKeyboardLayout', w.HANDLE, w.DWORD)
vk_key_scan = api(user, 'VkKeyScanExW', c.c_short, w.WCHAR, w.HANDLE)

# Linux evdev values are Set 1 scan codes for the main keyboard. Keep the
# original macro format and translate extended keys at the device boundary.
EXTENDED = {96: 0x1c, 97: 0x1d, 98: 0x35, 99: 0x37, 100: 0x38,
            102: 0x47, 103: 0x48, 104: 0x49, 105: 0x4b, 106: 0x4d,
            107: 0x4f, 108: 0x50, 109: 0x51, 110: 0x52, 111: 0x53,
            125: 0x5b, 126: 0x5c, 127: 0x5d}
SPECIAL_VK = {119: 0x13, 113: 0xad, 114: 0xae, 115: 0xaf,
              **{code: 0x7c + code - 183 for code in range(183, 195)}}
BUTTON_FLAGS = [(0x2, 0x4, 0), (0x8, 0x10, 0), (0x20, 0x40, 0),
                (0x80, 0x100, 1), (0x80, 0x100, 2)]


def emit(*packets):
    items = (Input * len(packets))(*packets)
    if send_input(len(items), items, c.sizeof(Input)) != len(items):
        raise login.LoginError('Windows rejected macro input.')


def keyboard_packet(scan, flags, vk=0):
    return Input(1, Payload(keyboard=Keyboard(vk, scan, flags, 0, 0)))


def key_packet(code, down):
    release = 0 if down else 2
    if code in EXTENDED:
        return keyboard_packet(EXTENDED[code], 9 | release)
    if code in SPECIAL_VK:
        return keyboard_packet(0, release, SPECIAL_VK[code])
    if 1 <= code <= 83 or code in (86, 87, 88):
        return keyboard_packet(code, 8 | release)
    raise login.LoginError(f'This key is unavailable on the Windows keyboard: {code}.')


def mouse_packet(flags, data=0):
    return Input(0, Payload(mouse=Mouse(0, 0, data & 0xffffffff, flags, 0, 0)))


def type_text(text, delay_ms):
    thread = get_pid(foreground(), None)
    layout = get_layout(thread)
    for char in text:
        vk = vk_key_scan(char, layout) if ord(char) <= 0xffff else -1
        if 0 <= vk <= 0x1ff:
            scan = map_key(vk & 0xff, 0)
            if scan:
                keys = [key_packet(42, True)] if vk & 0x100 else []
                keys.extend((keyboard_packet(scan, 8), keyboard_packet(scan, 10)))
                if vk & 0x100:
                    keys.append(key_packet(42, False))
                emit(*keys)
                if delay_ms:
                    time.sleep(delay_ms / 1000)
                continue
        for (unit,) in struct.iter_unpack('<H', char.encode('utf-16-le')):
            emit(keyboard_packet(unit, 4), keyboard_packet(unit, 6))
        if delay_ms:
            time.sleep(delay_ms / 1000)


def input_command(argv):
    if argv[0] != 'ydotool':
        raise login.LoginError('Unsupported input operation.')
    operation = argv[1]
    if operation == 'key':
        delay = int(argv[3]) / 1000
        events = [tuple(map(int, value.split(':'))) for value in argv[4:]]
        packets = [key_packet(code, bool(down)) for code, down in events]
        if delay:
            for packet in packets:
                emit(packet)
                time.sleep(delay)
        else:
            emit(*packets)
    elif operation == 'click':
        mask = int(argv[2], 16)
        button = mask & 15
        if button >= len(BUTTON_FLAGS):
            raise login.LoginError('This mouse button is unavailable on Windows.')
        down, up, data = BUTTON_FLAGS[button]
        emit(*(mouse_packet(flag, data) for flag, bit in ((down, 0x40), (up, 0x80)) if mask & bit))
    elif operation == 'mousemove' and argv[2] == '--wheel':
        dx, dy = map(int, argv[-2:])
        for flag, value in ((0x1000, dx), (0x0800, dy)):
            if value:
                emit(mouse_packet(flag, value * 120))
    else:
        raise login.LoginError('Unsupported input operation.')
    return subprocess.CompletedProcess([], 0, '', '')


def click(window, profile, x, y):
    current = login.verify_window(window)
    point = login.map_point(current, profile, x, y)
    if not (current.x <= point[0] < current.x + current.width and
            current.y <= point[1] < current.y + current.height):
        raise login.LoginError('Macro point is outside the game window.')
    move_cursor(*point)
    login.verify_window(current)
    input_command(['ydotool', 'click', '0xC0'])


class Credential(c.Structure):
    _fields_ = [('Flags', w.DWORD), ('Type', w.DWORD), ('TargetName', w.LPWSTR),
                ('Comment', w.LPWSTR), ('LastWritten', w.FILETIME), ('Size', w.DWORD),
                ('Blob', c.POINTER(c.c_ubyte)), ('Persist', w.DWORD),
                ('AttributeCount', w.DWORD), ('Attributes', c.c_void_p),
                ('TargetAlias', w.LPWSTR), ('UserName', w.LPWSTR)]


cred_read = api(advapi, 'CredReadW', w.BOOL, w.LPCWSTR, w.DWORD, w.DWORD,
                c.POINTER(c.POINTER(Credential)))
cred_write = api(advapi, 'CredWriteW', w.BOOL, c.POINTER(Credential), w.DWORD)
cred_free = api(advapi, 'CredFree', None, c.c_void_p)


def credential(target, *, account=False, optional=False):
    pointer = c.POINTER(Credential)()
    if not cred_read(target, 1, 0, c.byref(pointer)):
        if optional and c.get_last_error() == 1168:
            return None
        raise login.LoginError('Save the game credentials in the editor first.')
    try:
        entry = pointer.contents
        return entry.UserName or '' if account else c.string_at(entry.Blob, entry.Size).decode('utf-16-le')
    finally:
        cred_free(pointer)


def target_name(key):
    if not isinstance(key, str) or not re.fullmatch(r'[\w.-]{1,150}', key):
        raise login.LoginError('Invalid credential reference.')
    return 'MacroStudioNative/' + key


def get_secret(key):
    result = credential(target_name(key), optional=True)
    if result is not None:
        return result
    if key.startswith(('old-account.', 'old-password.')):
        kind, identity = key.split('.', 1)
        if re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identity):
            return credential('MacroStudioWindows/' + identity, account=kind == 'old-account')
    raise login.LoginError('Save the game credentials in the editor first.')


def store_secret(key, value):
    raw = value.encode('utf-16-le')
    if len(raw) > 2560:
        raise login.LoginError('Windows credential exceeds the maximum size.')
    blob = (c.c_ubyte * len(raw)).from_buffer_copy(raw)
    item = Credential(Type=1, TargetName=target_name(key), Size=len(raw), Blob=blob,
                      Persist=2, UserName='Macro Studio')
    try:
        if not cred_write(c.byref(item), 0):
            raise login.LoginError('Could not write to Windows Credential Manager.')
    finally:
        c.memset(blob, 0, len(raw))


def configure():
    original_load_profiles = login.load_profiles
    def load_profiles():
        if not login.CONFIG.exists():
            return {}, []
        settings, profiles = original_load_profiles()
        updated = []
        for profile in profiles:
            user = profile.user
            if not user and profile.credential_key.startswith('old-password.'):
                key = profile.credential_key.replace('old-password.', 'old-account.', 1)
                try:
                    user = get_secret(key)
                except login.LoginError:
                    pass
            updated.append(replace(profile, user=user))
        return settings, updated
    login.load_profiles = load_profiles
    login.active_window = active_window
    login.all_windows = all_windows
    login.cursor_position = cursor_position
    login.move_cursor = move_cursor
    login.click = click
    login.run = input_command
    login.type_text = type_text
    login.get_secret = get_secret
    login.store_secret = store_secret
    login.require_commands = lambda *_: None
    login.show_widget = lambda *_args, **_kwargs: None
    login.notify = lambda *_args, **_kwargs: None
    login.log = lambda *_args: None
