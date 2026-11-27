"""Phase B: independent per-page Marker and Docling extraction with content-addressed caching.

Raw outputs are written once into raw/<engine>/p0003-<key>/ and never modified afterwards.
A `_DONE.json` records command, exit code, timing and versions. A failed page is recorded as
FAILED (never as an empty page) and other pages continue.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path

from .. import config
from . import pdf as pdfio
from .storage import atomic_write


def engine_version(engine: str) -> str:
    pkg = {'marker': ('marker', 'marker-pdf'), 'docling': ('docling', 'docling')}[engine]
    p = subprocess.run([str(config.PY[pkg[0]]), '-c',
                        f'import importlib.metadata as m; print(m.version({pkg[1]!r}))'],
                       capture_output=True, text=True)
    return p.stdout.strip() or 'unknown'


def cache_key(pdf_sha: str, page: int, engine: str, version: str, args: list[str]) -> str:
    blob = json.dumps({'pdf': pdf_sha, 'page': page, 'engine': engine, 'version': version, 'args': args})
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def ocr_dpi(native: float | None, default: int) -> int:
    """OCR resolution for a page: the engine's default, raised to the scan's own density (config.OCR_MAX_DPI cap),
    rounded up to 10 dpi so cache keys stay stable."""
    if not native or native <= default:
        return default
    return int(math.ceil(min(native, config.OCR_MAX_DPI) / 10) * 10)


def _resolution_args(engine: str, profile_args: list[str], native: float | None) -> list[str]:
    """Profile args with the OCR resolution raised to the scan's density (see config.OCR_MAX_DPI). Unchanged — and
    so are the cache keys — for scans coarser than the engine's default."""
    if engine == 'marker':
        args = list(profile_args)
        cur = config.MARKER_DEFAULT_OCR_DPI
        if '--highres_image_dpi' in args:
            i = args.index('--highres_image_dpi')
            cur = int(args[i + 1])
            del args[i:i + 2]
        dpi = max(cur, ocr_dpi(native, cur))
        return args + (['--highres_image_dpi', str(dpi)] if dpi != config.MARKER_DEFAULT_OCR_DPI else [])
    dpi = ocr_dpi(native, config.DOCLING_DEFAULT_OCR_DPI)
    return ['--ocr-option', f'scale={dpi / 72:.3f}'] if dpi != config.DOCLING_DEFAULT_OCR_DPI else []


def _command(engine: str, pdf: Path, page: int, out_dir: Path, lang: str | None = None,
             profile: str = 'standard', marker_extra: list[str] | None = None, native: float | None = None,
             last: int | None = None) -> list[str]:
    """The engine's command for `page` (or the range page..last: several pages in ONE run, one model load)."""
    last = last or page
    if engine == 'marker':
        # Marker page ranges are ZERO-based.
        rng = str(page - 1) if last == page else f'{page - 1}-{last - 1}'
        return [str(config.MARKER_BIN), str(pdf), '--page_range', rng, *config.MARKER_ARGS,
                *_resolution_args('marker', config.PROFILES[profile], native), *(marker_extra or []),
                '--output_dir', str(out_dir)]
    # Docling page ranges are ONE-based.
    return [str(config.DOCLING_BIN), 'convert', str(pdf), '--page-range', f'{page}-{last}', *config.docling_args(lang),
            *_resolution_args('docling', [], native),
            '--enrich-picture-classes', '--artifacts-path', str(config.CKPT['docling']), '--output', str(out_dir)]


def find_raw_json(engine: str, run_dir: Path) -> Path | None:
    files = sorted(run_dir.rglob('*.json'))
    files = [f for f in files if f.name != '_DONE.json' and not f.name.endswith('_meta.json')]
    return files[0] if files else None


def _run_dir(engine, pdf, pdf_sha, page, raw_root, version, lang, profile, marker_extra, subdir, last=None):
    """(run directory, native dpi) of an extraction: content-addressed (pdf content, pages, engine, version, args)."""
    native = pdfio.native_dpi(pdf, page)
    args = _command(engine, Path('PDF'), page, Path('OUT'), lang, profile, marker_extra, native, last)[1:]  # no paths
    key = cache_key(pdf_sha, page if not last or last == page else [page, last], engine, version, args)
    return raw_root / (subdir or engine) / f'p{page:04d}-{key}', native, key


def cached_page(engine: str, pdf: Path, pdf_sha: str, page: int, raw_root: Path, version: str, lang=None,
                profile: str = 'standard', marker_extra=None, subdir: str | None = None) -> dict | None:
    """The cached successful extraction of this page with these settings, or None (nothing is run)."""
    run_dir, _, _ = _run_dir(engine, pdf, pdf_sha, page, raw_root, version, lang, profile, marker_extra, subdir)
    done = run_dir / '_DONE.json'
    if done.exists():
        rec = json.loads(done.read_text())
        if rec['status'] == 'OK':
            return {**rec, 'cached': True}
    return None


