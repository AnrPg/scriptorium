"""Book rules: user-defined, previewed, undoable token corrections (data, never hard-coded per book).

A rule replaces an ISOLATED token (not adjacent to letters/digits), optionally only in sentences matching a
context regex, minus individually excluded occurrences ("<block_id>#<n>"). Typical use: a book prints DNA
bases or variables in a script the OCR keeps "translating". Rules apply to machine text before checks run,
are logged per block, and never override a human edit.
"""
from __future__ import annotations

import re

from .text import fold, sentence_around


class RuleError(ValueError):
    pass


def validate(rule: dict) -> dict:
    if not rule.get('id') or not isinstance(rule.get('find'), str) or not rule['find'].strip():
        raise RuleError('rule needs id and non-empty find')
    if not isinstance(rule.get('replace'), str):
        raise RuleError('rule needs replace')
    if rule.get('context'):
        try:
            re.compile(rule['context'])
        except re.error as exc:
            raise RuleError(f'invalid context regex: {exc}')
    return {'id': rule['id'], 'find': rule['find'], 'replace': rule['replace'],
            'context': rule.get('context') or '', 'exclude': list(rule.get('exclude') or []),
            'note': rule.get('note') or ''}


def matches(text: str, rule: dict) -> list[dict]:
    text = text or ''
    ctx = re.compile(rule['context'], re.I) if rule.get('context') else None
    out = []
    for n, m in enumerate(re.finditer(r'(?<!\w)' + re.escape(rule['find']) + r'(?!\w)', text)):
        if ctx and not ctx.search(sentence_around(text, m.start(), m.end())):
            continue
        out.append({'n': n, 'start': m.start(), 'end': m.end(),
                    'context': text[max(0, m.start() - 40):m.end() + 40]})
    return out


def _confirmed_by_second_reading(text: str, second: str, rule: dict) -> set[int]:
    """Start offsets of `find` tokens that an independent engine ALSO read as `find` (aligned position).
    Evidence that the page really prints that glyph there, so the rule must not 'correct' it."""
    from difflib import SequenceMatcher
    a = [(m.group(0), m.start()) for m in re.finditer(r'\w+|[^\w\s]', text)]
    b = [m.group(0) for m in re.finditer(r'\w+|[^\w\s]', second)]
    fa, fb = [fold(t) for t, _ in a], [fold(t) for t in b]
    target = fold(rule['find'])
    keep = set()
    for op, i1, i2, j1, j2 in SequenceMatcher(None, fa, fb, autojunk=False).get_opcodes():
        if op == 'equal':
            keep |= {a[i][1] for i in range(i1, i2) if fa[i] == target}
    return keep


def apply(text: str, rules: list[dict], block_id: str, second_text: str = '') -> tuple[str, list[dict]]:
    """Apply rules to machine text. Occurrences confirmed by an independent reading are left alone."""
    changes = []
    for rule in rules:
        excluded = set(rule.get('exclude') or [])
        lookalike = fold(rule['find']) == fold(rule['replace'])      # Α->A: pixels cannot tell them apart
        confirmed = _confirmed_by_second_reading(text, second_text, rule) if second_text and not lookalike else set()
        hits = [h for h in matches(text, rule) if f'{block_id}#{h["n"]}' not in excluded and h['start'] not in confirmed]
        for h in reversed(hits):
            text = text[:h['start']] + rule['replace'] + text[h['end']:]
        changes += [{'rule': rule['id'], 'from': rule['find'], 'to': rule['replace'], 'n': h['n'],
                     'context': h['context']} for h in hits]
    return text, changes
