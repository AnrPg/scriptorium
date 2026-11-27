"""Output profiles (core.profiles, pipeline.profile) and the RAG serialisation (pipeline.rag)."""
import json

import pytest
from conftest import build, make_run

from scientific_ocr.adapters.event_store import EventStore, fold
from scientific_ocr.core import profiles
from scientific_ocr.core.decisions import event_errors
from scientific_ocr.core.model import in_reader_output, new_block, release_status
from scientific_ocr.pipeline import profile, rag


def _b(i, kind, text, page=1, **extra):
    chosen = {'type': 'text', 'text': text} if text is not None else None
    b = new_block(f'p{page}_{i}', page, kind, [50, 40 * i, 550, 40 * i + 30], source='marker', chosen=chosen, **extra)
    b['reading_order'] = i
    return b


def _ent(key, etype, members, gran='box', conf='high', **extra):
    return {'key': key, 'type': etype, 'granularity': gran, 'confidence': conf, 'members': members, 'pages': [1],
            'verified': gran == 'human', 'placement': 'inline', 'label': etype, **extra}


RAG = profiles.effective('rag')


# ---------------------------------------------------------------- profiles as data + the event
def test_profile_overrides_are_validated_and_applied_on_a_copy():
    p = profiles.effective('rag', {'heading_depth': 2, 'unknown': 1})
    assert p['heading_depth'] == 2 and 'unknown' not in p and profiles.PROFILES['rag']['heading_depth'] == 3
    assert profiles.effective('nope')['name'] == 'reading'
    assert event_errors({'type': 'output_profile', 'profile': 'rag'}) == []
    assert event_errors({'type': 'output_profile', 'profile': 'x'})
    assert event_errors({'type': 'output_profile', 'profile': 'rag', 'overrides': {'heading_depth': 9}})


def test_output_profile_event_folds_and_undoes():
    evs = [{'seq': 1, 'type': 'output_profile', 'profile': 'rag', 'actor': 'reviewer'}]
    assert fold(evs)['output_profile']['profile'] == 'rag'
    assert fold(evs + [{'seq': 2, 'type': 'undo', 'targets': [1], 'actor': 'reviewer'}])['output_profile'] is None


# ---------------------------------------------------------------- what the profile excludes
def test_box_question_is_excluded_automatically_but_a_heading_subtree_only_suggested():
    """A box has independent boundary evidence; a 'section title' entity (a margin label set as a heading) would own
    the body text after it — it is only suggested, so nothing of the body is lost."""
    blocks = [_b(1, 'paragraph', 'Body text.'), _b(2, 'heading', 'ΕΡΩΤΗΣΗ 1-2'), _b(3, 'paragraph', 'Why?'),
              _b(4, 'heading', 'ΕΡΩΤΗΣΗ 1-3'), _b(5, 'paragraph', 'Body that follows the label.')]
    ents = [_ent('E1', 'question', ['p1_2', 'p1_3']), _ent('E2', 'question', ['p1_4', 'p1_5'], gran='section')]
    s = profile.apply(blocks, ents, [], RAG)
    out = [b['id'] for b in blocks if in_reader_output(b)]
    assert out == ['p1_1', 'p1_4', 'p1_5']
    assert s['pending'] == 1 and [e['key'] for e in s['suggested']] == ['E2']
    assert release_status(blocks, [], s['pending']) == 'REVIEW_REQUIRED'


def test_collective_section_closing_the_chapter_is_automatic():
    blocks = [_b(1, 'heading', 'ΕΡΩΤΗΣΕΙΣ'), _b(2, 'paragraph', 'Q1')]
    e = _ent('E', 'question', ['p1_1', 'p1_2'], gran='section', collective=True, placement='end_of_chapter')
    profile.apply(blocks, [e], [], RAG)
    assert not any(in_reader_output(b) for b in blocks)


