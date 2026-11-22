#!/usr/bin/env python3
"""Basic installation diagnostics, deliberately NOT an inference or OCR accuracy test."""
import json
import os
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / '.venvs'
LOG = ROOT / 'logs'
MODELS = Path(os.getenv('SCIENTIFIC_OCR_MODEL_HOME', str(Path.home() / '.cache/scientific-textbook-ocr/models')))

CHECKS = {
    'marker': ('marker', 'marker'),
    'docling': ('docling', 'docling'),
    'unimernet': ('unimernet', 'unimernet'),
    'molscribe': ('chem', 'molscribe'),
    'rxnscribe': ('chem', 'rxnscribe'),
}


def check_import(env_name, module):
    python = VENV / env_name / 'bin' / 'python'
    if not python.is_file():
        return {'state': 'ENV_MISSING', 'python': str(python)}
    # Importlib spec avoids loading huge model weights just for package detection.
    code = f'import importlib.util; print(bool(importlib.util.find_spec({module!r})))'
    try:
        p = subprocess.run([str(python), '-c', code], capture_output=True, text=True, timeout=60)
        return {'state': 'PACKAGE_PRESENT_NOT_INFERENCE_TESTED' if p.returncode == 0 and p.stdout.strip() == 'True' else 'IMPORT_SPEC_FAILED',
                'python': str(python), 'error': (p.stderr or '').strip()[:2000]}
    except Exception as exc:
        return {'state': 'CHECK_FAILED', 'error': str(exc)}


def main():
    out = {'platform': platform.platform(), 'architecture': platform.machine(),
           'status_explanation': 'An installed import or downloaded checkpoint is NOT proof of working local inference.',
           'model_home': str(MODELS), 'packages': {}}
    for key, (env, module) in CHECKS.items():
        out['packages'][key] = check_import(env, module)

    for model, path in {
        'docling': MODELS / 'docling',
        'unimernet_base': MODELS / 'unimernet_base',
        'molscribe': MODELS / 'molscribe' / 'swin_base_char_aux_1m.pth',
        'rxnscribe': MODELS / 'rxnscribe' / 'pix2seq_reaction_full.ckpt',
    }.items():
        out.setdefault('download_paths', {})[model] = {'path': str(path), 'exists': path.exists()}

    if LOG.exists():
        out['install_steps'] = {p.stem: p.read_text().strip() for p in LOG.glob('*.status')}
    report = ROOT / 'doctor_report.json'
    report.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(out, indent=2, ensure_ascii=False))
    print(f'Wrote {report}. Perform real inference tests before claiming readiness.')


if __name__ == '__main__':
    main()
