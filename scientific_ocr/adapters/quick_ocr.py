"""Adapter (ports.QuickOcr): a quick, local OCR of a SMALL region with Tesseract — used only as EVIDENCE about
ink no layout engine covered (pipeline.fills: is it text at all?) and, when it is text, as one more candidate
reading the reviewer may accept. Never authoritative. Missing binary/language -> None (no evidence)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

TIMEOUT_S = 20


def available() -> bool:
    return shutil.which('tesseract') is not None


def read(png: Path, langs: str) -> str | None:
    """Text Tesseract reads in the image (psm 6: one block of text), or None when it cannot run."""
    exe = shutil.which('tesseract')
    if not exe:
        return None
    lang = '+'.join(x.strip() for x in (langs or 'eng').split(',') if x.strip())
    try:
        r = subprocess.run([exe, str(png), 'stdout', '-l', lang, '--psm', '6'], capture_output=True, text=True,
                           timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None
