"""Script- and language-independent checks on scientific symbols.

Root cause (observed on real pages): the primary OCR is a language model that "normalises" isolated symbols
toward the page language (G->Γ, C->Κ, O->0, ⇌->→) or invents text. Text alone cannot tell which reading is
right; an INDEPENDENT reading of the same pixels can. Severity therefore comes from cross-engine disagreement
on symbol-like tokens (after folding visually identical letters), not from guesses about the book's language.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .text import (
    CONFUSABLE,
    fold,
    script_of,
    symbol_plain,
    tokens,
    transliteration_pair,
)

_FORMULA = re.compile(r'^(?:[A-Z][a-z]?\d*)+[+\-−]?$')


def symbol_like(tok: str) -> bool:
    """Uppercase single letters (bases, labels, variables), tokens with digits, or formula-shaped tokens.
    Lowercase single letters are elisions/articles in most languages (σ', ν', a) and are not symbols here."""
    return (len(tok) == 1 and tok.isalpha() and tok.isupper()) or any(c.isdigit() for c in tok) or \
        (len(tok) <= 8 and bool(_FORMULA.match(fold(tok))) and any(c.isupper() for c in tok))


# Digit/letter confusions that change meaning (I vs l is excluded: visually identical, the image cannot decide).
_DIGIT_LETTER = {frozenset(p) for p in ('O0', 'l1', 'I1', 'S5', 'B8', 'Z2', 'G6', 'g9')}


# Classes of disagreement between two readings of the same token.
#   critical (escalate to review): one engine's symbol CONTRADICTS the other's, and the difference changes meaning
CRITICAL_CLASSES = {
    'script_swap': 'same letter in another alphabet (G↔Γ, C↔Κ): one engine "translated" a symbol',
    'digit_letter': 'digit vs letter inside a formula (O↔0, l↔1): changes the chemical/maths meaning',
    'number': 'different digits of the same length (2↔3): coefficients, subscripts, quantities',
    # Reviewer policy: one engine reading LESS (a dropped subscript, cut digits) is critical too. Either the
    # second engine missed a character, or the first invented one; text alone cannot tell which.
    'partial_reading': 'one engine read only part of the token (missing subscript/superscript, cut digits)',
}
#   minor (recorded as evidence, no escalation): the secondary reading is LESS, not DIFFERENT, or is unrelated noise
MINOR_CLASSES = {
    'lookalike': 'visually identical glyphs from different alphabets or accents (Α/A, Η/Ἡ)',
    'unrelated': 'readings share nothing plausible (Γ vs 6): a misread by the weaker engine, no symbol evidence',
}


def conflict_class(x: str, y: str) -> str | None:
    """Classify a disagreement (None = identical readings)."""
    if x == y:
        return None
    fx, fy = fold(x), fold(y)
    if fx == fy:
        return 'lookalike'
    if fx.startswith(fy) or fy.startswith(fx) or fx.endswith(fy) or fy.endswith(fx):
        return 'partial_reading'
    if re.sub(r'\d', '', fx) == re.sub(r'\d', '', fy) and len(fx) != len(fy):
        return 'partial_reading'
    if len(x) == 1 and len(y) == 1:
        if x.isalpha() and y.isalpha() and script_of(x) != script_of(y) and transliteration_pair(x, y):
            return 'script_swap'
        return 'number' if x.isdigit() and y.isdigit() else 'unrelated'
    if len(fx) != len(fy):
        return 'unrelated'
    diffs = [(a, b) for a, b in zip(fx, fy) if a != b]
    if all(frozenset((a, b)) in _DIGIT_LETTER for a, b in diffs) and _FORMULA.match(fx.replace('0', 'O')):
        return 'digit_letter'
    if all(a.isdigit() and b.isdigit() for a, b in diffs):
        return 'number'
    if all(a.isalpha() and b.isalpha() and script_of(a) != script_of(b) and transliteration_pair(a, b)
           for a, b in diffs):
        return 'script_swap'
    return 'unrelated'


CONTEXT_TOKENS = 12          # words shown on each side of a disagreement


def token_conflicts(primary: str, secondary: str, max_span: int = 3) -> list[dict]:
    """All disagreements on symbol-like tokens between two engines, each with its class and whether it is
    critical. Only short replace spans are compared, so a garbled phrase is not misread as many symbol errors.
    Each conflict carries the words before/after it and which occurrence of the token it is, so a reviewer UI can
    find and highlight the exact spot in the text being edited."""
    a, b = tokens(primary), tokens(secondary)
    out = []
    ops = SequenceMatcher(None, [fold(t) for t in a], [fold(t) for t in b], autojunk=False).get_opcodes()
    for op, i1, i2, j1, j2 in ops:
        if op not in ('replace', 'equal') or (op == 'replace' and (i2 - i1 > max_span or j2 - j1 > max_span)):
            continue
        for k, (x, y) in enumerate(zip(a[i1:i2], b[j1:j2])):
            if not (symbol_like(x) and symbol_like(y)):     # both readings must look like symbols:
                continue                                     # "τα" vs "Ta" is a word, not tantalum
            cls = conflict_class(x, y)
            if cls:
                i = i1 + k
                out.append({'primary': x, 'secondary': y, 'class': cls, 'critical': cls in CRITICAL_CLASSES,
                            'context': ' '.join(a[max(0, i - CONTEXT_TOKENS):i + CONTEXT_TOKENS + 1]),
                            'before': ' '.join(a[max(0, i - CONTEXT_TOKENS):i]), 'after': ' '.join(a[i + 1:i + CONTEXT_TOKENS + 1]),
                            'occurrence': sum(1 for t in a[:i] if t == x)})   # which occurrence of x in the text
    return out


_SINGLE = re.compile(r'(?<![\w\-])(\w)(?![\w\'′])')
# Structural labels in any script: "(Γ)", "Γ. " at line/sentence start, a letter glued to a number "5-2Γ".
_LABEL = re.compile(r'\((\w)\)|(?:^|(?<=[.;:]\s))(\w)\.(?=\s)|(?<=\d)(\w)(?!\w)', re.M)


def script_mix(text: str) -> dict | None:
    """Fallback when no independent reading exists: isolated UPPERCASE letters from several scripts where one
    has no lookalike in the others (Γ beside G). Lowercase symbols (orbitals, states, variables) are ignored."""
    t = re.sub(r'[*_$]', ' ', symbol_plain(text))
    labels = sorted({a or b or c for a, b, c in _LABEL.findall(t) if (a or b or c).isalpha()})
    t = _LABEL.sub(' ', t)
    letters = [x for x in (m.group(1) for m in _SINGLE.finditer(t)) if x.isalpha()]
    upper = {x for x in letters if x.isupper()}
    distinctive = sorted(x for x in upper if x not in CONFUSABLE) if len({script_of(x) for x in upper}) > 1 else []
    mixed_labels = len({script_of(x) for x in labels}) > 1 and any(x not in CONFUSABLE for x in labels)
    if not letters and not labels:
        return None
    scripts: dict[str, list] = {}
    for x in sorted(set(letters)):
        scripts.setdefault(script_of(x), []).append(x)
    return {'scripts': scripts, 'distinctive': distinctive, 'labels': labels,
            'severe': bool(distinctive) or mixed_labels}


def glyph_issues(latex_or_text: str) -> list[str]:
    """Classic OCR confusions in scientific notation (advisory, never auto-corrected)."""
    s = latex_or_text or ''
    issues = []
    if re.search(r'(?<![A-Za-z0-9])(?:P|C|S|N|H)\s*0(?:\s*_|\d|\b)', s):
        issues.append('DIGIT_ZERO_WHERE_OXYGEN_LIKELY')
    letters = {script_of(c) for c in re.sub(r'\\[A-Za-z]+', '', s) if c.isalpha()}
    if len(letters) > 1:
        issues.append('MIXED_SCRIPT_IN_NOTATION')
    if s.count('{') != s.count('}'):
        issues.append('UNBALANCED_BRACES')
    return issues


_DELIMS = (('$$', '$$'), ('\\[', '\\]'), ('\\(', '\\)'), ('$', '$'))


def strip_math_delimiters(s: str) -> str:
    """Canonical LaTeX carries no math delimiters; renderers add their own. Only a matching pair enclosing the
    whole string is removed ($$..$$, \\[..\\], \\(..\\), $..$), never a lone or escaped dollar."""
    t = (s or '').strip()
    for o, c in _DELIMS:
        if len(t) > len(o) + len(c) and t.startswith(o) and t.endswith(c) and not t.endswith('\\' + c):
            inner = t[len(o):-len(c)]
            # one pair around EVERYTHING only: '$x$ and $y$' is two formulas with text between, not '$…$'
            if re.search(r'(?<!\\)' + re.escape(c if c != '$$' else '$'), inner) and o in ('$', '$$'):
                return s or ''
            if o != '$' and (o in inner or c in inner):
                return s or ''
            return inner.strip()
    return s or ''                      # unchanged (keeps content hashes of already-clean LaTeX stable)


def latex_norm(s: str) -> str:
    """Strict comparison key for maths: only whitespace, spacing macros and \\displaystyle are ignored."""
    s = (s or '').replace('\\displaystyle', '')
    s = re.sub(r'\\(?:,|;|:|!|quad|qquad)', ' ', s)
    return re.sub(r'\s+', '', s)
