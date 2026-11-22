"""Domain core: pure functions, no files or engines."""
import pytest

from scientific_ocr.adapters.event_store import fold
from scientific_ocr.core import chem, reasons, rules, symbols, text
from scientific_ocr.core import geometry as geo
from scientific_ocr.core.model import new_block, release_status


# ---------------------------------------------------------------- geometry
def test_iou_containment_union():
    assert geo.iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1
    assert geo.containment([0, 0, 10, 10], [0, 0, 100, 100]) == 1
    assert geo.covered_by([0, 0, 100, 100], [0, 0, 10, 10]) == pytest.approx(0.01)
    assert geo.union([[0, 0, 1, 1], [5, 5, 6, 6]]) == [0, 0, 6, 6]


def test_insertion_respects_columns():
    seq = [[0, 0, 280, 100], [0, 110, 280, 400], [320, 0, 600, 100], [320, 110, 600, 200]]
    assert geo.insertion_index(seq, [320, 210, 600, 260]) == 4
    assert geo.insertion_index(seq, [0, 105, 280, 108]) == 1
    assert geo.insertion_index([[0, 0, 280, 100], [0, 300, 280, 400]], [0, 150, 280, 200]) == 1


def test_side_by_side_detects_split_lines_not_stacked_paragraphs():
    left, right = [50, 500, 300, 560], [305, 502, 560, 558]
    assert geo.side_by_side(left, right, max_gap=20)
    assert not geo.side_by_side([50, 100, 560, 150], [50, 160, 560, 210], max_gap=20)   # stacked
    assert not geo.side_by_side(left, [400, 502, 560, 558], max_gap=20)                 # wide gap = columns


# ---------------------------------------------------------------- text & symbols
def test_lookalikes_fold_but_similarity_does_not():
    assert text.fold('Α') == text.fold('A') and text.fold('О') == 'O' and text.fold('0') != text.fold('O')
    assert text.similarity('Κ', 'C') == 0.0


def test_token_conflicts_are_script_independent():
    crit = lambda p, q: {(x['primary'], x['secondary']) for x in symbols.token_conflicts(p, q) if x['critical']}
    assert crit('βάση -Α, Κ, Γ ή Τ-', 'βάση -A, C, G ή T-') == {('Κ', 'C'), ('Γ', 'G')}   # Α/A, Τ/T: lookalike
    assert crit(r'$\text{H}_2\text{O}$', 'H20') == {('H2O', 'H20')}
    assert crit('Die Base G', 'Die Base Γ')                                              # any language
    assert not symbols.token_conflicts('long sentence about cells', 'lnog sentnce abuot cels')


def test_script_mix_fallback():
    assert symbols.script_mix('βάση Κ με μια G')['severe']
    assert not symbols.script_mix('Κανόνα Α, d τροχιακά')['severe']            # lowercase Latin symbols are fine
    assert not symbols.script_mix('(Α) x (Β) (Γ) 5-2Γ')['severe']               # labels judged among themselves
    assert symbols.script_mix('(Α) κείμενο (G) άλλο')['severe']   # mixed label scripts, G has no Greek twin


def test_glyph_issues_and_latex_norm():
    assert 'DIGIT_ZERO_WHERE_OXYGEN_LIKELY' in symbols.glyph_issues('( - P 0 _ { 4 } )')
    assert symbols.glyph_issues(r'\Delta G = \Delta H - T \Delta S') == []
    assert 'UNBALANCED_BRACES' in symbols.glyph_issues('x^{2')
    assert symbols.latex_norm('a + b') == symbols.latex_norm('a+b')
    assert symbols.latex_norm('x^{2}') != symbols.latex_norm('x_{2}')


# ---------------------------------------------------------------- chemistry
def test_equation_key_keeps_arrow_type_and_charges():
    mk = r'\text{HCN}(aq) + \text{H}_2\text{O}(l) \longrightarrow \text{H}_3\text{O}^+(aq) + \text{CN}^-(aq)'
    um = r'\mathrm { H C N } ( a q ) + \mathrm { H } _ { 2 } \mathrm { O } ( l ) \rightarrow \mathrm { H } _ { 3 } \mathrm { O } ^ { + } ( a q ) + \mathrm { C N } ^ { - } ( a q )'
    assert chem.equation_key(mk) == chem.equation_key(um)
    assert chem.equation_key(mk) != chem.equation_key(mk.replace(r'\longrightarrow', r'\rightleftharpoons'))
    assert chem.equation_key(mk) != chem.equation_key(mk.replace('^+', '^-'))
    assert chem.is_equation(mk) and not chem.is_equation(r'x \rightarrow y')


def test_visual_classification():
    assert chem.classify_visual([':Cl-S—O:', ':CI—Cl—0:'], [])['kind'] == 'lewis_structure'
    assert chem.classify_visual(['HNO3'], [])['kind'] == 'figure'


# ---------------------------------------------------------------- book rules
def test_rules_isolated_tokens_context_and_exclusions():
    r = rules.validate({'id': 'r1', 'find': 'Γ', 'replace': 'G', 'context': 'βάσ'})
    t, ch = rules.apply('Η βάση Γ. (Γ) Πάνελ. ΓΓ', [r], 'b1')
    assert t == 'Η βάση G. (Γ) Πάνελ. ΓΓ' and len(ch) == 1                      # context + isolation respected
    r2 = {**r, 'context': '', 'exclude': ['b1#1']}
    assert rules.apply('Γ και (Γ)', [r2], 'b1')[0] == 'G και (Γ)'               # occurrence #1 excluded
    with pytest.raises(rules.RuleError):
        rules.validate({'id': 'x', 'find': 'a', 'replace': 'b', 'context': '('})


# ---------------------------------------------------------------- reasons & model
def test_flag_escalates_never_deescalates():
    b = new_block('b', 1, 'paragraph', [0, 0, 1, 1], source='marker')
    reasons.flag(b, 'TEXT_POSSIBLY_INCOMPLETE')
    reasons.flag(b, 'INLINE_NOTATION_UNVERIFIED')
    assert b['review'] == 'required' and b['display_crop']
    assert all(set(r.el) and set(r.en) for r in reasons.REASONS.values())       # every reason has both texts


def test_release_status_requires_human_review():
    b = new_block('b', 1, 'paragraph', [0, 0, 1, 1], source='marker')
    assert release_status([b], []) == 'LLM_READY_WITH_EXCERPTS'
    b['review'] = 'approved'
    assert release_status([b], []) == 'VERIFIED'
    assert release_status([b], [{'type': 'INTEGRITY_X', 'severity': 'critical'}]) == 'DRAFT'


# ---------------------------------------------------------------- event folding (pure)
def _ev(seq, type_, **kw):
    return {'seq': seq, 'type': type_, **kw}


def test_fold_undo_redo_and_groups():
    evs = [_ev(1, 'decision', block_id='a', action='approve'),
           _ev(2, 'block_hide', block_id='x', group='g2'), _ev(3, 'region_add', region={'id': 'r', 'page': 1}, group='g2')]
    st = fold(evs)
    assert st['undo_targets'] == [2, 3] and st['hidden'] == ['x'] and st['regions']
    st = fold(evs + [_ev(4, 'undo', targets=[2, 3])])
    assert st['hidden'] == [] and not st['regions'] and st['redo_targets'] == [2, 3] and st['undo_targets'] == [1]
    st = fold(evs + [_ev(4, 'undo', targets=[2, 3]), _ev(5, 'redo', targets=[2, 3])])
    assert st['hidden'] == ['x'] and st['redo_targets'] == []
    st = fold(evs + [_ev(4, 'undo', targets=[2, 3]), _ev(5, 'decision', block_id='b', action='approve')])
    assert st['redo_targets'] == []                                              # new action clears redo


def test_conflict_classes_are_explicit():
    k = symbols.conflict_class
    expected = {('HNO3', 'HNO'): 'partial_reading', ('1940', '194'): 'partial_reading', ('15', '15α'): 'partial_reading',
                ('Η', 'Ἡ'): 'lookalike', ('Α', 'A'): 'lookalike', ('C', 'Ο'): 'unrelated', ('Γ', '6'): 'unrelated',
                ('HCl', 'HCI'): 'unrelated', ('G', 'Γ'): 'script_swap', ('Κ', 'C'): 'script_swap',
                ('H3O', 'H30'): 'digit_letter', ('PO4', 'P04'): 'digit_letter', ('3', '2'): 'number', ('3η', '2η'): 'number'}
    for (a, b), cls in expected.items():
        assert k(a, b) == cls, (a, b, k(a, b))
    assert set(symbols.CRITICAL_CLASSES) == {'script_swap', 'digit_letter', 'number', 'partial_reading'}


