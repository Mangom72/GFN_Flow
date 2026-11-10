#!/usr/bin/python3
"""Native F9 macro picker and session watcher with scoped popup input."""
import argparse
from contextlib import nullcontext, suppress
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import threading
import time

from detection import Sessions, login, session_key, candidates, game_name, gfn_app_running, is_gfn_window
from macro_library import MacroLibrary, TYPES
from macro_engine import MacroRunner, Cancelled
from ui_theme import CSS, TOKENS, category_markup, macro_markup
from ui_motion import Motion
from ui_popup import place_popup
from preferences import Preferences, SIZES
from ui_localization import Localizer, KO
from ui_input_scroll import protect_inputs
from input_activity import owner_identity

ROOT = Path(__file__).resolve().parent
WINDOWS = sys.platform == 'win32'
if WINDOWS:
    from windows_host import RUNTIME, STATE
else:
    STATE = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'auto-macro'
    RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'auto-macro'
SOCKET = RUNTIME / 'picker.sock'
ENDPOINT = RUNTIME / 'picker-endpoint.json'

def send(action):
    if WINDOWS:
        try:
            endpoint = json.loads(ENDPOINT.read_text(encoding='utf-8'))
            address, message = ('127.0.0.1', int(endpoint['port'])), endpoint['token'] + '\n' + action
        except (ValueError, KeyError, TypeError) as error:
            raise OSError('Invalid Windows picker endpoint.') from error
    else:
        address, message = str(SOCKET), action
    with socket.socket(socket.AF_INET if WINDOWS else socket.AF_UNIX) as sock:
        sock.settimeout(1)
        sock.connect(address)
        sock.sendall(message.encode())


