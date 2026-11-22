#!/usr/bin/env python3
"""MolScribe / RxnScribe adapter. Runs INSIDE .venvs/chem (subprocess). JSON in -> JSON out.

Usage: python chem_infer.py --engine molscribe|rxnscribe --ckpt FILE --input jobs.json --output results.json
jobs.json: [{"id": "...", "image": "/abs/crop.png"}, ...]

Outputs are CANDIDATES only. RDKit parseability is recorded, but a valid molecule is NOT
evidence that it matches the printed drawing; lone pairs / radicals / curved arrows are not
represented in SMILES/MOL. The caller must keep the original crop.
"""
import argparse
import json
import sys
import time
import traceback
from pathlib import Path


def rdkit_check(smiles):
    try:
        from rdkit import Chem, RDLogger
        RDLogger.DisableLog('rdApp.*')
        m = Chem.MolFromSmiles(smiles) if smiles else None
        if m is None:
            return {'parseable': False}
        return {'parseable': True, 'canonical_smiles': Chem.MolToSmiles(m), 'num_atoms': m.GetNumAtoms(),
                'formal_charge': Chem.GetFormalCharge(m),
                'radical_electrons': sum(a.GetNumRadicalElectrons() for a in m.GetAtoms())}
    except Exception as exc:  # RDKit failure is recorded, not hidden
        return {'parseable': False, 'error': str(exc)}


def jsonable(x):
    try:
        import numpy as np
        if isinstance(x, np.ndarray):
            return x.tolist()
        if isinstance(x, (np.floating, np.integer)):
            return x.item()
    except ImportError:
        pass
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--engine', choices=['molscribe', 'rxnscribe'], required=True)
    ap.add_argument('--ckpt', type=Path, required=True)
    ap.add_argument('--molscribe-ckpt', type=Path, help='rxnscribe: also parse molecules with MolScribe')
    ap.add_argument('--ocr', action='store_true', help='rxnscribe: EasyOCR for condition text (prefetched weights only)')
    ap.add_argument('--ocr-dir', type=Path, default=Path.home() / '.cache/scientific-textbook-ocr/models/easyocr')
    ap.add_argument('--input', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    jobs = json.loads(a.input.read_text())
    results = []
    try:
        import torch
        dev = torch.device('cpu')
        if a.engine == 'molscribe':
            from molscribe import MolScribe
            model = MolScribe(str(a.ckpt), device=dev)
        else:
            import rxnscribe.interface as rxi
            # Upstream __init__ always hf_hub_download()s MolScribe and builds an EasyOCR reader
            # (network on cold cache). Use local checkpoints only; OCR only if prefetched.
            def _mol(self):
                if not a.molscribe_ckpt:
                    return None
                from molscribe import MolScribe
                return MolScribe(str(a.molscribe_ckpt), device=dev)

            def _ocr(self):
                if not a.ocr:
                    return None
                import easyocr
                return easyocr.Reader(['en'], gpu=False, download_enabled=False, model_storage_directory=str(a.ocr_dir))
            rxi.RxnScribe.get_molscribe = _mol
            rxi.RxnScribe.get_ocr_model = _ocr
            model = rxi.RxnScribe(str(a.ckpt), device=dev)
            # Upstream loads with strict=False; verify key coverage instead of trusting it silently.
            states = torch.load(str(a.ckpt), map_location='cpu')['state_dict']
            ck = {k.replace('model.', '') for k in states}
            mk = set(model.model.state_dict())
            missing = sorted(mk - ck)
            if missing:
                raise RuntimeError(f'RxnScribe checkpoint missing {len(missing)} model keys, e.g. {missing[:5]}')
    except Exception as exc:
        err = ''.join(traceback.format_exception(exc))[-4000:]
        results = [{'id': j['id'], 'status': 'FAILED', 'error': 'MODEL_LOAD: ' + err} for j in jobs]
        a.output.write_text(json.dumps(results, indent=2))
        print(err, file=sys.stderr)
        return 1
    for j in jobs:
        t0 = time.time()
        try:
            if a.engine == 'molscribe':
                out = model.predict_image_file(j['image'], return_atoms_bonds=True, return_confidence=True)
                rec = {'smiles': out.get('smiles'), 'molfile': out.get('molfile'),
                       'confidence': out.get('confidence'), 'atoms': out.get('atoms'), 'bonds': out.get('bonds'),
                       'rdkit': rdkit_check(out.get('smiles'))}
            else:
                preds = model.predict_image_file(j['image'], molscribe=bool(a.molscribe_ckpt), ocr=a.ocr)
                for r in preds:
                    for role in ('reactants', 'conditions', 'products'):
                        for ent in r.get(role, []):
                            if ent.get('smiles'):
                                ent['rdkit'] = rdkit_check(ent['smiles'])
                rec = {'reactions': preds}
            results.append({'id': j['id'], 'status': 'OK', 'error': None, 'seconds': round(time.time() - t0, 2),
                            **jsonable(rec)})
        except Exception as exc:
            results.append({'id': j['id'], 'status': 'FAILED', 'seconds': round(time.time() - t0, 2),
                            'error': ''.join(traceback.format_exception(exc))[-4000:]})
    a.output.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
