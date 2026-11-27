"""Job queue (producer/consumer) for long-running work, isolated in subprocesses.

Two lanes:
  heavy  - page extraction / full processing: one at a time (CPU/GPU-bound), FIFO
  light  - re-projection after human input: per book, coalesced (many clicks -> one rebuild), runs alongside
Cancellation kills the job's whole process group (Marker spawns helpers). Because every stage writes
atomically and caches by content, a cancelled job simply resumes where it stopped next time.
Job history persists in <library>/.jobs.json; jobs alive at server shutdown are marked 'interrupted'.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from .. import config
from ..adapters import extraction
from ..adapters.storage import NotFound, atomic_write, read_json

HISTORY = 200
# Exit codes that mean "outputs were written, but something needs attention" (not a failed job).
# 5 = the projection's integrity audit raised a critical finding (application.projection._publish).
WARNING_EXITS = {5: 'integrity'}


class JobQueue:
    def __init__(self, library_root: Path):
        self.root = library_root
        self.file = library_root / '.jobs.json'
        self.lock = threading.Condition()
        self.jobs: dict[str, dict] = {}
        self.heavy: deque[str] = deque()
        self.procs: dict[str, subprocess.Popen] = {}
        self.light_dirty: set[str] = set()
        for j in read_json(self.file, []) or []:
            if j['state'] in ('queued', 'running'):
                j['state'] = 'interrupted'
            self.jobs[j['id']] = j
        threading.Thread(target=self._heavy_worker, daemon=True, name='jobs-heavy').start()

    # ---------------------------------------------------------------- public API
    def submit(self, book_id: str, kind: str, args: list[str], label: str, lane: str = 'heavy',
               then: dict | None = None) -> dict:
        """`then`: a follow-up job (submit's own arguments) submitted when this one ENDS WELL — work that must come
        after it but must not delay it (e.g. the other output profiles' versions after the shown book's build)."""
        with self.lock:
            if lane == 'light':
                running = self._active(book_id, 'light', kind)
                if running:                                   # coalesce: rerun once when the current one ends
                    self.light_dirty.add((book_id, kind))
                    if then:
                        running['then'] = then
                    return running
            job = {'id': uuid.uuid4().hex[:10], 'book': book_id, 'kind': kind, 'label': label, 'lane': lane,
                   'args': args, 'state': 'queued', 'created': time.time(), 'started': None, 'finished': None,
                   'code': None, **({'then': then} if then else {})}
            self.jobs[job['id']] = job
            if lane == 'heavy':
                self.heavy.append(job['id'])
                self.lock.notify_all()
            else:
                threading.Thread(target=self._run_light, args=(job,), daemon=True).start()
            self._persist()
            return dict(job)

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise NotFound(job_id)
            if job['state'] == 'queued':
                if job_id in self.heavy:
                    self.heavy.remove(job_id)
                    job.update(state='cancelled', finished=time.time())
                else:                                 # a coalesced light rerun about to start: stop it when it does
                    job['cancel_requested'] = True
            elif job['state'] == 'running':
                job['cancel_requested'] = True
                p = self.procs.get(job_id)
                if p:
                    threading.Thread(target=_stop_group, args=(p,), daemon=True).start()
            self._persist()
            return dict(job)

    def remove(self, job_id: str) -> dict:
        """Forget a finished job completely: its history entry and its log file."""
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise NotFound(job_id)
            if job['state'] in ('queued', 'running'):
                raise ValueError('stop the job before removing it')
            del self.jobs[job_id]
            self._persist()
        self.log_path(job).unlink(missing_ok=True)
        return dict(job)

    def clear(self, book_id: str | None = None) -> int:
        with self.lock:
            ids = [i for i, j in self.jobs.items() if book_id in (None, j['book']) and j['state'] not in ('queued', 'running')]
        for i in ids:
            try:
                self.remove(i)
            except (NotFound, ValueError):          # started or removed meanwhile
                pass
        return len(ids)

    def list(self, book_id: str | None = None) -> list[dict]:
        with self.lock:
            js = [dict(j) for j in self.jobs.values() if book_id in (None, j['book'])]
        return sorted(js, key=lambda j: j['created'], reverse=True)

    def busy(self, book_id: str) -> dict | None:
        with self.lock:
            return self._active(book_id, 'heavy') or self._active(book_id, 'light')

    def active(self, book_id: str, lane: str) -> dict | None:
        with self.lock:
            j = self._active(book_id, lane)
            return dict(j) if j else None

    def log_path(self, job: dict) -> Path:
        return self.root / job['book'] / 'logs' / f'job-{job["id"]}.log'

    # ---------------------------------------------------------------- workers
    def _active(self, book_id, lane, kind=None):
        return next((j for j in self.jobs.values() if j['book'] == book_id and j['lane'] == lane
                     and (kind is None or j['kind'] == kind) and j['state'] in ('queued', 'running')), None)

    def _heavy_worker(self):
        while True:
            with self.lock:
                while not self.heavy:
                    self.lock.wait()
                job = self.jobs[self.heavy.popleft()]
            self._execute(job)

    def _run_light(self, job):
        while True:
            self._execute(job)
            with self.lock:
                if (job['book'], job['kind']) not in self.light_dirty:
                    return
                self.light_dirty.discard((job['book'], job['kind']))
                job = {k: v for k, v in job.items() if k not in ('cancel_requested', 'started', 'finished', 'code', 'error')}
                job.update(id=uuid.uuid4().hex[:10], state='queued', created=time.time())
                self.jobs[job['id']] = job

    def _execute(self, job):
        logf = self.log_path(job)
        logf.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, '-W', 'ignore', '-m', 'scientific_ocr', *job['args']]
        with self.lock:
            if job.get('cancel_requested'):            # cancelled while still queued
                job.update(state='cancelled', finished=time.time())
                self._persist()
                return
            job.update(state='running', started=time.time())
            self._persist()
        with open(logf, 'a', encoding='utf-8') as f:
            f.write(f'$ {" ".join(cmd)}\n')
            f.flush()
            p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=config.ROOT,
                                 start_new_session=True, env=config.subprocess_env())
            with self.lock:
                self.procs[job['id']] = p
            code = p.wait()
        with self.lock:
            self.procs.pop(job['id'], None)
            state = 'cancelled' if job.get('cancel_requested') else ('done' if code == 0 or code in WARNING_EXITS else 'failed')
            job.update(state=state, code=code, finished=time.time())
            if state == 'done' and code in WARNING_EXITS:
                job['warning'] = WARNING_EXITS[code]
            self._persist()
            idle = not self.procs
        if idle and state != 'done':
            # an interrupted engine may have left helper servers behind (see extraction.reap_engine_servers)
            extraction.reap_engine_servers(log=lambda m: _append_line(logf, m))
        if state == 'done' and job.get('then'):
            self.submit(job['book'], **job['then'])

    def _persist(self):
        keep = sorted(self.jobs.values(), key=lambda j: j['created'])[-HISTORY:]
        atomic_write(self.file, json.dumps(keep, indent=1))


def _append_line(path, line: str):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def _stop_group(p: subprocess.Popen, grace: float = 6.0):
    """Escalating stop of a job's process group: SIGINT lets Python run cleanup handlers (engines stop their
    helper servers), then SIGTERM, then SIGKILL."""
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(p.pid), sig)
        except ProcessLookupError:
            return
        try:
            p.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue
