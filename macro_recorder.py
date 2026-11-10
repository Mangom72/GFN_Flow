"""Explicit, in-memory recording from physical Linux input devices.

libevdev is loaded only when capture starts. Never grab a device or write a raw
input log. The caller supplies a foreground-game guard for every input batch.
"""
from collections import Counter
from pathlib import Path
import os
import select
import sys
import threading
import time

from macro_library import BUTTONS, key_definitions

RESERVED_KEYS = {67, 68, 88}  # F9 recording/playback, F10 VPN, F12 hotspot.
SAFE_KEY_NAMES = {
    'ESC': 'Esc', 'ENTER': 'Enter', 'KPENTER': 'Enter', 'TAB': 'Tab',
    'BACKSPACE': 'Backspace', 'DELETE': 'Delete',
    'INSERT': 'Insert', 'HOME': 'Home', 'END': 'End',
    'PAGEUP': 'Page Up', 'PAGEDOWN': 'Page Down',
    'UP': '↑', 'DOWN': '↓', 'LEFT': '←', 'RIGHT': '→',
    'LEFTCTRL': 'Ctrl', 'RIGHTCTRL': 'Ctrl', 'LEFTSHIFT': 'Shift', 'RIGHTSHIFT': 'Shift',
    'LEFTALT': 'Alt', 'RIGHTALT': 'Alt', 'LEFTMETA': 'Super', 'RIGHTMETA': 'Super',
    **{f'F{i}': f'F{i}' for i in range(1, 25)},
}


def recorded_action_hint(step):
    """Describe the latest step without exposing typed characters or text."""
    kind, _, value = step.partition(':')
    if kind == 'wait':
        return None
    if kind in ('down', 'up'):
        state = 'pressed' if kind == 'down' else 'released'
        if key := SAFE_KEY_NAMES.get(value):
            return ('{key} ' + state, {'key': key})
        return ('Key ' + state, {})
    if kind == 'text':
        return ('Text entered', {})
    try:
        if kind == 'button':
            button, state = value.split(',')
            if button in BUTTONS and state in ('0', '1'):
                return ('{button} mouse ' + ('pressed' if state == '1' else 'released'), {'button': button})
        if kind in ('move', 'wheel'):
            x, y = (int(v) for v in value.split(','))
            return ('Move to {x}, {y}' if kind == 'move' else 'Scroll ↔ {x}, ↕ {y}', {'x': x, 'y': y})
    except ValueError:
        pass
    return ('Action recorded', {})


def physical_input_path(path):
    # Bluetooth hardware commonly uses UHID, which also lives under virtual/.
    # Ordinary uinput injectors (including ydotool) live under virtual/input/.
    return '/virtual/' not in str(path) or '/virtual/misc/uhid/' in str(path)


class RecordingLimit(RuntimeError):
    pass


