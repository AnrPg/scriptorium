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


# a real table read as a list WITH LEVELS: the reviewer picks the key column (per row) / key row (per column)
ELEMENTS = ('<table><tr><th>Στοιχείο</th><th>Μάζα</th><th>Φορτίο</th></tr>'
            '<tr><td>πρωτόνιο</td><td>1</td><td>+1</td></tr>'
            '<tr><td>νετρόνιο</td><td>1</td><td></td></tr>'
            '<tr><td>ηλεκτρόνιο</td><td>1/1836</td><td>−1</td></tr></table>')


def _keyed(layout, key, html=ELEMENTS):
    b = _table(html, ink=False)
    ev = overlay.decision_event('set_table_layout', b, {'layout': layout, 'key': key}, lambda i: None)
    overlay.COMMANDS['set_table_layout'](b, ev, None)
    return b


def test_per_row_the_key_column_names_each_item_and_the_other_columns_are_its_keys():
    b = _keyed('rows', 0)
    lst = tables.as_list(b, ELEMENTS)
    assert lst['label'] == 'Στοιχείο'
    assert lst['items'][0] == {'key': 'πρωτόνιο', 'fields': [['Μάζα', '1'], ['Φορτίο', '+1']]}
    assert lst['items'][1] == {'key': 'νετρόνιο', 'fields': [['Μάζα', '1']]}          # an empty value says nothing
    assert tables.markdown(lst).startswith('Στοιχείο:\n- πρωτόνιο:\n  Μάζα: 1\n  Φορτίο: +1\n- νετρόνιο:')
    b2 = _keyed('rows', 2)                                                               # any column can be the key
    assert tables.as_list(b2, ELEMENTS)['items'][0] == {'key': '+1', 'fields': [['Στοιχείο', 'πρωτόνιο'], ['Μάζα', '1']]}


def test_per_column_the_key_row_names_each_item_and_the_header_column_labels_the_values():
    lst = tables.as_list(_keyed('cols', 0), ELEMENTS)
    assert lst['label'] == 'Στοιχείο'
    assert lst['items'][0] == {'key': 'Μάζα', 'fields': [['πρωτόνιο', '1'], ['νετρόνιο', '1'], ['ηλεκτρόνιο', '1/1836']]}
    assert tables.axes(ELEMENTS) == {'rows': ['Στοιχείο', 'Μάζα', 'Φορτίο'], 'cols': ['Στοιχείο', 'πρωτόνιο', 'νετρόνιο', 'ηλεκτρόνιο']}


def test_spanned_headers_are_repeated_and_every_piece_reaches_the_outputs():
    html = ('<table><tr><th rowspan="2">Ισότοπο</th><th colspan="2">Πυρήνας</th></tr><tr><th>Z</th><th>N</th></tr>'
            '<tr><td>¹²C</td><td>6</td><td>6</td></tr><tr><td>¹⁴C</td><td>6</td><td>8</td></tr></table>')
    lst = tables.as_list(_keyed('rows', 0, html), html)
    assert lst['label'] == 'Ισότοπο' and lst['items'][1] == {'key': '¹⁴C', 'fields': [['Πυρήνας / Z', '6'], ['Πυρήνας / N', '8']]}
    b = _keyed('rows', 0)
    c = {'source': {'sha256': 'abc'}, 'blocks': [b], 'groups': [], 'entities': [], 'excursus': []}
    md = rag.markdown(c, profiles.effective('rag'))
    assert '- ηλεκτρόνιο:\n  Μάζα: 1/1836\n  Φορτίο: −1' in md and '|' not in md
    assert '<ul class="pv-kv">' in rag.html(c, profiles.effective('rag'))


def test_the_key_is_validated_and_a_key_lost_by_a_correction_falls_back_to_the_flat_list():
    b = _table(ELEMENTS, ink=False)
    with pytest.raises(overlay.CommandError):
        overlay.decision_event('set_table_layout', b, {'layout': 'rows', 'key': 3}, None)
    b = _keyed('rows', 2)
    small = '<table><tr><td>α</td><td>β</td></tr><tr><td>γ</td><td>δ</td></tr></table>'
    assert tables.as_list(b, small)['items'] == ['α', 'β', 'γ', 'δ']                    # nothing lost
    overlay.COMMANDS['set_table_layout'](b, {'layout': 'table'}, None)
    assert b['table_key'] is None and tables.as_list(b, ELEMENTS) is None


def test_a_title_row_across_the_table_is_the_lists_title_not_a_key():
    html = ('<table><tr><th colspan="2">ΠΙΝΑΚΑΣ 6-2 Ένζυμα</th></tr><tr><th>Ένζυμο</th><th>Αντίδραση</th></tr>'
            '<tr><td>λιπάση</td><td>υδρόλυση λιπών</td></tr><tr><td>αμυλάση</td><td>υδρόλυση αμύλου</td></tr></table>')
    lst = tables.as_list(_keyed('rows', 0, html), html)
    assert lst['title'] == 'ΠΙΝΑΚΑΣ 6-2 Ένζυμα' and lst['label'] == 'Ένζυμο'
    assert lst['items'][0] == {'key': 'λιπάση', 'fields': [['Αντίδραση', 'υδρόλυση λιπών']]}
    assert tables.markdown(lst).startswith('ΠΙΝΑΚΑΣ 6-2 Ένζυμα\nΈνζυμο:\n- λιπάση:')
    per_col = tables.as_list(_keyed('cols', 0, html), html)
    assert per_col['title'] == 'ΠΙΝΑΚΑΣ 6-2 Ένζυμα' and per_col['items'] == [
        {'key': 'Αντίδραση', 'fields': [['λιπάση', 'υδρόλυση λιπών'], ['αμυλάση', 'υδρόλυση αμύλου']]}]
    assert tables.axes(html)['cols'] == ['Ένζυμο', 'λιπάση', 'αμυλάση']
