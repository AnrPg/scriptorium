"""Projection pipeline on a synthetic page with hand-made engine JSON (no OCR engines run)."""
import json
import re

import pytest
from conftest import H, W, build, load, make_run, marker_json

from scientific_ocr.adapters import pdf as pdfio
from scientific_ocr.adapters.event_store import EventStore
from scientific_ocr.core.model import release_status
from scientific_ocr.pipeline import align, audit, engine_json


# ---------------------------------------------------------------- anti-corruption layer & alignment
def test_parse_pages():
    assert pdfio.parse_pages('3,5-6', 24) == [3, 5, 6]
    assert pdfio.parse_pages(None, 3) == [1, 2, 3]
    for bad in ('0', '25', '6-5', ','):
        with pytest.raises(ValueError):
            pdfio.parse_pages(bad, 24)


def test_docling_bottomleft_to_topleft_and_orphans_kept():
    raw = {'body': {'children': [{'$ref': '#/texts/0'}]}, 'texts': [
        {'self_ref': '#/texts/0', 'label': 'text', 'text': 'x', 'children': [],
         'prov': [{'page_no': 1, 'bbox': {'l': 10, 't': 790, 'r': 50, 'b': 770, 'coord_origin': 'BOTTOMLEFT'}}]},
        {'self_ref': '#/texts/1', 'label': 'text', 'text': 'orphan', 'children': [],
         'prov': [{'page_no': 1, 'bbox': {'l': 0, 't': 100, 'r': 10, 'b': 90}}]}]}
    bs = engine_json.docling_blocks(raw, 1, 800)
    assert bs[0]['bbox'] == [10, 10, 50, 30] and [b['text'] for b in bs] == ['x', 'orphan']


def test_marker_zero_based_page_ids_and_math():
    assert engine_json.marker_blocks(marker_json(), 1)[0]['page'] == 1
    with pytest.raises(ValueError):
        engine_json.marker_blocks(marker_json(), 2)
    t, latex = engine_json.html_to_md('<p>(-PO<sub>4</sub>) και <math>x^2</math></p>')
    assert '<sub>4</sub>' in t and '$x^2$' in t and latex == ['x^2']


def _blk(ref, eng, bbox, text, kind='paragraph', order=0):
    return {'ref': ref, 'engine': eng, 'bbox': bbox, 'text': text, 'kind': kind, 'order': order}


def test_alignment_split_merge_single():
    m = [_blk('m1', 'marker', [0, 0, 100, 40], 'alpha beta gamma delta')]
    d = [_blk('d1', 'docling', [0, 0, 100, 20], 'alpha beta'), _blk('d2', 'docling', [0, 21, 100, 40], 'gamma delta', order=1)]
    comp = align.align(m, d)['components']
    assert len(comp) == 1 and comp[0]['shape'] == '1:2' and comp[0]['status'] == 'matched'
    m2 = m + [_blk('m3', 'marker', [0, 300, 100, 340], 'lonely', order=1)]
    assert sorted(c['shape'] for c in align.align(m2, d)['components']) == ['1:0', '1:2']


# ---------------------------------------------------------------- projection end-to-end
def test_everything_preserved_and_placed(built):
    out, c, md = built
    hdr = [b for b in c['blocks'] if 'RUNNING_HEADER_EMPTY' in b['reasons']]
    # an UNREAD header (no engine read its text) with nothing else to support it (no twin on another page, no
    # repeated reading) is not removed on the engine label alone: it stays visible as an image excerpt
    assert hdr and all(not b.get('excluded') and b['source_crop']['path'] in md for b in hdr)
    only = [b for b in c['blocks'] if 'SINGLE_ENGINE_DOCLING' in b['reasons']]
    assert len(only) == 1 and only[0]['candidates'][0]['text'] == 'Beta paragraph only docling saw'
    order = [b['id'] for b in c['blocks']]
    para = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    fig = next(b for b in c['blocks'] if b['kind'] == 'figure')
    assert order.index(para['id']) < order.index(only[0]['id']) < order.index(fig['id'])
    bottom = [b for b in c['blocks'] if 'UNCOVERED_INK_REGION' in b['reasons']]
    assert bottom and 'MISSING_BOTTOM_TEXT' in bottom[-1]['reasons'] and bottom[-1]['geometry']['bbox'][1] > 0.75 * H
    for b in c['blocks']:
        if b['kind'] == 'excerpt' and not b.get('excluded'):
            assert (out / b['source_crop']['path']).exists() and b['source_crop']['path'] in md


def test_caption_bounded_inside_figure(built):
    out, c, md = built
    cap = next(b for b in c['blocks'] if b['kind'] == 'caption')
    fig = next(b for b in c['blocks'] if b['kind'] == 'figure')
    assert cap['relations']['caption_for'] == fig['id']
    fig_md = re.search(r'<figure>.*?</figure>', md, re.S).group(0)
    assert '<figcaption>' in fig_md and 'A caption for the figure' in fig_md
    assert 'A caption for the figure' not in md.replace(fig_md, '')                 # never in the running text


def test_boundaries_schema_and_status(built):
    out, c, md = built
    for tag in ('BLOCK', 'FIGURE', 'EXCERPT', 'CAPTION', 'PAGE'):
        assert md.count(f'<!-- {tag}_START') == md.count(f'<!-- {tag}_END')
    audit.validate_schema(c)
    assert not [f for f in c['findings'] if f['type'].startswith('INTEGRITY')]
    assert c['release_status'] == 'REVIEW_REQUIRED'


def test_missing_asset_is_critical(built):
    out, c, md = built
    ex = next(b for b in c['blocks'] if b['kind'] == 'excerpt')
    (out / ex['source_crop']['path']).unlink()
    f = audit.pre_render(c, out)
    assert any(x['type'] == 'INTEGRITY_ASSET_MISSING' for x in f)
    assert release_status(c['blocks'], f) == 'DRAFT'


def test_marker_failure_and_bad_json_give_full_page(tmp_path):
    for i, kw in enumerate(({'marker_status': 'FAILED'}, {'marker': '{not json'})):
        out = make_run(tmp_path / str(i), **kw)
        assert build(out) == 0
        c, md = load(out)
        full = [b for b in c['blocks'] if 'CONSERVATIVE_FULL_PAGE_EXCERPT' in b['reasons']]
        assert full and full[0]['source_crop']['path'] in md and c['release_status'] == 'DRAFT'


def test_chemical_drawing_with_models_unavailable(tmp_path):
    out = make_run(tmp_path, marker=marker_json(fig_type='ChemicalBlock'))
    assert build(out) == 0
    c, md = load(out)
    chem = next(b for b in c['blocks'] if b['kind'] == 'molecular_structure')
    assert chem['chosen']['type'] == 'image_only' and chem['display_crop'] and chem['review'] == 'recommended'
    assert 'MOLSCRIBE_UNAVAILABLE' in chem['reasons'] and any(f['type'] == 'MODEL_UNAVAILABLE' for f in c['findings'])


def test_chemical_equation_keeps_marker_latex_with_crop(tmp_path):
    m = marker_json(fig_type='ChemicalBlock')
    m['children'][0]['children'][2]['html'] = ('<p><math display="block">\\text{NaOH}(s) \\longrightarrow '
                                               '\\text{Na}^+(aq) + \\text{OH}^-(aq)</math></p>')
    out = make_run(tmp_path, marker=m)
    assert build(out) == 0
    eq = next(b for b in load(out)[0]['blocks'] if b['kind'] == 'chemical_reaction')
    assert eq['display_crop'] and eq['review'] == 'required'          # UniMERNet unavailable -> no independent check
    assert not any(x['engine'] == 'molscribe' for x in eq['candidates'])


def test_symbol_conflict_uses_second_engine_evidence(tmp_path):
    m = marker_json()
    m['children'][0]['children'][1]['html'] = '<p>Alpha paragraph line one about DNA base Γ Alpha paragraph line two</p>'
    from conftest import docling_json
    d = docling_json()
    d['texts'][0]['text'] = 'Alpha paragraph line one about DNA base G'
    d['texts'][1]['text'] = 'Alpha paragraph line two'
    out = make_run(tmp_path, marker=m, docling=d)
    assert build(out) == 0
    c, _ = load(out)
    para = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    assert 'SYMBOL_READING_CONFLICT' in para['reasons'] and para['review'] == 'required'
    assert any(f['type'] == 'NOTATION_SYMBOL_CONFLICT' and f['severity'] == 'critical' for f in c['findings'])


# ---------------------------------------------------------------- human input via events
def test_decisions_replay_without_ocr(built, monkeypatch):
    out, c, md = built
    only = next(b for b in c['blocks'] if 'SINGLE_ENGINE_DOCLING' in b['reasons'])
    EventStore(out).append({'type': 'decision', 'block_id': only['id'], 'action': 'replace_text',
                            'value': 'Beta paragraph (reviewed)', 'prev_hash': only['machine_hash']})
    from scientific_ocr.adapters import extraction
    monkeypatch.setattr(extraction, 'extract_page', lambda *a, **k: pytest.fail('projection must not run OCR'))
    assert build(out) == 0
    c2, md2 = load(out)
    b2 = next(b for b in c2['blocks'] if b['id'] == only['id'])
    assert b2['chosen']['text'] == 'Beta paragraph (reviewed)' and b2['review'] == 'approved'
    assert 'Beta paragraph (reviewed)' in md2 and [b['id'] for b in c2['blocks']] == [b['id'] for b in c['blocks']]


def test_stale_decisions_do_not_block_the_book(built):
    out, c, md = built
    blk = c['blocks'][1]
    EventStore(out).append({'type': 'decision', 'block_id': 'p1_gone', 'action': 'approve'},
                           {'type': 'decision', 'block_id': blk['id'], 'action': 'approve', 'prev_hash': 'old'})
    assert build(out) == 0
    c2, _ = load(out)
    assert len(c2['human']['stale']) == 2
    b2 = next(b for b in c2['blocks'] if b['id'] == blk['id'])
    assert 'DECISION_STALE' in b2['reasons'] and b2['review'] == 'required'


def test_corrupt_event_log_leaves_outputs_untouched(built):
    out, c, md = built
    (out / 'events.jsonl').write_text('{broken\n')
    assert build(out) == 4 and load(out)[1] == md


def test_undo_restores_previous_projection(built):
    out, c, md = built
    only = next(b for b in c['blocks'] if 'SINGLE_ENGINE_DOCLING' in b['reasons'])
    es = EventStore(out)
    es.append({'type': 'decision', 'block_id': only['id'], 'action': 'replace_text', 'value': 'X',
               'prev_hash': only['machine_hash']})
    es.undo()
    assert build(out) == 0
    assert next(b for b in load(out)[0]['blocks'] if b['id'] == only['id'])['chosen']['type'] == 'image_only'
    es.redo()
    assert build(out) == 0
    assert next(b for b in load(out)[0]['blocks'] if b['id'] == only['id'])['chosen']['text'] == 'X'


def test_verified_only_after_full_review(built):
    out, c, md = built
    EventStore(out).append(*[{'type': 'decision', 'block_id': b['id'], 'prev_hash': b['machine_hash'],
                              'action': 'mark_image_only' if b['chosen']['type'] == 'image_only' else 'approve',
                              'approved_hash': b['auto_hash']} for b in c['blocks']])
    assert build(out) == 0
    assert load(out)[0]['release_status'] == 'VERIFIED'


def test_book_rule_applies_before_checks_and_keeps_raw(tmp_path):
    m = marker_json()
    m['children'][0]['children'][1]['html'] = '<p>Η βάση Γ ζευγαρώνει με την Κ στο DNA.</p>'
    out = make_run(tmp_path, marker=m)
    EventStore(out).append({'type': 'rule_add', 'rule': {'id': 'r1', 'find': 'Γ', 'replace': 'G', 'context': '',
                                                         'exclude': []}},
                           {'type': 'rule_add', 'rule': {'id': 'r2', 'find': 'Κ', 'replace': 'C', 'context': '',
                                                         'exclude': []}})
    assert build(out) == 0
    b = next(x for x in load(out)[0]['blocks'] if x.get('rule_corrections'))
    assert b['chosen']['text'].startswith('Η βάση G ζευγαρώνει με την C') and 'Γ' in b['candidates'][0]['text']


# ---------------------------------------------------------------- page-structure review
def _fake_region_ocr(out, region, text):
    """Write region OCR raw output as the extraction adapter would (Marker forced Text block + Docling)."""
    rw, rh = region['bbox'][2] - region['bbox'][0], region['bbox'][3] - region['bbox'][1]
    mdir = out / 'raw' / 'regions' / 'marker-x' / 'p0001-k'
    ddir = out / 'raw' / 'regions' / 'docling-x' / 'p0001-k'
    mdir.mkdir(parents=True)
    ddir.mkdir(parents=True)
    (mdir / 'out.json').write_text(json.dumps({'block_type': 'Document', 'children': [{
        'id': '/page/0/Page/0', 'block_type': 'Page', 'bbox': [0, 0, rw, rh], 'html': '', 'children': [
            {'id': '/page/0/Text/0', 'block_type': 'Text', 'bbox': [0, 0, rw, rh], 'html': f'<p>{text}</p>',
             'children': None, 'images': {}}]}]}))
    (ddir / 'out.json').write_text(json.dumps({'body': {'children': [{'$ref': '#/texts/0'}]}, 'texts': [
        {'self_ref': '#/texts/0', 'label': 'text', 'text': text, 'children': [],
         'prov': [{'page_no': 1, 'bbox': {'l': 0, 't': rh, 'r': rw, 'b': 0, 'coord_origin': 'BOTTOMLEFT'}}]}]}))
    idx = {region['id']: {'bbox': region['bbox'], 'kind': region['kind'], 'height': rh, 'engines': {
        'marker': {'status': 'OK', 'raw_json': str((mdir / 'out.json').relative_to(out))},
        'docling': {'status': 'OK', 'raw_json': str((ddir / 'out.json').relative_to(out))}}}}
    (out / 'raw' / 'regions' / 'index.json').write_text(json.dumps(idx))


def test_reviewer_region_replaces_split_boxes(built):
    out, c, md = built
    region = {'id': 'g1', 'page': 1, 'bbox': [50, 75, 410, 185], 'kind': 'text'}
    es = EventStore(out)
    es.append({'type': 'region_add', 'region': region})
    assert build(out) == 0
    c2, md2 = load(out)
    reg = next(b for b in c2['blocks'] if b['source'] == 'region')
    assert 'REGION_OCR_PENDING' in reg['reasons'] and reg['display_crop']           # nothing lost while pending
    assert c2['pages'][0]['replaced_by_region'] and reg['source_crop']['path'] in md2
    _fake_region_ocr(out, region, 'Alpha paragraph line one about DNA Alpha paragraph line two continues '
                                  'Beta paragraph only docling saw')
    assert build(out) == 0
    reg = next(b for b in load(out)[0]['blocks'] if b['source'] == 'region')
    assert reg['chosen']['type'] == 'text' and 'Beta paragraph' in reg['chosen']['text']
    assert 'HUMAN_REGION' in reg['reasons'] and reg['match']['status'] == 'matched'
    assert not any('SINGLE_ENGINE_DOCLING' in b['reasons'] for b in load(out)[0]['blocks'])   # replaced


def test_hidden_block_is_listed_not_lost(built):
    out, c, md = built
    only = next(b for b in c['blocks'] if 'SINGLE_ENGINE_DOCLING' in b['reasons'])
    EventStore(out).append({'type': 'block_hide', 'block_id': only['id']})
    assert build(out) == 0
    c2, _ = load(out)
    assert only['id'] not in {b['id'] for b in c2['blocks']}
    assert c2['pages'][0]['hidden'][0]['block_id'] == only['id']


