"""Repository for books on the local filesystem. A Book is the aggregate root; pages are its entities.

library/<book_id>/
  book.json            settings: title, OCR languages, source file info          (mutable, small)
  source/original.pdf  immutable, read-only copy; sha256 recorded                (never modified)
  raw/                 engine outputs, content-addressed per page/region/crop     (append-only cache)
  events.jsonl         human input (event store)                                 (append-only)
  canonical.json, chapter.md, review_report.html, assets/   projection outputs   (rebuilt, atomic writes)
  progress.json, logs/ processing telemetry;  cache/  thumbnails                 (disposable)
Deleting a book moves it to library/.trash (restorable).
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import stat
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..core.decisions import NEEDS_OCR
from .. import config
from ..core.model import page_summary
from . import pdf as pdfio
from .event_store import EventStore

ID_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')


class NotFound(KeyError):
    """An explicit lookup miss (unknown book, job, file): the HTTP adapter answers 404. A bare KeyError raised by a
    code bug is NOT this, and is reported as an internal error with a traceback."""


def atomic_write(path: Path, data: str | bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp')   # unique: concurrent writers never collide
    tmp.write_bytes(data) if isinstance(data, bytes) else tmp.write_text(data, encoding='utf-8')
    os.replace(tmp, path)


@contextlib.contextmanager
def book_lock(book_dir: Path, name: str = 'build'):
    """Exclusive, process-wide lock on one book: builds started by the UI server and by the CLI must never write
    the same projection files at the same time (they would mix crops and hashes from two runs). Blocks until free;
    released automatically if the process dies (flock)."""
    book_dir.mkdir(parents=True, exist_ok=True)
    with open(book_dir / f'.{name}.lock', 'w') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def read_json(path: Path, default=None):
    """OPTIONAL files (settings, caches, progress): missing or unreadable -> default."""
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return default


_CANON_CACHE: dict[Path, tuple] = {}            # canonical.json path -> ((mtime_ns, size), parsed) — Book.canonical
_CANON_LOCK = threading.Lock()

class CorruptFile(ValueError):
    """A required input exists but cannot be read: never treated as empty."""


def read_json_strict(path: Path, default=None):
    """REQUIRED inputs: missing -> default (not produced yet); present but unreadable -> CorruptFile."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CorruptFile(f'{path.name}: {exc}') from exc


def slug(s: str) -> str:
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')[:40] or 'book'


PREVIEW_DIR = Path('cache') / 'page_preview'     # page previews (application.projection.build_page)


