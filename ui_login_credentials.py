"""Register credentials for an existing login macro."""
from detection import login


class LoginCredentialsPage:
    def __init__(self, manager):
        self.manager = manager
        self.picker = picker = manager.picker
        self.library = manager.library
        Gtk = manager.Gtk
        self.page = manager.page('Login Credentials')

        self.target = Gtk.ComboBoxText()
        self.target._user_options = True
        for cell in self.target.get_cells():
            cell.set_property('ellipsize', 3)
            cell.set_property('max-width-chars', 32)
        self.field('Login macro', self.target)
        self.account = Gtk.Entry(input_purpose=Gtk.InputPurpose.EMAIL, width_chars=12)
        self.field('Login ID / email', self.account)
        self.password = Gtk.Entry(input_purpose=Gtk.InputPurpose.PASSWORD, visibility=False, width_chars=12)
        self.password.set_placeholder_text('Leave blank to keep saved password')
        self.field('Password', self.password)
        self.empty = picker.label('Create a login macro in the Library first.', 'muted')
        self.empty.set_no_show_all(True)
        self.page.pack_start(self.empty, False, False, 0)
        self.button = picker.button('Save credentials', lambda: manager.safe(self.save), primary=True)
        self.button.set_halign(Gtk.Align.END)
        self.page.pack_start(self.button, False, False, 0)

        self.selection_changed = self.target.connect('changed', self.load)
        self.password.connect('activate', lambda *_: self.button.clicked() if self.button.get_sensitive() else None)
        manager.tabs.connect('switch-page', self.switch_page)
        self.page.connect('unmap', lambda *_: self.password.set_text(''))

    def field(self, title, widget):
        label = self.picker.label(title, 'field-label')
        label.set_mnemonic_widget(widget)
        self.page.pack_start(label, False, False, 0)
        widget.get_accessible().set_name(title)
        self.page.pack_start(widget, False, False, 0)

    def switch_page(self, _, page, _index):
        self.password.set_text('')
        if page != self.page:
            self.account.set_text('')
            return
        selected = self.target.get_active_id()
        with self.target.handler_block(self.selection_changed):
            self.target.remove_all()
            for macro in self.library.macros:
                if macro['kind'] == 'login':
                    game, name = macro['game'], macro['name']
                    name_folded, game_folded = name.casefold(), game.casefold()
                    includes_game = name_folded == game_folded or name_folded.startswith(game_folded + ' ')
                    self.target.append(macro['id'], name if includes_game else f'{game} · {name}')
            if not selected or not self.target.set_active_id(selected):
                self.target.set_active(0)
        self.load()

    def load(self, *_):
        self.manager.say('')
        self.password.set_text('')
        macro = self.library.get(self.target.get_active_id())
        for widget in (self.account, self.password, self.button):
            widget.set_sensitive(macro is not None and not self.picker.busy)
        self.empty.set_visible(macro is None)
        account = ''
        if macro:
            try:
                account = self.library.resolved_profile(macro, self.picker.legacy_profiles).user
            except (login.LoginError, KeyError, ValueError):
                pass
        self.account.set_text(account)

    def save(self):
        if self.picker.busy:
            return
        self.library.save_login_credentials(self.target.get_active_id(), self.picker.legacy_profiles,
                                            self.account.get_text().strip(), self.password.get_text())
        self.password.set_text('')
        self.manager.say('Login credentials saved.')