def test_equation_key_ignores_latex_spacing_macros():
    a = r'\text{NaOH(s)} \xrightarrow{\text{H}_2\text{O}} \text{Na}^+(aq) + \text{OH}^-(aq)'
    b = (r'\mathrm { N a O H } ( s ) \ \xrightarrow { \mathrm { H } _ { 2 } \mathrm { O } } \ '
         r'\mathrm { N a } ^ { + } ( a q ) + \mathrm { O H } ^ { - } ( a q )')
    assert chem.equation_key(a) == chem.equation_key(b)


def test_rules_respect_an_independent_reading_of_the_glyph():
    g = rules.validate({'id': 'g', 'find': 'Γ', 'replace': 'G'})
    t, ch = rules.apply('(Β) μουσική, (Γ) Morse', [g], 'b', second_text='(B) μουσική, (Γ) Morse')
    assert t == '(Β) μουσική, (Γ) Morse' and not ch                     # Docling also saw Γ: really printed
    assert rules.apply('βάσεων Γ και Κ', [g], 'b', second_text='βάσεων 6 και 0')[0] == 'βάσεων G και Κ'
    a = rules.validate({'id': 'a', 'find': 'Α', 'replace': 'A'})
    assert rules.apply('βάση Α', [a], 'b', second_text='βάση A')[0] == 'βάση A'   # lookalikes: evidence n/a


def test_words_are_not_compared_as_symbols():
    assert not [c for c in symbols.token_conflicts('όλα τα μόρια και τη δομή', 'όλα Ta μόρια και TH δομή') if c['critical']]


# ---------------------------------------------------------------- proposals from notes
def _prop(**kw):
    base = {'understanding': 'x', 'action': 'none', 'value': None, 'rule': None, 'label': None, 'level': None,
            'confidence': 'high', 'clarification': None}
    return {**base, **kw}


def test_proposal_validation_and_deterministic_description():
    from scientific_ocr.core import proposals as P
    from scientific_ocr.pipeline.engine_json import label_catalog
    labels, seg = label_catalog(), {'kind': 'paragraph', 'text': 'Η βάση Γ ζευγαρώνει με την Κ.'}
    assert P.validate(_prop(action='replace_text', value='Η βάση G'), seg, labels) == []
    assert P.validate(_prop(action='replace_text', value=' '), seg, labels)
    assert P.validate(_prop(action='replace_latex', value='x^{2'), seg, labels)
    assert P.validate(_prop(action='add_rule', rule={'find': 'Γ', 'replace': 'G', 'context': ''}), seg, labels) == []
    assert P.validate(_prop(action='add_rule', rule={'find': 'Ω', 'replace': 'W', 'context': ''}), seg, labels)  # absent
    assert P.validate(_prop(action='relabel', label={'engine': 'marker', 'name': 'Equation'}), seg, labels) == []
    assert P.validate(_prop(action='relabel', label={'engine': 'marker', 'name': 'Poem'}), seg, labels)
    assert P.validate({'action': 'replace_text'}, seg, labels)                                   # schema errors
    d = P.describe(_prop(action='add_rule', rule={'find': 'Γ', 'replace': 'G', 'context': ''}), seg, labels)
    assert '«Γ»' in d and '«G»' in d


# ---------------------------------------------------------------- triage statistics (pure)
def test_wilson_bound_and_sample_size():
    from scientific_ocr.core.stats import sample_size, wilson_upper
    assert wilson_upper(0, 0) == 1.0 and wilson_upper(0, 100) < 0.04 and wilson_upper(10, 20) > 0.5
    n = sample_size(0.05)
    assert wilson_upper(0, n) <= 0.05 < wilson_upper(0, n - 1)


