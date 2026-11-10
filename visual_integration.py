"""Picker composition boundary for session-scoped visual workflows."""
import json
import threading

from detection import login, session_key
from visual_workflows import WorkflowStore, WorkflowRunner


class DisabledWorkflowStore:
    """Read-only empty facade; never repair or overwrite invalid storage."""
    def __init__(self, error):
        self.error = error
        self.workflows = []

    def get(self, identity):
        return None

    def automatic(self, pattern):
        return None

    def _unavailable(self, *args, **kwargs):
        raise ValueError(self.error)

    put = delete = import_image = image_path = _unavailable


class VisualIntegration:
    def __init__(self, picker):
        self.picker = picker
        self.status = dict(state='idle', step=0, message='')
        self.error = ''
        try:
            self.store = WorkflowStore(picker.library)
        except (OSError, ValueError, TypeError) as exc:
            self.error = f'Workflow storage unavailable: {exc}. Original file preserved.'
            self.store = DisabledWorkflowStore(self.error)
            self.status.update(state='failed', message=self.error)
        self.runner = None
        self.worker = None
        self.game = None
        self.path = picker.library.path.with_name('workflow-sessions.json')
        try:
            claims = json.loads(self.path.read_text())
            if not isinstance(claims, list) or not all(isinstance(key, str) for key in claims):
                raise ValueError('Invalid workflow session claims.')
            self.claims = set(claims)
        except (OSError, ValueError, TypeError):
            self.claims = set()

    @property
    def active(self):
        return self.runner is not None

    def owns(self, game):
        return bool(self.active and game and self.game and
                    session_key(*game) == session_key(*self.game))

    def pattern(self, game):
        if game[1]:
            return game[1].title_re
        pattern = self.picker.library.game_pattern(game[0].title)
        options = self.picker.library.game_options(self.picker.profiles)
        return pattern if any(rule == pattern for _, rule in options) else None

    def _progress(self, state, step, message):
        self.picker.GLib.idle_add(self._status, state, step, message)

    def _status(self, state, step, message):
        self.status.update(state=state, step=step, message=message)
        self.picker.message = message
        return False

    def _blocked(self):
        p = self.picker
        return any(getattr(p, name, None) for name in
                   ('busy', 'recorder', 'pending', 'pending_record', 'pending_capture',
                    'record_review', 'vpn_testing', 'runner'))

    def start(self, workflow_id, game=None, *, automatic=False):
        p = self.picker
        if self.error or p.preview or self.active or self._blocked():
            return False
        workflow = self.store.get(workflow_id)
        active = login.active_window()
        game = game or (p.game if not automatic else None) or p.sessions.games.get(active.address)
        if not workflow or not game or (automatic and active.address != game[0].address):
            return False
        if p.sessions.games.get(game[0].address) != game or workflow['game'] != self.pattern(game):
            return False
        key = session_key(*game)
        if automatic and key in self.claims:
            return False
        # Manual starts release popup focus; automatic starts never steal focus.
        if not automatic:
            self._release_focus()
        p.verify_target(game)
        self.claims.add(key)
        p.library.atomic_write(self.path, sorted(self.claims))
        if p.sessions.claim_first(game):
            p.persist()
        self.game = game
        runner = WorkflowRunner(self.store, p.library, lambda: p.verify_target(game),
                                p.legacy_profiles, p.settings, progress=self._progress)
        self.runner = runner
        self._status('running', 0, 'Visual workflow running. F9 opens the panel and pauses; resume explicitly from workflow controls.')
        def work():
            try:
                runner.run(workflow)
            except Exception as exc:
                self._progress('cancelled' if runner.cancel.is_set() else 'failed',
                               runner.completed, str(exc))
            finally:
                p.GLib.idle_add(self._finished, runner)
        try:
            self.worker = threading.Thread(target=work, daemon=True, name='visual-workflow')
            self.worker.start()
        except Exception as exc:
            runner.cancel.set()
            self.runner = self.worker = None
            self._status('failed', 0, f'Visual workflow could not start: {exc}')
            return False
        return True

    def _finished(self, runner):
        if self.runner is runner:
            self.runner = None
            self.worker = None
        return False

    def pause(self):
        if self.runner:
            self.runner.paused.set()
            self._status('paused', self.status['step'], 'Paused. Return focus to the game before resuming.')

    def _release_focus(self):
        # Match Picker.launch: release keyboard ownership, hide, then flush
        # before verifying. Never activate or focus an arbitrary game window.
        p = self.picker
        if not p.preview:
            p.Layer.set_keyboard_mode(p.window, p.Layer.KeyboardMode.NONE)
        p.hide()
        if not p.preview:
            p.window.get_display().sync()

    def resume(self):
        if self.runner:
            try:
                self._release_focus()
                self.picker.verify_target(self.game)
            except Exception:
                self.stop()
                raise
            self.runner.paused.clear()

    def stop(self):
        if self.runner:
            self.runner.cancel.set()
            self.runner.paused.clear()

    def reconcile(self):
        p = self.picker
        if self.error:
            return
        live = {session_key(*game) for game in p.sessions.games.values()}
        claims = self.claims & live
        if claims != self.claims:
            self.claims = claims
            p.library.atomic_write(self.path, sorted(claims))
        if self.active:
            try:
                if session_key(*self.game) not in live:
                    raise ValueError('Game session changed.')
                if not self.runner.paused.is_set():
                    p.verify_target(self.game)
            except Exception:
                self.stop()
            return
        if p.preview or self._blocked():
            return
        try:
            active = login.active_window()
            game = p.sessions.games.get(active.address)
            if not game or session_key(*game) in self.claims:
                return
            pattern = self.pattern(game)
            workflow = self.store.automatic(pattern) if pattern else None
            if workflow:
                self.start(workflow['id'], game, automatic=True)
        except Exception as exc:
            self.stop()
            self._status('failed', 0, f'Visual workflow could not start: {exc}')
