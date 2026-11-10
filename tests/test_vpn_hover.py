"""Fresh hover measurements, cancellation, and hidden legacy VPN notices."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from auto_macro import Picker
from detection import login
import vpn_servers as vpn


class CurrentLatencyTests(unittest.TestCase):
    def setUp(self):
        resolver = patch.object(vpn, 'resolve_probe_target', return_value=vpn.TARGET)
        resolver.start()
        self.addCleanup(resolver.stop)

    def test_measures_current_route_without_mutation_or_vpn_lock(self):
        for interface in ('eth0', 'gfn-jp'):
            with self.subTest(interface=interface), patch.object(vpn, 'route_device', return_value=interface), \
                 patch.object(vpn, 'probe', return_value={'latency': 27.5}) as probe, \
                 patch.object(vpn, 'vpn_lock') as lock, patch.object(vpn, 'SplitTunnel') as backend:
                cancel = threading.Event()
                self.assertEqual(vpn.current_latency(cancel), 27.5)
                self.assertEqual(probe.call_args.args[:2], (interface, cancel))
                self.assertEqual(probe.call_args.kwargs, {'samples': 3, 'target': vpn.TARGET})
                backend.assert_not_called()
                lock.assert_not_called()

    def test_cancelled_or_temporary_test_route_is_not_measured(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(vpn, 'route_device') as route:
            with self.assertRaises(vpn.SearchCancelled):
                vpn.current_latency(cancel)
            route.assert_not_called()
        with patch.object(vpn, 'route_device', return_value='gfnprobe12345'), patch.object(vpn, 'probe') as probe:
            with self.assertRaises(ValueError):
                vpn.current_latency(threading.Event())
            probe.assert_not_called()

    def test_failed_or_changed_route_never_returns_a_stale_latency(self):
        with patch.object(vpn, 'route_device', return_value='gfn-jp'), \
             patch.object(vpn, 'probe', return_value={'latency': None}):
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                vpn.current_latency(threading.Event())
        with patch.object(vpn, 'route_device', side_effect=['gfn-jp', 'eth0']), \
             patch.object(vpn.socket, 'create_connection') as connect:
            with self.assertRaises(ValueError):
                vpn.current_latency(threading.Event())
            connect.assert_not_called()


class HoverUiTests(unittest.TestCase):
    def setUp(self):
        with patch.object(login, 'load_profiles', return_value=({}, [])):
            self.picker = Picker(preview=True, width=360)
        self.picker.render()
        self.hover = self.picker.vpn_hover
        self.button = self.picker.tool_buttons['vpn']
        self.addCleanup(self.picker.preview_directory.cleanup)
        self.addCleanup(self.picker.window.destroy)

    def tooltip(self):
        tip = self.picker.Gtk.Tooltip()
        with patch.object(tip, 'set_text') as text:
            self.assertTrue(self.button.emit('query-tooltip', 0, 0, False, tip))
            return text.call_args.args[0]

    def pump(self):
        while self.picker.Gtk.events_pending():
            self.picker.Gtk.main_iteration_do(False)

    def test_real_hover_signal_starts_one_worker_and_updates_existing_tooltip(self):
        from gi.repository import Gdk
        enter = Gdk.Event.new(Gdk.EventType.ENTER_NOTIFY)
        enter.detail = Gdk.NotifyType.ANCESTOR
        with patch.object(self.picker, 'preview', False), \
             patch('ui_vpn_hover.threading.Thread') as thread, \
             patch.object(vpn, 'current_latency', return_value=31.2) as measure, \
             patch.object(self.picker.Gtk.Tooltip, 'trigger_tooltip_query'):
            self.button.emit('enter-notify-event', enter)
            self.assertIn('Checking GFN latency', self.tooltip())
            self.tooltip()
            thread.assert_called_once()
            thread.call_args.kwargs['target']()
            self.pump()
            self.assertIn('GFN latency: 31.2 ms', self.tooltip())
            self.assertIn('F10', self.tooltip())
            self.assertIs(measure.call_args.args[0], self.hover.cancel)
            self.picker.apply_tool_status(dict(vpn=True, afk=False))
            self.assertIn('Disconnect VPN', self.tooltip())
            self.assertIn('31.2 ms', self.tooltip())

    def test_leave_and_reenter_checks_again_and_ignores_old_worker(self):
        self.hover.start()
        generation, cancel = self.hover.generation, self.hover.cancel
        self.hover.stop()
        self.assertTrue(cancel.is_set())
        self.hover.start()
        self.hover.finished(generation, 50)
        self.assertIn('Checking GFN latency', self.tooltip())
        self.hover.finished(self.hover.generation, 24)
        self.assertIn('24 ms', self.tooltip())
        self.hover.stop()
        self.assertIn('Checking GFN latency', self.tooltip())

    def test_failure_and_language_round_trip_preserve_button_action(self):
        self.hover.start()
        self.hover.finished(self.hover.generation, None)
        self.assertIn('GFN latency unavailable', self.tooltip())
        self.hover.finished(self.hover.generation, 29)
        self.picker.localizer.set_language('ko')
        self.picker.update_tool_controls()
        self.assertIn('GFN 지연 시간: 29 ms', self.tooltip())
        self.picker.localizer.set_language('en')
        self.picker.update_tool_controls()
        self.assertIn('Connect Japan VPN', self.tooltip())
        self.assertIn('29 ms', self.tooltip())

    def test_unmap_destroy_and_click_discard_pending_measurements(self):
        for signal in ('unmap', 'clicked'):
            self.hover.start()
            generation, cancel = self.hover.generation, self.hover.cancel
            with patch.object(self.picker, 'vpn_action'):
                self.button.emit(signal)
            self.assertTrue(cancel.is_set())
            self.hover.finished(generation, 12)
            self.assertFalse(self.hover.active)
        self.hover.start()
        generation, cancel = self.hover.generation, self.hover.cancel
        self.picker.render()
        self.assertTrue(cancel.is_set())
        self.assertTrue(self.hover.destroyed)
        self.hover.finished(generation, 12)
        self.assertIsNone(self.hover.latency)


class VpnWidgetRemovalTests(unittest.TestCase):
    def test_vpn_notification_can_stay_hidden_without_changing_login_notice_defaults(self):
        with patch.object(login, 'show_widget') as show, patch('builtins.print'):
            login.notify('VPN already connected', visible=False)
            self.assertFalse(show.call_args.kwargs['visible'])
            login.notify('Login completed')
            self.assertTrue(show.call_args.kwargs['visible'])
            with patch.object(login, 'require_commands'), \
                 patch.object(login, 'choose_vpn_backend', return_value='nordvpn'), \
                 patch.object(login, 'nordvpn_status', return_value='Status: Connected\nCountry: Japan'), \
                 patch.object(login, 'log'), patch.object(login, 'run') as run:
                login.connect_japan_vpn(None, [], monitor=False)
                self.assertFalse(show.call_args.kwargs['visible'])
                run.assert_not_called()

    def test_hidden_notice_keeps_status_without_starting_standalone_widget(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(login, 'STATE_DIR', state), patch.object(login, 'WIDGET_STATE', state / 'widget.json'), \
                 patch.object(login, 'widget_running') as running, patch.object(login.subprocess, 'Popen') as spawn:
                login.show_widget('GFN VPN', 'Countdown', visible=False, countdown_until=12345)
                saved = json.loads((state / 'widget.json').read_text())
                self.assertFalse(saved['visible'])
                self.assertEqual(saved['countdown_until'], 12345)
                running.assert_not_called()
                spawn.assert_not_called()