def request(action, screen=None):
    if screen:
        action = json.dumps(dict(action=action, screen=screen))
    try:
        send(action)
        return
    except OSError:
        pass
    if WINDOWS:
        subprocess.Popen([sys.executable, '-X', 'utf8', str(ROOT / 'windows_entry.pyw'), 'daemon'],
                         creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        names = [name for name in ('WAYLAND_DISPLAY', 'DISPLAY', 'HYPRLAND_INSTANCE_SIGNATURE',
                                  'XDG_CURRENT_DESKTOP', 'YDOTOOL_SOCKET') if name in os.environ]
        if names:
            subprocess.run(['systemctl', '--user', 'import-environment', *names], check=True)
        subprocess.run(['systemctl', '--user', 'start', 'auto-macro.service'], check=True)
    for _ in range(100 if WINDOWS else 30):
        try:
            send(action)
            return
        except OSError:
            time.sleep(.1)
    raise RuntimeError('Macro picker did not start.')


class Picker:
    def __init__(self, preview=False, width=430, library=None, height=520):
        if WINDOWS:
            from windows_host import configure_fonts
            configure_fonts(ROOT)
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk, GLib
        if WINDOWS:
            from windows_host import Layer as GtkLayerShell
            Gtk.Settings.get_default().set_property('gtk-theme-name', 'Adwaita')
            Gtk.Settings.get_default().set_property('gtk-icon-theme-name', 'Adwaita')
        else:
            gi.require_version('GtkLayerShell', '0.1')
            from gi.repository import GtkLayerShell
        self.Gtk, self.GLib, self.Layer = Gtk, GLib, GtkLayerShell
        self.preview = preview
        self.motion = Motion(Gtk, enabled=not preview)
        self.popup_screen = None
        self.popup_monitor = None
        self.last_popup_press = None
        self.restart_confirmation = None
        self.tool_status = dict(vpn=False, afk=False) if preview else dict(vpn=None, afk=None)
        self.tool_polling = False
        self.width = width
        self.height = height
        self.settings, self.profiles = login.load_profiles()
        self.legacy_profiles = self.profiles[:]
        if preview and library is None:
            import tempfile
            self.preview_directory = tempfile.TemporaryDirectory()
            library = MacroLibrary(Path(self.preview_directory.name) / 'library.json', self.profiles)
        self.library = library or MacroLibrary(profiles=self.profiles)
        self.preferences = Preferences(self.library.path.with_name('settings.json'))
        self.localizer = Localizer(self.preferences.data['language'])
        self.motion.enabled = not preview and self.preferences.data['animations']
        self.library_height = 600
        if not preview:
            self.width, self.height, self.library_height = SIZES[self.preferences.data['size']]
            width = self.width
        self.settings_page = None
        self.vpn_testing = False
        self.vpn_cancel = threading.Event()
        self.vpn_test_state = {}
        from vpn_servers import load_report
        self.vpn_report = {} if preview else load_report()
        self.settings_return = 'picker'
        self.profiles = self.library.detection_profiles(self.legacy_profiles)
        self.manager = None
        self.runner = None
        self.pending = None
        self.pending_capture = None
        self.pending_record = None
        self.recorder = None
        self.recording_widget = None
        self.playback_widget = None
        self.record_manager = None
        self.record_review = None
        self.record_stopped_at = float('-inf')
        self.input_owner = owner_identity()
        self.playback = dict(running=False, recording=False, armed=False, name='', step=0, total=0)
        STATE.mkdir(parents=True, exist_ok=True)
        try:
            attempted = json.loads((STATE / 'sessions.json').read_text())
        except (OSError, ValueError):
            attempted = []
        self.sessions = Sessions(attempted)
        self.detected_windows = ()
        self.game = None
        self.busy = False
        from visual_integration import VisualIntegration
        self.visual = VisualIntegration(self)
        self.message = 'Open a GFN game to run its macros, or open the library to create one.'
        self.window = Gtk.OffscreenWindow() if preview else Gtk.Window(title='Macro Studio')
        self.window.set_default_size(width, -1)
        self.window.set_resizable(False)
        self.window.set_decorated(False)
        self.window.set_accept_focus(True)
        visual = self.window.get_screen().get_rgba_visual()
        if visual:
            self.window.set_visual(visual)
        self.window.get_style_context().add_class('macro-popup')
        if not self.preferences.data['animations']:
            self.window.get_style_context().add_class('reduced-motion')
        if not preview:
            GtkLayerShell.init_for_window(self.window)
            GtkLayerShell.set_namespace(self.window, 'gfn-macro-picker')
            GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.EXCLUSIVE)
            GtkLayerShell.set_exclusive_zone(self.window, -1)
            for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.LEFT):
                GtkLayerShell.set_anchor(self.window, edge, True)
        self.window.connect('delete-event', lambda *_: self.close())
        self.window.connect('key-press-event', self.settings_key)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), provider, 600)
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=TOKENS['space_sm'])
        self.panel.get_style_context().add_class('panel')
        self.stack = Gtk.Stack()
        self.stack.get_style_context().add_class('popup-surface')
        self.motion.stack(self.stack, slide=True)
        self.stack.set_hhomogeneous(False)
        self.stack.set_vhomogeneous(False)
        self.stack.add_named(self.panel, 'picker')
        self.overlay = Gtk.Overlay()
        self.overlay.add(self.stack)
        self.window.add(self.overlay)
        if not preview:
            # Observe our own GTK child-popup presses before GTK closes a menu
            # or changes page, then delegate normal dispatch without consuming.
            from gi.repository import Gdk
            Gdk.event_handler_set(self.route_event)
            RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.server = socket.socket(socket.AF_INET if WINDOWS else socket.AF_UNIX)
            if WINDOWS:
                import secrets
                self.server.bind(('127.0.0.1', 0))
                self.control_token = secrets.token_hex(32)
                self.library.atomic_write(ENDPOINT, {'port': self.server.getsockname()[1], 'token': self.control_token})
            else:
                self.server.bind(str(SOCKET))
                os.chmod(SOCKET, 0o600)
            self.server.listen(8)
            self.server.setblocking(False)
            GLib.timeout_add(80, self.receive)
            GLib.timeout_add(500, self.scan)
            GLib.timeout_add(3000, self.refresh_tool_status)
            if not WINDOWS:
                GLib.timeout_add(150, self.recover_vpn_probes)
            self.scan()
            self.publish_playback()
            if WINDOWS:
                from windows_controller import GamepadInput
                from windows_host import Hotkeys
                from windows_tools import configure_calibration
                configure_calibration()
                self.hotkeys = Hotkeys(self, {
                    0x78: lambda: self.open(auto=True),
                    0x79: lambda: self.vpn_action(global_shortcut=True),
                    (0x3, 0x20): lambda: self.windows_calibrate_point(),
                })
                GLib.timeout_add(500, self.windows_tools_tick)
                self.tray = Gtk.StatusIcon.new_from_icon_name('input-gaming-symbolic')
                self.tray.set_tooltip_text('Macro Studio · F9')
                self.tray.connect('activate', lambda *_: self.toggle())
                self.tray.connect('popup-menu', self.tray_menu)
                self.window.connect('focus-out-event', self.windows_focus_out)
                print(f'Macro Studio ready. Registered {len(self.hotkeys.bound)}/3 hotkeys.', flush=True)
                print(f'Focused-game F12 listener: {self.hotkeys.hotspot_hook}.', flush=True)
            else:
                from widget_controls import GamepadInput
            self.gamepad = GamepadInput(self)
            GLib.timeout_add(40, self.gamepad.poll)

    def tray_menu(self, icon, button, timestamp):
        menu = self.Gtk.Menu()
        for label, action in [('Macro Studio', self.toggle), ('Library', self.manage),
                              ('Settings', self.show_settings), ('Mobile Hotspot', self.hotspot_action),
                              ('Exit', self.shutdown)]:
            item = self.Gtk.MenuItem(label=self.tr(label))
            item.connect('activate', lambda _, callback=action: callback())
            menu.append(item)
        menu.show_all()
        menu.popup(None, None, self.Gtk.StatusIcon.position_menu, icon, button, timestamp)

    def windows_focus_out(self, *_):
        def check():
            if (self.window.get_visible() and not self.window.has_toplevel_focus()
                    and self.Gtk.grab_get_current() is None):
                self.hide()
            return False
        self.GLib.timeout_add(150, check)
        return False

    def shutdown(self):
        self.visual.stop()
        if self.runner:
            self.runner.cancel.set()
        if self.playback_widget:
            self.playback_widget.hide()
        if self.recording_widget:
            self.recording_widget.hide()
        if self.recorder:
            self.recorder.stop.set()
        self.cancel_vpn_search()
        self.dismiss_restart_confirmation()
        self.gamepad.close()
        if WINDOWS:
            self.hotkeys.close()
            self.tray.set_visible(False)
        self.server.close()
        self.Gtk.main_quit()
        return False

    def publish_playback(self):
        if not self.preview:
            self.library.atomic_write(STATE / 'playback.json', {**self.playback, 'input_owner': self.input_owner,
                'language': self.preferences.data['language']})

    def progress(self, step, total, repeat, repeats, action):
        if not self.playback.get('running') or self.runner is None:
            return False
        self.playback.update(step=step, total=total, repeat=repeat, repeats=repeats, action=action)
        self.publish_playback()
        if self.playback_widget:
            self.playback_widget.set_progress(step, total, repeat, repeats, action)
        self.message = f'{self.playback["name"]}: action {step}/{total}, repeat {repeat}/{repeats}. F9 stops playback.'
        if self.manager:
            self.manager.say(self.message)
        return False

    def render_if_visible(self):
        if self.window.get_visible() and self.stack.get_visible_child_name() == 'picker':
            self.render()

    def resize_view(self, height):
        width = self.width
        if not self.preview:
            display = self.window.get_display()
            try:
                if WINDOWS:
                    raise OSError
                monitors = json.loads(subprocess.check_output(['hyprctl', 'monitors', '-j'], timeout=1))
                monitors = [m for m in monitors if not m.get('disabled')]
                current = next((m for m in monitors if m['name'] == self.popup_screen), None)
                current = current or next((m for m in monitors if m.get('focused')), monitors[0])
            except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError):
                if WINDOWS:
                    seat = display.get_default_seat()
                    pointer = seat.get_pointer() if seat else None
                    if pointer:
                        _, x, y = pointer.get_position()
                        monitor = display.get_monitor_at_point(x, y)
                    else:
                        monitor = None
                else:
                    monitor = None
                monitor = monitor or display.get_primary_monitor() or display.get_monitor(0)
                geometry, area = monitor.get_geometry(), monitor.get_workarea()
                current = dict(name='', x=geometry.x, y=geometry.y, width=geometry.width, height=geometry.height,
                               reserved=[area.x - geometry.x, area.y - geometry.y,
                                         geometry.x + geometry.width - area.x - area.width,
                                         geometry.y + geometry.height - area.y - area.height])
            import re
            name = re.sub(r'[^a-zA-Z0-9_.-]', '_', current['name'])
            try:
                anchor = json.loads((STATE / f'anchor-{name}.json').read_text())
            except (OSError, ValueError):
                anchor = None
            rect = place_popup(current, width, height, anchor, TOKENS['popup_gap'], TOKENS['screen_inset'])
            self.popup_monitor = current
            self.popup_geometry = rect
            for index in range(display.get_n_monitors()):
                monitor = display.get_monitor(index)
                geometry = monitor.get_geometry()
                if (geometry.x, geometry.y) == (current['x'], current['y']):
                    self.Layer.set_monitor(self.window, monitor)
                    break
            self.Layer.set_margin(self.window, self.Layer.Edge.LEFT, rect.x)
            self.Layer.set_margin(self.window, self.Layer.Edge.TOP, rect.y)
            width, height = rect.width, rect.height
        # Non-resizable layer windows use their minimum request, not resize().
        self.window.set_size_request(width, height)
        self.window.resize(width, height)

    def manage(self):
        self.dismiss_restart_confirmation()
        if self.visual.active:
            self.visual.pause()
        if self.recorder:
            self.recorder.stop.set()
            return
        if self.busy:
            return
        self.cancel_recording()
        from macro_manager import Manager
        if not self.manager:
            self.manager = Manager(self)
        self.manager.show()

    def show_picker(self):
        if self.manager:
            self.manager.show_library()
        if not self.preview:
            self.Layer.set_keyboard_mode(self.window, self.Layer.KeyboardMode.EXCLUSIVE)
        self.stack.set_visible_child_name('picker')
        self.render()

    def cancel_recording(self):
        manager = self.pending_record
        if manager is None:
            return False
        self.pending_record = None
        self.playback.update(armed=False)
        self.publish_playback()
        manager.update_recording_controls()
        return True

    def acknowledge_recording(self, manager):
        if self.record_review is manager:
            self.record_review = None
            self.playback.update(review=False)
            self.publish_playback()

    def cancel_pending(self):
        self.cancel_recording()
        self.pending = None
        self.pending_capture = None
        self.pending_record = None
        self.playback.update(armed=False)
        self.publish_playback()
        self.message = 'Queued action cancelled.'
        self.render()

    def persist(self):
        temporary = STATE / 'sessions.tmp'
        temporary.write_text(json.dumps(sorted(self.sessions.attempted)))
        temporary.replace(STATE / 'sessions.json')

    def scan(self):
        self.detected_windows = ()
        try:
            self.settings, self.legacy_profiles = login.load_profiles()
            self.library.source_profiles = self.legacy_profiles
            self.profiles = self.library.detection_profiles(self.legacy_profiles)
            recording = self.pending_record or self.record_manager
            if recording:
                request = recording.record_request
                # Unsaved custom browser games must be detectable too. Keep
                # this temporary profile only for the recording session.
                self.profiles.append(login.Profile('Recording target', request['pattern'], '', '',
                                                   request['width'], request['height'], []))
            windows = login.all_windows()
            if self.library.remember_detected(candidates(windows, self.profiles).values()):
                self.profiles = self.library.detection_profiles(self.profiles)
                if self.manager:
                    self.manager.game_blacklist.refresh()
            previous = set(self.sessions.attempted)
            self.sessions.update(windows, self.profiles, blocked=self.library.window_is_blocked)
            self.detected_windows = windows
            self.visual.reconcile()
            if previous != self.sessions.attempted:
                self.persist()
            if self.game and not self.busy:
                current = self.sessions.games.get(self.game[0].address)
                if not current or session_key(*current) != session_key(*self.game):
                    self.game = None
                    self.message = 'Game session changed. Press F9 in the game to select it.'
                    self.render_if_visible()
        except Exception as exc:
            self.visual.stop()
            self.message = f'Detection unavailable: {exc}'
            # Keep the previous claims on transient failures; never rearm login.
        return True

    def receive(self):
        try:
            connection, _ = self.server.accept()
        except BlockingIOError:
            return True
        with connection:
            connection.settimeout(.2)
            try:
                action = connection.recv(4096).decode()
            except (OSError, UnicodeError):
                return True
        screen = None
        payload = {}
        if WINDOWS:
            import hmac
            token, separator, action = action.partition('\n')
            if not separator or not hmac.compare_digest(token.encode(), self.control_token.encode()):
                return True
        if action.startswith('{'):
            try:
                payload = json.loads(action)
                action, screen = payload['action'], payload.get('screen')
                if not isinstance(action, str) or screen is not None and not isinstance(screen, str):
                    return True
            except (ValueError, KeyError, TypeError):
                return True
        if action == 'pointer':
            if not isinstance(payload.get('x'), int) or not isinstance(payload.get('y'), int):
                return True
            self.outside_click(payload['x'], payload['y'], payload.get('surface'))
            return True
        if action in ('manage', 'settings', 'f9', 'show', 'toggle'):
            self.popup_screen = screen
        if action == 'hide':
            self.hide()
        elif action == 'quit' and WINDOWS:
            return self.shutdown()
        elif action == 'close':
            self.close()
        elif action == 'toggle':
            self.toggle()
        elif action == 'manage':
            self.manage()
        elif action == 'settings':
            self.show_settings()
        elif action in ('f9', 'show'):
            self.open(auto=action == 'f9')
        return True

    def hide(self):
        self.dismiss_restart_confirmation()
        self.window.hide()
        return True

    def close(self):
        self.cancel_recording()
        if self.vpn_testing:
            self.cancel_vpn_search()
        self.hide()
        if self.manager:
            self.manager.show_library()
        self.stack.set_visible_child_name('picker')
        return True

    def tr(self, source):
        return self.localizer.text(source)

    def prepare_ui(self, root=None):
        root = root or self.window
        self.localizer.scan(root, self.Gtk)
        protect_inputs(root, self.Gtk)

    def show_settings(self):
        self.dismiss_restart_confirmation()
        if self.busy:
            return
        if not self.preview:
            self.Layer.set_keyboard_mode(self.window, self.Layer.KeyboardMode.EXCLUSIVE)
        if self.stack.get_visible_child_name() != 'settings':
            self.settings_return = self.stack.get_visible_child_name() or 'picker'
        if self.settings_return == 'picker' and not self.panel.get_children():
            self.render()
        if self.settings_page is None:
            from ui_settings import SettingsPage
            self.settings_page = SettingsPage(self)
            self.stack.add_named(self.settings_page.root, 'settings')
        self.prepare_ui(self.settings_page.root)
        self.settings_page.root.show_all()
        self.settings_page.vpn.refresh()
        self.stack.set_visible_child_name('settings')
        self.resize_view(self.library_height if self.settings_return == 'manager' else self.height)
        self.window.show_all()

    def leave_settings(self):
        self.stack.set_visible_child_name(self.settings_return)
        self.resize_view(self.library_height if self.settings_return == 'manager' else self.height)

    def settings_key(self, window, event):
        from gi.repository import Gdk
        page = self.stack.get_visible_child_name()
        key = Gdk.keyval_name(event.keyval) or ''
        if self.restart_confirmation is not None:
            return self.restart_confirmation.key_handler(window, event)
        control = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if page == 'settings' and key == 'Escape':
            self.leave_settings()
            return True
        if page != 'picker':
            return False  # Manager retains its own native shortcuts.
        if key == 'Escape':
            self.hide()
            return True
        if control and key.lower() == 'l':
            self.manage()
            return True
        if control and key == 'comma':
            self.show_settings()
            return True
        rows = [row for kind, box in getattr(self, 'quick_lists', {}).items()
                if self.quick_sections[kind].get_expanded()
                for row in box.get_children() if hasattr(row, 'macro')]
        selected = getattr(self, 'quick_selected', None)
        if key in ('Up', 'Down', 'Home', 'End') and rows:
            index = rows.index(selected) if selected in rows else (-1 if key != 'Up' else len(rows))
            index = (0 if key == 'Home' else len(rows) - 1 if key == 'End'
                     else max(0, min(len(rows) - 1, index + (1 if key == 'Down' else -1))))
            row = rows[index]
            row.get_parent().select_row(row)
            row.grab_focus()
            return True
        focused = window.get_focus()
        if key in ('Left', 'Right') and focused in getattr(self, 'quick_sections', {}).values():
            focused.set_expanded(key == 'Right')
            return True
        if key == 'Left' and selected:
            section = self.quick_sections[selected.macro['kind']]
            section.set_expanded(False)
            section.grab_focus()
            return True
        if key in ('Return', 'KP_Enter') and selected and window.get_focus() in (selected, selected.get_parent(), None):
            if self.game:
                self.run_macro(selected.macro)
            return True
        return False

    def apply_preferences(self):
        self.localizer.set_language(self.preferences.data['language'])
        self.update_tool_controls()
        self.prepare_ui()
        self.motion.set_enabled(not self.preview and self.preferences.data['animations'])
        context = self.window.get_style_context()
        (context.remove_class if self.preferences.data['animations'] else context.add_class)('reduced-motion')
        self.publish_playback()
        if not self.preview:
            self.width, self.height, self.library_height = SIZES[self.preferences.data['size']]
            if self.manager:
                self.manager.view_width = self.width
                self.manager.view_height = self.library_height
            current = self.stack.get_visible_child_name()
            self.resize_view(self.library_height if current == 'manager' or current == 'settings' and self.settings_return == 'manager' else self.height)

    def toggle(self):
        if self.busy or self.recorder:
            self.open(auto=True)  # Preserve the bar's Stop action.
        elif self.window.get_visible():
            self.close()
        else:
            self.open(auto=False)

    def popup_surface(self):
        layers = json.loads(subprocess.check_output(['hyprctl', 'layers', '-j'], timeout=1))
        return next((layer for monitor in layers.values() for level in monitor['levels'].values()
                     for layer in level if layer['namespace'] == 'gfn-macro-picker'), None)

    def pointer_in_child_popup(self):
        # GTK owns menu/dropdown surfaces beyond the main popup rectangle.
        # Wayland only reports pointer windows belonging to this GTK client.
        seat = self.window.get_display().get_default_seat()
        if not seat or not seat.get_pointer():
            return False
        window, x, y = seat.get_pointer().get_window_at_position()
        return bool(window and 0 <= x < window.get_width() and 0 <= y < window.get_height())

    def route_event(self, event, *_):
        from gi.repository import Gdk
        try:
            if event.type == Gdk.EventType.BUTTON_PRESS and self.window.get_visible():
                window = event.get_window()
                if window and window.get_toplevel() != self.window.get_window() and self.pointer_in_child_popup():
                    self.last_popup_press = (time.monotonic(), login.cursor_position())
        except (OSError, ValueError, login.LoginError):
            pass
        finally:
            self.Gtk.main_do_event(event)

    def outside_click(self, x, y, surface=None):
        if not self.window.get_visible() or self.busy:
            return False
        if self.last_popup_press:
            stamp, point = self.last_popup_press
            if time.monotonic() - stamp < .75 and abs(point[0] - x) <= 2 and abs(point[1] - y) <= 2:
                return False
        try:
            bounds = self.popup_surface()
            if not bounds or surface and surface != bounds['address']:
                return False
            if bounds['x'] <= x < bounds['x'] + bounds['w'] and bounds['y'] <= y < bounds['y'] + bounds['h']:
                return False
            # Let the bar's own toggle handle an explicit button click. Its
            # press arrives here before Qt emits clicked on button release.
            if self.popup_monitor:
                import re
                monitor = self.popup_monitor
                name = re.sub(r'[^a-zA-Z0-9_.-]', '_', monitor['name'])
                try:
                    anchor = json.loads((STATE / f'anchor-{name}.json').read_text())
                    ax, ay = monitor['x'] + anchor['x'], monitor['y'] + anchor['y']
                    if ax <= x < ax + anchor['width'] and ay <= y < ay + anchor['height']:
                        return False
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            if self.pointer_in_child_popup():
                return False
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            return False
        self.hide()
        return True

    def can_open_on_f9(self, active):
        scope = self.preferences.data['f9_scope']
        if scope == 'always':
            return True
        if scope == 'focused':
            return is_gfn_window(active)
        if scope == 'running':
            return is_gfn_window(active) or gfn_app_running(self.detected_windows)
        if self.detected_windows and any(is_gfn_window(window) for window, _ in self.sessions.games.values()):
            return True
        if WINDOWS:
            windows = login.all_windows(include_minimized=True)
            return any(is_gfn_window(window) and not self.library.window_is_blocked(window, profile)
                       for window, profile in candidates(windows, self.profiles).values())
        return False

    def open(self, auto=True):
        if auto and self.restart_confirmation is not None:
            self.hide()
            return
        if self.vpn_testing:
            self.show_settings()
            return
        if self.recorder:
            self.recorder.stop.set()
            return
        if self.record_review:
            self.record_review.show(refresh=False)
            return
        if auto and time.monotonic() - self.record_stopped_at < .3:
            return  # A late stop key must never become an automatic login.
        if self.busy:
            if auto and self.runner:
                self.runner.cancel.set()
            return
        was_visible = self.window.get_visible()
        if auto and self.pending_record and was_visible:
            self.hide()  # F9 returns input to the game; a later F9 starts capture.
            return
        self.scan()
        f9_allowed = not auto or self.preferences.data['f9_scope'] == 'always'
        try:
            active = login.active_window()
            f9_allowed = f9_allowed or self.can_open_on_f9(active)
            if not f9_allowed:
                if was_visible:
                    self.hide()
                return
            self.game = self.sessions.select(active)
            if auto and self.visual.owns(self.game) and not was_visible:
                self.visual.pause()
            if auto and self.pending_record and not self.visual.active:
                manager = self.pending_record
                if self.game and active.address == self.game[0].address and manager.record_matches(active):
                    self.start_recording(manager, self.game)
                else:
                    manager.record_setup()
                    manager.say('Focus the matching game and press F9 to start recording.', error=True)
                    manager.show(refresh=False)
                return
            if self.pending_record and not self.visual.active:
                self.pending_record.record_setup()
                self.pending_record.show(refresh=False)
                return
            if auto and self.pending_capture and not self.visual.active:
                manager = self.pending_capture
                if manager.capture_from(active):
                    self.pending_capture = None
                    manager.show()
                else:
                    self.message = 'Focus the matching game with the pointer at your target, then press F9 to capture.'
                    self.render()
                return
            if auto and self.pending and not self.visual.active:
                macro = self.library.get(self.pending)
                if macro and not macro['hidden'] and macro['enabled'] and self.game and active.address == self.game[0].address and macro in self.library.matching(active.title):
                    self.pending = None
                    self.run_macro(macro)
                    return
                self.message = 'Queued macro needs its matching game focused. Open that game and press F9.'
                self.render()
                return
            # Auto-input only when the detected game itself is focused.
            first_login = self.preferences.data['first_f9']
            if auto and first_login and self.game and active.address == self.game[0].address and not self.visual.active:
                if self.library.auto_for(active.title) and self.sessions.claim_first(self.game):
                    self.persist()
                    self.run_login(full=False)
                    return
            if was_visible and auto:
                self.hide()
                return
            if self.game:
                if not first_login:
                    self.message = 'Choose a macro to run for this game.'
                elif not self.library.auto_for(self.game[0].title):
                    self.message = 'No first-F9 login is enabled for this game. Choose a macro or open the library.'
                else:
                    self.message = ('Auto login already attempted. You can rerun it below.'
                                    if session_key(*self.game) in self.sessions.attempted
                                    else 'Focus this game and press F9 to run its first auto login.')
                if self.visual.owns(self.game):
                    self.message = 'Visual workflow paused. Open Visual Workflows in Macro Studio to resume or stop.'
            elif login.is_gfn_idle(active):
                self.message = 'GFN launcher detected. Start a game or use Japan VPN below.'
            else:
                self.message = 'Open a GFN game to run its macros, or open the library to create one.'
        except Exception as exc:
            if was_visible and auto:
                self.hide()
                return
            self.message = str(exc)
            if not f9_allowed:
                return
        if self.stack.get_visible_child_name() == 'settings':
            self.show_settings()
        elif self.manager and self.stack.get_visible_child_name() == 'manager':
            self.manager.show(refresh=False)
        else:
            self.render()

    def label(self, text, style=None, localize=True):
        label = self.Gtk.Label(label=text, xalign=0)
        label.set_line_wrap(True)
        label.set_max_width_chars(42)
        if style:
            label.get_style_context().add_class(style)
        if not localize:
            label._user_content = True
            self.localizer.track(label)
        elif text in KO or any(line in KO for line in text.split('\n')):
            self.localizer.bind(label, 'label', text)
        return label

    def button(self, text, callback, enabled=True, primary=False):
        icons = {'Close': 'window-close-symbolic', 'Manage macros': 'view-grid-symbolic',
                 'Settings': 'preferences-system-symbolic', 'Back': 'go-previous-symbolic',
                 'Find best JP server': 'system-search-symbolic',
                 'Edit': 'document-edit-symbolic', 'Duplicate': 'edit-copy-symbolic',
                 'Hide / Show': 'view-reveal-symbolic', 'Hide': 'view-conceal-symbolic',
                 'Delete': 'user-trash-symbolic', 'Restore': 'edit-undo-symbolic',
                 'Queue for F9': 'media-playback-start-symbolic', 'Run': 'media-playback-start-symbolic',
                 'New action': 'edit-clear-symbolic', 'Record actions': 'media-record-symbolic',
                 'Discard recording': 'edit-undo-symbolic',
                 'Full login': 'media-skip-forward-symbolic',
                 'Toggle anti-AFK': 'input-mouse-symbolic', 'Calibrate login': 'find-location-symbolic',
                 'VPN': 'network-vpn-symbolic', 'Information': 'dialog-information-symbolic',
                 'Force restart GeForce NOW': 'view-refresh-symbolic',
                 'Back to actions': 'go-previous-symbolic', 'Arm recording': 'media-record-symbolic',
                 'Import': 'document-open-symbolic', 'Export': 'document-save-as-symbolic',
                 'Stop': 'media-playback-stop-symbolic', 'Move up': 'go-up-symbolic',
                 'Move down': 'go-down-symbolic', 'Remove action': 'list-remove-symbolic',
                 'Add action': 'list-add-symbolic', 'Save macro': 'document-save-symbolic',
                 'Back to library': 'go-previous-symbolic', 'Back to game macros': 'go-previous-symbolic', 'Capture click position': 'find-location-symbolic'}
        icon = icons.get(text)
        if icon and self.Gtk.IconTheme.get_default().has_icon(icon):
            button = self.Gtk.Button.new_from_icon_name(icon, self.Gtk.IconSize.BUTTON)
            button.set_tooltip_text(text)
            button.get_accessible().set_name(text)
        else:
            button = self.Gtk.Button(label=text)
        button.set_sensitive(enabled and not self.busy)
        if primary:
            button.get_style_context().add_class('primary')
        button.connect('clicked', lambda *_: callback())
        self.prepare_ui(button)
        return button

    def help_button(self, title, detail):
        button = self.button('Information', lambda: show())
        button.get_accessible().set_name(title)
        button.set_tooltip_text(title)
        def show():
            popup = self.Gtk.Popover.new(button)
            content = self.label(detail() if callable(detail) else detail, 'muted')
            content.set_margin_start(12)
            content.set_margin_end(12)
            content.set_margin_top(8)
            content.set_margin_bottom(8)
            popup.add(content)
            popup.connect('closed', lambda current: current.destroy())
            popup.show_all()
            popup.popup()
        return button

    def refresh_tool_status(self):
        if self.preview or self.tool_polling or self.busy or not self.window.get_visible():
            return True
        self.tool_polling = True
        def worker():
            from tool_status import read_tool_status
            status = read_tool_status()
            self.GLib.idle_add(self.apply_tool_status, status)
        threading.Thread(target=worker, daemon=True, name='macro-tool-status').start()
        return True

    def apply_tool_status(self, status):
        self.tool_polling = False
        if self.busy:
            return False
        self.tool_status = status
        self.update_tool_controls()
        return False

    def update_tool_controls(self):
        if not hasattr(self, 'tool_buttons'):
            return
        self.tool_buttons['restart'].set_sensitive(self.can_restart_gfn())
        vpn, afk = self.tool_status['vpn'], self.tool_status['afk']
        button = self.tool_buttons['vpn']
        title = 'Disconnect VPN' if vpn is True else 'Connect Japan VPN' if vpn is False else 'Checking VPN status…'
        button.get_accessible().set_name(self.tr(title))
        button.set_tooltip_text(self.tr(title) + ' · F10' if vpn is not None else self.tr('VPN status unavailable. Waiting for a fresh status check.'))
        button.set_sensitive(vpn is not None and not self.busy and not self.vpn_testing and not self.pending_record)
        style = button.get_style_context()
        (style.add_class if vpn else style.remove_class)('tool-active')
        button = self.tool_buttons['afk']
        title = 'Disable anti-AFK' if afk is True else 'Enable anti-AFK'
        button.set_tooltip_text(self.tr(title if afk is not None else 'Anti-AFK status unavailable'))
        button.get_accessible().set_name(self.tr(title))
        button.set_sensitive(afk is not None and bool(self.game or afk) and not self.busy)
        style = button.get_style_context()
        (style.add_class if afk else style.remove_class)('tool-active')

    def can_restart_gfn(self):
        return not (self.busy or self.recorder or self.pending_record or self.pending
                    or self.pending_capture or self.vpn_testing)

    def dismiss_restart_confirmation(self):
        popup, self.restart_confirmation = self.restart_confirmation, None
        if popup is not None:
            for widget, handler in popup.signal_handlers:
                widget.disconnect(handler)
            popup.signal_handlers.clear()
            popup.key_handler = None
            popup.destroy()
            button = getattr(self, 'tool_buttons', {}).get('restart')
            if button is not None and button.get_mapped() and button.get_sensitive():
                button.grab_focus()

    def request_gfn_restart(self):
        if not self.can_restart_gfn() or self.restart_confirmation is not None:
            return
        Gtk = self.Gtk
        popup = Gtk.EventBox()
        popup.get_style_context().add_class('confirmation-shade')
        popup.set_hexpand(True)
        popup.set_vexpand(True)
        self.restart_confirmation = popup
        popup.signal_handlers = []
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.get_style_context().add_class('confirmation-card')
        content.set_halign(Gtk.Align.CENTER)
        content.set_valign(Gtk.Align.CENTER)
        content.set_size_request(min(320, self.width - 64), -1)
        for side in ('start', 'end', 'top', 'bottom'):
            getattr(content, 'set_margin_' + side)(12)
        title = self.label('Force restart GeForce NOW?', 'section')
        detail = self.label('This turns on the Japan VPN, closes your GFN session and relaunches the app. Unsaved game progress may be lost.', 'muted')
        detail.set_max_width_chars(32 if self.width > 400 else 28)
        content.pack_start(title, False, False, 0)
        content.pack_start(detail, False, False, 0)
        buttons = Gtk.Box(spacing=8)
        cancel = Gtk.Button(label='Cancel')
        confirm = Gtk.Button(label='Force restart')
        for button, source in ((cancel, 'Cancel'), (confirm, 'Force restart')):
            self.localizer.bind(button.get_child(), 'label', source)
            self.localizer.bind(button, 'accessible-name', source)
        confirm.get_style_context().add_class('danger')
        cancel.set_can_default(True)
        confirm.set_can_default(True)
        popup.signal_handlers.append((cancel, cancel.connect('clicked', lambda *_: self.dismiss_restart_confirmation())))
        def restart(*_):
            if self.restart_confirmation is not popup:
                return
            allowed = self.can_restart_gfn()
            self.dismiss_restart_confirmation()
            if allowed:
                from gfn_tools import force_restart_gfn
                self.launch(force_restart_gfn, 'GeForce NOW restarted.', reopen_on_success=False)
        popup.signal_handlers.append((confirm, confirm.connect('clicked', restart)))
        def keyboard(_, event):
            from gi.repository import Gdk
            key = Gdk.keyval_name(event.keyval)
            if key == 'Escape':
                self.dismiss_restart_confirmation()
                return True
            if key in ('Tab', 'ISO_Left_Tab', 'Left', 'Right', 'Up', 'Down'):
                (confirm if self.window.get_focus() is cancel else cancel).grab_focus()
                return True
            if key in ('Return', 'KP_Enter'):
                (confirm if self.window.get_focus() is confirm else cancel).clicked()
                return True
            return True
        popup.key_handler = keyboard
        buttons.pack_end(confirm, False, False, 0)
        buttons.pack_end(cancel, False, False, 0)
        content.pack_start(buttons, False, False, 0)
        popup.add(content)
        popup.cancel_button, popup.confirm_button = cancel, confirm
        def outside(_, event):
            x, y = content.translate_coordinates(popup, 0, 0)
            if event.window == popup.get_window() and not (x <= event.x < x + content.get_allocated_width()
                    and y <= event.y < y + content.get_allocated_height()):
                self.dismiss_restart_confirmation()
                return True
            return False
        popup.signal_handlers.append((popup, popup.connect('button-press-event', outside)))
        from gi.repository import Atk
        popup.get_accessible().set_role(Atk.Role.DIALOG)
        self.localizer.bind(popup, 'accessible-name', 'Force restart GeForce NOW?')
        def cleared(current):
            if self.restart_confirmation is current:
                self.restart_confirmation = None
        popup.signal_handlers.append((popup, popup.connect('destroy', cleared)))
        self.overlay.add_overlay(popup)
        self.prepare_ui(popup)
        popup.show_all()
        cancel.grab_focus()

    def vpn_action(self, global_shortcut=False):
        if self.pending_record or self.busy or self.vpn_testing:
            return
        connected = self.tool_status['vpn']
        if WINDOWS:
            from tool_status import toggle_vpn
            def work():
                if not toggle_vpn():
                    raise ValueError('Another VPN or macro action is running.')
            self.launch(work, 'VPN action completed', reopen_on_success=not global_shortcut)
            return
        if connected is None:
            self.refresh_tool_status()
            return
        command = 'vpn-disconnect' if connected else 'vpn'
        self.tool_status['vpn'] = None
        self.update_tool_controls()
        self.run_command([str(Path.home() / '.local/bin/gfn-login'), command],
                         'VPN disconnected' if connected else 'VPN action completed', idle=not connected, vpn=True)

    def windows_tools_tick(self):
        from windows_tools import tick
        tick(self)
        return True

    def windows_calibrate_point(self):
        from windows_tools import capture_calibration, calibration_status
        if calibration_status(self) and not self.busy:
            capture_calibration(self)

    def hotspot_action(self, focused_only=False):
        if focused_only and not login.GFN_APP_CLASSES.search(login.active_window().window_class):
            return False
        from windows_tools import toggle_hotspot
        self.launch(toggle_hotspot, 'Mobile Hotspot toggled.', reopen_on_success=False)
        return False

    def start_vpn_search(self):
        if self.vpn_testing or self.busy or self.preview:
            return
        if WINDOWS:
            from windows_vpn import status
            if status() is None:
                self.vpn_test_state = {'error': 'Install WireGuard and configure the Japan tunnel first.'}
                self.refresh_vpn_search()
                return
        if self.recorder or self.pending_record or self.pending or self.pending_capture:
            self.vpn_test_state = {'error': 'Finish the pending macro action before testing VPN servers.'}
            self.refresh_vpn_search()
            return
        self.vpn_cancel = threading.Event()
        self.vpn_testing = True
        self.vpn_test_state = {'stage': 'fetch'}
        self.refresh_vpn_search()
        profiles = self.profiles[:]
        def worker():
            from vpn_servers import optimize, SearchCancelled
            def progress(stage, index, total, server, rows):
                self.GLib.idle_add(self.vpn_search_progress, dict(
                    stage=stage, index=index, total=total, server=server, rows=rows))
            try:
                if WINDOWS:
                    from windows_vpn_scan import scan
                    report = scan(progress, self.vpn_cancel)
                else:
                    report = optimize(profiles, progress, self.vpn_cancel, allow_in_game=True)
                self.GLib.idle_add(self.vpn_search_finished, report, '')
            except SearchCancelled:
                self.GLib.idle_add(self.vpn_search_finished, None, 'Server test cancelled. Previous server kept.')
            except ValueError as error:
                self.GLib.idle_add(self.vpn_search_finished, None, str(error))
            except Exception as error:
                print(f'VPN server test failed ({type(error).__name__}).', flush=True)
                self.GLib.idle_add(self.vpn_search_finished, None, 'VPN server test failed. Check the VPN connection.')
        # A normal service shutdown cancels and lets this worker clean up.
        threading.Thread(target=worker, name='macro-vpn-search', daemon=False).start()

    def recover_vpn_probes(self):
        def worker():
            from tool_status import vpn_lock
            from vpn_servers import SplitTunnel
            try:
                with vpn_lock():
                    backend = SplitTunnel()
                    try:
                        backend.cleanup_stale()
                    finally:
                        backend.close()
            except Exception:
                pass  # An active scan owns the lock; its own finally cleans up.
        threading.Thread(target=worker, name='macro-vpn-recovery', daemon=True).start()
        return False

    def cancel_vpn_search(self):
        self.vpn_cancel.set()

    def vpn_search_progress(self, state):
        self.vpn_test_state = state
        self.refresh_vpn_search()
        return False

    def vpn_search_finished(self, report, error):
        self.vpn_testing = False
        self.vpn_test_state = {'error': error} if error else {}
        if report:
            self.vpn_report = report
        self.refresh_vpn_search()
        self.refresh_tool_status()
        return False

    def refresh_vpn_search(self):
        if self.settings_page:
            self.settings_page.vpn.refresh()
        self.update_tool_controls()

    def render(self):
        self.dismiss_restart_confirmation()
        Gtk = self.Gtk
        if not self.preview:
            self.Layer.set_keyboard_mode(self.window, self.Layer.KeyboardMode.EXCLUSIVE)
        self.stack.set_visible_child_name('picker')
        self.resize_view(self.height)
        expanded = {kind: section.get_expanded() for kind, section in getattr(self, 'quick_sections', {}).items()}
        for child in self.panel.get_children():
            child.destroy()
        header = Gtk.Box(spacing=16)
        if self.game:
            name = game_name(self.game[0].title)
            title = self.label('', 'heading', localize=False)
            title.set_line_wrap_mode(2)  # Wrap long game names within compact windows.
            title.set_max_width_chars(22)
            def update_title(label, name=name):
                text = self.tr("{game} macros").format(game=name)
                label.set_text(text)
                label.set_tooltip_text(text)
            self.localizer.bind(title, 'render', update_title)
        else:
            title = self.label('GFN macros', 'heading')
        header.pack_start(title, True, True, 0)
        header.pack_end(self.button('Close', self.close), False, False, 0)
        header.pack_end(self.button('Settings', self.show_settings), False, False, 0)
        header.pack_end(self.button('Manage macros', self.manage), False, False, 0)
        if self.pending or self.pending_capture or self.pending_record:
            header.pack_end(self.button('Stop', self.cancel_pending), False, False, 0)
        self.panel.pack_start(header, False, False, 0)
        if not self.game:
            self.panel.pack_start(self.label('No active game', 'game'), False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.quick_scroll = scroll
        # Matching game first; every installed profile remains visible.
        matching = login.detect_profile(self.legacy_profiles, self.game[0].title) if self.game else None
        macros = self.library.matching(self.game[0].title) if self.game else [m for m in self.library.macros if not m['hidden'] and m['enabled']]
        groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.quick_sections, self.quick_lists = {}, {}
        self.quick_selected = None
        for kind, title in TYPES:
            members = [macro for macro in macros if macro['kind'] == kind]
            section = Gtk.Expander()
            section.get_style_context().add_class('macro-section')
            heading = Gtk.Label()
            self.localizer.bind(heading, 'render', lambda label, title=title, count=len(members):
                                label.set_markup(category_markup(self.tr(title), count)))
            section.set_label_widget(heading)
            section.get_accessible().set_name(title)
            section.set_expanded(expanded.get(kind, True))
            section.set_tooltip_text('Collapse or expand ' + title.lower())
            rows = Gtk.ListBox()
            rows.get_style_context().add_class('macro-list')
            rows.set_activate_on_single_click(False)
            rows.set_selection_mode(Gtk.SelectionMode.SINGLE)
            for macro in members:
                row = Gtk.ListBoxRow()
                row.macro = macro
                content = Gtk.Box(spacing=8)
                content.get_style_context().add_class('macro-row')
                name = self.label('')
                name._user_content = True
                self.localizer.bind(name, 'render', lambda label, macro=macro: label.set_markup(
                    macro_markup(macro['game'], len(macro['steps']), self.tr)))
                name.set_line_wrap(False)
                name.set_ellipsize(3)
                name.set_tooltip_text(macro['name'])
                content.pack_start(name, True, True, 0)
                row.add(content)
                rows.add(row)
            if not members:
                empty = Gtk.ListBoxRow()
                empty.set_selectable(False)
                empty.set_activatable(False)
                message = self.label('No macros to show.', 'macro-empty')
                empty.add(message)
                rows.add(empty)
            section.add(rows)
            groups.pack_start(section, False, False, 0)
            self.quick_sections[kind] = section
            self.quick_lists[kind] = rows
        self.quick_rows = self.quick_lists['login']
        scroll.add(groups)
        self.panel.pack_start(scroll, True, True, 0)
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.quick_footer = footer
        self.panel.pack_end(footer, False, False, 0)
        controls = Gtk.Box(spacing=8)
        controls.get_style_context().add_class('context-bar')
        controls.set_no_show_all(True)
        play = self.button('Run', lambda: self.run_macro(self.quick_selected.macro), bool(self.game), True)
        full = self.button('Full login', lambda: self.run_macro(self.quick_selected.macro, True), bool(self.game))
        full.set_no_show_all(True)
        controls.pack_start(play, False, False, 0)
        controls.pack_start(full, False, False, 0)
        def selected(current, row):
            macro = getattr(row, 'macro', None)
            self.quick_selected = None
            if macro:
                for other in self.quick_lists.values():
                    if other is not current:
                        other.unselect_all()
                self.quick_selected = row
            controls.set_visible(bool(macro))
            if macro:
                play.show()
                full.set_visible(macro['kind'] == 'login' and bool(macro['source_profile'] or macro.get('login_limit', 0)))
        for kind, rows in self.quick_lists.items():
            rows.connect('row-selected', selected)
            rows.connect('row-activated', lambda _, row: self.run_macro(row.macro) if self.game and hasattr(row, 'macro') else None)
            self.quick_sections[kind].connect('notify::expanded', lambda section, _, rows=rows: rows.unselect_all() if not section.get_expanded() else None)
        footer.pack_start(controls, False, False, 0)
        tools = Gtk.Box(spacing=4)
        tools.get_style_context().add_class('context-bar')
        tools.get_accessible().set_name('Utilities')
        self.tool_buttons = dict(
            vpn=self.button('VPN', self.vpn_action),
            afk=self.button('Toggle anti-AFK', lambda: self.run_command(
                [str(Path.home() / '.local/bin/gfn-anti-afk'), 'toggle'], 'Anti-AFK toggled')),
            calibrate=self.button('Calibrate login', lambda: self.run_command(
                [str(Path.home() / '.local/bin/gfn-login'), 'calibrate'], 'Calibration started', target=True), bool(matching)),
            restart=self.button('Force restart GeForce NOW', self.request_gfn_restart))
        self.tool_buttons['restart'].get_style_context().add_class('danger')
        from ui_vpn_hover import VpnHover
        self.vpn_hover = VpnHover(self, self.tool_buttons['vpn'])
        for button in self.tool_buttons.values():
            tools.pack_start(button, False, False, 0)
        self.status_button = self.help_button('Status and shortcuts', lambda: self.message +
            '\n\nF9 · open / stop\nF10 · VPN on / off\n↑ ↓ · select   Enter · run   Esc · back\nTab · next control   Ctrl+L · Library\nGuide · open   D-pad · navigate   A · select   B · back\nLB / RB · previous / next control\nF9 behavior can be changed in Settings.')
        self.status_button.set_tooltip_text(self.message + '\nStatus and shortcuts')
        if self.message.startswith(('Action stopped:', 'Detection unavailable:')):
            self.status_button.get_style_context().add_class('danger')
            self.status_button.set_image(Gtk.Image.new_from_icon_name('dialog-warning-symbolic', Gtk.IconSize.BUTTON))
        tools.pack_end(self.status_button, False, False, 0)
        footer.pack_start(tools, False, False, 0)
        self.update_tool_controls()
        self.prepare_ui()
        self.window.show_all()
        self.refresh_tool_status()

    def arm_recording(self, manager):
        from tool_status import vpn_lock
        # Serialize arming with global F10 before publishing the active guard.
        with vpn_lock() if not self.preview else nullcontext():
            self._arm_recording(manager)

    def _arm_recording(self, manager):
        if self.visual.active:
            raise ValueError('Stop the visual workflow before recording.')
        if self.vpn_testing:
            return
        if self.busy:
            raise ValueError('Stop playback before recording.')
        self.pending = self.pending_capture = None
        self.pending_record = manager
        self.playback.update(armed=True, recording=False, running=False, name='Recording armed', step=0, total=0)
        self.publish_playback()
        manager.update_recording_controls()
        self.message = 'Recording armed. Focus the matching game and press F9 to start. Press F9 again to stop.'
        self.hide()
        manager.hide()

    def start_recording(self, manager, game):
        if self.visual.active:
            return
        from macro_recorder import Recorder
        request = manager.record_request.copy()
        def verify():
            active = self.verify_target(game)
            if not manager.record_matches(active):
                raise login.LoginError('Game changed.')
            return active
        self.verify_target(game)
        if self.playback_widget:
            self.playback_widget.hide()
        self.recorder = Recorder(verify, lambda window: manager.record_point(window, request),
                                 lambda steps, reason: self.GLib.idle_add(self.recorded, steps, reason),
                                 lambda count, elapsed, hint: self.GLib.idle_add(self.record_progress, count, elapsed, hint),
                                 keyboard=request['keyboard'], mouse=request['mouse'], capacity=request['capacity'])
        self.record_manager = manager
        self.pending_record = None
        self.busy = True
        self.playback.update(armed=False, recording=True, running=False, name='Recording', step=0, elapsed=0)
        self.publish_playback()
        if not self.preview:
            self.Layer.set_keyboard_mode(self.window, self.Layer.KeyboardMode.NONE)
        self.hide()
        manager.hide()
        self.window.get_display().sync()
        try:
            if self.recording_widget is None:
                from ui_recording import RecordingWidget
                self.recording_widget = RecordingWidget(self)
            self.recording_widget.show(game[0])
            self.recorder.start()
        except Exception:
            self.recorded([], 'Recording could not start.')
            raise

    def record_progress(self, count, elapsed, hint=None):
        if self.recorder:
            self.playback.update(step=count, elapsed=elapsed)
            self.publish_playback()
            if self.recording_widget:
                self.recording_widget.update(count, elapsed, hint)
        return False

    def recorded(self, steps, reason):
        if self.recording_widget:
            self.recording_widget.hide()
        manager = self.record_manager
        explicit_stop = bool(self.recorder and self.recorder.stop.is_set())
        self.recorder = self.record_manager = None
        self.busy = False
        self.record_stopped_at = time.monotonic()
        self.playback.update(recording=False, armed=False, review=bool(manager))
        self.message = reason
        if manager:
            manager.accept_recording(steps, reason)
            self.record_review = manager
        self.publish_playback()
        if manager and explicit_stop:
            manager.show(refresh=False)
        # Focus/device/limit stops keep the draft ready without opening over
        # another app. The next F9/bar/Guide opens review before auto login.
        return False

    def verify_target(self, game):
        if not game:
            raise login.LoginError('No game selected.')
        active = login.active_window()
        detected = login.detect_profile(self.profiles, active.title)
        if session_key(active, detected) != session_key(*game):
            raise login.LoginError('Focus the detected game and try again. No input was sent.')
        if not login.GFN_APP_CLASSES.search(active.window_class):
            raise login.LoginError('Selected window is no longer a GFN game.')
        return active

    def launch(self, work, success, *, reopen_on_success=True):
        if self.busy or self.preview or self.vpn_testing or self.visual.active:
            return
        self.busy = True
        # Hide the entire surface so it cannot intercept scripted mouse clicks.
        self.Layer.set_keyboard_mode(self.window, self.Layer.KeyboardMode.NONE)
        self.hide()
        # Release layer keyboard focus before starting injected input.
        self.window.get_display().sync()
        def worker():
            reopen = reopen_on_success
            outcome = 'completed'
            try:
                work()
                message = success
            except Exception as exc:
                message = f'Action stopped: {exc}'
                reopen = True
                outcome = 'cancelled' if isinstance(exc, Cancelled) else 'failed'
                print(message, flush=True)
            self.GLib.idle_add(self.finished, message, reopen, outcome)
        try:
            if self.runner and self.playback.get('running') and self.game:
                if self.playback_widget is None:
                    from ui_playback import PlaybackWidget
                    self.playback_widget = PlaybackWidget(self)
                self.playback_widget.show(self.game[0], self.playback['name'], self.playback['total'],
                                          self.playback.get('repeats', 1))
        except Exception as exc:
            print(f'Playback widget unavailable: {type(exc).__name__}', flush=True)
            if self.playback_widget:
                with suppress(Exception):
                    self.playback_widget.window.destroy()
            self.playback_widget = None
        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception as exc:
            self.finished(f'Action stopped: {exc}', outcome='failed')

    def finished(self, message, reopen=True, outcome='completed'):
        if self.playback_widget and self.runner is not None and self.playback.get('running'):
            try:
                self.playback_widget.finish(outcome, self.runner.completed)
            except Exception as exc:
                print(f'Playback widget unavailable: {type(exc).__name__}', flush=True)
                with suppress(Exception):
                    self.playback_widget.window.destroy()
                self.playback_widget = None
        self.busy = False
        self.playback['running'] = False
        self.publish_playback()
        self.runner = None
        self.message = message
        self.scan()
        if not reopen:
            return False
        if self.manager and self.stack.get_visible_child_name() == 'manager':
            self.manager.say(message)
            self.manager.show()
        else:
            self.render()
        return False

    def run_login(self, full=False):
        if not self.game:
            return
        macro = self.library.auto_for(self.game[0].title) or next((m for m in self.library.matching(self.game[0].title) if m['kind'] == 'login'), None)
        if macro:
            self.run_macro(macro, full)

    def run_macro(self, macro, full=False):
        game = self.game
        if self.busy or self.vpn_testing or self.visual.active or macro['hidden'] or not macro['enabled']:
            return
        if macro['kind'] == 'login' and self.sessions.claim_first(game):
            self.persist()
        runner = MacroRunner(self.library, lambda: self.verify_target(game),
                             lambda *args: self.GLib.idle_add(self.progress, *args))
        self.runner = runner
        if self.manager:
            self.manager.hide()
        limit = macro.get('login_limit', 0)
        total = limit if macro['kind'] == 'login' and not full and limit else len(macro['steps'])
        self.playback = dict(running=True, name=macro['name'], step=0, total=total, repeats=macro['repeat'])
        self.publish_playback()
        def work():
            start = time.monotonic()
            status = 'Completed'
            try:
                active = self.verify_target(game)
                if macro not in self.library.matching(active.title):
                    raise ValueError('This macro does not match the focused game.')
                login.SESSION_GAME_TITLE = active.title
                runner.run(macro, self.legacy_profiles, self.settings, full=full)
            except Cancelled:
                status = 'Cancelled'
                raise
            except subprocess.CalledProcessError:
                status = 'Failed'
                raise ValueError('An input command failed. Check the input service.') from None
            except Exception:
                status = 'Failed'
                raise
            finally:
                if not self.preview:
                    self.library.record(macro['id'], macro['name'], status, time.monotonic() - start, runner.completed)
        self.launch(work, f'{macro["name"]} completed. F9 now opens the list.',
                    reopen_on_success=macro['kind'] != 'login' and self.preferences.data['reopen_after_macro'])

    def run_command(self, argv, success, target=False, idle=False, vpn=False):
        game = self.game
        def work():
            if target:
                self.verify_target(game)
            if idle:
                active = login.active_window()
                if not login.is_gfn_idle(active) and not (game and active.address == game[0].address):
                    raise login.LoginError('Focus the GFN launcher or detected game before connecting.')
            if WINDOWS:
                from windows_tools import run_utility
                run_utility(Path(argv[0]).name, argv[1])
                return
            from tool_status import vpn_lock
            with vpn_lock() if vpn else nullcontext():
                subprocess.run(argv, check=True, timeout=120)
        self.launch(work, success)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', nargs='?', default='manage', choices=('daemon', 'quit', 'f9', 'show', 'hide', 'close', 'toggle', 'vpn-toggle', 'pointer', 'manage', 'settings', 'preview', 'preview-manager', 'preview-editor', 'preview-widget'))
    parser.add_argument('--width', type=int, default=430)
    parser.add_argument('--screen', help='Monitor containing the bar widget')
    parser.add_argument('--point', type=int, nargs=2, help=argparse.SUPPRESS)
    parser.add_argument('--surface', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.action == 'vpn-toggle':
        from tool_status import toggle_vpn
        try:
            toggle_vpn()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            login.show_widget('VPN', str(exc), tone='error', expires=5, visible=False)
            raise SystemExit(1)
    elif args.action == 'pointer':
        if not args.point:
            parser.error('pointer requires --point')
        try:
            send(json.dumps(dict(action='pointer', x=args.point[0], y=args.point[1], surface=args.surface)))
        except OSError:
            pass  # A dismissal event must never start the app/service.
    elif args.action.startswith('preview'):
        picker = Picker(preview=True, width=args.width)
        if args.action == 'preview':
            picker.render()
            picker.GLib.timeout_add(500, lambda: save_preview(picker))
        elif args.action == 'preview-widget':
            picker.manage()
            def capture_widget():
                pixbuf = picker.window.get_pixbuf()
                pixbuf.savev(str(ROOT / f'preview-widget-{pixbuf.get_width()}.png'), 'png', [], [])
                picker.Gtk.main_quit()
                return False
            picker.GLib.timeout_add(500, capture_widget)
        else:
            from macro_manager import Manager
            manager = Manager(picker, offscreen=True, width=args.width)
            manager.show()
            if args.action == 'preview-editor':
                from macro_library import new_macro
                demo = new_macro('sequence')
                demo.update(name='Confirm menu', game='Demo game', title_re='Demo game',
                            steps=['wait:500', 'key:Enter', 'wait:250', 'key:Escape'])
                manager.edit(demo)
            def capture():
                pixbuf = manager.window.get_pixbuf()
                pixbuf.savev(str(ROOT / f'{args.action}-{pixbuf.get_width()}.png'), 'png', [], [])
                picker.Gtk.main_quit()
                return False
            picker.GLib.timeout_add(500, capture)
        picker.Gtk.main()
    elif args.action == 'quit' and WINDOWS:
        with suppress(OSError):
            send('quit')
    elif args.action == 'daemon' and WINDOWS:
        from windows_host import file_lock
        try:
            with file_lock(RUNTIME / 'daemon.lock'):
                ENDPOINT.unlink(missing_ok=True)
                try:
                    picker = Picker()
                    picker.Gtk.main()
                finally:
                    ENDPOINT.unlink(missing_ok=True)
        except BlockingIOError:
            return
    elif args.action == 'daemon':
        import fcntl
        RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (RUNTIME / 'daemon.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            SOCKET.unlink(missing_ok=True)
            try:
                picker = Picker()
                def shutdown():
                    picker.visual.stop()
                    if picker.playback_widget:
                        picker.playback_widget.hide()
                    if picker.recording_widget:
                        picker.recording_widget.hide()
                    if picker.recorder:
                        picker.recorder.stop.set()
                    picker.cancel_vpn_search()
                    picker.dismiss_restart_confirmation()
                    picker.gamepad.close()
                    picker.Gtk.main_quit()
                    return False
                for signum in (signal.SIGTERM, signal.SIGINT):
                    picker.GLib.unix_signal_add(picker.GLib.PRIORITY_DEFAULT, signum, shutdown)
                picker.Gtk.main()
            finally:
                SOCKET.unlink(missing_ok=True)
    else:
        request(args.action, args.screen)


def save_preview(picker):
    pixbuf = picker.window.get_pixbuf()
    pixbuf.savev(str(ROOT / f'preview-{pixbuf.get_width()}.png'), 'png', [], [])
    picker.Gtk.main_quit()
    return False


if __name__ == '__main__':
    main()