def test_layout_split_suggestion(tmp_path):
    """L-shaped paragraph: the layout model cut the bottom lines into two side-by-side boxes, both garbled."""
    m = marker_json()
    kids = m['children'][0]['children']
    kids.append({'id': '/page/0/Text/9', 'block_type': 'Text', 'bbox': [55, 600, 300, 700], 'html': '<p>x</p>',
                 'children': None, 'images': {}})
    kids.append({'id': '/page/0/Text/10', 'block_type': 'Text', 'bbox': [305, 602, 560, 698], 'html': '<p>y</p>',
                 'children': None, 'images': {}})
    for i, y in enumerate((340, 420, 500)):          # ordinary prose sets the page's text-density baseline
        kids.append({'id': f'/page/0/Text/{11 + i}', 'block_type': 'Text', 'bbox': [55, y, 560, y + 75],
                     'html': '<p>' + 'normal dense paragraph text ' * 40 + '</p>', 'children': None, 'images': {}})
    out = make_run(tmp_path, marker=m)
    assert build(out) == 0
    c, _ = load(out)
    sug = c['pages'][0]['suggestions']
    assert sug and sug[0]['kind'] == 'merge_reocr' and sug[0]['bbox'][0] <= 55 and sug[0]['bbox'][2] >= 560
    assert all('LAYOUT_SPLIT_SUSPECTED' in b['reasons'] for b in c['blocks'] if b['id'] in sug[0]['members'])


def test_source_pdf_unchanged(built):
    out, c, md = built
    assert pdfio.sha256_file(out / 'source' / 'original.pdf') == c['source']['sha256']
    assert W == 600


def test_split_paragraph_around_figure_is_linked_and_marked(tmp_path):
    """A sentence interrupted by a figure: linked in canonical; in the reader's Markdown the paragraph is ONE
    paragraph and the figure (with its caption) follows it."""
    m = marker_json()
    kids = m['children'][0]['children']
    kids[1]['html'] = '<p>Alpha paragraph line one about DNA whose sentence is cut by the</p>'
    kids.append({'id': '/page/0/Text/7', 'block_type': 'Text', 'bbox': [55, 340, 400, 380],
                 'html': '<p>figure and continues here after the caption.</p>', 'children': None, 'images': {}})
    out = make_run(tmp_path, marker=m)
    assert build(out) == 0
    c, md = load(out)
    a = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    z = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('figure and'))
    fig = next(b for b in c['blocks'] if b['kind'] == 'figure')
    assert a['relations']['continues_in'] == z['id'] and z['relations']['continues_from'] == a['id']
    assert fig['id'] in z['relations']['interrupted_by'] and fig['flow'] == 'insert'
    assert 'cut by the figure and continues here after the caption.' in md          # joined sentence
    assert md.index('continues here after the caption.') < md.index('<figure>')      # the insert follows it
    hdr = next(b for b in c['blocks'] if b.get('role') == 'page_header')
    assert hdr['flow'] != 'furniture' and not hdr.get('excluded')     # unread and unconfirmed: stays visible


def test_reader_markdown_is_clean_well_formed_and_complete(built):
    """Both separator styles carry the same content; tags are balanced; nothing internal leaks."""
    import xml.etree.ElementTree as ET

    from scientific_ocr.pipeline.markdown import markdown
    out, c, md = built
    tagged, commented = markdown(c, images=True, style='tags'), markdown(c, images=True, style='comments')
    body = re.sub(r'&(?!amp;|lt;|gt;|quot;)', '&amp;', tagged)
    ET.fromstring('<doc>' + re.sub(r'<(?!/?[a-z][\w-]*[ />])', '&lt;', body) + '</doc>')   # raises if unbalanced
    strip = lambda t: ' '.join(re.sub(r'<[^>]+>|<!--.*?-->|\*', ' ', t, flags=re.S).split())
    assert strip(tagged) == strip(commented)                                      # same content, other separators
    for t in (tagged, commented):
        assert not re.search(r'p\d+_[0-9a-f]{8}(?!\.png)|OCR|review|provenance|page=', re.sub(r'\]\([^)]+\)', ']', t))
    assert not [f for f in c['findings'] if f['type'].startswith(('INTEGRITY', 'EXPORT_', 'CAPTION_BLEED'))]

def test_section_hierarchy_from_numbering_and_engine_levels(tmp_path):
    m = marker_json()
    kids = m['children'][0]['children']
    kids[1:1] = [{'id': '/page/0/SectionHeader/20', 'block_type': 'SectionHeader', 'bbox': [55, 45, 400, 60],
                  'html': '<h2>5 DNA and chromosomes</h2>', 'children': None, 'images': {}},
                 {'id': '/page/0/SectionHeader/21', 'block_type': 'SectionHeader', 'bbox': [55, 62, 400, 78],
                  'html': '<h3>5.1 The structure of DNA</h3>', 'children': None, 'images': {}}]
    out = make_run(tmp_path, marker=m)
    assert build(out) == 0
    c, md = load(out)
    lv = {s['title'][:5]: s['level'] for s in c['sections']}
    assert lv == {'5 DNA': 1, '5.1 T': 2}
    sub = next(s for s in c['sections'] if s['level'] == 2)
    para = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    assert para['section_path'][-1] == sub['id']
    assert '# 5 DNA and chromosomes' in md and '## 5.1 The structure of DNA' in md        # sections = headings
    h = next(b for b in c['blocks'] if b['id'] == sub['id'])
    EventStore(out).append({'type': 'decision', 'block_id': h['id'], 'action': 'set_heading_level', 'level': 0})
    assert build(out) == 0
    assert len(load(out)[0]['sections']) == 1                                   # demoted by the reviewer


def test_one_decision_resolves_overlapping_open_segments(built):
    """Deciding a region once settles open segments printed inside it (no double review across engines)."""
    out, c, md = built
    fig = next(b for b in c['blocks'] if b['kind'] == 'figure')
    inner = {'type': 'region_add', 'region': {'id': 'gx', 'page': 1, 'bbox': [100, 210, 300, 290], 'kind': 'text'}}
    es = EventStore(out)
    es.append(inner)                                       # an open (pending OCR) segment inside the figure
    assert build(out) == 0
    reg = next(b for b in load(out)[0]['blocks'] if b['source'] == 'region')
    assert reg['review'] == 'required'
    es.append({'type': 'decision', 'block_id': fig['id'], 'action': 'mark_image_only', 'prev_hash': fig['machine_hash']})
    assert build(out) == 0
    reg = next(b for b in load(out)[0]['blocks'] if b['source'] == 'region')
    assert reg['review'] == 'approved' and reg['relations']['resolved_by'] == fig['id']


# ---------------------------------------------------------------- content decisions: value vs approval
def test_approve_never_replaces_a_human_value_and_revert_restores_machine(built):
    out, c, md = built
    only = next(b for b in c['blocks'] if 'SINGLE_ENGINE_DOCLING' in b['reasons'])
    es = EventStore(out)
    es.append({'type': 'decision', 'block_id': only['id'], 'action': 'replace_text', 'value': 'fixed',
               'prev_hash': only['machine_hash']},
              {'type': 'decision', 'block_id': only['id'], 'action': 'approve', 'prev_hash': only['machine_hash'],
               'approved_hash': only['auto_hash']})        # e.g. an old client pressing "approve" afterwards
    assert build(out) == 0
    c2, _ = load(out)
    b = next(x for x in c2['blocks'] if x['id'] == only['id'])
    assert b['chosen']['text'] == 'fixed' and b['content_source'] == 'human'
    assert any(x['action'] == 'approve' for x in c2['human']['superseded'])
    es.append({'type': 'decision', 'block_id': only['id'], 'action': 'revert'})
    assert build(out) == 0
    b = next(x for x in load(out)[0]['blocks'] if x['id'] == only['id'])
    assert b['chosen']['type'] == 'image_only' and b['review'] == 'required'


# ---------------------------------------------------------------- caption groups
def test_split_caption_title_is_chained_and_rendered_as_one_group(tmp_path):
    """Layout split 'Fig. 1' (detected as a heading) from its caption body: both join the figure's group."""
    m = marker_json()
    kids = m['children'][0]['children']
    kids[3]['html'] = '<p>A caption body for the figure</p>'
    kids.insert(3, {'id': '/page/0/SectionHeader/9', 'block_type': 'SectionHeader', 'bbox': [55, 303, 140, 309],
                    'html': '<h4>Fig. 1</h4>', 'children': None, 'images': {}})
    out = make_run(tmp_path, marker=m)
    assert build(out) == 0
    c, md = load(out)
    fig = next(b for b in c['blocks'] if b['kind'] == 'figure')
    caps = [next(b for b in c['blocks'] if b['id'] == i) for i in fig['relations']['captions']]
    assert [x['caption_role'] for x in caps] == ['title', 'text'] and caps[0]['caption_label'] == 'Fig. 1'
    seg = re.search(r'<figure>.*?</figure>', md, re.S).group(0)
    assert 'Fig. 1' in seg and 'A caption body for the figure' in seg and seg.index('Fig. 1') < seg.index('A caption body')
    assert not any(s['title'] == 'Fig. 1' for s in c['sections'])           # no longer a section heading


def test_reviewer_links_caption_to_any_insert_and_sets_roles(tmp_path):
    m = marker_json(fig_type='ChemicalBlock')
    m['children'][0]['children'][2]['html'] = ('<p><math display="block">\\text{NaOH}(s) \\longrightarrow '
                                               '\\text{Na}^+(aq)</math></p>')
    out = make_run(tmp_path, marker=m)
    assert build(out) == 0
    c, _ = load(out)
    eq = next(b for b in c['blocks'] if b['kind'] == 'chemical_reaction')
    cap = next(b for b in c['blocks'] if b['kind'] == 'caption')
    assert not cap['relations'].get('caption_for')                         # equations are not auto-captioned
    es = EventStore(out)
    es.append({'type': 'decision', 'block_id': cap['id'], 'action': 'associate_caption', 'figure_id': eq['id']},
              {'type': 'decision', 'block_id': cap['id'], 'action': 'set_caption_role', 'role': 'title'})
    assert build(out) == 0
    c, md = load(out)
    cap = next(b for b in c['blocks'] if b['id'] == cap['id'])
    assert cap['relations']['caption_for'] == eq['id'] and cap['caption_role'] == 'title'
    seg = re.search(r'<figure>.*?</figure>', md, re.S).group(0)
    assert 'NaOH' in seg and '<figcaption>' in seg                                   # the equation owns the caption
    es.append({'type': 'decision', 'block_id': cap['id'], 'action': 'detach_caption'})
    assert build(out) == 0
    assert not next(b for b in load(out)[0]['blocks'] if b['id'] == cap['id'])['relations'].get('caption_for')


def test_captioned_equation_keeps_its_latex(tmp_path):
    m = marker_json(fig_type='ChemicalBlock')
    m['children'][0]['children'][2]['html'] = ('<p><math display="block">\\text{NaOH}(s) \\longrightarrow '
                                               '\\text{Na}^+(aq)</math></p>')
    out = make_run(tmp_path, marker=m)
    build(out)
    c, _ = load(out)
    eq = next(b for b in c['blocks'] if b['kind'] == 'chemical_reaction')
    cap = next(b for b in c['blocks'] if b['kind'] == 'caption')
    EventStore(out).append({'type': 'decision', 'block_id': cap['id'], 'action': 'associate_caption', 'figure_id': eq['id']})
    build(out)
    md = load(out)[1]
    seg = re.search(r'<figure>.*?</figure>', md, re.S).group(0)
    assert 'NaOH' in seg and '$$' in seg and '<figcaption>' in seg


# ---------------------------------------------------------------- document-level structure (multi-page)
def _doc_blk(page, i, kind, text, y, h=20, **extra):
    from scientific_ocr.core.model import new_block
    chosen = {'type': 'text', 'text': text} if text is not None else None
    b = new_block(f'p{page}_{i}', page, kind, [50, y, 550, y + h], source='marker', chosen=chosen, **extra)
    b['reading_order'] = i
    return b


def _book_pages(n, header=None, footer=None, edge=None, offset=100, extra=None):
    """Synthetic multi-page book: body text in a frame [60..540] x [80..720] on a 600x800 page."""
    dims = {p: (600, 800) for p in range(1, n + 1)}
    blocks = []
    for p in range(1, n + 1):
        i = 0
        def add(kind, text, bbox, **kw):
            nonlocal i
            from scientific_ocr.core.model import new_block
            b = new_block(f'p{p}_{i}', p, kind, bbox, source='marker',
                          chosen={'type': 'text', 'text': text} if text is not None else None, **kw)
            b['reading_order'] = i
            b['source_crop'] = {'path': f'assets/p{p}_{i}.png'}
            blocks.append(b)
            i += 1
        if header:
            t = header(p)
            add('paragraph', t, [60, 30, 400, 45]) if t else None
        add('paragraph', 'Σώμα κειμένου με αρκετό μήκος ώστε να ορίζει το πλαίσιο της σελίδας. ' * 3, [60, 80, 540, 400])
        add('paragraph', 'Δεύτερη παράγραφος του σώματος, επίσης αρκετά μεγάλη για να μετρήσει ως περιεχόμενο. ' * 3, [60, 420, 540, 720])
        if footer:
            add('paragraph', footer(p, offset), [280, 750, 320, 765])
        if edge:
            add('paragraph', edge(p, offset), [570, 380, 590, 420])
        for e in (extra or {}).get(p, []):
            add(*e)
    return blocks, dims


def _excluded(blocks):
    return {(b['page'], b['chosen'].get('text')) for b in blocks if b.get('excluded')}


def test_running_heads_and_page_numbers_are_excluded_body_untouched():
    from scientific_ocr.pipeline import furniture
    blocks, dims = _book_pages(4, header=lambda p: 'ΚΕΦΑΛΑΙΟ 4 ΟΞΕΑ ΚΑΙ ΒΑΣΕΙΣ', footer=lambda p, o: str(p + o))
    fixes = furniture.classify(blocks, dims, set())
    ex = [b for b in blocks if b.get('excluded')]
    assert len(ex) == 8 and all(b['review'] == 'not_needed' and b['excluded']['signals'] for b in ex)
    nums = [b for b in ex if b['excluded']['as'] == 'page_number']
    assert len(nums) == 4 and all('page_number' in b['excluded']['signals'] for b in nums)   # shared offset
    assert not any(b.get('excluded') for b in blocks if len(b['chosen'].get('text', '')) > 100)
    restored = [{**b, 'role': None, 'excluded': None, 'review': 'not_needed', 'reasons': []} for b in blocks]
    furniture.classify(restored, dims, {fixes[0]['key']})
    assert not restored[0].get('excluded') and restored[0]['role'] is None                  # dismissal = restore


def test_page_numbers_on_the_outer_edge_and_alternating_heads():
    from scientific_ocr.pipeline import furniture
    heads = lambda p: 'Χημεία — Γενικές αρχές' if p % 2 == 0 else '9.9 ΤΥΠΙΚΟ ΦΟΡΤΙΟ ΚΑΙ ΤΥΠΟΙ LEWIS'
    blocks, dims = _book_pages(6, header=heads, edge=lambda p, o: str(p + o))
    furniture.classify(blocks, dims, set())
    ex = _excluded(blocks)
    assert {(p, str(p + 100)) for p in range(1, 7)} <= ex                                  # side numbers (sequence)
    assert sum(1 for p, t in ex if t == heads(p)) == 6                                      # verso + recto heads


