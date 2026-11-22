"""Use case: process a book (source retention -> page extraction -> region OCR -> projection).

Incremental and resumable: processing more pages ADDS them (pages_processed is a union); every expensive
artifact is content-addressed, so re-running, resuming after a stop, or re-rendering never redoes finished OCR.
Stopping is safe at any point because every stage publishes its output atomically.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import datetime
from pathlib import Path

from .. import config
from ..adapters import extraction
from ..adapters import pdf as pdfio
from ..adapters.event_store import EventStore
from ..adapters.storage import atomic_write, book_lock, read_json
from ..core.reasons import REASONS
from ..pipeline import engine_json
from ..ports import Log
from . import projection

# Steps shown to the user, in order. Each writes progress.json via `progress_for(out, kind)`.
STEPS = {
    'process': ['prepare', 'extract', 'regions', 'compose', 'models', 'render'],
    'reproject': ['regions', 'compose', 'models', 'render'],
    'structure': ['structure'],
    'entities': ['entities'],
    'extents': ['extents'],
}


def progress_for(out: Path, kind: str):
    steps = STEPS[kind]

    def report(step: str, index: int = 0, total: int = 1, **detail):
        extraction.write_progress(out, kind=kind, steps=steps, step=step, stage=step, index=index, total=total,
                                  **detail)
    return report


def file_logger(out: Path) -> Log:
    logf = out / 'logs' / 'pipeline.log'
    logf.parent.mkdir(parents=True, exist_ok=True)

    def log(msg: str):
        line = f'{datetime.now().isoformat(timespec="seconds")} {msg}'
        print(line, flush=True)
        with open(logf, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    return log


def prepare_source(pdf: Path, out: Path, pages_spec: str | None, log: Log, progress=None) -> dict:
    """Retain an immutable, hash-verified copy of the PDF; merge requested pages into pages_processed."""
    sha = pdfio.sha256_file(pdf)
    kept = out / 'source' / 'original.pdf'
    kept.parent.mkdir(parents=True, exist_ok=True)
    if not kept.exists():
        shutil.copy2(pdf, kept)
        os.chmod(kept, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    if pdfio.sha256_file(kept) != sha:
        raise SystemExit(f'retained copy {kept} does not match input hash; refusing to continue')
    info = pdfio.page_info(kept)
    requested = pdfio.parse_pages(pages_spec, len(info))
    prev = read_json(out / 'source' / 'source.json', {}) or {}
    pages = sorted(set(prev.get('pages_processed', [])) | set(requested))
    rasters = {}
    for i, p in enumerate(pages):
        if progress:
            progress('prepare', i, len(pages), page=p)
        dest = out / 'source' / 'pages' / f'p{p:04d}.png'
        old = (prev.get('rasters') or {}).get(str(p)) or {}
        # reuse a raster only when it is known to be complete and rendered at the current zoom (render_page writes
        # atomically, so an interrupted render never leaves a truncated file behind)
        if dest.exists() and old.get('zoom') == config.RENDER_ZOOM and old.get('sha256') == pdfio.sha256_file(dest):
            r = dict(old, path=str(dest))
        else:
            r = pdfio.render_page(kept, p, dest)
        r['path'] = str(Path(r['path']).relative_to(out))
        rasters[str(p)] = r
    meta = {'input_path': prev.get('input_path') or str(pdf), 'retained_copy': 'source/original.pdf', 'sha256': sha,
            'page_count': len(info), 'pages_processed': pages,
            'page_dims_pt': {str(p['page']): [p['width'], p['height']] for p in info if p['page'] in pages},
            'text_layer': {str(p['page']): p['has_text_layer'] for p in info if p['page'] in pages},
            'rasters': rasters, 'coord_system': 'PDF points, origin TOPLEFT, bbox [x0,y0,x1,y1]; pages 1-based'}
    atomic_write(out / 'source' / 'source.json', json.dumps(meta, indent=2))
    log(f'source sha256={sha[:12]} requested={requested} processed={pages}')
    return {**meta, 'requested': requested}


def extract_pages(out: Path, pages: list[int], log: Log, progress=None) -> dict:
    book = read_json(out / 'book.json', {}) or {}
    human = EventStore(out).state()
    src = read_json(out / 'source' / 'source.json')
    res = extraction.extract(out / src['retained_copy'], src['sha256'], pages, out, log=log,
                             lang=book.get('ocr_lang'), profiles=human['profiles'], progress=progress)
    idx = read_json(out / 'raw' / 'extraction_index.json', {}) or {}
    for eng, recs in res.items():
        idx.setdefault(eng, {}).update({str(p): r for p, r in recs.items()})
    atomic_write(out / 'raw' / 'extraction_index.json', json.dumps(idx, indent=2, default=str))
    failed = [(e, p) for e, d in res.items() for p, r in d.items() if r['status'] != 'OK']
    log(f'extraction done; failed={failed}')
    return res


def ocr_regions(out: Path, log: Log, progress=None) -> int:
    """OCR reviewer regions whose box, label or OCR language is new or changed (figure regions need no OCR).
    Serialised per book: a light rebuild and a heavy job may both get here, and must neither OCR the same region
    twice nor lose each other's entries in the shared index."""
    with book_lock(out, 'regions'):
        return _ocr_regions(out, log, progress)


