"""Isolated visual workflow persistence and sequential guarded playback."""
import copy
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

from macro_library import MacroLibrary, new_macro, key_codes
from macro_engine import MacroRunner, Cancelled
from visual_images import recognize, MAX_PIXELS


def new_workflow(pattern, name='New workflow'):
    return dict(id=uuid.uuid4().hex, name=name, game=pattern, automatic=False,
                ref_width=1920, ref_height=1080, steps=[])


def number(value, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid numeric value or out-of-range workflow field.')
    return value


def validate(workflow):
    if not isinstance(workflow, dict):
        raise ValueError('Expected a workflow object.')
    for field, limit in [('id', 128), ('name', 100), ('game', 300)]:
        value = workflow.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f'Invalid workflow {field}.')
    if not re.fullmatch(r'[\w-]+', workflow['id']):
        raise ValueError('Invalid workflow ID.')
    try:
        re.compile(workflow['game'].replace('(?i)', ''), re.I)
    except re.error as exc:
        raise ValueError('Invalid game pattern.') from exc
    if not isinstance(workflow.get('automatic'), bool):
        raise ValueError('Automatic must be boolean.')
    width = number(workflow.get('ref_width'), 1, 10000, True)
    height = number(workflow.get('ref_height'), 1, 10000, True)
    if width*height > MAX_PIXELS:
        raise ValueError('Reference geometry is too large.')
    steps = workflow.get('steps')
    if not isinstance(steps, list) or len(steps) > 1000:
        raise ValueError('Expected up to 1000 steps.')
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get('name'), str) or len(step['name']) > 100:
            raise ValueError('Every step needs a name.')
        if step.get('condition') not in ('always', 'image', 'delay') or step.get('failure', 'stop') not in ('stop', 'skip'):
            raise ValueError('Invalid step condition or failure policy.')
        image = step.get('image', '')
        if not isinstance(image, str) or (image and (Path(image).name != image or not re.fullmatch(r'[\w.-]+', image) or image in ('.', '..'))):
            raise ValueError('Invalid image filename.')
        if step['condition'] == 'image' and not image:
            raise ValueError('Image condition needs an image.')
        number(step.get('threshold', .9), 0, 1)
        number(step.get('confirm', 2), 1, 1000, True)
        number(step.get('timeout', 30), 0, 86400)
        number(step.get('delay', 0), 0, 600)
        region = step.get('region')
        if region is not None:
            if not isinstance(region, list) or len(region) != 4:
                raise ValueError('Region requires x, y, width, height.')
            x, y, w, h = [number(v, 0, 10000, True) for v in region]
            if not w or not h or x+w > width or y+h > height:
                raise ValueError('Region exceeds reference geometry.')
        actions = step.get('actions')
        if not isinstance(actions, list) or len(actions) > 1000:
            raise ValueError('Expected an action list.')
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError('Invalid action.')
            kind = action.get('type')
            if kind == 'click':
                number(action.get('x', 0), 0, width)
                number(action.get('y', 0), 0, height)
                if not isinstance(action.get('detected', False), bool):
                    raise ValueError('Detected must be boolean.')
                offset = action.get('offset', [0, 0])
                if not isinstance(offset, list) or len(offset) != 2:
                    raise ValueError('Offset requires two coordinates.')
                for v in offset:
                    number(v, -10000, 10000)
            elif kind == 'key':
                if not isinstance(action.get('key'), str):
                    raise ValueError('Key must be text.')
                key_codes(action['key'])
            elif kind == 'wait':
                number(action.get('seconds'), 0, 600)
            elif kind == 'macro':
                if not isinstance(action.get('macro_id'), str) or not re.fullmatch(r'[\w-]{1,128}', action['macro_id']) or not isinstance(action.get('full', False), bool):
                    raise ValueError('Invalid macro reference.')
            else:
                raise ValueError('Unknown workflow action.')
    return workflow


