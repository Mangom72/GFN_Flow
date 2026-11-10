"""Cross-feature guards with private temporary state and mocked system actions."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from input_activity import macro_input_active, owner_identity


def installed_script(name):
    # Review snapshots carry copies of these host integration scripts.
    snapshot = Path(__file__).resolve().parents[1] / 'external' / name
    path = snapshot if snapshot.exists() else Path.home() / '.local/bin' / name
    if not path.exists():
        raise unittest.SkipTest('Installed host integration script unavailable: ' + name)
    return path


def module_at(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ActivityTests(unittest.TestCase):
    def test_activity_guard_tolerates_missing_corrupt_and_idle_state(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', XDG_STATE_HOME=tmp):
            path = Path(tmp) / 'auto-macro/playback.json'
            self.assertFalse(macro_input_active())
            path.parent.mkdir()
            for content in ('{bad', '[]', 'null', '{}', '{"recording":false}'):
                path.write_text(content)
                self.assertFalse(macro_input_active())
            for key in ('armed', 'recording', 'running'):
                path.write_text(json.dumps({key: True, 'input_owner': owner_identity()}))
                self.assertTrue(macro_input_active())

    def test_orphaned_rebooted_and_legacy_activity_does_not_latch_shortcuts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', XDG_STATE_HOME=tmp):
            path = Path(tmp) / 'auto-macro/playback.json'
            path.parent.mkdir()
            for owner in (None, {'pid': 99999999, 'start': '1', 'boot': 'old'},
                          {**owner_identity(), 'boot': 'previous-boot'},
                          {**owner_identity(), 'start': 'recycled-pid'}):
                path.write_text(json.dumps({'recording': True, 'input_owner': owner}))
                self.assertFalse(macro_input_active())

    def test_hotspot_shortcut_does_not_toggle_during_macro_input(self):
        action = module_at('test_f9_action_guard', installed_script('f9-action'))
        hotspot = Mock(ControlError=RuntimeError)
        with patch.dict('sys.modules', hotspot_control=hotspot), patch.object(action.sys, 'argv', ['f9-action', '--hotspot-toggle']), \
             patch('input_activity.macro_input_active', return_value=True), \
             patch.object(action.subprocess, 'run') as run:
            action.main()
        run.assert_not_called()
        hotspot.toggle_hotspot.assert_not_called()

    def test_anti_afk_pauses_without_reading_or_injecting_input(self):
        afk = module_at('test_afk_macro_guard', installed_script('gfn-anti-afk.py'))
        with patch.object(afk, 'macro_input_active', return_value=True), \
             patch.object(afk, 'write_state') as state, patch.object(afk, 'run') as run:
            self.assertTrue(afk.nudge())
        run.assert_not_called()
        self.assertTrue(state.call_args.kwargs['waiting'])

    def test_normal_f9_route_is_preserved(self):
        action = module_at('test_f9_normal_route', installed_script('f9-action'))
        with patch.object(action.sys, 'argv', ['f9-action']), patch.object(action.subprocess, 'run') as run:
            action.main()
        self.assertEqual(run.call_args.args[0][-1], 'f9')
