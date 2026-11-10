"""Wheel gestures scroll pages, never change input values."""


def protect_inputs(root, Gtk):
    from gi.repository import Gdk
    if isinstance(root, (Gtk.Entry, Gtk.ComboBox, Gtk.TextView, Gtk.Range)) and not getattr(root, '_wheel_protected', False):
        root._wheel_protected = True
        root.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        root.connect('scroll-event', lambda widget, event: scroll_page(widget, event, Gtk))
    if isinstance(root, Gtk.Container):
        for child in root.get_children():
            protect_inputs(child, Gtk)


def scroll_page(widget, event, Gtk):
    from gi.repository import Gdk
    page = widget.get_parent()
    while page and (not isinstance(page, Gtk.ScrolledWindow) or getattr(page, '_input_scroll', False)):
        page = page.get_parent()
    if page:
        delta = 0
        if event.direction == Gdk.ScrollDirection.UP:
            delta = -1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            delta = 1
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            valid, _, delta = event.get_scroll_deltas()
            if not valid:
                delta = 0
        adjustment = page.get_vadjustment()
        step = max(24, min(80, adjustment.get_page_size() ** (2 / 3)))
        adjustment.set_value(max(adjustment.get_lower(), min(adjustment.get_upper() - adjustment.get_page_size(),
                                                            adjustment.get_value() + delta * step)))
    return True

