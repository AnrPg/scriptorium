"""The glossary stage (pipeline.glossary) and its RAG form: one entry = term (bold) + definition = one chunk."""
from scientific_ocr.core import profiles
from scientific_ocr.core.model import new_block
from scientific_ocr.pipeline import audit, glossary, rag

RAG = profiles.effective('rag')


def _b(page, i, kind, text, role=None):
    b = new_block(f'p{page}_{i}', page, kind, [50, 30 * i, 550, 30 * i + 25], source='marker', chosen={'type': 'text', 'text': text})
    b['reading_order'] = i
    b['role'] = role
    return b


def _head(page, text):
    b = _b(page, 0, 'excerpt', '', role='page_header')
    b['chosen'] = {'type': 'image_only'}
    b['candidates'] = [{'engine': 'docling', 'text': text}]
    return b


ENTRIES = ['**αδελφή χρωματίδα** Αντίγραφο χρωμοσώματος που παραμένει συνδεδεμένο.',
           '**ADP** Διφωσφορικό νουκλεοτίδιο.',
           '**αλληλόμορφο** Μια εναλλακτική μορφή ενός γονιδίου.\n**αλληλουχία** Η γραμμική σειρά των υπομονάδων.',
           '**κυκλίνη G<sub>1</sub>**, Ρυθμιστική πρωτεΐνη.']


def _glossary_page(page=10):
    return [_head(page, 'ΛΕΞΙΚΟ ΕΠΙΣΤΗΜΟΝΙΚΩΝ ΟΡΩΝ'), _b(page, 1, 'heading', 'Γ / C / G')] + \
           [_b(page, i + 2, 'paragraph', t) for i, t in enumerate(ENTRIES)] + [_b(page, 9, 'paragraph', 'και συνέχεια του ορισμού.')]


def _canon(blocks):
    return {'source': {'sha256': 'abc'}, 'blocks': blocks, 'groups': [], 'entities': [], 'excursus': []}


def test_entries_are_detected_with_evidence_and_not_labels_or_bold_subjects():
    answers = [_b(3, i, 'paragraph', f'**ΑΠΑΝΤΗΣΗ 7-{i}.** Η απάντηση είναι αυτή.') for i in range(1, 6)]
    prose = [_b(4, i, 'paragraph', t) for i, t in enumerate(['**Τα ενδιάμεσα ινίδια** μοιάζουν με σχοινιά.',
             '**ΠΡΟΣΚΟΛΛΗΣΗ:** Στην αρχή του κύκλου.', '**Οι μικροσωληνίσκοι** είναι κύλινδροι.', '**Η ακτίνη** σχηματίζει ινίδια.'], 1)]
    blocks = _glossary_page() + answers + prose
    res = glossary.annotate(blocks)
    assert res['pages'] == [10] and res['label'] == 'ΛΕΞΙΚΟ ΕΠΙΣΤΗΜΟΝΙΚΩΝ ΟΡΩΝ'
    by = {b['id']: b for b in blocks}
    assert by['p10_4']['glossary']['terms'] == ['αλληλόμορφο', 'αλληλουχία']
    assert by['p10_5']['glossary']['terms'] == ['κυκλίνη G<sub>1</sub>']
    assert by['p10_9']['glossary']['terms'] == []           # the rest of an entry
    assert by['p10_1'].get('glossary_letter')
    assert not any(b.get('glossary') for b in answers + prose)
    assert glossary.annotate(blocks, {10: False})['pages'] == []            # the reviewer said: not a glossary


