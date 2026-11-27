"""Adapter: spelling dictionaries (ports.Lexicon) — Hunspell files read by spylls (pure Python, offline).

The dictionaries are downloaded ONCE (install(): pinned sha256, config.DICTIONARIES) into config.DICT_HOME; after
that everything is local. A missing package or dictionary is not an error: lexicon() returns None and the
spelling stage works from the book's own vocabulary and the second engine's reading alone.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import urllib.request
import uuid

from .. import config


class HunspellLexicon:
    """known(word) answered from a PERSISTENT memo of earlier answers (same dictionary files, same spylls version:
    the answer cannot differ); the dictionaries themselves are read only when a word was never asked before.
    Reading a Greek dictionary takes seconds and every book rebuild is a new process, while a book's words are
    almost all the same from one rebuild to the next."""

    def __init__(self, bases: list, memo_path=None):
        self.bases = bases
        self.name = '+'.join(b.name for b in bases)
        self._dicts = None
        self._memo_path = memo_path
        self._memo: dict[str, bool] = _read_memo(memo_path)
        self._new = 0
        if memo_path is not None:
            atexit.register(self.save)

    def _dictionaries(self):
        if self._dicts is None:
            from spylls.hunspell import Dictionary
            self._dicts = [Dictionary.from_files(str(b)) for b in self.bases]
        return self._dicts

    def known(self, word: str) -> bool:
        hit = self._memo.get(word)
        if hit is None:
            hit = self._memo[word] = any(d.lookup(word) for d in self._dictionaries())
            self._new += 1
        return hit

    def save(self) -> None:
        """Write the memo when it learned new words (atomic; concurrent writers only lose each other's additions)."""
        if not self._new or self._memo_path is None:
            return
        try:
            self._memo_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._memo_path.with_name(f'{self._memo_path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp')
            tmp.write_text(json.dumps(self._memo, ensure_ascii=False, separators=(',', ':')))
            tmp.replace(self._memo_path)
            self._new = 0
        except OSError:
            pass                                       # a cache: never fatal


def _read_memo(path) -> dict[str, bool]:
    try:
        m = json.loads(path.read_text()) if path is not None else {}
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def _files(code: str):
    spec = config.DICTIONARIES.get(code)
    if not spec:
        return None
    base = config.DICT_HOME / spec['name']
    return base if base.with_suffix('.aff').exists() and base.with_suffix('.dic').exists() else None


_LOADED: dict[str, HunspellLexicon | None] = {}


def lexicon(langs: str | None = None) -> HunspellLexicon | None:
    """The dictionaries for a book's OCR languages ('ell,eng'), or None when none is installed. One per process;
    answers memoised on disk (see HunspellLexicon). SCIENTIFIC_OCR_NO_LEXICON=1 disables them (tests)."""
    if os.environ.get('SCIENTIFIC_OCR_NO_LEXICON'):
        return None
    key = langs or config.DEFAULT_OCR_LANG
    if key not in _LOADED:
        _LOADED[key] = _load(key)
    return _LOADED[key]


def _load(langs: str) -> HunspellLexicon | None:
    try:
        import spylls
        from spylls.hunspell import Dictionary  # noqa: F401  (availability check only)
    except ImportError:
        return None
    bases = [b for code in langs.split(',') if (b := _files(code.strip())) is not None]
    if not bases:
        return None
    # the memo is valid for exactly these dictionary files and this spylls version
    sig = hashlib.sha1(json.dumps([[b.name, *(_stat(b.with_suffix(x)) for x in ('.aff', '.dic'))] for b in bases]
                                  + [getattr(spylls, '__version__', '')]).encode()).hexdigest()[:16]
    return HunspellLexicon(bases, config.DICT_HOME / 'memo' / f'{sig}.json')


def _stat(p) -> list:
    st = p.stat()
    return [st.st_size, int(st.st_mtime)]


def install(log=print) -> dict:
    """Download every configured dictionary (verified against its pinned sha256). Returns {name: status}."""
    config.DICT_HOME.mkdir(parents=True, exist_ok=True)
    status = {}
    for spec in config.DICTIONARIES.values():
        base = config.DICT_HOME / spec['name']
        ok = True
        for ext in ('aff', 'dic'):
            dest = base.with_suffix('.' + ext)
            if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == spec['sha256'][ext]:
                continue
            data = urllib.request.urlopen(f"{spec['url']}.{ext}", timeout=60).read()
            digest = hashlib.sha256(data).hexdigest()
            if digest != spec['sha256'][ext]:
                log(f"FAILED {spec['name']}.{ext}: sha256 {digest} != pinned {spec['sha256'][ext]}")
                ok = False
                continue
            dest.write_bytes(data)
        status[spec['name']] = 'OK' if ok else 'FAILED'
        log(f"{spec['name']}: {status[spec['name']]}")
    return status
