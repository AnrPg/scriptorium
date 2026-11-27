"""Health check: stops the app's processes that can never finish, and reports what it stopped.

A process is stopped only on evidence that it does no more work:
  finished_hung  a job of this app that printed `@@exit` (its work is done, see scientific_ocr/__main__.py) and is
                 still alive config.HEALTH_EXIT_GRACE_S later: stuck in its exit (native library teardown). Its job
                 ends with the code it reported, so the job is recorded as it really ended (nothing to redo).
  orphan         a job process of this app (`python -W ignore -m scientific_ocr <cmd>`) whose app is gone (parent
                 pid 1): nothing will ever record its result; when the app restarted its job became 'interrupted'.
                 A command typed in a terminal (no `-W ignore`, a shell parent) is never touched.
  engine_server  a helper server an interrupted extraction left behind (extraction.reap_engine_servers), only when
                 nothing could be using it: no job of any app instance and no Marker/Docling run on the machine.
A job that is merely slow is never stopped (a book's processing legitimately takes hours).

Runs when the app starts, every config.HEALTH_INTERVAL_S, and whenever the app is opened in a browser. Each run
that stopped something is kept in <library>/.health.json until the reviewer has seen it (shown in the browser).
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .. import config
from ..adapters import extraction
from ..adapters.storage import atomic_write, read_json

KEEP = 50
EXIT_MARK = re.compile(r'^@@exit (-?\d+) (\d+)\s*$', re.M)
ENGINE_RUN = re.compile(r'(?:^|/)(?:marker_single|docling)\s')
JOB_SIGNATURE = re.compile(r'python[\d.]*\s+-W\s+ignore\s+-m\s+scientific_ocr\s+(?!ui\b)([\w-]+)')


def _etime_s(s: str) -> int:
    """ps etime: [[dd-]hh:]mm:ss -> seconds."""
    days, _, rest = s.strip().rpartition('-')
    parts = [int(x) for x in rest.split(':')]
    while len(parts) < 3:
        parts.insert(0, 0)
    return int(days or 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def processes() -> list[dict]:
    out = subprocess.run(['ps', '-axo', 'pid=,ppid=,pgid=,etime=,%cpu=,command='], capture_output=True, text=True).stdout
    procs = []
    for line in out.splitlines():
        f = line.split(None, 5)
        if len(f) == 6:
            procs.append({'pid': int(f[0]), 'ppid': int(f[1]), 'pgid': int(f[2]), 'age_s': _etime_s(f[3]),
                          'cpu': float(f[4]), 'command': f[5]})
    return procs


def exit_reported(log: Path) -> tuple[int, float] | None:
    """(code, epoch) of the `@@exit` line at the end of a job's log, if the command finished its work."""
    try:
        with open(log, 'rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read().decode('utf-8', 'replace')
    except OSError:
        return None
    m = list(EXIT_MARK.finditer(tail))
    return (int(m[-1].group(1)), float(m[-1].group(2))) if m else None


def _alive(pid: int) -> bool:
    """Running (a zombie -- dead, not yet reaped by its parent, e.g. the job thread's p.wait() -- is not)."""
    stat = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(stat) and not stat.startswith('Z')


def stop_group(pid: int, pgid: int | None = None, grace: float = 5.0) -> bool:
    """SIGTERM then SIGKILL to the process group a job runs in (its own session, see jobs._execute)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            if pgid and pgid == pid:
                os.killpg(pgid, sig)
            else:
                os.kill(pid, sig)
        except ProcessLookupError:
            return True
        end = time.time() + grace
        while time.time() < end:
            if not _alive(pid):
                return True
            time.sleep(0.2)
    return not _alive(pid)


def _book_of(cmd: str) -> str | None:
    m = re.search(r'--book\s+(\S+)', cmd)
    return Path(m.group(1)).name if m else None


def _last_line(log: Path) -> str:
    try:
        lines = [l for l in log.read_text(encoding='utf-8', errors='replace').splitlines()[-40:]
                 if l.strip() and not l.startswith(('@@', '$ '))]
        return lines[-1][:240] if lines else ''
    except OSError:
        return ''


class Health:
    def __init__(self, library_root: Path, jobs, procs=processes, stop=stop_group, now=time.time,
                 reap=extraction.reap_engine_servers):
        self.root, self.jobs = library_root, jobs
        self.file = library_root / '.health.json'
        self._procs, self._stop, self._now, self._reap = procs, stop, now, reap
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- the check
    def check(self, trigger: str) -> dict | None:
        """One run. Returns the report when something was stopped (also stored, unseen), else None."""
        with self.lock:
            own = self.jobs.running()                       # pid -> job, the jobs this app runs now
            stopped, procs = [], self._procs()
            for p in procs:
                kind = JOB_SIGNATURE.search(p['command'])
                if not kind or p['pid'] == os.getpid():
                    continue
                job = own.get(p['pid'])
                if job:
                    rep = exit_reported(self.jobs.log_path(job))
                    if not rep or self._now() - rep[1] < config.HEALTH_EXIT_GRACE_S:
                        continue                            # working (however long it takes), or just finishing
                    self.jobs.report_exit(job['id'], rep[0])
                    item = self._item(p, kind.group(1), 'finished_hung', job=job, code=rep[0])
                elif p['ppid'] == 1:
                    job = self.jobs.find(_book_of(p['command']), kind.group(1), p['age_s'], self._now())
                    item = self._item(p, kind.group(1), 'orphan', job=job)
                else:
                    continue                                # another running app instance owns it
                item['stopped'] = self._stop(p['pid'], p['pgid'])
                stopped.append(item)
            gone = {x['pid'] for x in stopped}
            busy = any(p['pid'] not in gone and (JOB_SIGNATURE.search(p['command']) or ENGINE_RUN.search(p['command']))
                       for p in procs)                      # any app instance's job, any extraction, anywhere
            if not busy:
                for pid in self._reap(log=lambda m: None):
                    stopped.append({'reason': 'engine_server', 'pid': pid, 'stopped': True})
            if not stopped:
                return None
            report = {'id': uuid.uuid4().hex[:10], 'ts': self._now(), 'trigger': trigger, 'stopped': stopped, 'seen': False}
            reports = (read_json(self.file, []) or []) + [report]
            atomic_write(self.file, json.dumps(reports[-KEEP:], indent=1, ensure_ascii=False))
            return report

    def _item(self, p, cmd, reason, job=None, code=None) -> dict:
        log = self.jobs.log_path(job) if job else None
        return {'reason': reason, 'pid': p['pid'], 'command': cmd, 'book': (job or {}).get('book') or _book_of(p['command']),
                'job': (job or {}).get('id'), 'label': (job or {}).get('label'), 'age_s': p['age_s'], 'cpu': p['cpu'],
                'code': code, 'last_log': _last_line(log) if log else ''}

    # ---------------------------------------------------------------- reports for the browser
    def unseen(self) -> list[dict]:
        return [r for r in read_json(self.file, []) or [] if not r.get('seen')]

    def mark_seen(self, ids: list[str]) -> int:
        with self.lock:
            reports = read_json(self.file, []) or []
            n = 0
            for r in reports:
                if r['id'] in ids and not r.get('seen'):
                    r['seen'], n = True, n + 1
            if n:
                atomic_write(self.file, json.dumps(reports, indent=1, ensure_ascii=False))
            return n

    def start(self, interval: float | None = None):
        """At start and every `interval` seconds, in the background."""
        interval = interval or config.HEALTH_INTERVAL_S

        def loop():
            trigger = 'start'
            while True:
                try:
                    self.check(trigger)
                except Exception as e:                       # a failed check must never stop the app
                    print(f'health check failed: {e}', flush=True)
                trigger = 'periodic'
                time.sleep(interval)
        threading.Thread(target=loop, daemon=True, name='health').start()
