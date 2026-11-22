"""Stage: 'fix once, apply everywhere'. When reviewers made the same single-token correction several times
(machine 'Γ' -> human 'G'), propose a book rule, with the number of other occurrences it would change."""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher

from ..core import rules as book_rules
from ..core.text import tokens

MIN_REPEATS = 2


def _machine_text(b) -> str:
    return next((c.get('text') for c in b['candidates'] if c.get('engine') == 'marker' and c.get('text')), '') or ''


def suggest(blocks: list[dict], existing_rules: list[dict]) -> list[dict]:
    pairs = Counter()
    examples = {}
    for b in blocks:
        human = next((d for d in reversed(b.get('decisions') or []) if d['action'] == 'replace_text'), None)
        if not human or b['chosen'].get('type') != 'text':
            continue
        a, c = tokens(_machine_text(b)), tokens(b['chosen']['text'])
        for op, i1, i2, j1, j2 in SequenceMatcher(None, a, c, autojunk=False).get_opcodes():
            if op == 'replace' and i2 - i1 == 1 and j2 - j1 == 1 and len(a[i1]) <= 4 and len(c[j1]) <= 4:
                pairs[(a[i1], c[j1])] += 1
                examples.setdefault((a[i1], c[j1]), b['id'])
    have = {(r['find'], r['replace']) for r in existing_rules}
    out = []
    for (find, repl), n in pairs.most_common():
        if n < MIN_REPEATS or (find, repl) in have:
            continue
        rule = {'id': 'preview', 'find': find, 'replace': repl, 'context': '', 'exclude': []}
        undecided = sum(len(book_rules.matches(_machine_text(b), rule)) for b in blocks if not b.get('decisions'))
        out.append({'find': find, 'replace': repl, 'corrections': n, 'would_change': undecided,
                    'example_block': examples[(find, repl)]})
    return out
