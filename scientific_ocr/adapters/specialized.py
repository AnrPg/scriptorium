"""Adapter for specialised models (UniMERNet, MolScribe, RxnScribe), each in its own venv as a subprocess.

Implements ports.SpecializedRunner. Results are cached by crop SHA-256 (content-addressed); only successful
inference is cached, so failures are retried next time. Outputs are candidates, never content.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .. import config

MODEL_IDS = {e: config.CKPT[e].name for e in ('unimernet', 'molscribe', 'rxnscribe')}   # checkpoint = version
COMMANDS = {
    'unimernet': lambda ocr: [config.PY['unimernet'], config.ENGINES_DIR / 'unimernet_infer.py',
                              '--model-dir', config.CKPT['unimernet']],
    'molscribe': lambda ocr: [config.PY['chem'], config.ENGINES_DIR / 'chem_infer.py', '--engine', 'molscribe',
                              '--ckpt', config.CKPT['molscribe']],
    'rxnscribe': lambda ocr: [config.PY['chem'], config.ENGINES_DIR / 'chem_infer.py', '--engine', 'rxnscribe',
                              '--ckpt', config.CKPT['rxnscribe'], '--molscribe-ckpt', config.CKPT['molscribe']]
                             + (['--ocr', '--ocr-dir', config.CKPT['easyocr']] if ocr else []),
}


def _cache_dir(out: Path, engine: str) -> Path:
    d = out / 'raw' / 'specialized' / engine
    d.mkdir(parents=True, exist_ok=True)
    return d


def run(engine: str, jobs: list[dict], out: Path, allow_inference: bool, log=print) -> dict:
    """jobs: [{'id': block_id, 'image': abs_path, 'sha256': crop_sha}] -> {block_id: result}"""
    ocr = engine == 'rxnscribe' and (config.CKPT['easyocr'] / 'english_g2.pth').exists()
    cache = _cache_dir(out, engine + ('-ocr' if ocr else ''))   # OCR changes outputs -> separate cache
    results, todo = {}, []
    for j in jobs:
        f = cache / f'{j["sha256"][:24]}.json'
        if f.exists():
            results[j['id']] = {'model': MODEL_IDS[engine], **json.loads(f.read_text()), 'cached': True,
                                'raw_file': str(f.relative_to(out))}
        else:
            todo.append(j)
    if todo and not allow_inference:
        for j in todo:
            results[j['id']] = {'status': 'NOT_RUN', 'error': 'render-only mode: no cached result'}
        return results
    if not todo:
        return results
    inp, res = cache / '_jobs.json', cache / '_results.json'
    inp.write_text(json.dumps(todo))
    cmd = [str(c) for c in COMMANDS[engine](ocr)] + ['--input', str(inp), '--output', str(res)]
    log(f'[{engine}] {len(todo)} crops: {" ".join(cmd)}')
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=config.subprocess_env(), timeout=3600)
        (cache / '_last_run.log').write_text(f'$ {" ".join(cmd)}\nexit {p.returncode}\n{p.stderr[-20000:]}')
        got = {r['id']: r for r in json.loads(res.read_text())} if res.exists() else {}
        err = None if p.returncode == 0 else f'exit {p.returncode}: {p.stderr[-1500:]}'
    except Exception as exc:  # recorded, never hidden
        got, err = {}, f'{type(exc).__name__}: {exc}'
    for j in todo:
        r = got.get(j['id']) or {'status': 'FAILED', 'error': err or 'no result returned'}
        r = {**r, 'engine': engine, 'command': ' '.join(cmd), 'crop_sha256': j['sha256'], 'model': MODEL_IDS[engine]}
        if r.get('status') == 'OK':          # only successful inference is cached
            (cache / f'{j["sha256"][:24]}.json').write_text(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            log(f'[{engine}] {j["id"]}: {r.get("status")} {str(r.get("error"))[:300]}')
        cached = cache / f'{j["sha256"][:24]}.json'
        results[j['id']] = {**r, 'cached': False, 'raw_file': str(cached.relative_to(out)) if cached.exists() else None}
    return results
