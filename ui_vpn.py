"""Compact server test controls inside the existing Settings page."""
from pathlib import Path
import sys
import threading
import time
import webbrowser
from detection import login
from windows_vpn_profiles import COUNTRIES, FILE_LIMIT, LEGACY, PROVIDERS


class VpnPanel:
    def __init__(self, picker):
        self.picker = picker
        Gtk = picker.Gtk
        self.windows = sys.platform == 'win32'
        self.catalog = {'active': LEGACY, 'profiles': [LEGACY], 'benchmark_id': None} if picker.preview else None
        self.profile_pending = False
        self.profile_error = ''
        self.chooser = None
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.root.get_style_context().add_class('context-bar')
        self.root.set_margin_top(12)
        header = Gtk.Box(spacing=4)
        header.pack_start(picker.label('VPN' if self.windows else 'Japan VPN', 'section'), True, True, 0)
        self.root.pack_start(header, False, False, 0)
        if self.windows:
            self.build_profiles()
            header = Gtk.Box(spacing=4)
            header.pack_start(picker.label('NordVPN Japan server test'), True, True, 0)
            self.root.pack_start(header, False, False, 0)
        self.test_header = header
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

    def build_profiles(self):
        picker, Gtk = self.picker, self.picker.Gtk
        from ui_vpn_country import CountryPicker
        self.provider = Gtk.ComboBoxText()
        self.country = CountryPicker(picker, self.profile_changed)
        for code, (name, _) in PROVIDERS.items():
            self.provider.append(code, name)
        self.provider.set_active_id(LEGACY[0])
        for label, combo in (('VPN provider', self.provider), ('VPN country', self.country.button)):
            title = picker.label(label)
            title.set_mnemonic_widget(combo)
            self.root.pack_start(title, False, False, 0)
            self.root.pack_start(combo, False, False, 0)
        picker.localizer.bind(self.provider, 'accessible-name', 'VPN provider')
        self.provider.connect('changed', self.profile_changed)
        actions = Gtk.Box(spacing=8)
        self.import_button = picker.button('Import .conf', self.choose_file)
        self.use_button = picker.button('Use profile', lambda: self.request_profile(b'u', self.selection()))
        actions.pack_start(self.import_button, False, False, 0)
        actions.pack_start(self.use_button, False, False, 0)
        self.root.pack_start(actions, False, False, 0)
        self.profile_status = picker.label('', 'muted', localize=False)
        self.root.pack_start(self.profile_status, False, False, 0)
        self.guide_url = ''
        self.guide = picker.button('Get a WireGuard file', lambda: webbrowser.open(self.guide_url))
        self.guide.get_style_context().add_class('flat')
        self.guide.set_halign(Gtk.Align.START)
        self.guide.set_no_show_all(True)
        self.root.pack_start(self.guide, False, False, 0)

    def selection(self):
        return {'provider': self.provider.get_active_id(), 'country': self.country.get_active_id()}

    def profile_changed(self, *_):
        self.profile_error = ''
        self.refresh_profile_controls()

    def load_profiles(self):
        if self.windows and not self.picker.preview and not self.profile_pending:
            self.request_profile(b'p')

    def request_profile(self, op, payload=None):
        picker = self.picker
        if self.profile_pending or picker.preview:
            return
        self.profile_pending = True
        self.profile_error = ''
        self.refresh_profile_controls()

        def worker():
            from tool_status import vpn_lock
            from windows_vpn_broker import profiles
            try:
                if op == b'p':
                    event = profiles()
                else:
                    with vpn_lock():
                        event = profiles(op, payload)
            except (OSError, ValueError) as exc:
                error = str(exc) if op != b'p' and isinstance(exc, ValueError) else 'VPN profile service unavailable.'
                picker.GLib.idle_add(self.profile_done, op, None, error)
            else:
                picker.GLib.idle_add(self.profile_done, op, event, '')

        threading.Thread(target=worker, name='macro-vpn-profiles', daemon=True).start()

    def profile_done(self, op, event, error):
        self.profile_pending = False
        first = self.catalog is None
        self.profile_error = error
        if event:
            if first:
                self.provider.set_active_id(event['active'][0])
                self.country.set_active_id(event['active'][1])
            self.catalog = event
            if op != b'p' and not self.picker.vpn_testing:
                self.picker.vpn_test_state = {}
        elif op == b'p':
            self.catalog = None
        self.refresh()
        self.picker.refresh_tool_status()
        return False

    def choose_file(self):
        Gtk, picker = self.picker.Gtk, self.picker
        dialog = Gtk.FileChooserDialog(title=picker.tr('Import .conf'), transient_for=picker.window,
                                       modal=True, action=Gtk.FileChooserAction.OPEN)
        dialog.add_button(picker.tr('Cancel'), Gtk.ResponseType.CANCEL)
        dialog.add_button(picker.tr('Open file'), Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        file_filter = Gtk.FileFilter()
        file_filter.set_name('WireGuard (*.conf)')
        file_filter.add_pattern('*.[cC][oO][nN][fF]')
        dialog.add_filter(file_filter)
        self.chooser = dialog
        path = None
        try:
            if dialog.run() == Gtk.ResponseType.ACCEPT:
                path = dialog.get_filename()
        finally:
            dialog.destroy()
            self.chooser = None
        if not path:
            return
        try:
            path = Path(path)
            if path.suffix.lower() != '.conf' or not path.is_file():
                raise OSError
            with path.open('rb') as stream:
                content = stream.read(FILE_LIMIT + 1)
            if len(content) > FILE_LIMIT:
                self.profile_error = 'The WireGuard file is too large.'
                self.refresh_profile_controls()
                return
            config = content.decode('utf-8-sig')
        except (OSError, UnicodeError):
            self.profile_error = 'Could not read the WireGuard file.'
            self.refresh_profile_controls()
            return
        self.request_profile(b'i', {**self.selection(), 'config': config})

    def refresh_profile_controls(self):
        if not self.windows:
            return
        picker = self.picker
        selected = (self.provider.get_active_id(), self.country.get_active_id())
        active = tuple(self.catalog['active']) if self.catalog else ()
        profiles = {tuple(value) for value in self.catalog['profiles']} if self.catalog else set()
        connected = picker.tool_status['vpn'] is True
        available = bool(self.catalog and not self.profile_pending and not picker.vpn_testing)
        self.provider.set_sensitive(not self.profile_pending)
        self.country.button.set_sensitive(not self.profile_pending)
        self.country.set_saved({country for provider, country in profiles if provider == selected[0]})
        self.import_button.set_sensitive(available and not (connected and selected == active))
        self.use_button.set_sensitive(available and not connected and selected in profiles and selected != active)
        self.guide_url = PROVIDERS.get(selected[0], ('', ''))[1]
        self.guide.set_visible(bool(self.guide_url))
        lines = []
        if active in profiles:
            lines.append(picker.tr('F10 profile: {provider} · {country}').format(
                provider=picker.tr(PROVIDERS[active[0]][0]), country=picker.tr(COUNTRIES[active[1]])))
        if self.profile_error:
            lines.append(picker.tr(self.profile_error))
        elif self.profile_pending or not self.catalog:
            lines.append(picker.tr('Checking VPN profiles…'))
        elif selected not in profiles:
            lines.append(picker.tr('Import the .conf for this country.'))
        elif selected != active:
            lines.append(picker.tr('Disconnect with F10 to switch profiles.' if connected else 'Profile saved. Select Use profile for F10.'))
        self.profile_status.set_text('\n'.join(lines))
        style = self.profile_status.get_style_context()
        (style.add_class if self.profile_error else style.remove_class)('error-status')

    def refresh(self):
        picker, Gtk = self.picker, self.picker.Gtk
        state = picker.vpn_test_state
        running = picker.vpn_testing
        self.refresh_profile_controls()
        benchmark = (not self.windows or bool(self.catalog and tuple(self.catalog['active']) == LEGACY
                     and LEGACY in map(tuple, self.catalog['profiles'])))
        if self.windows:
            self.test_header.set_no_show_all(not (benchmark or running))
            self.status.set_no_show_all(not (benchmark or running))
            self.test_header.set_visible(benchmark or running)
            self.status.set_visible(benchmark or running)
        self.warning.set_visible(running or benchmark and (bool(picker.game) or bool(picker.sessions.games)))
        title = 'Cancel server test' if running else 'Find best JP server'
        self.action.set_image(Gtk.Image.new_from_icon_name(
            'media-playback-stop-symbolic' if running else 'system-search-symbolic', Gtk.IconSize.BUTTON))
        self.action.set_tooltip_text(picker.tr(title))
        self.action.get_accessible().set_name(picker.tr(title))
        self.action.set_sensitive(running or not picker.busy)
        report = picker.vpn_report if benchmark and (not self.windows or
            picker.vpn_report.get('profile_revision') == self.catalog['benchmark_id']) else {}
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
