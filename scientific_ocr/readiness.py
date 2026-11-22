"""Phase A: real inference smoke tests for all five engines -> model_readiness.json.

Statuses: READY (inference ran AND a known-answer control was correct),
INFERENCE_RAN_OUTPUT_WRONG, INFERENCE_FAILED, INSTALLED_NOT_READY (env/checkpoint missing).
Real-crop results are recorded as evidence but are not used to declare readiness, because the
correct answer for a scanned crop is judged by a human, not by this script.
"""
from __future__ import annotations

import json
import platform
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .adapters import pdf as source

GREEK = re.compile(r'[Ͱ-Ͽἀ-῿]')

# Known-answer controls (generated locally, deterministic).
CONTROL_LATEX = r'\Delta G = \Delta H - T \Delta S'
CONTROL_MOL = 'Cn1c(=O)c2c(ncn2C)n(C)c1=O'           # caffeine
CONTROL_RXN = 'CCO.CC(=O)O>>CC(=O)OCC'                 # esterification (water omitted)
# The p6 drawing shows thymine N1 bonded to the backbone (bare stub -> MolScribe writes methyl) and N3-H.
THYMINE = 'Cc1cn(C)c(=O)[nH]c1=O'

# Real crops from CHAPTER_5.pdf (page, bbox in PDF points, TOPLEFT). Chosen by visual inspection.
REAL_CROPS = {
    'thymine_p6': (6, [250, 724, 545, 995]),        # Fig 5-4A thymine incl. N3-H, N1 stub
    'po4_p6': (6, [1680, 1448, 1768, 1492]),        # caption fragment "(-PO4)" with subscript
    'basepairs_p6': (6, [150, 140, 940, 1080]),     # Fig 5-4A full (not a reaction: negative check)
}

CONTROL_SCRIPT = r'''
import sys
from rdkit import Chem
from rdkit.Chem import Draw, AllChem
out = sys.argv[1]
Draw.MolToImage(Chem.MolFromSmiles(sys.argv[2]), size=(500, 400)).save(out + '/control_molecule.png')
rxn = AllChem.ReactionFromSmarts(sys.argv[3], useSmiles=True)
Draw.ReactionToImage(rxn, subImgSize=(300, 260)).save(out + '/control_reaction.png')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig = plt.figure(figsize=(4, 0.8), dpi=200)
fig.text(0.05, 0.3, '$' + sys.argv[4] + '$', fontsize=20)
fig.savefig(out + '/control_math.png', facecolor='white')
'''


def _run(cmd, log: Path, timeout=3600) -> dict:
    t0 = time.time()
    try:
        p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout,
                           env=config.subprocess_env())
        log.write_text(f'$ {" ".join(map(str, cmd))}\n\n--- stdout\n{p.stdout}\n--- stderr\n{p.stderr}')
        return {'cmd': ' '.join(map(str, cmd)), 'exit': p.returncode, 'seconds': round(time.time() - t0, 1),
                'log': str(log), 'stderr_tail': p.stderr[-1500:] if p.returncode else ''}
    except subprocess.TimeoutExpired:
        return {'cmd': ' '.join(map(str, cmd)), 'exit': None, 'seconds': timeout, 'log': str(log),
                'stderr_tail': 'TIMEOUT'}


def _canon_many(smiles_list: list[str]) -> list[str | None]:
    code = ('import sys,json\nfrom rdkit import Chem, RDLogger\nRDLogger.DisableLog("rdApp.*")\n'
            'out=[]\nfor s in json.loads(sys.argv[1]):\n'
            '    m=Chem.MolFromSmiles(s) if s else None\n    out.append(Chem.MolToSmiles(m) if m else None)\n'
            'print(json.dumps(out))')
    p = subprocess.run([str(config.PY['chem']), '-c', code, json.dumps(smiles_list)], capture_output=True, text=True)
    return json.loads(p.stdout) if p.returncode == 0 else [None] * len(smiles_list)


def _latex_tokens(s: str) -> str:
    return re.sub(r'\s+|\{|\}', '', s or '')


