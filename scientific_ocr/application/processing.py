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
from ..core import joblog
from ..pipeline import engine_json
from ..ports import Log
from . import projection

# Steps shown to the user, in order. Each writes progress.json via `progress_for(out, kind)`.
STEPS = {
    'process': ['prepare', 'extract', 'crossread', 'regions', 'compose', 'models', 'render'],
    'reproject': ['regions', 'compose', 'models', 'render'],
    'pages': ['compose'],
    'regions': ['regions', 'compose'],
    'structure': ['structure'],
    'entities': ['entities'],
    'extents': ['extents'],
}


def progress_for(out: Path, kind: str, **context):
    """A progress reporter for one run of `kind`. `context` is written with every report, e.g. the phase of a
    run (phase='diligent', phase_pages=[...]) so the UI can name it and mark its pages."""
    steps = STEPS[kind]

    def report(step: str, index: int = 0, total: int = 1, **detail):
        extraction.write_progress(out, kind=kind, steps=steps, step=step, stage=step, index=index, total=total,
                                  **context, **detail)
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
    log(joblog.fact('extraction', pages=len(pages), failed=failed))
    return res


def cross_read(out: Path, pages: list[int], log: Log, progress=None) -> int:
    """Before composition (and so before any review): every box of `pages` that only ONE engine found (Docling
    only or Marker only) is read by the engine that missed it (pipeline.crossread); ink no engine boxed is not. Idempotent: a region already read with the same box and language is not read again,
    and the engines' own cache is content-addressed. Returns the number of regions read."""
    from ..pipeline import crossread
    with book_lock(out, 'crossread'):
        lang = (read_json(out / 'book.json', {}) or {}).get('ocr_lang') or config.DEFAULT_OCR_LANG
        idx_path = out / 'raw' / 'crossread' / 'index.json'
        idx = read_json(idx_path, {}) or {}
        todo, recs = [], {}
        for i, ctx in enumerate(projection.page_contexts(out, pages, log)):
            if progress:
                progress('crossread', i, len(pages), page=ctx.page)
            for t in crossread.targets(ctx):
                old = idx.get(t['id']) or {}
                if old.get('read_bbox') == t['read_bbox'] and old.get('lang') == lang and \
                        all((old.get('engines') or {}).get(e, {}).get('status') == 'OK' for e in t['engines']):
                    continue
                todo.append(t)
                recs[t['id']] = {'engines': {'tesseract': crossread.tesseract(ctx, t)} if 'tesseract' in t['engines'] else {}}
        by_source = {s: sum(t['source'] == s for t in todo) for s in ('docling', 'marker')}
        log(f'cross-reading: {len(todo)} boxes found by one engine only {by_source}')
        if not todo:
            return 0
        crops = [t for t in todo if 'marker' in t['engines']]      # one Marker run over all of them
        if crops:
            read = extraction.regions_ocr(out, out / 'source' / 'original.pdf',
                                          [({'id': t['id'], 'page': t['page'], 'bbox': t['read_bbox']}, t['force_block']) for t in crops],
                                          log, lang, engines_of={t['id']: ('marker',) for t in crops})
            for t in crops:
                recs[t['id']] = {**read[t['id']], 'engines': {**recs[t['id']]['engines'], **read[t['id']]['engines']}}
        for t in todo:
            idx[t['id']] = {**t, **recs[t['id']], 'lang': lang}
        atomic_write(idx_path, json.dumps(idx, indent=2, default=str))
        failed = [(t['id'], e) for t in todo for e, r in recs[t['id']]['engines'].items() if r.get('status') != 'OK']
        log(f'cross-reading done; failed={failed}')
        if progress:
            progress('crossread', len(pages), len(pages))
        return len(todo)


def ocr_regions(out: Path, log: Log, progress=None) -> int:
    """OCR reviewer regions whose box, label or OCR language is new or changed (figure regions need no OCR).
    Serialised per book: a light rebuild and a heavy job may both get here, and must neither OCR the same region
    twice nor lose each other's entries in the shared index."""
    with book_lock(out, 'regions'):
        return _ocr_regions(out, log, progress)


def regions_to_ocr(out: Path, human: dict | None = None) -> list[tuple[dict, dict, dict]]:
    """Reviewer regions whose box, label or OCR language is new or changed: [(region, label, spec)]."""
    human = human or EventStore(out).state()
    book = read_json(out / 'book.json', {}) or {}
    idx = read_json(out / 'raw' / 'regions' / 'index.json', {}) or {}
    lang = book.get('ocr_lang') or config.DEFAULT_OCR_LANG
    todo = []
    for r in human['regions']:
        label = engine_json.region_label(r)
        spec = engine_json.region_spec(label)
        done = idx.get(r['id'], {})
        if not spec['image_only'] and (done.get('bbox') != r['bbox'] or done.get('label') != label
                                       or done.get('lang', lang) != lang):
            todo.append((r, label, spec))
    return todo


def _ocr_regions(out: Path, log: Log, progress=None) -> int:
    book = read_json(out / 'book.json', {}) or {}
    idx_path = out / 'raw' / 'regions' / 'index.json'
    idx = read_json(idx_path, {}) or {}
    lang = book.get('ocr_lang') or config.DEFAULT_OCR_LANG
    todo = regions_to_ocr(out)
    if not todo:
        return 0
    if progress:
        progress('regions', 0, len(todo), page=todo[0][0]['page'])
    for r, label, _ in todo:
        log(f'[region] {r["id"]} p{r["page"]} {r["bbox"]} label={label["engine"]}:{label["name"]}')
    # every pending region in ONE run per engine (an engine run costs mostly its model loading)
    recs = extraction.regions_ocr(out, out / 'source' / 'original.pdf', [(r, spec['force_block']) for r, _, spec in todo],
                                  log, book.get('ocr_lang'))
    for r, label, _ in todo:
        idx[r['id']] = {**recs[r['id']], 'bbox': r['bbox'], 'label': label, 'lang': lang,
                        'height': r['bbox'][3] - r['bbox'][1]}
    atomic_write(idx_path, json.dumps(idx, indent=2, default=str))
    if progress:
        progress('regions', len(todo), len(todo))
    return len(todo)