def test_lookalikes_that_are_content_survive():
    from scientific_ocr.pipeline import furniture
    extra = {1: [('paragraph', '12.', [20, 300, 45, 315]),                                  # exercise number, left margin
                 ('paragraph', '(4.3)', [560, 200, 590, 215]),                              # equation number
                 ('paragraph', '¹ Βλ. Κεφάλαιο 3.', [60, 735, 300, 748])],                  # short footnote
             2: [('heading', 'Ισχυρά και ασθενή οξέα', [60, 40, 400, 75])]}                  # section title above body
    blocks, dims = _book_pages(3, extra=extra)
    blocks[-2]['candidates'] = [{'engine': 'marker', 'label': 'PageFooter'}]               # engine mislabels footnote
    furniture.classify(blocks, dims, set())
    assert _excluded(blocks) == set()


def test_engine_header_inside_the_text_frame_is_given_back_to_the_text():
    from scientific_ocr.pipeline import furniture
    blocks, dims = _book_pages(2, extra={1: [('paragraph', 'Σημείωση', [60, 405, 200, 418])]})
    note = next(b for b in blocks if b['chosen'].get('text') == 'Σημείωση')
    note['role'] = 'page_header'
    furniture.classify(blocks, dims, set())
    assert note['role'] is None and not note.get('excluded') and 'ENGINE_FURNITURE_IN_BODY' in note['reasons']


def test_running_title_repeating_a_heading_is_excluded_even_on_one_page():
    from scientific_ocr.pipeline import furniture
    blocks, dims = _book_pages(1, header=lambda p: 'ΤΥΠΙΚΟ ΦΟΡΤΙΟ ΚΑΙ ΤΥΠΟΙ LEWIS',
                               extra={1: [('heading', '9.9 Τυπικό φορτίο και τύποι Lewis', [60, 402, 400, 418])]})
    furniture.classify(blocks, dims, set())
    assert _excluded(blocks) == {(1, 'ΤΥΠΙΚΟ ΦΟΡΤΙΟ ΚΑΙ ΤΥΠΟΙ LEWIS')}


def test_caption_across_page_break_is_linked_and_flagged():
    from scientific_ocr.pipeline import captions, document
    fig = _doc_blk(1, 1, 'figure', None, 500, h=280)
    cap = _doc_blk(2, 0, 'caption', 'ΣΧΗΜΑ 4.9 Αντίδραση νιτρικού οξέος.', 40)
    blocks = [_doc_blk(1, 0, 'paragraph', 'Κείμενο.', 100), fig, cap, _doc_blk(2, 1, 'paragraph', 'Συνέχεια.', 100)]
    fixes = document.captions_across_pages(blocks, set())
    captions.finalize(blocks)
    assert cap['relations']['caption_for'] == fig['id'] and fig['relations']['captions'] == [cap['id']]
    assert 'CAPTION_ACROSS_PAGES' in cap['reasons'] and 'CAPTION_WITHOUT_FIGURE' not in cap['reasons']
    assert cap['review'] == 'recommended' and fixes[0]['pages'] == [1, 2]


def test_reviewer_caption_decision_wins_over_document_stage():
    from scientific_ocr.pipeline import document
    fig = _doc_blk(1, 1, 'figure', None, 500, h=280)
    cap = _doc_blk(2, 0, 'caption', 'ΣΧΗΜΑ 1', 40, decisions=[{'action': 'detach_caption'}])
    assert document.captions_across_pages([fig, cap], set()) == [] and not cap['relations'].get('caption_for')


def test_box_continued_on_next_page_by_repeated_label():
    from scientific_ocr.pipeline import document
    a0, a1 = _doc_blk(1, 0, 'heading', 'ΠΑΡΑΔΕΙΓΜΑ 4.3', 600), _doc_blk(1, 1, 'paragraph', 'Εκφώνηση', 640)
    b0, b1 = _doc_blk(2, 0, 'heading', 'ΠΑΡΑΔΕΙΓΜΑ 4.3 (συνέχεια)', 40), _doc_blk(2, 1, 'paragraph', 'Λύση', 80)
    groups = [{'id': 'A1', 'key': '1', 'type': 'labelled_box', 'name': 'ΠΑΡΑΔΕΙΓΜΑ 4.3', 'members': [a0['id'], a1['id']], 'rule': 'layout'},
              {'id': 'A2', 'key': '2', 'type': 'labelled_box', 'name': 'ΠΑΡΑΔΕΙΓΜΑ 4.3 (συνέχεια)', 'members': [b0['id'], b1['id']], 'rule': 'layout'}]
    out, fixes = document.boxes_across_pages(groups, [a0, a1, b0, b1], set())
    assert len(out) == 1 and out[0]['members'] == [a0['id'], a1['id'], b0['id'], b1['id']]
    assert fixes[0]['kind'] == 'box_across_pages' and fixes[0]['pages'] == [1, 2]


def test_box_subheadings_leave_the_book_hierarchy():
    from scientific_ocr.pipeline import document, sections
    sec = _doc_blk(1, 0, 'heading', 'Τυπικά φορτία', 50, engine_heading_levels={'marker': 2})
    box = _doc_blk(1, 1, 'heading', 'ΠΑΡΑΔΕΙΓΜΑ 9.11', 300, engine_heading_levels={'marker': 2})
    sub = _doc_blk(1, 2, 'heading', 'ΑΚΟΛΟΥΘΗΤΕΑ ΣΤΡΑΤΗΓΙΚΗ', 340, engine_heading_levels={'marker': 3})
    nxt = _doc_blk(1, 3, 'heading', 'Εξαιρέσεις στον κανόνα', 600, engine_heading_levels={'marker': 3})
    blocks = [sec, box, sub, nxt]
    sections.annotate(blocks)
    fixes = document.box_headings([{'type': 'labelled_box', 'name': 'ΠΑΡΑΔΕΙΓΜΑ 9.11', 'members': [box['id'], sub['id']]}], blocks, set())
    toc = sections.annotate(blocks)
    lv = {s['title']: s['level'] for s in toc}
    assert lv['ΑΚΟΛΟΥΘΗΤΕΑ ΣΤΡΑΤΗΓΙΚΗ'] == lv['ΠΑΡΑΔΕΙΓΜΑ 9.11'] + 1 and fixes[0]['kind'] == 'box_heading'
    assert lv['Εξαιρέσεις στον κανόνα'] == lv['Τυπικά φορτία'] + 1      # the ranking ignores the box sub-heading


def test_unnumbered_headings_take_level_of_numbered_style_and_jumps_close():
    from scientific_ocr.pipeline import document, sections
    hs = [_doc_blk(1, 0, 'heading', '5 Το DNA', 10, engine_heading_levels={'marker': 1}),
          _doc_blk(1, 1, 'heading', '5.1 Δομή', 100, engine_heading_levels={'marker': 3}),
          _doc_blk(2, 0, 'heading', '5.2 Αντιγραφή', 100, engine_heading_levels={'marker': 3}),
          _doc_blk(2, 1, 'heading', 'Επισκευή', 300, engine_heading_levels={'marker': 3}),
          _doc_blk(3, 0, 'heading', 'Ορολογία', 100, engine_heading_levels={'marker': 5})]
    sections.annotate(hs)
    assert hs[3]['heading_level'] == 2 and hs[3]['heading_level_source'] == 'style_of_numbered'
    assert hs[4]['heading_level'] == 3                                   # no deeper than parent + 1
    assert not document.heading_fixes(hs)          # the engine already said level 2: nothing was corrected
    sections.annotate(hs, {sections.revoke_key(hs[3]['id'], 2)})
    assert hs[3]['heading_level_source'] == 'revoked'


def test_heading_numbering_duplicates_and_backward_steps_are_flagged_not_fixed():
    from scientific_ocr.pipeline import document, sections
    hs = [_doc_blk(1, 0, 'heading', '4.1 Οξέα', 10), _doc_blk(1, 1, 'heading', '4.3 Βάσεις', 300),
          _doc_blk(2, 0, 'heading', '4.2 Άλατα', 10), _doc_blk(2, 1, 'heading', '4.3 Ρυθμιστικά', 300)]
    toc = sections.annotate(hs)
    issues = document.heading_issues(hs, toc)
    assert [i['type'] for i in issues] == ['gap', 'out_of_order', 'duplicate']
    assert 'HEADING_NUMBER_OUT_OF_ORDER' in hs[2]['reasons'] and hs[2]['chosen']['text'] == '4.2 Άλατα'


def test_unread_header_is_excluded_by_position_of_confirmed_headers_elsewhere():
    from scientific_ocr.pipeline import furniture
    blocks, dims = _book_pages(3, header=lambda p: None if p == 3 else 'ΚΕΦΑΛΑΙΟ 5 DNA',
                               extra={3: [('excerpt', None, [62, 31, 398, 44])]})          # OCR read nothing here
    furniture.classify(blocks, dims, set())
    unread = next(b for b in blocks if b['page'] == 3 and b['kind'] == 'excerpt')
    assert unread['excluded']['signals'] == ['position_twin']


def test_text_at_the_running_head_position_is_never_dropped_by_position_alone():
    from scientific_ocr.pipeline import furniture
    blocks, dims = _book_pages(4, header=lambda p: 'ΚΕΦΑΛΑΙΟ 5 DNA' if p != 2 else None,
                               extra={2: [('paragraph', 'φτούμε ποιες ακόμη οδηγίες θα μπορούσε να περιέχει.', [62, 31, 398, 44])]})
    furniture.classify(blocks, dims, set())
    line = next(b for b in blocks if b['chosen'].get('text', '').startswith('φτούμε'))
    assert not line.get('excluded') and sum(1 for b in blocks if b.get('excluded')) == 3


def test_concurrent_builds_of_one_book_are_serialised(built):
    import threading
    out, c, md = built
    codes = []
    ts = [threading.Thread(target=lambda: codes.append(build(out))) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    c2, _ = load(out)
    assert codes == [0, 0, 0] and not [f for f in c2['findings'] if f['type'].startswith('INTEGRITY')]


# ---------------------------------------------------------------- export quality
def test_approving_text_makes_it_the_content_in_exports():
    from scientific_ocr.core.model import new_block
    from scientific_ocr.pipeline import overlay
    txt = new_block('p1_a', 1, 'caption', [0, 0, 1, 1], source='region', chosen={'type': 'text', 'text': 'ΣΧΗΜΑ 4.9'},
                    display_crop=True)
    drawing = new_block('p1_b', 1, 'lewis_structure', [0, 0, 1, 1], source='marker', display_crop=True)
    for b in (txt, drawing):
        overlay.COMMANDS['approve'](b, {'action': 'approve'}, None)
    assert txt['review'] == 'approved' and not txt['display_crop']       # verified text replaces the excerpt
    assert drawing['display_crop']                                        # an image-only drawing keeps its image


def test_inline_formatting_is_rendered_and_everything_else_escaped():
    from scientific_ocr.pipeline.html_preview import inline_html
    h = inline_html('**ΣΧΗΜΑ 4.9** HC<sub>2</sub>O <script>x</script> *πλάγια* $a*b*c$ 2*3*4')
    assert '<b>ΣΧΗΜΑ 4.9</b>' in h and '<sub>2</sub>' in h and '&lt;script&gt;' in h and '<i>πλάγια</i>' in h
    assert '$a*b*c$' in h and '2*3*4' in h                                     # maths and arithmetic untouched


def test_formulas_that_the_renderer_cannot_typeset_show_their_source_image():
    from scientific_ocr import config
    from scientific_ocr.core.model import new_block
    from scientific_ocr.pipeline import latex_check
    if not config.node():
        pytest.skip('node not installed')
    ok = new_block('p1_a', 1, 'math', [0, 0, 1, 1], source='marker', chosen={'type': 'latex', 'latex': r'\ce{H3O+} \longrightarrow x'})
    bad = new_block('p1_b', 1, 'math', [0, 0, 1, 1], source='marker', chosen={'type': 'latex', 'latex': r'\chemfig{A-B}'})
    txt = new_block('p1_c', 1, 'paragraph', [0, 0, 1, 1], source='marker', chosen={'type': 'text', 'text': r'το $\text{H}^+$ και $\frac{1}{$'})
    from scientific_ocr.adapters import latex_render
    r = latex_check.annotate([ok, bad, txt], latex_render.check)
    assert r == {'status': 'checked', 'formulas': 4, 'not_renderable': 2}
    assert 'LATEX_NOT_RENDERABLE' not in ok['reasons'] and bad['display_crop'] and txt['display_crop']


def test_pdf_typesets_maths_like_the_preview(built):
    import pymupdf

    from scientific_ocr import config
    from scientific_ocr.application.exports import export
    if not config.chrome():
        pytest.skip('no Chrome/Chromium')
    out, c, md = built
    data, mime, _ = export(out, c, 'pdf')
    text = ''.join(p.get_text() for p in pymupdf.open(stream=data, filetype='pdf'))
    assert mime == 'application/pdf' and '\\text{' not in text and '$' not in text and '**' not in text


def test_engine_tables_render_as_tables_but_nothing_else_passes():
    from scientific_ocr.pipeline.html_preview import inline_html
    h = inline_html('<table><tr><th>A</th><td colspan="2">HClO<sub>4</sub></td><td onclick="x">b</td></tr></table>')
    assert '<table><tr><th>A</th><td colspan="2">HClO<sub>4</sub></td>' in h and '&lt;td onclick' in h


def _three_para_run(tmp_path):
    m = marker_json()
    kids = m['children'][0]['children']
    for i, (y, t) in enumerate([(300, 'Alpha one is the first paragraph of the page.'),
                                (340, 'Bravo two is the second paragraph of the page.'),
                                (380, 'Charlie three is the third paragraph of the page.')]):
        kids.append({'id': f'/page/0/Text/{90 + i}', 'block_type': 'Text', 'bbox': [55, y, 400, y + 14],
                     'html': f'<p>{t}</p>', 'children': None, 'images': {}})
    out = make_run(tmp_path, marker=m)
    build(out)
    c, _ = load(out)
    pick = lambda w: next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith(w))
    return out, c, pick('Alpha one'), pick('Bravo two'), pick('Charlie three')


def test_merge_joins_parts_in_the_order_the_reviewer_arranged(tmp_path):
    out, c, p1, p2, p3 = _three_para_run(tmp_path)
    first_pos = min(c['blocks'].index(x) for x in (p1, p2, p3))
    EventStore(out).append({'type': 'merge_add', 'merge': {'id': 'mord', 'page': 1, 'members': [p1['id'], p3['id'], p2['id']],
                                                           'label': {'engine': 'marker', 'name': 'Text'}, 'ordered': True}})
    build(out)
    c2, md2 = load(out)
    m = next(b for b in c2['blocks'] if b.get('merge_id') == 'mord')
    t = m['chosen']['text']
    a, b_, c_ = (t.find(x['chosen']['text'].strip()[:25]) for x in (p1, p3, p2))
    assert -1 < a < b_ < c_                                                     # par1, par3, par2
    assert [x['id'] for x in m['merged_from']] == [p1['id'], p3['id'], p2['id']]
    assert c2['blocks'].index(m) == first_pos                                  # takes the earliest reading position
    assert md2.find(p1['chosen']['text'].strip()[:25]) < md2.find(p3['chosen']['text'].strip()[:25])


def test_legacy_merge_without_order_keeps_reading_order(tmp_path):
    out, c, p1, p2, p3 = _three_para_run(tmp_path)
    EventStore(out).append({'type': 'merge_add', 'merge': {'id': 'mold', 'page': 1, 'members': [p3['id'], p1['id'], p2['id']],
                                                           'label': {'engine': 'marker', 'name': 'Text'}}})
    build(out)
    m = next(b for b in load(out)[0]['blocks'] if b.get('merge_id') == 'mold')
    assert [x['id'] for x in m['merged_from']] == [p1['id'], p2['id'], p3['id']]


