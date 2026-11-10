"""A searchable country menu contained in the settings popup."""
from windows_vpn_profiles import COUNTRIES, LEGACY


class CountryPicker:
    def __init__(self, picker, changed):
        self.picker, self.changed = picker, changed
        Gtk = picker.Gtk
        self.active = LEGACY[1]
        self.saved = set()
        self.button = Gtk.MenuButton()
        content = Gtk.Box(spacing=8)
        self.caption = picker.label('', localize=False)
        self.caption.set_line_wrap(False)
        self.caption.set_ellipsize(3)
        content.pack_start(self.caption, True, True, 0)
        content.pack_end(Gtk.Image.new_from_icon_name('pan-down-symbolic', Gtk.IconSize.MENU), False, False, 0)
        self.button.add(content)
        self.menu = Gtk.Popover.new(self.button)
        self.button.set_popover(self.menu)
        self.button.connect('destroy', lambda *_: self.menu.destroy())
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=8)
        panel.set_size_request(216, -1)
        self.search = Gtk.SearchEntry(placeholder_text='Search countries', width_chars=16)
        picker.localizer.bind(self.search, 'accessible-name', 'Search countries')
        picker.localizer.bind(self.search, 'placeholder-text', 'Search countries')
        panel.pack_start(self.search, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(156)
        self.rows = Gtk.ListBox()
        self.rows.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.rows.set_activate_on_single_click(True)
        scroll.add(self.rows)
        panel.pack_start(scroll, True, True, 0)
        self.menu.add(panel)
        panel.show_all()
        self.menu.connect('show', self.open)
        self.rows.connect('row-activated', lambda _, row: self.choose(row))
        self.search.connect('changed', lambda *_: self.filter())
        self.search.connect('activate', lambda *_: self.choose(self.rows.get_selected_row()))
        self.search.connect('stop-search', lambda *_: self.close())
        self.search.connect('key-press-event', self.search_key)
        picker.localizer.bind(self.caption, 'render', lambda *_: self.translate())

    def translate(self):
        name = self.picker.tr(COUNTRIES[self.active])
        self.caption.set_text(name)
        self.button.get_accessible().set_name(self.picker.tr('VPN country') + ': ' + name)
        if self.menu.get_visible():
            self.filter()

    def get_active_id(self):
        return self.active

    def set_active_id(self, code):
        if code not in COUNTRIES or code == self.active:
            return
        self.active = code
        self.translate()
        self.changed()

    def set_saved(self, codes):
        if self.saved != codes:
            self.saved = codes
            if self.menu.get_visible():
                self.filter()

    def open(self, *_):
        self.search.set_text('')
        self.filter()
        self.search.grab_focus()

    def filter(self):
        picker = self.picker
        search = self.search.get_text().strip().casefold()
        for row in self.rows.get_children():
            row.destroy()
        matches = [(code, picker.tr(name)) for code, name in COUNTRIES.items()
                   if any(search in value.casefold() for value in (code, name, picker.tr(name)))]
        matches.sort(key=lambda item: (item[0].casefold() != search, item[0] not in self.saved, item[1].casefold()))
        for code, name in matches:
            row = picker.Gtk.ListBoxRow()
            row.code = code
            label = picker.label(f'{name} · {code}', localize=False)
            label.set_margin_start(8)
            label.set_margin_end(8)
            label.set_margin_top(6)
            label.set_margin_bottom(6)
            row.add(label)
            self.rows.add(row)
        if not matches:
            self.rows.add(picker.label('No countries match.', 'muted'))
            self.rows.get_row_at_index(0).set_selectable(False)
        self.rows.show_all()
        selected = next((row for row in self.rows.get_children() if getattr(row, 'code', None) == self.active), None)
        self.rows.select_row((selected if not search else None) or (self.rows.get_row_at_index(0) if matches else None))

    def search_key(self, _, event):
        from gi.repository import Gdk
        if event.keyval == Gdk.KEY_Down and (row := self.rows.get_selected_row()):
            row.grab_focus()
            return True
        return False

    def choose(self, row):
        if row is not None and hasattr(row, 'code'):
            self.set_active_id(row.code)
            self.close()

    def close(self):
        self.menu.popdown()
        self.button.grab_focus()
