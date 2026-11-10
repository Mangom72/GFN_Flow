"""Short native GTK transitions, with no playback/hide delays or extra timers."""
from ui_theme import TOKENS
import weakref


class Motion:
    def __init__(self, Gtk, enabled=True):
        self.Gtk, self.enabled = Gtk, enabled
        self.stacks = weakref.WeakSet()
        self.fades = weakref.WeakSet()
        self.tab_titles = weakref.WeakSet()

    def set_enabled(self, enabled):
        self.enabled = enabled
        for stack in self.stacks:
            stack.set_transition_duration(TOKENS['motion_page_ms'] if enabled else 0)
        for title in self.tab_titles:
            title.set_transition_duration(TOKENS['motion_page_ms'] if enabled else 0)
            selected = title.get_reveal_child()
            if not enabled and (title.get_child_revealed() != selected
                                or title.get_child().get_child_visible() != selected):
                title.set_reveal_child(not selected)
                title.set_reveal_child(selected)
        if not enabled:
            for fade in self.fades:
                fade.cancel()

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

    def tab_title(self, label):
        Gtk = self.Gtk
        title = Gtk.Revealer()
        title.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        title.set_transition_duration(TOKENS['motion_page_ms'] if self.enabled else 0)
        title.add(label)
        title.show_all()
        self.tab_titles.add(title)
        return title

    def notebook(self, notebook, *, slide=False):
        fade = PageFade(self, TOKENS['motion_page_ms'])
        self.fades.add(fade)
        previous = notebook.get_current_page()

        def switch(book, page, index):
            nonlocal previous
            offset = 0
            if slide and previous >= 0 and index != previous:
                direction = 1 if index > previous else -1
                if book.get_direction() == self.Gtk.TextDirection.RTL:
                    direction = -direction
                offset = direction * TOKENS['motion_tab_px']
            previous = index
            fade.start(page, book.get_mapped(), offset)

        notebook.connect('switch-page', switch)
        notebook.connect('unmap', lambda *_: fade.cancel())
        notebook.connect('destroy', lambda *_: fade.cancel())
        return fade


class PageFade:
    def __init__(self, motion, duration):
        self.motion, self.duration = motion, duration
        self.widget = self.tick = self.started = None
        self.offset = 0
        self.draw_offset = 0
        self.draw_handler = None
        self.initial_opacity = .4

    def cancel(self):
        if self.widget is not None and self.tick is not None:
            self.widget.remove_tick_callback(self.tick)
        self.finish()

    def finish(self):
        if self.widget is not None:
            if self.draw_handler is not None:
                self.widget.disconnect(self.draw_handler)
                self.widget.queue_draw()
            self.widget.set_opacity(1)
        self.draw_handler = None
        self.widget = self.tick = self.started = None

    def start(self, widget, mapped=True, offset=0):
        self.cancel()
        if not mapped or not self.motion.allowed():
            widget.set_opacity(1)
            return
        self.widget = widget
        self.offset = self.draw_offset = offset
        self.initial_opacity = .65 if offset else .4
        if offset:
            self.draw_handler = widget.connect('draw', self.draw_page)
        widget.set_opacity(self.initial_opacity)
        self.tick = widget.add_tick_callback(self.advance)

    def draw_page(self, widget, context):
        # Translate paint only so tab width and child allocations stay fixed.
        context.rectangle(0, 0, widget.get_allocated_width(), widget.get_allocated_height())
        context.clip()
        context.translate(self.draw_offset, 0)
        self.motion.Gtk.Box.do_draw(widget, context)
        return True

    def advance(self, widget, clock):
        now = clock.get_frame_time()
        if self.started is None:
            self.started = now
        progress = min(1, (now - self.started) / (self.duration * 1000))
        if not self.motion.allowed():
            progress = 1
        if progress >= 1:
            self.finish()
            return False
        remainder = (1 - progress) ** 3
        widget.set_opacity(1 - (1 - self.initial_opacity) * remainder)
        if self.draw_handler is not None:
            self.draw_offset = self.offset * remainder
            widget.queue_draw()
        return True