class Book:
    def __init__(self, root: Path):
        self.dir = root
        self.id = root.name
        self.events = EventStore(root)

    # paths
    source_pdf = property(lambda self: self.dir / 'source' / 'original.pdf')
    canonical_path = property(lambda self: self.dir / 'canonical.json')

    def settings(self) -> dict:
        s = read_json(self.dir / 'book.json', {}) or {}
        s.setdefault('id', self.id)
        s.setdefault('title', self.id)
        s.setdefault('ocr_lang', config.DEFAULT_OCR_LANG)
        s.setdefault('calibrate_with', [])
        s.setdefault('auto_diligent', True)
        s.setdefault('page_updates_paused', False)     # reviewer paused the per-page previews (book-wide rebuild only)
        return s

    def update_settings(self, **changes) -> dict:
        allowed = {'title', 'ocr_lang', 'calibrate_with', 'auto_diligent', 'page_updates_paused'}
        bad = set(changes) - allowed
        if bad:
            raise ValueError(f'unknown settings {sorted(bad)}')
        if 'ocr_lang' in changes and not re.fullmatch(r'[a-z_]{3,8}(,[a-z_]{3,8})*', changes['ocr_lang']):
            raise ValueError('ocr_lang: tesseract codes like "eng" or "ell,eng"')
        if 'calibrate_with' in changes and not (isinstance(changes['calibrate_with'], list)
                                                and all(isinstance(x, str) and ID_RE.match(x) for x in changes['calibrate_with'])):
            raise ValueError('calibrate_with: a list of book ids')
        for k in ('auto_diligent', 'page_updates_paused'):
            if k in changes and not isinstance(changes[k], bool):
                raise ValueError(f'{k}: true or false')
        s = {**self.settings(), **{k: v for k, v in changes.items() if v is not None and v != ''}}
        atomic_write(self.dir / 'book.json', json.dumps(s, ensure_ascii=False, indent=2))
        return s

    def source_meta(self) -> dict:
        return read_json(self.dir / 'source' / 'source.json', {}) or {}

    def canonical(self) -> dict | None:
        """The projection, parsed once per VERSION of the file (path, mtime, size — it is only ever replaced by an
        atomic write) and shared by every request of this process: a book's canonical.json is tens of MB, and
        re-parsing it on each request cost ~0.15 s per call (several per page change). Callers treat it as read-only."""
        try:
            st = self.canonical_path.stat()
        except FileNotFoundError:
            return None
        stamp = (st.st_mtime_ns, st.st_size)
        with _CANON_LOCK:
            hit = _CANON_CACHE.get(self.canonical_path)
        if hit and hit[0] == stamp:
            return hit[1]
        data = read_json(self.canonical_path)
        with _CANON_LOCK:
            _CANON_CACHE[self.canonical_path] = (stamp, data)
        return data

    def revision(self) -> str:
        """Changes whenever the projection or the event log is written: clients compare it to know their view is
        stale (every derived indicator — levels, continuations, groups, issues — comes from these two files)."""
        parts = []
        pv = [f.stat().st_mtime_ns for f in (self.dir / PREVIEW_DIR).glob('p*.json')]
        for f in (self.canonical_path, self.events.path):
            try:
                st = f.stat()
                parts.append(f'{st.st_mtime_ns:x}.{st.st_size:x}')
            except FileNotFoundError:
                parts.append('0')
        if pv:                                         # a page preview written: the open page view is stale
            parts.append(f'{max(pv):x}.{len(pv):x}')
        return '-'.join(parts)

    # model proposals: an append-only audit trail (what a local model suggested; never authoritative)
    proposals_path = property(lambda self: self.dir / 'proposals.jsonl')

    def log_proposal(self, record: dict):
        with open(self.proposals_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

    def proposals(self) -> list[dict]:
        out = []
        if self.proposals_path.exists():
            for line in self.proposals_path.read_text(encoding='utf-8').splitlines():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue                   # a torn last line (crash mid-write) is skipped, the rest is kept
        return out

    def progress(self) -> dict | None:
        return read_json(self.dir / 'progress.json')

    def page_count(self) -> int:
        s = self.settings().get('source', {})
        if s.get('page_count'):
            return s['page_count']
        return len(pdfio.page_info(self.source_pdf)) if self.source_pdf.exists() else 0

    def pages(self) -> list[dict]:
        c = self.canonical() or {}
        by_page: dict[int, list] = {}
        for b in self.seen_blocks():                       # page previews included: decisions show at once
            by_page.setdefault(b['page'], []).append(b)
        failed = {p['page'] for p in c.get('pages', [])
                  if any(e.get('status') != 'OK' for e in p.get('engines', {}).values())}
        dims = (c.get('source') or {}).get('page_dims_pt', {})
        removed = set(self.removed_pages())
        out = []
        for p in range(1, self.page_count() + 1):
            if p in removed:
                s = {'state': 'removed', 'blocks': 0, 'required': 0, 'recommended': 0, 'decided': 0}
            elif p in by_page:
                s = page_summary(by_page[p])
                if p in failed:
                    s['state'] = 'failed'
            else:
                s = {'state': 'not_processed', 'blocks': 0, 'required': 0, 'recommended': 0, 'decided': 0}
            out.append({'page': p, 'dims': dims.get(str(p)), **s})
        return out

    def page_preview(self, page: int) -> dict | None:
        """The page's preview if it was made from the current full build (else None: superseded)."""
        pv = read_json(self.dir / PREVIEW_DIR / f'p{int(page):04d}.json')
        base = ((self.canonical() or {}).get('run') or {}).get('built_utc')
        return pv if pv and base and pv.get('base') == base else None

    def seen_blocks(self) -> list[dict]:
        """Every segment as the reviewer SEES it, in reading order: the full projection with each page rebuilt
        since (valid page preview) in its place. Every view of review state (page list, queue, segment) reads
        this, so a page-local decision shows everywhere at once, not only on its page."""
        blocks = (self.canonical() or {}).get('blocks', [])
        previews = {}
        for f in (self.dir / PREVIEW_DIR).glob('p*.json'):
            if f.stem[1:].isdigit() and (pv := self.page_preview(int(f.stem[1:]))):
                previews[pv['page']] = pv['blocks']
        if not previews:
            return blocks
        out, done = [], set()
        for x in blocks:
            p = x['page']
            if p in previews:
                if p not in done:
                    out += previews[p]
                    done.add(p)
            else:
                out.append(x)
        return out

    def preview_applied(self) -> set[int]:
        """Event seqs already visible in valid page previews (applied to their pages, not yet to the book)."""
        out: set[int] = set()
        for f in (self.dir / PREVIEW_DIR).glob('p*.json'):
            pv = self.page_preview(int(f.stem[1:]))
            if pv:
                out |= set(pv.get('applied') or [])
        return out

    def rebuild_estimate(self) -> float | None:
        """Seconds the next rebuild will probably take: the last measured one, else a rough guess from the number of
        processed pages (None for a book never built)."""
        m = read_json(self.dir / 'cache' / 'build.json')
        if m and m.get('seconds') is not None:
            return float(m['seconds'])
        c = self.canonical()
        return len(c['source']['pages_processed']) * config.BUILD_SECONDS_PER_PAGE_GUESS if c else None

    def pending(self) -> dict:
        """Reviewer actions recorded after the last rebuild (banked: in the event log, not yet in the outputs).
        `ocr`: how many of them need new OCR, done by the rebuild."""
        last = ((self.canonical() or {}).get('human') or {}).get('last_seq', 0)
        try:
            evs = [e for e in self.events.load() if e['seq'] > last]
        except Exception:                      # an invalid log is reported by the projection, not here
            evs = []
        return {'count': len(evs), 'ocr': sum(e['type'] in NEEDS_OCR for e in evs), 'events': evs[-200:],
                'estimate_s': self.rebuild_estimate(), 'page_applied': sorted(self.preview_applied())}

    def removed_pages(self) -> list[int]:
        """Pages the reviewer left out of the book (events page_remove/page_restore; reversible, raw OCR kept)."""
        try:
            return self.events.state()['removed_pages']
        except Exception:                      # an invalid log is reported by the projection, not here
            return []

    def summary(self) -> dict:
        c = self.canonical() or {}
        pages = self.pages()
        st = self.settings()
        return {'id': self.id, 'title': st['title'], 'ocr_lang': st['ocr_lang'], 'created': st.get('created'),
                'auto_diligent': st.get('auto_diligent', True), 'page_updates_paused': st['page_updates_paused'],
                'banks_actions': (est := self.rebuild_estimate()) is not None and est > config.AUTO_REBUILD_MAX_S,
                'calibrate_with': st.get('calibrate_with', []),
                'source': st.get('source'), 'page_count': sum(1 for p in pages if p['state'] != 'removed'),
                'removed': sum(1 for p in pages if p['state'] == 'removed'),
                'processed': sum(1 for p in pages if p['state'] not in ('not_processed', 'removed')),
                'required': sum(p['required'] for p in pages), 'recommended': sum(p['recommended'] for p in pages),
                'release_status': c.get('release_status'), 'built': (c.get('run') or {}).get('built_utc'),
                'updated': max((f.stat().st_mtime for f in (self.canonical_path, self.events.path) if f.exists()),
                               default=None)}


class Library:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def books(self) -> list[Book]:
        return [Book(d) for d in sorted(self.root.iterdir())
                if d.is_dir() and not d.name.startswith(('.', '_')) and (d / 'source' / 'original.pdf').exists()]

    def get(self, book_id: str) -> Book:
        if not ID_RE.match(book_id or '') or not (self.root / book_id / 'source' / 'original.pdf').exists():
            raise NotFound(book_id)
        return Book(self.root / book_id)

    def find_by_sha(self, sha: str) -> Book | None:
        return next((b for b in self.books() if (b.settings().get('source') or {}).get('sha256') == sha), None)

    def staging(self) -> Path:
        d = self.root / '_incoming'
        d.mkdir(exist_ok=True)
        return d

    def create_from_pdf(self, pdf: Path, filename: str, title: str | None = None) -> tuple[Book, bool]:
        """Register a PDF as a book (moved in, made read-only). Returns (book, created)."""
        sha = pdfio.sha256_file(pdf)
        existing = self.find_by_sha(sha)
        if existing:
            pdf.unlink(missing_ok=True)
            return existing, False
        info = pdfio.page_info(pdf)
        base = slug(title or Path(filename).stem)
        book_id, n = base, 2
        while (self.root / book_id).exists():
            book_id, n = f'{base}-{n}', n + 1
        d = self.root / book_id
        (d / 'source').mkdir(parents=True)
        shutil.move(str(pdf), d / 'source' / 'original.pdf')
        os.chmod(d / 'source' / 'original.pdf', stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        atomic_write(d / 'book.json', json.dumps({
            'id': book_id, 'title': title or Path(filename).stem, 'ocr_lang': config.DEFAULT_OCR_LANG,
            'created': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'source': {'filename': filename, 'sha256': sha, 'page_count': len(info)}}, ensure_ascii=False, indent=2))
        return Book(d), True

    def create_from_images(self, images: list[Path], title: str, names: list[str] | None = None,
                           order: str = 'name') -> tuple[Book, bool]:
        """Page images -> one book. `images` are IN PAGE ORDER (the caller decides it, see page_order); `names`
        are the original file names, recorded per page in book.json (source.page_files) for provenance."""
        names = list(names) if names else [p.name for p in images]
        if len(names) != len(images):
            raise ValueError('one name per image')
        pdf = self.staging() / f'{slug(title)}-{int(time.time())}.pdf'
        pages = pdfio.images_to_pdf(images, pdf)
        for im in images:
            im.unlink(missing_ok=True)
        book, created = self.create_from_pdf(pdf, f'{slug(title)}.pdf', title)
        if created:
            s = read_json(book.dir / 'book.json', {}) or {}
            s.setdefault('source', {}).update(page_files=names, page_order=order, page_dpi=[p['dpi'] for p in pages])
            atomic_write(book.dir / 'book.json', json.dumps(s, ensure_ascii=False, indent=2))
        return book, created

    def adopt(self, run_dir: Path) -> Book:
        """Register an existing pipeline output folder (pre-library layout) as a book in place."""
        b = Book(run_dir)
        if not (run_dir / 'book.json').exists():
            src = b.source_meta()
            atomic_write(run_dir / 'book.json', json.dumps({
                'id': run_dir.name, 'title': Path(src.get('input_path', run_dir.name)).stem,
                'ocr_lang': config.DEFAULT_OCR_LANG, 'created': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'source': {'filename': Path(src.get('input_path', '')).name, 'sha256': src.get('sha256'),
                           'page_count': src.get('page_count')}}, ensure_ascii=False, indent=2))
        return b

    def trash(self, book_id: str) -> str:
        b = self.get(book_id)
        t = self.root / '.trash'
        t.mkdir(exist_ok=True)
        name = f'{book_id}--{int(time.time())}'
        shutil.move(str(b.dir), t / name)
        return name

    def restore(self, trash_name: str) -> Book:
        src = self.root / '.trash' / trash_name
        book_id = trash_name.rsplit('--', 1)[0]
        if not src.is_dir() or (self.root / book_id).exists():
            raise NotFound(trash_name)
        shutil.move(str(src), self.root / book_id)
        return Book(self.root / book_id)


def natural_key(name: str) -> list[tuple]:
    """Natural order of file names: 'p2' before 'p10', case-insensitive; folders compare part by part.
    Typed tuples, so a digit run and a word never get compared with each other (no TypeError)."""
    return [(0, int(t), '') if t.isdigit() else (1, 0, t.casefold())
            for part in re.split(r'[\\/]', name) for t in re.split(r'(\d+)', part) if t]


def page_order(names: list[str]) -> list[int]:
    """Indices of `names` in page order (natural order of the original names; ties keep the given order)."""
    return sorted(range(len(names)), key=lambda i: natural_key(names[i]))
