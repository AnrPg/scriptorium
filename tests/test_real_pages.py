"""Integration assertions on real pages (library/ch5 = CHAPTER_5.pdf pages 2,3,5,6,7; library/chem = 3 chemistry
pages). Ground truth was established by looking at the source pages, not at OCR output."""
import json
import re
import shutil

import pytest
from conftest import REAL_PDF, REAL_RUN, build

from scientific_ocr.adapters import pdf as pdfio
from scientific_ocr.pipeline import audit

CHEM_RUN = REAL_RUN.parent / 'chem'


def test_schema_integrity_and_retention(real):
    out, c, md = real
    audit.validate_schema(c)
    assert {3, 5, 6} <= set(c['source']['pages_processed'])
    assert not [f for f in c['findings'] if f['type'].startswith('INTEGRITY')]
    assert c['release_status'] in ('REVIEW_REQUIRED', 'LLM_READY_WITH_EXCERPTS')
    assert pdfio.sha256_file(out / 'source' / 'original.pdf') == c['source']['sha256']
    if REAL_PDF.exists():
        assert pdfio.sha256_file(REAL_PDF) == c['source']['sha256']
    idx = json.loads((out / 'raw' / 'extraction_index.json').read_text())
    for eng in ('marker', 'docling'):
        for p in ('3', '5', '6'):
            assert idx[eng][p]['status'] == 'OK' and (out / idx[eng][p]['raw_json']).exists()
    assert not re.search(r'BLOCK_|provenance=|review=', md)                    # reader-facing, clean
    for b in c['blocks']:                                                     # content that stayed an image is shown
        if not b.get('excluded') and b.get('flow') != 'furniture' and b['chosen']['type'] in ('image_only', 'unresolved'):
            assert b['source_crop']['path'] in md


def test_p5_question_items_missed_by_marker_are_preserved(real):
    """p.5 question box has items α. and β.; Marker returns them only inside a childless ListGroup (no item boxes),
    which used to be dropped. Now each is its own list item with Marker's text (or, failing that, a Docling-only
    segment / a reviewer region): never lost, always in order."""
    out, c, md = real
    p5 = [b for b in c['blocks'] if b['page'] == 5]
    txt = lambda b: (b['chosen'].get('text') or '') + ' '.join(x.get('text') or '' for x in b['candidates'])
    kept = lambda b: b.get('role') == 'list_item' or 'SINGLE_ENGINE_DOCLING' in b['reasons'] or b['source'] == 'region'
    a = next(b for b in p5 if 'πολικότητα' in txt(b) and kept(b))
    bb = next(b for b in p5 if 'G-C' in txt(b) and kept(b))
    assert a['id'] != bb['id'] and 'πολικότητα' in (a['chosen'].get('text') or '')     # separate items, with text
    ids = [b['id'] for b in p5]
    q = next(b for b in p5 if b['chosen'].get('text', '').startswith('Ποιες'))
    assert ids.index(q['id']) < ids.index(a['id']) < ids.index(bb['id'])


def test_running_headers_not_lost(real):
    out, c, md = real
    for p in (3, 5, 6):
        hdr = [b for b in c['blocks'] if b['page'] == p and 'RUNNING_HEADER_EMPTY' in b['reasons']]
        # page furniture: kept in canonical with evidence and crop, excluded from the export
        assert len(hdr) >= 2 and all(b['excluded']['signals'] and (out / b['source_crop']['path']).exists()
                                     and f'id={b["id"]}' not in md for b in hdr)


def test_chemical_figure_is_image_authoritative(real):
    """p.6 Fig 5-4 = base pairs with hydrogen bonds."""
    out, c, md = real
    chem = [b for b in c['blocks'] if b['page'] == 6 and b['kind'] in ('molecular_structure', 'reaction_scheme')]
    assert len(chem) == 1 and chem[0]['chosen']['type'] == 'image_only' and chem[0]['display_crop']
    for s in (x.get('smiles') for x in chem[0]['candidates'] if x['engine'] == 'molscribe'):
        assert not s or s not in md


def test_base_letters_fixed_by_book_rules_and_raw_kept(real):
    """Source prints Latin A,T,G,C; Marker emits Greek Γ/Κ. The book's rules (user-approved) restore Latin."""
    out, c, md = real
    assert c['human']['rules']
    swapped = [b for b in c['blocks'] if b['candidates'] and b['candidates'][0]['engine'] == 'marker'
               and re.search(r'βάσ\w*\s+[ΓΚ](?!\w)', b['candidates'][0].get('text', ''))]
    assert swapped
    for b in swapped:
        assert b.get('rule_corrections') and not re.search(r'βάσ\w*\s+[ΓΚ](?!\w)', b['chosen']['text'])


