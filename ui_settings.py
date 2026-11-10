"""Settings stay inside the same popup and preserve the page beneath them."""
class SettingsPage:
    def __init__(self, picker):
        self.picker = picker
        self.updating = False
        Gtk = picker.Gtk
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.root.get_style_context().add_class('panel')
        header = Gtk.Box(spacing=8)
        header.pack_start(picker.button('Back', picker.leave_settings), False, False, 0)
        header.pack_start(picker.label('Settings', 'heading'), True, True, 0)
        header.pack_end(picker.button('Close', picker.close), False, False, 0)
        self.root.pack_start(header, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        self.scroll = scroll
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        if picker.width >= 640:
            content.set_halign(Gtk.Align.START)
            content.set_size_request(560, -1)
        scroll.add(content)
        self.root.pack_start(scroll, True, True, 0)
        content.pack_start(picker.label('Appearance', 'section'), False, False, 0)

        def row(title, control):
            box = Gtk.Box(spacing=12)
            name = picker.label(title)
            box.pack_start(name, True, True, 0)
            control.set_valign(Gtk.Align.CENTER)
            control.get_accessible().set_name(title)
            box.pack_end(control, False, False, 0)
            content.pack_start(box, False, False, 0)

        self.language = Gtk.ComboBoxText()
        for identity, title in [('en', 'English'), ('ko', '한국어')]:
            self.language.append(identity, title)
        self.language.set_active_id(picker.preferences.data['language'])
        row('Language', self.language)
        self.animations = Gtk.Switch()
        self.animations.set_active(picker.preferences.data['animations'])
        row('Page animations', self.animations)
        self.size = Gtk.ComboBoxText()
        for identity, title in [('compact', 'Compact'), ('standard', 'Standard'), ('large', 'Large')]:
            self.size.append(identity, title)
        self.size.set_active_id(picker.preferences.data['size'])
        row('Widget size', self.size)
        shortcuts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        shortcuts.get_style_context().add_class('context-bar')
        shortcuts.set_margin_top(12)
        shortcuts.pack_start(picker.label('Shortcuts', 'section'), False, False, 0)
        for key, title in [('F9', 'Open / stop macros'), ('F10', 'VPN on / off'), ('F12', 'Toggle hotspot')]:
            line = Gtk.Box(spacing=12)
            key_label = picker.label(key, 'shortcut-key')
            key_label.set_size_request(36, -1)
            line.pack_start(key_label, False, False, 0)
            line.pack_start(picker.label(title), True, True, 0)
            shortcuts.pack_start(line, False, False, 0)
        from ui_vpn import VpnPanel
        self.vpn = VpnPanel(picker)
        content.pack_start(self.vpn.root, False, False, 0)
        content.pack_start(shortcuts, False, False, 0)
        self.notice = picker.label('', 'input-error')
        self.notice.set_no_show_all(True)
        self.root.pack_end(self.notice, False, False, 0)
        saved = picker.help_button('Saved automatically', 'Saved automatically')
        saved.set_halign(Gtk.Align.END)
        self.root.pack_end(saved, False, False, 0)
        self.language.connect('changed', lambda control: self.change('language', control.get_active_id()))
        self.animations.connect('notify::active', lambda control, _: self.change('animations', control.get_active()))
        self.size.connect('changed', lambda control: self.change('size', control.get_active_id()))

    def change(self, key, value):
        if self.updating:
            return
        previous = self.picker.preferences.data[key]
        try:
            self.picker.preferences.set(key, value)
        except (OSError, ValueError):
            self.notice.set_text(self.picker.tr('Unable to save settings.'))
            self.notice.show()
            control = dict(language=self.language, size=self.size, animations=self.animations)[key]
            self.updating = True
            try:
                if key == 'animations':
                    control.set_active(previous)
                else:
                    control.set_active_id(previous)
            finally:
                self.updating = False
            return
        self.notice.hide()
        self.picker.apply_preferences()