def test_boundary_that_cuts_a_body_paragraph_keeps_that_member_and_flags_it():
    blocks = [_b(1, 'paragraph', 'The cell divides and'), _b(2, 'paragraph', 'Question?'), _b(3, 'paragraph', 'its halves')]
    blocks[0]['relations']['continues_in'] = 'p1_3'
    blocks[2]['relations']['continues_from'] = 'p1_1'
    ent = _ent('E', 'question', ['p1_2', 'p1_3'])           # wrong boundary: swallowed the end of the paragraph
    profile.apply(blocks, [ent], [], RAG)
    assert in_reader_output(blocks[2]) and 'PROFILE_BOUNDARY_CHECK' in blocks[2]['reasons']
    assert not in_reader_output(blocks[1])


def test_answered_question_stays_with_its_answer_and_reviewer_keep_wins():
    blocks = [_b(1, 'paragraph', 'Q 1.1'), _b(2, 'paragraph', 'A 1.1'), _b(3, 'paragraph', 'Q 1.2')]
    ents = [_ent('Q1', 'question', ['p1_1'], answers=['p1_2']), _ent('A', 'answer', ['p1_2']), _ent('Q2', 'question', ['p1_3'])]
    blocks[2]['kept'] = {'seq': 9}
    profile.apply(blocks, ents, [], RAG)
    assert all(in_reader_output(b) for b in blocks)


def test_excursus_type_removed_and_reading_profile_changes_nothing():
    blocks = [_b(1, 'paragraph', 'History of the microscope'), _b(2, 'paragraph', 'Body')]
    x = [{'id': 'X1', 'type': 'history', 'members': ['p1_1'], 'pages': [1]}]
    assert profile.apply(blocks, [], x, profiles.effective('reading'))['excluded'] == []
    assert all(in_reader_output(b) for b in blocks)
    profile.apply(blocks, [], x, RAG)
    assert [in_reader_output(b) for b in blocks] == [False, True]


def test_text_only_output_asks_again_about_content_kept_only_as_an_image():
    """A table kept as an image under another profile (carried into RAG) would vanish from a text-only output."""
    t = _b(1, 'table', None)
    t['review'] = 'image_only'
    t['decisions'] = [{'seq': 3, 'action': 'mark_image_only', 'scope': 'reading'}]
    pic = _b(2, 'figure', None)
    pic['review'] = 'recommended'
    pic['reasons'] = ['FIGURE_INTERNAL_TEXT_WITHHELD']
    s = profile.apply([t, pic], [], [], RAG)
    assert 'TEXT_ONLY_LOSS' in t['reasons'] and t['review'] == 'required'
    assert pic['review'] == 'not_needed' and 'PROFILE_NOT_RELEVANT' in pic['reasons']
    assert s['text_only_loss'] == 1 and s['pictures_not_reviewed'] == 1
    t2 = _b(3, 'table', None)
    t2['review'], t2['decisions'] = 'approved', [{'seq': 12, 'action': 'approve', 'scope': 'rag'}]   # under RAG: stands
    profile.apply([t2], [], [], RAG)
    assert t2['review'] == 'approved'


# ---------------------------------------------------------------- the RAG text
def test_clean_strips_markup_keeps_math_collects_bold_terms():
    s, terms = rag.clean('Το **γονιδίωμα** και <i>κύτταρα</i> με H<sub>2</sub>O και $x^{2}$ δια-\nσπάται­')
    assert s == 'Το γονιδίωμα και κύτταρα με H$_{2}$O και $x^{2}$ διασπάται' and terms == ['γονιδίωμα']


def test_tables_become_markdown_or_rows():
    assert rag.table_text('<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>') == '| A | B |\n|---|---|\n| 1 | 2 |'
    assert rag.table_text('<table><tr><td>1655</td><td>Hooke</td></tr></table>') == '- 1655: Hooke'
    assert rag.table_text('<table><tr><th>A</th><th>B</th></tr><tr><td colspan="2">x</td></tr></table>') == '- A / B: x'   # said once, under both columns


