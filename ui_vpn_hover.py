"""One fresh, asynchronous latency measurement per VPN-button hover."""
import threading


class VpnHover:
    def __init__(self, picker, button):
        from gi.repository import Gdk
        self.picker, self.button = picker, button
        self.active = self.destroyed = False
        self.cancel = threading.Event()
        self.generation = 0
        self.checking = False
        self.latency = None
        button.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        button.connect('enter-notify-event', self.enter)
        button.connect('leave-notify-event', self.leave)
        button.connect('focus-out-event', lambda *_: self.stop())
        button.connect('unmap', lambda *_: self.stop())
        button.connect('clicked', lambda *_: self.stop())
        button.connect('destroy', self.destroy)
        button.connect('query-tooltip', self.tooltip)

    def enter(self, _, event):
        from gi.repository import Gdk
        if event.detail != Gdk.NotifyType.INFERIOR:
            self.start()
        return False

    def leave(self, _, event):
        from gi.repository import Gdk
        if event.detail != Gdk.NotifyType.INFERIOR:
            self.stop()
        return False

    def start(self):
        if self.active or self.destroyed:
            return
        self.active = self.checking = True
        self.latency = None
        self.cancel = threading.Event()
        self.generation += 1
        generation, cancel = self.generation, self.cancel
        if self.picker.preview:
            return  # Offscreen previews never initiate network activity.
        def worker():
            from vpn_servers import current_latency
            try:
                latency = current_latency(cancel)
            except Exception:
                latency = None
            self.picker.GLib.idle_add(self.finished, generation, latency)
        threading.Thread(target=worker, name='macro-vpn-latency', daemon=True).start()

    def stop(self):
        self.active = self.checking = False
        self.latency = None
        self.cancel.set()
        self.generation += 1
        return False

    def destroy(self, *_):
        self.destroyed = True
        self.stop()

    def finished(self, generation, latency):
        if self.destroyed or not self.active or generation != self.generation:
            return False
        self.checking = False
        self.latency = latency
        if not self.picker.preview:
            self.picker.Gtk.Tooltip.trigger_tooltip_query(self.button.get_display())
        return False

    def text(self):
        picker = self.picker
        detail = picker.tr('Checking GFN latency…') if self.checking else (
            picker.tr('GFN latency: {latency} ms').format(latency=f'{self.latency:g}')
            if self.latency is not None else picker.tr('GFN latency unavailable'))
        return (self.button.get_tooltip_text() or '') + '\n' + detail

    def tooltip(self, _, x, y, keyboard, tooltip):
        self.start()  # Also supports keyboard-triggered tooltips.
        tooltip.set_text(self.text())
        return True
