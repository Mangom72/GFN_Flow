"""Live translation of UI chrome only; user text and macro values stay intact."""
import json
from pathlib import Path

KO = json.loads(Path(__file__).with_name('ui_ko.json').read_text(encoding='utf-8'))


class Localizer:
    def __init__(self, language='en'):
        self.language = language
        # GTK can retain a C widget after its Python wrapper loses references.
        # Keep translated wrappers alive until their explicit destroy signal.
        self.bindings = {}
        self.drawings = set()

    def text(self, source):
        if self.language != 'ko':
            return source
        return KO.get(source, '\n'.join(KO.get(line, line) for line in source.split('\n')))

    def bind(self, widget, prop, source):
        self.track(widget)
        self.bindings[widget][prop] = source
        self.apply(widget, prop, source)

    def track(self, widget):
        self.bindings.setdefault(widget, {})
        if not getattr(widget, '_localization_cleanup', False):
            widget._localization_cleanup = True
            widget.connect('destroy', self.release)

    def release(self, widget):
        # GTK can emit destroy while cyclic garbage collection clears us.
        if bindings := getattr(self, 'bindings', None):
            bindings.pop(widget, None)
        if drawings := getattr(self, 'drawings', None):
            drawings.discard(widget)

    def apply(self, widget, prop, source):
        if prop == 'render':
            source(widget)
            return
        value = self.text(source)
        if prop == 'accessible-name':
            widget.get_accessible().set_name(value)
        elif prop == 'accessible-description':
            widget.get_accessible().set_description(value)
        else:
            widget.set_property(prop, value)

    def set_language(self, language):
        self.language = language
        for widget, properties in list(self.bindings.items()):
            for prop, source in properties.items():
                self.apply(widget, prop, source)
        for widget in self.drawings:
            widget.queue_draw()
            widget.queue_resize()

    def scan(self, widget, Gtk):
        if getattr(widget, '_user_content', False):
            return
        properties = ['tooltip-text']
        if isinstance(widget, Gtk.Label) and not widget.get_use_markup():
            properties.append('label')
        if isinstance(widget, Gtk.Entry):
            properties.append('placeholder-text')
        for prop in properties:
            value = widget.get_property(prop)
            if isinstance(value, str) and (value in KO or any(line in KO for line in value.split('\n'))):
                self.bind(widget, prop, value)
        accessible = widget.get_accessible().get_name()
        if accessible in KO:
            self.bind(widget, 'accessible-name', accessible)
        if (isinstance(widget, Gtk.ComboBoxText) and not getattr(widget, '_localized_cells', False)
                and (not getattr(widget, '_user_options', False) or getattr(widget, '_fixed_options', None))):
            widget._localized_cells = True
            fixed = getattr(widget, '_fixed_options', None)
            id_column = widget.get_id_column()
            def render(_, renderer, model, iterator, data=None):
                text = model[iterator][0]
                translate = fixed is None or model[iterator][id_column] in fixed
                renderer.set_property('text', self.text(text) if translate else text)
            for cell in widget.get_cells():
                if isinstance(cell, Gtk.CellRendererText):
                    widget.set_cell_data_func(cell, render)
            self.drawings.add(widget)
            self.track(widget)
        if isinstance(widget, Gtk.TreeView):
            self.drawings.add(widget)
            self.track(widget)
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                self.scan(child, Gtk)
        if isinstance(widget, Gtk.Notebook):
            for index in range(widget.get_n_pages()):
                tab = widget.get_tab_label(widget.get_nth_page(index))
                if tab:
                    self.scan(tab, Gtk)

