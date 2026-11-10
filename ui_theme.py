"""Shared visual tokens for Macro Studio's native widgets."""
import json
from html import escape
from pathlib import Path

TOKENS = json.loads(Path(__file__).with_name('ui_tokens.json').read_text(encoding='utf-8'))


def category_markup(title, count):
    return (f'<span size="{TOKENS["section_size"] * 768}" weight="600">{escape(title)}</span>  '
            f'<span size="{TOKENS["meta"] * 768}" foreground="{TOKENS["muted"]}">{count}</span>')


def macro_markup(game, count, translate=lambda value: value, muted=False):
    color = TOKENS['muted'] if muted else TOKENS['text']
    count_label = translate('{count} action' if count == 1 else '{count} actions').format(count=count)
    return (f'<span weight="600" size="{TOKENS["name"] * 768}" foreground="{color}">{escape(game)}</span>\n'
            f'<span size="{TOKENS["meta"] * 768}" foreground="{TOKENS["muted"]}">'
            f'{escape(count_label)}</span>')

CSS = ('''
window { background: %(background)s; color: %(text)s; font-family: "%(font)s", sans-serif; font-size: %(body)spx; }
window.macro-popup { background: transparent; }
window.recording-window { background: transparent; }
.recording-hud { background: %(surface)s; color: %(text)s; border-radius: %(popup_radius)spx;
  padding: %(space_sm)spx %(space_md)spx; border: 1px solid %(line)s; }
.recording-dot { color: %(danger)s; }
.playback-dot { color: %(accent)s; }
.playback-completed { color: %(success)s; }
.playback-cancelled { color: %(muted)s; }
.playback-failed { color: %(danger)s; }
.playback-stats { color: %(muted)s; font-size: %(meta)spx; font-feature-settings: "tnum"; }
.recording-clock { font-size: %(body)spx; font-weight: 600; font-feature-settings: "tnum"; }
.recording-shortcut { color: %(muted)s; font-size: %(meta)spx; }
.popup-surface { background: %(background)s; border-radius: %(popup_radius)spx; }
.panel { padding: %(space_lg)spx; }
.heading { font-size: %(title)spx; font-weight: 700; }
.game, .macro-name { font-size: %(name)spx; font-weight: 600; color: %(text)s; }
.muted, .status, .field-label { color: %(muted)s; font-size: 11px; }
.status { padding: 8px 0; border-top: 1px solid %(line)s; }
.error-status { color: %(danger)s; }
.input-error { color: %(danger)s; font-size: %(meta)spx; }
textview, textview text { color: %(text)s; background: %(surface)s; }
.section { font-size: 12px; font-weight: 600; color: %(muted)s; padding: 4px 0; }
.shortcut-key { min-width: 36px; font-weight: 600; color: %(muted)s; }
.reduced-motion button { transition-duration: 0ms; }
button { background-image: none; background: transparent; color: %(text)s; border: 1px solid transparent;
  border-radius: 5px; padding: 5px 7px; min-width: 16px; min-height: 16px; box-shadow: none; font-weight: 500;
  transition: background-color %(motion_hover_ms)sms ease-out, border-color %(motion_hover_ms)sms ease-out, color %(motion_hover_ms)sms ease-out; }
button:hover { background: %(hover)s; }
button:active, button:checked { background: %(selected)s; }
button:disabled { color: #777780; background: transparent; }
button:focus, entry:focus, combobox:focus, spinbutton:focus { border-color: %(accent)s; outline: 1px solid %(accent)s; outline-offset: -1px; }
button.primary { background: %(accent)s; color: #202027; font-weight: 600; }
button.primary:hover { background: #c6c2fb; }
button.primary:disabled { background: %(raised)s; color: #8d8d97; }
.confirmation-shade { background: rgba(0, 0, 0, 0.7); }
.confirmation-card { padding: 14px; background: %(surface)s; border: 1px solid %(line)s; border-radius: 8px; }
button.danger { color: %(danger)s; }
button.danger:hover { background: #422a30; }
button.danger:disabled { color: #777780; background: transparent; }
button.secondary { background: %(raised)s; }
button.tool-active { color: %(success)s; background: %(raised)s; }
entry, spinbutton { min-height: 22px; padding: 4px 8px; color: %(text)s; background: %(surface)s;
  border: 1px solid %(line)s; border-radius: 5px; box-shadow: none; }
entry selection { background: %(selected)s; color: %(text)s; }
spinbutton button { padding: 3px 5px; }
combobox button { background: %(surface)s; border-color: %(line)s; padding: 4px 7px; font-weight: 400; }
notebook { background: transparent; border: none; }
notebook > header { background: transparent; border: none; border-bottom: 1px solid %(line)s; padding: 0; }
notebook > header tabs { background: transparent; }
notebook > header tabs tab { min-height: 24px; margin: 0 10px 0 0; padding: 4px 0; border: none; border-bottom: 2px solid transparent; background: transparent; color: %(muted)s;
  transition: color %(motion_hover_ms)sms ease-out, border-color %(motion_hover_ms)sms ease-out; }
notebook > header tabs tab:checked { color: %(text)s; border-bottom-color: %(accent)s; }
.studio-tabs > header tabs tab { margin-right: 4px; padding: 4px 5px; min-width: 16px; min-height: 24px;
  box-shadow: none; background-image: none; }
.studio-tabs > header tabs tab:focus { outline: 1px solid %(accent)s; outline-offset: -1px; }
.studio-tabs > header tabs tab label { font-weight: 600; }
notebook > header tabs tab:hover { color: %(text)s; }
notebook > stack { background: transparent; border: none; padding-top: 10px; }
treeview.view { background: %(background)s; color: %(text)s; border: none; }
treeview.view:selected { background: %(selected)s; color: %(text)s; }
treeview.view:selected:focus { background: %(selected)s; outline: 1px solid %(accent)s; outline-offset: -1px; }
treeview.view:focus { outline: 1px solid %(accent)s; outline-offset: -1px; }
treeview.view header button { background: transparent; border: none; border-bottom: 1px solid %(line)s; color: %(muted)s; padding: 6px; }
scrolledwindow, viewport { border: none; background: transparent; }
scrollbar { background: transparent; }
scrollbar slider { background: #494950; border: none; border-radius: 5px; min-width: 4px; min-height: 24px; }
scrollbar slider:hover { background: #686871; }
scrollbar trough { background: transparent; }
progressbar trough { background: %(raised)s; border: none; min-height: 4px; }
progressbar progress { background: %(accent)s; border: none; min-height: 4px; }
checkbutton { padding: 3px 0; }
checkbutton check { min-width: 14px; min-height: 14px; background: %(surface)s; border: 1px solid #696973; border-radius: 3px; }
checkbutton check:checked { background: %(accent)s; color: #202027; border-color: %(accent)s; }
switch { background: %(surface)s; border: 1px solid %(line)s; border-radius: 14px; }
switch:checked { background: %(accent)s; border-color: %(accent)s; }
switch slider { background: %(text)s; border: none; border-radius: 12px; box-shadow: none; }
menu, popover { background: %(raised)s; color: %(text)s; border: 1px solid %(line)s; border-radius: 6px; padding: 4px;
  font-family: "%(font)s", sans-serif; }
menuitem { padding: 6px 10px; }
menuitem:hover { background: %(selected)s; }
tooltip { background: %(raised)s; color: %(text)s; border: 1px solid %(line)s; border-radius: 5px;
  font-family: "%(font)s", sans-serif; }
.context-bar { border-top: 1px solid %(line)s; padding-top: 8px; }
.macro-row { padding: %(row_padding)spx %(list_indent)spx; border-radius: 5px; }
.macro-row:hover { background: %(hover)s; }
.macro-empty { padding: %(row_padding)spx %(list_indent)spx; color: %(muted)s; font-size: %(section_size)spx; }
treeview.macro-list { -GtkTreeView-horizontal-separator: 0; -GtkTreeView-vertical-separator: 0; }
list.macro-list { padding: 0; }
list.macro-list > row { margin: 0; }
.macro-section > title { min-height: 28px; padding: 0; color: %(text)s; }
.macro-section > title > arrow { min-width: %(icon)spx; min-height: %(icon)spx; margin-right: 4px; }
list { background: transparent; }
list row { background: transparent; border: none; padding: 0; }
list row:selected { background: %(selected)s; }
list row:selected .macro-row:hover { background: transparent; }
list row:focus { outline: 1px solid %(accent)s; outline-offset: -1px; }
expander { color: %(muted)s; font-size: 12px; }
separator { background: %(line)s; min-height: 1px; }
''') % TOKENS
CSS = CSS.encode()

STATUS_CSS = ('''
window { background: transparent; font-family: "%(font)s", sans-serif; }
.buff { background: %(surface)s; background-image: none; border: 1px solid %(line)s;
  border-radius: 6px; padding: 3px; min-width: 32px; min-height: 32px;
  box-shadow: none; color: %(text)s; }
.buff:hover { background: %(hover)s; }
.buff-ok { border-color: %(line)s; }
.buff-warn { border-color: #a68e63; }
.buff-error { border-color: %(danger)s; }
.buff-idle { border-color: %(line)s; }
.number { color: %(text)s; font-size: 11px; font-weight: 600;
  background: %(raised)s; border-radius: 3px; padding: 1px 3px; }
.buff-detail { background: %(raised)s; color: %(text)s;
  border: 1px solid %(line)s; border-radius: 6px; padding: 12px; }
.title { color: %(text)s; font-size: 13px; font-weight: 600; }
.detail { color: %(muted)s; font-size: 11px; }
''') % TOKENS
STATUS_CSS = STATUS_CSS.encode()
