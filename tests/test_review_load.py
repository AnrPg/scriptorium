"""Review-load reduction: background fills, empty fragments and duplicate readings, boxes found from their title
bars, collective sections bounded by their items, the Marker-primary engine policy, caption continuations that
are body text, and course text continuing past a box label."""
from scientific_ocr.core import profiles
from scientific_ocr.core.model import in_output, new_block
from scientific_ocr.pipeline import captions, entities, fills, flow, profile


def _b(i, kind, text, bbox, page=1, source='marker', **extra):
    chosen = {'type': 'text', 'text': text} if text is not None else None
    b = new_block(f'p{page}_{i}', page, kind, bbox, source=source, chosen=chosen, **extra)
    b['reading_order'] = i
    return b


DIMS = {1: (600, 800)}


def test_background_fill_needs_a_band_and_text_read_on_it():
    label = _b(1, 'heading', 'ΕΡΩΤΗΣΗ 1-2', [40, 60, 130, 80])
    bar = _b(2, 'excerpt', None, [132, 55, 208, 90], source='coverage', ink_band={'band': 0.5, 'band_y': [55, 82]})
    lone = _b(3, 'excerpt', None, [300, 400, 400, 440], source='coverage', ink_band={'band': 0.6, 'band_y': [400, 430]})
    fixes = fills.classify([label, bar, lone], set())
    assert bar['excluded']['as'] == 'background_fill' and bar['excluded']['fill_for'] == 'p1_1' and len(fixes) == 1
    assert in_output(lone)                         # a dark band nobody read text on may hide white text: kept
    bar.pop('excluded')
    fills.classify([label, bar], {'fill:p1_2'})    # the reviewer restored it
    assert in_output(bar)
    assert fills.band_boxes([label, bar]) == {}


def test_empty_fragments_and_duplicate_readings_never_lose_text():
    pic = _b(1, 'figure', None, [100, 100, 400, 400])
    speck = _b(2, 'excerpt', None, [150, 150, 180, 170], source='docling')
    fallback = _b(3, 'excerpt', None, [0, 0, 600, 800], source='fallback')
    big = _b(4, 'paragraph', 'Τα κύτταρα διαιρούνται σε δύο θυγατρικά.', [50, 500, 550, 560])
    dup = _b(5, 'paragraph', 'σε δύο θυγατρικά', [300, 520, 540, 555], source='docling')
    other = _b(6, 'paragraph', 'Κάτι άλλο εντελώς', [60, 505, 200, 520], source='docling')
    twin_a = _b(7, 'paragraph', 'Ίδιο κείμενο', [50, 600, 300, 620])
    twin_b = _b(8, 'paragraph', 'Ίδιο κείμενο', [50, 600, 300, 620], source='docling')
    blocks = [pic, speck, fallback, big, dup, other, twin_a, twin_b]
    fills.fragments(blocks, set(), DIMS)
    assert speck['excluded']['as'] == 'empty_fragment' and in_output(fallback)
    assert dup['excluded']['as'] == 'duplicate_reading' and in_output(other) and in_output(big)
    assert in_output(twin_a) != in_output(twin_b)  # exactly one of two identical readings stays


def test_title_bar_gives_the_box_column():
    label = _b(1, 'heading', 'ΕΡΩΤΗΣΗ 1-2', [360, 60, 450, 80])
    bar = _b(2, 'excerpt', None, [452, 55, 560, 88], source='coverage', ink_band={'band': 0.5, 'band_y': [55, 82]})
    q1 = _b(3, 'paragraph', 'Οι μεταλλάξεις είναι λάθη στο DNA.', [362, 90, 558, 150])
    q2 = _b(4, 'paragraph', 'Δικαιολογήστε την απάντησή σας.', [362, 155, 558, 180])
    body = _b(5, 'paragraph', 'Κυρίως κείμενο στην άλλη στήλη.', [40, 95, 340, 300])
    blocks = [label, bar, q1, q2, body]
    fills.classify(blocks, set())
    res = entities.detect(blocks, [], [], DIMS, {}, set())
    q = next(e for e in res['entities'] if e['type'] == 'question')
    assert q['members'] == ['p1_1', 'p1_3', 'p1_4'] and q['granularity'] == 'box' and 'bar' in q['rule']