class Timeline:
    def __init__(self, start, capacity=1000):
        self.steps = []
        self.last = start
        self.capacity = capacity
        self.names = {code: name for name, code in key_definitions().items() if name != 'MAX'}
        self.held = Counter()
        self.devices = set()
        self.point = None

    def add(self, steps, stamp, reserve=0):
        gap = max(0, round((stamp - self.last) * 1000))
        extra = ([f'wait:{min(gap, 600000)}'] if gap else []) + steps
        # Always reserve enough room to release every held input on stop.
        if len(self.steps) + len(extra) + reserve + 1 > self.capacity:
            raise RecordingLimit('Action limit reached.')
        self.steps.extend(extra)
        self.last = max(self.last, stamp)

    def key(self, code, value, stamp, device='keyboard', point=None):
        if code in RESERVED_KEYS or value not in (0, 1):
            return
        mouse = 272 <= code <= 279
        if not mouse and code not in self.names:
            return
        token = (device, code)
        if value:
            if token in self.devices:
                return
            if self.held[code]:
                self.devices.add(token)
                self.held[code] += 1
                return
            if mouse and point is None:
                return
            steps = []
            if mouse and point != self.point:
                steps.append(f'move:{point[0]},{point[1]}')
            steps.append(self.event(code, True))
            self.add(steps, stamp, len(self.held) + 1)
            if mouse:
                self.point = point
            self.devices.add(token)
            self.held[code] += 1
        elif token in self.devices:
            if self.held[code] == 1:
                steps = []
                if mouse and point is not None and point != self.point:
                    steps.append(f'move:{point[0]},{point[1]}')
                steps.append(self.event(code, False))
                self.add(steps, stamp, len(self.held) - 1)
                if mouse and point is not None:
                    self.point = point
                del self.held[code]
            else:
                self.held[code] -= 1
            self.devices.remove(token)

    def move(self, point, stamp):
        if point is not None and point != self.point:
            self.add([f'move:{point[0]},{point[1]}'], stamp, len(self.held))
            self.point = point

    def wheel(self, x, y, point, stamp):
        if point is None:
            return
        steps = [] if point == self.point else [f'move:{point[0]},{point[1]}']
        steps.append(f'wheel:{x},{y}')
        self.add(steps, stamp, len(self.held))
        self.point = point

    def event(self, code, down):
        if 272 <= code <= 279:
            return f'button:{BUTTONS[code - 272]},{int(down)}'
        return ('down:' if down else 'up:') + self.names[code]

    def finish(self, stamp):
        if self.held:
            gap = max(0, round((stamp - self.last) * 1000))
            if gap:
                self.steps.append(f'wait:{min(gap, 600000)}')
            self.steps.extend(self.event(code, False) for code in reversed(self.held))
        self.held.clear()
        self.devices.clear()
        return self.steps


class InputDevices:
    """Nonexclusive, nonblocking libevdev readers; no virtual injection devices."""
    def __init__(self, keyboard=True, mouse=True):
        import libevdev
        self.lib = libevdev
        self.readers = []
        self.ignored = set()
        found_keyboard = found_mouse = False
        errors = False
        try:
            for path in sorted(Path('/dev/input').glob('event*')):
                sys_path = Path('/sys/class/input') / path.name
                if not physical_input_path(sys_path.resolve()):
                    continue
                fd = None
                try:
                    fd = open(path, 'rb', buffering=0)
                    os.set_blocking(fd.fileno(), False)
                    device = libevdev.Device(fd)
                except (OSError, libevdev.InvalidFileError):
                    if fd is not None:
                        fd.close()
                    errors = True
                    continue
                is_keyboard = keyboard and device.has(libevdev.EV_KEY.KEY_A) and device.has(libevdev.EV_KEY.KEY_ENTER)
                is_mouse = mouse and device.has(libevdev.EV_KEY.BTN_LEFT) and device.has(libevdev.EV_REL.REL_X)
                if not (is_keyboard or is_mouse):
                    fd.close()
                    continue
                self.readers.append((fd, device, str(path), is_keyboard, is_mouse))
                found_keyboard |= is_keyboard
                found_mouse |= is_mouse
                # Keys held before Start are not part of this recording.
                for code in device.evbits.get(libevdev.EV_KEY, []):
                    if device.value[code]:
                        self.ignored.add((str(path), code.value))
            if keyboard and not found_keyboard or mouse and not found_mouse:
                hint = ' Check input-device permissions.' if errors else ''
                raise RuntimeError('No readable physical ' + ('keyboard' if keyboard and not found_keyboard else 'mouse') + '.' + hint)
        except BaseException:
            self.close()
            raise

    def read(self, timeout=.04):
        ready, _, _ = select.select([r[0] for r in self.readers], [], [], timeout)
        events = []
        for fd, device, identity, keyboard, mouse in self.readers:
            if fd not in ready:
                continue
            for event in device.events():
                if event.code == self.lib.EV_SYN.SYN_DROPPED:
                    raise RuntimeError('Input events were dropped. Recording stopped.')
                kind, code = event.type.value, event.code.value
                if kind == 1:
                    if (identity, code) in self.ignored:
                        if event.value == 0:
                            self.ignored.remove((identity, code))
                        continue
                    if (272 <= code <= 279 and mouse) or (code < 256 and keyboard):
                        events.append((event.sec + event.usec / 1e6, identity, kind, code, event.value))
                elif kind == 2 and mouse and code in (0, 1, 6, 8):
                    events.append((event.sec + event.usec / 1e6, identity, kind, code, event.value))
        return sorted(events)

    def close(self):
        for fd, *_ in self.readers:
            fd.close()
        self.readers.clear()


