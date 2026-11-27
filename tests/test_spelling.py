"""Spelling proposals (pipeline.spelling): evidence, strength, what is never checked, applying fixes."""
from scientific_ocr.core.model import new_block
from scientific_ocr.pipeline import spelling


class FakeLexicon:
    name = 'fake'

    def __init__(self, words):
        self.words = set(words)

    def known(self, w):
        return w in self.words


def _b(i, text, other=None):
    b = new_block(f'p1_{i}', 1, 'paragraph', [0, 0, 1, 1], source='marker',
                  chosen={'type': 'text', 'text': text, 'engine': 'marker'},
                  candidates=[{'engine': 'marker', 'text': text}] + ([{'engine': 'docling', 'text': other}] if other else []))
    b['reading_order'] = i
    return b


LEX = FakeLexicon({'ονόμασε', 'κύτταρα', 'πρωτεΐνες', 'είναι', 'ζωντανά', 'τους', 'οποίους', 'Hooke', 'cells', 'παρά'})


def test_engine_reading_confirmed_by_the_lexicon_is_a_strong_proposal():
    blocks = [_b(1, 'Ο Hooke τους οποίους σνόμασε cells', 'Ο Hooke τους οποίους ονόμασε cells')]
    s = spelling.check(blocks, LEX)
    p = blocks[0]['spelling'][0]
    assert p['word'] == 'σνόμασε' and p['candidates'][0]['text'] == 'ονόμασε' and p['strength'] == 'strong'
    assert {'engine', 'lexicon'} <= set(p['candidates'][0]['why']) and s['strong'] == 1


def test_accent_variant_and_mixed_script_and_micro_sign():
    blocks = [_b(1, 'Οι πρωτεΐνες είναι πρωτεϊνες και пара'), _b(2, 'τα κύτταρα παρά'),
              _b(3, 'νεοσυντεθειμένες', 'νεοσυντεθειµένες')]       # micro sign in the other engine: no proposal
    spelling.check(blocks, LEX)
    words = {p['word']: p for p in blocks[0]['spelling']}
    assert words['πρωτεϊνες']['candidates'][0]['text'] == 'πρωτεΐνες' and 'accent' in words['πρωτεϊνες']['candidates'][0]['why']
    assert words['пара']['candidates'][0]['text'] == 'παρά' and 'script' in words['пара']['candidates'][0]['why']
    assert not blocks[2].get('spelling')


def test_book_terms_capitals_inflections_formulas_and_decided_text_are_left_alone():
    term = 'λυσοσωμάτια'
    blocks = [_b(i, f'Τα {term} ζωντανά') for i in range(1, 4)]          # the book's own term (3x), unknown to LEX
    blocks += [_b(4, 'ΚΥΤΤΑΡΑ και $\\alpha \\beta$ ζωντανά'), _b(5, 'λυσοσωμάτιου')]   # capitals, formula, inflection
    decided = _b(6, 'σνόμασε', 'ονόμασε')
    decided['decisions'] = [{'seq': 1, 'action': 'replace_text'}]
    spelling.check(blocks + [decided], LEX)
    assert not any(b.get('spelling') for b in blocks + [decided])


def test_dismissed_proposals_stay_dismissed_and_fixes_apply_right_to_left():
    b = _b(1, 'Ο Hooke σνόμασε cells', 'Ο Hooke ονόμασε cells')
    spelling.check([b], LEX)
    key = b['spelling'][0]['key']
    spelling.check([b], LEX, {key})
    assert not b.get('spelling')
    assert spelling.apply_fixes('ab cd ef', [(0, 2, 'ab', 'AB'), (6, 8, 'ef', 'EF')]) == 'AB cd EF'
    assert spelling.apply_fixes('ab cd', [(0, 2, 'xx', 'AB')]) is None


def test_lexicon_answers_are_memoised_on_disk_and_the_dictionary_is_read_only_for_new_words(tmp_path):
    """Every rebuild is a new process and a Greek dictionary takes seconds to read: answers persist (keyed by the
    exact dictionary files), and the dictionary is opened only when a word was never asked before."""
    from scientific_ocr.adapters.lexicon import HunspellLexicon

    class Dict:
        loads = 0

        def lookup(self, w):
            return w == 'κύτταρο'

    memo = tmp_path / 'memo' / 'sig.json'
    a = HunspellLexicon([], memo)
    a._dictionaries = lambda: (setattr(Dict, 'loads', Dict.loads + 1), [Dict()])[1]
    assert a.known('κύτταρο') and not a.known('κυτταρο') and Dict.loads == 2
    a.save()
    b = HunspellLexicon([], memo)
    b._dictionaries = lambda: (_ for _ in ()).throw(AssertionError('dictionary read for a memoised word'))
    assert b.known('κύτταρο') and not b.known('κυτταρο')
