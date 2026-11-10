"""Small, passive recording HUD. Uses the recorder's existing progress events."""
import json
import subprocess

from ui_theme import TOKENS
from ui_popup import monitor_size


def elapsed_text(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


def recording_margins(geometry, area, monitors):
    top = max(0, area.y - geometry.y)
    right = max(0, geometry.x + geometry.width - area.x - area.width)
    for monitor in monitors:
        if (monitor['x'], monitor['y'], *monitor_size(monitor)) == (
                geometry.x, geometry.y, geometry.width, geometry.height):
            reserved = monitor.get('reserved', [0, 0, 0, 0])
            top, right = max(top, reserved[1]), max(right, reserved[2])
            break
    return tuple(max(TOKENS['screen_inset'], value + TOKENS['popup_gap']) for value in (top, right))


class RecordingWidget:
    def __init__(self, picker, *, title='Recording progress',
                 namespace='gfn-recording-progress', icon='media-record-symbolic'):
        self.picker = picker
        Gtk, Layer = picker.Gtk, picker.Layer
        self.window = Gtk.OffscreenWindow() if picker.preview else Gtk.Window(title=title)
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_accept_focus(False)
        self.window.set_focus_on_map(False)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.get_style_context().add_class('recording-window')
        visual = self.window.get_screen().get_rgba_visual()
        if visual:
            self.window.set_visual(visual)
        # Union of no interactive child windows is an empty input region.
        # GDK creates the region internally, without an extra Cairo binding.
        self.window.connect('realize', self.make_passive)
        self.window.connect('map', self.make_passive)
        if not picker.preview:
            Layer.init_for_window(self.window)
            Layer.set_namespace(self.window, namespace)
            Layer.set_layer(self.window, Layer.Layer.OVERLAY)
            Layer.set_keyboard_mode(self.window, Layer.KeyboardMode.NONE)
            Layer.set_exclusive_zone(self.window, -1)
            for edge in (Layer.Edge.TOP, Layer.Edge.RIGHT):
                Layer.set_anchor(self.window, edge, True)
                Layer.set_margin(self.window, edge, TOKENS['screen_inset'])
        row = Gtk.Box(spacing=TOKENS['space_md'])
        dot = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU)
        self.indicator = dot
        dot.set_pixel_size(12)
        dot.get_style_context().add_class('recording-dot' if icon == 'media-record-symbolic' else 'playback-dot')
        row.pack_start(dot, False, False, 0)
        self.clock = Gtk.Label()
        self.clock.get_style_context().add_class('recording-clock')
        self.clock.set_width_chars(5)
        row.pack_start(self.clock, False, False, 0)
        self.count = Gtk.Label(xalign=0)
        self.count.set_width_chars(10)
        self.count.get_style_context().add_class('muted')
        row.pack_start(self.count, False, False, 0)
        shortcut = Gtk.Box(spacing=TOKENS['space_xs'])
        self.shortcut = shortcut
        shortcut.get_style_context().add_class('recording-shortcut')
        stop = Gtk.Image.new_from_icon_name('media-playback-stop-symbolic', Gtk.IconSize.MENU)
        stop.set_pixel_size(10)
        shortcut.pack_start(stop, False, False, 0)
        shortcut.pack_start(Gtk.Label(label='F9'), False, False, 0)
        row.pack_start(shortcut, False, False, 0)
        surface = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=TOKENS['space_xs'])
        self.surface = surface
        surface.get_style_context().add_class('recording-hud')
        surface.pack_start(row, False, False, 0)
        self.latest = Gtk.Label(xalign=0)
        self.latest.set_ellipsize(3)
        self.latest.set_width_chars(1)
        self.latest.set_max_width_chars(1)
        self.latest.get_style_context().add_class('muted')
        surface.pack_start(self.latest, False, False, 0)
        self.window.add(surface)
        self.actions = self.seconds = 0
        self.last_action = None
        picker.localizer.bind(self.count, 'render', lambda *_: self.render())
        picker.localizer.bind(self.window, 'accessible-name', title)
        picker.window.connect('destroy', lambda *_: self.window.destroy())

    def show(self, game):
        self.last_action = None
        self.update(0, 0)
        if not self.picker.preview:
            display = self.window.get_display()
            monitor = display.get_monitor_at_point(game.x + game.width // 2, game.y + game.height // 2)
            if monitor:
                self.picker.Layer.set_monitor(self.window, monitor)
                area, geometry = monitor.get_workarea(), monitor.get_geometry()
                # Wayland GDK reports full-monitor workareas on this host.
                try:
                    monitors = json.loads(subprocess.check_output(['hyprctl', 'monitors', '-j'], timeout=1))
                    top, right = recording_margins(geometry, area, monitors)
                except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError):
                    top, right = recording_margins(geometry, area, [])
                self.picker.Layer.set_margin(self.window, self.picker.Layer.Edge.TOP, top)
                self.picker.Layer.set_margin(self.window, self.picker.Layer.Edge.RIGHT, right)
        self.window.show_all()

    def make_passive(self, window):
        surface = window.get_window()
        for child in surface.get_children():
            child.set_pass_through(True)
        surface.set_child_input_shapes()
        surface.set_pass_through(True)

    def update(self, count, elapsed, hint=None):
        self.actions, self.seconds = max(0, int(count)), max(0, int(elapsed))
        if hint is not None:
            self.last_action = hint
        self.render()

    def render(self):
        self.clock.set_text(elapsed_text(self.seconds))
        source = '{count} action' if self.actions == 1 else '{count} actions'
        self.count.set_text(self.picker.tr(source).format(count=self.actions))
        description = self.picker.tr('{elapsed} elapsed, {count} actions. Press F9 to stop recording.').format(
            elapsed=elapsed_text(self.seconds), count=self.actions)
        if self.last_action is None:
            action = self.picker.tr('Waiting for input')
        else:
            source, values = self.last_action
            action = self.picker.tr(source).format(**{
                key: self.picker.tr(value) if key == 'button' else value for key, value in values.items()})
        self.latest.set_text(action)
        description += ' ' + action
        self.window.get_accessible().set_description(description)
        self.window.set_tooltip_text(description)

    def hide(self):
        self.window.hide()
