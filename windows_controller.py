"""XInput adapter. GTK receives navigation only while its popup owns focus."""
import ctypes as c
from ctypes import wintypes as w

from widget_controls import GamepadInput as GtkGamepadInput


class State(c.Structure):
    _fields_ = [('packet', w.DWORD), ('buttons', w.WORD), ('left_trigger', c.c_ubyte),
                ('right_trigger', c.c_ubyte), ('left_x', c.c_short), ('left_y', c.c_short),
                ('right_x', c.c_short), ('right_y', c.c_short)]


class GamepadInput(GtkGamepadInput):
    buttons = {0x1: 'Up', 0x2: 'Down', 0x4: 'Left', 0x8: 'Right',
               0x100: 'ISO_Left_Tab', 0x200: 'Tab', 0x1000: 'Return', 0x2000: 'Escape'}

    def __init__(self, picker):
        self.picker = picker
        self.previous = {}
        self.neutral = set()
        self.read = None
        try:
            self.dll = c.WinDLL('xinput1_4')
            self.read = self.dll.XInputGetState
            self.read.restype, self.read.argtypes = w.DWORD, (w.DWORD, c.POINTER(State))
        except OSError:
            pass

    def poll(self):
        if not self.read:
            return True
        p = self.picker
        focused = p.window.get_visible() and (p.window.has_toplevel_focus() or p.Gtk.grab_get_current() is not None)
        for index in range(4):
            state = State()
            if self.read(index, c.byref(state)):
                self.previous.pop(index, None)
                self.neutral.discard(index)
                continue
            buttons = state.buttons
            buttons |= 4 if state.left_x < -17000 else 8 if state.left_x > 17000 else 0
            buttons |= 2 if state.left_y < -17000 else 1 if state.left_y > 17000 else 0
            before = self.previous.get(index, buttons)
            self.previous[index] = buttons
            if not focused:
                self.neutral.discard(index)
            elif not buttons:
                self.neutral.add(index)
            elif index in self.neutral:
                for mask, key in self.buttons.items():
                    if buttons & ~before & mask:
                        self.dispatch(key)
        return True

    def close(self):
        self.read = None
