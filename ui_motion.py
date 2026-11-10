"""Short native GTK transitions, with no playback/hide delays or extra timers."""
from ui_theme import TOKENS
import weakref


class Motion:
    def __init__(self, Gtk, enabled=True):
        self.Gtk, self.enabled = Gtk, enabled
        self.stacks = weakref.WeakSet()

    def set_enabled(self, enabled):
        self.enabled = enabled
        for stack in self.stacks:
            stack.set_transition_duration(TOKENS['motion_page_ms'] if enabled else 0)

    def allowed(self):
        settings = self.Gtk.Settings.get_default()
        return self.enabled and bool(settings and settings.get_property('gtk-enable-animations'))

    def stack(self, stack, slide=False):
        self.stacks.add(stack)
        stack.set_transition_type(self.Gtk.StackTransitionType.SLIDE_LEFT_RIGHT if slide
                                  else self.Gtk.StackTransitionType.CROSSFADE)
        # GtkStack itself watches gtk-enable-animations, including live changes.
        stack.set_transition_duration(TOKENS['motion_page_ms'] if self.enabled else 0)
        return stack

    def notebook(self, notebook):
        fade = PageFade(self, TOKENS['motion_page_ms'])
        notebook.connect('switch-page', lambda book, page, _: fade.start(page, book.get_mapped()))
        notebook.connect('destroy', lambda *_: fade.cancel())
        return fade


class PageFade:
    def __init__(self, motion, duration):
        self.motion, self.duration = motion, duration
        self.widget = self.tick = self.started = None

    def cancel(self):
        if self.widget is not None:
            if self.tick is not None:
                self.widget.remove_tick_callback(self.tick)
            self.widget.set_opacity(1)
        self.widget = self.tick = self.started = None

    def start(self, widget, mapped=True):
        self.cancel()
        if not mapped or not self.motion.allowed():
            widget.set_opacity(1)
            return
        self.widget = widget
        widget.set_opacity(.4)
        self.tick = widget.add_tick_callback(self.advance)

    def advance(self, widget, clock):
        now = clock.get_frame_time()
        if self.started is None:
            self.started = now
        progress = min(1, (now - self.started) / (self.duration * 1000))
        if not self.motion.allowed():
            progress = 1
        widget.set_opacity(.4 + .6 * (1 - (1 - progress) ** 3))
        if progress >= 1:
            self.widget = self.tick = self.started = None
            return False
        return True