if sys.platform == 'win32':
    from windows_recording import InputDevices


class Recorder:
    def __init__(self, verify, point, done, progress=lambda *_: None,
                 keyboard=True, mouse=True, capacity=1000, source=InputDevices):
        self.verify, self.point, self.done, self.progress = verify, point, done, progress
        self.keyboard, self.mouse, self.capacity, self.source = keyboard, mouse, capacity, source
        self.stop = threading.Event()
        self.discard = False
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name='macro-recording')
        self.thread.start()

    def run(self):
        source = None
        timeline = Timeline(time.monotonic(), self.capacity)
        started = timeline.last
        reason = 'Recording stopped.'
        published = last_move = timeline.last
        try:
            window = self.verify()
            source = self.source(self.keyboard, self.mouse)
            previous_point = self.point(window) if self.mouse else None
            while not self.stop.is_set():
                events = source.read()
                window = self.verify()  # Discard the batch if the game lost focus.
                now = time.monotonic()
                if now - started > 300:
                    reason = 'Five-minute recording limit reached.'
                    break
                if self.stop.is_set():
                    break
                current_point = self.point(window) if self.mouse else None
                # The cursor query is the batch endpoint. Interpolate relative
                # motion between snapshots so a fast press/drag/release does
                # not incorrectly record both buttons at the final position.
                dx = sum(value for _, _, kind, code, value in events if kind == 2 and code == 0)
                dy = sum(value for _, _, kind, code, value in events if kind == 2 and code == 1)
                moved = [0, 0]
                def event_point():
                    if previous_point is None or current_point is None:
                        return current_point
                    coordinates = []
                    for axis, total in enumerate((dx, dy)):
                        before, after = previous_point[axis], current_point[axis]
                        value = round(before + (after - before) * moved[axis] / total) if total else after
                        coordinates.append(max(min(before, after), min(max(before, after), value)))
                    return tuple(coordinates)
                motion = False
                for stamp, device, kind, code, value in events:
                    # python-libevdev sets CLOCK_MONOTONIC on its device fd.
                    stamp = min(now, max(timeline.last, stamp))
                    if kind == 1:
                        point = event_point() if 272 <= code <= 279 else None
                        timeline.key(code, value, stamp, device, point)
                    elif code in (6, 8):
                        timeline.wheel(value if code == 6 else 0, value if code == 8 else 0,
                                       event_point(), stamp)
                    else:
                        motion = True
                        moved[code] += value
                # Record drags at a bounded rate, not idle cursor motion.
                if motion and any(272 <= code <= 279 for code in timeline.held) and now - last_move >= .05:
                    timeline.move(current_point, now)
                    last_move = now
                previous_point = current_point
                if now - published >= .25:
                    hint = recorded_action_hint(timeline.steps[-1]) if timeline.steps else None
                    self.progress(len(timeline.steps), round(now - started), hint)
                    published = now
        except RecordingLimit as exc:
            reason = str(exc)
        except Exception:
            # Never put input payloads or account data into shared status/logs.
            reason = 'Recording stopped: game focus changed or input device unavailable.'
        finally:
            try:
                if source:
                    source.close()
            finally:
                steps = timeline.finish(time.monotonic())
                self.done([] if self.discard else steps, reason)
