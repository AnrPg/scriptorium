"""Stage (document level, after decisions): SPELLING PROPOSALS — OCR word errors («σνόμασε», «μποχόνδρια»,
«Κυταρικής», «ρΗ», «τοµές» with a micro sign), which hurt retrieval more than any formatting (OHRBench, ICCV 2025).

Nothing is corrected here: each suspect word gets candidates with their EVIDENCE, the reviewer accepts them (one by
one or all strong ones at once) as ordinary replace_text decisions. Evidence, independent of each other:
  lexicon   the spelling dictionaries of the book's languages (ports.Lexicon; optional)
  engine    the SECOND engine read this word differently at the same place (a near word in its candidate text)
  book      the book itself uses the candidate repeatedly (its own terms: «λυσοσωμάτια» is in no dictionary)
  script    the word mixes lookalike letters of two scripts; the candidate is the same word in one script
  accent    the candidate differs only in accents/diaeresis («πρωτεϊνες» → «πρωτεΐνες»)
A word is SUSPECT when no evidence supports it as written: unknown to the lexicon (or no lexicon) and rare in the
book (< BOOK_TRUST occurrences), or mixing scripts. A proposal is STRONG when two kinds of evidence agree on the
candidate (e.g. engine + lexicon) or it is an accent/script variant the lexicon knows; else WEAK.
Words inside formulas, with digits, shorter than MIN_LEN, and the reviewer's own text (a value decision in force)
are never checked; dismissed proposals (suggestion_dismiss 'sp:…') are remembered.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import re
import unicodedata
from collections import Counter, defaultdict

from ..core.model import in_reader_output
from ..core.text import MATH_SPAN, TO_GREEK, TO_LATIN, letter_scripts, strip_marks

WORD = re.compile(r"[^\W\d_]+", re.U)
MIN_LEN = 4
BOOK_TRUST = 3                      # a form the book uses this often is the book's own spelling
MAX_PROPOSALS_PER_BLOCK = 20


def _key(w: str) -> str:
    return unicodedata.normalize('NFC', w).casefold().replace('ς', 'σ')


def _bare(w: str) -> str:
    return strip_marks(_key(w))


def _deletes(w: str) -> set[str]:
    return {w[:i] + w[i + 1:] for i in range(len(w))}


def _distance(a: str, b: str, cap: int = 3) -> int:
    """Damerau-Levenshtein (adjacent transpositions), stopped early above cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > cap:
            return cap + 1
        prev2, prev = prev, cur
    return prev[-1]


def _match_case(cand: str, word: str) -> str:
    if word.isupper() and len(word) > 1:
        return cand.upper()
    return (cand[:1].upper() if word[:1].isupper() else cand[:1].lower()) + cand[1:]


def _plain_spans(text: str):
    """(offset, text) of the parts outside formula spans."""
    pos = 0
    for i, part in enumerate(MATH_SPAN.split(text)):
        if not i % 2:
            yield pos, part
        pos += len(part)


class _Vocabulary:
    def __init__(self, texts: list[str]):
        self.freq: Counter = Counter()
        self.forms: dict[str, Counter] = defaultdict(Counter)       # case-folded -> written forms
        for t in texts:
            for _, part in _plain_spans(t):
                for m in WORD.finditer(part):
                    w = m.group(0)
                    self.freq[_key(w)] += 1
                    self.forms[_key(w)][w] += 1
        self.by_bare: dict[str, list[str]] = defaultdict(list)
        self.index: dict[str, set[str]] = defaultdict(set)          # deletion index of the book's trusted words
        for k, n in self.freq.items():
            self.by_bare[strip_marks(k)].append(k)
            if n >= BOOK_TRUST and len(k) >= MIN_LEN:
                bare = strip_marks(k)
                self.index[bare].add(k)
                for d in _deletes(bare):
                    self.index[d].add(k)

    def near(self, word: str) -> set[str]:
        """Trusted book words within edit distance 1 (2 for words of 8+ letters), accents ignored."""
        bare = _bare(word)
        cap = 2 if len(bare) >= 8 else 1
        out = set(self.index.get(bare, ()))
        for d in _deletes(bare):
            out |= self.index.get(d, set())
        return {k for k in out if _distance(strip_marks(k), bare, cap) <= cap}


