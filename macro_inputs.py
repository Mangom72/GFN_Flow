"""Typed GTK action fields that serialize to the existing, validated step format."""
import re
from macro_library import BUTTONS, key_definitions, parse_step


ACTIONS = [('wait', 'Wait'), ('click', 'Click position'), ('key', 'Press key / shortcut'),
           ('down', 'Hold key'), ('up', 'Release key'), ('move', 'Move pointer'),
           ('button', 'Mouse button'), ('wheel', 'Scroll'),
           ('text', 'Type text'), ('clear', 'Clear field'), ('tab', 'Tab'),
           ('enter', 'Enter'), ('shifttab', 'Shift+Tab'),
           ('id', 'Enter saved account'), ('pw', 'Enter saved password')]


class ActionInputs:
    def __init__(self, Gtk, label):
        self.Gtk, self.label = Gtk, label
        self.loading = True
        self.on_change = lambda: None
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.action = Gtk.ComboBoxText()
        for identity, title in ACTIONS:
            self.action.append(identity, title)
        self.action.get_accessible().set_name('Action type')
        self.root.pack_start(self.action, False, False, 0)
        self.values = Gtk.Stack()
        self.values.set_hhomogeneous(False)
        self.values.set_vhomogeneous(False)
        self.root.pack_start(self.values, False, False, 0)
        wait = Gtk.Box(spacing=8)
        self.delay = self.number('Wait duration', 0, 600000, 500)
        wait.pack_start(self.delay, True, True, 0)
        wait.pack_start(label('ms', 'muted'), False, False, 0)
        self.values.add_named(wait, 'wait')
        point = Gtk.Grid(column_spacing=12, row_spacing=4)
        self.x = self.number('Click X coordinate', 0, 10000, 0)
        self.y = self.number('Click Y coordinate', 0, 10000, 0)
        for column, (title, control) in enumerate((('X', self.x), ('Y', self.y))):
            point.attach(label(title, 'field-label'), column, 0, 1, 1)
            control.set_hexpand(True)
            point.attach(control, column, 1, 1, 1)
        self.reference_hint = label('', 'muted')
        point.attach(self.reference_hint, 0, 2, 2, 1)
        self.values.add_named(point, 'click')
        wheel = Gtk.Box(spacing=8)
        self.wheel_x = self.number('Horizontal scroll ticks', -120, 120, 0)
        self.wheel_y = self.number('Vertical scroll ticks', -120, 120, 1)
        for title, control in (('Horizontal · + right', self.wheel_x), ('Vertical · + up', self.wheel_y)):
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            group.pack_start(label(title, 'field-label'), False, False, 0)
            group.pack_start(control, False, False, 0)
            wheel.pack_start(group, True, True, 0)
        self.values.add_named(wheel, 'wheel')
        button = Gtk.Box(spacing=8)
        self.mouse_button, self.button_state = Gtk.ComboBoxText(), Gtk.ComboBoxText()
        for name in BUTTONS:
            self.mouse_button.append(name, name)
        for identity, title in (('1', 'Press'), ('0', 'Release')):
            self.button_state.append(identity, title)
        self.mouse_button.set_active(0)
        self.button_state.set_active(0)
        for control, name in ((self.mouse_button, 'Mouse button'), (self.button_state, 'Button state')):
            control.get_accessible().set_name(name)
            control.connect('changed', self.changed)
            button.pack_start(control, True, True, 0)
        self.values.add_named(button, 'button')
        shortcut = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.key = Gtk.ComboBoxText.new_with_entry()
        self.key._user_options = True  # Linux key names are input data.
        self.key.get_child().set_width_chars(12)
        self.key.get_child().set_placeholder_text('Choose or search a key')
        self.key.get_accessible().set_name('Key')
        definitions = key_definitions()
        common = ['ENTER', 'ESC', 'SPACE', 'TAB', 'BACKSPACE', 'DELETE', 'UP', 'DOWN', 'LEFT', 'RIGHT',
                  'HOME', 'END', 'PAGEUP', 'PAGEDOWN']
        common += list('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') + [f'F{i}' for i in range(1, 25)]
        friendly = {'ESC': 'Escape', 'UP': 'Arrow up', 'DOWN': 'Arrow down', 'LEFT': 'Arrow left',
                    'RIGHT': 'Arrow right', 'PAGEUP': 'Page up', 'PAGEDOWN': 'Page down'}
        self.key_labels = {}
        for name in dict.fromkeys(common + sorted(definitions)):
            if name not in definitions or name == 'MAX':
                continue
            title = friendly.get(name, name if len(name) == 1 or name.startswith('F') and name[1:].isdigit() else name.replace('_', ' ').title())
            self.key.append(name, title)
            self.key_labels[title.casefold()] = name
        shortcut.pack_start(self.key, False, False, 0)
        modifiers = Gtk.Box(spacing=8)
        self.modifier_box = modifiers
        modifiers.set_no_show_all(True)
        self.modifiers = {}
        for name in ('Ctrl', 'Alt', 'Shift', 'Super'):
            check = Gtk.CheckButton(label=name)
            check.set_tooltip_text('Hold ' + name + ' with the selected key')
            check.connect('toggled', self.changed)
            self.modifiers[name] = check
            modifiers.pack_start(check, False, False, 0)
        shortcut.pack_start(modifiers, False, False, 0)
        modifiers.show_all()
        self.key.connect('changed', self.changed)
        self.key.get_child().connect('changed', self.changed)
        self.values.add_named(shortcut, 'key')
        text_scroll = Gtk.ScrolledWindow()
        text_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        text_scroll._input_scroll = True
        text_scroll.set_min_content_height(64)
        self.text = Gtk.TextView()
        self.text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text.set_left_margin(8)
        self.text.set_right_margin(8)
        self.text.set_top_margin(6)
        self.text.set_bottom_margin(6)
        self.text.get_accessible().set_name('Text to type')
        self.text.get_buffer().connect('changed', self.changed)
        text_scroll.add(self.text)
        self.values.add_named(text_scroll, 'text')
        for action in ('clear', 'tab', 'enter', 'shifttab', 'id', 'pw'):
            message = {'id': 'Uses the account in Settings → Account and password.',
                       'pw': 'Uses the saved keyring password. No password is stored in this action.'}.get(action, 'No additional input needed.')
            self.values.add_named(label(message, 'muted'), action)
        self.action.connect('changed', self.changed)
        self.load('wait:500')

    def number(self, name, minimum, maximum, value):
        entry = self.Gtk.SpinButton.new_with_range(minimum, maximum, 1)
        entry.set_numeric(True)
        entry.set_width_chars(6)
        entry.set_value(value)
        entry.get_accessible().set_name(name)
        entry.connect('changed', self.changed)
        entry.connect('value-changed', self.changed)
        return entry

    def changed(self, *_):
        if self.loading:
            return
        self.dirty = True
        action = self.action.get_active_id()
        if action:
            self.show_action(action)
        self.on_change()

    def show_action(self, action):
        self.values.set_visible_child_name({'down': 'key', 'up': 'key', 'move': 'click'}.get(action, action))
        self.modifier_box.set_visible(action == 'key')
        self.x.get_accessible().set_name('Pointer X coordinate' if action == 'move' else 'Click X coordinate')
        self.y.get_accessible().set_name('Pointer Y coordinate' if action == 'move' else 'Click Y coordinate')

    def load(self, step):
        action, value = parse_step(step)
        self.loading = True
        try:
            self.action.set_active_id(action)
            self.show_action(action)
            if action == 'wait':
                self.delay.set_value(value)
            elif action in ('click', 'move'):
                self.x.set_value(value[0])
                self.y.set_value(value[1])
            elif action == 'text':
                self.text.get_buffer().set_text(value)
            elif action == 'wheel':
                self.wheel_x.set_value(value[0])
                self.wheel_y.set_value(value[1])
            elif action == 'button':
                self.mouse_button.set_active(value[0])
                self.button_state.set_active_id(str(value[1]))
            elif action in ('key', 'down', 'up'):
                parts = step.partition(':')[2].replace('{', '').replace('}', '').split('+')
                aliases = {'CTRL': 'Ctrl', 'CONTROL': 'Ctrl', 'LEFTCTRL': 'Ctrl',
                           'ALT': 'Alt', 'LEFTALT': 'Alt', 'SHIFT': 'Shift', 'LEFTSHIFT': 'Shift',
                           'SUPER': 'Super', 'WIN': 'Super', 'LEFTMETA': 'Super'}
                keys = [p.strip() for p in parts if p.strip().upper() not in aliases]
                modifier_parts = parts
                if not keys:  # A modifier can also be the sole pressed key.
                    keys = [parts[-1].strip()]
                    modifier_parts = parts[:-1]
                held = {aliases[p.strip().upper()] for p in modifier_parts if p.strip().upper() in aliases}
                for name, check in self.modifiers.items():
                    check.set_active(name in held)
                keys = '+'.join(keys)
                key_aliases = {'ESCAPE': 'ESC', 'RETURN': 'ENTER', 'DEL': 'DELETE',
                               'CTRL': 'LEFTCTRL', 'CONTROL': 'LEFTCTRL', 'ALT': 'LEFTALT',
                               'SHIFT': 'LEFTSHIFT', 'SUPER': 'LEFTMETA', 'WIN': 'LEFTMETA'}
                identity = key_aliases.get(keys.upper(), keys.upper())
                if not self.key.set_active_id(identity):
                    self.key.set_active(-1)
                    self.key.get_child().set_text(keys)
        finally:
            self.loaded_step = step
            self.dirty = False
            self.loading = False

    def value(self):
        if not self.dirty:
            return self.loaded_step
        action = self.action.get_active_id()
        try:
            if action == 'wait':
                step = f'wait:{int(self.delay.get_text())}'
            elif action in ('click', 'move'):
                x, y = int(self.x.get_text()), int(self.y.get_text())
                step = f'{action}:{x},{y}'
            elif action == 'wheel':
                step = f'wheel:{int(self.wheel_x.get_text())},{int(self.wheel_y.get_text())}'
            elif action == 'button':
                step = f'button:{self.mouse_button.get_active_id()},{self.button_state.get_active_id()}'
            elif action in ('key', 'down', 'up'):
                key = self.key.get_active_id()
                if not key:
                    text = self.key.get_child().get_text().strip()
                    key = self.key_labels.get(text.casefold(), text)
                held = [name for name, check in self.modifiers.items() if check.get_active()] if action == 'key' else []
                step = action + ':' + '+'.join(held + [key])
            elif action == 'text':
                buffer = self.text.get_buffer()
                step = 'text:' + buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
            else:
                step = action
        except (TypeError, ValueError):
            raise ValueError('Enter whole numbers in the numeric fields.') from None
        if action in ('click', 'move') and (x > 10000 or y > 10000):
            raise ValueError('Coordinates must be between 0 and 10000.')
        if action in ('key', 'down', 'up') and not key:
            raise ValueError('Choose a key from the list.')
        parse_step(step)
        return step


