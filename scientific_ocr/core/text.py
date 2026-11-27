"""Text normalisation for COMPARISON only. Nothing here ever changes the text shown as content."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

# Cross-script lookalikes (Latin/Greek/Cyrillic). Folding them makes Α≡A, Ο≡O, Н≡H; digits stay distinct (O≠0).
_CONFUSABLE_GROUPS = ['AΑА', 'BΒВ', 'CСϹ', 'EΕЕ', 'HΗН', 'IΙІӀ', 'JЈ', 'KΚК', 'MΜМ', 'NΝ', 'OΟО', 'PΡР', 'SЅ',
                      'TΤТ', 'XΧХ', 'YΥҮ', 'ZΖ', 'aа', 'cс', 'eе', 'oοо', 'pр', 'xх', 'yу', 'iі']
CONFUSABLE = {ch: g[0] for g in _CONFUSABLE_GROUPS for ch in g}


# Transliteration pairs (Greek/Cyrillic -> Latin): the pattern of an engine "translating" a symbol (G -> Γ).
TRANSLIT = {'Α': 'A', 'Β': 'BV', 'Γ': 'G', 'Δ': 'D', 'Ε': 'E', 'Ζ': 'Z', 'Η': 'HI', 'Θ': '', 'Ι': 'I', 'Κ': 'KC',
            'Λ': 'L', 'Μ': 'M', 'Ν': 'N', 'Ξ': 'X', 'Ο': 'O', 'Π': 'P', 'Ρ': 'RP', 'Σ': 'S', 'Τ': 'T', 'Υ': 'YU',
            'Φ': 'F', 'Χ': 'XC', 'Ψ': '', 'Ω': 'OW',
            'А': 'A', 'Б': 'B', 'В': 'VB', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'З': 'Z', 'И': 'I', 'К': 'KC', 'Л': 'L',
            'М': 'M', 'Н': 'NH', 'О': 'O', 'П': 'P', 'Р': 'RP', 'С': 'SC', 'Т': 'T', 'У': 'UY', 'Ф': 'F', 'Х': 'XH'}


# Lookalike letters mapped INTO one script: a word read with letters of two scripts ('ρΗ', 'τοµές' with the micro
# sign, Cyrillic 'парагόνται') becomes a candidate in each script (pipeline.spelling).
TO_GREEK = {**dict(zip('ABEHIKMNOPTXYZaoρpvkiuxtynwµ', 'ΑΒΕΗΙΚΜΝΟΡΤΧΥΖαορρνκιυχτγηωμ')),
            **dict(zip('АВЕНКМОРТХаорпгктхи', 'ΑΒΕΗΚΜΟΡΤΧαορπγκτχυ'))}
TO_LATIN = {**dict(zip('ΑΒΕΗΙΚΜΝΟΡΤΧΥΖορνκαιυχ', 'ABEHIKMNOPTXYZopvkaiux')),
            **dict(zip('АВЕНКМОРТХаорсху', 'ABEHKMOPTXaopcxy'))}


def letter_scripts(word: str) -> set[str]:
    """Scripts of the LETTERS of a word (GREEK, LATIN, CYRILLIC; the micro sign counts as a lookalike)."""
    out = set()
    for ch in word:
        if ch == 'µ':
            out.add('MICRO')
        elif ch.isalpha():
            out.add(script_of(ch))
    return out


def strip_marks(s: str) -> str:
    """Remove diacritics (polytonic/monotonic Greek, accents) for comparison."""
    return ''.join(c for c in unicodedata.normalize('NFD', s) if not unicodedata.combining(c))


def fold(s: str) -> str:
    return ''.join(CONFUSABLE.get(c, c) for c in strip_marks(s))


def transliteration_pair(a: str, b: str) -> bool:
    a, b = strip_marks(a).upper(), strip_marks(b).upper()
    return (b and b in TRANSLIT.get(a, '')) or (a and a in TRANSLIT.get(b, '')) or False


def script_of(ch: str) -> str:
    try:
        return unicodedata.name(ch).split(' ')[0]
    except ValueError:
        return 'UNKNOWN'


# Formula spans ($...$, $$...$$) in engine text; they are never touched by markup handling.
MATH_SPAN = re.compile(r'((?<!\\)\$\$.+?(?<!\\)\$\$|(?<!\\)\$[^$\n]+?(?<!\\)\$)', re.S)
_MARKUP = re.compile(r'\*+|^#+\s*|`|<[^>]+>', re.M)


def strip_markup(t: str) -> str:
    """Engine text without Markdown emphasis/heading marks and HTML tags; formula spans kept verbatim; never
    removes '_' (LaTeX subscripts); whitespace collapsed."""
    parts = MATH_SPAN.split(t or '')
    return re.sub(r'\s+', ' ', ''.join(p if i % 2 else _MARKUP.sub(' ', p) for i, p in enumerate(parts))).strip()


def label_key(t: str) -> str:
    """Comparison key for labels and titles: no markup, no accents, case-folded (final sigma becomes σ)."""
    return ' '.join(strip_marks(strip_markup(t)).casefold().split())


def plain_text(s: str) -> str:
    s = re.sub(r'<[^>]+>', '', s or '')
    s = re.sub(r'[*$]', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def symbol_plain(s: str) -> str:
    """Engine text -> plain text without HTML/markdown/LaTeX markup, keeping every symbol."""
    s = re.sub(r'<[^>]+>', '', s or '')
    s = re.sub(r'\\(?:text|mathrm|mathbf|mathit|rm|ce|operatorname)\s*', '', s)
    s = re.sub(r'\\[A-Za-z]+', ' ', s)
    s = re.sub(r'[{}$*_^~]', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def tokens(s: str) -> list[str]:
    return re.findall(r'\w+|[^\w\s]', symbol_plain(s))


def match_norm(s: str) -> str:
    """For similarity scores only; lookalikes deliberately NOT folded (a script swap lowers similarity)."""
    return ' '.join(unicodedata.normalize('NFC', plain_text(s)).casefold().split())


def similarity(a: str, b: str) -> float:
    a, b = match_norm(a), match_norm(b)
    return fuzz.ratio(a, b) / 100.0 if a and b else 0.0


def sentence_around(text: str, start: int, end: int) -> str:
    s0 = max(text.rfind('.', 0, start), text.rfind('\n', 0, start)) + 1
    ends = [i for i in (text.find('.', end), text.find('\n', end)) if i >= 0]
    return text[s0:min(ends) if ends else len(text)]
