"""Windows session integration for the original GTK window hierarchy."""
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
from enum import Enum
from functools import lru_cache
import msvcrt
import os
from pathlib import Path
import threading
import weakref

CONFIG = Path(os.environ['APPDATA']) / 'MacroStudioNative'
RUNTIME = Path(os.environ['LOCALAPPDATA']) / 'MacroStudioNative' / 'runtime'
STATE = RUNTIME.parent / 'state'


@lru_cache(maxsize=1)
def configure_fonts(root):
    from windows_input import api
    os.environ.setdefault('PANGOCAIRO_BACKEND', 'fc')
    directory = root / 'assets' / 'fonts'
    if directory.is_dir():
        fontconfig = c.WinDLL('libfontconfig-1.dll')
        add_fonts = api(fontconfig, 'FcConfigAppFontAddDir', c.c_int, c.c_void_p, c.c_char_p)
        if not add_fonts(None, str(directory).encode('utf-8')):
            raise RuntimeError('The bundled app fonts could not be loaded.')


@contextmanager
def file_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise BlockingIOError('Another operation is running.') from None
        try:
            yield handle
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class Layer:
    """Translate GTK layer-shell calls to ordinary Windows GTK toplevels."""
    Edge = Enum('Edge', 'TOP LEFT RIGHT')
    KeyboardMode = Enum('KeyboardMode', 'NONE EXCLUSIVE')
    Layer = Enum('Layer', 'OVERLAY')
    windows = weakref.WeakKeyDictionary()

    @classmethod
    def init_for_window(cls, window):
        cls.windows[window] = {'monitor': None, 'margins': {}}
        window.set_keep_above(True)
        window.set_skip_taskbar_hint(True)
        window.connect('size-allocate', cls.position)
        window.connect('map', cls.mapped)

    @classmethod
    def mapped(cls, window):
        cls.position(window)
        if window.get_accept_focus():
            window.present()

    @classmethod
    def position(cls, window, *_):
        item = cls.windows[window]
        monitor = item['monitor'] or window.get_display().get_primary_monitor()
        if monitor is None:
            return
        geometry = monitor.get_geometry()
        margins = item['margins']
        left = margins.get(cls.Edge.LEFT, 0)
        if cls.Edge.RIGHT in margins:
            left = geometry.width - window.get_size()[0] - margins[cls.Edge.RIGHT]
        window.move(geometry.x + left, geometry.y + margins.get(cls.Edge.TOP, 0))

    @classmethod
    def set_monitor(cls, window, monitor):
        cls.windows[window]['monitor'] = monitor

    @classmethod
    def set_margin(cls, window, edge, amount):
        cls.windows[window]['margins'][edge] = amount
        cls.position(window)

    @classmethod
    def set_keyboard_mode(cls, window, mode):
        window.set_accept_focus(mode == cls.KeyboardMode.EXCLUSIVE)
        window.set_focus_on_map(mode == cls.KeyboardMode.EXCLUSIVE)

    @staticmethod
    def set_namespace(*_):
        pass

    set_anchor = set_exclusive_zone = set_layer = set_namespace


class Hotkeys:
    def __init__(self, picker, actions):
        self.picker, self.actions = picker, actions
        self.ready = threading.Event()
        self.bound = {}
        self.hotspot_hook = False
        self.thread_id = None
        self.thread = threading.Thread(target=self.listen, name='macro-hotkeys', daemon=True)
        self.thread.start()
        if not self.ready.wait(3):
            raise RuntimeError('Windows hotkeys did not initialize.')

    def listen(self):
        from windows_input import api, user, kernel
        from windows_recording import KeyEvent, callback_type, module, next_hook, set_hook, unhook
        register = api(user, 'RegisterHotKey', w.BOOL, w.HWND, c.c_int, w.UINT, w.UINT)
        unregister = api(user, 'UnregisterHotKey', w.BOOL, w.HWND, c.c_int)
        get_message = api(user, 'GetMessageW', c.c_int, c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
        self.thread_id = api(kernel, 'GetCurrentThreadId', w.DWORD)()
        message = w.MSG()
        held = False

        @callback_type
        def hotspot(code, msg, pointer):
            nonlocal held
            if code >= 0:
                event = KeyEvent.from_address(pointer)
                if event.vk == 0x7b and not event.flags & 0x12:
                    down = not event.flags & 0x80
                    if down and not held:
                        self.picker.GLib.idle_add(self.picker.hotspot_action, True)
                    held = down
            return next_hook(None, code, msg, pointer)

        hook = None
        try:
            for number, (key, action) in enumerate(self.actions.items(), 1):
                modifiers, virtual_key = key if isinstance(key, tuple) else (0, key)
                if register(None, number, 0x4000 | modifiers, virtual_key):
                    self.bound[number] = action
            hook = set_hook(13, hotspot, module(None), 0)
            self.hotspot_hook = bool(hook)
            self.ready.set()
            while get_message(c.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312 and message.wParam in self.bound:
                    self.picker.GLib.idle_add(self.dispatch, self.bound[message.wParam])
        finally:
            if hook:
                unhook(hook)
            for number in self.bound:
                unregister(None, number)

    @staticmethod
    def dispatch(action):
        action()
        return False

    def close(self):
        from windows_input import api, user
        if self.thread_id is not None:
            api(user, 'PostThreadMessageW', w.BOOL, w.DWORD, w.UINT, w.WPARAM, w.LPARAM)(
                self.thread_id, 0x0012, 0, 0)
            self.thread.join(1)


def owner_identity(pid):
    from windows_input import api, kernel, open_process, close_handle
    handle = open_process(0x1000, False, pid)
    if not handle:
        return None
    try:
        timestamps = [w.FILETIME() for _ in range(4)]
        get_times = api(kernel, 'GetProcessTimes', w.BOOL, w.HANDLE, *[c.POINTER(w.FILETIME)] * 4)
        if get_times(handle, *(c.byref(value) for value in timestamps)):
            start = timestamps[0]
            return {'pid': pid, 'start': (start.dwHighDateTime << 32) | start.dwLowDateTime}
    finally:
        close_handle(handle)
    return None