def _greekify(w: str) -> str:
    """Engine text uses the micro sign for μ and Latin lookalikes inside Greek words: one script per word."""
    sc = letter_scripts(w)
    return ''.join(TO_GREEK.get(ch, ch) for ch in w) if 'GREEK' in sc and len(sc) > 1 else w


def _aligned_engine_words(text: str, b: dict) -> dict[int, str]:
    """Start offset of a word of the chosen text -> the word the OTHER engine read at the same place (difflib
    alignment of the two word sequences; only 1:1 replacements inside matching context)."""
    chosen_engine = (b['chosen'] or {}).get('engine')
    other = ' '.join(re.sub(r'<[^>]+>', ' ', c['text']) for c in b.get('candidates') or []
                     if c.get('engine') != chosen_engine and isinstance(c.get('text'), str))
    if not other:
        return {}
    mine = [(off + m.start(), m.group(0)) for off, part in _plain_spans(text) for m in WORD.finditer(part)]
    theirs = [_greekify(m.group(0)) for m in WORD.finditer(other)]
    sm = difflib.SequenceMatcher(None, [_bare(w) for _, w in mine], [_bare(w) for w in theirs], autojunk=False)
    out = {}
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'replace' and i2 - i1 == j2 - j1:
            for k in range(i2 - i1):
                out[mine[i1 + k][0]] = theirs[j1 + k]
    return out


def _inflection(a: str, b: str) -> bool:
    """The two words differ only in their last 3 letters (an ending, not a misread letter inside the stem)."""
    a, b = _bare(a), _bare(b)
    common = len(os.path.commonprefix([a, b]))
    return common >= max(len(a), len(b)) - 3


def _script_variants(word: str) -> list[str]:
    out = []
    for table in (TO_GREEK, TO_LATIN):
        conv = ''.join(table.get(ch, ch) for ch in word)
        if conv != word and len(letter_scripts(conv) - {'MICRO'}) == 1 and 'MICRO' not in letter_scripts(conv):
            out.append(conv)
    return out


def _strong(word: str, cand: str, why: set[str]) -> bool:
    """Two independent kinds of evidence: the other engine's reading confirmed by the lexicon or the book; an
    accent/script variant the lexicon knows; or one letter off a word that both the lexicon and the book use
    (words of 6+ letters: a short word one letter off another is too often a different word)."""
    if 'engine' in why and why & {'lexicon', 'book'}:
        return True
    if why & {'accent', 'script'} and 'lexicon' in why:
        return True
    return {'book', 'lexicon'} <= why and len(word) >= 6 and _distance(_bare(word), _bare(cand), 1) <= 1


def _proposal_key(block_id: str, start: int, word: str) -> str:
    return 'sp:' + hashlib.sha1(f'{block_id}|{start}|{word}'.encode()).hexdigest()[:12]


