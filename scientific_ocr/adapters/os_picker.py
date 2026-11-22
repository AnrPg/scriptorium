"""Driven adapter: the operating system's own file picker (macOS Finder open panel), shown by the LOCAL server.

The app runs on the user's Mac, so the server can open the native panel and get real paths back: files are then
copied straight from disk (no upload through the browser, no size limits). Files AND folders can be chosen in the
same panel. Returns [] when the user cancels; raises PickerUnavailable when there is no native picker.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

# JXA (JavaScript for Automation) drives NSOpenPanel: files and folders together, multiple selection.
_JXA = r'''
ObjC.import('AppKit');
function run(argv) {
  var cfg = JSON.parse(argv[0]);
  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);
  var p = $.NSOpenPanel.openPanel;
  p.canChooseFiles = true; p.canChooseDirectories = true; p.allowsMultipleSelection = true;
  p.canCreateDirectories = false; p.resolvesAliases = true;
  p.message = cfg.message; p.prompt = cfg.prompt;
  p.allowedFileTypes = $(cfg.types);
  if (cfg.start) p.directoryURL = $.NSURL.fileURLWithPath(cfg.start);
  app.activateIgnoringOtherApps(true);
  p.level = $.NSModalPanelWindowLevel;
  if (p.runModal != $.NSModalResponseOK) return '[]';
  var out = [], urls = p.URLs;
  for (var i = 0; i < urls.count; i++) out.push(urls.objectAtIndex(i).path.js);
  return JSON.stringify(out);
}
'''


class PickerUnavailable(RuntimeError):
    pass


def available() -> bool:
    return sys.platform == 'darwin' and bool(shutil.which('osascript'))


def pick(types: list[str], message: str, prompt: str, start: str | None = None, timeout: float = 1800) -> list[str]:
    """Show the native open panel; `types` are file extensions without dots. Blocks until the user answers."""
    if not available():
        raise PickerUnavailable('no native file picker on this system')
    cfg = json.dumps({'types': types, 'message': message, 'prompt': prompt, 'start': start})
    try:
        r = subprocess.run(['osascript', '-l', 'JavaScript', '-e', _JXA, cfg],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode:
        raise PickerUnavailable(f'native picker failed: {r.stderr.strip()[:300]}')
    try:
        return [p for p in json.loads(r.stdout.strip() or '[]') if isinstance(p, str)]
    except json.JSONDecodeError:
        raise PickerUnavailable(f'native picker returned {r.stdout[:200]!r}')
