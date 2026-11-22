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
             profile: str = 'standard', marker_extra: list[str] | None = None, native: float | None = None) -> list[str]:
    if engine == 'marker':
        # Marker page ranges are ZERO-based.
        return [str(config.MARKER_BIN), str(pdf), '--page_range', str(page - 1), *config.MARKER_ARGS,
                *_resolution_args('marker', config.PROFILES[profile], native), *(marker_extra or []),
                '--output_dir', str(out_dir)]
    # Docling page ranges are ONE-based.
    return [str(config.DOCLING_BIN), 'convert', str(pdf), '--page-range', f'{page}-{page}', *config.docling_args(lang),
            *_resolution_args('docling', [], native),
            '--enrich-picture-classes', '--artifacts-path', str(config.CKPT['docling']), '--output', str(out_dir)]


def find_raw_json(engine: str, run_dir: Path) -> Path | None:
    files = sorted(run_dir.rglob('*.json'))
    files = [f for f in files if f.name != '_DONE.json' and not f.name.endswith('_meta.json')]
    return files[0] if files else None


def extract_page(engine: str, pdf: Path, pdf_sha: str, page: int, raw_root: Path, version: str,
                 log=print, lang: str | None = None, profile: str = 'standard', marker_extra=None,
                 subdir: str | None = None) -> dict:
    native = pdfio.native_dpi(pdf, page)
    args = _command(engine, Path('PDF'), page, Path('OUT'), lang, profile, marker_extra, native)[1:]  # no paths in key
    key = cache_key(pdf_sha, page, engine, version, args)
    run_dir = raw_root / (subdir or engine) / f'p{page:04d}-{key}'
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
    cmd = _command(engine, pdf, page, tmp, lang, profile, marker_extra, native)
    log(f'[{engine}] page {page}: {" ".join(cmd)}')
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


def region_ocr(out: Path, src_pdf: Path, region: dict, force_block: str, log=print, lang: str | None = None) -> dict:
    """OCR one reviewer region with both engines; Marker treats the whole region as `force_block` (no layout
    detection), Docling gives an independent reading. Cached by crop content + settings."""
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
    res = {}
    for engine in ('marker', 'docling'):
        extra = ['--force_layout_block', force_block] if engine == 'marker' else None
        res[engine] = extract_page(engine, final_pdf, sha, 1, out / 'raw', engine_version(engine), log, lang,
                                   'standard', extra, subdir=f'regions/{engine}-{sha[:16]}')
    return {'crop_pdf': str(final_pdf.relative_to(out)), 'crop_sha': sha, 'engines': res}


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