def check(blocks: list[dict], lexicon=None, dismissed: frozenset | set = frozenset()) -> dict:
    """Mutates blocks: b['spelling'] = [{key, word, start, end, candidates: [{text, why}], strength}]. Returns a summary."""
    texts = [b for b in blocks if in_reader_output(b) and b.get('flow') != 'furniture'
             and (b['chosen'] or {}).get('type') == 'text' and (b['chosen'].get('text') or '').strip()]
    for b in blocks:
        b.pop('spelling', None)
    vocab = _Vocabulary([b['chosen']['text'] for b in texts])
    known = (lambda w: lexicon.known(w)) if lexicon is not None else (lambda w: False)

    def supported(word: str) -> bool:
        if known(word) or vocab.freq[_key(word)] >= BOOK_TRUST or (word[:1].isupper() and known(word.lower())):
            return True
        if word.isupper():                               # capitals carry no accents: any accented form counts
            return any(known(f) or vocab.freq[k] >= BOOK_TRUST for k in vocab.by_bare.get(_bare(word), ())
                       for f in vocab.forms[k])
        return False

    def good(cand: str) -> list[str]:
        why = []
        if known(cand) or known(cand.lower()):
            why.append('lexicon')
        if vocab.freq[_key(cand)] >= 2:
            why.append('book')
        return why

    n_blocks = n_words = n_strong = 0
    for b in texts:
        if any(d['action'] in ('replace_text', 'approve_candidate') for d in b.get('decisions') or []):
            continue                                     # the reviewer's (or chosen) text: not second-guessed
        text, found = b['chosen']['text'], []
        engine = _aligned_engine_words(text, b)
        for off, part in _plain_spans(text):
            for m in WORD.finditer(part):
                w = m.group(0)
                start = off + m.start()
                if len(w) < MIN_LEN or (start > 0 and text[start - 1] in '\\'):
                    continue                             # short words and LaTeX commands are never checked
                scripts = letter_scripts(w)
                mixed = len(scripts) > 1
                if not mixed and supported(w):
                    continue
                key = _proposal_key(b['id'], start, w)
                if key in dismissed:
                    continue
                cands: dict[str, list[str]] = {}

                def add(c: str, *why: str):
                    c = _match_case(c, w) if not mixed else c
                    if c != w and len(c) >= 2 and len(letter_scripts(c)) == 1:
                        cands.setdefault(c, [])
                        cands[c] += [x for x in dict.fromkeys(why) if x not in cands[c]]
                for v in _script_variants(w):
                    if good(v) or mixed:
                        add(v, 'script', *good(v))
                    for k in vocab.by_bare.get(_bare(v), ()):          # the converted word, accents restored
                        for written in vocab.forms[k]:
                            if good(written):
                                add(written, 'script', 'accent', *good(written))
                if not w.isupper():                      # (capitals carry no accents)
                    for form in vocab.by_bare.get(_bare(w), ()):
                        for written in vocab.forms[form]:
                            if written != w and not written.isupper() and good(written):
                                add(written, 'accent', *good(written))
                e = engine.get(start)
                if e and e != w and _distance(_bare(e), _bare(w), 2) <= 2 and good(e):
                    add(e, 'engine', *good(e))
                for k in vocab.near(w):
                    written = vocab.forms[k].most_common(1)[0][0]
                    if k != _key(w) and not _inflection(written, w):     # an ending is grammar, not a misreading
                        add(written, 'book', *good(written))
                if not cands:
                    continue
                ranked = sorted(cands.items(), key=lambda kv: (-len(set(kv[1])), -vocab.freq[_key(kv[0])], kv[0]))[:3]
                strong = _strong(w, ranked[0][0], set(ranked[0][1]))
                found.append({'key': key, 'word': w, 'start': start, 'end': start + len(w),
                              'candidates': [{'text': c, 'why': why} for c, why in ranked],
                              'strength': 'strong' if strong else 'weak'})
                n_strong += strong
                if len(found) >= MAX_PROPOSALS_PER_BLOCK:
                    break
        if found:
            b['spelling'] = found
            n_blocks += 1
            n_words += len(found)
    return {'blocks': n_blocks, 'words': n_words, 'strong': n_strong, 'lexicon': getattr(lexicon, 'name', None)}


def apply_fixes(text: str, fixes: list[tuple[int, int, str, str]]) -> str | None:
    """Replace (start, end, word, new) spans in text, right to left; None if a span no longer holds its word."""
    out = text
    for start, end, word, new in sorted(fixes, key=lambda f: -f[0]):
        if out[start:end] != word:
            return None
        out = out[:start] + new + out[end:]
    return out