def process(out: Path, pdf: Path | None = None, pages_spec: str | None = None, log: Log | None = None) -> int:
    out.mkdir(parents=True, exist_ok=True)
    log = log or file_logger(out)
    progress = progress_for(out, 'process')
    progress('prepare')
    meta = prepare_source(pdf or out / 'source' / 'original.pdf', out, pages_spec, log, progress)
    extract_pages(out, meta['requested'], log, progress)
    cross_read(out, meta['requested'], log, progress)
    ocr_regions(out, log, progress)
    code = projection.build(out, log, progress=progress)
    retry = auto_diligent_pages(out, meta['requested'])
    if retry:                                        # spend compute before human time
        log(f'auto-diligent re-extraction of pages {retry} (many layout problems)')
        log(joblog.fact('diligent', pages=retry))
        es = EventStore(out)
        es.append(*[{'type': 'page_profile', 'page': p, 'profile': 'diligent', 'auto': True} for p in retry],
                  actor='system:auto-diligent')
        careful = progress_for(out, 'process', phase='diligent', phase_pages=retry)
        careful('extract', 0, len(retry))
        extract_pages(out, retry, log, careful)
        cross_read(out, retry, log, careful)
        code = projection.build(out, log, progress=careful)
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


def reproject_pages(out: Path, log: Log | None = None) -> int:
    """Fast path after PAGE-LOCAL actions in a book whose full rebuild is slow: rebuild only the pages those
    actions touched, as page previews (projection.build_page; ~1 s a page). Book-wide actions stay banked for the
    next full rebuild. New reviewer regions are NOT read here (that takes ~30 s): a region shows as its crop
    («OCR pending») until its own job, ocr_region_pages, has read it — so no click waits for a region's OCR."""
    import time as _t
    log = log or file_logger(out)
    progress = progress_for(out, 'pages')
    t0 = _t.perf_counter()
    pages = projection.dirty_pages(out)
    if len(pages) > config.PAGE_PREVIEW_MAX_PAGES:     # many pages at once (bulk actions): one full rebuild is cheaper
        log(f'{len(pages)} pages changed: full rebuild instead of page previews')
        code = reproject(out, log)
        log(joblog.fact('pages', pages=pages, seconds=round(_t.perf_counter() - t0, 1), full=True))
        return code
    code = 0
    for i, p in enumerate(pages):
        progress('compose', i, len(pages), page=p)
        c = projection.build_page(out, p, log)
        code = code or (c if c not in (0, 2) else 0)
    log(joblog.fact('pages', pages=pages, seconds=round(_t.perf_counter() - t0, 1)))
    progress('done', 1, 1, code=code)
    return code


def ocr_region_pages(out: Path, log: Log | None = None) -> int:
    """OCR the reviewer's new or changed regions (both engines at once), then refresh the previews of their pages.
    A job of its own beside reproject_pages: page updates elsewhere never wait for it."""
    import time as _t
    log = log or file_logger(out)
    progress = progress_for(out, 'regions')
    t0 = _t.perf_counter()
    pages = sorted({int(r['page']) for r, _, _ in regions_to_ocr(out)})
    ocr_regions(out, log, progress)
    code = 0
    if (read_json(out / 'canonical.json') or {}).get('run'):    # a built book: refresh the pages now
        for p in pages:
            c = projection.build_page(out, p, log)
            code = code or (c if c not in (0, 2) else 0)
    log(joblog.fact('regions', pages=pages, seconds=round(_t.perf_counter() - t0, 1)))
    progress('done', 1, 1, code=code)
    return code


def build_variants(out: Path, log: Log | None = None, force: bool = False) -> int:
    """The versions of the output profiles NOT shown (build_history.variant_dir), each with its own decisions and
    settings, so a profile switch shows its version at once. Only versions whose key changed are built (new OCR,
    another profile's decisions, settings, code); `force`: all of them (the manual «Προεπεξεργασία»). Runs after
    the shown book's build (it waits for it) and never holds the shown book: the reviewer never waits for it."""
    from ..core import profiles
    from . import build_history
    log = log or file_logger(out)
    with book_lock(out):                             # after the shown build that queued this one
        shown = EventStore(out).state()['scope']
    todo = [p for p in profiles.names() if p != shown and (force or build_history.variant_state(out, p) != 'current')]
    log(f'profile versions to build: {todo or "none (all up to date)"}')
    code = 0
    for p in todo:
        c = projection.build(out, log, profile=p)
        code = code or (0 if c in (0, 5) else c)
    return code


def reproject(out: Path, log: Log | None = None, with_regions: bool = True) -> int:
    """Cheap path after human input: OCR new reviewer regions (if any), then rebuild outputs."""
    log = log or file_logger(out)
    progress = progress_for(out, 'reproject')
    if with_regions:
        ocr_regions(out, log, progress)
    code = projection.build(out, log, allow_inference=True, progress=progress)
    progress('done', 1, 1, code=code)
    return code
