"""Adapter: the LaTeX renderer the user sees (vendored KaTeX + mhchem), used outside the browser.

check(): every formula of the canonical is rendered with the same KaTeX build as the app preview and the PDF
export (node, offline). A formula that does not render is flagged so its source image is shown instead —
the export never shows raw LaTeX where the preview shows maths, or vice versa.
print_pdf(): headless Chrome/Chromium prints the export HTML (the same renderer as the preview) to PDF.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .. import config

KATEX_DIR = Path(__file__).resolve().parent / 'http' / 'static' / 'vendor' / 'katex'
CHECK_JS = Path(__file__).resolve().parent / 'js' / 'katex_check.js'


def check(items: list[dict]) -> dict[str, str] | None:
    """items: [{id, latex, display}] -> {id: error}; None when node is unavailable (recorded, not hidden)."""
    node = config.node()
    if not node:
        return None
    if not items:
        return {}
    r = subprocess.run([str(node), str(CHECK_JS), str(KATEX_DIR)], input=json.dumps(items), capture_output=True,
                       text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f'katex check failed: {r.stderr[-500:]}')
    return json.loads(r.stdout or '{}')


def print_pdf(html_file: Path, timeout: int = 180) -> bytes | None:
    """None when no Chrome/Chromium is installed (caller falls back and says so)."""
    chrome = config.chrome()
    if not chrome:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'out.pdf'
        cmd = [str(chrome), '--headless=new', '--disable-gpu', '--no-pdf-header-footer', '--allow-file-access-from-files',
               f'--user-data-dir={tmp}/profile', '--run-all-compositor-stages-before-draw', '--virtual-time-budget=20000',
               f'--print-to-pdf={out}', html_file.resolve().as_uri()]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if not out.exists():
            raise RuntimeError(f'chrome print failed ({r.returncode}): {r.stderr[-500:]}')
        return out.read_bytes()
