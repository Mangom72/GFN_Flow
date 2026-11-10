"""Register credentials for an existing login macro."""
from detection import login
from macro_library import MAX_EXTRA_ACCOUNTS, PRIMARY_ACCOUNT


class LoginCredentialsPage:
    def __init__(self, manager):
        self.manager = manager
        self.picker = picker = manager.picker
        self.library = manager.library
        Gtk = manager.Gtk
        self.page = manager.page('Login Credentials')
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.add(self.form)
        self.page.pack_start(scroll, True, True, 0)

        self.target = Gtk.ComboBoxText()
        self.target._user_options = True
        for cell in self.target.get_cells():
            cell.set_property('ellipsize', 3)
            cell.set_property('max-width-chars', 32)
        self.field('Login macro', self.target)
        self.adding = False
        self.confirm_remove = False
        self.profile = Gtk.ComboBoxText()
        self.profile._user_options = True
        self.profile._fixed_options = {PRIMARY_ACCOUNT}
        self.profile.get_cells()[0].set_property('ellipsize', 3)
        self.profile.get_cells()[0].set_property('max-width-chars', 28)
        self.profile_field = self.field('Login profile', self.profile, conditional=True)
        self.profile_handler = self.profile.connect('changed', self.change_profile)
        self.alias = Gtk.Entry(width_chars=12, max_length=40)
        self.alias_field = self.field('Profile name', self.alias, conditional=True)
        self.account = Gtk.Entry(input_purpose=Gtk.InputPurpose.EMAIL, width_chars=12)
        self.field('Login ID / email', self.account)
        self.password = Gtk.Entry(input_purpose=Gtk.InputPurpose.PASSWORD, visibility=False, width_chars=12)
        self.password.set_placeholder_text('Leave blank to keep saved password')
        self.field('Password', self.password)
        self.empty = picker.label('Create a login macro in the Library first.', 'muted')
        self.empty.set_no_show_all(True)
        self.form.pack_start(self.empty, False, False, 0)
        self.button = picker.button('Save credentials', lambda: manager.safe(self.save), primary=True)
        actions = Gtk.Box(spacing=8)
        self.add = picker.button('Add profile', self.start_add)
        self.remove = picker.button('Remove profile', lambda: manager.safe(self.remove_profile))
        self.cancel = picker.button('Cancel', self.change_profile)
        self.add.set_no_show_all(True)
        self.remove.set_no_show_all(True)
        self.cancel.set_no_show_all(True)
        actions.pack_start(self.add, False, False, 0)
        self.form.pack_start(self.remove, False, False, 0)
        self.remove.set_halign(Gtk.Align.START)
        actions.pack_start(self.cancel, False, False, 0)
        actions.pack_end(self.button, False, False, 0)
        self.page.pack_start(actions, False, False, 0)

        self.selection_changed = self.target.connect('changed', lambda *_: self.reload_profiles())
        self.password.connect('activate', lambda *_: self.button.clicked() if self.button.get_sensitive() else None)
        manager.tabs.connect('switch-page', self.switch_page)
        self.page.connect('unmap', lambda *_: self.password.set_text(''))

    def field(self, title, widget, conditional=False):
        Gtk = self.manager.Gtk
        label = self.picker.label(title, 'field-label')
        label.set_mnemonic_widget(widget)
        self.picker.localizer.bind(widget, 'accessible-name', title)
        if not conditional:
            self.form.pack_start(label, False, False, 0)
            self.form.pack_start(widget, False, False, 0)
            return None
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        group.pack_start(label, False, False, 0)
        group.pack_start(widget, False, False, 0)
        group.show_all()
        group.set_no_show_all(True)
        group.hide()
        self.form.pack_start(group, False, False, 0)
        return group

    def switch_page(self, _, page, _index):
        self.password.set_text('')
        if page != self.page:
            self.account.set_text('')
            self.alias.set_text('')
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
        self.reload_profiles(self.profile.get_active_id())

    def reload_profiles(self, preferred=PRIMARY_ACCOUNT):
        macro = self.library.get(self.target.get_active_id())
        with self.profile.handler_block(self.profile_handler):
            self.profile.remove_all()
            choices = self.library.login_choices(macro) if macro else []
            for identity, title in choices:
                self.profile.append(identity, title)
            if choices and (not preferred or not self.profile.set_active_id(preferred)):
                self.profile.set_active_id(PRIMARY_ACCOUNT)
        self.profile_field.set_visible(bool(choices))
        self.change_profile()

    def change_profile(self, *_):
        self.adding = False
        self.load()

    def start_add(self):
        self.adding = True
        self.load()
        self.alias.grab_focus()

    def load(self):
        self.manager.say('')
        self.confirm_remove = False
        self.password.set_text('')
        macro = self.library.get(self.target.get_active_id())
        identity = self.profile.get_active_id()
        entry = next((entry for entry in macro.get('login_accounts', []) if entry['id'] == identity), None) if macro else None
        enabled = macro is not None and not self.picker.busy
        for widget in (self.account, self.password, self.button, self.profile):
            widget.set_sensitive(enabled)
        self.empty.set_visible(macro is None)
        self.alias_field.set_visible(self.adding or entry is not None)
        self.alias.set_text(entry['label'] if entry and not self.adding else '')
        self.cancel.set_visible(self.adding)
        self.remove.set_visible(entry is not None and not self.adding)
        self.remove.set_sensitive(enabled)
        self.add.set_visible(not self.adding)
        self.add.set_sensitive(enabled and self.library.primary_registered(macro, self.picker.legacy_profiles)
                               and len(macro.get('login_accounts', [])) < MAX_EXTRA_ACCOUNTS)
        account = ''
        if macro and not self.adding:
            try:
                account = self.library.resolved_profile(macro, self.picker.legacy_profiles, account_id=identity).user
            except (login.LoginError, KeyError, ValueError):
                pass
        self.account.set_text(account)

    def save(self):
        if self.picker.busy:
            return
        identity = self.profile.get_active_id()
        macro = self.target.get_active_id()
        account, password = self.account.get_text().strip(), self.password.get_text()
        if self.adding or identity not in (None, PRIMARY_ACCOUNT):
            identity = self.library.save_extra_account(macro, None if self.adding else identity,
                                                       self.alias.get_text().strip(), account, password)
        else:
            self.library.save_login_credentials(macro, self.picker.legacy_profiles, account, password)
        self.reload_profiles(identity)
        self.password.set_text('')
        self.manager.say('Login credentials saved.')

    def remove_profile(self):
        if self.picker.busy or self.adding:
            return
        identity = self.profile.get_active_id()
        if identity in (None, PRIMARY_ACCOUNT):
            return
        if not self.confirm_remove:
            self.confirm_remove = True
            self.manager.say('Select Remove profile again to confirm.')
            return
        self.library.remove_extra_account(self.target.get_active_id(), identity)
        self.reload_profiles()
        self.manager.say('Login profile removed.')