def test_reviewer_group_keeps_its_member_order(tmp_path):
    out, c, p1, p2, p3 = _three_para_run(tmp_path)
    EventStore(out).append({'type': 'group_add', 'group': {'id': 'Gord', 'type': 'example', 'name': '', 'ordered': True,
                                                           'members': [p2['id'], p3['id'], p1['id']]}})
    build(out)
    g = next(x for x in load(out)[0]['groups'] if x['id'] == 'Gord')
    assert g['members'] == [p2['id'], p3['id'], p1['id']] and g['ordered']


def test_a_region_that_replaces_segments_keeps_their_reading_position(built):
    """Order preservation: re-tagging/redrawing/re-OCR puts the new entity where the replaced content was."""
    out, c, md = built
    page1 = [b for b in c['blocks'] if b['page'] == 1]
    for target in [b for b in page1 if b['kind'] in ('caption', 'paragraph') and not b.get('excluded')][:3]:
        x0, y0, x1, y1 = target['geometry']['bbox']
        es = EventStore(out)
        es.append({'type': 'region_add', 'region': {'id': 'r' + target['id'], 'page': 1, 'bbox': [x0 - 1, y0 - 1, x1 + 1, y1 + 1],
                                                    'kind': 'text'}})
        assert build(out) == 0
        c2, _ = load(out)
        before = [b['id'] for b in page1 if not b.get('excluded')]
        after = [b for b in c2['blocks'] if b['page'] == 1 and not b.get('excluded')]
        reg = next(b for b in after if b.get('region_id') == 'r' + target['id'])
        assert [b['id'] for b in after].index(reg['id']) == before.index(target['id'])
        es.append({'type': 'undo', 'targets': [es.state()['history'][-1]['seq']]})
        build(out)


# ---------------------------------------------------------------- everything derived from order is recomputed
def _move(out, bid, **where):
    EventStore(out).append({'type': 'decision', 'block_id': bid, 'action': 'move_after_block', **where})
    assert build(out) == 0
    return load(out)


def test_continuation_links_follow_a_reorder(tmp_path):
    m = marker_json()
    kids = m['children'][0]['children']
    kids[1]['html'] = '<p>Alpha paragraph line one about DNA whose sentence is cut by the</p>'
    kids.append({'id': '/page/0/Text/7', 'block_type': 'Text', 'bbox': [55, 340, 400, 380],
                 'html': '<p>figure and continues here after the caption.</p>', 'children': None, 'images': {}})
    out = make_run(tmp_path, marker=m)
    build(out)
    c, md = load(out)
    a = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    z = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('figure and'))
    assert a['relations'].get('continues_in') == z['id']
    c, md = _move(out, z['id'], before=a['id'])                       # the second half now comes first
    a2 = next(b for b in c['blocks'] if b['id'] == a['id'])
    assert not a2['relations'].get('continues_in') and 'cut by the figure and continues' not in md
    c, md = _move(out, z['id'], after=a['id'])                         # adjacent again: joined, no interruption
    z2 = next(b for b in c['blocks'] if b['id'] == z['id'])
    assert z2['relations'].get('continues_from') == a['id'] and not z2['relations'].get('interrupted_by')
    assert 'cut by the figure and continues here' in md


def test_hierarchy_marks_follow_reorders_and_level_changes(tmp_path):
    m = marker_json()
    kids = m['children'][0]['children']
    kids[1:1] = [{'id': '/page/0/SectionHeader/20', 'block_type': 'SectionHeader', 'bbox': [55, 45, 400, 60],
                  'html': '<h2>5 DNA and chromosomes</h2>', 'children': None, 'images': {}},
                 {'id': '/page/0/SectionHeader/21', 'block_type': 'SectionHeader', 'bbox': [55, 62, 400, 78],
                  'html': '<h3>Structure of DNA</h3>', 'children': None, 'images': {}}]
    out = make_run(tmp_path, marker=m)
    build(out)
    c, md = load(out)
    top = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('5 DNA'))
    sub = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Structure'))
    para = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    assert para['section_path'][-1] == sub['id'] and '## Structure of DNA' in md
    c, md = _move(out, para['id'], before=top['id'])                   # above every heading: no section
    assert next(b for b in c['blocks'] if b['id'] == para['id'])['section_path'] == []
    EventStore(out).append({'type': 'decision', 'block_id': sub['id'], 'action': 'set_heading_level', 'level': 1})
    build(out)
    c, md = load(out)
    assert {s['id']: s['level'] for s in c['sections']}[sub['id']] == 1 and '\n# Structure of DNA' in md


def test_caption_parts_follow_the_reading_order(tmp_path):
    m = marker_json()
    kids = m['children'][0]['children']
    kids[3]['html'] = '<p>A caption body for the figure</p>'
    kids.insert(3, {'id': '/page/0/SectionHeader/9', 'block_type': 'SectionHeader', 'bbox': [55, 303, 140, 309],
                    'html': '<h4>Fig. 1</h4>', 'children': None, 'images': {}})
    out = make_run(tmp_path, marker=m)
    build(out)
    c, md = load(out)
    fig = next(b for b in c['blocks'] if b['kind'] == 'figure')
    title, body = fig['relations']['captions']
    c, md = _move(out, body, before=title)
    assert next(b for b in c['blocks'] if b['id'] == fig['id'])['relations']['captions'] == [body, title]
    cap = re.search(r'<figcaption>.*?</figcaption>', md, re.S).group(0)
    assert cap.index('A caption body') < cap.index('Fig. 1')


def test_group_broken_by_a_reorder_is_reported_not_stretched(tmp_path):
    out, c, p1, p2, p3 = _three_para_run(tmp_path)
    EventStore(out).append({'type': 'group_add', 'group': {'id': 'Gx', 'type': 'example', 'name': 'Ex', 'ordered': True,
                                                           'members': [p1['id'], p2['id']]}})
    build(out)
    assert load(out)[1].count('<aside>') == 1
    c, md = _move(out, p3['id'], after=p1['id'])                       # an outsider now sits between the members
    assert '<aside>' not in md
    iss = [i for i in c['document_structure']['issues'] if i['type'] == 'group_not_contiguous']
    assert iss and iss[0]['between'] == [p3['id']]


# ---------------------------------------------------------------- entities and reader-export options
def test_entity_labels_in_greek_and_english_and_many_formats():
    from scientific_ocr.pipeline.entities import classify, number_of
    cases = {'ΠΑΡΑΔΕΙΓΜΑ 4.3': 'example', 'Παράδ. 4-3': 'example', 'Λυμένο παράδειγμα 2': 'example',
             'Example 2.1:': 'example', 'Worked Example 5': 'example', 'Sample Problem 3.4': 'example', 'Ex. 7': 'example',
             'Άσκηση 12': 'exercise', 'ΑΣΚΗΣΕΙΣ': 'exercise', 'Practice Exercise 4': 'exercise', 'Exercises': 'exercise',
             'Πρόβλημα 3': 'problem', 'Problems': 'problem', 'ΕΡΩΤΗΣΗ 5-1': 'question', 'Concept Check 2': 'question',
             'Ερωτήσεις επανάληψης': 'review', 'Review Questions': 'review', 'Αυτοαξιολόγηση': 'review',
             'ΛΥΣΗ': 'solution', 'Solution': 'solution', 'ΑΚΟΛΟΥΘΗΤΕΑ ΣΤΡΑΤΗΓΙΚΗ': 'strategy', 'Strategy': 'strategy',
             'Υπόδειξη': 'hint', 'Ορισμός 1.2': 'definition', 'Definition': 'definition', 'Θεώρημα 3': 'theorem',
             'Corollary 2.1': 'theorem', 'Απόδειξη': 'proof', 'Νόμος του Hess': 'law', 'Κανόνας της οκτάδας': 'law',
             'Το ήξερες;': 'did_you_know', 'Did you know?': 'did_you_know', 'Η χημεία στην καθημερινή ζωή': 'application',
             'Chemistry in Action': 'application', 'Εφαρμογή 2': 'application', 'Ιστορικό σημείωμα': 'history',
             'Πείραμα 4': 'experiment', 'Δραστηριότητα 1': 'activity', 'Σύνοψη': 'summary', 'Chapter Summary': 'summary',
             'Βασικοί όροι': 'key_terms', 'Key Terms': 'key_terms', 'Μαθησιακοί στόχοι': 'objectives',
             'Learning Objectives': 'objectives', 'Προσοχή': 'caution', 'Γλωσσάρι': 'glossary', 'Σημείωση': 'note'}
    wrong = {lab: (classify(lab), want) for lab, want in cases.items() if classify(lab) != want}
    assert not wrong, wrong
    assert classify('Η δομή του DNA') is None and classify('Ισχυρά και ασθενή οξέα') is None   # ordinary titles
    assert number_of('ΠΑΡΑΔΕΙΓΜΑ 4.3') == '4.3' and number_of('Άσκηση 12') == '12' and number_of('Example 2.1:') == '2.1'


def _book_with_example_and_figure(tmp_path):
    m = marker_json()
    kids = m['children'][0]['children']
    kids[1:1] = [{'id': '/page/0/SectionHeader/20', 'block_type': 'SectionHeader', 'bbox': [55, 45, 400, 60],
                  'html': '<h2>4 Acids and bases</h2>', 'children': None, 'images': {}}]
    kids.append({'id': '/page/0/SectionHeader/30', 'block_type': 'SectionHeader', 'bbox': [55, 600, 300, 615],
                 'html': '<h3>Example 4.3</h3>', 'children': None, 'images': {}})
    kids.append({'id': '/page/0/Text/31', 'block_type': 'Text', 'bbox': [55, 620, 500, 660],
                 'html': '<p>Classify the following compounds as strong or weak acids.</p>', 'children': None, 'images': {}})
    out = make_run(tmp_path, marker=m)
    assert build(out) == 0
    return out


def test_only_verified_entities_can_be_excluded(tmp_path):
    from scientific_ocr.pipeline.markdown import markdown
    out = _book_with_example_and_figure(tmp_path)
    c, md = load(out)
    ex = next(e for e in c['entities'] if e['type'] == 'example')
    assert not ex['verified'] and 'Classify the following' in md
    assert 'Classify the following' in markdown(c, options={'exclude': {'example'}})        # unverified: stays
    EventStore(out).append({'type': 'entity_confirm', 'key': ex['key'], 'entity_type': 'example'})
    build(out)
    c, _ = load(out)
    assert next(e for e in c['entities'] if e['key'] == ex['key'])['verified']
    assert 'Classify the following' not in markdown(c, options={'exclude': {'example'}})    # verified: excluded
    assert 'Classify the following' in markdown(c, options={'exclude': {'example@margin'}})  # other placement only
    assert 'Classify the following' in markdown(c)                                         # default: everything


def test_only_captioned_visuals_move_to_the_end_of_the_unit(tmp_path):
    from scientific_ocr.pipeline.markdown import markdown
    out = _book_with_example_and_figure(tmp_path)
    c, md = load(out)
    end = markdown(c, options={'move': {'figures'}, 'images': 'keep', 'unit_level': 1})
    captioned = next(f for f in re.findall(r'<figure>.*?</figure>', end, re.S) if 'Figure 1. A caption' in f)
    assert end.index(captioned) > end.index('Classify the following') and '## Figures' in end and '![' in captioned
    caps = markdown(c, options={'move': {'figures'}, 'images': 'drop', 'unit_level': 1})
    fig = next(f for f in re.findall(r'<figure>.*?</figure>', caps, re.S) if 'Figure 1. A caption' in f)
    assert '![' not in fig                                                              # captions only
    for b in c['blocks']:                                                               # uncaptioned pictures stay put
        if b['chosen']['type'] == 'image_only' and not b['relations'].get('captions') and b.get('source_crop') \
                and not b.get('excluded') and b['kind'] != 'caption':
            pos = caps.find(b['source_crop']['path'])
            assert pos == -1 or pos < caps.index('## Figures')


def test_equations_and_text_kept_as_images_never_move_without_a_picture_caption():
    from scientific_ocr.core.model import new_block
    from scientific_ocr.pipeline.entities import is_captioned_visual
    eq = new_block('p1_e', 1, 'chemical_reaction', [0, 0, 1, 1], source='marker')            # OCR failed -> image
    txt = new_block('p1_t', 1, 'excerpt', [0, 0, 1, 1], source='coverage')
    fig = new_block('p1_f', 1, 'figure', [0, 0, 1, 1], source='marker')
    pic_cap = {'caption_label': 'ΣΧΗΜΑ 4.9'}
    eq_cap = {'caption_label': 'Εξίσωση 2'}
    assert not is_captioned_visual(eq, 'chemical_equation', [])                       # no caption: never
    assert not is_captioned_visual(txt, None, [])
    assert not is_captioned_visual(eq, 'chemical_equation', [eq_cap])                  # caption is not a picture label
    assert is_captioned_visual(eq, 'chemical_equation', [pic_cap])                     # "ΣΧΗΜΑ 4.9": a picture
    assert is_captioned_visual(fig, 'figure', [{'caption_label': None}])               # visual type + any caption


def test_verified_margin_entities_move_to_the_end_of_the_unit(tmp_path):
    from scientific_ocr.pipeline.markdown import markdown
    out = _book_with_example_and_figure(tmp_path)
    c, md = load(out)
    ex = next(e for e in c['entities'] if e['type'] == 'example')
    assert 'Classify' in markdown(c, options={'move': {'example'}}).split('## ')[0] + md   # unverified: stays put
    EventStore(out).append({'type': 'entity_confirm', 'key': ex['key'], 'entity_type': 'example'})
    build(out)
    c, _ = load(out)
    moved = markdown(c, options={'move': {'example'}, 'unit_level': 1})
    assert moved.index('## Παράδειγμα') < moved.index('Classify the following') if '## Παράδειγμα' in moved else \
        moved.index('## Example') < moved.index('Classify the following')
    assert moved.count('<aside>') == moved.count('</aside>')
    assert markdown(c, options={'move': {'example@margin'}}) == markdown(c)              # inline example: not selected


def test_placement_end_of_chapter_and_margin():
    from scientific_ocr.core.model import new_block
    from scientific_ocr.pipeline import entities
    def blk(i, kind, text, bbox, path, **kw):
        b = new_block(f'p1_{i}', 1, kind, bbox, source='marker', chosen={'type': 'text', 'text': text}, **kw)
        b['section_path'], b['flow'] = path, 'main'
        return b
    ch = blk('a', 'heading', '4 Acids', [60, 40, 500, 60], ['p1_a'])
    s1 = blk('b', 'heading', '4.1 Strong acids', [60, 70, 500, 90], ['p1_a', 'p1_b'])
    body = blk('c', 'paragraph', 'Body text long enough to define the text frame of this page. ' * 3, [60, 100, 500, 300], ['p1_a', 'p1_b'])
    side = blk('d', 'paragraph', 'Note: acids taste sour.', [520, 120, 590, 160], ['p1_a', 'p1_b'])
    sidehead = blk('d0', 'heading', 'Σημείωση', [520, 100, 590, 115], ['p1_a', 'p1_b', 'p1_d0'])
    ex = blk('e', 'heading', 'Ασκήσεις', [60, 400, 500, 420], ['p1_a', 'p1_e'])
    q = blk('f', 'paragraph', '1. Name three strong acids.', [60, 430, 500, 450], ['p1_a', 'p1_e'])
    blocks = [ch, s1, body, sidehead, side, ex, q]
    toc = [{'id': 'p1_a', 'level': 1, 'title': '4 Acids', 'parent': None}, {'id': 'p1_b', 'level': 2, 'title': '4.1 Strong acids', 'parent': 'p1_a'},
           {'id': 'p1_d0', 'level': 3, 'title': 'Σημείωση', 'parent': 'p1_b'}, {'id': 'p1_e', 'level': 2, 'title': 'Ασκήσεις', 'parent': 'p1_a'}]
    side['section_path'] = ['p1_a', 'p1_b', 'p1_d0']
    ents = entities.detect(blocks, toc, [], {1: (600, 800)}, {}, set())['entities']
    got = {(e['type'], e['placement']) for e in ents}
    assert ('exercise', 'end_of_chapter') in got and ('note', 'margin') in got