def check_marker_docling(pdf: Path, page: int, work: Path) -> dict:
    out = {}
    m_dir, d_dir = work / 'marker', work / 'docling'
    m_dir.mkdir(parents=True, exist_ok=True)
    d_dir.mkdir(parents=True, exist_ok=True)
    r = _run([config.MARKER_BIN, pdf, '--page_range', str(page - 1), *config.MARKER_ARGS, '--output_dir', m_dir],
             work / 'marker.log')
    files = list(m_dir.glob('*/*.json'))
    files = [f for f in files if not f.name.endswith('_meta.json')]
    rec = {'run': r, 'offline_env': True}
    if r['exit'] == 0 and files:
        d = json.loads(files[0].read_text())
        texts = []

        def walk(b):
            texts.append(re.sub('<[^>]+>', '', b.get('html') or ''))
            for c in b.get('children') or []:
                walk(c)
        walk(d)
        txt = ' '.join(texts)
        n_greek = len(GREEK.findall(txt))
        rec.update(output=str(files[0]), chars=len(txt), greek_chars=n_greek,
                   status='READY' if n_greek > 200 else 'INFERENCE_RAN_OUTPUT_WRONG')
    else:
        rec['status'] = 'INFERENCE_FAILED'
    out['marker'] = rec

    r = _run([config.DOCLING_BIN, 'convert', pdf, '--page-range', f'{page}-{page}', *config.DOCLING_ARGS,
              '--artifacts-path', config.CKPT['docling'], '--output', d_dir], work / 'docling.log')
    rec = {'run': r, 'offline_env': True}
    files = list(d_dir.glob('*.json'))
    if r['exit'] == 0 and files:
        d = json.loads(files[0].read_text())
        txt = ' '.join(t.get('text', '') for t in d.get('texts', []))
        has_prov = all(t.get('prov') for t in d.get('texts', []))
        n_greek = len(GREEK.findall(txt))
        rec.update(output=str(files[0]), chars=len(txt), greek_chars=n_greek, provenance_on_all_texts=has_prov,
                   status='READY' if n_greek > 200 and has_prov else 'INFERENCE_RAN_OUTPUT_WRONG')
    else:
        rec['status'] = 'INFERENCE_FAILED'
    out['docling'] = rec
    return out


def check_specialized(pdf: Path, work: Path) -> dict:
    fx = work / 'fixtures'
    fx.mkdir(parents=True, exist_ok=True)
    gen = _run([config.PY['chem'], '-c', CONTROL_SCRIPT, fx, CONTROL_MOL, CONTROL_RXN, CONTROL_LATEX],
               work / 'controls.log')
    crops = {k: source.crop(pdf, pg, bb, fx / f'{k}.png', pad=0) for k, (pg, bb) in REAL_CROPS.items()}
    out = {}
    meta = {'control_generation': gen, 'real_crops': crops}

    def jobs(name, items):
        p = work / f'{name}_jobs.json'
        p.write_text(json.dumps([{'id': i, 'image': str(path)} for i, path in items]))
        return p

    # UniMERNet
    rec = {}
    if not config.PY['unimernet'].exists() or not (config.CKPT['unimernet'] / 'pytorch_model.pth').exists():
        rec['status'] = 'INSTALLED_NOT_READY'
    else:
        res = work / 'unimernet_results.json'
        rec['run'] = _run([config.PY['unimernet'], config.ENGINES_DIR / 'unimernet_infer.py', '--model-dir',
                           config.CKPT['unimernet'], '--input',
                           jobs('unimernet', [('control_math', fx / 'control_math.png'),
                                              ('po4_p6', fx / 'po4_p6.png')]),
                           '--output', res], work / 'unimernet.log')
        results = json.loads(res.read_text()) if res.exists() else []
        rec['results'] = results
        ctl = next((r for r in results if r['id'] == 'control_math'), None)
        if not ctl or ctl['status'] != 'OK':
            rec['status'] = 'INFERENCE_FAILED'
        else:
            ok = _latex_tokens(ctl['latex']) == _latex_tokens(CONTROL_LATEX)
            rec['control_expected'] = CONTROL_LATEX
            rec['control_exact_match'] = ok
            rec['status'] = 'READY' if ok else 'INFERENCE_RAN_OUTPUT_WRONG'
    out['unimernet'] = rec

    # MolScribe
    rec = {}
    if not config.CKPT['molscribe'].exists():
        rec['status'] = 'INSTALLED_NOT_READY'
    else:
        res = work / 'molscribe_results.json'
        rec['run'] = _run([config.PY['chem'], config.ENGINES_DIR / 'chem_infer.py', '--engine', 'molscribe',
                           '--ckpt', config.CKPT['molscribe'], '--input',
                           jobs('molscribe', [('control_molecule', fx / 'control_molecule.png'),
                                              ('thymine_p6', fx / 'thymine_p6.png')]),
                           '--output', res], work / 'molscribe.log')
        results = json.loads(res.read_text()) if res.exists() else []
        rec['results'] = results
        ctl = next((r for r in results if r['id'] == 'control_molecule'), None)
        if not ctl or ctl['status'] != 'OK':
            rec['status'] = 'INFERENCE_FAILED'
        else:
            exp, got, thy, got_thy = _canon_many([CONTROL_MOL, ctl.get('smiles'), THYMINE,
                                                  next((r.get('smiles') for r in results if r['id'] == 'thymine_p6'), None)])
            rec['control_expected'] = exp
            rec['control_exact_match'] = bool(got) and got == exp
            rec['real_thymine_expected'] = thy
            rec['real_thymine_got'] = got_thy
            rec['real_thymine_exact_match'] = bool(got_thy) and got_thy == thy
            rec['status'] = 'READY' if rec['control_exact_match'] else 'INFERENCE_RAN_OUTPUT_WRONG'
    out['molscribe'] = rec

    # RxnScribe
    rec = {}
    if not config.CKPT['rxnscribe'].exists():
        rec['status'] = 'INSTALLED_NOT_READY'
    else:
        res = work / 'rxnscribe_results.json'
        rec['run'] = _run([config.PY['chem'], config.ENGINES_DIR / 'chem_infer.py', '--engine', 'rxnscribe',
                           '--ckpt', config.CKPT['rxnscribe'], '--molscribe-ckpt', config.CKPT['molscribe'],
                           '--input', jobs('rxnscribe', [('control_reaction', fx / 'control_reaction.png'),
                                                         ('basepairs_p6', fx / 'basepairs_p6.png')]),
                           '--output', res], work / 'rxnscribe.log')
        results = json.loads(res.read_text()) if res.exists() else []
        rec['results'] = results
        ctl = next((r for r in results if r['id'] == 'control_reaction'), None)
        if not ctl or ctl['status'] != 'OK':
            rec['status'] = 'INFERENCE_FAILED'
        else:
            exp_r, exp_p = CONTROL_RXN.split('>>')
            got_r = sorted(s for rx in ctl['reactions'] for e in rx.get('reactants', []) if (s := e.get('smiles')))
            got_p = sorted(s for rx in ctl['reactions'] for e in rx.get('products', []) if (s := e.get('smiles')))
            canon = _canon_many(exp_r.split('.') + exp_p.split('.') + got_r + got_p)
            n1, n2 = len(exp_r.split('.')), len(exp_p.split('.'))
            e_r, e_p = sorted(canon[:n1]), sorted(canon[n1:n1 + n2])
            g_r, g_p = sorted(filter(None, canon[n1 + n2:n1 + n2 + len(got_r)])), sorted(filter(None, canon[n1 + n2 + len(got_r):]))
            rec.update(control_expected={'reactants': e_r, 'products': e_p},
                       control_got={'reactants': g_r, 'products': g_p},
                       control_exact_match=(e_r == g_r and e_p == g_p),
                       control_n_reactions=len(ctl['reactions']))
            rec['status'] = 'READY' if rec['control_exact_match'] else 'INFERENCE_RAN_OUTPUT_WRONG'
    out['rxnscribe'] = rec
    return out, meta