def test_the_reviewer_marks_a_page_a_glossary_without_evidence_and_undo_restores():
    from scientific_ocr.adapters.event_store import fold
    from scientific_ocr.core.decisions import event_errors
    blocks = [_b(7, 1, 'paragraph', '**ομόλογος** Γονίδιο που μοιάζει με άλλο.'), _b(7, 2, 'paragraph', 'συνέχεια του ορισμού.')]
    assert glossary.annotate(blocks)['pages'] == []                      # one entry, no running head: no evidence
    res = glossary.annotate(blocks, {7: True})
    assert res['pages'] == [7] and res['evidence']['7'] == 'marked by the reviewer'
    assert blocks[0]['glossary']['terms'] == ['ομόλογος'] and blocks[1]['glossary']['terms'] == []
    ev = {'seq': 1, 'type': 'glossary_page', 'pages': [7], 'glossary': True, 'actor': 'reviewer'}
    assert event_errors(ev) == [] and event_errors({**ev, 'glossary': 'yes'}) and event_errors({**ev, 'pages': []})
    assert fold([ev])['glossary_pages'] == {7: True}
    assert fold([ev, {'seq': 2, 'type': 'undo', 'targets': [1], 'actor': 'reviewer'}])['glossary_pages'] == {}


def test_split_reads_each_entry_and_the_text_before_the_first():
    head, pairs = glossary.split('τέλος ορισμού.\nαλληλόμορφο Μια μορφή.\nαλληλουχία Η σειρά.', ['αλληλόμορφο', 'αλληλουχία'])
    assert head == 'τέλος ορισμού.' and pairs == [('αλληλόμορφο', 'Μια μορφή.'), ('αλληλουχία', 'Η σειρά.')]


def test_rag_writes_each_entry_with_bold_term_one_chunk_each_and_loses_nothing():
    c = _canon(_glossary_page())
    c['glossary'] = glossary.annotate(c['blocks'])
    from scientific_ocr.pipeline import profile
    profile.text_policy(c['blocks'], RAG)                    # the RAG profile strips styling before review
    md = rag.markdown(c, RAG)
    assert '# ΛΕΞΙΚΟ ΕΠΙΣΤΗΜΟΝΙΚΩΝ ΟΡΩΝ' in md and '## Γ / C / G' in md
    assert '**Όρος: αλληλόμορφο**\nΟρισμός: Μια εναλλακτική μορφή ενός γονιδίου.' in md
    assert 'Ορισμός: Ρυθμιστική πρωτεΐνη. και συνέχεια του ορισμού.' in md   # the continuation joins its entry
    assert audit.rag_export(c, RAG, md) == []
    ch = [x for x in rag.chunks(c, RAG, 't') if x.get('term')]
    assert [x['term'] for x in ch] == ['αδελφή χρωματίδα', 'ADP', 'αλληλόμορφο', 'αλληλουχία', 'κυκλίνη G$_{1}$']
    assert all(x['types'] == ['definition'] and x['breadcrumb'] == ['ΛΕΞΙΚΟ ΕΠΙΣΤΗΜΟΝΙΚΩΝ ΟΡΩΝ', 'Γ / C / G'] for x in ch)
    c['glossary']['entries'] = glossary.entries(c)
    assert [e['term'] for e in c['glossary']['entries']][:2] == ['αδελφή χρωματίδα', 'ADP']


def test_an_entry_whose_reading_lost_its_bold_is_still_an_entry_on_a_glossary_page():
    """A reading approved from an engine without styling («οργανοειδές Τρισδιάστατη…») is not glued to the previous
    definition; text continuing a definition (lowercase, punctuation, no capital soon) is not taken for a term."""
    assert glossary.plain_term('οργανοειδές Τρισδιάστατη μικρογραφία ιστών.') == 'οργανοειδές'
    assert glossary.plain_term('αρπαχτικά πρωτόζωα, π.χ. το Amoeba proteus.') is None
    assert glossary.plain_term('ρο. Περιλαμβάνει το δίκτυο.') is None
    blocks = _glossary_page() + [_b(10, 10, 'paragraph', 'οργανοειδές Τρισδιάστατη μικρογραφία.')]
    glossary.annotate(blocks)
    assert blocks[-1]['glossary']['terms'] == ['οργανοειδές']
