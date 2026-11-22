"""Stage: route crops to specialised models and attach their outputs as candidates (never as content).

Routing: equations (math + typeset chemical equations) -> UniMERNet as an independent reading;
molecular drawings -> MolScribe + RxnScribe. Lewis structures and typeset equations are NOT sent to MolScribe
(it cannot represent lone pairs/charges and hallucinates graphs from text).
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core import chem
from ..core.model import CHEMICAL_EQUATION_KINDS
from ..core.reasons import REASONS, flag
from ..core.symbols import glyph_issues, latex_norm
from ..ports import Log, SpecializedRunner

ROUTES = {'unimernet': lambda b: b.get('needs_unimernet'),
          'molscribe': lambda b: b['kind'] == 'molecular_structure',
          'rxnscribe': lambda b: b['kind'] == 'molecular_structure'}


def enrich(out: Path, blocks: list[dict], run: SpecializedRunner, allow_inference: bool, log: Log) -> dict:
    jobs = {e: [] for e in ROUTES}
    for b in blocks:
        for engine, wants in ROUTES.items():
            if wants(b):
                jobs[engine].append({'id': b['id'], 'image': str(out / b['source_crop']['path']),
                                     'sha256': b['source_crop']['sha256']})
    res = {e: run(e, j, out, allow_inference, log) if j else {} for e, j in jobs.items()}
    for b in blocks:
        if b['id'] in res['unimernet']:
            _equation(b, res['unimernet'][b['id']])
        for eng in ('molscribe', 'rxnscribe'):
            if b['id'] in res[eng]:
                _structure(b, eng, res[eng][b['id']])
        b.pop('needs_unimernet', None)
    return {e: {'jobs': len(j), 'ok': sum(1 for r in res[e].values() if r.get('status') == 'OK'),
                'failed': [k for k, r in res[e].items() if r.get('status') != 'OK']} for e, j in jobs.items()}


def _lineage(r) -> dict:
    """Where a specialised-model candidate comes from: model checkpoint, cached raw result, input crop."""
    return {'version': r.get('model'), 'raw_file': r.get('raw_file'), 'input_crop_sha256': r.get('crop_sha256'),
            'ref': f'{r.get("engine")}:crop:{(r.get("crop_sha256") or "")[:12]}'}


def _equation(b, r):
    b['candidates'].append({'engine': 'unimernet', 'status': r.get('status'), 'latex': r.get('latex'),
                            'error': r.get('error'), 'confidence': None, **_lineage(r)})
    mk = b['chosen'].get('latex', '')
    if r.get('status') != 'OK':
        flag(b, 'UNIMERNET_UNAVAILABLE')
        return
    chemical = b['kind'] in CHEMICAL_EQUATION_KINDS          # incl. nuclear: isotope numbers are never consensus-accepted
    key = chem.equation_key if chemical else latex_norm
    if key(r['latex']) == key(mk):
        flag(b, 'MARKER_UNIMERNET_AGREE')
        # Agreement of two independent readings is evidence for maths; chemistry keeps its crop (charges,
        # states and arrows are never accepted from OCR consensus alone).
        # The agreement removes only the default 'until agreement' crop: a crop demanded by another reason stays.
        b['display_crop'] = chemical or any(REASONS[c].crop for c in b['reasons'] if c in REASONS and c != 'MARKER_UNIMERNET_AGREE')
        if b['review'] == 'not_needed':
            b['review'] = 'recommended'
    else:
        b['chosen'] = {'type': 'image_only', 'provenance': 'source_crop', 'withheld_latex': mk}
        flag(b, 'EQUATION_CONFLICT')
    for issue in sorted(set(glyph_issues(mk) + glyph_issues(r.get('latex') or ''))):
        flag(b, issue)


def _structure(b, eng, r):
    cand = {'engine': eng, 'status': r.get('status'), 'error': r.get('error'), 'confidence': r.get('confidence'),
            **_lineage(r)}
    if eng == 'molscribe':
        cand.update(smiles=r.get('smiles'), molfile=r.get('molfile'), rdkit=r.get('rdkit'))
        smi = r.get('smiles') or ''
        heavy = [a for a in re.findall(r'\[?([A-Z][a-z]?)', smi) if a != 'H']
        if r.get('status') == 'OK' and (smi.count('.') >= 3 or not heavy):
            flag(b, 'CHEM_CANDIDATE_IMPLAUSIBLE', fragments=smi.count('.') + 1, heavy_atoms=len(heavy))
    else:
        rx = r.get('reactions') or []
        cand['reactions'] = rx
        if r.get('status') == 'OK' and any(x.get('reactants') and x.get('products') for x in rx):
            b['kind'] = 'reaction_scheme'
            flag(b, 'REACTION_SCHEME_IMAGE_AUTHORITATIVE')
    b['candidates'].append(cand)
    if r.get('status') != 'OK':
        flag(b, f'{eng.upper()}_UNAVAILABLE', error=str(r.get('error'))[:500])