def _blk(i, reasons, review='required', decision=None, image_only=False):
    from scientific_ocr.core.model import new_block
    b = new_block(f'b{i}', 1, 'paragraph', [0, 0, 1, 1], source='marker')
    b['reasons'], b['review'], b['machine_reasons'], b['machine_image_only'] = list(reasons), review, list(reasons), image_only
    if decision:
        b['decisions'] = [{'action': decision}]
    return b


def test_triage_lowers_only_proven_non_critical_flags():
    from scientific_ocr.pipeline import triage
    reviewed = [_blk(i, ['TEXT_POSSIBLY_INCOMPLETE'], 'approved', 'approve') for i in range(80)]
    reviewed += [_blk(100 + i, ['SYMBOL_READING_CONFLICT'], 'approved', 'approve') for i in range(80)]
    open_ = [_blk(200, ['TEXT_POSSIBLY_INCOMPLETE']), _blk(201, ['SYMBOL_READING_CONFLICT'])]
    res = triage.apply(reviewed + open_, [], [])
    assert open_[0]['review'] == 'recommended' and 'TRIAGE_DOWNGRADED' in open_[0]['reasons']
    assert open_[1]['review'] == 'required'                                   # critical: never lowered
    assert 'TEXT_POSSIBLY_INCOMPLETE' in res['reliable'] and 'SYMBOL_READING_CONFLICT' not in res['reliable']


def test_triage_needs_evidence_and_respects_errors():
    from scientific_ocr.pipeline import triage
    few = [_blk(i, ['TEXT_POSSIBLY_INCOMPLETE'], 'approved', 'approve') for i in range(5)]
    wrong = [_blk(50 + i, ['ENGINE_TEXT_CONFLICT_STRONG'], 'approved', 'replace_text' if i % 3 == 0 else 'approve')
             for i in range(60)]
    o1, o2 = _blk(200, ['TEXT_POSSIBLY_INCOMPLETE']), _blk(201, ['ENGINE_TEXT_CONFLICT_STRONG'])
    triage.apply(few + wrong + [o1, o2], [], [])
    assert o1['review'] == 'required' and o2['review'] == 'required'         # too little evidence / too many errors


def test_sampling_acceptance_marks_class_accepted_not_verified():
    from scientific_ocr.core.model import release_status
    from scientific_ocr.pipeline import triage
    o = _blk(1, ['RUNNING_HEADER_EMPTY'], 'recommended')
    c = _blk(2, ['SYMBOL_READING_CONFLICT'])
    triage.apply([o, c], [], [{'reason': 'RUNNING_HEADER_EMPTY', 'seq': 7}, {'reason': 'SYMBOL_READING_CONFLICT', 'seq': 8}])
    assert o['review'] == 'accepted' and c['review'] == 'required'            # critical never accepted
    o2 = _blk(3, ['RUNNING_HEADER_EMPTY'], 'accepted')
    assert release_status([o2], []) == 'LLM_READY_WITH_EXCERPTS'              # statistical != VERIFIED


def test_strip_math_delimiters_only_enclosing_pairs():
    from scientific_ocr.core.symbols import strip_math_delimiters as f
    assert f('$$\n\\frac{a}{b}\n$$') == '\\frac{a}{b}'
    assert f('\\[ y \\]') == 'y' and f('$x$') == 'x'
    assert f('a = 5\\$') == 'a = 5\\$' and f('x$') == 'x$'          # lone / escaped dollar kept


def test_lineage_dag_parallel_reads_converge_and_parts_are_roots():
    from scientific_ocr.core import lineage
    from scientific_ocr.core.model import new_block
    b = new_block('p1_x', 1, 'paragraph', [0, 0, 10, 10], source='merge',
                  candidates=[{'engine': 'marker', 'text': 'a'}, {'engine': 'docling', 'text': 'a'}],
                  chosen={'type': 'text', 'text': 'a', 'provenance': 'human:r@t'},
                  match={'status': 'matched', 'shape': '1:1', 'text_similarity': 1.0},
                  merged_from=[{'id': 'p1_a', 'content_source': 'marker'}, {'id': 'p1_b', 'content_source': 'docling'}],
                  decisions=[{'action': 'replace_text', 'actor': 'reviewer', 'seq': 3}])
    g = lineage.dag(b)
    st = {n['id']: n['stage'] for n in g['nodes']}
    parents = lambda nid: [a for a, z in g['edges'] if z == nid]
    roots = [n for n in st if not parents(n)]
    assert [st[r] for r in roots] == ['part', 'part']                         # merged parts converge
    reads = [n for n in st if st[n] == 'read']
    cmp_ = next(n for n in st if st[n] == 'compared')
    assert len(reads) == 2 and sorted(parents(cmp_)) == sorted(reads)          # parallel readings feed the comparison
    final = [n for n in g['nodes'] if n.get('final')]
    assert len(final) == 1 and st[parents(final[0]['id'])[0]] == 'human'      # decisions precede the final content
    assert not [n for n in st if n != final[0]['id'] and not any(a == n for a, _ in g['edges'])]   # single sink