def _canon(blocks, **extra):
    return {'source': {'sha256': 'abc'}, 'blocks': blocks, 'groups': [], 'entities': [], 'excursus': [], **extra}


def test_markdown_flattens_deep_headings_drops_pictures_and_places_captions_after_the_reference():
    h1 = _b(1, 'heading', 'Κύτταρα', heading_level=1)
    h4 = _b(2, 'heading', 'Μικροσκόπια', heading_level=4)
    fig = _b(3, 'figure', None)
    cap = _b(4, 'caption', '**ΕΙΚΟΝΑ 1-4** Ένα κύτταρο. (Με την άδεια του E.B. Lewis)', caption_label='ΕΙΚΟΝΑ 1-4')
    fig['relations']['captions'] = ['p1_4']
    cap['relations']['caption_for'] = 'p1_3'
    p1 = _b(5, 'paragraph', 'Πρώτη παράγραφος.')
    p2 = _b(6, 'paragraph', 'Όπως δείχνει η Εικόνα 1-4.')
    p2['relations']['refers_to'] = ['p1_3']
    lonely = _b(7, 'figure', None)
    md = rag.markdown(_canon([h1, h2 := h4, fig, cap, p1, p2, lonely]), RAG)
    assert md.split('\n\n') == ['# Κύτταρα', 'Μικροσκόπια. Πρώτη παράγραφος.', 'Όπως δείχνει η Εικόνα 1-4.',
                                'ΕΙΚΟΝΑ 1-4: Ένα κύτταρο.\n'] and h2


def test_chunks_carry_breadcrumbs_pages_terms_and_respect_sizes():
    long = ' '.join(['Λέξη πρότασης εδώ.'] * 150)                # 450 words: split at sentence ends
    blocks = [_b(1, 'heading', 'Κεφάλαιο', heading_level=1), _b(2, 'heading', 'Ενότητα', heading_level=2),
              _b(3, 'paragraph', 'Το **κύτταρο** ζει. ' * 30), _b(4, 'paragraph', long, page=2), _b(5, 'paragraph', 'Τέλος.', page=2)]
    cfg = {**RAG, 'chunk': {'target_words': 120, 'max_words': 200, 'min_words': 40}}
    ch = rag.chunks(_canon(blocks), cfg, 'Βιβλίο')
    assert all(c['breadcrumb'] == ['Κεφάλαιο', 'Ενότητα'] and c['text'].startswith('Κεφάλαιο › Ενότητα\n\n') for c in ch)
    assert all(c['words'] <= 200 for c in ch) and ch[0]['key_terms'] == ['κύτταρο'] and ch[-1]['pages'] == [2]
    assert 'Τέλος.' in ch[-1]['text'] and len({c['id'] for c in ch}) == len(ch)
    assert json.loads(rag.jsonl(ch).splitlines()[0])['book'] == 'Βιβλίο'


# ---------------------------------------------------------------- end to end: event -> build -> export gate
def test_rag_profile_end_to_end_build_and_export(tmp_path):
    from scientific_ocr.adapters.storage import Library
    from scientific_ocr.application.services import App, ServiceError
    from test_application import FakeJobs
    out = make_run(tmp_path)
    lib = Library(tmp_path)
    lib.adopt(out)
    assert build(out) == 0
    app = App(lib, FakeJobs())
    with pytest.raises(ServiceError):
        app.set_output_profile('run', 'rag')                     # a change needs the reviewer's choice (core.scopes)
    app.set_output_profile('run', 'rag', carry='copy')
    with pytest.raises(ServiceError):
        app.export('run', 'markdown_text')                       # built for 'reading': never exported as 'rag'
    assert build(out) == 0
    st = app.output_profile('run')
    assert st['current'] == 'rag' and st['built'] == 'rag' and not st['awaiting_rebuild']
    c = json.loads((out / 'canonical.json').read_text())
    if rag.unsettled(c):
        with pytest.raises(ServiceError):
            app.export('run', 'rag_zip')
    else:
        data, ctype, name = app.export('run', 'rag_zip')
        assert name.endswith('.rag.zip') and data[:2] == b'PK'
    assert app.preview('run', 'rag', False).strip()
    with pytest.raises(ServiceError):
        app.set_output_profile('run', 'nope')
    assert EventStore(out).state()['output_profile']['profile'] == 'rag'


