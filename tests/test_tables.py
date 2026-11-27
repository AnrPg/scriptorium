"""Term grids read as lists (pipeline.tables): automatic hint with an independent check, continuation lines joined
only on geometric evidence, the reviewer's set_table_layout, and the outputs (RAG, reading model)."""
import numpy as np
import pytest

from scientific_ocr.core import profiles
from scientific_ocr.core.decisions import event_errors, event_pages
from scientific_ocr.core.model import new_block
from scientific_ocr.pipeline import overlay, rag, reading, tables

# «ΒΑΣΙΚΟΙ ΟΡΟΙ» as an engine reads it: three columns, alphabetical DOWN each column; two long terms wrap onto an
# indented line that the engine made a cell of its own
TERMS = ('<table>'
         '<tr><td>αφετηρία αντιγραφής</td><td>ετεροχρωματίνη</td><td>συμπληρωματικός</td></tr>'
         '<tr><td>γενετικός κώδικας</td><td>ευχρωματίνη</td><td>σύμπλοκο αναδιαμόρφωσης</td></tr>'
         '<tr><td>γονίδιο</td><td>ζεύγος βάσεων</td><td>της χρωματίνης</td></tr>'
         '<tr><td>διπλή έλικα</td><td>ιστόνη</td><td>τελομερίδιο</td></tr>'
         '<tr><td>ένζυμο που τροποποιεί</td><td>καρυότυπος</td><td>χρωματίνη</td></tr>'
         '<tr><td>τις ιστόνες</td><td>κεντρομερίδιο</td><td></td></tr>'
         '</table>')
COLS = [100.0, 250.0, 400.0]                   # left edge of each printed column (page units)
INDENTED = {(2, 2), (5, 0)}                    # (row, col) printed with an indent


def _ink(indented=INDENTED):
    """What pipeline.page measures on the scan: one line per row, one run of ink per non-empty cell."""
    rows = tables.grid(TERMS).rows
    return [{'y': [200 + 12 * r, 210 + 12 * r],
             'runs': [[COLS[c] + (8 if (r, c) in indented else 0), COLS[c] + 90] for c, (t, _) in enumerate(row) if t]}
            for r, row in enumerate(rows)]


def _table(html=TERMS, ink=True, **extra):
    extra.setdefault('chosen', {'type': 'text', 'text': html})
    b = new_block('p1_1', 1, 'table', [90, 195, 500, 280], source='marker', **extra)
    b['reading_order'] = 1
    if ink:
        b['ink_lines'] = _ink()
    return b


def test_a_terms_grid_is_a_list_by_columns_with_wrapped_terms_joined():
    b = _table()
    hint = tables.suggest(b)
    assert hint['order'] == 'cols' and hint['evidence'] == ['alphabetical']
    items = tables.items(b, TERMS, 'cols')
    assert items[:5] == ['αφετηρία αντιγραφής', 'γενετικός κώδικας', 'γονίδιο', 'διπλή έλικα', 'ένζυμο που τροποποιεί τις ιστόνες']
    assert 'σύμπλοκο αναδιαμόρφωσης της χρωματίνης' in items and 'τις ιστόνες' not in items and len(items) == 15
    rows = tables.items(b, TERMS, 'rows')
    assert rows[:3] == ['αφετηρία αντιγραφής', 'ετεροχρωματίνη', 'συμπληρωματικός']


def test_nothing_is_joined_without_geometric_evidence():
    b = _table(ink=False)
    assert 'τις ιστόνες' in tables.items(b, TERMS, 'cols')
    b['ink_lines'] = _ink()[:-1]                                    # lines do not match the rows: no evidence
    assert 'τις ιστόνες' in tables.items(b, TERMS, 'cols')