def extract_page(engine: str, pdf: Path, pdf_sha: str, page: int, raw_root: Path, version: str,
                 log=print, lang: str | None = None, profile: str = 'standard', marker_extra=None,
                 subdir: str | None = None, last: int | None = None) -> dict:
    """Extract `page` (or pages page..last in ONE engine run) into a content-addressed run directory."""
    run_dir, native, key = _run_dir(engine, pdf, pdf_sha, page, raw_root, version, lang, profile, marker_extra, subdir, last)
    done = run_dir / '_DONE.json'
    if done.exists():
        rec = json.loads(done.read_text())
        if rec['status'] == 'OK':
            rec['cached'] = True
            return rec
        # previous failure: retry this page only
    tmp = run_dir.with_name(run_dir.name + '.partial')
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    cmd = _command(engine, pdf, page, tmp, lang, profile, marker_extra, native, last)
    log(f'[{engine}] page {page}{f"-{last}" if last and last != page else ""}: {" ".join(cmd)}')
    t0 = time.time()
    with open(tmp / 'process.log', 'w') as lf:
        try:
            p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, env=config.subprocess_env(), timeout=3600)
            code = p.returncode
        except subprocess.TimeoutExpired:
            code = 'TIMEOUT'
    raw = find_raw_json(engine, tmp)
    status = 'OK' if code == 0 and raw is not None else 'FAILED'
    rec = {'engine': engine, 'version': version, 'page': page, 'status': status, 'exit_code': code,
           'profile': profile, 'lang': lang or config.DEFAULT_OCR_LANG,
           'command': cmd, 'seconds': round(time.time() - t0, 1), 'cache_key': key,
           'offline_env': config.OFFLINE_ENV}
    if run_dir.exists():                          # stale failed attempt
        shutil.rmtree(run_dir)
    os.replace(tmp, run_dir)                      # atomic publish of the whole directory
    rec['raw_json'] = str(find_raw_json(engine, run_dir).relative_to(raw_root.parent)) if status == 'OK' else None
    rec['log'] = str((run_dir / 'process.log').relative_to(raw_root.parent))
    done.write_text(json.dumps(rec, indent=2))
    rec['cached'] = False
    if status != 'OK':
        log(f'[{engine}] page {page}: FAILED (exit {code}); see {run_dir / "process.log"}')
    return rec


def write_progress(out: Path, **kw):
    p = out / 'progress.json'
    atomic_write(p, json.dumps({'updated': time.time(), **kw}))


def extract(pdf: Path, pdf_sha: str, pages: list[int], out: Path, engines=('marker', 'docling'), log=print,
            lang: str | None = None, profiles: dict | None = None, progress=None) -> dict:
    """Page-major order so each finished page is usable (and progress is meaningful) before the next starts."""
    raw_root = out / 'raw'
    results: dict = {}
    versions = {e: engine_version(e) for e in engines}
    for i, page in enumerate(pages):
        for engine in engines:
            if progress:
                progress('extract', i, len(pages), engine=engine, page=page)
            results.setdefault(engine, {})[page] = extract_page(
                engine, pdf, pdf_sha, page, raw_root, versions[engine], log, lang,
                (profiles or {}).get(page, 'standard'))
    return results


def region_pdf(src_pdf: Path, page: int, bbox, dest: Path, zoom: float = config.REGION_ZOOM) -> Path:
    """Crop a region of the ORIGINAL page pixels into a one-page PDF (page size = region size in pt)."""
    import pymupdf
    with pymupdf.open(src_pdf) as doc:
        pg = doc[page - 1]
        clip = pymupdf.Rect(*bbox) & pg.rect
        zoom = pdfio.faithful_zoom(pg, zoom)          # the region keeps every scan pixel (engines then read it as such)
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
    out = pymupdf.open()
    np_ = out.new_page(width=clip.width, height=clip.height)
    np_.insert_image(np_.rect, stream=pix.tobytes('png'))
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest)
    return dest


def _region_crop(out: Path, src_pdf: Path, region: dict) -> tuple[Path, str]:
    """The region's pixels as a one-page PDF, stored by content: (path, sha256)."""
    import hashlib as _h
    rdir = out / 'raw' / 'regions'
    tmp_pdf = rdir / '_tmp' / f'{region["id"]}.pdf'
    region_pdf(src_pdf, region['page'], region['bbox'], tmp_pdf)
    sha = _h.sha256(tmp_pdf.read_bytes()).hexdigest()
    final_pdf = rdir / f'{sha[:16]}.pdf'
    if not final_pdf.exists():
        os.replace(tmp_pdf, final_pdf)
    else:
        tmp_pdf.unlink()
    return final_pdf, sha