def test_rag_context_sentences_are_kept_apart_and_content_addressed(tmp_path):
    """The local model's situating sentence goes to the chunk's own 'context' field; asked once per chunk."""
    from scientific_ocr.application import rag_context
    from scientific_ocr.application.exports import rag_context as stored
    out = make_run(tmp_path)
    assert build(out) == 0

    class Fake:
        name, calls = 'fake', 0

        def complete_json(self, system, user, schema, **kw):
            Fake.calls += 1
            return {'json': {'context': 'Situates the excerpt.'}}
    assert rag_context.run(out, Fake(), log=lambda m: None) == 0
    first = Fake.calls
    assert first and set(stored(out).values()) == {'Situates the excerpt.'}
    assert rag_context.run(out, Fake(), log=lambda m: None) == 0 and Fake.calls == first     # cached
    c = json.loads((out / 'canonical.json').read_text())
    ch = rag.chunks(c, RAG, 't', stored(out))
    assert all(x.get('context') == 'Situates the excerpt.' and 'Situates' not in x['text'] for x in ch)


def test_an_exclusion_with_wrong_boundaries_is_corrected_in_one_step(tmp_path):
    """Right kind, wrong limits: the segments from the new start to the new end (reading order, furniture left out)
    become the reviewer's own entity of the same type; the detection is dismissed as wrongly bounded; one undo."""
    from scientific_ocr.adapters.storage import Library
    from scientific_ocr.application.services import App, ServiceError
    from scientific_ocr.pipeline import entities
    from test_application import FakeJobs
    blocks = [_b(i, 'paragraph', f't{i}') for i in range(1, 6)]
    blocks[2]['flow'] = 'furniture'
    assert entities.span(blocks, 'p1_4', 'p1_2') == ['p1_2', 'p1_4']            # either order; furniture left out
    with pytest.raises(ValueError):
        entities.span(blocks, 'p1_3', 'p1_5')                                    # a boundary must be text
    out = make_run(tmp_path)
    lib = Library(tmp_path)
    lib.adopt(out)
    assert build(out) == 0
    app = App(lib, FakeJobs())
    ids = [b['id'] for b in app.page('run', 1)['blocks'] if b.get('flow') != 'furniture']
    assert len(ids) >= 3
    detected = {'key': 'Nx', 'type': 'question', 'granularity': 'box', 'members': ids[:1], 'verified': False}
    c = json.loads((out / 'canonical.json').read_text())
    c['entities'] = [detected]
    (out / 'canonical.json').write_text(json.dumps(c))
    with pytest.raises(ServiceError):
        app.set_entity_bounds('run', 'Nx', ids[0], ids[0])                     # nothing changes
    r = app.set_entity_bounds('run', 'Nx', ids[0], ids[2])
    assert r['members'] == ids[:3]
    evs = EventStore(out).load()[-2:]
    assert evs[0]['type'] == 'suggestion_dismiss' and evs[0]['boundary'] and evs[1]['type'] == 'entity_assign'
    assert evs[1]['entity_type'] == 'question' and evs[0].get('group') == evs[1].get('group')    # one undo
    assert build(out) == 0
    ent = next(e for e in json.loads((out / 'canonical.json').read_text())['entities'] if e['key'] == r['key'])
    assert ent['verified'] and ent['members'] == ids[:3]