def test_a_hyphenated_line_end_joins_into_one_word():
    html = ('<table><tr><td>αλληλουχία</td><td>ρετροϊός</td></tr><tr><td>πολυμορφισμός μεμονω-</td><td>σθένος</td></tr>'
            '<tr><td>μένου νουκλεοτιδίου</td><td>υδρόλυση</td></tr><tr><td>πρωτεΐνη</td><td>φωσφολιπίδιο</td></tr></table>')
    b = _table(html, ink=False)
    b['ink_lines'] = [{'y': [0, 10], 'runs': [[100, 150], [250, 300]]}, {'y': [12, 22], 'runs': [[100, 150], [250, 300]]},
                      {'y': [24, 34], 'runs': [[108, 150], [250, 300]]}, {'y': [36, 46], 'runs': [[100, 150], [250, 300]]}]
    assert 'πολυμορφισμός μεμονωμένου νουκλεοτιδίου' in tables.items(b, html, 'cols')


def test_real_tables_are_not_lists():
    header = '<table><tr><th>Οργανισμός</th><th>Γονίδια</th></tr><tr><td>άνθρωπος</td><td>19000</td></tr>' \
             '<tr><td>βακτήριο</td><td>4300</td></tr><tr><td>ζύμη</td><td>6600</td></tr></table>'
    data = '<table><tr><td>1655</td><td>Ο Hooke χρησιμοποίησε ένα πρωτόγονο μικροσκόπιο για να περιγράψει πόρους.</td></tr>' \
           '<tr><td>1674</td><td>Ο Leeuwenhoek ανέφερε την ανακάλυψή του.</td></tr></table>'
    unsorted = '<table><tr><td>υδρογόνο</td><td>κέρδος</td><td>απώλεια</td></tr><tr><td>ήλιον</td><td>κέρδος</td><td>απώλεια</td></tr>' \
               '<tr><td>οξυγόνο</td><td>κέρδος</td><td>απώλεια</td></tr><tr><td>άνθρακας</td><td>κέρδος</td><td>απώλεια</td></tr></table>'
    for html in (header, data, unsorted):
        assert tables.suggest(_table(html, ink=False)) is None


def test_the_key_terms_label_is_evidence_and_an_image_table_names_the_engine_reading():
    unsorted = '<table><tr><td>ζ όρος</td><td>α όρος</td></tr><tr><td>β όρος</td><td>κ όρος</td></tr>' \
               '<tr><td>ω όρος</td><td>δ όρος</td></tr></table>'
    b = _table(unsorted, ink=False, entities=[{'type': 'key_terms'}])
    assert tables.suggest(b)['evidence'] == ['entity:key_terms']
    img = _table(ink=False, chosen={'type': 'image_only'})
    img['candidates'] = [{'engine': 'docling', 'text': 'x'}, {'engine': 'marker', 'text': TERMS}]
    hint = tables.suggest(img)
    assert hint['candidate'] == 'marker' and tables.layout({**img, 'table_list': hint}) is None   # not until adopted


def test_reviewer_layout_overrides_the_hint_and_is_a_page_local_undoable_decision():
    b = _table()
    tables.annotate([b])
    assert tables.layout(b) == 'cols'
    ev = overlay.decision_event('set_table_layout', b, {'layout': 'table'}, lambda i: None)
    assert event_errors({**ev, 'seq': 1}) == [] and event_pages(ev, {}, {}, {}) == {1}
    overlay.COMMANDS['set_table_layout'](b, ev, None)
    assert tables.layout(b) is None
    overlay.COMMANDS['set_table_layout'](b, {**ev, 'layout': 'rows'}, None)
    assert tables.layout(b) == 'rows'
    with pytest.raises(overlay.CommandError):
        overlay.decision_event('set_table_layout', new_block('p1_2', 1, 'paragraph', [0, 0, 1, 1], source='marker',
                                                             chosen={'type': 'text', 'text': 'x'}), {'layout': 'cols'}, None)


