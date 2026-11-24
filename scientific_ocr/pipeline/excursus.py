"""Stage: excursus (παρέκβαση) annotation and suggestions.

An excursus is a stretch of the book intercalated into the course text — a historical note, applications, an
appendix of another science — on one or several pages, or on part of a page. The reviewer states only its two
ENDS (first and last segment); every segment between them in the book's reading order belongs to it, so the
excursus survives merges, new regions and re-OCR of what lies inside.

Consequences (other stages):
  * reading flow (pipeline.flow): an excursus is its own STRAND — a paragraph of the course text interrupted by four
    pages of excursus continues the course paragraph before it, not the excursus' last text, and text inside the
    excursus continues text of the same excursus;
  * outputs (pipeline.reading): in place (a box), at the end of the chapter, at the end of the book, or left out —
    the interrupted course paragraph is joined in every case.

Suggestions: a course paragraph that stops mid-sentence and resumes (lowercase) only after one or more pages
whose text does not continue it marks the pages between as a likely excursus.
"""
from __future__ import annotations

import hashlib

from ..core.decisions import page_of
from ..core.model import chosen_text

PLACEMENTS = ('inplace', 'chapter_end', 'book_end', 'remove')
SUGGEST_MAX_GAP_PAGES = 6          # an excursus longer than this is not looked for (resumption too far to be sure)
TEXT_KINDS = {'paragraph', 'list_item'}


def _flowing(blocks: list[dict]) -> list[dict]:
    return [b for b in blocks if not b.get('excluded')]


def _resolve(end: str | None, merged_into: dict, by_id: dict, flow: list[dict], last: bool) -> int | None:
    """Index in `flow` of an end segment: followed into a merge; a segment that no longer exists falls back to the
    first (or last) segment of its page, so the excursus keeps its pages."""
    end = merged_into.get(end, end)
    idx = {b['id']: i for i, b in enumerate(flow)}
    if end in idx:
        return idx[end]
    page = page_of(end)
    on_page = [i for i, b in enumerate(flow) if b['page'] == page]
    return (on_page[-1] if last else on_page[0]) if on_page else None


def annotate(blocks: list[dict], excursus: list[dict], merged_into: dict[str, str]) -> list[dict]:
    """Mutates blocks (b['excursus'] = id); returns the canonical excursus list (members resolved, in book order).
    Overlaps: the excursus that starts first keeps the shared segments."""
    for b in blocks:
        b.pop('excursus', None)
    flow = _flowing(blocks)
    by_id = {b['id']: b for b in flow}
    out = []
    for x in excursus:
        a = _resolve(x.get('first'), merged_into, by_id, flow, last=False)
        z = _resolve(x.get('last'), merged_into, by_id, flow, last=True)
        if a is None or z is None or z < a:
            out.append({**_public(x), 'members': [], 'pages': [], 'broken': True})
            continue
        members = [b['id'] for b in flow[a:z + 1] if not b.get('excursus')]
        for m in members:
            by_id[m]['excursus'] = x['id']
        out.append({**_public(x), 'members': members, 'pages': sorted({by_id[m]['page'] for m in members}),
                    'first_id': flow[a]['id'], 'last_id': flow[z]['id'], 'broken': False})
    out.sort(key=lambda e: (e['pages'][0] if e['pages'] else 1e9))
    return out


def _public(x: dict) -> dict:
    return {'id': x['id'], 'type': x.get('type') or 'other', 'name': x.get('name') or '',
            'placement': x.get('placement') if x.get('placement') in PLACEMENTS else 'inplace',
            'first': x.get('first'), 'last': x.get('last')}


def suggest(blocks: list[dict], dismissed: set, continues) -> list[dict]:
    """Pages that interrupt a course paragraph: the last course paragraph of page p stops mid-sentence (and was
    linked to nothing), the first course paragraph of page q > p+1 continues it, and no page between continues
    it. The suggested excursus spans the segments between the two."""
    flow = [b for b in _flowing(blocks) if b.get('flow') != 'furniture']
    idx = {b['id']: i for i, b in enumerate(flow)}
    course = [b for b in flow if b.get('flow') == 'main' and not b.get('excursus') and b['kind'] in TEXT_KINDS
              and chosen_text(b)]
    first_of, last_of = {}, {}
    for b in course:
        first_of.setdefault(b['page'], b)
        last_of[b['page']] = b
    out = []
    for p, last in sorted(last_of.items()):
        if last['relations'].get('continues_in'):
            continue
        for q in range(p + 1, p + SUGGEST_MAX_GAP_PAGES + 2):
            f = first_of.get(q)
            if f is None:
                continue
            if not continues(chosen_text(last), chosen_text(f)):
                continue                                   # this page starts its own text: keep looking further
            if f['relations'].get('continues_from'):
                continue                                   # it continues text of the gap itself (the excursus' own)
            if q >= p + 2:
                a, z = idx[last['id']] + 1, idx[f['id']] - 1
                if a <= z:
                    key = 'xs' + hashlib.sha1(f'{last["id"]}>{f["id"]}'.encode()).hexdigest()[:10]
                    if key not in dismissed:
                        out.append({'key': key, 'first': flow[a]['id'], 'last': flow[z]['id'],
                                    'pages': sorted({flow[a]['page'], flow[z]['page']}), 'interrupted': last['id'],
                                    'resumes': f['id'], 'segments': z - a + 1,
                                    'explanation_el': f'Η παράγραφος της σ. {p} σταματά στη μέση και συνεχίζει στη '
                                                      f'σ. {q}: ό,τι υπάρχει ανάμεσα μοιάζει με παρέκβαση.'})
            break
    best: dict[str, dict] = {}                     # one resumption continues ONE paragraph: the nearest claim wins
    for x in out:
        if x['resumes'] not in best or x['segments'] < best[x['resumes']]['segments']:
            best[x['resumes']] = x
    return [x for x in out if best[x['resumes']] is x]
