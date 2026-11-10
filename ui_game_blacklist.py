"""Manage the saved game catalog and detection blacklist."""


class GameBlacklistPage:
    def __init__(self, manager):
        self.manager = manager
        self.picker = picker = manager.picker
        self.library = manager.library
        self.Gtk = Gtk = manager.Gtk
        self.page = manager.page('Blacklist')
        self.detected = self.game_list('Detected games', 'No detected games.')
        self.delete = picker.button('Delete detected game', lambda: manager.safe(self.block))
        self.delete.set_tooltip_text('Remove the game from detection and add it to the blacklist.')
        self.delete.get_style_context().add_class('danger')
        self.delete.set_halign(Gtk.Align.END)
        self.page.pack_start(self.delete, False, False, 0)

        self.blocked = self.game_list('Blacklist', 'The blacklist is empty.')
        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        self.restore = picker.button('Unblock and restore', lambda: manager.safe(lambda: self.unblock(restore=True)),
                                     primary=True)
        self.forget = picker.button('Remove only', lambda: manager.safe(lambda: self.unblock(restore=False)))
        self.forget.set_tooltip_text('Remove from the blacklist without restoring. Future detection is allowed.')
        buttons.pack_start(self.restore, False, False, 0)
        buttons.pack_start(self.forget, False, False, 0)
        self.page.pack_start(buttons, False, False, 0)

        for rows in (self.detected, self.blocked):
            rows.connect('row-selected', self.update_buttons)
        manager.tabs.connect('switch-page', self.switch_page)
        self.update_buttons()

    def game_list(self, title, empty):
        Gtk = self.Gtk
        self.page.pack_start(self.picker.label(title, 'section'), False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(56)
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, activate_on_single_click=False)
        rows.get_style_context().add_class('macro-list')
        rows.get_accessible().set_name(title)
        placeholder = self.picker.label(empty, 'macro-empty')
        placeholder.show()
        rows.set_placeholder(placeholder)
        scroll.add(rows)
        self.page.pack_start(scroll, True, True, 0)
        return rows

    def switch_page(self, _, page, _index):
        if page == self.page:
            self.manager.say('')
            self.refresh()

    def refresh(self):
        for rows, games in ((self.detected, self.library.games), (self.blocked, self.library.blacklist)):
            selected = rows.get_selected_row()
            selected_name = selected.game_name if selected else None
            for child in rows.get_children():
                child.destroy()
            names = {game['name'].casefold(): game['name'] for game in games}
            for _, name in sorted(names.items()):
                row = self.Gtk.ListBoxRow()
                row.game_name = name
                label = self.picker.label(name, 'macro-row', localize=False)
                label.set_line_wrap(False)
                label.set_ellipsize(3)
                label.set_tooltip_text(name)
                row.add(label)
                rows.add(row)
                row.show_all()
                if name == selected_name:
                    rows.select_row(row)
        self.update_buttons()

    def update_buttons(self, *_):
        idle = not self.picker.busy
        self.delete.set_sensitive(idle and self.detected.get_selected_row() is not None)
        for button in (self.restore, self.forget):
            button.set_sensitive(idle and self.blocked.get_selected_row() is not None)

    def block(self):
        row = self.detected.get_selected_row()
        if row and not self.picker.busy:
            self.library.move_game(row.game_name, from_blacklist=False)
            self.picker.scan()
            self.refresh()
            self.manager.say('Game added to the blacklist.')

    def unblock(self, *, restore):
        row = self.blocked.get_selected_row()
        if row and not self.picker.busy:
            self.library.move_game(row.game_name, from_blacklist=True, restore=restore)
            self.refresh()
            self.manager.say('Game restored.' if restore else 'Game removed from the blacklist.')
