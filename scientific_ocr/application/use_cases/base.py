"""Shared base of the use-case services: repository access, the job queue, and the error type."""
from __future__ import annotations

import contextlib
import threading
import time
from datetime import datetime, timezone

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

    def _block(self, b: Book, block_id: str) -> dict:
        blk = next((x for x in (b.canonical() or {}).get('blocks', []) if x['id'] == block_id), None)
        if blk is None:
            raise ServiceError('unknown block (the page may have been rebuilt; reload)')
        return blk

    def _reproject(self, b: Book, label='Ενημέρωση αποτελέσματος'):
        if getattr(_uow, 'events', None) is not None:          # inside a batch: one rebuild at the end
            return None
        return self.jobs.submit(b.id, 'reproject', ['reproject', '--book', str(b.dir)], label, lane='light')

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