class GameInputs:
    """Known game rules by selection; custom literal titles or explicit regex."""
    def __init__(self, Gtk, label, pairs, game, pattern):
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.root.pack_start(label('Game', 'field-label'), False, False, 0)
        self.choice = Gtk.ComboBoxText()
        self.choice._user_options = True  # Game names are user data, never UI translations.
        self.choice._fixed_options = {'none', 'custom'}
        self.choice.get_accessible().set_name('Game')
        self.choice.append('none', 'Choose a game…')
        self.options = {}
        seen = set()
        name_counts = {}
        for name, title_re in pairs:
            if not name or not title_re or (name, title_re) in seen:
                continue
            seen.add((name, title_re))
            identity = str(len(self.options))
            self.options[identity] = (name, title_re)
            name_counts[name] = name_counts.get(name, 0) + 1
            self.choice.append(identity, name if name_counts[name] == 1 else f'{name} · rule {name_counts[name]}')
        self.choice.append('custom', 'Custom game…')
        for cell in self.choice.get_cells():
            if isinstance(cell, Gtk.CellRendererText):
                cell.set_property('ellipsize', 3)
                cell.set_property('max-width-chars', 20)
        self.root.pack_start(self.choice, False, False, 0)
        self.custom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.root.pack_start(self.custom, False, False, 0)

        def entry(title, value, parent):
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            group.pack_start(label(title, 'field-label'), False, False, 0)
            field = Gtk.Entry()
            field.set_width_chars(12)
            field.set_text(value)
            field.get_accessible().set_name(title)
            group.pack_start(field, False, False, 0)
            parent.pack_start(group, False, False, 0)
            return field, group

        self.game, _ = entry('Game name', game, self.custom)
        self.match = Gtk.ComboBoxText()
        self.match.append('contains', 'Window title contains')
        self.match.append('pattern', 'Advanced title pattern')
        self.match.get_accessible().set_name('Game detection method')
        self.custom.pack_start(self.match, False, False, 0)
        self.contains, literal_group = entry('Title text', '', self.custom)
        self.contains.set_placeholder_text('Text shown in the game window title')
        self.pattern, pattern_group = entry('Window title pattern (regex)', pattern, self.custom)
        self.pattern.set_tooltip_text('Advanced: a case-insensitive regular expression. Existing rules are preserved.')
        self.custom.show_all()
        self.custom.set_no_show_all(True)
        # Child widgets must be shown before excluding conditional groups from show_all.
        for group in (literal_group, pattern_group):
            group.show_all()
            group.set_no_show_all(True)

        def match_changed(*_):
            literal = self.match.get_active_id() == 'contains'
            literal_group.set_visible(literal)
            pattern_group.set_visible(not literal)
            if literal:
                self.pattern.set_text(re.escape(self.contains.get_text()))

        def choose(*_):
            option = self.options.get(self.choice.get_active_id())
            self.custom.set_visible(self.choice.get_active_id() == 'custom')
            if option:
                self.game.set_text(option[0])
                self.pattern.set_text(option[1])
                self.choice.set_tooltip_text('Game detection uses the saved window-title rule.')
            elif self.choice.get_active_id() == 'custom':
                match_changed()
            else:
                self.game.set_text('')
                self.pattern.set_text('')

        self.match.connect('changed', match_changed)
        self.contains.connect('changed', lambda *_: match_changed() if self.match.get_active_id() == 'contains' else None)
        self.choice.connect('changed', choose)
        self.match.set_active_id('pattern' if pattern else 'contains')
        current = next((identity for identity, option in self.options.items() if option == (game, pattern)), None)
        self.choice.set_active_id(current or ('custom' if game or pattern else 'none'))
