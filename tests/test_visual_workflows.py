import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from macro_engine import Cancelled
from macro_library import MacroLibrary, new_macro
from visual_workflows import WorkflowStore, WorkflowRunner, new_workflow, validate


def step(**kwargs):
    result = dict(name='step', condition='always', actions=[])
    result.update(kwargs)
    return result


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library = SimpleNamespace(path=Path(self.tmp.name)/'library.json')
        self.library.path.write_text('untouched')
        self.store = WorkflowStore(self.library)
        self.workflow = new_workflow('Game')

    def runner(self, recognizer=None):
        return WorkflowRunner(self.store, self.library, lambda: SimpleNamespace(title='Game'), [], {}, recognizer=recognizer)

    def test_persistence_isolation_and_automatic(self):
        self.workflow['automatic'] = True
        self.store.put(self.workflow)
        other = new_workflow('Game')
        other['automatic'] = True
        self.store.put(other)
        loaded = WorkflowStore(self.library)
        self.assertEqual(loaded.automatic('Game')['id'], other['id'])
        self.assertEqual(self.library.path.read_text(), 'untouched')
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        loaded.delete(other['id'])
        self.assertIsNone(loaded.get(other['id']))

    def test_asset_and_validation(self):
        source = Path(self.tmp.name)/'source.png'
        Image.new('RGB', (4, 4)).save(source)
        filename = self.store.import_image(source)
        self.assertTrue(self.store.image_path(filename).exists())
        with self.assertRaises(ValueError):
            self.store.image_path('../library.json')
        self.workflow['steps'] = [step(delay=float('nan'))]
        with self.assertRaises(ValueError):
            validate(self.workflow)

    def test_stability_order_and_macro_completion(self):
        samples = iter([None, dict(x=5, y=5), None, dict(x=5, y=5), dict(x=5, y=5)])
        self.workflow['steps'] = [step(condition='image', image='x.png', actions=[dict(type='key', key='Enter')]), step(actions=[dict(type='wait', seconds=.001)])]
        runner = self.runner(lambda *_: next(samples))
        runner.wait = lambda _: runner.check()
        calls = []
        with patch.object(runner, '_macro', side_effect=lambda macro: calls.append(macro['steps'][0])):
            runner.run(self.workflow)
        self.assertEqual(calls, ['key:Enter', 'wait:1'])
        self.assertEqual(runner.completed, 2)

    def test_timeout_skip_and_stop(self):
        self.workflow['steps'] = [step(condition='image', image='x.png', timeout=.01)]
        with self.assertRaises(TimeoutError):
            self.runner(lambda *_: None).run(self.workflow)
        self.workflow['steps'][0]['failure'] = 'skip'
        runner = self.runner(lambda *_: None)
        runner.run(self.workflow)
        self.assertEqual(runner.completed, 0)

    def test_pause_and_cancel(self):
        runner = self.runner()
        runner.paused.set()
        timer = threading.Timer(.12, runner.paused.clear)
        timer.start()
        start = time.monotonic()
        runner.wait(.08)
        timer.join()
        self.assertGreaterEqual(time.monotonic()-start, .19)
        runner.cancel.set()
        with self.assertRaises(Cancelled):
            runner.check()

    def test_missing_macro(self):
        self.library.get = lambda _: None
        self.workflow['steps'] = [step(actions=[dict(type='macro', macro_id='missing')])]
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.runner().run(self.workflow)

    def real_library(self):
        self.library.path.unlink()
        self.library = MacroLibrary(self.library.path)
        macro = new_macro()
        macro.update(name='Blocking', game='Game', title_re='Game', steps=['wait:100'], step_delay=0)
        self.library.put(macro)
        return macro

    def test_real_macro_blocks_later_image_and_cancels(self):
        macro = self.real_library()
        self.workflow['steps'] = [step(condition='image', image='one.png', confirm=1,
                                     actions=[dict(type='macro', macro_id=macro['id'])]),
                                  step(condition='image', image='two.png', confirm=1,
                                       actions=[dict(type='key', key='Ctrl+A')])]
        for cancel in (False, True):
            entered, release = threading.Event(), threading.Event()
            seen, errors = [], []
            runner = self.runner(lambda _, s, *__: seen.append(s['image']) or dict(x=2, y=2))
            def wait(_):
                entered.set()
                while not release.wait(.01):
                    runner.check()
                runner.check()
            runner.wait = wait
            def run():
                try:
                    runner.run(self.workflow)
                except Exception as exc:
                    errors.append(exc)
            with patch('macro_engine.login.run') as inputs:
                worker = threading.Thread(target=run)
                worker.start()
                self.assertTrue(entered.wait(2))
                self.assertEqual(seen, ['one.png'])
                if cancel:
                    runner.cancel.set()
                release.set()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                if cancel:
                    self.assertIsInstance(errors[0], Cancelled)
                    self.assertEqual(seen, ['one.png'])
                else:
                    self.assertEqual(errors, [])
                    self.assertEqual(seen, ['one.png', 'two.png'])
                    self.assertTrue(inputs.called)
            self.assertEqual(self.library.history[0]['status'], 'Cancelled' if cancel else 'Completed')

    def test_wrong_game_overlapping_pattern(self):
        macro = self.real_library()
        macro['title_re'] = 'Game|Other'
        self.library.put(macro)
        self.workflow['steps'] = [step(actions=[dict(type='macro', macro_id=macro['id'])])]
        with self.assertRaisesRegex(ValueError, 'another game'):
            self.runner().run(self.workflow)

    def test_real_clicks_and_chord(self):
        self.real_library()
        self.workflow['steps'] = [step(condition='image', image='a.png', confirm=1, actions=[
            dict(type='click', x=10, y=20, offset=[2, -3]),
            dict(type='click', detected=True, offset=[3, 4]),
            dict(type='key', key='Ctrl+A')])]
        runner = self.runner(lambda *_: dict(x=100, y=200))
        with patch('macro_engine.login.click') as click, patch('macro_engine.login.run') as inputs:
            runner.run(self.workflow)
        self.assertEqual([call.args[2:] for call in click.call_args_list], [(12, 17), (103, 204)])
        self.assertEqual(click.call_args.args[1].ref_width, 1920)
        self.assertIn('29:1', inputs.call_args.args[0])
        self.assertIn('30:1', inputs.call_args.args[0])

    def test_missing_image(self):
        self.workflow['steps'] = [step(condition='image', image='absent.png')]
        with self.assertRaises(FileNotFoundError):
            self.runner().run(self.workflow)

    def test_timeout_excludes_pause_and_no_duplicate_matches(self):
        runner = self.runner()
        samples = []
        def recognize(*_):
            samples.append(1)
            if len(samples) == 1:
                runner.paused.set()
                threading.Timer(.15, runner.paused.clear).start()
                return None
            return dict(x=10, y=20)
        runner.recognizer = recognize
        original_wait = runner.wait
        runner.wait = lambda _: original_wait(.005)
        self.workflow['steps'] = [step(condition='image', image='a.png', timeout=.1, confirm=2)]
        runner.run(self.workflow)
        self.assertEqual(len(samples), 3)
        self.assertEqual(runner.completed, 1)

    def test_matching_cadence_and_timeout_clamp(self):
        for timeout, expected in [(0, 6), (3, 1)]:
            now = [0.0]
            samples = [None, dict(x=1, y=1)]
            def recognize(*_):
                now[0] += 2
                return samples.pop(0)
            runner = self.runner(recognize)
            waits = []
            def wait(seconds):
                waits.append(seconds)
                now[0] += seconds
            runner.wait = wait
            self.workflow['steps'] = [step(condition='image', image='a.png', confirm=1, timeout=timeout)]
            with patch('visual_workflows.time.monotonic', side_effect=lambda: now[0]):
                if timeout:
                    with self.assertRaises(TimeoutError):
                        runner.run(self.workflow)
                else:
                    runner.run(self.workflow)
            self.assertEqual(waits, [expected])

    def test_legacy_pause_does_not_block_main_thread_window_query(self):
        from macro_engine import login
        macro = self.real_library()
        profile = login.Profile('Legacy', 'Game', '', '', 1920, 1080, ['wait:100'])
        macro.update(source_profile='Legacy')
        runner = self.runner()
        runner.profiles = [profile]
        entered, proceed = threading.Event(), threading.Event()
        errors = []
        window = SimpleNamespace(title='Game')
        def original():
            return window
        def execute_profile(*args, **kwargs):
            entered.set()
            if not proceed.wait(2):
                raise RuntimeError('Test synchronization timed out.')
            # Real legacy code queries active_window during playback. This
            # worker query must encounter the workflow pause/cancel guard.
            login.active_window()
        def playback():
            try:
                runner._macro(macro)
            except Exception as exc:
                errors.append(exc)
        with patch.object(login, 'active_window', original), patch.object(login, 'execute_profile', execute_profile), patch.object(login, 'run') as inputs:
            worker = threading.Thread(target=playback)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                runner.paused.set()
                proceed.set()
                # Safety timer prevents a regression from hanging the suite:
                # old global guard blocks this main-thread call until cancel.
                safety = threading.Timer(.5, runner.cancel.set)
                safety.start()
                start = time.monotonic()
                try:
                    self.assertIs(login.active_window(), window)
                    self.assertLess(time.monotonic()-start, .2)
                    self.assertTrue(worker.is_alive())
                finally:
                    runner.cancel.set()
                    safety.cancel()
            finally:
                proceed.set()
                runner.cancel.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], Cancelled)
            self.assertIs(login.active_window, original)
            inputs.assert_not_called()
