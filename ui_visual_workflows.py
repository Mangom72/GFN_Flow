"""Draft-based GTK workflow editing. Image workers never send desktop input."""
import copy
import tempfile
import threading
import time
import uuid
from pathlib import Path

from detection import session_key


class VisualWorkflowsPage:
    def __init__(self, manager):
        from gi.repository import GLib
        self.manager, self.picker, self.Gtk = manager, manager.picker, manager.Gtk
        self.GLib = GLib
        self.draft = None
        self.step_index = self.action_index = 0
        self.loading = self.busy = False
        self.alive = True
        self.image = None
        self.page = manager.page('Visual Workflows')
        self.selector = self.Gtk.ComboBoxText()
        self.compact_combo(self.selector)
        self.page.pack_start(self.selector, False, False, 0)
        self.selector.connect('changed', self.select_workflow)
        self.edit_buttons = self.buttons(self.page, [('New workflow', self.new), ('Duplicate', self.duplicate),
                                                     ('Delete', self.delete)])
        self.scroll = self.Gtk.ScrolledWindow()
        self.scroll.set_policy(self.Gtk.PolicyType.NEVER, self.Gtk.PolicyType.AUTOMATIC)
        self.form = self.box()
        self.scroll.add(self.form)
        self.page.pack_start(self.scroll, True, True, 0)
        self.status = self.label(self.page, '', 'status')
        self.status.set_no_show_all(True)
        self.playback_buttons = self.buttons(self.page, [('Save', self.save), ('Start', self.start), ('Pause', self.pause),
                                                         ('Resume', self.resume), ('Stop', self.stop)])
        self.page.connect('destroy', self.destroy)
        self.timer = GLib.timeout_add(300, self.update_status)
        self.refresh()
        self.update_status()

    @property
    def adapter(self):
        return getattr(self.picker, 'visual', None)

    @property
    def store(self):
        return self.adapter.store if self.adapter else None

    def box(self):
        return self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=8)

    @staticmethod
    def compact_combo(widget):
        for cell in widget.get_cells():
            if cell.find_property('ellipsize'):
                cell.set_property('ellipsize', 3)
                cell.set_property('max-width-chars', 24)

    def buttons(self, parent, pairs):
        flow = self.Gtk.FlowBox()
        flow.set_selection_mode(self.Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(6)
        flow.set_min_children_per_line(1)
        buttons = {}
        for title, callback in pairs:
            buttons[title] = button = self.picker.button(title, lambda cb=callback: self.manager.safe(cb))
            flow.add(button)
        parent.pack_start(flow, False, False, 0)
        return buttons

    def label(self, parent, title, style='field-label'):
        label = self.picker.label(title, style)
        label.set_line_wrap(True)
        label.set_max_width_chars(26)
        parent.pack_start(label, False, False, 0)
        return label

    def field(self, parent, title, data, key, default='', bounds=None, choices=None):
        self.label(parent, title)
        value = data.get(key, default)
        if choices is not None:
            widget = self.Gtk.ComboBoxText()
            self.compact_combo(widget)
            for identity, name in choices:
                widget.append(str(identity), name)
            widget.set_active_id(str(value))
            widget.connect('changed', lambda w: data.__setitem__(key, w.get_active_id()))
        elif isinstance(value, bool):
            widget = self.Gtk.CheckButton(label=title)
            widget.set_active(value)
            widget.get_child().set_line_wrap(True)
            widget.get_child().set_max_width_chars(24)
            widget.connect('toggled', lambda w: data.__setitem__(key, w.get_active()))
        elif bounds:
            low, high, increment = bounds
            widget = self.Gtk.SpinButton.new_with_range(low, high, increment)
            widget.set_digits(0 if increment == 1 else 2)
            widget.set_value(value)
            widget.set_numeric(True)
            widget.set_width_chars(7)
            widget.connect('value-changed', lambda w: data.__setitem__(key, w.get_value_as_int() if increment == 1 else w.get_value()))
        else:
            widget = self.Gtk.Entry()
            widget.set_width_chars(10)
            widget.set_text(str(value))
            widget.connect('changed', lambda w: data.__setitem__(key, w.get_text()))
        widget.get_accessible().set_name(title)
        parent.pack_start(widget, False, False, 0)
        return widget

    def options(self):
        return self.picker.library.game_options(self.picker.legacy_profiles)

    def refresh(self):
        selected = self.selector.get_active_id()
        self.loading = True
        self.selector.remove_all()
        if self.store:
            for workflow in self.store.workflows:
                self.selector.append(workflow['id'], workflow['name'])
        self.selector.set_active_id(selected)
        self.selector.set_sensitive(self.selector.get_model().iter_n_children(None) > 0)
        self.loading = False
        if not self.draft:
            self.render()

    def select_workflow(self, *_):
        if self.loading or not self.store:
            return
        identity = self.selector.get_active_id()
        workflow = self.store.get(identity) if identity else None
        self.draft = copy.deepcopy(workflow) if workflow else None
        self.step_index = self.action_index = 0
        self.image = None
        self.render()

    def new(self):
        if not self.store:
            raise ValueError('Visual workflows are unavailable.')
        options = self.options()
        if not options:
            raise ValueError('Add a game in the macro library first.')
        from visual_workflows import new_workflow
        self.draft = new_workflow(options[0][1])
        self.step_index = self.action_index = 0
        self.image = None
        self.render()

    def duplicate(self):
        if self.draft:
            self.draft = copy.deepcopy(self.draft)
            self.draft.update(id=str(uuid.uuid4()), name=self.draft['name'] + ' copy', automatic=False)
            self.render()

    def delete(self):
        if self.draft and self.store:
            if self.store.get(self.draft['id']):
                self.store.delete(self.draft['id'])
            self.draft = None
            self.refresh()

    def save(self):
        if not self.draft or not self.store:
            return
        if self.draft['game'] not in [pattern for _, pattern in self.options()]:
            raise ValueError('Choose an existing game from the library.')
        self.store.put(copy.deepcopy(self.draft))
        self.refresh()
        self.loading = True
        self.selector.set_active_id(self.draft['id'])
        self.loading = False
        self.manager.say('Workflow saved.')

    def render(self):
        self.update_status()
        for child in self.form.get_children():
            child.destroy()
        if not self.draft:
            error = getattr(self.adapter, 'error', '')
            self.label(self.form, error or 'Choose a workflow or create a new one.',
                       'input-error' if error else 'muted')
            self.form.show_all()
            return
        d = self.draft
        self.label(self.form, 'Workflow settings', 'section')
        self.field(self.form, 'Name', d, 'name')
        game = self.field(self.form, 'Game', d, 'game', choices=[(pattern, name) for name, pattern in self.options()])
        game.connect('changed', lambda *_: self.render())
        self.field(self.form, 'Run automatically for this game', d, 'automatic', False)
        self.label(self.form, 'Saving automatic replaces the automatic workflow for this game. Changes are drafts until Save.', 'muted')
        self.label(self.form, 'Opening this panel pauses a running workflow. Resume hides the panel and continues only after checking the game. Stop ends the workflow.', 'muted')
        for key, title in [('ref_width', 'Reference width'), ('ref_height', 'Reference height')]:
            self.field(self.form, title, d, key, 1920 if key == 'ref_width' else 1080, (1, 10000, 1))
        self.label(self.form, 'Steps', 'section')
        self.step_picker = self.Gtk.ComboBoxText()
        self.compact_combo(self.step_picker)
        for i, step in enumerate(d['steps']):
            self.step_picker.append(str(i), f'{i + 1}. {step["name"]}')
        self.step_index = min(self.step_index, max(0, len(d['steps']) - 1))
        self.step_picker.set_active_id(str(self.step_index))
        self.step_picker.connect('changed', self.select_step)
        self.form.pack_start(self.step_picker, False, False, 0)
        self.buttons(self.form, [('Add step', self.add_step), ('Remove step', self.remove_step),
                                 ('Step up', lambda: self.move_step(-1)), ('Step down', lambda: self.move_step(1))])
        if d['steps']:
            self.render_step(self.step())
        self.form.show_all()
        self.picker.prepare_ui(self.form)

    def step(self):
        return self.draft['steps'][self.step_index] if self.draft and self.draft['steps'] else None

    def select_step(self, widget):
        if widget.get_active_id() is not None:
            self.step_index = int(widget.get_active_id())
            self.action_index = 0
            self.image = None
            self.render()

    def add_step(self):
        if self.draft:
            self.draft['steps'].append(dict(name='New step', condition='always', image='', region=None,
                threshold=.9, confirm=2, timeout=30, failure='stop', delay=0, actions=[]))
            self.step_index = len(self.draft['steps']) - 1
            self.action_index = 0
            self.image = None
            self.render()

    def remove_step(self):
        if self.step():
            self.draft['steps'].pop(self.step_index)
            self.image = None
            self.render()

    @staticmethod
    def move(items, index, direction):
        other = index + direction
        if 0 <= other < len(items):
            items[index], items[other] = items[other], items[index]
            return other
        return index

    def move_step(self, direction):
        if self.step():
            self.step_index = self.move(self.draft['steps'], self.step_index, direction)
            self.render()

    def rectangle(self, parent, title, values):
        self.label(parent, title, 'section')
        grid = self.Gtk.Grid(column_spacing=8, row_spacing=6)
        parent.pack_start(grid, False, False, 0)
        for i, name in enumerate(('X', 'Y', 'Width', 'Height')):
            group = self.box()
            grid.attach(group, i % 2, i // 2, 1, 1)
            group.set_hexpand(True)
            widget = self.field(group, name, {'value': values[i]}, 'value', bounds=(0 if i < 2 else 1, 10000, 1))
            widget.connect('value-changed', lambda w, i=i: values.__setitem__(i, w.get_value_as_int()))

    def render_step(self, step):
        self.field(self.form, 'Step name', step, 'name')
        self.field(self.form, 'Condition', step, 'condition', choices=[('always', 'Always'), ('image', 'Wait for image'), ('delay', 'Delay')])
        for title, key, default, bounds in [('Delay · seconds', 'delay', 0, (0, 600, .1)),
                ('Confidence · 0–1', 'threshold', .9, (.01, 1, .01)), ('Stable matches', 'confirm', 2, (1, 100, 1)),
                ('Timeout · seconds (0 = unlimited)', 'timeout', 30, (0, 86400, .1))]:
            self.field(self.form, title, step, key, default, bounds)
        self.field(self.form, 'On timeout', step, 'failure', choices=[('stop', 'Stop workflow'), ('skip', 'Skip step')])
        region = self.Gtk.CheckButton(label='Limit image search to a region')
        region.set_active(step.get('region') is not None)
        self.form.pack_start(region, False, False, 0)
        values = list(step.get('region') or [0, 0, self.draft['ref_width'], self.draft['ref_height']])
        region_box = self.box()
        self.form.pack_start(region_box, False, False, 0)
        self.rectangle(region_box, 'Search region · reference coordinates', values)
        if region.get_active():
            step['region'] = values
        def toggle(w):
            step['region'] = values if w.get_active() else None
            region_box.set_sensitive(w.get_active())
        region.connect('toggled', toggle)
        region_box.set_sensitive(region.get_active())
        self.label(self.form, 'Template image', 'section')
        asset_label = self.label(self.form, step.get('image') or 'No image selected', 'muted')
        asset_label.set_ellipsize(3)
        self.preview = self.Gtk.Image()
        self.form.pack_start(self.preview, False, False, 0)
        self.buttons(self.form, [('Import image', self.import_image), ('Capture game', self.capture), ('Test recognition', self.test)])
        self.label(self.form, 'Capture and recognition hide this panel for 3 seconds. Keep the matching recognized game focused. Neither sends input.', 'muted')
        self.label(self.form, 'Captured images use reference content coordinates, excluding centered letterboxing. Import templates at that same scale. Crop X/Y start at the top-left.', 'muted')
        self.crop = [0, 0, 100, 100]
        if self.image is not None:
            self.crop[2:] = self.image.size
            self.show_preview(self.image)
        elif step.get('image') and self.store:
            self.load_preview(step['image'])
        self.rectangle(self.form, 'Crop template · image pixels', self.crop)
        self.buttons(self.form, [('Apply crop', self.apply_crop)])
        self.label(self.form, 'Actions · run in order', 'section')
        actions = step['actions']
        self.action_index = min(self.action_index, max(0, len(actions) - 1))
        combo = self.Gtk.ComboBoxText()
        self.compact_combo(combo)
        for i, action in enumerate(actions):
            combo.append(str(i), f'{i + 1}. {action["type"]}')
        combo.set_active_id(str(self.action_index))
        combo.connect('changed', self.select_action)
        self.form.pack_start(combo, False, False, 0)
        self.add_type = self.Gtk.ComboBoxText()
        for kind in ('click', 'key', 'wait', 'macro'):
            self.add_type.append(kind, kind.capitalize())
        self.add_type.set_active(0)
        self.form.pack_start(self.add_type, False, False, 0)
        self.buttons(self.form, [('Add action', self.add_action), ('Remove action', self.remove_action),
            ('Action up', lambda: self.move_action(-1)), ('Action down', lambda: self.move_action(1))])
        if actions:
            self.render_action(actions[self.action_index])

    def select_action(self, widget):
        if widget.get_active_id() is not None:
            self.action_index = int(widget.get_active_id())
            self.render()

    def add_action(self):
        if self.step():
            kind = self.add_type.get_active_id()
            action = {'click': dict(type='click', x=0, y=0, detected=False, offset=[0, 0]),
                      'key': dict(type='key', key='Enter'), 'wait': dict(type='wait', seconds=1),
                      'macro': dict(type='macro', macro_id='', full=False)}[kind]
            self.step()['actions'].append(action)
            self.action_index = len(self.step()['actions']) - 1
            self.render()

    def remove_action(self):
        if self.step() and self.step()['actions']:
            self.step()['actions'].pop(self.action_index)
            self.render()

    def move_action(self, direction):
        if self.step():
            self.action_index = self.move(self.step()['actions'], self.action_index, direction)
            self.render()

    def render_action(self, action):
        kind = action['type']
        if kind == 'click':
            self.field(self.form, 'Click detected image center', action, 'detected', False)
            for key in ('x', 'y'):
                self.field(self.form, 'Click ' + key.upper(), action, key, 0, (0, 10000, 1))
            for i, title in enumerate(('Center offset X', 'Center offset Y')):
                control = self.field(self.form, title, {'value': action['offset'][i]}, 'value', bounds=(-10000, 10000, 1))
                control.connect('value-changed', lambda w, i=i: action['offset'].__setitem__(i, w.get_value_as_int()))
        elif kind == 'key':
            self.field(self.form, 'Key (for example Enter or Escape)', action, 'key', 'Enter')
        elif kind == 'wait':
            self.field(self.form, 'Wait · seconds', action, 'seconds', 1, (0, 600, .1))
        elif kind == 'macro':
            macros = [m for m in self.picker.library.macros if m['title_re'] == self.draft['game']
                      and m['kind'] in ('login', 'normal') and not m.get('hidden') and m.get('enabled', True)]
            self.field(self.form, 'Existing macro', action, 'macro_id', choices=[(m['id'], m['name'] + ' · ' + m['kind']) for m in macros])
            self.field(self.form, 'Full login (login macros only)', action, 'full', False)

    def recognized_game(self):
        games = getattr(getattr(self.picker, 'sessions', None), 'games', {})
        current = getattr(self.picker, 'game', None)
        matches = [game for game in games.values() if self.adapter.pattern(game) == self.draft['game']]
        if current and current[0].address in games and any(g[0].address == current[0].address for g in matches):
            return games[current[0].address]
        if len(matches) == 1:
            return matches[0]
        raise ValueError('Select a recognized matching game first.')

    def verified_window(self, game, pattern):
        """Reuse session identity and focus verification; never trust cached geometry."""
        def check_live():
            live = self.picker.sessions.games.get(game[0].address)
            if (not live or session_key(*live) != session_key(*game)
                    or self.adapter.pattern(live) != pattern):
                raise ValueError('The recognized game session changed. No image captured.')
        check_live()
        window = self.picker.verify_target(game)
        check_live()
        return window

    def release_for_image(self):
        self.manager.hide()
        try:
            self.adapter._release_focus()
        except Exception:
            self.manager.show(refresh=False)
            raise

    def worker(self, task, done, restore=False):
        if self.busy:
            raise ValueError('An image operation is already running.')
        self.busy = True
        def work():
            try:
                result, error = task(), None
            except Exception as exc:
                result, error = None, str(exc)
            def finish():
                self.busy = False
                if not self.alive:
                    return False
                if restore:
                    self.manager.show(refresh=False)
                if error:
                    self.manager.say(error, error=True)
                else:
                    self.manager.safe(lambda: done(result))
                return False
            self.GLib.idle_add(finish)
        threading.Thread(target=work, daemon=True).start()

    def import_image(self):
        if not self.step():
            return
        Gtk = self.Gtk
        dialog = Gtk.FileChooserDialog(title='Import template image', transient_for=self.manager.window,
            action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'Import', Gtk.ResponseType.OK)
        def response(widget, response):
            path = widget.get_filename()
            widget.destroy()
            if response == Gtk.ResponseType.OK and path:
                step = self.step()
                def imported(asset):
                    step['image'] = asset
                    self.image = None
                    self.render()
                self.worker(lambda: self.store.import_image(path), imported)
        dialog.connect('response', response)
        dialog.show_all()

    def capture(self):
        if not self.step():
            return
        if self.busy:
            raise ValueError('An image operation is already running.')
        game = self.recognized_game()
        reference = (self.draft['ref_width'], self.draft['ref_height'])
        self.manager.say('Capturing matching game in 3 seconds…')
        self.release_for_image()
        pattern = self.draft['game']
        def task():
            import visual_images
            time.sleep(3)
            window = self.verified_window(game, pattern)
            if reference[0] * reference[1] > visual_images.MAX_PIXELS:
                raise ValueError('Reference image is too large.')
            return visual_images.normalize(visual_images.capture(window), reference)
        step = self.step()
        def captured(image):
            if self.step() is step:
                self.image = image
                self.render()
            self.manager.say('Captured game. Set crop coordinates, then Apply crop to save the template.')
        self.worker(task, captured, restore=True)

    def show_preview(self, image):
        from gi.repository import GdkPixbuf
        preview = image.convert('RGB').copy()
        preview.thumbnail((max(120, min(480, self.manager.view_width - 64)), 220))
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(self.GLib.Bytes.new(preview.tobytes()),
            GdkPixbuf.Colorspace.RGB, False, 8, preview.width, preview.height, preview.width * 3)
        self.preview.set_from_pixbuf(pixbuf)

    def load_preview(self, asset):
        if self.busy:
            return
        def task():
            from PIL import Image
            from visual_images import MAX_PIXELS
            with Image.open(self.store.image_path(asset)) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError('Template is too large; original image preserved.')
                return image.copy()
        step = self.step()
        def done(image):
            if self.step() is step:
                self.image = image
                self.render()
        self.worker(task, done)

    def apply_crop(self):
        if self.image is None or not self.step():
            raise ValueError('Import or capture an image first.')
        x, y, width, height = self.crop
        if x + width > self.image.width or y + height > self.image.height:
            raise ValueError('Crop must fit inside the image.')
        image, step = self.image.copy(), self.step()
        def task():
            with tempfile.TemporaryDirectory(prefix='visual-crop-') as directory:
                path = Path(directory) / 'template.png'
                cropped = image.crop((x, y, x + width, y + height))
                cropped.save(path)
                return self.store.import_image(path), cropped
        def done(result):
            step['image'], image = result
            if self.step() is step:
                self.image = image
                self.render()
            self.manager.say('Cropped template ready. Save the workflow to keep this change.')
        self.worker(task, done)

    def test(self):
        if not self.step() or not self.step().get('image'):
            raise ValueError('Choose a template image first.')
        if self.busy:
            raise ValueError('An image operation is already running.')
        game = self.recognized_game()
        step, workflow = copy.deepcopy(self.step()), copy.deepcopy(self.draft)
        self.manager.say('Testing matching game in 3 seconds… No input will be sent.')
        self.release_for_image()
        def task():
            import visual_images
            time.sleep(3)
            window = self.verified_window(game, workflow['game'])
            return visual_images.recognize(window, step, self.store, workflow)
        def done(result):
            self.manager.say('No match. No input sent.' if result is None else
                f'Match at {result["x"]}, {result["y"]} · confidence {result["score"]:.3f}. No input sent.')
        self.worker(task, done, restore=True)

    def start(self):
        if self.draft and self.adapter:
            self.save()
            self.adapter.start(self.draft['id'], game=self.recognized_game())

    def pause(self):
        if self.adapter:
            self.adapter.pause()

    def resume(self):
        if self.adapter:
            self.adapter.resume()

    def stop(self):
        if self.adapter:
            self.adapter.stop()

    def update_status(self):
        if not self.alive:
            return False
        status = self.adapter.status if self.adapter else {}
        error = getattr(self.adapter, 'error', '')
        active = bool(self.adapter and self.adapter.active)
        editable = bool(self.draft and self.store and not error and not self.busy)
        paused = active and status.get('state') == 'paused'
        for button, enabled in ((self.edit_buttons['New workflow'], bool(self.store and not error)),
                                (self.edit_buttons['Duplicate'], editable), (self.edit_buttons['Delete'], editable),
                                (self.playback_buttons['Save'], editable),
                                (self.playback_buttons['Start'], editable and not active),
                                (self.playback_buttons['Pause'], active and not paused),
                                (self.playback_buttons['Resume'], paused), (self.playback_buttons['Stop'], active)):
            button.set_sensitive(enabled)
        parts = []
        for key in ('state', 'step', 'message'):
            value = status.get(key)
            if value in (None, '') or status.get('state') == 'idle':
                continue
            if key == 'step' and status.get('state') in (
                    'running', 'paused', 'waiting', 'waiting-macro', 'executing',
                    'completed-step', 'skipped') and isinstance(value, int):
                value = f'Step {value + 1}'
            parts.append(str(value))
        message = error or ' · '.join(parts)
        self.status.set_text(message)
        self.status.set_visible(bool(message))
        return True

    def destroy(self, *_):
        self.alive = False
        if self.timer:
            self.GLib.source_remove(self.timer)
            self.timer = None