class WorkflowStore:
    def __init__(self, library):
        self.path = Path(library.path).with_name('workflows.json')
        self.images_dir = self.path.parent / 'visual-images'
        self.data = json.loads(self.path.read_text()) if self.path.exists() else dict(version=1, workflows=[])
        if not isinstance(self.data, dict) or self.data.get('version') != 1 or not isinstance(self.data.get('workflows'), list):
            raise ValueError('Unsupported workflow file; original preserved.')
        seen = set()
        for workflow in self.workflows:
            validate(workflow)
            if workflow['id'] in seen:
                raise ValueError('Duplicate workflow ID.')
            seen.add(workflow['id'])

    @property
    def workflows(self):
        return self.data['workflows']

    def get(self, identity):
        return next((w for w in self.workflows if w['id'] == identity), None)

    def put(self, workflow):
        workflow = copy.deepcopy(validate(workflow))
        data = copy.deepcopy(self.data)
        data['workflows'] = [w for w in data['workflows'] if w['id'] != workflow['id']]
        if workflow['automatic']:
            for other in data['workflows']:
                if other['game'] == workflow['game']:
                    other['automatic'] = False
        data['workflows'].append(workflow)
        MacroLibrary.atomic_write(self.path, data)
        self.data = data
        return workflow

    def delete(self, identity):
        data = copy.deepcopy(self.data)
        data['workflows'] = [w for w in self.workflows if w['id'] != identity]
        MacroLibrary.atomic_write(self.path, data)
        self.data = data

    def automatic(self, pattern):
        return next((w for w in self.workflows if w['game'] == pattern and w['automatic']), None)

    def image_path(self, filename):
        if not isinstance(filename, str) or not re.fullmatch(r'[\w.-]+', filename) or filename in ('.', '..'):
            raise ValueError('Invalid image filename.')
        path = self.images_dir / filename
        if path.resolve().parent != self.images_dir.resolve():
            raise ValueError('Image path escapes asset directory.')
        return path

    def import_image(self, path):
        from PIL import Image
        with Image.open(path) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ValueError('Template is too large.')
            image = image.convert('RGB')
        self.images_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        filename = uuid.uuid4().hex + '.png'
        target = self.image_path(filename)
        with open(target, 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            image.save(stream, format='PNG')
        return filename


class WorkflowRunner:
    def __init__(self, store, library, verify, profiles, settings, progress=lambda *_: None, recognizer=None):
        self.store, self.library, self.verify = store, library, verify
        self.profiles, self.settings, self.progress = profiles, settings, progress
        self.recognizer = recognizer or recognize
        self.cancel, self.paused = threading.Event(), threading.Event()
        self.completed = 0
        self._pause_time = 0
        self._running = threading.Lock()

    def check(self):
        if self.cancel.is_set():
            raise Cancelled('Workflow cancelled.')
        start = time.monotonic()
        while self.paused.is_set():
            if self.cancel.wait(.05):
                raise Cancelled('Workflow cancelled.')
        self._pause_time += time.monotonic() - start
        return self.verify()

    def clock(self):
        return time.monotonic() - self._pause_time

    def wait(self, seconds):
        deadline = self.clock() + seconds
        while True:
            self.check()
            remaining = deadline - self.clock()
            if remaining <= 0:
                return
            if self.cancel.wait(min(.05, remaining)):
                raise Cancelled('Workflow cancelled.')

    def _macro(self, macro, full=False, record=False):
        runner = MacroRunner(self.library, self.check)
        runner.cancel = self.cancel
        # Existing playback/input implementation, but all waits use our pause-aware clock.
        runner.wait = lambda ms: self.wait(ms / 1000)
        start, status = time.monotonic(), 'Completed'
        try:
            runner.run(macro, self.profiles, self.settings, full=full)
        except Exception as exc:
            status = 'Cancelled' if isinstance(exc, Cancelled) else 'Failed'
            raise
        finally:
            if record:
                self.library.record(macro['id'], macro['name'], status, time.monotonic()-start, runner.completed)

    def run(self, workflow):
        if not self._running.acquire(blocking=False):
            raise RuntimeError('Workflow is already running.')
        try:
            workflow = copy.deepcopy(validate(workflow))
            self.completed = 0
            self.progress('running', 0, workflow['name'])
            for index, step in enumerate(workflow['steps']):
                window = self.check()
                if not re.search(workflow['game'].replace('(?i)', ''), window.title, re.I):
                    raise ValueError('Active game does not match workflow.')
                self.progress('waiting', index, step['name'])
                detected = None
                if step['condition'] == 'delay':
                    self.wait(step.get('delay', 0))
                elif step['condition'] == 'image':
                    start, stable, previous = self.clock(), 0, None
                    while True:
                        window = self.check()
                        recognition_started = time.monotonic()
                        detected = self.recognizer(window, step, self.store, workflow)
                        recognition_elapsed = time.monotonic() - recognition_started
                        self.check()
                        if step.get('timeout', 30) and self.clock()-start >= step.get('timeout', 30):
                            if step.get('failure', 'stop') == 'skip':
                                stable = 0
                                self.progress('skipped', index, 'Image condition timed out.')
                                break
                            raise TimeoutError('Image condition timed out: ' + step['name'])
                        if detected:
                            point = (detected['x'], detected['y'])
                            stable = stable+1 if previous and math.dist(previous, point) <= 3 else 1
                            previous = point
                        else:
                            stable, previous = 0, None
                        if stable >= step.get('confirm', 2):
                            break
                        interval = max(.5, 3 * recognition_elapsed)
                        if step.get('timeout', 30):
                            interval = min(interval, max(0, step.get('timeout', 30) - (self.clock()-start)))
                        self.wait(interval)
                    if stable < step.get('confirm', 2):
                        continue
                for action in step['actions']:
                    window = self.check()
                    if action['type'] == 'macro':
                        macro = self.library.get(action['macro_id'])
                        if not macro or macro['title_re'] != workflow['game'] or macro not in self.library.matching(window.title) or macro.get('hidden') or not macro.get('enabled', True):
                            raise ValueError('Referenced macro is missing, disabled, hidden, or belongs to another game.')
                        self.progress('waiting-macro', index, macro['name'])
                        self._macro(copy.deepcopy(macro), action.get('full', False), True)
                        continue
                    self.progress('executing', index, action['type'])
                    macro = new_macro()
                    macro.update(game=workflow['name'], title_re=workflow['game'], ref_width=workflow['ref_width'], ref_height=workflow['ref_height'], step_delay=0)
                    if action['type'] == 'wait':
                        value = 'wait:' + str(round(action['seconds']*1000))
                    elif action['type'] == 'key':
                        value = 'key:' + action['key']
                    else:
                        x, y = action.get('x', 0), action.get('y', 0)
                        if action.get('detected'):
                            if detected is None:
                                raise ValueError('Detected click requires a successful image condition.')
                            x, y = detected['x'], detected['y']
                        dx, dy = action.get('offset', [0, 0])
                        x, y = round(x+dx), round(y+dy)
                        number(x, 0, workflow['ref_width'])
                        number(y, 0, workflow['ref_height'])
                        value = f'click:{x},{y}'
                    macro['steps'] = [value]
                    self._macro(macro)
                self.completed += 1
                self.progress('completed-step', index, step['name'])
            self.progress('completed', self.completed, workflow['name'])
        except Exception as exc:
            self.progress('cancelled' if isinstance(exc, Cancelled) else 'failed', self.completed, str(exc))
            raise
        finally:
            self._running.release()