def test_profile_text_policy_strips_styling_before_review_and_keeps_the_source():
    """RAG drops the page's styling from the CONTENT before review (the reviewer checks what the output says);
    formulas and sub/superscripts are notation, not styling; the styled source and the bold terms are kept."""
    from scientific_ocr.core.text import strip_emphasis
    assert strip_emphasis('τα *γλυκολιπίδια* (**Εικόνα 11-7**) και <i>x</i> $a*b*c$ H<sub>2</sub>O') == \
        'τα γλυκολιπίδια (Εικόνα 11-7) και x $a*b*c$ H<sub>2</sub>O'
    blocks = [_b(1, 'paragraph', 'τα *γλυκολιπίδια* και το **γονιδίωμα**.'), _b(2, 'paragraph', 'απλό κείμενο')]
    assert profile.text_policy(blocks, profiles.effective('reading')) == 0 and '*' in blocks[0]['chosen']['text']
    assert profile.text_policy(blocks, RAG) == 1
    assert blocks[0]['chosen']['text'] == 'τα γλυκολιπίδια και το γονιδίωμα.'
    assert blocks[0]['chosen']['profile_source'].startswith('τα *γλυκολιπίδια*') and blocks[0]['emphasis_terms'] == ['γονιδίωμα']
    assert profile.text_policy(blocks, RAG) == 0                                   # idempotent
    u = rag.units(_canon(blocks), RAG)
    assert u[0]['text'] == 'τα γλυκολιπίδια και το γονιδίωμα.' and u[0]['terms'] == ['γονιδίωμα']   # key term kept


def test_a_picture_caption_goes_to_the_end_of_its_section_right_before_the_next_title():
    h1 = _b(1, 'heading', 'Ενότητα Α', heading_level=2)
    fig = _b(2, 'figure', None)
    cap = _b(3, 'caption', 'ΕΙΚΟΝΑ 1-4 Ένα κύτταρο.', caption_label='ΕΙΚΟΝΑ 1-4')
    fig['relations']['captions'] = ['p1_3']
    cap['relations']['caption_for'] = 'p1_2'
    p1 = _b(4, 'paragraph', 'Όπως δείχνει η Εικόνα 1-4, το κύτταρο.')
    p1['relations']['refers_to'] = ['p1_2']
    p2 = _b(5, 'paragraph', 'Η ενότητα συνεχίζει.')
    deep = _b(6, 'heading', 'Λεπτομέρεια', heading_level=5)          # leads its paragraph: not the section end
    p3 = _b(7, 'paragraph', 'Κι άλλο κείμενο.')
    h2 = _b(8, 'heading', 'Ενότητα Β', heading_level=2)
    md = rag.markdown(_canon([h1, fig, cap, p1, p2, deep, p3, h2]), RAG).split('\n\n')
    assert md[:4] == ['## Ενότητα Α', 'Όπως δείχνει η Εικόνα 1-4, το κύτταρο.', 'Η ενότητα συνεχίζει.',
                      'Λεπτομέρεια. Κι άλλο κείμενο.']
    assert md[4] == 'ΕΙΚΟΝΑ 1-4: Ένα κύτταρο.' and md[5].strip() == '## Ενότητα Β'