def test_collective_section_is_bounded_by_its_last_item():
    sec = {'key': 'S', 'type': 'question', 'granularity': 'section', 'collective': True, 'placement': 'inline',
           'members': ['h', 'a1', 'a2', 'b1', 'next_chapter_text'], 'confidence': 'high'}
    items = [{'key': 'A', 'type': 'question', 'granularity': 'box', 'members': ['a1', 'a2']},
             {'key': 'B', 'type': 'question', 'granularity': 'box', 'members': ['b1']}]
    auto = profiles.effective('rag')['auto_exclude']
    assert profile._automatic(sec, auto, [sec] + items) == ['h', 'a1', 'a2', 'b1']
    assert profile._automatic(sec, auto, [sec, items[0]]) is None        # one item is not enough evidence


def test_marker_primary_waives_disagreements_but_not_lost_text_or_equations():
    p = profiles.effective('rag')

    def blk(i, reasons, marker, docling, kind='paragraph'):
        b = _b(i, kind, marker, [0, 0, 1, 1], candidates=[{'engine': 'marker', 'text': marker}, {'engine': 'docling', 'text': docling}])
        b['reasons'], b['review'], b['display_crop'] = list(reasons), 'required', True
        return b
    typo = blk(1, ['SYMBOL_READING_CONFLICT'], 'Το H3O+ ορίζει το pH του διαλύματος', 'Το H30+ ορίζει το pH του διαλύματος')
    lost = blk(2, ['ENGINE_TEXT_CONFLICT'], 'Η μεταγραφή ξεκινά', 'Η μεταγραφή ξεκινά από τον υποκινητή και τελειώνει στον τερματιστή του γονιδίου')
    eq = blk(3, ['EQUATION_CONFLICT', 'SYMBOL_READING_CONFLICT'], 'x', 'x', kind='math')
    assert profile.review_policy([typo, lost, eq], p) == 1
    assert typo['review'] == 'not_needed' and typo['waived'] == ['SYMBOL_READING_CONFLICT'] and not typo['display_crop']
    assert lost['review'] == 'required' and 'ENGINE_TEXT_LOSS_SUSPECTED' in lost['reasons']
    assert eq['review'] == 'required'
    assert profile.review_policy([blk(4, ['SYMBOL_READING_CONFLICT'], 'a', 'b')], profiles.effective('reading')) == 0


def test_caption_continuation_never_takes_a_body_sentence():
    assert captions._mid_sentence_start('μπορεί να έχει δραματικές επιπτώσεις')
    assert not captions._mid_sentence_start('α. Το πρώτο στοιχείο') and not captions._mid_sentence_start('Πηγή: …')
    assert captions._open_end(_b(1, 'caption', 'ΕΙΚΟΝΑ 1-2 Το κύτταρο και', [0, 0, 1, 1]))
    assert not captions._open_end(_b(2, 'caption', 'ΕΙΚΟΝΑ 1-2 Το κύτταρο.', [0, 0, 1, 1]))


def test_course_text_continues_past_a_box_label_and_with_an_acronym():
    assert flow.continues('τέλος, το ένζυμο λιγκάση του', 'DNA ligase συνδέει τα άκρα')
    assert not flow.continues('τέλος της πρότασης.', 'DNA ligase συνδέει')
    assert not flow.continues('το κείμενο συνεχίζει', 'ΤΑ ΚΥΤΤΑΡΑ ΣΤΟ ΜΙΚΡΟΣΚΟΠΙΟ')
    a = _b(1, 'paragraph', 'Τα κύτταρα διαιρούνται και', [40, 100, 300, 200])
    label = _b(2, 'heading', 'ΕΡΩΤΗΣΗ 6-2', [360, 100, 550, 120], heading_level_source='labelled_element')
    b = _b(3, 'paragraph', 'παράγουν δύο θυγατρικά κύτταρα.', [40, 210, 300, 260])
    flow.annotate([a, label, b])
    assert b['relations'].get('continues_from') == 'p1_1'