def test_margin_content_moves_without_caption_or_verification_but_types_need_verification():
    from scientific_ocr.core.model import new_block
    from scientific_ocr.pipeline import reading
    def blk(i, text, bbox, kind='paragraph'):
        b = new_block(f'p1_{i}', 1, kind, bbox, source='marker', chosen={'type': 'text', 'text': text})
        b['flow'], b['section_path'] = 'main', []
        return b
    body = blk('a', 'Main text paragraph long enough. ' * 6, [60, 100, 500, 300])
    note = blk('b', 'A side note in the margin.', [520, 120, 590, 160])
    ex = blk('c', 'Inline example text.', [60, 320, 500, 340])
    c = {'blocks': [body, note, ex], 'groups': [], 'entities': [
        {'key': 'k1', 'type': 'sidenote', 'placement': 'margin', 'members': ['p1_b'], 'verified': False, 'granularity': 'block'},
        {'key': 'k2', 'type': 'example', 'placement': 'inline', 'members': ['p1_c'], 'verified': False, 'granularity': 'block'}]}
    texts = lambda opt: [n.get('text') for n in reading.nodes(c, options=opt)]
    assert texts({'move': {'sidenote'}})[-1] == 'A side note in the margin.'                # margin: moves unverified
    assert texts({'move': {'*@margin'}})[-1] == 'A side note in the margin.'
    assert texts({'move': {'example'}}) == texts({})                                         # type: needs verification
    c['entities'][1]['verified'] = True
    assert texts({'move': {'example'}})[-1] == 'Inline example text.'


def test_corrupt_required_input_stops_the_build_and_keeps_outputs(built):
    out, c, md = built
    before = (out / 'canonical.json').read_text()
    (out / 'raw' / 'extraction_index.json').write_text('{ not json')
    assert build(out) == 6                                            # never "no engine output"
    assert (out / 'canonical.json').read_text() == before


def test_entity_rule_trust_needs_evidence_and_low_error():
    from scientific_ocr.pipeline import entities
    few = entities.stats([('box:example', 'right')] * 5)
    many = entities.stats([('box:example', 'right')] * 40)
    noisy = entities.stats([('box:example', 'right')] * 30 + [('box:example', 'wrong')] * 10)
    assert not few['rules']['box:example']['trusted'] and many['rules']['box:example']['trusted']
    assert not noisy['rules']['box:example']['trusted']


def test_entity_family_selectors_and_labels():
    from scientific_ocr.core.vocabulary import (
        entity_label,
        family_of,
        valid_entity_type,
    )
    from scientific_ocr.pipeline.entities import excluded_blocks, selectors
    assert family_of('example') == 'worked' and family_of('family:aside') == 'aside' and family_of('family:x') is None
    assert valid_entity_type('family:practice') and not valid_entity_type('practice') and not valid_entity_type(None)
    assert entity_label('family:practice') == 'Εξάσκηση' and entity_label('example', 'en') == 'Example'
    e = {'type': 'exercise', 'placement': 'margin', 'verified': True, 'members': ['b1']}
    assert set(selectors(e)) == {'exercise', 'exercise@margin', 'family:practice', 'family:practice@margin'}
    assert excluded_blocks([e], {'family:practice@margin'}) == {'b1'}
    assert excluded_blocks([{**e, 'verified': False}], {'family:practice'}) == set()


def test_cross_references_resolve_by_word_stem_and_number():
    """'Σχήματος 4.9' ~ 'ΣΧΗΜΑ 4.9' (inflection, case, accents; '.'/'-' equivalent); own numbers are not
    references; a mention with no target is never resolved; reviewer overrides win."""
    from scientific_ocr.pipeline import references
    blk = lambda i, kind, text, **kw: {'id': i, 'page': 1, 'kind': kind, 'chosen': {'type': 'text', 'text': text},
                                       'relations': kw.pop('rel', {}), **kw}
    fig = blk('f', 'figure', '', rel={'captions': ['c']})
    cap = blk('c', 'caption', 'ΣΧΗΜΑ 4-9 Η δομή του DNA', caption_label='ΣΧΗΜΑ 4-9', rel={'caption_for': 'f'})
    para = blk('p', 'paragraph', 'Όπως φαίνεται στο Σχήματος 4.9 και στον Πίνακα 7.1, η έλικα…')
    res = references.annotate([fig, cap, para])
    got = {r['label']: (r['status'], r.get('target')) for r in para['references']}
    assert got['Σχήματος 4.9'] == ('resolved', 'f') and got.get('Πίνακα 7.1', ('unresolved',))[0] == 'unresolved'
    assert 'references' not in cap and fig['relations']['referenced_by'] == ['p'] and res['resolved'] == 1
    references.annotate([fig, cap, para], overrides={'p#0': None})          # the reviewer: not a reference
    assert 'Σχήματος 4.9' not in [r['label'] for r in para.get('references', [])]


# ---------------------------------------------------------------- typography, coverage, hierarchy hints
def test_text_metrics_measure_print_size_and_lines_on_rotated_scans(tmp_path):
    from conftest import FONT
    from PIL import Image, ImageDraw, ImageFont

    from scientific_ocr.adapters import pdf as pdfio
    font = lambda n: ImageFont.truetype(str(FONT), n) if FONT.exists() else ImageFont.load_default()
    def img(size, lines, name):
        im = Image.new('RGB', (900, 40 + lines * size * 2), 'white')
        d = ImageDraw.Draw(im)
        for i in range(lines):
            d.text((20, 20 + i * size * 2), 'Typography gives hints agy', fill=(30, 30, 30), font=font(size))
        im = im.rotate(0.6, expand=True, fillcolor='white')           # photographed pages are slightly rotated
        im.save(tmp_path / name)
        return pdfio.text_metrics(tmp_path / name, 1.0)
    big, small = img(48, 1, 'big.png'), img(24, 3, 'small.png')
    assert small['lines'] == 3 and big['lines'] == 1
    assert 1.6 < big['text_h'] / small['text_h'] < 2.4


def test_coverage_sees_light_coloured_print_that_a_grey_raster_misses(tmp_path):
    import pymupdf
    from conftest import H, W

    from scientific_ocr.adapters import pdf as pdfio
    from scientific_ocr.pipeline.coverage import uncovered_ink_regions
    pdf = tmp_path / 'c.pdf'
    doc = pymupdf.open()
    pg = doc.new_page(width=W, height=H)
    for i in range(4):                                                # a light blue margin outline, small print
        pg.insert_text((450, 300 + 14 * i), 'CHAPTER OUTLINE ITEM', fontsize=8, color=(0.3, 0.7, 0.95))
    doc.save(pdf)
    regs, _ = uncovered_ink_regions(pdfio, pdf, 1, [], 1.0)
    assert any(r['bbox'][0] >= 430 and r['bbox'][1] <= 300 <= r['bbox'][3] + 10 for r in regs)


def _head(i, page, text, size, tag, level=None, src=None):
    b = {'id': i, 'page': page, 'kind': 'heading', 'chosen': {'type': 'text', 'text': text}, 'relations': {},
         'geometry': {'bbox': [50, 100, 400, 120]}, 'engine_heading_levels': {'marker': tag},
         'typography': {'text_u': size, 'lines': 1}}
    if level:
        b['heading_level'], b['heading_level_source'] = level, src
    return b


def _head_furniture(i, page, text, x):
    return {'id': i, 'page': page, 'kind': 'excerpt', 'role': 'page_header', 'chosen': {'type': 'image_only'},
            'candidates': [{'engine': 'docling', 'text': text}], 'relations': {}, 'flow': 'furniture',
            'geometry': {'bbox': [x, 20, x + 200, 40]}, 'excluded': {'as': 'running_title', 'side': 'top'}}


def test_heading_hints_style_applies_unless_calibration_distrusts_it():
    """Measured typography/style applies on its own (an automatic, undoable correction) even against the engine;
    if this book's calibration shows the typography errs, it stops applying alone and becomes a proposal.
    Typography + running heads (independent kinds) apply. A reviewer level anchors its style; calibration counts."""
    from scientific_ocr.pipeline import sections
    blocks = [_head('ch', 1, 'DNA and Chromosomes', 20.0, 2), _head('s1', 3, 'THE STRUCTURE OF DNA', 11.0, 2),
              _head('a', 3, 'A molecule of DNA consists of two chains', 11.0, 3), _head('b', 6, 'DNA provides a mechanism for heredity', 11.0, 2),
              _head('s2', 7, 'THE STRUCTURE OF CHROMOSOMES', 10.8, 2)]
    heads = [_head_furniture('h2', 2, 'CHAPTER 5 DNA AND CHROMOSOMES', 50), _head_furniture('h3', 3, 'The Structure of DNA', 500),
             _head_furniture('h5', 5, 'The Structure of DNA', 500), _head_furniture('h7', 7, 'The Structure of Chromosomes', 500)]
    rep = {}
    toc = {s['id']: s for s in sections.annotate(blocks + heads, report=rep)}
    assert toc['ch']['level'] == 1 and toc['s1']['level'] == 2 and toc['s2']['level'] == 2   # typography + running heads
    assert toc['a']['level'] == 3                                                            # typography + engine agree
    # b has the SAME style as a, whose level passed the check: same style = same level (independent confirmation)
    assert toc['b']['level'] == 3 and 'style_of_checked' in next(x for x in blocks if x['id'] == 'b')['heading_evidence']
    # without a checked sibling, the typography of a distinct style still applies on its own
    alone = [dict(x) for x in blocks if x['id'] != 'a']
    toc1 = {s['id']: s for s in sections.annotate([dict(x) for x in alone] + heads)}
    assert toc1['b']['level'] == 3 and toc1['b']['source'] == 'typography'
    # ... unless the calibration on this book's decisions shows typography errs: then it is only a proposal
    rep0 = {}
    toc0 = {s['id']: s for s in sections.annotate(alone + heads, prior=[('typography', 'wrong')] * 12, report=rep0)}
    assert toc0['b']['level'] == 2 and toc0['b']['source'] == 'engine_rank'
    prop = next(p for p in rep0['proposals'] if 'b' in p['members'])
    assert prop['level'] == 3 and prop['sources'] == ['typography']
    blocks = alone
    # the reviewer sets b: its outcome calibrates the hint sources (typography right, engine wrong)
    next(x for x in blocks if x['id'] == 'b').update(heading_level=3, heading_level_source='human')
    rep2 = {}
    sections.annotate(blocks + heads, report=rep2)
    assert rep2['stats']['typography']['wrong'] == 0 and rep2['stats']['engine_rank']['wrong'] >= 1
    assert not any('b' in p['members'] for p in rep2['proposals'])
    # rejecting a proposal counts against its sources
    next(x for x in blocks if x['id'] == 'b').update(heading_level=None, heading_level_source=None)
    rep3 = {}
    sections.annotate(blocks + heads, dismissed={prop['key']}, prior=[('typography', 'wrong')] * 12, report=rep3)
    assert rep3['stats']['typography']['wrong'] >= 13                  # the rejection counted on top of the prior


def test_running_heads_give_no_level_without_a_changing_series_and_outline_uses_hidden_lines():
    from scientific_ocr.pipeline import sections
    blocks = [_head('x', 3, 'FIRST SECTION', 11.0, 2), _head('y', 7, 'SECOND SECTION', 11.0, 1),
              _head('z', 9, 'Some Other Title', 8.0, 3, 2, 'human')]
    same = [_head_furniture('h3', 3, 'First Section', 500), _head_furniture('h5', 5, 'First Section', 500)]
    assert sections._running_anchors(blocks, sections.running_heads(same)) == {}           # nothing changes: no level
    sib = sections._outline_siblings(blocks, sections.outlines([], [(1, '■ FIRST SECTION'), (1, '■ Some Other Title')]))
    assert sib == [{'x', 'z'}]                                                              # hidden outline lines count
    h = sections._hints(blocks, sections.styles(blocks), {}, [], None, sib)
    assert h['x']['outline'] == 2                                                           # sibling the reviewer set


def test_unread_header_is_not_excluded_on_an_engine_label_alone_but_a_read_repeated_one_is():
    from scientific_ocr.pipeline import furniture
    def hdr(i, page, text):
        return {'id': i, 'page': page, 'kind': 'excerpt', 'role': 'page_header', 'chosen': {'type': 'image_only'},
                'candidates': [{'engine': 'marker', 'text': ''}] + ([{'engine': 'docling', 'text': text}] if text else []),
                'relations': {}, 'reasons': [], 'decisions': [], 'source': 'marker', 'geometry': {'bbox': [400, 10, 560, 30]}}
    def body(i, page):
        return {'id': i, 'page': page, 'kind': 'paragraph', 'chosen': {'type': 'text', 'text': 'long body text ' * 30},
                'candidates': [], 'relations': {}, 'reasons': [], 'decisions': [], 'source': 'marker',
                'geometry': {'bbox': [50, 100, 560, 700]}}
    blocks = [hdr('u1', 1, ''), body('b1', 1), hdr('r2', 2, 'The Structure of DNA'), body('b2', 2),
              hdr('r3', 3, 'The Structure of DNA'), body('b3', 3)]
    furniture.classify(blocks, {p: (600, 800) for p in (1, 2, 3)}, set())
    by = {b['id']: b for b in blocks}
    assert by['r2'].get('excluded') and by['r3'].get('excluded')         # Docling's reading repeats: furniture
    assert not by['u1'].get('excluded') or 'position_twin' in by['u1']['excluded']['signals']


def test_labelled_paragraph_without_figure_becomes_caption_and_assembly_is_suggested():
    from scientific_ocr.pipeline import captions, structure
    def blk(i, kind, text, bb, typ='text'):
        return {'id': i, 'page': 1, 'kind': kind, 'chosen': {'type': typ, 'text': text}, 'relations': {}, 'reasons': [],
                'geometry': {'bbox': bb}}
    page = [blk('p', 'paragraph', 'Body text of the page that is long enough to be body text and not a piece.', [50, 50, 300, 150]),
            blk('a', 'paragraph', '(A) molecular biology is ...', [320, 400, 560, 420]),
            blk('e', 'paragraph', '', [320, 425, 560, 445], 'image_only'),
            blk('c', 'paragraph', '**FIGURE 5-6** Examples of linear messages.', [320, 460, 560, 500])]
    captions.associate(page)
    assert page[3]['kind'] == 'caption' and not page[3]['relations'].get('caption_for')
    info = {'page': 1}
    structure.suggest(page, [], [info], set())
    s = next(x for x in info['suggestions'] if x['kind'] == 'figure_assemble')
    assert s['members'] == ['a', 'e'] and s['caption'] == 'c'


def test_model_vote_is_one_more_hint_never_applied_alone():
    from scientific_ocr.pipeline import sections
    blocks = [_head('ch', 1, 'DNA and Chromosomes', 20.0, 2),
              _head('b', 6, 'DNA provides a mechanism for heredity', 11.0, 2), _head('m', 8, 'Odd heading with no style', 11.0, 2)]
    blocks[2].pop('typography')
    distrust = [('typography', 'wrong')] * 12                       # a book where typography proved unreliable
    toc = {s['id']: s for s in sections.annotate([dict(b) for b in blocks], prior=distrust)}
    assert toc['b']['level'] == 1                                  # engine 1 vs distrusted typography 2: not applied
    toc = {s['id']: s for s in sections.annotate(blocks, prior=distrust, model={'b': 2, 'm': 4})}
    assert toc['b']['level'] == 2 and 'model' in next(x for x in blocks if x['id'] == 'b')['heading_evidence']   # + typography
    assert toc['m']['level'] != 4                                                                              # model alone


