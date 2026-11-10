"""Choose an account for a login macro without duplicating its actions."""
from detection import session_key
from macro_library import PRIMARY_ACCOUNT


class LoginAccountChoice:
    def __init__(self, picker, macro, full, choices):
        self.picker, self.macro_id, self.full = picker, macro['id'], full
        self.game = session_key(*picker.game)
        self.previous = picker.stack.get_visible_child_name() or 'picker'
        self.focus = picker.window.get_focus()
        Gtk = picker.Gtk
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.root.get_style_context().add_class('panel')
        self.root.pack_start(picker.label('Choose a login profile', 'heading'), False, False, 0)
        title = picker.label(macro['game'], 'muted', localize=False)
        self.root.pack_start(title, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.root.pack_start(scroll, True, True, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.add(box)
        self.buttons = {}
        for account_id, name in choices:
            button = Gtk.Button()
            button.get_style_context().add_class('secondary')
            label = picker.label(name, localize=account_id == PRIMARY_ACCOUNT)
            label.set_line_wrap(False)
            label.set_ellipsize(3)
            button.add(label)
            button.get_accessible().set_name(name)
            if account_id == PRIMARY_ACCOUNT:
                picker.localizer.bind(button, 'accessible-name', name)
            else:
                button._user_content = True
                button.set_tooltip_text(name)
            button.connect('clicked', lambda _, account_id=account_id: self.select(account_id))
            box.pack_start(button, False, False, 0)
            self.buttons[account_id] = button
        self.cancel = picker.button('Cancel', lambda: picker.dismiss_login_choice(restore=True))
        self.cancel.set_halign(Gtk.Align.END)
        self.root.pack_end(self.cancel, False, False, 0)

    def show(self):
        picker = self.picker
        picker.stack.add_named(self.root, 'login-account')
        picker.prepare_ui(self.root)
        self.root.show_all()
        picker.stack.set_visible_child(self.root)
        picker.resize_view(min(picker.height, 150 + 38 * len(self.buttons)))
        if not picker.preview:
            picker.Layer.set_keyboard_mode(picker.window, picker.Layer.KeyboardMode.EXCLUSIVE)
        picker.window.show_all()
        self.cancel.grab_focus()

    def select(self, account_id):
        picker = self.picker
        macro = picker.library.get(self.macro_id)
        picker.dismiss_login_choice()
        if (not picker.game or session_key(*picker.game) != self.game or not macro
                or account_id not in dict(picker.library.login_choices(macro))):
            picker.message = 'Game or login profile changed. Press F9 to choose again.'
            picker.render_if_visible()
            return
        picker.run_macro(macro, self.full, account_id=account_id)