def test_rag_and_the_reading_model_render_the_list():
    b = _table()
    tables.annotate([b])
    c = {'source': {'sha256': 'abc'}, 'blocks': [b], 'groups': [], 'entities': [], 'excursus': []}
    node = next(n for n in reading.nodes(c) if n['t'] == 'figure')
    assert node['body']['items'][0] == 'αφετηρία αντιγραφής'
    u = rag.units(c, profiles.effective('rag'))[0]
    assert u['t'] == 'list' and u['text'].startswith('- αφετηρία αντιγραφής\n- γενετικός κώδικας')
    assert '|' not in rag.markdown(c, profiles.effective('rag')) and 'ένζυμο που τροποποιεί τις ιστόνες' in u['terms']


def test_ink_lines_keep_accents_with_their_line_and_split_columns():
    m = np.zeros((60, 300), bool)
    for y0 in (5, 25, 45):                       # three lines of text, 10 px high, 10 px leading
        m[y0:y0 + 10, 10:60] = True              # column 1
        m[y0:y0 + 10, 150:220] = True            # column 2 (gap far wider than a line)
    m[3:4, 30:33] = True                         # an accent above line 1, separated by one empty row
    lines = tables.ink_lines(m, 0, 0, 1)
    assert len(lines) == 3 and [len(x['runs']) for x in lines] == [2, 2, 2] and lines[0]['y'][0] == 3


def test_choosing_how_a_table_reads_records_what_it_implies_as_one_step():
    """A list needs text (an image adopts the engine reading) and the reading judged is confirmed: the server
    expands it, so a single segment, a selection or any button behave alike; 'auto' confirms nothing."""
    def acts(blk, layout):
        return [e['action'] for e in overlay.decision_events('set_table_layout', blk, {'layout': layout}, lambda i: None)]

    machine = _table(review='required', auto_hash='h1')
    assert acts(machine, 'cols') == ['set_table_layout', 'approve'] and acts(machine, 'table') == ['set_table_layout', 'approve']
    assert acts(machine, 'auto') == ['set_table_layout']
    assert acts({**machine, 'review': 'approved'}, 'rows') == ['set_table_layout']
    img = _table(ink=False, chosen={'type': 'image_only'})
    img['candidates'] = [{'engine': 'docling', 'text': 'x'}, {'engine': 'marker', 'text': TERMS}]
    evs = overlay.decision_events('set_table_layout', img, {'layout': 'cols'}, lambda i: None)
    assert [(e['action'], e.get('engine')) for e in evs] == [('approve_candidate', 'marker'), ('set_table_layout', None)]
    assert acts(img, 'table') == ['set_table_layout']                       # stays an image
    img['candidates'] = [{'engine': 'docling', 'text': 'x'}]
    with pytest.raises(overlay.CommandError):                               # never recorded without effect
        overlay.decision_events('set_table_layout', img, {'layout': 'cols'}, lambda i: None)


def test_page_output_lists_every_kind_of_automatic_exclusion():
    """Furniture has a page side; fills, duplicates and empty fragments do not: the reviewer's page output must
    render every kind (a missing optional field once left the whole «Αποτέλεσμα» view loading forever)."""
    from scientific_ocr.pipeline import html_preview
    blocks = []
    for i, ex in enumerate([{'as': 'page_number', 'side': 'bottom', 'key': 'k1', 'signals': ['x']},
                            {'as': 'background_fill', 'fill_for': 'p1_9', 'key': 'k2', 'signals': ['x']},
                            {'as': 'duplicate_reading', 'key': 'k3', 'signals': ['x']}], 1):
        b = new_block(f'p1_{i}', 1, 'paragraph', [0, 10 * i, 100, 10 * i + 8], source='marker',
                      chosen={'type': 'text', 'text': f't{i}'})
        b['excluded'] = ex
        b['reading_order'] = i
        blocks.append(b)
    c = {'source': {'sha256': 'abc'}, 'blocks': blocks, 'groups': [], 'entities': [], 'excursus': []}
    html = html_preview.page_html(c, 1, lambda p: p)
    assert 'page_number · bottom: t1' in html and 'background_fill: t2' in html and 'duplicate_reading: t3' in html
