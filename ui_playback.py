"""Passive playback HUD sharing recording's placement and input-free surface."""
import time

from ui_recording import RecordingWidget, elapsed_text
from ui_theme import TOKENS

RESULT_HOLD_MS = 3000


ACTION_LABELS = {
    'wait': 'Waiting', 'id': 'Enter saved account', 'pw': 'Enter saved password',
    'key': 'Key press', 'down': 'Key pressed', 'up': 'Key released',
    'click': 'Mouse click', 'move': 'Move pointer', 'button': 'Mouse button',
    'wheel': 'Scroll', 'text': 'Text entry', 'clear': 'Clear field',
    'enter': 'Enter', 'tab': 'Tab', 'shifttab': 'Shift+Tab',
}


class PlaybackWidget(RecordingWidget):
    def __init__(self, picker):
        self.macro_name = ''
        self.total = self.step = 0
        self.repeat = self.repeats = 1
        self.action = 'Starting playback'
        self.started = 0
        self.timer = None
        self.dismiss_timer = None
        self.outcome = None
        self.completed = 0
        self.stats = None
        super().__init__(picker, title='Playback progress', namespace='gfn-playback-progress',
                         icon='media-playback-start-symbolic')
        self.count.set_width_chars(7)
        self.count.set_xalign(1)
        self.count.get_parent().set_child_packing(self.count, True, True, 0, picker.Gtk.PackType.START)
        self.window.set_size_request(TOKENS['playback_width'], -1)
        self.stats = picker.Gtk.Box(spacing=TOKENS['space_md'])
        self.stats.get_style_context().add_class('playback-stats')
        self.repeat_stat = picker.Gtk.Label(xalign=0)
        self.action_stat = picker.Gtk.Label(xalign=0)
        self.total_stat = picker.Gtk.Label(xalign=1)
        for label in (self.repeat_stat, self.action_stat, self.total_stat):
            self.stats.pack_start(label, True, True, 0)
        self.surface.pack_start(self.stats, False, False, 0)
        self.surface.reorder_child(self.stats, 1)
        self.render()
        self.window.connect('destroy', lambda *_: self.stop_timer())

    def show(self, game, name, total, repeats=1):
        self.stop_timer()
        self.macro_name = name
        self.total, self.step = total, 0
        self.repeat, self.repeats = 1, repeats
        self.outcome = None
        self.completed = 0
        self.action = 'Starting playback'
        self.started = time.monotonic()
        super().show(game)
        # Keep the clock alive during long waits, without publishing extra state.
        self.timer = self.picker.GLib.timeout_add(250, self.tick)

    def set_progress(self, step, total, repeat, repeats, action):
        if self.outcome is not None:
            return
        self.step, self.total = step, total
        self.repeat, self.repeats = repeat, repeats
        # Progress is emitted before an action begins, not after it completes.
        self.completed = (repeat - 1) * total + max(0, step - 1)
        # Never put action values (typed text, accounts or passwords) in the HUD.
        self.action = ACTION_LABELS.get(action.split(':', 1)[0], 'Action')
        self.tick()

    def tick(self):
        if self.outcome is not None:
            return False
        completed = getattr(self.picker.runner, 'completed', None)
        if type(completed) is int:
            self.completed = min(self.total * self.repeats, max(0, completed))
        self.update(self.step, time.monotonic() - self.started)
        return True

    def finish(self, outcome='completed', completed=None):
        if not self.window.get_visible() or self.outcome is not None:
            return
        self.stop_timer()
        self.outcome = outcome
        self.action = {'completed': 'Completed', 'cancelled': 'Cancelled', 'failed': 'Failed'}[outcome]
        if type(completed) is int:
            self.completed = min(self.total * self.repeats, max(0, completed))
        if outcome == 'completed':
            self.step, self.repeat = self.total, self.repeats
            self.completed = self.total * self.repeats
        self.update(self.step, time.monotonic() - self.started)
        self.dismiss_timer = self.picker.GLib.timeout_add(RESULT_HOLD_MS, self.dismiss)

    def dismiss(self):
        self.dismiss_timer = None
        super().hide()
        return False

    def render(self):
        elapsed = elapsed_text(self.seconds)
        total = self.total * self.repeats
        percent = self.completed * 100 // total if total else 0
        self.clock.set_text(elapsed)
        self.count.set_text(f'{percent}%')
        if self.stats is not None:
            self.repeat_stat.set_text(f'↻ {self.repeat}/{self.repeats}')
            self.action_stat.set_text(f'# {self.step}/{self.total}')
            self.total_stat.set_text(f'Σ {self.completed}/{total}')
        icons = {'completed': 'emblem-ok-symbolic', 'cancelled': 'media-playback-stop-symbolic',
                 'failed': 'dialog-warning-symbolic'}
        self.indicator.set_from_icon_name(icons.get(self.outcome, 'media-playback-start-symbolic'),
                                          self.picker.Gtk.IconSize.MENU)
        context = self.indicator.get_style_context()
        for outcome in icons:
            context.remove_class('playback-' + outcome)
        if self.outcome is None:
            self.shortcut.show_all()
        else:
            context.add_class('playback-' + self.outcome)
            self.shortcut.hide()
        action = self.picker.tr(self.action)
        self.latest.set_text(f'{action} · {self.macro_name}' if self.macro_name else action)
        description = self.picker.tr(
            '{elapsed} elapsed. {percent}% complete ({done}/{total}).').format(
                elapsed=elapsed, percent=percent, done=self.completed, total=total)
        description += ' ' + self.picker.tr('Repeat {repeat} of {repeats}.').format(
            repeat=self.repeat, repeats=self.repeats)
        description += ' ' + self.picker.tr('{completed} repeats complete. Action {step} of {total}.').format(
            completed=self.completed // self.total if self.total else 0, step=self.step, total=self.total)
        if self.outcome is None:
            description += ' ' + self.picker.tr('Press F9 to stop playback.')
        description += f' {self.macro_name}. {action}'
        self.window.get_accessible().set_description(description)
        self.window.set_tooltip_text(description)

    def stop_timer(self):
        if self.timer is not None:
            self.picker.GLib.source_remove(self.timer)
            self.timer = None
        if self.dismiss_timer is not None:
            self.picker.GLib.source_remove(self.dismiss_timer)
            self.dismiss_timer = None

    def hide(self):
        self.stop_timer()
        super().hide()