def test_undoing_an_automatic_level_restores_the_original_and_counts_against_its_evidence():
    from scientific_ocr.pipeline import sections
    blocks = [_head('ch', 1, 'DNA and Chromosomes', 20.0, 2), _head('s1', 3, 'THE STRUCTURE OF DNA', 11.0, 2),
              _head('s2', 7, 'THE STRUCTURE OF CHROMOSOMES', 10.8, 2)]
    heads = [_head_furniture('h2', 2, 'CHAPTER 5 DNA AND CHROMOSOMES', 50), _head_furniture('h3', 3, 'The Structure of DNA', 500),
             _head_furniture('h7', 7, 'The Structure of Chromosomes', 500)]
    toc = {s['id']: s for s in sections.annotate([dict(b) for b in blocks] + heads)}
    assert toc['ch']['level'] == 1
    rep = {}
    toc = {s['id']: s for s in sections.annotate(blocks + heads, dismissed={sections.revoke_key('ch', 1)}, report=rep)}
    assert toc['ch']['level'] == 2 and toc['ch']['source'] == 'revoked'          # exactly the engine's level, not re-repaired
    assert rep['stats']['typography']['wrong'] >= 1 and rep['stats']['running_head']['wrong'] >= 1
    assert not any('ch' in p['members'] and p['level'] == 1 for p in rep['proposals'])   # not proposed again


def test_undo_targets_one_change_not_its_subject_and_only_real_changes_are_reported():
    """General rules for every automatic correction: (1) an undo records WHICH change was refused, so a later,
    different correction of the same segment is still possible; (2) a record whose before equals its after is not a
    correction and is not reported (nothing to undo)."""
    from scientific_ocr.pipeline import document, sections
    assert sections.revoke_key('h', 2) != sections.revoke_key('h', 3)
    assert sections.jump_key('h', 4, 2) != sections.jump_key('h', 3, 2)
    fixes = [{'kind': 'heading_style', 'before': 2, 'after': 2}, {'kind': 'heading_style', 'before': 2, 'after': 3},
             {'kind': 'furniture', 'before': None, 'after': 'running_title', 'before_el': 'στο κείμενο', 'after_el': 'εκτός εξόδου'},
             {'kind': 'x', 'before': None, 'after': None, 'before_el': 'Α', 'after_el': 'Α'}]
    assert [f['after'] for f in document.real_changes(fixes)] == [3, 'running_title']
    hs = [_head('top', 1, 'Top Title', 20.0, 1), _head('deep', 1, 'Deep Title', 11.0, 5)]
    toc = {s['id']: s for s in sections.annotate([dict(b) for b in hs])}
    lv = toc['deep']['level']
    toc = {s['id']: s for s in sections.annotate([dict(b) for b in hs], {sections.jump_key('deep', 9, 2)})}
    assert toc['deep']['level'] == lv                    # refusing a different jump leaves this repair in place


def test_title_style_is_relative_to_the_body_text_of_its_page():
    """Page 2 was photographed closer (everything twice as large) and page 3 is a low-resolution scan: relative to
    each page's body text, the same kind of title is the same style; an unreliable (few-pixel) measurement is not
    used; a page without body text falls back to the book's reference."""
    from scientific_ocr.pipeline import sections
    def head(i, page, text, h, px=30, ink=(30, 30, 30)):
        return {'id': i, 'page': page, 'kind': 'heading', 'chosen': {'type': 'text', 'text': text}, 'relations': {},
                'geometry': {'bbox': [50, 100, 400, 120]}, 'engine_heading_levels': {'marker': 2},
                'typography': {'text_h': h, 'text_u': h, 'lines': 1, 'band_px': px, 'density': 0.3, 'ink_rgb': list(ink)}}
    refs = {1: {'text_h': 10.0, 'density': 0.2, 'ink_rgb': [40, 40, 40]}, 2: {'text_h': 20.0, 'density': 0.2, 'ink_rgb': [40, 40, 40]},
            3: {'text_h': 10.0, 'density': 0.2, 'ink_rgb': [40, 40, 40]}}
    hs = [head('a', 1, 'First section title', 20.0), head('b', 2, 'Second section title', 40.0),
          head('c', 3, 'Third section title', 20.0, px=5), head('d', 4, 'Fourth section title', 20.0, ink=(20, 120, 140))]
    sections.relative_typography(hs, refs)
    st = sections.styles(hs)
    assert hs[0]['typography_rel']['size'] == hs[1]['typography_rel']['size'] == 2.0
    assert st['a'] == st['b']                                     # same style despite the photo scale
    assert 'typography_rel' not in hs[2] and st['c'][0] == 'm'    # low resolution: falls back to the engine tag
    assert hs[3]['typography_rel']['ref'] == 'book' and hs[3]['typography_rel']['colour'] > 40   # coloured vs body


def test_one_notation_for_scripts_is_derived_lossless_and_leaves_the_rest_alone():
    from scientific_ocr.pipeline import notation
    n = lambda t: notation.normalize_text(t)[0]
    assert n('(α) H<sub>3</sub>PO<sub>4</sub>, (δ) Sr(OH)<sub>2</sub>.') == '(α) $\\mathrm{H_{3}PO_{4}}$, (δ) $\\mathrm{Sr(OH)_{2}}$.'
    assert n('Na⁺ και SO₄²⁻') == '$\\mathrm{Na^{+}}$ και $\\mathrm{SO_{4}^{2-}}$'                 # Unicode scripts too
    assert n('ομάδα (-PO<sub>4</sub>) του') == 'ομάδα (-$\\mathrm{PO_{4}}$) του'                    # prose brackets stay out
    assert n('CuSO<sub>4</sub>·5H<sub>2</sub>O') == '$\\mathrm{CuSO_{4}\\cdot 5H_{2}O}$'
    assert n('<table><tr><td>H<sub>2</sub>O</td></tr></table>') == '<table><tr><td>$\\mathrm{H_{2}O}$</td></tr></table>'
    assert n('το OH<sup>−</sup>.') == 'το $\\mathrm{OH^{-}}$.'                                    # Marker's U+2212 minus
    for same in ('ήδη $\\text{H}_2\\text{O}$', 'x<sub>αβ</sub>', 'απλό'):
        assert n(same) == same                                       # formulas, tables, unknown scripts: untouched
    b = {'chosen': {'type': 'text', 'text': 'H<sub>2</sub>O'}}
    notation.annotate([b])
    assert b['chosen']['text'] == 'H<sub>2</sub>O' and b['text_norm'] == '$\\mathrm{H_{2}O}$'   # source kept


def test_marker_only_region_is_accepted_docling_only_still_needs_review(built):
    """Policy: Marker (primary, statistically more reliable) alone -> accepted, informational reason only;
    Docling alone (Marker missed content) -> review required. Content checks still apply to Marker-only text."""
    from scientific_ocr.core.reasons import REASONS, flag
    assert REASONS['SINGLE_ENGINE_MARKER'].review is None and REASONS['SINGLE_ENGINE_DOCLING'].review == 'required'
    b = {'reasons': [], 'review': 'not_needed'}
    flag(b, 'SINGLE_ENGINE_MARKER')
    assert b['review'] == 'not_needed' and b['reasons'] == ['SINGLE_ENGINE_MARKER']
    flag(b, 'SYMBOL_READING_CONFLICT')                                 # any content check still escalates
    assert b['review'] != 'not_needed'
    out, c, md = built
    only = [x for x in c['blocks'] if 'SINGLE_ENGINE_DOCLING' in x['reasons']]
    assert only and all(x['review'] == 'required' for x in only)


# ---------------------------------------------------------------- entity extents (automatic groups)
def _xb(i, kind, text, y, tone=None, page=1, x0=60, x1=500, typ='text', **kw):
    b = {'id': i, 'page': page, 'kind': kind, 'chosen': {'type': typ, 'text': text}, 'relations': {}, 'reasons': [], 'review': 'not_needed',
         'geometry': {'bbox': [x0, y, x1, y + 14]}, 'source_crop': {'paper_tone': [tone] * 3} if tone else {}, **kw}
    return b


def test_shaded_box_extent_follows_the_lighting_gradient_and_includes_its_solution():
    """The box is most of the page (so 'paper' must come from the BRIGHT backgrounds), and the photo darkens down
    the page: the extent chains neighbour to neighbour, includes the solution and its paragraphs, and stops at
    the next box's label."""
    from scientific_ocr.pipeline import extents
    page = [_xb('p', 'paragraph', 'Body text before the box with enough words.', 40, 255),
            _xb('l', 'heading', 'ΠΑΡΑΔΕΙΓΜΑ 4.3', 100, 208), _xb('t', 'heading', 'Κατάταξη οξέων', 120, 203),
            _xb('s', 'paragraph', 'Κατατάξτε τις ενώσεις…', 140, 201), _xb('h', 'heading', 'ΛΥΣΗ', 170, 195),
            _xb('a', 'paragraph', 'Ο πίνακας μας δίνει…', 190, 196), _xb('b', 'paragraph', '(α) Όπως φαίνεται…', 210, 191),
            _xb('c', 'paragraph', '(β) Το οξικό οξύ…', 230, 186), _xb('n', 'heading', 'Άσκηση 4.3', 260, 176),
            _xb('q', 'paragraph', 'Χαρακτηρίστε…', 280, 181)]
    out = extents.extents(page, {'l', 'n'}, {1: 1.0})
    ex = next(x for x in out if x['head'] == 'l')
    assert ex['members'] == ['l', 't', 's', 'h', 'a', 'b', 'c'] and ex['rule'].startswith('background shading')
    assert next(x for x in out if x['head'] == 'n')['members'] == ['n', 'q']


def test_unshaded_extent_keeps_equations_and_parts_and_stops_at_the_next_label_or_section():
    from scientific_ocr.pipeline import extents
    page = [_xb('l', 'paragraph', '**Παράδειγμα:** Υπολογίστε το pH του διαλύματος.', 100),
            _xb('e', 'math', '', 120, typ='latex', x0=150, x1=400, flow='insert'),
            _xb('h', 'paragraph', '**Λύση:**\nΑπό την εξίσωση έχουμε', 140),
            _xb('a', 'paragraph', 'Άρα pH = 3.', 160),
            _xb('next', 'heading', 'Ισχυρά οξέα', 200, section_path=['next'])]
    out = extents.extents(page, set(), {1: 1.0})
    assert out[0]['members'] == ['l', 'e', 'h', 'a']                  # the section heading ends it


def test_caption_continuation_joins_list_and_source_lines_under_a_caption():
    from scientific_ocr.pipeline import captions
    fig = _xb('f', 'figure', '', 40, typ='image_only')
    cap = _xb('c', 'caption', '**ΣΧΗΜΑ 2.1** Η δομή:', 100, relations={'caption_for': 'f'})
    fig['relations'] = {'captions': ['c']}
    li = _xb('l1', 'paragraph', '(α) το άτομο', 118)
    src = _xb('s', 'paragraph', 'Πηγή: Εθνικό Ινστιτούτο', 136)
    body = _xb('b', 'paragraph', 'Το κείμενο του κεφαλαίου συνεχίζει εδώ με κανονικό μέγεθος.', 190)
    fixes = captions.continuations([fig, cap, li, src, body], {}, {1: 1.0}, set())
    assert fig['relations']['captions'] == ['c', 'l1', 's'] and body['kind'] == 'paragraph'
    assert {f['kind'] for f in fixes} == {'caption_continuation'}
    li2 = _xb('l1', 'paragraph', '(α) το άτομο', 118)
    fig['relations'] = {'captions': ['c']}
    captions.continuations([fig, cap, li2], {}, {1: 1.0}, {fixes[0]['key']})   # undone: stays a paragraph
    assert li2['kind'] == 'paragraph'


def test_exercises_link_to_answers_gathered_elsewhere():
    from scientific_ocr.pipeline import entities
    blocks = [_xb('x', 'paragraph', 'q', 10), _xb('ans', 'heading', 'Απαντήσεις ασκήσεων', 100, page=9),
              _xb('a1', 'paragraph', '4.3 Ισχυρό οξύ το HBr.', 120, page=9)]
    ents = [{'key': 'E', 'type': 'exercise', 'number': '4.3', 'members': ['x']},
            {'key': 'A', 'type': 'answer', 'collective': True, 'members': ['ans', 'a1']}]
    assert entities.link_answers(ents, blocks) == 1
    assert ents[0]['answers'] == ['a1'] and blocks[2]['relations']['answer_for'] == 'E'


def test_extent_looks_past_interleaved_segments_keeps_proofs_and_groups_equation_explanations():
    from scientific_ocr.pipeline import extents
    # a shaded box in the right column; the reading order interleaves a left-column body paragraph
    page = [_xb('w', 'paragraph', 'White page text', 20, 255),
            _xb('l', 'heading', 'ΠΑΡΑΔΕΙΓΜΑ 2.1', 100, 205, x0=320, x1=560), _xb('s', 'paragraph', 'Υπολογίστε…', 120, 203, x0=320, x1=560),
            _xb('o', 'paragraph', 'Κείμενο της αριστερής στήλης', 125, 255, x0=40, x1=290),
            _xb('a', 'paragraph', '**Λύση:** …', 140, 200, x0=320, x1=560), _xb('z', 'paragraph', 'Μετά το πλαίσιο', 200, 255, x0=320, x1=560)]
    ex = next(x for x in extents.extents(page, {'l'}, {1: 1.0}) if x['head'] == 'l')
    assert ex['members'] == ['l', 's', 'a'] and ex['stop'] == 'the shading ends' and not ex['ambiguous']
    # a theorem's proof belongs to it; a gap ends an unshaded entity, flagged ambiguous (asked to the model)
    page2 = [_xb('t', 'heading', 'Θεώρημα 3', 100), _xb('st', 'paragraph', 'Κάθε συνεχής συνάρτηση…', 120),
             _xb('p', 'heading', 'Απόδειξη', 140), _xb('pp', 'paragraph', 'Έστω f…', 160), _xb('far', 'paragraph', 'Νέο θέμα', 400)]
    th = extents.extents(page2, {'t'}, {1: 1.0})[0]
    assert th['members'] == ['t', 'st', 'p', 'pp'] and th['ambiguous'] and th['stop'] == 'an unusually large gap'
    eq = [_xb('e', 'math', '', 100, typ='latex', x0=150, x1=400), _xb('x', 'paragraph', 'όπου k η σταθερά ταχύτητας.', 118)]
    g = extents.extents(eq, set(), {1: 1.0})
    assert g and g[0]['members'] == ['e', 'x'] and g[0]['type'] == 'equation_group'


def test_notes_under_a_table_without_caption_join_it():
    from scientific_ocr.pipeline import captions
    tab = _xb('t', 'table', '<table><tr><td>1</td></tr></table>', 100)
    note = _xb('n', 'paragraph', '* Τιμές στους 25 °C.', 120)
    src = _xb('s', 'paragraph', 'Πηγή: CRC Handbook', 138)
    body = _xb('b', 'paragraph', '(α) Το κείμενο συνεχίζει…', 156)       # list-like, but a table has no caption list
    captions.continuations([tab, note, src, body], {}, {1: 1.0}, set())
    assert tab['relations']['captions'] == ['n', 's'] and body['kind'] == 'paragraph'