def test_an_exclusion_verdict_is_undone_on_its_own_even_after_other_actions(tmp_path):
    """The exclusions review has its own undo: the latest verdict on one exclusion (from «Όλα σωστά», only that
    one), or the last exclusion step (like Ctrl-Z, focused); later unrelated actions stay. A verdict counts at once
    (before any rebuild) and a confirmation undone since the build makes the exclusion pending again."""
    from scientific_ocr.adapters.storage import Library
    from scientific_ocr.application.services import App, ServiceError
    from test_application import FakeJobs
    out = make_run(tmp_path)
    lib = Library(tmp_path)
    lib.adopt(out)
    assert build(out) == 0
    app = App(lib, FakeJobs())
    ids = [b['id'] for b in app.page('run', 1)['blocks'] if b.get('flow') != 'furniture']
    c = json.loads((out / 'canonical.json').read_text())
    c['entities'] = [{'key': k, 'type': 'question', 'granularity': 'box', 'members': [ids[i]], 'pages': [1],
                      'verified': k == 'D'} for i, k in enumerate('ABCD')]
    c['profile'] = {'name': 'rag', 'suggested': [],
                    'excluded': [{'key': k, 'type': 'question', 'members': [ids[i]], 'pages': [1], 'n': 1,
                                  'confirmed': k == 'D'} for i, k in enumerate('ABCD')]}
    (out / 'canonical.json').write_text(json.dumps(c))
    pending = lambda: sorted(e['key'] for e in app.output_profile('run')['excluded'] if not e['confirmed'] and not e['verdict'])
    assert pending() == ['A', 'B', 'C', 'D']                        # D's confirmation is not in the log
    with pytest.raises(ServiceError):
        app.undo_exclusion('run')                                   # nothing judged yet
    EventStore(out).append({'type': 'entity_confirm', 'key': 'D', 'entity_type': 'question'})   # the one the build saw
    assert pending() == ['A', 'B', 'C'] and app.output_profile('run')['pending'] == 3
    app.confirm_exclusions('run')                                   # «Όλα σωστά»: one step
    assert pending() == [] and len(app.output_profile('run')['decided']) == 4
    EventStore(out).append({'type': 'block_hide', 'block_id': ids[-1]})       # an unrelated later action
    r = app.undo_exclusion('run', 'B')
    assert r['keys'] == ['B'] and pending() == ['B']
    assert ids[-1] in EventStore(out).state()['hidden']            # the later action stays
    app.keep_in_output('run', 'B')                                  # «not this»
    assert pending() == [] and next(d for d in app.output_profile('run')['decided'] if d['key'] == 'B')['verdict'] == 'kept'
    assert app.undo_exclusion('run')['keys'] == ['B']               # the last exclusion step
    assert sorted(app.undo_exclusion('run')['keys']) == ['A', 'C'] and pending() == ['A', 'B', 'C']
    app.redo('run')                                                 # an ordinary undo: redo brings it back
    assert pending() == ['B']
    app.undo_exclusion('run', 'D')                                  # confirmed when built, undone since -> pending
    assert pending() == ['B', 'D']


# ---------------------------------------------------------------- nothing the RAG arranges may get lost
def _c(blocks):
    return {'source': {'sha256': 'abc'}, 'blocks': blocks, 'groups': [], 'entities': [], 'excursus': []}


def test_a_deep_title_before_a_table_or_at_the_end_is_kept_and_the_audit_checks_the_rag_output():
    from scientific_ocr.pipeline import audit
    deep = _b(1, 'heading', 'Πίνακας ενζύμων', heading_level=5)
    tbl = _b(2, 'table', '<table><tr><th>Ένζυμο</th><th>Ρόλος</th></tr><tr><td>λιπάση</td><td>υδρόλυση</td></tr></table>')
    last = _b(3, 'heading', 'Τελευταίος υπότιτλος', heading_level=5)
    for b in (deep, tbl, last):
        b['flow'] = 'main'
    c = _c([deep, tbl, last])
    md = rag.markdown(c, RAG)
    assert 'Πίνακας ενζύμων\n| Ένζυμο | Ρόλος |' in md and 'Τελευταίος υπότιτλος' in md
    assert audit.rag_export(c, RAG, md) == []
    lost = audit.rag_export(c, RAG, md.replace('Τελευταίος υπότιτλος', ''))
    assert [f['block_ids'] for f in lost] == [['p1_3']] and lost[0]['type'] == 'INTEGRITY_CONTENT_NOT_EXPORTED'


def test_a_table_with_a_title_row_and_spanned_headers_keeps_every_cell():
    html = ('<table><tr><th colspan="3">ΠΙΝΑΚΑΣ 13-1 Ένζυμα</th></tr><tr><th>Ένζυμο</th><th colspan="2">Ρόλος</th></tr>'
            '<tr><th></th><th>γενικός</th><th>στη γλυκόλυση</th></tr>'
            '<tr><td>Κινάση</td><td>φωσφορυλίωση</td><td>βήματα 1 και 3</td></tr></table>')
    t = rag.table_text(html)
    assert t.startswith('ΠΙΝΑΚΑΣ 13-1 Ένζυμα\n| Ένζυμο | Ρόλος / γενικός | Ρόλος / στη γλυκόλυση |')
    assert '| Κινάση | φωσφορυλίωση | βήματα 1 και 3 |' in t