def versions() -> dict:
    pk = {'marker': ['marker-pdf', 'surya-ocr', 'torch'], 'docling': ['docling', 'docling-core', 'pymupdf', 'torch'],
          'unimernet': ['unimernet', 'torch', 'transformers'],
          'chem': ['molscribe', 'rxnscribe', 'torch', 'torchvision', 'rdkit', 'timm', 'opennmt-py']}
    out = {}
    for env, names in pk.items():
        try:
            p = subprocess.run(['uv', 'pip', 'list', '--python', str(config.PY[env]), '--format', 'json'],
                               capture_output=True, text=True, timeout=60)
            have = {d['name'].lower(): d['version'] for d in json.loads(p.stdout)}
            out[env] = {n: have.get(n) for n in names}
        except Exception as exc:
            out[env] = {'error': str(exc)}
    try:
        out['tesseract'] = subprocess.run(['tesseract', '--version'], capture_output=True, text=True).stdout.split('\n')[0]
        out['tesseract_langs'] = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True).stdout.split()[4:]
    except FileNotFoundError:
        out['tesseract'] = None
    return out


def checkpoints() -> dict:
    out = {}
    for k, p in config.CKPT.items():
        files = [p] if p.is_file() else sorted(x for x in p.rglob('*') if x.suffix in ('.pth', '.ckpt', '.safetensors', '.bin'))
        out[k] = {'path': str(p), 'exists': p.exists(),
                  'files': [{'path': str(f), 'bytes': f.stat().st_size, 'sha256': source.sha256_file(f)} for f in files]}
    return out


def run(pdf: Path, page: int, work: Path, skip_layout: bool = False) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    report = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(),
              'machine': platform.machine(), 'offline_env': config.OFFLINE_ENV,
              'note': 'READY = real local inference ran and a known-answer control matched exactly. '
                      'It does NOT certify accuracy on scanned textbook content.',
              'source_pdf': {'path': str(pdf), 'sha256': source.sha256_file(pdf), 'smoke_page': page},
              'versions': versions(), 'checkpoints': checkpoints(), 'engines': {}}
    if not skip_layout:
        report['engines'].update(check_marker_docling(pdf, page, work))
    spec, report['fixtures'] = check_specialized(pdf, work)
    report['engines'].update(spec)
    (work / 'model_readiness.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report