def test_moving_a_heading_moves_its_whole_subtree_by_the_same_amount():
    """H4 -> H2 by the reviewer: its inferred H5/H6 sub-headings become H3/H4 (relative depth kept); the next
    section of the original level is not touched; numbered headings keep their numbering; a nested reviewer
    decision takes over its own subtree; each shift is undoable."""
    from scientific_ocr.pipeline import document, sections

    def hd(i, lvl, src='typography', text=None):
        return {'id': i, 'page': 1, 'kind': 'heading', 'chosen': {'type': 'text', 'text': text or f'Title {i}'},
                'relations': {}, 'geometry': {'bbox': [0, 0, 1, 1]}, 'heading_level': lvl, 'heading_level_source': src}
    hs = [hd('a', 1), hd('p', 2, 'human'), hd('c5', 5), hd('c6', 6), hd('n', 3, 'numbering', '4.2.1 Numbered'), hd('d5', 5), hd('next', 4)]
    base = {b['id']: b['heading_level'] for b in hs}
    base['p'] = 4                                          # the hints said H4; the reviewer set H2: -2
    sections.shift_subtrees(hs, base=base)
    lv = {b['id']: b['heading_level'] for b in hs}
    assert (lv['c5'], lv['c6']) == (3, 4) and lv['n'] == 3 and lv['next'] == 4
    assert lv['d5'] == 5                                  # after the numbered H3 (not deeper than H4): outside the subtree
    fixes = [f for f in document.heading_fixes(hs) if f['kind'] == 'level_shift']
    assert {f['after'] for f in fixes} == {3, 4}
    hs2 = [hd('p', 2, 'human'), hd('c5', 5), hd('q', 5, 'human'), hd('c7', 7)]
    base2 = {'p': 4, 'c5': 5, 'q': 6, 'c7': 7}             # nested decision: 6 -> 5 (-1) for its own subtree
    sections.shift_subtrees(hs2, {sections.revoke_key('c5', 3)}, base2)
    assert hs2[1]['heading_level'] == 5 and hs2[3]['heading_level'] == 6   # c5 undone; c7 follows q (-1)
    # no double shift: a child that the hints ALREADY moved (e.g. via its style) stays where it is
    hs3 = [hd('p', 2, 'human'), hd('c', 3)]
    sections.shift_subtrees(hs3, base={'p': 4, 'c': 5})
    assert hs3[1]['heading_level'] == 3 and hs3[1]['heading_level_source'] == 'typography'


def test_contents_page_headings_are_not_sections_and_render_as_text():
    """On a contents page (many '…… 26' entries) headings — also split ones like 'ΚΕΦΑΛΑΙΟ 7' without a page
    number — are entries, not sections of the book; they render as text, and a contents entity is detected."""
    from scientific_ocr.pipeline import entities, reading, sections

    def blk(i, page, kind, text):
        return {'id': f'p{page}_{i}', 'page': page, 'kind': kind, 'chosen': {'type': 'text', 'text': text},
                'reasons': [], 'review': 'not_needed', 'relations': {}, 'reading_order': i, 'excluded': {},
                'geometry': {'bbox': [50, 50 + 40 * i, 500, 80 + 40 * i]}}
    toc_lines = '\n'.join(f'Ενότητα {k} ........ {20 + k}' for k in range(6))
    blocks = [blk(1, 13, 'heading', 'ΚΕΦΑΛΑΙΟ 7'), blk(2, 13, 'heading', 'Από το DNA στις Πρωτεΐνες / 285'),
              blk(3, 13, 'paragraph', toc_lines),
              blk(1, 40, 'heading', 'ΚΕΦΑΛΑΙΟ 7'), blk(2, 40, 'paragraph', 'Το DNA περιέχει την πληροφορία.')]
    toc = sections.annotate(blocks)
    assert [s['id'] for s in toc] == ['p40_1']                                   # only the real chapter
    assert all(b.get('toc_entry') for b in blocks[:2]) and not blocks[3].get('toc_entry')
    assert reading._node(blocks[0], {b['id']: b for b in blocks})['t'] == 'para'
    res = entities.detect(blocks, toc, [], {13: [600, 800], 40: [600, 800]}, {}, set())
    assert any(e['type'] == 'toc' and 'p13_3' in e['members'] for e in res['entities'])


def test_captions_follow_their_figure_in_reading_order():
    """Wherever a caption was printed (above the figure, after other text) and wherever the figure was moved,
    the caption comes right after its figure; captions continuing on another page keep their place."""
    from scientific_ocr.pipeline import captions
    def b(i, page, kind, **rel):
        return {'id': i, 'page': page, 'kind': kind, 'relations': {'captions': [], **rel}, 'reading_order': 0}
    blocks = [b('c1', 1, 'caption', caption_for='f'), b('p', 1, 'paragraph'), b('f', 1, 'figure', captions=['c1', 'c2']),
              b('q', 1, 'paragraph'), b('c2', 1, 'caption', caption_for='f'), b('x', 2, 'caption', caption_for='f')]
    captions.reconcile(blocks)
    assert [x['id'] for x in blocks] == ['p', 'f', 'c1', 'c2', 'q', 'x']
    assert [x['reading_order'] for x in blocks if x['page'] == 1] == [1, 2, 3, 4, 5]


def test_reviewer_overrides_reading_flow_and_continuations():
    """«παρεμβολή» and «↳ συνέχεια» are automatic; the reviewer's set_flow / set_continuation decisions win."""
    from scientific_ocr.pipeline import flow, overlay
    def b(i, kind, text, ctype='text'):
        return {'id': i, 'page': 1, 'kind': kind, 'chosen': {'type': ctype, 'text': text}, 'relations': {},
                'reasons': [], 'review': 'not_needed'}
    p1, fig, p2 = b('p1', 'paragraph', 'Η πρόταση συνεχίζει'), b('f', 'figure', '', 'image_only'), b('p2', 'paragraph', 'μετά το σχήμα.')
    blocks = [p1, fig, p2]
    flow.annotate(blocks)
    assert fig['flow'] == 'insert' and p2['relations'].get('continues_from') == 'p1'      # automatic
    ctx = {'blocks': blocks, 'by_id': {x['id']: x for x in blocks}}
    overlay.COMMANDS['set_continuation'](p2, {'continues': False}, ctx)
    overlay.COMMANDS['set_flow'](fig, {'flow': 'main'}, ctx)
    flow.annotate(blocks)
    assert fig['flow'] == 'main' and not p2['relations'].get('continues_from')
    p3 = b('p3', 'paragraph', 'Νέα πρόταση.')
    overlay.COMMANDS['set_continuation'](p3, {'continues': True}, ctx)
    blocks2 = [b('q', 'paragraph', 'Τελειώνει εδώ.'), p3]
    flow.annotate(blocks2)
    assert p3['relations'].get('continues_from') == 'q'                                  # forced by the reviewer


def test_merging_images_into_a_figure_needs_no_text_but_a_text_merge_does():
    """Parts without text are what a Picture/Figure merge is made of (the merged entity is the crop of their union):
    no MERGE_PART_WITHOUT_TEXT, no review because of it. A merge meant to be text still flags the missing text."""
    from scientific_ocr.core.model import new_block
    from scientific_ocr.pipeline import merge as mergemod

    def parts():
        out = []
        for i, y in enumerate((100, 300)):
            b = new_block(f'p1_img{i}', 1, 'figure', [50, y, 400, y + 150], source='docling')
            b['chosen'] = {'type': 'image_only', 'provenance': 'source_crop'}
            b['review'] = 'not_needed'
            out.append(b)
        return out
    crop = lambda page, bid, bbox: {'path': f'{bid}.png', 'bbox': bbox}
    codes = lambda b: [r if isinstance(r, str) else r.get('code') for r in b['reasons']]
    for name, flagged in (('Picture', False), ('Text', True)):
        blocks = parts()
        mergemod.apply(blocks, [{'id': 'm1', 'page': 1, 'members': ['p1_img0', 'p1_img1'],
                                 'label': {'engine': 'marker', 'name': name}, 'ordered': True}], 'sha', crop, [{'page': 1}])
        (m,) = blocks
        assert m['display_crop'] and ('MERGE_PART_WITHOUT_TEXT' in codes(m)) == flagged
        if not flagged:
            assert m['review'] not in ('required', 'recommended') and m['chosen']['type'] == 'image_only'


def test_a_group_of_figures_has_its_own_caption_besides_each_figure_caption():
    """Several figures grouped: each keeps its own caption, the group has one caption for all; the export renders
    one outer figure (inner figures with their captions, then the group caption) and the reading order puts the
    group caption right after the group's last member."""
    from scientific_ocr.pipeline import groups, markdown, overlay
    def blk(i, kind, text='', **rel):
        return {'id': i, 'page': 1, 'kind': kind, 'chosen': {'type': 'text', 'text': text} if text else {'type': 'image_only'},
                'relations': {'captions': [], **rel}, 'reasons': [], 'review': 'not_needed', 'reading_order': 0,
                'source_crop': {'path': f'assets/{i}.png'}, 'excluded': {}, 'flow': 'insert' if kind != 'caption' else 'main'}
    gcap = blk('gc', 'paragraph', 'ΕΙΚΟΝΑ 2-9 Τρεις μορφές του ίδιου μορίου')
    f1, c1 = blk('f1', 'figure', captions=['c1']), blk('c1', 'caption', '(Α) Σκελετικό μοντέλο', caption_for='f1')
    f2, c2 = blk('f2', 'figure', captions=['c2']), blk('c2', 'caption', '(Β) Μοντέλο κορδέλας', caption_for='f2')
    blocks = [gcap, f1, c1, f2, c2]
    overlay.COMMANDS['associate_group_caption'](gcap, {'group_id': 'G1'}, {'blocks': blocks, 'by_id': {b['id']: b for b in blocks}})
    out = groups.annotate(blocks, [{'id': 'G1', 'type': 'figure_group', 'name': '', 'members': ['f1', 'f2']}], {})
    assert out[0]['captions'] == ['gc'] and [b['id'] for b in blocks] == ['f1', 'c1', 'f2', 'c2', 'gc']
    md = markdown.markdown({'blocks': blocks, 'groups': out, 'entities': [], 'source': {}, 'pages': []})
    inner1, inner2, group = md.index('(Α) Σκελετικό'), md.index('(Β) Μοντέλο'), md.index('ΕΙΚΟΝΑ 2-9')
    assert inner1 < inner2 < group and md.count('<figure>') == md.count('</figure>') == 3
    assert md.count('ΕΙΚΟΝΑ 2-9') == 1 and md.rstrip().endswith('</figure>')


def test_text_inside_a_group_of_figures_does_not_change_its_nature_or_its_group_caption():
    """Figures with parsed text (labels, important words in the picture) and their own captions are still a figure
    group: one outer figure in the export, with the caption given to the group. The type still rules semantic
    boxes (a sidebar with a figure and text stays a box)."""
    from scientific_ocr.pipeline import groups, markdown, overlay
    from scientific_ocr.pipeline.groups import is_figure_group
    def blk(i, kind, text='', **rel):
        return {'id': i, 'page': 1, 'kind': kind, 'chosen': {'type': 'text', 'text': text} if text else {'type': 'image_only'},
                'relations': {'captions': [], **rel}, 'reasons': [], 'review': 'not_needed', 'reading_order': 0,
                'source_crop': {'path': f'assets/{i}.png'}, 'excluded': {}, 'flow': 'insert'}
    f1, c1 = blk('f1', 'figure', captions=['c1']), blk('c1', 'caption', '(Α) μιτοχόνδριο', caption_for='f1')
    t1, f2 = blk('t1', 'paragraph', 'ATP συνθάση → 3 ATP'), blk('f2', 'figure')
    gc = blk('gc', 'paragraph', 'ΕΙΚΟΝΑ 3-1 Οξειδωτική φωσφορυλίωση')
    blocks = [f1, c1, t1, f2, gc]
    overlay.COMMANDS['associate_group_caption'](gc, {'group_id': 'G1'}, {'blocks': blocks, 'by_id': {b['id']: b for b in blocks}})
    kinds = {b['id']: b['kind'] for b in blocks}
    for gtype, figure in (('figure_group', True), ('other', True), ('sidebar', False), ('example', False)):
        out = groups.annotate(blocks, [{'id': 'G1', 'type': gtype, 'name': '', 'members': ['f1', 'c1', 't1', 'f2']}], {})
        assert out[0]['figure'] is figure and out[0]['captions'] == ['gc'] and not gc['reasons']
        md = markdown.markdown({'blocks': blocks, 'groups': out, 'entities': [], 'source': {}, 'pages': []})
        outer = 'figure' if figure else 'aside'
        assert md.startswith(f'<{outer}>') and md.rstrip().endswith(f'</{outer}>')
        assert md.index('ATP συνθάση') < md.index('ΕΙΚΟΝΑ 3-1') and md.count('ΕΙΚΟΝΑ 3-1') == 1
    assert is_figure_group({'type': 'example', 'members': ['f1', 'c1', 'f2']}, kinds)      # all visual: a figure
    assert not is_figure_group({'type': 'other', 'members': ['t1', 'gc']}, kinds)          # no figure at all


def test_captions_are_inserts_by_default_and_follow_their_figure_unless_the_reviewer_says_otherwise():
    from scientific_ocr.pipeline import flow
    def blk(i, kind, text='x', **kw):
        return {'id': i, 'page': 1, 'kind': kind, 'chosen': {'type': 'text', 'text': text}, 'relations': kw.pop('rel', {}),
                'excluded': {}, **kw}
    fig = blk('f', 'figure', rel={'captions': ['c']})
    fig['chosen'] = {'type': 'image_only'}
    cap = blk('c', 'caption', 'Σχήμα 1', rel={'caption_for': 'f'})
    orphan, grp = blk('o', 'caption', 'Σχήμα 2'), blk('g', 'caption', 'ΕΙΚΟΝΑ 3', rel={'caption_group': 'G1'})
    mine = blk('m', 'caption', 'Σχήμα 4', flow_override='main')
    blocks = [blk('p', 'paragraph', 'Κείμενο.'), fig, cap, orphan, grp, mine]
    flow.annotate(blocks)
    assert [b['flow'] for b in blocks] == ['main', 'insert', 'insert', 'insert', 'insert', 'main']
    fig['flow_override'] = 'main'                         # the figure set to the main text: its caption follows
    flow.annotate(blocks)
    assert cap['flow'] == 'main' and orphan['flow'] == 'insert'


def test_images_figures_and_tables_are_always_inserts_by_default():
    """Visual content is an insert whatever made it visual: its kind or the reviewer's category. A text segment
    shown as an image for now (unreadable, image only, OCR pending) stays main text and may continue a paragraph;
    the reviewer's set_flow overrides."""
    from scientific_ocr.pipeline import flow
    def blk(i, kind, ctype='text', **kw):
        return {'id': i, 'page': 1, 'kind': kind, 'chosen': {'type': ctype, 'text': 'λέξη'}, 'relations': {}, 'excluded': {}, **kw}
    blocks = [blk('p', 'paragraph'), blk('t', 'table'), blk('f', 'figure', 'image_only'), blk('img', 'paragraph', 'image_only'),
              blk('u', 'unknown', 'unreadable'), blk('cat', 'paragraph'), blk('x', 'excerpt', 'excerpt'), blk('xt', 'excerpt'),
              blk('mine', 'figure', 'image_only', flow_override='main')]
    flow.annotate(blocks, frozenset({'cat'}))             # 'cat': the reviewer categorised it as a figure/table
    assert {b['id']: b['flow'] for b in blocks} == {'p': 'main', 't': 'insert', 'f': 'insert', 'img': 'main', 'u': 'main',
                                                     'cat': 'insert', 'x': 'insert', 'xt': 'main', 'mine': 'main'}