def _ocr_regions(out: Path, log: Log, progress=None) -> int:
    human = EventStore(out).state()
    book = read_json(out / 'book.json', {}) or {}
    idx_path = out / 'raw' / 'regions' / 'index.json'
    idx = read_json(idx_path, {}) or {}
    lang = book.get('ocr_lang') or config.DEFAULT_OCR_LANG
    todo = []
    for r in human['regions']:
        label = engine_json.region_label(r)
        spec = engine_json.region_spec(label)
        done = idx.get(r['id'], {})
        if not spec['image_only'] and (done.get('bbox') != r['bbox'] or done.get('label') != label
                                       or done.get('lang', lang) != lang):
            todo.append((r, label, spec))
    for i, (r, label, spec) in enumerate(todo):
        if progress:
            progress('regions', i, len(todo), page=r['page'])
        log(f'[region] {r["id"]} p{r["page"]} {r["bbox"]} label={label["engine"]}:{label["name"]}')
        rec = extraction.region_ocr(out, out / 'source' / 'original.pdf', r, spec['force_block'], log,
                                    book.get('ocr_lang'))
        idx[r['id']] = {**rec, 'bbox': r['bbox'], 'label': label, 'lang': lang, 'height': r['bbox'][3] - r['bbox'][1]}
        atomic_write(idx_path, json.dumps(idx, indent=2, default=str))
    return len(todo)


def process(out: Path, pdf: Path | None = None, pages_spec: str | None = None, log: Log | None = None) -> int:
    out.mkdir(parents=True, exist_ok=True)
    log = log or file_logger(out)
    progress = progress_for(out, 'process')
    progress('prepare')
    meta = prepare_source(pdf or out / 'source' / 'original.pdf', out, pages_spec, log, progress)
    extract_pages(out, meta['requested'], log, progress)
    ocr_regions(out, log, progress)
    code = projection.build(out, log, progress=progress)
    retry = auto_diligent_pages(out, meta['requested'])
    if retry:                                        # spend compute before human time
        log(f'auto-diligent re-extraction of pages {retry} (many layout problems)')
        es = EventStore(out)
        es.append(*[{'type': 'page_profile', 'page': p, 'profile': 'diligent', 'auto': True} for p in retry],
                  actor='system:auto-diligent')
        extract_pages(out, retry, log, progress)
        code = projection.build(out, log, progress=progress)
    progress('done', 1, 1, code=code)
    return code


AUTO_DILIGENT_MIN_LAYOUT_FLAGS = 3


def auto_diligent_pages(out: Path, pages: list[int]) -> list[int]:
    """Pages just processed with >= N mandatory layout problems, never re-examined before (by a human or the
    system), when the book allows it (book.json auto_diligent, default true)."""
    if not (read_json(out / 'book.json', {}) or {}).get('auto_diligent', True):
        return []
    c = read_json(out / 'canonical.json', {}) or {}
    profiled = EventStore(out).state()['profiles']
    counts: dict[int, int] = {}
    for b in c.get('blocks', []):
        if b['review'] == 'required' and any(REASONS.get(r) and REASONS[r].stage == 'layout' and REASONS[r].review == 'required'
                                             for r in b['reasons']):
            counts[b['page']] = counts.get(b['page'], 0) + 1
    return sorted(p for p in pages if counts.get(p, 0) >= AUTO_DILIGENT_MIN_LAYOUT_FLAGS and p not in profiled)


def reproject(out: Path, log: Log | None = None, with_regions: bool = True) -> int:
    """Cheap path after human input: OCR new reviewer regions (if any), then rebuild outputs."""
    log = log or file_logger(out)
    progress = progress_for(out, 'reproject')
    if with_regions:
        ocr_regions(out, log, progress)
    code = projection.build(out, log, allow_inference=True, progress=progress)
    progress('done', 1, 1, code=code)
    return code
