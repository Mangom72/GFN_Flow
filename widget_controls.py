"""Controller-only input for the native popup; never synthesize system input.

Only udev-identified joysticks are opened. Guide opens the popup; other input is
ignored while hidden. A neutral controller is grabbed only while the popup owns
keyboard focus. Hiding keeps the grab until buttons are released, preventing a
UI button release from leaking into the game. Closing the fd always releases it.
"""
import os
from pathlib import Path
import time


KEYS = {304: 'Return', 305: 'Escape', 310: 'ISO_Left_Tab', 311: 'Tab',
        544: 'Up', 545: 'Down', 546: 'Left', 547: 'Right'}
GUIDE = 316


def joystick_paths():
    for path in Path('/dev/input').glob('event*'):
        try:
            device = path.stat().st_rdev
            properties = Path(f'/run/udev/data/c{os.major(device)}:{os.minor(device)}').read_text()
            if 'E:ID_INPUT_JOYSTICK=1' in properties.splitlines():
                yield path
        except OSError:
            continue


class Controller:
    def __init__(self, path):
        import libevdev
        self.fd = open(path, 'rb', buffering=0)
        try:
            os.set_blocking(self.fd.fileno(), False)
            self.device = libevdev.Device(self.fd)
            if not self.device.has(libevdev.EV_KEY.BTN_GAMEPAD):
                raise ValueError('Not a gamepad')
            self.lib = libevdev
            self.grabbed = False
            self.rest_axes = []
            for code in self.device.evbits.get(libevdev.EV_ABS, []):
                if code.value > 23:
                    continue
                info = self.device.absinfo[code]
                rest = info.minimum if code.value in (2, 5, 9, 10) else (info.minimum + info.maximum) / 2
                tolerance = max(info.flat, (info.maximum - info.minimum) * .05)
                self.rest_axes.append((code, rest, tolerance))
            self.axes = {code.value: self.direction(code, self.device.value[code])
                         for code in self.device.evbits.get(libevdev.EV_ABS, [])
                         if code.value in (0, 1, 16, 17)}
        except BaseException:
            self.fd.close()
            raise

    def direction(self, code, value):
        info = self.device.absinfo[code]
        center = (info.minimum + info.maximum) / 2
        threshold = max(.5, (info.maximum - info.minimum) * .3)
        return -1 if value < center - threshold else 1 if value > center + threshold else 0

    def neutral(self):
        return (not any(self.device.value[code] for code in self.device.evbits.get(self.lib.EV_KEY, []))
                and not any(self.axes.values())
                and all(abs(self.device.value[code] - rest) <= tolerance
                        for code, rest, tolerance in self.rest_axes))

    def read(self, visible, focused):
        # Drain before changing ownership: old game input must not become UI input.
        events = list(self.device.events())
        eligible = visible and focused
        owned = self.grabbed
        actions = []
        for event in events:
            if event.code == self.lib.EV_SYN.SYN_DROPPED:
                raise OSError('Controller input overflow')
            kind, code, value = event.type.value, event.code.value, event.value
            if kind == 1:
                if code == GUIDE and value == 1 and (not visible or owned and focused):
                    actions.append('toggle')
                elif eligible and owned and value == 1 and code in KEYS:
                    actions.append(KEYS[code])
            elif kind == 3 and code in (0, 1, 16, 17):
                direction = self.direction(event.code, value)
                before = self.axes.get(code, 0)
                self.axes[code] = direction
                if eligible and owned and direction and direction != before:
                    actions.append(('Left' if direction < 0 else 'Right') if code in (0, 16)
                                   else ('Up' if direction < 0 else 'Down'))
        if eligible != owned and self.neutral():
            if eligible:
                self.device.grab()
            else:
                self.device.ungrab()
            self.grabbed = eligible
        return actions

    def close(self):
        self.fd.close()  # EVIOCGRAB is automatically released even after errors.


class GamepadInput:
    def __init__(self, picker):
        self.picker = picker
        self.controllers = {}
        self.next_discovery = 0

    def poll(self):
        if time.monotonic() >= self.next_discovery:
            self.next_discovery = time.monotonic() + 2
            paths = set(joystick_paths())
            for path in self.controllers.keys() - paths:
                self.controllers.pop(path).close()
            for path in paths - self.controllers.keys():
                try:
                    self.controllers[path] = Controller(path)
                except Exception:
                    pass  # Inaccessible/unsupported devices never receive controls.
        visible = self.picker.window.get_visible()
        focused = self.picker.window.has_toplevel_focus() or self.picker.Gtk.grab_get_current() is not None
        for path, controller in list(self.controllers.items()):
            try:
                actions = controller.read(visible, focused)
            except Exception:
                controller.close()
                del self.controllers[path]
                continue
            try:
                for action in actions:
                    if action == 'toggle':
                        # Guide is an explicit UI request, never a first-F9 login.
                        self.picker.toggle()
                    elif self.picker.window.get_visible() and (self.picker.window.has_toplevel_focus() or self.picker.Gtk.grab_get_current() is not None):
                        self.dispatch(action)
            except Exception:
                # A destroyed GTK target must never leave a controller grabbed.
                controller.close()
                del self.controllers[path]
        return True

    def dispatch(self, key):
        """Route inside this GTK process only, including native dropdown menus."""
        from gi.repository import Gdk
        Gtk = self.picker.Gtk
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = Gdk.keyval_from_name(key)
        event.state = Gdk.ModifierType.SHIFT_MASK if key == 'ISO_Left_Tab' else 0
        keymap = Gdk.Keymap.get_for_display(self.picker.window.get_display())
        found, entries = keymap.get_entries_for_keyval(event.keyval)
        if found:
            event.hardware_keycode = entries[0].keycode
            event.group = entries[0].group
        event.time = Gdk.CURRENT_TIME
        event.send_event = True
        # A GTK menu can own the grab while the layer remains its parent.
        if self.picker.restart_confirmation is not None:
            self.picker.window.emit('key-press-event', event)
            return
        grab = Gtk.grab_get_current()
        target = grab or self.picker.window.get_focus() or self.picker.window
        if key == 'Return' and isinstance(target, (Gtk.Button, Gtk.Expander, Gtk.Switch)):
            target.activate()
            return
        event.window = target.get_window() or self.picker.window.get_window()
        event.set_device(self.picker.window.get_display().get_default_seat().get_keyboard())
        if grab:
            Gtk.propagate_event(target, event)
        else:
            self.picker.window.emit('key-press-event', event)

    def close(self):
        for controller in self.controllers.values():
            controller.close()
        self.controllers.clear()