def _excursus_book():
    def blk(i, page, text, kind='paragraph', order=1):
        return {'id': i, 'page': page, 'kind': kind, 'chosen': {'type': 'text', 'text': text}, 'relations': {'captions': []},
                'reasons': [], 'review': 'not_needed', 'reading_order': order, 'excluded': {}, 'source_crop': None}
    return [blk('h', 1, 'Πρωτεΐνες', 'heading'), blk('c1', 1, 'Οι πρωτεΐνες αποτελούνται από αμινοξέα που', order=2),
            blk('xh', 2, 'ΙΣΤΟΡΙΚΟ ΣΗΜΕΙΩΜΑ', 'heading'), blk('x1', 2, 'Ο Πάουλινγκ πρότεινε το 1951 ότι η έλικα', order=2),
            blk('x2', 3, 'σταθεροποιείται με δεσμούς υδρογόνου.'),
            blk('c2', 4, 'συνδέονται με πεπτιδικούς δεσμούς.'), blk('h2', 4, 'Ένζυμα', 'heading', order=2),
            blk('c3', 4, 'Τα ένζυμα καταλύουν αντιδράσεις.', order=3)]


def test_an_excursus_of_several_pages_is_suggested_read_in_its_own_flow_and_placed_by_the_reviewer():
    """A course paragraph broken by two pages of historical note: the pages are suggested as an excursus; once
    marked, the course paragraph continues across them (context: course text) while the note's own paragraph
    continues inside the note (context: the excursus) — and the outputs join the paragraph and put the note in a
    box where the reviewer chose (in place, end of chapter, end of book, or nowhere)."""
    from scientific_ocr.pipeline import excursus, flow, markdown
    blocks = _excursus_book()
    flow.annotate(blocks)
    (sug,) = excursus.suggest(blocks, set(), flow.continues)
    assert (sug['first'], sug['last'], sug['interrupted'], sug['resumes']) == ('xh', 'x2', 'c1', 'c2')
    assert excursus.suggest(blocks, {sug['key']}, flow.continues) == []          # dismissed
    for placement in ('inplace', 'chapter_end', 'book_end', 'remove'):
        blocks = _excursus_book()
        ex = excursus.annotate(blocks, [{'id': 'X1', 'type': 'history', 'first': 'xh', 'last': 'x2', 'placement': placement}], {})
        assert ex[0]['members'] == ['xh', 'x1', 'x2'] and ex[0]['pages'] == [2, 3]
        flow.annotate(blocks)
        r = {b['id']: b['relations'] for b in blocks}
        assert r['c1']['continues_in'] == 'c2' and r['c2']['continues_strand'] == 'main'
        assert 'excursus:X1' in r['c2']['interrupted_by']
        assert r['x1']['continues_in'] == 'x2' and r['x2']['continues_strand'] == 'x:X1'
        assert excursus.suggest(blocks, set(), flow.continues) == []              # nothing left to suggest
        md = markdown.markdown({'blocks': blocks, 'groups': [], 'entities': [], 'excursus': ex, 'source': {}, 'pages': []})
        joined = md.index('αμινοξέα που συνδέονται')
        if placement == 'remove':
            assert 'Πάουλινγκ' not in md and '<aside' not in md
            continue
        note, enzymes = md.index('Πάουλινγκ'), md.index('Ένζυμα')
        assert md.count('<aside data-excursus="history"') == 1
        assert {'inplace': joined < note < enzymes, 'chapter_end': joined < note < enzymes,
                'book_end': joined < enzymes < note}[placement]


def test_categorised_segments_are_inserts_and_continue_inside_their_own_box():
    """A question box (any special category) is an insert by default: the course paragraph it interrupts
    continues past it, and the question's own text continues inside the box."""
    from scientific_ocr.pipeline import flow
    blocks = _excursus_book()
    boxes = {'xh': 'e:Q', 'x1': 'e:Q', 'x2': 'e:Q'}
    flow.annotate(blocks, frozenset(), boxes)
    f = {b['id']: b for b in blocks}
    assert [f[i]['flow'] for i in ('c1', 'x1', 'x2', 'c2')] == ['main', 'insert', 'insert', 'main']
    assert f['c1']['relations']['continues_in'] == 'c2' and 'box:Q' in f['c2']['relations']['interrupted_by']
    assert f['x1']['relations']['continues_in'] == 'x2' and f['x2']['relations']['continues_strand'] == 'e:Q'
    f['x1']['flow_override'] = 'main'                    # the reviewer takes a segment out of its box
    flow.annotate(blocks, frozenset(), boxes)
    assert f['x1']['flow'] == 'main' and f['c2']['relations']['continues_from'] == 'x1'   # now course text


def test_childless_marker_list_is_kept_and_split_into_items_with_docling_boxes():
    """Marker 2.1 returns a ListGroup with no children (only HTML). It is never dropped: each <li> becomes a Marker
    ListItem whose box is the union of the Docling boxes carrying its text (an item's wrapped tail included); when
    the boxes do not account for every item, the list stays one block with all its text."""
    from scientific_ocr.pipeline import engine_json as ej
    html = '<ul><li>• Alpha items contain a long first sentence that wraps onto a second line ending in bonds.</li>' \
           '<li>• Bravo is the second item of the list.</li><li>• Charlie closes the list.</li></ul>'
    raw = {'children': [{'block_type': 'Page', 'id': '/page/0/Page/0', 'children': [
        {'block_type': 'ListGroup', 'id': '/page/0/ListGroup/1', 'bbox': [50, 100, 500, 220], 'html': html, 'children': None}]}]}
    (m,) = ej.marker_blocks(raw, 1)
    assert m['label'] == 'ListGroup' and 'Bravo' in m['text'] and len(m['extra']['list_items']) == 3
    d = lambda i, y, t: {'ref': f'docling:{i}', 'engine': 'docling', 'kind': 'paragraph', 'text': t, 'bbox': [52, y, 498, y + 12]}
    dl = [d(0, 102, '5 Alpha items contain a long first sentence that wraps onto a second line'), d(1, 116, 'bonds.'),
          d(2, 140, '9 Bravo is the second item of the list.'), d(3, 170, 'e Charlie closes the list.')]
    items = ej.split_list_groups([m], dl)
    assert [x['label'] for x in items] == ['ListItem'] * 3 and all(x['role'] == 'list_item' for x in items)
    assert items[0]['text'].startswith('- Alpha') and items[0]['bbox'][1] == 102 and items[0]['bbox'][3] == 128
    assert items[1]['bbox'][1] == 140 and items[2]['text'] == '- Charlie closes the list.'
    assert all(x['extra']['bbox_from'] == 'docling' for x in items)
    kept = ej.split_list_groups([m], dl[:2])                     # boxes cannot account for every item
    assert kept == [m]


def test_decision_on_a_segment_that_changed_identity_is_found_where_its_content_is_now():
    """A Docling-only segment absorbed into another (e.g. Marker's reading of the same list item) gets a new id.
    The old decision is found again through the Docling element: satisfied when the text there already is the
    reviewer's, superseded by a later decision there, otherwise that segment is flagged for review — never replayed."""
    import json
    from scientific_ocr.application.projection import _relocate_stale
    from scientific_ocr.core.model import block_id, new_block
    sha = 'sha'

    def seg(i, text, dref, dbox):
        b = new_block(f'p1_new{i}', 1, 'paragraph', dbox, source='marker',
                      candidates=[{'engine': 'docling', 'ref': dref, 'bbox': dbox, 'text': text}])
        b['chosen'] = {'type': 'text', 'text': text, 'provenance': 'm'}
        return b
    blocks = [seg(0, '- Alpha item', 'docling:#/texts/1', [10, 10, 200, 20]),
              seg(1, '- Bravo item', 'docling:#/texts/2', [10, 30, 200, 40]),
              seg(2, '- Charlie itme', 'docling:#/texts/3', [10, 50, 200, 60])]
    old = lambda b: block_id(sha, 1, b['candidates'][0]['ref'] + json.dumps([round(v) for v in b['candidates'][0]['bbox']]))
    decisions = [{'seq': 1, 'block_id': old(blocks[0]), 'action': 'replace_text', 'value': 'Alpha item'},
                 {'seq': 2, 'block_id': old(blocks[1]), 'action': 'replace_text', 'value': 'Bravo fixed'},
                 {'seq': 3, 'block_id': old(blocks[2]), 'action': 'replace_text', 'value': 'Charlie item'},
                 {'seq': 4, 'block_id': 'p1_new1', 'action': 'approve'},
                 {'seq': 5, 'block_id': 'p1_nothing', 'action': 'replace_text', 'value': 'x'}]
    why = 'block no longer exists (layout or OCR changed)'
    result = {'stale': [{'seq': s, 'block_id': decisions[s - 1]['block_id'], 'action': 'replace_text', 'why': why} for s in (1, 2, 3, 5)],
              'ignored': []}
    _relocate_stale(blocks, result, decisions, sha)
    assert [r['seq'] for r in result['resolved_stale']] == [1]                      # text already the reviewer's
    assert [(r['seq'], r['now_in']) for r in result['ignored']] == [(2, 'p1_new1')]  # approved since
    assert [(r['seq'], r.get('now_in')) for r in result['stale']] == [(3, 'p1_new2'), (5, None)]
    assert 'DECISION_STALE' in blocks[2]['reasons'] and blocks[2]['review'] == 'required'
    assert blocks[2]['chosen']['text'] == '- Charlie itme'                          # never replayed onto new reading


def test_audit_bounds_nested_and_attributed_figures_and_asides():
    """A group of figures nests <figure>s and an excursus opens <aside data-…>: neither is an unbalanced boundary,
    and a caption inside the group is not 'outside its figure'. A caption really outside still is."""
    from pathlib import Path
    from scientific_ocr.pipeline import audit
    cap_text = 'ΕΙΚΟΝΑ 1-4 Η ζωή είναι μια διεργασία που αυτο-καταλύεται μέσω του DNA και των πρωτεϊνών του κυττάρου.'
    cap = {'id': 'c1', 'kind': 'caption', 'page': 1, 'relations': {'caption_for': 'f1'}, 'chosen': {'type': 'text', 'text': cap_text},
           'reasons': [], 'review': 'not_needed'}
    c = {'blocks': [cap], 'findings': []}
    inside = ('<figure data-group="g">\n<figure>\n![a](x.png)\n</figure>\n<figure>\n![b](y.png)\n</figure>\n'
              f'<figcaption>{cap_text}</figcaption>\n</figure>\n<aside data-excursus="history" title="t">\ntext\n</aside>\n')
    codes = lambda md: {f['type'] for f in audit.post_render(c, md, Path('.'), images=False)}
    assert not codes(inside) & {'CAPTION_BLEED', 'INTEGRITY_UNBALANCED_BOUNDARY'}
    assert 'CAPTION_BLEED' in codes(f'<figure>\n![a](x.png)\n</figure>\n\n{cap_text}\n')


# ---------------------------------------------------------------- cross-reading (pipeline.crossread)
def _fake_marker_regions(text):
    """Fake region OCR (adapters.extraction.regions_ocr): Marker reads `text` in every crop."""
    def regions_ocr(out, src_pdf, items, log=print, lang=None, engines_of=None):
        res = {}
        for r, force in items:
            d = out / 'raw' / 'regions' / f"fake-{r['id']}"
            d.mkdir(parents=True, exist_ok=True)
            bb = [0, 0, r['bbox'][2] - r['bbox'][0], r['bbox'][3] - r['bbox'][1]]
            (d / 'out.json').write_text(json.dumps({'block_type': 'Document', 'children': [{
                'id': '/page/0/Page/0', 'block_type': 'Page', 'bbox': bb, 'html': '', 'children': [
                    {'id': '/page/0/Text/0', 'block_type': force, 'bbox': bb, 'html': f'<p>{text}</p>',
                     'children': None, 'images': {}}]}]}))
            assert (engines_of or {}).get(r['id']) == ('marker',)
            res[r['id']] = {'crop_pdf': 'x', 'crop_sha': 'x', 'engines': {'marker': {
                'engine': 'marker', 'status': 'OK', 'page': 1, 'version': 'test',
                'raw_json': str((d / 'out.json').relative_to(out))}}}
        return res
    return regions_ocr


def _cross_read(run_dir, monkeypatch, marker_text, tesseract_text='Bottom line that no engine extracted at all'):
    from scientific_ocr.adapters import extraction, quick_ocr
    from scientific_ocr.application import processing
    monkeypatch.setattr(extraction, 'regions_ocr', _fake_marker_regions(marker_text))
    monkeypatch.setattr(quick_ocr, 'read', lambda png, langs: tesseract_text)
    n = processing.cross_read(run_dir, [1], log=lambda m: None)
    assert build(run_dir) == 0
    return n, load(run_dir)[0]


def test_a_box_only_docling_found_is_read_by_marker_before_review(run_dir, monkeypatch):
    n, c = _cross_read(run_dir, monkeypatch, 'Beta paragraph only docling saw')
    assert n >= 1
    beta = next(b for b in c['blocks'] if 'Beta' in json.dumps(b['candidates']))
    assert 'CROSS_READ' in beta['reasons'] and 'SINGLE_ENGINE_DOCLING' not in beta['reasons']
    assert beta['kind'] == 'paragraph' and beta['chosen'] == {'type': 'text', 'text': 'Beta paragraph only docling saw',
                                                               'provenance': 'crossread:marker'}
    assert {x['engine'] for x in beta['candidates']} == {'docling', 'marker'}
    assert beta['match']['status'] == 'matched'
    # idempotent: nothing is read again
    from scientific_ocr.application import processing
    assert processing.cross_read(run_dir, [1], log=lambda m: None) == 0


def test_a_cross_read_that_disagrees_keeps_the_excerpt_with_both_readings(run_dir, monkeypatch):
    _, c = _cross_read(run_dir, monkeypatch, 'Completely different words entirely here')
    beta = next(b for b in c['blocks'] if 'Beta' in json.dumps(b['candidates']))
    assert beta['kind'] == 'excerpt' and beta['review'] == 'required'
    assert {'CROSS_READ', 'SINGLE_ENGINE_DOCLING'} <= set(beta['reasons'])
    assert beta['match']['status'] == 'conflict'


def test_ink_no_engine_boxed_is_not_cross_read(run_dir, monkeypatch):
    """Only boxes an ENGINE found are read again: uncovered ink is mostly noise the review-load work keeps quiet."""
    from scientific_ocr.pipeline import crossread
    from scientific_ocr.application import projection
    ctx = next(projection.page_contexts(run_dir, [1], lambda m: None))
    assert {t['source'] for t in crossread.targets(ctx)} <= {'docling', 'marker'}
    _, c = _cross_read(run_dir, monkeypatch, 'Bottom line that no engine extracted at all')
    low = next(b for b in c['blocks'] if 'UNCOVERED_INK_REGION' in b['reasons'])
    assert 'CROSS_READ' not in low['reasons'] and not any(x['engine'] == 'marker' for x in low['candidates'])


def test_a_box_only_marker_found_gets_tesseracts_reading_as_evidence(tmp_path, monkeypatch):
    from conftest import docling_json
    dj = docling_json()
    dj['texts'] = [t for t in dj['texts'] if t['self_ref'] != '#/texts/3']          # Docling misses the caption
    dj['body']['children'] = [x for x in dj['body']['children'] if x['$ref'] != '#/texts/3']
    dj['pictures'][0]['captions'] = []
    out = make_run(tmp_path, docling=dj)
    _, c = _cross_read(out, monkeypatch, 'unused', tesseract_text='Figure 1. A caption for the figure')
    cap = next(b for b in c['blocks'] if b['kind'] == 'caption')
    assert 'CROSS_READ' in cap['reasons'] and 'SINGLE_ENGINE_MARKER' not in cap['reasons']
    assert cap['match'] == {'status': 'matched', 'shape': 'crossread', 'text_similarity': 1.0}
    assert any(x['engine'] == 'tesseract' and x['ref'] == 'crossread:tesseract' for x in cap['candidates'])
