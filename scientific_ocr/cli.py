"""Composition root and CLI: `python -m scientific_ocr <command>` (run with .venvs/docling/bin/python).

  ui        local app: library, upload, processing, review          (normal way to use the tool)
  process   process pages of a book folder (incremental, resumable)
  run       legacy: PDF + output folder -> process (same as the app does)
  reproject rebuild outputs from raw OCR + events (+ OCR of new reviewer regions); no page OCR
  render    rebuild outputs only (no models at all)
  doctor    real inference smoke tests of all five engines
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_LIBRARY = Path(__file__).resolve().parent.parent / 'library'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='scientific_ocr')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('ui')
    p.add_argument('--library', type=Path, default=DEFAULT_LIBRARY)
    p.add_argument('--port', type=int, default=8765)
    p = sub.add_parser('process')
    p.add_argument('--book', type=Path, required=True)
    p.add_argument('--pages', default=None)
    p = sub.add_parser('run')
    p.add_argument('pdf', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--pages', default=None)
    for name in ('reproject', 'render'):
        p = sub.add_parser(name)
        p.add_argument('--book', '--output', dest='book', type=Path, required=True)
    for name in ('propose-structure', 'propose-entities', 'propose-extents'):   # second passes with the local model: proposals only
        p = sub.add_parser(name)
        p.add_argument('--book', type=Path, required=True)
        p.add_argument('--samples', type=int, default=3)
        p.add_argument('--reproject', action='store_true', help='rebuild the projection first (after an accepted change)')
    p = sub.add_parser('doctor')
    p.add_argument('pdf', type=Path)
    p.add_argument('--page', type=int, default=3)
    p.add_argument('--work', type=Path, default=Path('smoke/readiness'))
    p.add_argument('--skip-layout', action='store_true')
    a = ap.parse_args(argv)

    if a.cmd == 'ui':
        from .adapters.http.server import serve
        from .adapters.storage import Library
        from .application.jobs import JobQueue
        from .application.services import App
        lib = Library(a.library)
        serve(App(lib, JobQueue(lib.root)), a.port)
        return 0
    if a.cmd in ('process', 'run'):
        from .application import processing
        out = (a.book if a.cmd == 'process' else a.output).resolve()
        return processing.process(out, None if a.cmd == 'process' else a.pdf.expanduser().resolve(), a.pages)
    if a.cmd == 'reproject':
        from .application import processing
        return processing.reproject(a.book.resolve())
    if a.cmd == 'render':
        from .application import projection
        from .application.processing import file_logger
        return projection.build(a.book.resolve(), file_logger(a.book.resolve()), allow_inference=False)
    if a.cmd in ('propose-structure', 'propose-entities', 'propose-extents') and a.reproject:
        from .application import processing
        processing.reproject(a.book.resolve())
    if a.cmd == 'propose-structure':
        from .application import structure_llm
        return structure_llm.run(a.book.resolve(), a.samples)
    if a.cmd == 'propose-extents':
        from .application import extent_llm
        return extent_llm.run(a.book.resolve(), a.samples)
    if a.cmd == 'propose-entities':
        from .application import entity_llm
        return entity_llm.run(a.book.resolve(), a.samples)
    if a.cmd == 'doctor':
        from . import readiness
        rep = readiness.run(a.pdf.expanduser().resolve(), a.page, a.work.resolve(), a.skip_layout)
        print(json.dumps({k: v.get('status') for k, v in rep['engines'].items()}, indent=2))
        return 0 if all(v.get('status') == 'READY' for v in rep['engines'].values()) else 1
    return 2
