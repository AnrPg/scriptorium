"""Stage: cross-references ("see Fig. 4.9", "βλ. Εικόνα 5-2Γ", "Πίνακα 4.3") resolved to the numbered element.

Targets: every numbered element of the processed pages: figure/table groups (their caption label) and labelled
boxes ("ΠΑΡΑΔΕΙΓΜΑ 4.3", "Exercise 2-1"). A mention matches a target when the NUMBER is identical (separators
'.', '-', '–' are equivalent) and the WORD shares its first three letters, ignoring case and accents (so inflected
forms match: "Σχήματος 4.9" ~ "ΣΧΗΜΑ 4.9", "Εικ. 5-4" ~ "ΕΙΚΟΝΑ 5-4", "Fig. 2" ~ "Figure 2"). A trailing panel letter
("5-2Γ") is kept as `panel`. Results: resolved / ambiguous (several targets) / unresolved (e.g. on an unprocessed
page). Reviewer decisions (`link_reference`) override any mention. Deterministic; no language lists.
"""
from __future__ import annotations

import re

from ..core.reasons import flag
from ..core.text import strip_marks
from .captions import caption_label

# word (>=3 letters, or an abbreviation ending in '.') + number (+ optional panel letter); a number followed by a
# prime/apostrophe (3′, 5΄ ends of DNA strands, minutes) is not an element number
MENTION = re.compile(r'(?<!\w)((?:[^\W\d_]{3,}|[^\W\d_]{2,}\.)\.?)\s*(\d+(?:[.\-–]\d+)*)([A-Za-zΑ-Ωα-ω])?(?![\w′΄ʹ\'’])')


def _num(n: str) -> str:
    return re.sub(r'[.\-–]', '.', n)


def _stem(w: str) -> str:
    return strip_marks(w.rstrip('.')).casefold()[:3]


def own_label(b: dict, by_id: dict) -> str | None:
    """The number an element carries itself: from its captions; a caption with no figure (still a numbered
    element); a labelled box heading; or the start of its own content (tables often embed 'TABLE 4.3')."""
    caps = b['relations'].get('captions')
    if caps:
        lab = next((by_id[c].get('caption_label') for c in caps if c in by_id and by_id[c].get('caption_label')), None)
        if lab:
            return lab
    if b['kind'] == 'caption' and not b['relations'].get('caption_for'):
        return b.get('caption_label')
    if b['kind'] in ('heading', 'table', 'figure', 'chart') and b['chosen'].get('text'):
        return caption_label(re.sub(r'<[^>]+>', ' ', b['chosen']['text']))[0]
    return None


def targets(blocks: list[dict]) -> list[dict]:
    out = []
    by_id = {x['id']: x for x in blocks}
    for b in blocks:
        label = own_label(b, by_id)
        if label:
            m = MENTION.match(label)
            if m:
                out.append({'id': b['id'], 'label': label, 'stem': _stem(m.group(1)), 'num': _num(m.group(2)),
                            'page': b['page']})
    return out


def annotate(blocks: list[dict], overrides: dict | None = None) -> dict:
    """Mutates text blocks with `references`; targets get relations.referenced_by. Returns a summary."""
    tg = targets(blocks)
    by_id = {b['id']: b for b in blocks}
    stats = {'resolved': 0, 'ambiguous': 0, 'unresolved': 0}
    for b in blocks:
        b.pop('references', None)
        b['relations'].pop('refers_to', None)
    stems = {t['stem'] for t in tg}                                # the book's own label vocabulary
    for b in blocks:
        if b['chosen'].get('type') != 'text' or b.get('flow') == 'furniture':
            continue
        text = b['chosen']['text']
        mine = [b.get('caption_label'), own_label(b, by_id)]
        own_keys = {(_stem(MENTION.match(x).group(1)), _num(MENTION.match(x).group(2))) for x in mine if x and MENTION.match(x)}
        own = {t['id'] for t in tg if t['id'] in (b['id'], b['relations'].get('caption_for'))}
        refs = []
        for n, m in enumerate(MENTION.finditer(text)):
            word, stem, num = m.group(1), _stem(m.group(1)), _num(m.group(2))
            if (stem, num) in own_keys or not text[:m.start()].strip(' *_#'):
                continue                                           # an element's own number (or a label that
                                                                   # opens the block), not a reference
            if not (word[0].isupper() or stem in stems):
                continue                                           # "την 3", "το 5": not a label word
            hits = [t for t in tg if t['num'] == num and t['stem'] == stem and t['id'] not in own]
            if not hits and not _looks_like_reference(text, m) and stem not in stems:
                continue                                           # e.g. "Ομάδων 1940": not written as a reference
            key = f'{b["id"]}#{n}'
            ref = {'n': n, 'text': m.group(0), 'label': f'{m.group(1)} {m.group(2)}', 'panel': m.group(3),
                   'start': m.start(), 'end': m.end()}
            if overrides and key in overrides:
                tgt = overrides[key]
                ref.update(target=tgt, status='resolved' if tgt else 'not_a_reference', source='human')
            elif len(hits) == 1:
                ref.update(target=hits[0]['id'], status='resolved', source='auto')
            elif hits:
                ref.update(target=None, candidates=[t['id'] for t in hits], status='ambiguous', source='auto')
                flag(b, 'REFERENCE_AMBIGUOUS', text=m.group(0))
            else:
                ref.update(target=None, status='unresolved', source='auto')
            if ref['status'] == 'not_a_reference':
                continue
            stats[ref['status']] = stats.get(ref['status'], 0) + 1
            refs.append(ref)
            if ref.get('target') in by_id:
                b['relations'].setdefault('refers_to', [])
                if ref['target'] not in b['relations']['refers_to']:
                    b['relations']['refers_to'].append(ref['target'])
                by_id[ref['target']]['relations'].setdefault('referenced_by', [])
                if b['id'] not in by_id[ref['target']]['relations']['referenced_by']:
                    by_id[ref['target']]['relations']['referenced_by'].append(b['id'])
        if refs:
            b['references'] = refs
    return {'targets': len(tg), **stats}


def _looks_like_reference(text: str, m) -> bool:
    """Unresolved mentions are kept only when written like a reference: inside parentheses or after a
    'see'-style abbreviation (any short word ending in '.' right before it), e.g. '(βλ. Σχήμα 4.9)'."""
    before = text[max(0, m.start() - 8):m.start()]
    return '(' in before or bool(re.search(r'\w{1,4}\.\s*$', before))
