"""Use case — build history: undo/redo of a book-wide action shown at once, without a rebuild.

A full build is a pure function of (inputs, code, ACTIVE reviewer events). Every build records that key next to
its outputs (cache/builds/current.json); the previous builds' outputs are kept as hard links (no copy) in
cache/builds/<slot>/ with their key. When undo/redo brings the active events back to exactly the set some kept
build saw, and nothing else changed since (same inputs fingerprint: raw OCR, source, settings, model proposals,
calibration books, dictionaries, code), that build's outputs ARE what a rebuild would produce: they are put back
in place instead of rebuilding. Any doubt (no key, other inputs, a build running) -> nothing is restored and the
normal rebuild path runs. The kept builds swap with the current one, so redo can come back the same way.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..adapters.event_store import EventStore, fold
from ..adapters.storage import PREVIEW_DIR, atomic_write, book_lock, read_json, try_book_lock

OUTPUTS = ('canonical.json', 'chapter.md', 'review_report.html', 'run_manifest.json')   # projection._publish
HISTORY = Path('cache') / 'builds'
CURRENT = HISTORY / 'current.json'
_CODE = Path(__file__).resolve().parents[1]                 # the scientific_ocr package
# book files that are NOT build inputs: outputs, the event log (keyed separately), run state
_NOT_INPUTS = set(OUTPUTS) | {'events.jsonl', 'progress.json'}
_UI_SETTINGS = {'page_updates_paused'}                      # book.json keys the build never reads


def fingerprint(out: Path) -> str:
    """Everything besides the event log that a build reads, by name/size/mtime: source + raw OCR (incl. region
    OCR and specialised-model results), top-level book files (settings, model proposals), the calibration books'
    projections, the installed dictionaries, and the code."""
    def stats(root: Path, pattern: str = '*'):
        if not root.exists():
            return []
        files = sorted(f for f in root.rglob(pattern) if f.is_file())
        return [(str(f.relative_to(root)), (st := f.stat()).st_size, st.st_mtime_ns) for f in files]
    parts = {'raw': stats(out / 'raw'), 'source': stats(out / 'source'),
             'book': [(f.name, f.stat().st_size, f.stat().st_mtime_ns) for f in sorted(out.iterdir())
                      if f.is_file() and f.name not in _NOT_INPUTS | {'book.json'} and not f.name.startswith('.')],
             'settings': {k: v for k, v in (read_json(out / 'book.json', {}) or {}).items() if k not in _UI_SETTINGS},
             'calibration': [(b, (out.parent / b / 'canonical.json').stat().st_mtime_ns)
                             for b in (read_json(out / 'book.json', {}) or {}).get('calibrate_with') or []
                             if (out.parent / b / 'canonical.json').exists()],
             'dictionaries': stats(config.DICT_HOME, '*.dic') + stats(config.DICT_HOME, '*.aff'),
             'no_lexicon': bool(os.environ.get('SCIENTIFIC_OCR_NO_LEXICON')),
             'code': stats(_CODE, '*.py')}
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()


def keep_previous(out: Path) -> None:
    """Before a build replaces the outputs: keep the current ones (hard links) with their key. Called under the
    build lock; a book built before this existed has no key and is not kept."""
    meta = read_json(out / CURRENT)
    if meta and all((out / f).exists() for f in OUTPUTS):
        _shelve(out, meta)


def record(out: Path, canonical: dict, human: dict) -> None:
    """After a build wrote its outputs: their key (the active events it saw + the inputs fingerprint)."""
    atomic_write(out / CURRENT, json.dumps({
        'built_utc': canonical['run']['built_utc'], 'last_seq': canonical['human']['last_seq'],
        'allow_inference': canonical['run']['allow_inference'], 'active': sorted(human['active']),
        'fingerprint': fingerprint(out)}))


def restore(out: Path) -> bool:
    """Put back a kept build whose key matches the book now (see module doc). True when outputs were restored.
    Never waits for a running build (then False: the normal path applies the change)."""
    slots = _slots(out)
    if not slots:
        return False
    with try_book_lock(out) as locked:
        if not locked:
            return False
        try:
            active = sorted(fold(EventStore(out).load())['active'])
        except Exception:                                    # an invalid log is reported by the build
            return False
        cur = read_json(out / CURRENT) or {}
        if cur.get('active') == active:
            return False                                     # the outputs already are that state
        fp = None
        for slot in slots:
            meta = read_json(slot / 'meta.json') or {}
            if meta.get('active') != active or meta.get('allow_inference') != cur.get('allow_inference', True) \
                    or not all((slot / f).exists() for f in OUTPUTS):
                continue
            fp = fp or fingerprint(out)
            if meta.get('fingerprint') != fp:
                continue
            if cur and all((out / f).exists() for f in OUTPUTS):
                _shelve(out, cur)                            # redo can come back the same way
            for f in OUTPUTS:
                tmp = out / f'.{f}.restore.tmp'
                tmp.unlink(missing_ok=True)
                os.link(slot / f, tmp)
                os.replace(tmp, out / f)
                os.utime(out / f)                            # a new revision for the views (Book.revision)
            atomic_write(out / CURRENT, json.dumps(meta))
            shutil.rmtree(slot, ignore_errors=True)
            with book_lock(out, 'preview'):                  # previews were made from the replaced build
                for p in (out / PREVIEW_DIR).glob('p*.json'):
                    p.unlink(missing_ok=True)
            return True
    return False


def _slots(out: Path) -> list[Path]:
    """Kept builds, newest first."""
    d = out / HISTORY
    return sorted((p for p in d.iterdir() if p.is_dir() and (p / 'meta.json').exists()), reverse=True) if d.exists() else []


def _shelve(out: Path, meta: dict) -> None:
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    slot = out / HISTORY / stamp
    tmp = out / HISTORY / f'.{stamp}.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for f in OUTPUTS:
        try:
            os.link(out / f, tmp / f)                        # outputs are replaced by rename: the link keeps them
        except OSError:
            shutil.copy2(out / f, tmp / f)
    (tmp / 'meta.json').write_text(json.dumps(meta))
    os.replace(tmp, slot)
    for old in _slots(out)[config.BUILD_HISTORY_KEEP:]:
        shutil.rmtree(old, ignore_errors=True)
