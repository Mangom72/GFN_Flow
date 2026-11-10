"""Compact server test controls inside the existing Settings page."""
import time
from detection import login


class VpnPanel:
    def __init__(self, picker):
        self.picker = picker
        Gtk = picker.Gtk
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.root.get_style_context().add_class('context-bar')
        self.root.set_margin_top(12)
        header = Gtk.Box(spacing=4)
        header.pack_start(picker.label('Japan VPN', 'section'), True, True, 0)
        header.pack_end(picker.help_button('VPN server test information',
            'Tests the GFN connection route through Tokyo and Osaka VPN servers. '
            'Measures TCP latency, variation and failed connections; this is not a streaming bandwidth or packet-loss test. '
            'The game stream keeps its current route. The best server is saved for your next VPN connection. '
            'Includes a direct connection test. If direct is fastest, VPN disconnects 10 seconds after game detection. '
            'Automatic disconnect requires a result from the same network within 24 hours. '
            'Server testing may briefly increase latency or interrupt the game.'), False, False, 0)
        self.action = picker.button('Find best JP server', lambda: (
            picker.cancel_vpn_search() if picker.vpn_testing else picker.start_vpn_search()))
        header.pack_end(self.action, False, False, 0)
        self.root.pack_start(header, False, False, 0)
        self.warning = Gtk.Box(spacing=6)
        self.warning.pack_start(Gtk.Image.new_from_icon_name(
            'dialog-warning-symbolic', Gtk.IconSize.MENU), False, False, 0)
        self.warning.pack_start(picker.label('May interrupt gameplay', 'muted'), True, True, 0)
        picker.localizer.bind(self.warning, 'tooltip-text',
            'Server testing may briefly increase latency or interrupt the game.')
        self.warning.show_all()
        self.warning.set_no_show_all(True)
        self.root.pack_start(self.warning, False, False, 0)
        self.status = picker.label('', 'muted', localize=False)
        self.status.set_line_wrap(True)
        self.root.pack_start(self.status, False, False, 0)
        self.previous = picker.label('', 'muted', localize=False)
        self.previous.set_no_show_all(True)
        self.root.pack_start(self.previous, False, False, 0)
        self.progress = Gtk.ProgressBar()
        self.progress.set_no_show_all(True)
        self.root.pack_start(self.progress, False, False, 0)
        self.results = Gtk.Grid(column_spacing=12, row_spacing=5)
        self.root.pack_start(self.results, False, False, 0)
        picker.localizer.bind(self.status, 'render', lambda *_: self.refresh())
        self.refresh()

    def refresh(self):
        picker, Gtk = self.picker, self.picker.Gtk
        state = picker.vpn_test_state
        running = picker.vpn_testing
        self.warning.set_visible(running or bool(picker.game) or bool(picker.sessions.games))
        title = 'Cancel server test' if running else 'Find best JP server'
        self.action.set_image(Gtk.Image.new_from_icon_name(
            'media-playback-stop-symbolic' if running else 'system-search-symbolic', Gtk.IconSize.BUTTON))
        self.action.set_tooltip_text(picker.tr(title))
        self.action.get_accessible().set_name(picker.tr(title))
        self.action.set_sensitive(running or not picker.busy)
        report = picker.vpn_report
        if state.get('error'):
            text = picker.tr(state['error'])
        elif running:
            if state.get('stage') == 'test':
                text = picker.tr('Testing {server} · {index}/{total}').format(
                    server=picker.tr(state['server']), index=state['index'], total=state['total'])
            else:
                text = picker.tr('Finding Japanese servers…')
        elif report.get('best'):
            tested = time.strftime('%m-%d %H:%M', time.localtime(report['tested_at']))
            text = picker.tr('Next connection: {server}\nTested {time}').format(
                server=report['best']['hostname'], time=tested)
            if login.direct_connection_is_fastest(report):
                text += '\n' + picker.tr('Without VPN is fastest · 10s auto-off after game starts')
        else:
            text = picker.tr('No server test yet.')
        self.status.set_text(text)
        previous = bool(state.get('error') and report.get('results') and report.get('tested_at'))
        self.previous.set_visible(previous)
        if previous:
            tested = time.strftime('%m-%d %H:%M', time.localtime(report['tested_at']))
            self.previous.set_text(picker.tr('Previous results · {time}').format(time=tested))
        style = self.status.get_style_context()
        (style.add_class if state.get('error') else style.remove_class)('error-status')
        self.progress.set_visible(running)
        total = state.get('total', 0)
        self.progress.set_fraction((state.get('index', 0) - 1) / total if total else 0)
        for child in self.results.get_children():
            child.destroy()
        rows = state.get('rows', []) if running else report.get('results', [])
        if rows:
            for column, title in enumerate(('Server', 'Latency', 'Variation', 'Failed')):
                self.results.attach(picker.label(title, 'muted'), column, 0, 1, 1)
            for index, row in enumerate(rows, 1):
                direct = row.get('kind') == 'direct'
                name = picker.tr('Without VPN') if direct else (
                    row.get('address') or row['hostname'].removesuffix('.nordvpn.com'))
                if not direct and row.get('city'):
                    name += ' · ' + picker.tr(row['city'])
                values = (name,
                          f"{row['latency']:g} ms" if row['latency'] is not None else '—',
                          f"{row['variation']:g} ms" if row['variation'] is not None else '—',
                          f"{row['failures']}/{row['samples']}")
                for column, value in enumerate(values):
                    label = picker.label(value, 'muted', localize=False)
                    label.set_line_wrap(False)
                    label.set_ellipsize(3)
                    detail = picker.tr('Direct connection') if direct else row['hostname'] + '\n' + name
                    if row.get('error'):
                        detail += '\n' + picker.tr(row['error'])
                    label.set_tooltip_text(detail)
                    if column == 0:
                        label.set_hexpand(True)
                    self.results.attach(label, column, index, 1, 1)
        self.results.show_all()