def test_uncovered_ink_whose_own_ocr_reads_no_words_is_not_content():
    known = {'άτομο', 'άνθρακα', 'περιέχει', 'πρωτόνια'}.__contains__
    assert not fills.is_text('2-2') and not fills.is_text('— Ι —') and fills.is_text('άτομο άνθρακα')
    assert not fills.reads_as_text('ιο -----Ἔὂμο----', known) and fills.reads_as_text('Ένα άτομο άνθρακα περιέχει', known)
    tail = _b(1, 'excerpt', None, [200, 300, 260, 320], source='coverage', ink_text='2-2')
    words = _b(2, 'excerpt', None, [50, 600, 400, 640], source='coverage', ink_text='Ένα άτομο άνθρακα περιέχει έξι πρωτόνια')
    unread = _b(3, 'excerpt', None, [50, 700, 400, 740], source='coverage')            # no OCR evidence at all
    fills.fragments([tail, words, unread], set(), DIMS, known)
    assert tail['excluded']['as'] == 'no_text_ink' and in_output(words) and in_output(unread)


def test_an_old_image_approval_does_not_keep_a_title_bar_as_content():
    label = _b(1, 'heading', 'ΕΡΩΤΗΣΗ 2-2', [56, 258, 145, 274])
    bar = _b(2, 'excerpt', None, [146, 251, 255, 285], source='coverage', ink_band={'band': 0.65, 'band_y': [257, 279]})
    bar['decisions'] = [{'seq': 3, 'action': 'approve'}]                   # «the image is the content», asked as an error
    fills.classify([label, bar], set())
    assert bar['excluded']['as'] == 'background_fill'
    bar.pop('excluded')
    bar['decisions'] = [{'seq': 4, 'action': 'replace_text', 'value': 'ΚΕΙΜΕΝΟ'}]   # the reviewer typed text: respected
    fills.classify([label, bar], set())
    assert in_output(bar)


def test_a_pictures_title_or_label_is_not_its_caption():
    from scientific_ocr.pipeline.captions import caption_label, figure_title
    assert figure_title('ΦΩΣΦΟΛΙΠΙΔΙΑ') == 'all capitals' and figure_title('ΟΡΓΑΝΩΜΕΝΗ ΠΡΟΣΠΑΘΕΙΑ ΠΟΥ ΑΠΑΙΤΕΙ ΕΝΕΡΓΕΙΑ')
    assert figure_title('Μη διαιρούμενο κύτταρο') and figure_title('100 nm')
    for caption in ('**ΕΙΚΟΝΑ 1-4** Η ζωή', '(Β)', '(Δ) Χωροπληρωτικό μοντέλο', 'Με την άδεια του Stefan Heil.',
                    'σε φυτικά κύτταρα.', 'Παρατηρήστε ότι κάθε άτομο άνθρακα έχει έναν αριθμό.', 'ΕΙΚΟΝΑ Ε5-7', ''):
        assert figure_title(caption) is None, caption
    assert caption_label('ΕΙΚΟΝΑ Ε5-7 Πλαίσιο')[0] == 'ΕΙΚΟΝΑ Ε5-7'
    fig = _b(1, 'figure', None, [100, 100, 400, 300])
    title = _b(2, 'caption', 'ΦΩΣΦΟΛΙΠΙΔΙΑ', [100, 80, 200, 95])
    cap = _b(3, 'caption', 'ΕΙΚΟΝΑ 2-5 Τα φωσφολιπίδια.', [100, 310, 400, 330])
    captions.associate([title, fig, cap], [])
    assert title['kind'] == 'heading' and not title['relations'].get('caption_for')
    assert cap['relations'].get('caption_for') == 'p1_1' and fig['relations']['captions'] == ['p1_3']