def regions_ocr(out: Path, src_pdf: Path, items: list[tuple[dict, str]], log=print, lang: str | None = None,
                engines_of: dict | None = None) -> dict:
    """OCR several regions [(region, force_block)] with both engines (`engines_of`: region id -> the engines that
    read it, default both): {region id: record}.

    An engine run costs mostly its START (loading its models: Marker ~20 s, Docling ~6 s, whatever it reads), so
    the regions are read TOGETHER — one engine run over a PDF with one page per region — instead of one run per
    region. Each region reads its own page of that run (engine_json selects pages by id); the readings are the
    same as alone (measured). A region already read alone with the same settings reuses that cache; Marker runs
    once per forced block type (its setting applies to the whole run). The two engines run at once."""
    import hashlib as _h

    import pymupdf
    crops = {r['id']: _region_crop(out, src_pdf, r) for r, _ in items}
    force = {r['id']: fb for r, fb in items}
    raw_root = out / 'raw'

    wants = lambda rid, engine: engine in (engines_of or {}).get(rid, ('marker', 'docling'))
    used = [e for e in ('marker', 'docling') if any(wants(r['id'], e) for r, _ in items)]

    def engine_runs(engine):
        version = engine_version(engine)
        res, groups = {}, {}
        for r, _ in items:
            if not wants(r['id'], engine):
                continue
            pdf, sha = crops[r['id']]
            extra = ['--force_layout_block', force[r['id']]] if engine == 'marker' else None
            sub = f'regions/{engine}-{sha[:16]}'
            hit = cached_page(engine, pdf, sha, 1, raw_root, version, lang, 'standard', extra, sub)
            if hit is not None:
                res[r['id']] = {**hit, 'page': 1}
            else:
                # regions share a run when the engine would read them with the SAME arguments (forced block type,
                # resolution): the raw pixel density of crops differs a little, the resolution argument does not
                args = tuple(_command(engine, Path('PDF'), 1, Path('OUT'), lang, 'standard', extra, pdfio.native_dpi(pdf, 1)))
                groups.setdefault((tuple(extra or ()), args), []).append(r['id'])
        for (extra, _), ids in groups.items():
            if len(ids) == 1:                              # alone: exactly the single-region run (and its cache)
                pdf, sha = crops[ids[0]]
                rec = extract_page(engine, pdf, sha, 1, raw_root, version, log, lang, 'standard', list(extra) or None,
                                   subdir=f'regions/{engine}-{sha[:16]}')
                res[ids[0]] = {**rec, 'page': 1}
                continue
            shas = [crops[i][1] for i in ids]
            bsha = _h.sha256('|'.join(shas).encode()).hexdigest()
            bpdf = out / 'raw' / 'regions' / f'batch-{bsha[:16]}.pdf'
            if not bpdf.exists():
                doc = pymupdf.open()
                for i in ids:
                    with pymupdf.open(crops[i][0]) as one:
                        doc.insert_pdf(one)
                tmp = bpdf.with_name(bpdf.name + f'.{os.getpid()}.tmp')
                doc.save(tmp)
                os.replace(tmp, bpdf)
            rec = extract_page(engine, bpdf, bsha, 1, raw_root, version, log, lang, 'standard', list(extra) or None,
                               subdir=f'regions/{engine}-batch-{bsha[:16]}', last=len(ids))
            for k, i in enumerate(ids, 1):
                res[i] = {**rec, 'page': k, 'batch': len(ids)}
        return res

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {e: pool.submit(engine_runs, e) for e in used}
        per_engine = {e: f.result() for e, f in futures.items()}
    return {r['id']: {'crop_pdf': str(crops[r['id']][0].relative_to(out)), 'crop_sha': crops[r['id']][1],
                      'engines': {e: per_engine[e][r['id']] for e in per_engine if r['id'] in per_engine[e]}}
            for r, _ in items}


# Marker's OCR (surya) runs a llama-server it spawns DETACHED and stops only via atexit in the spawning
# process; a killed extraction can leave it running. The sentinel records the server's pid.
SURYA_SENTINELS = Path.home() / '.cache' / 'datalab' / 'surya'


def reap_engine_servers(log=print) -> list[int]:
    """Stop engine helper servers left behind by interrupted extractions. Call only when no extraction runs."""
    import signal
    stopped = []
    for sentinel in SURYA_SENTINELS.glob('*_server.json'):
        try:
            pid = int(json.loads(sentinel.read_text()).get('pid') or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if pid <= 0:
            continue
        cmd = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True).stdout
        if not any(k in cmd for k in ('llama-server', 'surya', 'marker')):
            sentinel.unlink(missing_ok=True)           # stale sentinel; the pid may belong to another program
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
            log(f'stopped orphaned engine server pid {pid} ({sentinel.name})')
        except ProcessLookupError:
            pass
        sentinel.unlink(missing_ok=True)
    return stopped
