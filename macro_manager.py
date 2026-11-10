"""Native macro library, visual action editor, and playback history."""
import copy
from pathlib import Path

from detection import login
from macro_library import TYPES, new_macro, parse_step, store_login_secret, validate
from macro_inputs import ActionInputs, GameInputs
from ui_theme import TOKENS, category_markup, macro_markup


class Manager:
    def __init__(self, picker, offscreen=False, width=920, height=600):
        self.picker = picker
        self.library = picker.library
        self.Gtk = Gtk = picker.Gtk
        self.draft = None
        self.fields = {}
        self.group_expanded = dict.fromkeys(dict(TYPES), True)
        self.rebuilding = False
        self.offscreen = offscreen
        self.embedded = not offscreen
        self.view_width = picker.width if self.embedded else width
        self.view_height = picker.library_height if self.embedded else height
        self.window = picker.window if self.embedded else Gtk.OffscreenWindow()
        if not self.embedded:
            self.window.set_default_size(width, height)
            self.window.set_size_request(width, height)
            self.window.connect('delete-event', lambda *_: self.close_popup())
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.root = root
        root.get_style_context().add_class('panel')
        if self.embedded:
            picker.stack.add_named(root, 'manager')
        else:
            self.window.add(root)
        header = Gtk.Box(spacing=8)
        self.back_button = picker.button('Back to game macros', self.back)
        header.pack_start(self.back_button, False, False, 0)
        header.pack_start(picker.label('Macro Studio', 'heading'), True, True, 0)
        header.pack_end(picker.button('Close', self.close_popup), False, False, 0)
        header.pack_end(picker.button('Settings', picker.show_settings), False, False, 0)
        root.pack_start(header, False, False, 0)
        self.notice = picker.label('', 'status')
        self.notice.set_line_wrap(False)
        self.notice.set_ellipsize(3)
        self.notice.set_no_show_all(True)
        self.tabs = Gtk.Notebook()
        self.tabs.set_scrollable(True)
        self.tabs.get_style_context().add_class('studio-tabs')
        self.picker.motion.notebook(self.tabs, slide=True)
        self.views = Gtk.Stack()
        self.views.set_hhomogeneous(False)
        self.views.set_vhomogeneous(False)
        self.picker.motion.stack(self.views, slide=True)
        self.views.add_named(self.tabs, 'browse')
        root.pack_start(self.views, True, True, 0)
        self.build_library()
        self.build_editor()
        self.build_history()
        from ui_visual_workflows import VisualWorkflowsPage
        self.visual_page = VisualWorkflowsPage(self)
        from ui_login_credentials import LoginCredentialsPage
        self.login_page = LoginCredentialsPage(self)
        from ui_game_blacklist import GameBlacklistPage
        self.game_blacklist = GameBlacklistPage(self)
        self.views.connect('notify::visible-child-name', self.update_back)
        self.tabs.connect('notify::page', self.update_back)
        self.tabs.connect('switch-page', self.update_tab_titles)
        self.update_tab_titles(self.tabs, self.tabs.get_nth_page(0), 0)
        self.update_back()
        root.pack_start(self.notice, False, False, 0)
        self.window.connect('key-press-event', self.keyboard)
        self.refresh()

    def close(self):
        if self.embedded:
            self.picker.show_picker()
        else:
            self.hide()
        return True

    def back_target(self):
        view = self.views.get_visible_child_name()
        if (view == 'editor' and self.editor.get_children()[0].get_current_page() == 1
                and self.action_stack.get_visible_child_name() == 'record'):
            return 'Back to actions'
        if view != 'browse' or self.tabs.get_current_page() != 0:
            return 'Back to library'
        return 'Back to game macros'

    def update_back(self, *_):
        target = self.back_target()
        for prop in ('tooltip-text', 'accessible-name'):
            self.picker.localizer.bind(self.back_button, prop, target)

    def back(self):
        target = self.back_target()
        if target == 'Back to actions':
            self.record_back()
        elif target == 'Back to library':
            self.show_library()
        else:
            self.close()

    def hide(self):
        if self.embedded:
            return self.picker.hide()
        self.window.hide()
        return True

    def close_popup(self):
        if self.embedded:
            return self.picker.close()
        self.show_library()
        return self.hide()

    def show(self, refresh=True):
        visual = getattr(self.picker, 'visual', None)
        if visual is not None and getattr(visual, 'active', False):
            visual.pause()
        self.picker.acknowledge_recording(self)
        if refresh:
            self.refresh()
        self.picker.prepare_ui(self.root)
        self.root.show_all()
        if self.embedded:
            self.picker.stack.set_visible_child_name('manager')
            if not self.picker.preview:
                self.picker.Layer.set_keyboard_mode(self.window, self.picker.Layer.KeyboardMode.EXCLUSIVE)
            self.picker.resize_view(self.view_height)
        self.window.show_all()

    def say(self, text, error=False):
        self.picker.localizer.bindings.get(self.notice, {}).pop('render', None)
        self.picker.localizer.bind(self.notice, 'label', text)
        self.picker.localizer.bind(self.notice, 'tooltip-text', text)
        self.picker.localizer.bind(self.notice, 'accessible-description', text)
        self.notice.set_visible(bool(text))
        style = self.notice.get_style_context()
        style.remove_class('error-status')
        if error:
            style.add_class('error-status')

    def show_library(self):
        self.picker.cancel_recording()
        self.picker.acknowledge_recording(self)
        self.tabs.set_current_page(0)
        self.views.set_visible_child_name('browse')

    def show_history(self):
        self.picker.cancel_recording()
        self.picker.acknowledge_recording(self)
        self.tabs.set_current_page(1)
        self.views.set_visible_child_name('browse')

    def show_visual_workflows(self):
        self.picker.cancel_recording()
        self.picker.acknowledge_recording(self)
        self.tabs.set_current_page(2)
        self.views.set_visible_child_name('browse')

    def flow(self, buttons):
        Gtk = self.Gtk
        box = Gtk.Box(spacing=4)
        for title, callback in buttons:
            box.pack_start(self.picker.button(title, lambda cb=callback: self.safe(cb)), False, False, 0)
        return box

    def safe(self, callback):
        try:
            callback()
        except (ValueError, OSError, KeyError, TypeError, login.LoginError) as exc:
            self.say(str(exc), error=True)

    def page(self, name):
        page = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=8)
        icon = {'Library': 'view-list-symbolic', 'History': 'document-open-recent-symbolic',
                'Visual Workflows': 'image-x-generic-symbolic',
                'Login Credentials': 'dialog-password-symbolic',
                'Blacklist': 'action-unavailable-symbolic'}[name]
        tab = self.Gtk.Box()
        image = self.Gtk.Image.new_from_icon_name(icon, self.Gtk.IconSize.BUTTON)
        image.show()
        tab.pack_start(image, False, False, 0)
        tab.title = self.picker.label(name)
        tab.title.set_line_wrap(False)
        tab.title.set_margin_start(5)
        tab.title_reveal = self.picker.motion.tab_title(tab.title)
        tab.pack_start(tab.title_reveal, False, False, 0)
        tab.set_tooltip_text(name)
        tab.get_accessible().set_name(name)
        self.tabs.append_page(page, tab)
        return page

    def update_tab_titles(self, tabs, selected, _index):
        for index in range(tabs.get_n_pages()):
            page = tabs.get_nth_page(index)
            tabs.get_tab_label(page).title_reveal.set_reveal_child(page == selected)

    def build_library(self):
        Gtk = self.Gtk
        page = self.page('Library')
        toolbar = Gtk.Box(spacing=8)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text('Search macros or games')
        self.search.set_width_chars(12)
        self.search.connect('search-changed', lambda *_: self.refresh())
        toolbar.pack_start(self.search, True, True, 0)
        self.filter = Gtk.ComboBoxText()
        for text in ('Visible macros', 'All macros including hidden', 'Deleted macros'):
            self.filter.append_text(text)
        self.filter.set_active(0)
        self.filter.connect('changed', lambda *_: self.refresh())
        filter_button = Gtk.MenuButton()
        filter_button.set_image(Gtk.Image.new_from_file(str(Path(__file__).parent / 'assets/filter.svg')))
        filter_button.set_tooltip_text('Filter: visible, hidden, or deleted macros')
        filter_button.get_accessible().set_name('Filter macros')
        filter_menu = Gtk.Menu()
        for index, title in enumerate(('Visible macros', 'Include hidden', 'Deleted macros')):
            item = Gtk.MenuItem(label=title)
            item.connect('activate', lambda _, index=index: self.filter.set_active(index))
            filter_menu.append(item)
        filter_menu.show_all()
        filter_button.set_popup(filter_menu)
        toolbar.pack_end(filter_button, False, False, 0)
        page.pack_start(toolbar, False, False, 0)
        library_header = Gtk.Box(spacing=8)
        self.library_title = self.picker.label('All macros', 'section')
        library_header.pack_start(self.library_title, True, True, 0)
        add = Gtk.MenuButton()
        add.set_always_show_image(True)
        add.set_image(Gtk.Image.new_from_icon_name('list-add-symbolic', Gtk.IconSize.BUTTON))
        add.get_style_context().add_class('primary')
        add.set_tooltip_text('Add macro')
        add.get_accessible().set_name('Add macro')
        popup = Gtk.Menu()
        add.set_popup(popup)
        for title, kind in [('Login macro', 'login'), ('Normal macro', 'normal')]:
            def choose(_, kind=kind):
                self.edit(new_macro(kind))
            item = Gtk.MenuItem(label=title)
            item.connect('activate', choose)
            popup.append(item)
        popup.show_all()
        library_header.pack_end(add, False, False, 0)
        more = Gtk.MenuButton()
        more.set_image(Gtk.Image.new_from_icon_name('view-more-symbolic', Gtk.IconSize.BUTTON))
        more.set_tooltip_text('Import / Export')
        more.get_accessible().set_name('Import / Export')
        file_menu = Gtk.Menu()
        for title, exporting in [('Import macros…', False), ('Export macros…', True)]:
            item = Gtk.MenuItem(label=title)
            item.connect('activate', lambda _, exporting=exporting: self.choose_file(exporting))
            file_menu.append(item)
        file_menu.show_all()
        more.set_popup(file_menu)
        library_header.pack_end(more, False, False, 0)
        page.pack_start(library_header, False, False, 0)
        self.rows = Gtk.TreeStore(str, str, str, str)
        self.tree = Gtk.TreeView(model=self.rows)
        self.tree.get_style_context().add_class('macro-list')
        self.tree.set_show_expanders(False)
        self.tree.set_level_indentation(0)
        self.tree.set_headers_visible(False)
        self.tree.set_has_tooltip(True)
        self.tree.connect('query-tooltip', self.macro_tooltip)
        self.hovered = None
        renderer = Gtk.CellRendererText()
        renderer.set_property('ellipsize', 3)
        renderer.set_property('ypad', TOKENS['row_padding'])
        column = Gtk.TreeViewColumn()
        chevron = Gtk.CellRendererPixbuf()
        chevron.set_property('stock-size', Gtk.IconSize.MENU)
        column.pack_start(chevron, False)
        column.set_cell_data_func(chevron, self.render_chevron)
        column.pack_start(renderer, True)
        column.set_cell_data_func(renderer, self.render_macro)
        column.set_expand(True)
        self.tree.append_column(column)
        self.tree.get_selection().connect('changed', lambda *_: self.update_context())
        self.tree.get_selection().set_select_function(lambda selection, model, path, *args: bool(model[path][0]))
        self.tree.connect('row-expanded', lambda tree, iterator, path: self.group_toggled(path, True))
        self.tree.connect('row-collapsed', lambda tree, iterator, path: self.group_toggled(path, False))
        self.tree.connect('motion-notify-event', self.hover)
        self.tree.connect('button-press-event', self.group_pressed)
        self.tree.connect('key-press-event', self.group_key)
        self.tree.connect('leave-notify-event', self.leave)
        self.tree.connect('row-activated', self.activate_row)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.library_scroll = scroll
        scroll.add(self.tree)
        page.pack_start(scroll, True, True, 0)
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.pack_end(footer, False, False, 0)
        self.context = Gtk.Box(spacing=4)
        self.context.get_style_context().add_class('context-bar')
        self.context.set_no_show_all(True)
        self.context_buttons = {}
        for title, action in [('Edit', 'edit'), ('Duplicate', 'duplicate'), ('Hide / Show', 'hide'),
                              ('Delete', 'delete'), ('Restore', 'restore'), ('Queue for F9', 'queue')]:
            button = self.picker.button(title, lambda action=action: self.safe(lambda: self.act(action)))
            button.set_no_show_all(True)
            if action == 'delete':
                button.get_style_context().add_class('danger')
            self.context.pack_start(button, False, False, 0)
            self.context_buttons[action] = button
        footer.pack_start(self.context, False, False, 0)
        self.summary = self.picker.label('', 'muted')
        footer.pack_end(self.summary, False, False, 0)

    def refresh(self):
        if not hasattr(self, 'rows'):
            return
        model, iterator = self.tree.get_selection().get_selected()
        selected = model[iterator][0] if iterator else None
        self.rebuilding = True
        self.rows.clear()
        query = self.search.get_text().casefold()
        mode = self.filter.get_active()
        source = self.library.data['trash'] if mode == 2 else self.library.macros
        self.shown_count = 0
        selected_path = None
        for kind, title in TYPES:
            macros = [m for m in source if m['kind'] == kind
                      and not (mode == 0 and m['hidden'])
                      and (not query or query in (m['name'] + ' ' + m['game']).casefold())]
            group = self.rows.append(None, ['', title, '', str(len(macros))])
            self.shown_count += len(macros)
            if not macros:
                self.rows.append(group, ['', 'No macros to show.', 'empty', ''])
            for macro in macros:
                state = 'Deleted' if mode == 2 else 'Hidden' if macro['hidden'] else 'Disabled' if not macro['enabled'] else 'First F9' if macro['auto'] else 'Manual'
                row = self.rows.append(group, [macro['id'], macro['name'], macro['game'], state])
                if macro['id'] == selected:
                    selected_path = self.rows.get_path(row)
            if query or self.group_expanded[kind]:
                self.tree.expand_row(self.rows.get_path(group), False)
        if selected_path is not None:
            group_path = selected_path.copy()
            group_path.up()
            if self.tree.row_expanded(group_path):
                self.tree.get_selection().select_path(selected_path)
        self.rebuilding = False
        if hasattr(self, 'summary'):
            self.picker.localizer.bind(self.summary, 'render', lambda label: label.set_text(
                self.picker.tr('{shown} shown · {hidden} hidden · {deleted} deleted').format(
                    shown=self.shown_count, hidden=sum(m['hidden'] for m in self.library.macros), deleted=len(self.library.data['trash']))))
        if hasattr(self, 'library_title'):
            self.picker.localizer.bind(self.library_title, 'label', ('Visible macros', 'All macros', 'Deleted macros')[mode])
        self.update_context()
        self.refresh_history()
        if hasattr(self, 'visual_page'):
            self.visual_page.refresh()
        self.picker.prepare_ui(self.root)

    def group_toggled(self, path, expanded):
        if self.rebuilding or len(path.get_indices()) != 1:
            return
        if not self.search.get_text():
            self.group_expanded[TYPES[path.get_indices()[0]][0]] = expanded
        if not expanded:
            model, selected = self.tree.get_selection().get_selected()
            if selected is not None and path.is_ancestor(model.get_path(selected)):
                self.tree.get_selection().unselect_all()

    def activate_row(self, tree, path, column):
        if self.rows[path][0]:
            self.safe(lambda: self.act('edit'))
        elif self.rows[path][2] == 'empty':
            return
        elif tree.row_expanded(path):
            tree.collapse_row(path)
        else:
            tree.expand_row(path, False)

    def group_pressed(self, tree, event):
        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if event.button != 1 or not hit or len(hit[0].get_indices()) != 1:
            return False
        tree.get_selection().unselect_all()
        tree.set_cursor(hit[0])
        tree.grab_focus()
        self.activate_row(tree, hit[0], hit[1])
        return True

    def group_key(self, tree, event):
        from gi.repository import Gdk
        path, column = tree.get_cursor()
        if path is None:
            return False
        key = Gdk.keyval_name(event.keyval) or ''
        if len(path.get_indices()) > 1:
            if key != 'Left':
                return False
            path.up()
            tree.get_selection().unselect_all()
            tree.set_cursor(path)
            return True
        if key not in ('Left', 'Right', 'space', 'Return', 'KP_Enter'):
            return False
        if key == 'Left':
            tree.collapse_row(path)
        elif key == 'Right':
            if tree.row_expanded(path):
                child = self.rows.iter_children(self.rows.get_iter(path))
                if child is not None and self.rows[child][0]:
                    tree.set_cursor(self.rows.get_path(child))
            else:
                tree.expand_row(path, False)
        else:
            self.activate_row(tree, path, column)
        return True

    def render_macro(self, column, renderer, model, iterator, *_):
        escape = self.picker.GLib.markup_escape_text
        identity, name, game, state = list(model[iterator])
        renderer.set_property('ypad', TOKENS['row_padding'])
        renderer.set_property('xpad', 4 if model.iter_parent(iterator) is None else TOKENS['list_indent'])
        if not identity:
            renderer.set_property('markup', f'<span size="{TOKENS["section_size"] * 768}" foreground="{TOKENS["muted"]}">{escape(self.picker.tr(name))}</span>' if game == 'empty' else category_markup(self.picker.tr(name), state))
            renderer.set_property('cell-background-set', False)
            return
        source = self.library.data['trash'] if self.filter.get_active() == 2 else self.library.macros
        macro = next((m for m in source if m['id'] == identity), None)
        count = len(macro['steps']) if macro else 0
        renderer.set_property('markup', macro_markup(game, count, self.picker.tr, muted=state in ('Deleted', 'Hidden', 'Disabled')))
        selected_model, selected = self.tree.get_selection().get_selected()
        is_selected = selected is not None and selected_model[selected][0] == identity
        renderer.set_property('cell-background-set', self.hovered == identity and not is_selected)
        if self.hovered == identity and not is_selected:
            renderer.set_property('cell-background', TOKENS['hover'])

    def macro_tooltip(self, tree, x, y, keyboard, tooltip):
        valid, _, _, model, path, iterator = tree.get_tooltip_context(x, y, keyboard)
        if not valid or not model[iterator][0]:
            return False
        _, name, _, state = list(model[iterator])
        tooltip.set_text(name + ('\n' + self.picker.tr(state) if state in ('Deleted', 'Hidden', 'Disabled') else ''))
        tree.set_tooltip_row(tooltip, path)
        return True

    def render_chevron(self, column, renderer, model, iterator, *_):
        group = model.iter_parent(iterator) is None
        renderer.set_property('visible', group)
        if group:
            expanded = self.tree.row_expanded(model.get_path(iterator))
            renderer.set_property('icon-name', 'pan-down-symbolic' if expanded else 'pan-end-symbolic')

    def hover(self, tree, event):
        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        identity = self.rows[hit[0]][0] if hit else None
        if identity != self.hovered:
            self.hovered = identity
            tree.queue_draw()
        return False

    def leave(self, tree, *_):
        self.hovered = None
        tree.queue_draw()
        return False

    def update_context(self):
        if not hasattr(self, 'context'):
            return
        model, iterator = self.tree.get_selection().get_selected()
        selected = iterator is not None
        self.context.set_visible(selected)
        if not selected:
            return
        deleted = self.filter.get_active() == 2
        macro = self.library.get(model[iterator][0])
        for action, button in self.context_buttons.items():
            button.set_visible(action == 'restore' if deleted else action != 'restore')
        if macro:
            self.context_buttons['hide'].set_tooltip_text('Show macro' if macro['hidden'] else 'Hide macro')
            icon = 'view-reveal-symbolic' if macro['hidden'] else 'view-conceal-symbolic'
            if self.Gtk.IconTheme.get_default().has_icon(icon):
                self.context_buttons['hide'].set_image(self.Gtk.Image.new_from_icon_name(icon, self.Gtk.IconSize.BUTTON))
            self.context_buttons['queue'].set_sensitive(not macro['hidden'] and macro['enabled'] and not self.picker.busy)

    def keyboard(self, window, event):
        from gi.repository import Gdk
        if self.embedded and self.picker.stack.get_visible_child_name() != 'manager':
            return False
        if self.picker.restart_confirmation is not None:
            return False
        key = Gdk.keyval_name(event.keyval) or ''
        control = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if control and key in ('1', '2', '3', '4', '5') and self.views.get_visible_child_name() == 'browse':
            self.tabs.set_current_page(int(key) - 1)
            return True
        if control and key.lower() == 'f':
            self.show_library()
            self.search.grab_focus()
            return True
        if control and key.lower() == 'n':
            if self.views.get_visible_child_name() == 'browse' and self.tabs.get_current_page() == 2:
                self.safe(self.visual_page.new)
            else:
                self.safe(lambda: self.edit(new_macro('normal')))
            return True
        if control and key.lower() == 's' and self.views.get_visible_child_name() == 'browse' and self.tabs.get_current_page() == 2:
            self.safe(self.visual_page.save)
            return True
        if control and key.lower() == 's' and self.views.get_visible_child_name() == 'browse' and self.tabs.get_current_page() == 3:
            if self.login_page.button.get_sensitive():
                self.login_page.button.clicked()
            return True
        if control and key.lower() == 's' and self.views.get_visible_child_name() == 'editor':
            self.safe(self.save_draft)
            return True
        if key == 'Escape':
            if self.views.get_visible_child_name() != 'browse' or self.tabs.get_current_page() != 0:
                self.back()
            elif self.tree.get_selection().count_selected_rows():
                self.tree.get_selection().unselect_all()
            else:
                self.hide()
            return True
        if key == 'Delete' and self.views.get_visible_child_name() == 'browse' and self.tabs.get_current_page() == 0 and not isinstance(window.get_focus(), self.Gtk.Entry):
            self.safe(lambda: self.act('delete'))
            return True
        return False

    def act(self, action):
        model, iterator = self.tree.get_selection().get_selected()
        if iterator is None:
            self.say('Select a macro first.')
            return
        identity = model[iterator][0]
        if self.filter.get_active() == 2:
            if action == 'restore':
                self.library.restore(identity)
                self.say('Macro restored with auto login turned off.')
                self.refresh()
                self.picker.render_if_visible()
            else:
                self.say('Deleted macros can be restored. Switch to All macros to edit them.')
            return
        macro = self.library.get(identity)
        if action == 'edit':
            self.edit(macro)
        elif action == 'duplicate':
            self.edit(self.library.duplicate(identity))
        elif action == 'hide':
            self.library.hide(identity)
            self.say('Macro hidden from F9 and auto login.' if macro['hidden'] else 'Macro is visible again.')
        elif action == 'delete':
            self.library.delete(identity)
            self.say('Macro deleted. Restore it from Deleted macros if needed.')
        elif action == 'queue':
            if macro['hidden'] or not macro['enabled']:
                self.say('Show and enable this macro before running it.')
                return
            self.picker.pending = identity
            self.picker.message = f'Queued: {macro["name"]}. Focus its game, then press F9.'
            self.picker.hide()
            self.hide()
        else:
            self.say('Select Deleted macros to restore a macro.')
        self.refresh()
        self.picker.render_if_visible()

    def build_editor(self):
        Gtk = self.Gtk
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.views.add_named(page, 'editor')
        self.editor_title = self.picker.label('Edit macro', 'game')
        page.pack_start(self.editor_title, False, False, 0)
        self.editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.pack_start(self.editor, True, True, 0)
        self.editor.pack_start(self.picker.label('Choose Edit or Add in the library.', 'muted'), False, False, 0)
        footer = Gtk.Box(spacing=4)
        footer.get_style_context().add_class('context-bar')
        save = self.picker.button('Save macro', lambda: self.safe(self.save_draft), primary=True)
        save.set_tooltip_text('Save macro · Ctrl+S')
        save.set_no_show_all(True)
        self.save_button = save
        footer.pack_end(save, False, False, 0)
        page.pack_start(footer, False, False, 0)

    def field(self, title, value, key, numeric=None, secret=False):
        Gtk = self.Gtk
        titles = {'name': 'Name', 'game': 'Game', 'title_re': 'Window title pattern',
                  'user': 'Account / email', 'credential_key': 'Keyring credential',
                  'password': 'New password (optional)', 'login_limit': 'Login action limit',
                  'repeat': 'Repeats', 'repeat_delay': 'Repeat delay · ms', 'step_delay': 'Action delay · ms',
                  'ref_width': 'Reference width', 'ref_height': 'Reference height'}
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        label = self.picker.label(titles.get(key, title), 'field-label')
        label.set_tooltip_text(title)
        group.pack_start(label, False, False, 0)
        if numeric:
            entry = Gtk.SpinButton.new_with_range(*numeric, 1)
            entry.set_numeric(True)
            entry.set_value(value)
        else:
            entry = Gtk.Entry()
            entry.set_width_chars(12)
            entry.set_text(value)
            entry.set_visibility(not secret)
            if secret:
                entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
                entry.set_input_hints(Gtk.InputHints.NO_SPELLCHECK)
        entry.get_accessible().set_name(title)
        group.pack_start(entry, False, False, 0)
        if numeric:
            if not hasattr(self, 'numeric_grid'):
                self.numeric_grid = Gtk.Grid(column_spacing=12, row_spacing=10)
                self.numeric_index = 0
                self.playback_form.pack_start(self.numeric_grid, False, False, 0)
            group.set_hexpand(True)
            self.numeric_grid.attach(group, self.numeric_index % 2, self.numeric_index // 2, 1, 1)
            self.numeric_index += 1
        else:
            self.form.pack_start(group, False, False, 0)
        self.fields[key] = entry
        return entry

    def edit(self, macro):
        Gtk = self.Gtk
        self.editor_title.set_text('Edit macro' if self.library.get(macro['id']) else 'New macro')
        self.picker.pending_capture = None
        self.picker.cancel_recording()
        self.picker.acknowledge_recording(self)
        self.draft = copy.deepcopy(macro)
        self.record_undo = None
        self.reference_notice = False
        self.save_button.show()
        self.save_button.set_sensitive(True)
        for child in self.editor.get_children():
            child.destroy()
        if hasattr(self, 'numeric_grid'):
            del self.numeric_grid
        sections = Gtk.Notebook()
        self.picker.motion.notebook(sections)
        sections.connect('notify::page', self.update_back)
        self.editor.pack_start(sections, True, True, 0)
        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.playback_form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.playback_form.pack_start(self.picker.label('Playback', 'section'), False, False, 0)
        settings = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL if self.view_width >= 640 else Gtk.Orientation.VERTICAL, spacing=24)
        self.form.set_hexpand(True)
        self.playback_form.set_hexpand(True)
        settings.pack_start(self.form, True, True, 0)
        settings.pack_start(self.playback_form, True, True, 0)
        general = Gtk.ScrolledWindow()
        general.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        general.add(settings)
        sections.append_page(general, Gtk.Label(label='Settings'))
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.action_stack = Gtk.Stack()
        self.action_stack.connect('notify::visible-child-name', self.update_back)
        self.picker.motion.stack(self.action_stack, slide=True)
        self.action_stack.set_hhomogeneous(False)
        self.action_stack.set_vhomogeneous(False)
        self.action_stack.add_named(actions, 'edit')
        sections.append_page(self.action_stack, Gtk.Label(label='Actions'))
        record_header = Gtk.Box(spacing=8)
        record_header.pack_start(self.picker.label('Action sequence', 'section'), True, True, 0)
        self.record_button = self.picker.button('Record actions', self.record_setup)
        record_header.pack_end(self.record_button, False, False, 0)
        self.discard_record_button = self.picker.button('Discard recording', self.discard_recording)
        self.discard_record_button.set_tooltip_text('Discard recording and restore the actions from before it')
        self.discard_record_button.set_no_show_all(True)
        record_header.pack_end(self.discard_record_button, False, False, 0)
        actions.pack_start(record_header, False, False, 0)
        self.build_record_setup()
        self.fields = {}
        profile = None
        credential_notice = ''
        if macro['source_profile']:
            profile = self.library.resolved_profile(macro, self.picker.legacy_profiles, with_account=False)
            self.draft.update(user=profile.user, credential_key=profile.credential_key,
                              steps=profile.steps[:], ref_width=profile.ref_width, ref_height=profile.ref_height)
            self.draft['login_limit'] = next((i for i, step in enumerate(profile.steps) if step.startswith('wait:') and int(step.split(':', 1)[1]) >= 2000), 0)
        if macro.get('account_key'):
            try:
                self.draft['user'] = login.get_secret(macro['account_key'])
            except login.LoginError:
                self.draft['user'] = ''
                credential_notice = 'Saved account unavailable. Unlock your keyring or enter a replacement account.'
        self.form.pack_start(self.picker.label('Identity', 'section'), False, False, 0)
        self.field('Macro name', self.draft['name'], 'name')
        self.form.pack_start(self.picker.label('Macro type', 'field-label'), False, False, 0)
        self.kind = Gtk.ComboBoxText()
        for kind, title in TYPES:
            self.kind.append(kind, title.removesuffix('s'))
        self.kind.set_active_id(macro['kind'])
        self.kind.get_accessible().set_name('Macro type')
        self.kind.set_tooltip_text('Login macros can run on the first F9. Normal macros run manually. Changing type preserves actions.')
        self.form.pack_start(self.kind, False, False, 0)
        pairs = self.library.game_options(self.picker.legacy_profiles)
        self.target = GameInputs(Gtk, self.picker.label, pairs, self.draft['game'], self.draft['title_re'])
        self.fields.update(game=self.target.game, title_re=self.target.pattern)
        self.form.pack_start(self.target.root, False, False, 0)
        self.field('Description', self.draft['description'], 'description')
        self.credentials = Gtk.Expander(label='Account and password')
        self.credentials.set_no_show_all(True)
        self.credentials.set_expanded(macro['kind'] == 'login')
        self.form.pack_start(self.credentials, False, False, 0)
        identity_form = self.form
        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.credentials.add(self.form)
        self.field('Account / email', self.draft['user'], 'user')
        self.field('New password (optional; saved only to your keyring)', '', 'password', secret=True)
        self.fields['password'].set_placeholder_text('Leave blank to keep saved password')
        self.fields['password'].set_tooltip_text('Stored only in your system keyring. Leave blank to keep the saved password.')
        self.form.show_all()
        self.form = identity_form
        limit = self.field('Login-only action count (0 = all; Full login plays all)', self.draft.get('login_limit', 0), 'login_limit', (0, 1000))
        self.login_limit_group = limit.get_parent()
        self.login_limit_group.show_all()
        self.login_limit_group.set_no_show_all(True)
        self.auto = Gtk.CheckButton(label='Run this login on the first F9 for its game')
        self.auto.set_active(macro['auto'])
        self.auto.set_sensitive(macro['kind'] == 'login')
        self.auto.get_child().set_line_wrap(True)
        self.auto.get_child().set_max_width_chars(24)
        self.auto.set_no_show_all(True)
        self.auto.set_visible(macro['kind'] == 'login')
        self.playback_form.pack_start(self.auto, False, False, 0)
        self.enabled = Gtk.CheckButton(label='Enabled')
        self.enabled.set_active(macro['enabled'])
        self.playback_form.pack_start(self.enabled, False, False, 0)
        self.field('Repeat count', macro['repeat'], 'repeat', (1, 100))
        self.field('Delay between repeats (ms)', macro['repeat_delay'], 'repeat_delay', (0, 600000))
        self.field('Delay between actions (ms)', macro['step_delay'], 'step_delay', (0, 10000))
        self.field('Reference width', self.draft['ref_width'], 'ref_width', (1, 10000))
        self.field('Reference height', self.draft['ref_height'], 'ref_height', (1, 10000))
        self.playback_form.reorder_child(self.numeric_grid, -1)
        self.steps = Gtk.ListStore(str)
        for step in self.draft['steps']:
            self.steps.append([step])
        self.steps_view = Gtk.TreeView(model=self.steps)
        self.steps_view.set_headers_visible(False)
        renderer = Gtk.CellRendererText()
        renderer.set_property('ypad', 7)
        renderer.set_property('xpad', 8)
        renderer.set_property('ellipsize', 3)
        def step_markup(column, cell, model, iterator, *_):
            escape = self.picker.GLib.markup_escape_text
            raw = model[iterator][0]
            action, _, value = raw.partition(':')
            names = {'wait': 'Wait', 'click': 'Click', 'key': 'Press', 'text': 'Type',
                     'down': 'Hold key', 'up': 'Release key', 'move': 'Move pointer',
                     'button': 'Mouse button', 'wheel': 'Scroll',
                     'id': 'Enter account', 'pw': 'Enter password', 'clear': 'Clear field',
                     'tab': 'Tab', 'enter': 'Enter', 'shifttab': 'Shift+Tab'}
            if action == 'wait':
                value += ' ms'
            elif action == 'button':
                button, _, state = value.partition(',')
                value = button + (' · Press' if state == '1' else ' · Release')
            elif action == 'wheel':
                x, y = map(int, value.split(','))
                value = ', '.join(title + f' {abs(amount)}' for title, amount in
                                  (('Right' if x > 0 else 'Left', x), ('Up' if y > 0 else 'Down', y)) if amount)
            elif action == 'text':
                value = value.replace('\r', '').replace('\n', ' ↵ ')
                if len(value) > 120:
                    value = value[:117] + '…'
            step = model.get_path(iterator).get_indices()[0] + 1
            cell.set_property('markup', f'<span foreground="{TOKENS["muted"]}">{step:02d}</span>  <b>{escape(self.picker.tr(names.get(action, action)))}</b>  <span foreground="{TOKENS["muted"]}">{escape(value)}</span>')
        column = Gtk.TreeViewColumn()
        column.pack_start(renderer, True)
        column.set_cell_data_func(renderer, step_markup)
        column.set_expand(True)
        self.steps_view.append_column(column)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(TOKENS['control'] + 2)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.steps_view)
        actions.pack_start(scroll, True, True, 0)
        action_header = Gtk.Box(spacing=8)
        self.action_title = self.picker.label('New action', 'section')
        action_header.pack_start(self.action_title, True, True, 0)
        self.new_action_button = self.picker.button('New action', self.new_action)
        self.new_action_button.set_tooltip_text('Clear selection to start a new action')
        self.new_action_button.set_no_show_all(True)
        action_header.pack_end(self.new_action_button, False, False, 0)
        actions.pack_start(action_header, False, False, 0)
        inspector_scroll = Gtk.ScrolledWindow()
        inspector_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        inspector_scroll.set_overlay_scrolling(False)
        inspector_scroll.set_min_content_height(0)
        inspector_scroll.set_propagate_natural_height(True)
        inspector = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inspector_scroll.add(inspector)
        self.inspector_scroll = inspector_scroll
        actions.pack_start(inspector_scroll, False, True, 0)
        self.inputs = ActionInputs(Gtk, self.picker.label)
        self.picker.motion.stack(self.inputs.values)
        self.action = self.inputs.action
        inspector.pack_start(self.inputs.root, False, False, 0)
        self.action_error = self.picker.label('', 'input-error')
        self.action_error.set_no_show_all(True)
        inspector.pack_start(self.action_error, False, False, 0)
        step_toolbar = self.flow([('Add action', self.add_step), ('Capture click position', self.capture_point), ('Move up', lambda: self.move_step(-1)),
                                 ('Move down', lambda: self.move_step(1)), ('Remove action', self.remove_step)])
        self.add_action_button = step_toolbar.get_children()[0]
        self.add_action_button.set_tooltip_text('Append a new action using the values above')
        self.step_buttons = step_toolbar.get_children()[2:]
        self.editing_step = None
        self.action_valid = True
        self.selecting_step = False
        self.syncing_action = False
        self.inputs.on_change = self.action_changed
        for key in ('ref_width', 'ref_height'):
            for signal in ('value-changed', 'changed'):
                self.fields[key].connect(signal, lambda *_: self.action_changed(apply=False))
        self.steps_view.get_selection().connect('changed', self.select_action)
        for signal in ('row-inserted', 'row-deleted', 'rows-reordered'):
            self.steps.connect(signal, lambda *_: self.update_step_actions())
        self.update_step_actions()
        actions.pack_start(step_toolbar, False, False, 0)
        self.views.set_visible_child_name('editor')
        self.say(credential_notice, error=bool(credential_notice))
        self.kind.connect('changed', lambda *_: self.type_changed())
        self.steps.connect('row-inserted', lambda *_: self.type_changed())
        self.steps.connect('row-deleted', lambda *_: self.type_changed())
        self.steps.connect('row-changed', lambda *_: self.type_changed())
        self.type_changed()
        self.picker.prepare_ui(self.root)
        self.window.show_all()
        sections.set_current_page(0 if not macro['game'] else 1)
        sections.connect('notify::page', self.editor_section_changed)

    def editor_section_changed(self, sections, *_):
        if sections.get_current_page() != 1 and self.picker.pending_record is self:
            self.record_back()

    def build_record_setup(self):
        Gtk = self.Gtk
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.action_stack.add_named(page, 'record')
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_overlay_scrolling(False)
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        scroll.add(form)
        page.pack_start(scroll, True, True, 0)
        form.pack_start(self.picker.label('Record actions', 'section'), False, False, 0)
        self.record_hint = self.picker.label('Esc is recorded. F9 starts/stops recording.', 'muted')
        form.pack_start(self.record_hint, False, False, 0)
        self.record_keyboard = Gtk.CheckButton(label='Keyboard')
        self.record_keyboard.set_tooltip_text('Record keyboard presses, releases and held keys')
        self.record_mouse = Gtk.CheckButton(label='Mouse')
        self.record_mouse.set_tooltip_text('Record menu clicks, drags and scroll. Locked-camera movement is not supported.')
        self.record_keyboard.set_active(True)
        self.record_mouse.set_active(True)
        for control in (self.record_keyboard, self.record_mouse):
            form.pack_start(control, False, False, 0)
        form.pack_start(self.picker.label('Add recorded actions', 'field-label'), False, False, 0)
        self.record_mode = Gtk.ComboBoxText()
        self.record_mode.append('replace', 'Replace current actions')
        self.record_mode.append('append', 'Append to current actions')
        self.record_mode.set_active_id('replace')
        self.record_mode.get_accessible().set_name('Recording destination')
        form.pack_start(self.record_mode, False, False, 0)
        form.pack_start(self.picker.label('Typed keys are captured. Use saved credentials for login.', 'muted'), False, False, 0)
        controls = self.flow([('Arm recording', self.arm_recording)])
        self.record_arm_button = controls.get_children()[-1]
        self.record_arm_button.get_style_context().add_class('primary')
        controls.pack_end(self.picker.help_button('Recording information',
            'Focus your matching game, then F9 starts/stops capture. Leaving the game stops it.\n\n'
            'Timing is preserved with a 0 ms action delay. Review, then Save; nothing is saved automatically.\n\n'
            'Use saved account/password actions for credentials. F9/F10/F12 are excluded. VPN, hotspot shortcuts and anti-AFK pause during recording. Limit: 5 minutes / 1,000 actions.\n\n'
            'Mouse capture supports menus, clicks, drags and scroll, not locked-camera movement.'), False, False, 0)
        page.pack_end(controls, False, False, 0)

    def record_setup(self):
        self.update_recording_controls()
        self.action_stack.set_visible_child_name('record')
        self.save_button.hide()
        self.save_button.set_sensitive(False)
        self.say('')

    def record_back(self):
        self.picker.cancel_recording()
        self.say('')
        self.action_stack.set_visible_child_name('edit')
        self.save_button.show()
        self.save_button.set_sensitive(self.action_valid)

    def update_recording_controls(self):
        armed = self.picker.pending_record is self
        self.picker.localizer.bind(self.record_arm_button, 'label', 'Cancel recording' if armed else 'Arm recording')
        self.picker.localizer.bind(self.record_arm_button, 'accessible-name', 'Cancel recording' if armed else 'Arm recording')
        self.picker.localizer.bind(self.record_hint, 'label',
            'Recording armed. Return to your game and press F9.' if armed
            else 'Esc is recorded. F9 starts/stops recording.')
        for control in (self.record_keyboard, self.record_mouse, self.record_mode):
            control.set_sensitive(not armed)

    def arm_recording(self):
        if self.picker.pending_record is self:
            self.picker.cancel_recording()
            return
        import re
        width, height = self.reference_dimensions()
        pattern = self.fields['title_re'].get_text().strip()
        if not pattern or not self.fields['game'].get_text().strip():
            raise ValueError('Choose a game in Settings before recording.')
        try:
            re.compile(pattern.replace('(?i)', ''), re.I)
        except re.error:
            raise ValueError('Fix the game title pattern before recording.') from None
        keyboard, mouse = self.record_keyboard.get_active(), self.record_mouse.get_active()
        if not (keyboard or mouse):
            raise ValueError('Choose keyboard or mouse recording.')
        replace = self.record_mode.get_active_id() == 'replace'
        delay = int(self.fields['step_delay'].get_text())
        if not 0 <= delay <= 10000:
            raise ValueError('Action delay must be between 0 and 10000 ms.')
        prefix = []
        if not replace:
            for row in self.steps:
                prefix.append(row[0])
                if delay:
                    prefix.append(f'wait:{delay}')
        capacity = 1000 - len(prefix)
        if capacity < 10:
            raise ValueError('Not enough space to append. Choose Replace current actions or remove some actions.')
        self.record_request = dict(pattern=pattern, width=width, height=height,
                                   keyboard=keyboard, mouse=mouse, replace=replace, capacity=capacity, prefix=prefix,
                                   prefix_limit=self.fields['login_limit'].get_value_as_int() * (2 if delay else 1))
        self.picker.arm_recording(self)

    def record_matches(self, window):
        import re
        return bool(login.GFN_APP_CLASSES.search(window.window_class) and
                    re.search(self.record_request['pattern'].replace('(?i)', ''), window.title, re.I))

    def record_point(self, window, request):
        width, height = request['width'], request['height']
        scale = min(window.width / width, window.height / height)
        if scale <= 0:
            return None
        profile = login.Profile('', '', '', '', width, height, [])
        ox, oy = login.map_point(window, profile, 0, 0)
        cx, cy = login.cursor_position()
        x, y = round((cx - ox) / scale), round((cy - oy) / scale)
        return (x, y) if 0 <= x <= width and 0 <= y <= height else None

    def accept_recording(self, steps, reason):
        self.record_back()
        if steps:
            self.record_undo = ([row[0] for row in self.steps], self.fields['step_delay'].get_text(),
                                self.fields['login_limit'].get_text())
            self.new_action()
            if self.record_request['replace']:
                self.steps.clear()
                self.fields['login_limit'].set_value(0)
            elif 'prefix' in self.record_request:
                self.steps.clear()
                for step in self.record_request['prefix']:
                    self.steps.append([step])
                self.fields['login_limit'].set_value(self.record_request['prefix_limit'])
            for step in steps:
                self.steps.append([step])
            self.fields['step_delay'].set_value(0)
            self.steps_view.get_selection().select_path(len(self.steps) - len(steps))
            self.discard_record_button.show()
        source = ('{reason} Added {count} actions to this draft. Review, then Save.' if steps
                  else '{reason} No actions added; your draft is unchanged.')
        self.say('')
        def render(label):
            text = self.picker.tr(source).format(reason=self.picker.tr(reason), count=len(steps))
            label.set_text(text)
            label.set_tooltip_text(text)
            label.get_accessible().set_description(text)
        self.picker.localizer.bind(self.notice, 'render', render)
        self.notice.show()

    def discard_recording(self):
        if not self.record_undo:
            return
        steps, delay, limit = self.record_undo
        self.new_action()
        self.steps.clear()
        for step in steps:
            self.steps.append([step])
        for key, text in (('step_delay', delay), ('login_limit', limit)):
            try:
                value = int(text)
            except ValueError:
                self.fields[key].set_text(text)
            else:
                self.fields[key].set_value(value)
        self.record_undo = None
        self.discard_record_button.hide()
        self.say('Recording discarded. Previous draft actions restored.')

    def type_changed(self):
        is_login = self.kind.get_active_id() == 'login'
        if not is_login:
            self.auto.set_active(False)
        self.auto.set_sensitive(is_login)
        self.auto.set_visible(is_login)
        self.login_limit_group.set_visible(is_login)
        numeric = (['login_limit'] if is_login else []) + ['repeat', 'repeat_delay', 'step_delay', 'ref_width', 'ref_height']
        for index, key in enumerate(numeric):
            group = self.fields[key].get_parent()
            self.numeric_grid.child_set_property(group, 'left-attach', index % 2)
            self.numeric_grid.child_set_property(group, 'top-attach', index // 2)
        has_credentials = any(row[0] in ('id', 'pw') for row in self.steps)
        self.credentials.set_visible(is_login or has_credentials)

    def reference_dimensions(self):
        try:
            width, height = (int(self.fields[key].get_text()) for key in ('ref_width', 'ref_height'))
        except ValueError:
            raise ValueError('Enter whole numbers for the reference resolution in Settings.') from None
        if not 1 <= width <= 10000 or not 1 <= height <= 10000:
            raise ValueError('Reference dimensions must be between 1 and 10000 in Settings.')
        return width, height

    def action_value(self):
        width, height = self.reference_dimensions()
        self.picker.localizer.bind(self.inputs.reference_hint, 'render', lambda label: label.set_text(
            self.picker.tr('Reference: {width} × {height}').format(width=width, height=height)))
        step = self.inputs.value()
        action, value = parse_step(step)
        if action in ('click', 'move') and (value[0] > width or value[1] > height):
            raise ValueError('Click coordinates must fit the reference resolution in Settings.')
        return step

    def action_changed(self, apply=True):
        if self.syncing_action:
            return
        self.syncing_action = True
        try:
            try:
                value = self.action_value()
            except ValueError as exc:
                self.action_error.set_text(str(exc))
                self.action_error.show()
                self.action_valid = False
            else:
                self.action_valid = True
                self.action_error.hide()
                if apply and self.editing_step and self.editing_step.valid():
                    self.steps[self.editing_step.get_path()][0] = value
            self.add_action_button.set_sensitive(self.action_valid)
            self.save_button.set_sensitive(self.action_valid)
            if not apply:
                if not self.action_valid:
                    self.say(self.action_error.get_text(), error=True)
                    self.reference_notice = True
                elif self.reference_notice:
                    self.say('')
                    self.reference_notice = False
            self.update_step_actions()
        finally:
            self.syncing_action = False

    def select_action(self, selection):
        if self.selecting_step:
            return
        model, iterator = selection.get_selected()
        path = model.get_path(iterator) if iterator is not None else None
        if not self.action_valid and self.editing_step and self.editing_step.valid():
            self.selecting_step = True
            try:
                selection.select_path(self.editing_step.get_path())
            finally:
                self.selecting_step = False
            return
        self.editing_step = self.Gtk.TreeRowReference.new(model, path) if path is not None else None
        self.inputs.load(model[iterator][0] if iterator is not None else 'wait:500')
        self.picker.localizer.bind(self.action_title, 'render', lambda label, number=path.get_indices()[0] + 1 if path is not None else None:
            label.set_text(self.picker.tr('Edit action {number}').format(number=number) if number is not None else self.picker.tr('New action')))
        self.action_title.set_tooltip_text('Changes update this draft action. Save the macro to keep them.' if path is not None else 'Choose an action and use + to append it.')
        self.new_action_button.set_visible(path is not None)
        # Loading a form validates it without rewriting imported action syntax.
        self.syncing_action = True
        try:
            self.action_value()
            self.action_error.hide()
            self.action_valid = True
        except ValueError as exc:
            self.action_error.set_text(str(exc))
            self.action_error.show()
            self.action_valid = False
        finally:
            self.syncing_action = False
        self.add_action_button.set_sensitive(self.action_valid)
        self.save_button.set_sensitive(self.action_valid)
        self.update_step_actions()

    def new_action(self):
        self.editing_step = None
        self.action_valid = True
        self.steps_view.get_selection().unselect_all()
        self.select_action(self.steps_view.get_selection())

    def add_step(self):
        iterator = self.steps.append([self.action_value()])
        self.action_valid = True
        self.steps_view.get_selection().select_iter(iterator)
        self.steps_view.scroll_to_cell(self.steps.get_path(iterator))

    def capture_point(self):
        import re
        self.reference_dimensions()
        if not self.fields['title_re'].get_text().strip() or not self.fields['game'].get_text().strip():
            raise ValueError('Set the game and title pattern in Settings before capturing a point.')
        try:
            re.compile(self.fields['title_re'].get_text().replace('(?i)', ''), re.I)
        except re.error:
            raise ValueError('Fix the game title pattern before capturing a point.') from None
        self.picker.pending_capture = self
        self.picker.pending_record = None
        self.picker.playback.update(armed=False)
        self.picker.publish_playback()
        self.picker.pending = None
        self.picker.hide()
        self.hide()
        self.picker.message = 'Move your pointer to the desired position in the matching game and press F9. No click will be sent.'

    def capture_from(self, window):
        import re
        if not login.GFN_APP_CLASSES.search(window.window_class) or not re.search(self.fields['title_re'].get_text().replace('(?i)', ''), window.title, re.I):
            return False
        width, height = self.reference_dimensions()
        scale = min(window.width / width, window.height / height)
        if scale <= 0:
            return False
        profile = login.Profile('', '', '', '', width, height, [])
        origin_x, origin_y = login.map_point(window, profile, 0, 0)
        cursor_x, cursor_y = login.cursor_position()
        x, y = round((cursor_x - origin_x) / scale), round((cursor_y - origin_y) / scale)
        if not (0 <= x <= width and 0 <= y <= height):
            return False
        self.steps.append([f'click:{x},{y}'])
        self.say(f'Captured click position {x},{y}. Save the macro to keep it.')
        return True

    def move_step(self, direction):
        model, iterator = self.steps_view.get_selection().get_selected()
        if iterator is not None:
            index = model.get_path(iterator).get_indices()[0]
            target = index + direction
            if 0 <= target < len(model):
                model.swap(iterator, model.get_iter(str(target)))
        self.update_step_actions()

    def remove_step(self):
        model, iterator = self.steps_view.get_selection().get_selected()
        if iterator is not None:
            self.editing_step = None
            self.action_valid = True
            model.remove(iterator)
            self.select_action(self.steps_view.get_selection())
        self.update_step_actions()

    def update_step_actions(self):
        model, iterator = self.steps_view.get_selection().get_selected()
        index = model.get_path(iterator).get_indices()[0] if iterator is not None else -1
        for button, enabled in zip(self.step_buttons, (index > 0, 0 <= index < len(model) - 1, index >= 0)):
            button.set_no_show_all(True)
            button.set_visible(index >= 0)
            button.set_sensitive(enabled and (self.action_valid or button is self.step_buttons[-1]))
        if self.editing_step and self.editing_step.valid():
            self.picker.localizer.bind(self.action_title, 'render', lambda label, number=self.editing_step.get_path().get_indices()[0] + 1:
                label.set_text(self.picker.tr('Edit action {number}').format(number=number)))

    def save_draft(self):
        if not self.draft:
            self.say('Add or edit a macro first.')
            return
        if self.action_stack.get_visible_child_name() == 'record':
            raise ValueError('Return to actions to review the macro before saving.')
        if not self.action_valid:
            raise ValueError(self.action_error.get_text())
        macro = copy.deepcopy(self.draft)
        for key, entry in self.fields.items():
            if key != 'password':
                if isinstance(entry, self.Gtk.SpinButton):
                    try:
                        macro[key] = int(entry.get_text())
                    except ValueError:
                        raise ValueError(f'Enter a whole number for {entry.get_accessible().get_name()}.') from None
                else:
                    macro[key] = entry.get_text().strip()
        macro.update(kind=self.kind.get_active_id(), auto=self.auto.get_active() and self.kind.get_active_id() == 'login', enabled=self.enabled.get_active(),
                     steps=[row[0] for row in self.steps], source_profile='')
        if macro['kind'] == 'normal':
            macro['login_limit'] = 0
        for key, title in (('name', 'a macro name'), ('game', 'a game'), ('title_re', 'a game window title')):
            if not macro[key]:
                raise ValueError(f'Choose or enter {title} in Settings.')
        validate(macro)
        if (macro['kind'] == 'login' or 'id' in macro['steps']) and not macro['user']:
            raise ValueError('Enter an account in Settings → Account and password.')
        password = self.fields.get('password')
        if password and password.get_text():
            # Never overwrite a shared legacy/imported/duplicated password entry.
            macro['credential_key'] = store_login_secret('password', password.get_text())
        if macro['user']:
            if not macro.get('account_key') or macro['user'] != self.draft['user']:
                macro['account_key'] = store_login_secret('account', macro['user'])
            macro['user'] = ''
        self.library.put(macro)
        self.record_undo = None
        self.discard_record_button.hide()
        if password:
            password.set_text('')
        # Keep the in-memory draft useful if Save is pressed a second time.
        account = self.fields['user'].get_text().strip()
        self.draft = copy.deepcopy(macro)
        self.draft['user'] = account
        self.picker.scan()
        self.picker.render_if_visible()
        self.refresh()
        self.show_library()
        self.say('Macro saved. Hide removes it from the picker and first-F9 auto login.')

    def choose_file(self, exporting):
        Gtk = self.Gtk
        if not hasattr(self, 'files_page'):
            self.files_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.views.add_named(self.files_page, 'files')
        for child in self.files_page.get_children():
            child.destroy()
        self.files_page.pack_start(self.picker.label('Export library' if exporting else 'Import library', 'game'), False, False, 0)
        self.files_page.pack_start(self.picker.label('File path', 'muted'), False, False, 0)
        path_entry = Gtk.Entry()
        path_entry.set_text(str(Path.home() / 'macro-library.json'))
        self.files_page.pack_start(path_entry, False, False, 0)
        overwrite = Gtk.CheckButton(label='Overwrite an existing export file')
        if exporting:
            self.files_page.pack_start(overwrite, False, False, 0)
        def transfer():
            path = Path(path_entry.get_text()).expanduser()
            if not path.is_absolute():
                raise ValueError('Enter an absolute file path or a path beginning with ~/.')
            if exporting:
                if path.exists() and not overwrite.get_active():
                    raise ValueError('File exists. Choose another path or enable overwrite.')
                self.library.export_file(path)
                self.say('Exported actions without account names. Passwords remain in the keyring.')
            else:
                count = self.library.import_file(path)
                self.say(f'Imported {count} macros with auto login turned off.')
                self.refresh()
                self.picker.scan()
                self.picker.render_if_visible()
        transfer_button = self.picker.button('Export' if exporting else 'Import', lambda: self.safe(transfer), primary=True)
        transfer_button.set_halign(Gtk.Align.START)
        controls = Gtk.Box(spacing=4)
        controls.pack_start(transfer_button, False, False, 0)
        controls.pack_start(self.picker.help_button('Import / export information',
            'Exports include actions and credential references. Accounts and passwords stay in your system keyring.'), False, False, 0)
        self.files_page.pack_start(controls, False, False, 0)
        path_entry.get_accessible().set_name('File path')
        path_entry.connect('activate', lambda *_: self.safe(transfer))
        self.files_page.show_all()
        self.picker.prepare_ui(self.files_page)
        self.views.set_visible_child_name('files')

    def build_history(self):
        Gtk = self.Gtk
        page = self.page('History')
        self.history_rows = Gtk.ListStore(str, str, str, str)
        view = Gtk.TreeView(model=self.history_rows)
        view.set_headers_visible(False)
        view.set_tooltip_column(1)
        renderer = Gtk.CellRendererText()
        renderer.set_property('ellipsize', 3)
        renderer.set_property('ypad', 10)
        renderer.set_property('xpad', 10)
        def render(column, cell, model, iterator, *_):
            escape = self.picker.GLib.markup_escape_text
            when, name, result, duration = list(model[iterator])
            color = TOKENS['danger'] if result == 'Failed' else TOKENS['success'] if result == 'Completed' else TOKENS['muted']
            cell.set_property('markup', f'<b>{escape(name)}</b>\n<span size="{TOKENS["meta"] * 768}" foreground="{TOKENS["muted"]}">{escape(when)} · <span foreground="{color}">{escape(self.picker.tr(result))}</span> · {escape(duration)}</span>')
        column = Gtk.TreeViewColumn()
        column.pack_start(renderer, True)
        column.set_cell_data_func(renderer, render)
        column.set_expand(True)
        view.append_column(column)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(view)
        overlay = Gtk.Overlay()
        overlay.add(scroll)
        self.history_empty = self.picker.label('No runs yet. Results will appear here.', 'muted')
        self.history_empty.set_halign(Gtk.Align.CENTER)
        self.history_empty.set_valign(Gtk.Align.CENTER)
        self.history_empty.set_no_show_all(True)
        overlay.add_overlay(self.history_empty)
        page.pack_start(overlay, True, True, 0)
        help_button = self.picker.help_button('History information', 'Last 100 runs. Accounts and passwords are never logged.')
        help_button.set_halign(Gtk.Align.END)
        page.pack_start(help_button, False, False, 0)

    def refresh_history(self):
        if not hasattr(self, 'history_rows'):
            return
        import time
        self.history_rows.clear()
        self.history_empty.set_visible(not self.library.history)
        for record in self.library.history:
            self.history_rows.append([time.strftime('%m-%d %H:%M', time.localtime(record['at'])), record['name'],
                                      record['status'], f'{record["duration"]:.1f}s'])
