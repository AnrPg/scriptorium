"""Shared base of the use-case services: repository access, the job queue, and the error type."""
from __future__ import annotations

import contextlib
import threading
import time
from datetime import datetime, timezone

from ... import config
from ...core.decisions import page_of
from ...adapters import local_llm
from ...adapters.storage import Book, Library
from ..jobs import JobQueue


_uow = threading.local()          # the open batch of the current request thread (see ServiceBase._batch)


class ServiceError(ValueError):
    pass


PROFILES = ('standard', 'diligent')


class ServiceBase:
    def __init__(self, library: Library, jobs: JobQueue, interpreter=None):
        self.lib, self.jobs = library, jobs
        self._interpreter = interpreter          # ports.NoteInterpreter; None -> resolved lazily from config

    def interpreter(self):
        """The local model, if installed. A miss is re-checked after a minute (a model installed while the app runs
        is picked up without a restart)."""
        now = time.monotonic()
        if self._interpreter is None or (self._interpreter is False and now - getattr(self, '_interp_checked', 0) > 60):
            self._interp_checked = now
            self._interpreter = local_llm.interpreter() or False
        return self._interpreter or None

    # ---------------------------------------------------------------- helpers
    def _book(self, book_id: str) -> Book:
        try:
            return self.lib.get(book_id)
        except KeyError:
            raise ServiceError(f'unknown book {book_id!r}')

    def _seen_list(self, b: Book) -> list[dict]:
        """Every segment as the reviewer SEES it, in reading order: the full projection with each page rebuilt
        since (page preview) in its place. Reviewer ACTIONS resolve their segments and targets here — a merge,
        region or caption made a minute ago exists in the page preview before the next full rebuild."""
        return b.seen_blocks()

    def _blocks_seen(self, b: Book) -> dict[str, dict]:
        return {x['id']: x for x in self._seen_list(b)}

    def _block(self, b: Book, block_id: str) -> dict:
        """The segment as the reviewer sees it: from its page preview when there is one (decisions validate and
        pin what is on screen), else from the full projection."""
        page = page_of(block_id)
        pv = b.page_preview(page) if page else None
        blk = next((x for x in pv['blocks'] if x['id'] == block_id), None) if pv else None
        blk = blk or next((x for x in (b.canonical() or {}).get('blocks', []) if x['id'] == block_id), None)
        if blk is None:
            raise ServiceError('unknown block (the page may have been rebuilt; reload)')
        return blk

    def _reproject(self, b: Book, label='Ενημέρωση αποτελέσματος', force: bool = False):
        """Apply recorded actions to the book's outputs. A rebuild runs at once only for books that rebuild quickly
        (config.AUTO_REBUILD_MAX_S); otherwise the actions stay BANKED (Book.pending) until the reviewer rebuilds
        (`rebuild`), so no click ever waits minutes for a whole-book rebuild."""
        if getattr(_uow, 'events', None) is not None:          # inside a batch: one rebuild at the end
            return None
        est = b.rebuild_estimate()
        if not force and est is not None and est > config.AUTO_REBUILD_MAX_S:
            return self._update_pages(b)            # banked for the book; touched pages get a preview at once
        return self.jobs.submit(b.id, 'reproject', ['reproject', '--book', str(b.dir)], label, lane='light',
                                then=variants_job(b))

    def _update_pages(self, b: Book):
        """Rebuild, as page previews, only the pages a PAGE-LOCAL action changed (projection.dirty_pages). Nothing
        runs for a book-wide action (no page can show it before the full rebuild) or while the reviewer paused page
        updates (book.json page_updates_paused: quick edits now, one book rebuild at the end)."""
        if b.settings().get('page_updates_paused'):
            return None
        from ..projection import dirty_pages            # lazy: the projection pulls in the whole pipeline
        from ..processing import regions_to_ocr
        pages = dirty_pages(b.dir, b.canonical())
        if not pages:
            return None
        job = self.jobs.submit(b.id, 'pages', ['reproject-pages', '--book', str(b.dir)], _pages_label(pages), lane='light')
        # a new/changed region also needs OCR (~30 s): its own job, beside the page update, which never waits for it
        ocr = sorted({int(r['page']) for r, _, _ in regions_to_ocr(b.dir)})
        if ocr and len(pages) <= config.PAGE_PREVIEW_MAX_PAGES:     # (more: reproject_pages rebuilds the book, OCR incl.)
            self.jobs.submit(b.id, 'regions', ['ocr-regions', '--book', str(b.dir)], _ocr_label(ocr), lane='light')
        return job

    def rebuild(self, book_id: str) -> dict:
        """Apply every banked action now (one rebuild for all of them)."""
        b = self._book(book_id)
        n = b.pending()['count']
        return self._reproject(b, f'Ενημέρωση βιβλίου ({n} αλλαγ{"ή" if n == 1 else "ές"})' if n else 'Ενημέρωση βιβλίου', force=True)

    def _append(self, b: Book, *events: dict, actor='reviewer') -> list[dict]:
        """Append reviewer events, or collect them when a batch is open (several use cases → ONE undoable step)."""
        if getattr(_uow, 'events', None) is not None:
            _uow.events.extend(events)
            return [dict(e) for e in events]
        return b.events.append(*events, actor=actor)

    @contextlib.contextmanager
    def _batch(self):
        """Unit of work for this request thread: use cases called inside it only collect their events; the caller
        appends them once (one group, one undo) and queues one rebuild."""
        _uow.events = []
        try:
            yield _uow.events
        finally:
            _uow.events = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')



def variants_job(b: Book, force: bool = False) -> dict:
    """The follow-up of a shown-book build: the versions of the other output profiles, in the background (light
    lane: never queued behind OCR, never delaying the reviewer's next update; processing.build_variants)."""
    return {'kind': 'variants', 'args': ['variants', '--book', str(b.dir), *(['--all'] if force else [])],
            'label': 'Προεπεξεργασία των άλλων προφίλ (στο παρασκήνιο)', 'lane': 'light'}


def _pages_label(pages: list[int]) -> str:
    """Job label naming the pages being updated: «Σελίδα 169: ενημέρωση», «Σελίδες 12, 13: ενημέρωση»."""
    if len(pages) > getattr(config, 'PAGE_PREVIEW_MAX_PAGES', len(pages)):    # reproject_pages rebuilds the book
        return f'Ενημέρωση βιβλίου ({len(pages)} σελίδες άλλαξαν)'
    if len(pages) == 1:
        return f'Σελίδα {pages[0]}: ενημέρωση'
    shown = ', '.join(map(str, pages[:3])) + (f' … ({len(pages)})' if len(pages) > 3 else '')
    return f'Σελίδες {shown}: ενημέρωση'


def _ocr_label(pages: list[int]) -> str:
    """«Σελίδα 406: OCR νέας περιοχής (~30″)» — says why that job takes longer than a page update."""
    where = f'Σελίδα {pages[0]}' if len(pages) == 1 else 'Σελίδες ' + ', '.join(map(str, pages[:3])) + (' …' if len(pages) > 3 else '')
    return f'{where}: OCR νέας περιοχής (~30″)'
