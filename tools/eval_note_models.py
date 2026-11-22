"""Benchmark local note-interpreter models on real segments of books in library/.

Usage (repo root):  .venvs/docling/bin/python tools/eval_note_models.py <scratch_dir> <model_key>
model_key from scientific_ocr.config.NOTE_MODELS. Works on a COPY of library/ (books ch5, chem). Extend CASES
with your own (book, block_id, note, acceptable actions, required substring) to compare future models."""
import json, shutil, sys, time
from pathlib import Path
sys.path.insert(0, '.')
from scientific_ocr import config
from scientific_ocr.adapters.local_llm import LocalVLM
from scientific_ocr.adapters.storage import Library
from scientific_ocr.application.services import App

SCR = Path(sys.argv[1]); LIB = SCR / 'lib'
if not LIB.exists():
    LIB.mkdir(parents=True)
    for b in ('chem', 'ch5'):
        shutil.copytree(Path('library') / b, LIB / b, ignore=shutil.ignore_patterns('cache'))

class Jobs:
    def submit(self, *a, **k): return {}
    def busy(self, b): return None
    def list(self, b=None): return []

CASES = [
 ('chem', 'p1_9abec2c2', 'Το βέλος σε αυτή την εξίσωση είναι διπλό βέλος ισορροπίας (⇌), όχι απλό.', {'replace_latex'}, 'rightleftharpoons'),
 ('chem', 'p1_957cc271', 'Σε όλο το βιβλίο το Brønsted πρέπει να γράφεται Brönsted.', {'add_rule'}, 'Brønsted'),
 ('chem', 'p3_0dc8b3cb', 'Αυτό δεν είναι επικεφαλίδα ενότητας· είναι ο τίτλος ενός πλαισίου μέσα στο παράδειγμα.', {'set_heading_level', 'relabel'}, None),
 ('ch5', 'p2_53831ee5', 'Αυτό είναι η κεφαλίδα της σελίδας με τον αριθμό σελίδας.', {'relabel', 'replace_text'}, None),
 ('ch5', 'p2_6a33942f', 'Λείπει η τελευταία γραμμή αυτής της παραγράφου.', {'reocr', 'replace_text'}, None),
 ('chem', 'p2_c6ff5449', 'Η εξίσωση όπως την έγραψε το Marker είναι σωστή.', {'replace_latex', 'approve'}, None),
 ('chem', 'p1_71b8cf87', 'δεν μου αρέσει', {'none'}, None),
 ('chem', 'p1_0e583ebc', 'The nitrate ion is missing its negative charge in the products.', {'none', 'approve'}, None),
]

def run(key):
    m = config.NOTE_MODELS[key]
    vlm = LocalVLM(m['model'], m['mmproj'], m['name'], idle_seconds=3600)
    app = App(Library(LIB), Jobs(), vlm)
    t0 = time.time(); rows = []
    for book, bid, note, ok_actions, must in CASES:
        try:
            r = app.interpret_note(book, bid, note)
            p = r['proposal']; blob = json.dumps(p, ensure_ascii=False)
            good = p['action'] in ok_actions and (must is None or must in blob) and r['valid']
            rows.append({'case': bid, 'action': p['action'], 'valid': r['valid'], 'good': good, 'sec': r['seconds'],
                         'understanding': p['understanding'], 'value': p.get('value'), 'rule': p.get('rule'),
                         'label': p.get('label'), 'level': p.get('level'), 'clar': p.get('clarification'), 'errors': r['errors']})
        except Exception as e:
            rows.append({'case': bid, 'error': str(e)[:300], 'good': False})
    vlm.stop()
    return {'model': m['name'], 'load_and_run_s': round(time.time() - t0, 1), 'score': sum(r['good'] for r in rows), 'n': len(rows), 'rows': rows}

if __name__ == '__main__':
    res = run(sys.argv[2])
    (SCR / f'result-{sys.argv[2]}.json').write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(res['model'], 'score', res['score'], '/', res['n'], 'time', res['load_and_run_s'])
    for r in res['rows']:
        print(' ', r['case'], r.get('action'), 'OK' if r['good'] else 'xx', r.get('sec'), '|', (r.get('understanding') or r.get('error') or '')[:110])