def test_kind_vocabularies_are_complete_and_consistent():
    from scientific_ocr.core.model import (
        CAPTIONABLE_KINDS,
        EQUATION_KINDS,
        FIGURE_KINDS,
        KINDS,
        REVIEW_PRIORITY,
    )
    assert set(REVIEW_PRIORITY) == set(KINDS)                         # every kind has a review priority
    assert EQUATION_KINDS | CAPTIONABLE_KINDS | FIGURE_KINDS <= set(KINDS)


def test_approving_a_candidate_of_any_equation_kind_keeps_latex():
    from scientific_ocr.core.model import EQUATION_KINDS, new_block
    from scientific_ocr.pipeline import overlay
    for kind in EQUATION_KINDS:
        b = new_block('p1_x', 1, kind, [0, 0, 1, 1], source='marker',
                      candidates=[{'engine': 'unimernet', 'latex': '{}^{235}_{92}U', 'text': ''}])
        overlay.COMMANDS['approve_candidate'](b, {'engine': 'unimernet', 'action': 'approve_candidate'}, None)
        assert b['chosen']['type'] == 'latex', kind


def test_symbol_conflict_classes_are_a_closed_vocabulary():
    from scientific_ocr.core.symbols import (
        CRITICAL_CLASSES,
        MINOR_CLASSES,
        conflict_class,
    )
    assert not set(CRITICAL_CLASSES) & set(MINOR_CLASSES)
    for x, y in [('HNO3', 'HNO'), ('Α', 'A'), ('Γ', '6'), ('2', 'Z'), ('3', '8'), ('α', 'a'), ('Cl', 'CI')]:
        cls = conflict_class(x, y)
        assert cls is None or cls in CRITICAL_CLASSES or cls in MINOR_CLASSES, (x, y, cls)


def test_event_schemas_are_enforced_on_append(tmp_path):
    from scientific_ocr.adapters.event_store import EventError, EventStore
    es = EventStore(tmp_path)
    es.append({'type': 'decision', 'block_id': 'p1_a', 'action': 'replace_text', 'value': 'x'})
    for bad in ({'type': 'decision', 'block_id': 'p1_a', 'action': 'replace_text'},     # missing value
                {'type': 'decision', 'block_id': 'p1_a', 'action': 'move_after_block'},  # needs after or before
                {'type': 'group_add'}, {'type': 'nonsense'}):
        with pytest.raises(EventError):
            es.append(bad)
    assert len(es.load()) == 1                                                          # nothing partial written


def test_commands_validate_requests_before_anything_is_recorded():
    from scientific_ocr.core.model import new_block
    from scientific_ocr.pipeline.overlay import CommandError, decision_event
    a = new_block('p1_a', 1, 'paragraph', [0, 0, 1, 1], source='marker', chosen={'type': 'text', 'text': 'x'})
    other = new_block('p2_b', 2, 'paragraph', [0, 0, 1, 1], source='marker')
    find = {'p2_b': other}.get
    for action, params in (('replace_text', {'value': '  '}), ('set_heading_level', {'level': 99}),
                           ('move_after_block', {'after': 'p2_b'}), ('approve_candidate', {'engine': 'x'}),
                           ('revert', {}), ('nope', {})):
        with pytest.raises(CommandError):
            decision_event(action, a, params, find)
    ev = decision_event('replace_latex', a, {'value': '$$x^2$$'}, find)
    assert ev['value'] == 'x^2' and ev['prev_hash'] is None and ev['action'] == 'replace_latex'
