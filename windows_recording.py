"""Physical keyboard and mouse events for the original bounded recorder."""
import ctypes as c
from ctypes import wintypes as w
import time

from windows_input import EXTENDED, SPECIAL_VK, api, cursor_position, kernel, user

callback_type = c.WINFUNCTYPE(w.LPARAM, c.c_int, w.WPARAM, w.LPARAM)
set_hook = api(user, 'SetWindowsHookExW', w.HANDLE, c.c_int, callback_type, w.HMODULE, w.DWORD)
unhook = api(user, 'UnhookWindowsHookEx', w.BOOL, w.HANDLE)
next_hook = api(user, 'CallNextHookEx', w.LPARAM, w.HANDLE, c.c_int, w.WPARAM, w.LPARAM)
peek = api(user, 'PeekMessageW', w.BOOL, c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT)
dispatch = api(user, 'DispatchMessageW', w.LPARAM, c.POINTER(w.MSG))
wait = api(user, 'MsgWaitForMultipleObjects', w.DWORD, w.DWORD, c.c_void_p, w.BOOL, w.DWORD, w.DWORD)
module = api(kernel, 'GetModuleHandleW', w.HMODULE, w.LPCWSTR)
scans = {scan: code for code, scan in EXTENDED.items()}
special = {vk: code for code, vk in SPECIAL_VK.items()}
buttons = {0x201: (272, 1), 0x202: (272, 0), 0x204: (273, 1), 0x205: (273, 0),
           0x207: (274, 1), 0x208: (274, 0)}


class KeyEvent(c.Structure):
    _fields_ = [('vk', w.DWORD), ('scan', w.DWORD), ('flags', w.DWORD),
                ('stamp', w.DWORD), ('extra', c.c_size_t)]


class MouseEvent(c.Structure):
    _fields_ = [('point', w.POINT), ('data', w.DWORD), ('flags', w.DWORD),
                ('stamp', w.DWORD), ('extra', c.c_size_t)]


class InputDevices:
    def __init__(self, keyboard=True, mouse=True):
        self.events = []
        self.hooks = []
        self.point = cursor_position()
        self.keyboard = callback_type(self.on_key)
        self.mouse = callback_type(self.on_mouse)
        try:
            for enabled, code, handler in ((keyboard, 13, self.keyboard), (mouse, 14, self.mouse)):
                if enabled:
                    hook = set_hook(code, handler, module(None), 0)
                    if not hook:
                        raise OSError('Windows could not start input recording.')
                    self.hooks.append(hook)
        except BaseException:
            self.close()
            raise

    def add(self, kind, code, value, device):
        self.events.append((time.monotonic(), device, kind, code, value))

    def on_key(self, code, message, address):
        if code >= 0:
            event = KeyEvent.from_address(address)
            if not event.flags & 0x12:
                key = special.get(event.vk, scans.get(event.scan) if event.flags & 1 else event.scan)
                if key:
                    self.add(1, key, int(not event.flags & 0x80), 'keyboard')
        return next_hook(None, code, message, address)

    def on_mouse(self, code, message, address):
        if code >= 0:
            event = MouseEvent.from_address(address)
            if not event.flags & 3:
                x, y = event.point.x, event.point.y
                for axis, (current, previous) in enumerate(zip((x, y), self.point)):
                    if current != previous:
                        self.add(2, axis, current - previous, 'mouse')
                self.point = x, y
                high = event.data >> 16
                if message in buttons:
                    self.add(1, *buttons[message], 'mouse')
                elif message in (0x20b, 0x20c) and high in (1, 2):
                    self.add(1, 274 + high, int(message == 0x20b), 'mouse')
                elif message in (0x20a, 0x20e):
                    ticks = c.c_short(high).value
                    if ticks:
                        self.add(2, 8 if message == 0x20a else 6, round(ticks / 120) or (1 if ticks > 0 else -1), 'mouse')
        return next_hook(None, code, message, address)

    def read(self, timeout=.04):
        wait(0, None, False, round(timeout * 1000), 0x04ff)
        message = w.MSG()
        while peek(c.byref(message), None, 0, 0, 1):
            dispatch(c.byref(message))
        result, self.events = self.events, []
        return result

    def close(self):
        for hook in self.hooks:
            unhook(hook)
        self.hooks.clear()