def test_a_tables_caption_keeps_its_printed_place_above_or_below_the_table():
    for above in (True, False):
        cap = _b(1 if above else 3, 'caption', 'ΠΙΝΑΚΑΣ 16-4 Μερικές απαντήσεις')
        tbl = _b(2, 'table', '<table><tr><th>Μόριο</th><th>Στόχος</th></tr><tr><td>βαζοπρεσσίνη</td><td>ήπαρ</td></tr></table>')
        cap['relations']['caption_for'], tbl['relations']['captions'] = tbl['id'], [cap['id']]
        md = rag.markdown(_c([cap, tbl] if above else [tbl, cap]), RAG)
        assert (md.index('ΠΙΝΑΚΑΣ 16-4') < md.index('| Μόριο')) == above


def test_an_excursus_removed_by_the_profile_takes_every_verdict_of_the_exclusions_loop(tmp_path):
    """Root cause of «unknown entity» on a historical note: an exclusion may be an EXCURSUS (its type removed by the
    profile), not an entity. New ends re-bound it (it becomes the reviewer's), «Σωστό» makes it the reviewer's,
    «Όχι αυτό» removes it; each is a verdict of the loop (listed, undoable)."""
    from scientific_ocr.adapters.storage import Library
    from scientific_ocr.application.services import App, ServiceError
    from test_application import FakeJobs
    out = make_run(tmp_path)
    lib = Library(tmp_path)
    lib.adopt(out)
    assert build(out) == 0
    app = App(lib, FakeJobs())
    ids = [b['id'] for b in app.page('run', 1)['blocks'] if b.get('flow') != 'furniture']
    assert len(ids) >= 4
    c = json.loads((out / 'canonical.json').read_text())
    auto = lambda i, a, z: {'id': i, 'first': a, 'last': z, 'type': 'history', 'name': 'Ιστορία', 'placement': 'inplace',
                            'source': 'entity', 'members': [a, z], 'pages': [1]}
    c['excursus'] = [auto('xe1', ids[0], ids[1]), auto('xe2', ids[2], ids[2]), auto('xe3', ids[3], ids[3])]
    c['profile'] = {**(c.get('profile') or {}), 'excluded': [
        {'key': x['id'], 'type': 'excursus:history', 'label': '', 'pages': [1], 'members': x['members'], 'confirmed': True}
        for x in c['excursus']], 'suggested': []}
    (out / 'canonical.json').write_text(json.dumps(c))
    assert {e['key'] for e in app.output_profile('run')['excluded'] if not e['confirmed']} == {'xe1', 'xe2', 'xe3'}
    with pytest.raises(ServiceError):
        app.set_entity_bounds('run', 'xe1', ids[0], ids[1])                       # the same ends
    r = app.set_entity_bounds('run', 'xe1', ids[0], ids[2])                       # new ends
    assert (r['first'], r['last']) == (ids[0], ids[2])
    ev = EventStore(out).load()[-1]
    assert ev['type'] == 'excursus_set' and ev['excursus']['id'] == 'xe1' and ev['verdict'] == 'bounds'
    app.confirm_exclusions('run', ['xe2'])                                          # «Σωστό»
    app.keep_in_output('run', 'xe3')                                                # «Όχι αυτό»
    assert EventStore(out).load()[-1]['type'] == 'suggestion_dismiss'
    verdicts = {d['key']: d['verdict'] for d in app.output_profile('run')['decided']}
    assert verdicts == {'xe1': 'bounds', 'xe2': 'confirmed', 'xe3': 'kept'}
    assert app.undo_exclusion('run')['keys'] == ['xe3']                             # the loop's ↶ finds it