def test_book_rule_does_not_override_independent_reading(real):
    """p.7 Fig 5-6 caption prints the panel label (Γ). The book's DNA rule (Γ->G) must not change it, because
    Docling independently read Γ there (it used to, and the symbol-conflict check caught it)."""
    out, c, md = real
    cap = next(b for b in c['blocks'] if b['page'] == 7 and 'Morse' in (b['chosen'].get('text') or ''))
    assert '(Γ) κώδικας Morse' in cap['chosen']['text']
    assert not any(x['rule'] and x['from'] == 'Γ' and 'Morse' in x['context'] for x in cap.get('rule_corrections', []))


@pytest.mark.skipif(not (CHEM_RUN / 'canonical.json').exists(), reason='library/chem not present')
def test_chem_equilibrium_arrow_error_is_caught():
    """Source: HCN(aq) + H2O(l) ⇌ H3O+(aq) + CN−(aq). Marker wrote →, UniMERNet ⟹: conflict, LaTeX withheld."""
    c = json.loads((CHEM_RUN / 'canonical.json').read_text())
    hcn = next(b for b in c['blocks'] if b['kind'] == 'chemical_reaction'
               and any('HCN' in str(x.get('latex')) for x in b['candidates'] if x['engine'] == 'marker'))
    assert 'EQUATION_CONFLICT' in hcn['reasons']
    if hcn.get('decisions'):                     # the reviewer settled it: the human value, with ⇌, is the content
        assert 'rightleftharpoons' in hcn['chosen'].get('latex', '') and hcn['content_source'] == 'human'
    else:
        assert hcn['chosen']['type'] == 'image_only' and hcn['display_crop']
    agree = [b for b in c['blocks'] if b['kind'] == 'chemical_reaction' and 'MARKER_UNIMERNET_AGREE' in b['reasons']]
    # machine agreement never removes the crop of chemistry; only a reviewer's approval does
    assert agree and all(b['display_crop'] or b['review'] == 'approved' for b in agree)
    assert any(b['kind'] == 'lewis_structure' for b in c['blocks'])


def test_rebuild_is_stable_and_never_reruns_ocr(real, tmp_path):
    out, c, md = real
    copy = tmp_path / 'ch5'
    shutil.copytree(out, copy, ignore=shutil.ignore_patterns('original.pdf', 'cache'))
    shutil.copy(out / 'source' / 'original.pdf', copy / 'source' / 'original.pdf')
    raw = {p: p.stat().st_mtime for p in (copy / 'raw').rglob('*') if p.is_file()}
    from scientific_ocr.adapters import specialized

    def cached_only(engine, jobs, out_, allow, log):
        return specialized.run(engine, jobs, out_, False, log)       # cache hits only, never inference
    assert build(copy, runner=cached_only) == 0
    c2 = json.loads((copy / 'canonical.json').read_text())
    assert [b['id'] for b in c2['blocks']] == [b['id'] for b in c['blocks']]
    assert {p: p.stat().st_mtime for p in raw} == raw


def test_hierarchy_from_checked_hints_on_real_chapter(real):
    """Ch. 5: chapter title (p1) > CAPITAL sections (p3, p7) > sentence-case subsections. Marker tags these
    inconsistently across pages; checked hints (measured typography + running heads) fix what two kinds of
    evidence agree on; typography alone (p6) stays a proposal for the reviewer."""
    out, c, md = real
    toc = {s['title'][:20]: s for s in c['sections']}
    assert toc['DNA και Χρωμοσώματα']['level'] == 1
    assert toc['Η ΔΟΜΗ ΤΟΥ DNA']['level'] == 2 and toc['Η ΔΟΜΗ ΤΩΝ ΕΥΚΑΡΥΩΤΙ']['level'] == 2
    assert toc['Ένα μόριο DNA αποτελ']['level'] == 3
    p6 = toc['Η δομή του DNA προσφ']
    assert p6['level'] == 3 or any(p6['id'] in p['members'] and p['level'] == 3 for p in c['heading_review']['proposals'])


def test_real_figure_caption_without_figure_and_unread_chapter_band(real):
    out, c, md = real
    cap = next(b for b in c['blocks'] if b['page'] == 7 and (b['chosen'].get('text') or '').startswith('**ΕΙΚΟΝΑ 5-6'))
    assert cap['kind'] == 'caption'
    info = next(p for p in c['pages'] if p['page'] == 7)
    assert any(s['kind'] == 'figure_assemble' and s['caption'] == cap['id'] for s in info['suggestions'])
    band = next(b for b in c['blocks'] if b['page'] == 1 and 'RUNNING_HEADER_EMPTY' in b['reasons'])
    assert not band.get('excluded') and band['source_crop']['path'] in md       # «ΚΕΦΑΛΑΙΟ 5» is not dropped
