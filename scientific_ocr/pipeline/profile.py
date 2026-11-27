"""Stage (document level, after entities and the final flow): the book's OUTPUT PROFILE (core.profiles) applied.

Content. Members of entities whose type or family the profile excludes are marked `profile_omitted` (they stay in
the model, like a reviewer omission; core.model.in_reader_output leaves them out of every reader output, the
review queue and the release status). Which entities qualify:
  * verified ones (the reviewer's own or confirmed) — CONFIRMED exclusions;
  * detected ones whose BOUNDARY has independent evidence (profile 'auto_exclude': a box or engine element
    detected with enough confidence or by a trusted rule; a collective section closing its chapter) — AUTOMATIC
    exclusions, each waiting for ONE boundary confirmation (entity_confirm), never for a review of its content.
    A heading's subtree alone is not such evidence (a margin label set as a heading owns the body text after it);
    a collective section («ΕΡΩΤΗΣΕΙΣ») is corroborated by >= 2 numbered items of its family inside it and then
    bounded by the last of them (_items_bound);
  * anything else is only SUGGESTED: it stays in the output and is listed (no silent loss on a weak guess).
  A question with a linked answer (profile 'keep_answered') stays with its answer: the pair holds the answer.
  Excursus whose type the profile removes (history, biography…) are excluded as reviewer-made, i.e. confirmed.
Safety check (independent evidence): a member that body text continues INTO, or that continues body text outside
the entity, means the boundary cuts a paragraph — that member is not excluded and is flagged
PROFILE_BOUNDARY_CHECK. The reviewer's set_output include=True ('kept') always wins.

Review. With profile 'review_pictures' false (a text-only output drops pictures), a picture's own fidelity flags
are not asked about (PROFILE_NOT_RELEVANT, review lowered to not_needed — never for critical flags or decided
segments). With pictures='captions_only', content that is ONLY an image but is not a picture (a paragraph, table,
caption or uncovered region kept as a crop) would vanish from a text-only output: TEXT_ONLY_LOSS (required). A
content decision taken under ANOTHER output profile (carried over into this one: core.scopes; its `scope`) was
taken for an output that kept images, so it is asked again; one taken under this profile (approve = keep the image
knowingly) stands, and the export lists the segment among what the text does not contain.
"""
from __future__ import annotations

import re

from ..core.decisions import CONTENT_ACTIONS
from ..core.model import FIGURE_KINDS, in_output
from ..core.profiles import MARKER_PRIMARY_WAIVES
from ..core.reasons import REASONS, REVIEW_ORDER, flag
from ..core.vocabulary import family_of
from .entities import selectors

PICTURE_KINDS = FIGURE_KINDS                      # what a text-only output drops (captions survive as text)


def _critical(code: str) -> bool:
    r = REASONS.get(code)
    return bool(r and r.finding and r.finding[1] == 'critical')


def _boundary_cut(b: dict, members: set, by_id: dict) -> bool:
    rel = b.get('relations') or {}
    for k in ('continues_from', 'continues_in'):
        other = by_id.get(rel.get(k))
        if other is not None and other['id'] not in members and in_output(other):
            return True
    return False


MIN_ITEMS = 2                    # numbered items that corroborate a collective section


def _items_bound(e: dict, entities: list[dict]) -> list[str] | None:
    """A collective section («ΕΡΩΤΗΣΕΙΣ») corroborated by its CONTENT: >= MIN_ITEMS entities of the same family
    lie inside it. Its boundary ends with the last of them (a heading subtree can run on past the questions when
    the book's hierarchy is wrong; whatever follows the last item is not taken). None = no such evidence."""
    pos = {m: i for i, m in enumerate(e['members'])}
    fam = family_of(e['type'])
    items = [x for x in entities if x is not e and x['granularity'] != 'section' and family_of(x['type']) == fam
             and x['members'] and all(m in pos for m in x['members'])]
    if len(items) < MIN_ITEMS:
        return None
    last = max(pos[m] for x in items for m in x['members'])
    return e['members'][:last + 1]


def _automatic(e: dict, auto: dict, entities: list[dict]) -> list[str] | None:
    """The members to exclude when the entity's boundary has independent evidence (see the module doc), else None."""
    if e['granularity'] in (auto.get('granularity') or ()):
        ok = bool(e.get('trusted')) or e.get('confidence') in (auto.get('confidence') or ())
        return e['members'] if ok else None
    if e['granularity'] == 'section' and e.get('collective'):
        if e.get('placement') in (auto.get('collective_placement') or ()):
            return e['members']
        return _items_bound(e, entities)
    return None


def apply(blocks: list[dict], entities: list[dict], excursus_list: list[dict], profile: dict) -> dict:
    """Mutates blocks; returns the canonical 'profile' summary (name, exclusions, suggestions, counts)."""
    for b in blocks:
        b.pop('profile_omitted', None)
    name = profile['name']
    by_id = {b['id']: b for b in blocks}
    exclude = set(profile.get('exclude') or ())
    auto = profile.get('auto_exclude') or {}
    excluded, suggested, keep = [], [], set()

    def mark(e_like: dict, members: list[str], confirmed: bool) -> dict:
        mem = set(members)
        cut, done = [], []
        for m in members:
            b = by_id.get(m)
            if b is None or not in_output(b):
                continue
            if _boundary_cut(b, mem, by_id):
                flag(b, 'PROFILE_BOUNDARY_CHECK', entity=e_like['key'], type=e_like['type'])
                cut.append(m)
                continue
            b['profile_omitted'] = {'profile': name, 'key': e_like['key'], 'type': e_like['type'], 'confirmed': confirmed}
            done.append(m)
        return {**e_like, 'members': done, 'boundary_cut': cut, 'confirmed': confirmed}

    for e in entities:
        if not exclude.intersection(selectors(e)):
            continue
        if profile.get('keep_answered') and e.get('answers'):
            keep.update(e['members'])
            keep.update(e['answers'])
            continue
        summary = {'key': e['key'], 'type': e['type'], 'label': e.get('label') or '', 'pages': e.get('pages') or [],
                   'confidence': e.get('confidence'), 'rule': e.get('rule')}
        members = e['members'] if e.get('verified') else _automatic(e, auto, entities)
        if members:
            excluded.append(mark(summary, members, bool(e.get('verified'))))
        else:
            suggested.append({**summary, 'members': e['members']})
    remove_x = set(profile.get('excursus_remove') or ())
    for x in excursus_list:
        if x.get('type') in remove_x and x.get('members'):
            excluded.append(mark({'key': x['id'], 'type': 'excursus:' + x['type'], 'label': x.get('name') or '',
                                  'pages': x.get('pages') or [], 'confidence': 'human', 'rule': 'excursus type'},
                                 x['members'], True))
    for m in keep:                                        # an answered question and its answer stay
        if m in by_id:
            by_id[m].pop('profile_omitted', None)

    not_relevant = loss = 0
    text_only = profile.get('pictures') == 'captions_only'
    for b in blocks:
        if not in_output(b) or b.get('profile_omitted') or b.get('omitted'):
            continue
        picture = b['kind'] in PICTURE_KINDS or b.get('original_kind') in PICTURE_KINDS \
            or 'FIGURE_INTERNAL_TEXT_WITHHELD' in b['reasons']          # a picture (one engine's) or text inside one
        if text_only and not picture and b['chosen'].get('type') == 'image_only' and b.get('flow') != 'furniture':
            flag(b, 'TEXT_ONLY_LOSS')
            last = next((d for d in reversed(b.get('decisions') or []) if d['action'] in CONTENT_ACTIONS), None)
            if last and last.get('scope', name) != name:
                b['review'] = 'required'                  # decided for ANOTHER output profile (carried over): ask again
            loss += 1
        if picture and not profile.get('review_pictures', True) and b['review'] in ('required', 'recommended') \
                and not b.get('decisions') and not any(_critical(r) for r in b['reasons']):
            was = b['review']
            flag(b, 'PROFILE_NOT_RELEVANT', was=was, profile=name)
            b['review'] = 'not_needed'
            not_relevant += 1
    waived = review_policy(blocks, profile)            # after the picture rule (more specific)
    for e in excluded:
        first = by_id.get(e['members'][0]) if e['members'] else None
        last = by_id.get(e['members'][-1]) if e['members'] else None
        e['first'] = ((first or {}).get('chosen') or {}).get('text', '')[:120] if first else ''
        e['last'] = ((last or {}).get('chosen') or {}).get('text', '')[-120:] if last else ''
        e['n'] = len(e['members'])
    return {'name': name, 'settings': profile, 'excluded': excluded, 'suggested': suggested,
            'pending': sum(1 for e in excluded if not e['confirmed'] and e['members']),
            'excluded_segments': sum(1 for b in blocks if b.get('profile_omitted') and not b.get('kept')),
            'pictures_not_reviewed': not_relevant, 'text_only_loss': loss, 'waived_by_engine_policy': waived}


# ---------------------------------------------------------------- engine policy (page-local: full and page builds)
LOSS_MIN_CHARS = 25              # Docling read this many more letters than the chosen reading …
LOSS_SHARE = 0.25                # … and this share of its own reading: text may be MISSING (a misread word
                                 # changes letters, not length — so OCR noise of the second engine never counts)


def _letters(t: str) -> int:
    return len(re.findall(r'[^\W\d_]', re.sub(r'<[^>]+>|\$[^$]*\$', ' ', t or '')))


def text_loss(b: dict) -> tuple[int, int]:
    """(letters Docling read beyond the chosen reading, letters Docling read) — the evidence that text was LOST."""
    mine = _letters((b.get('chosen') or {}).get('text') or '')
    theirs = sum(_letters(c.get('text') or '') for c in b.get('candidates') or [] if c.get('engine') == 'docling')
    return max(0, theirs - mine), theirs


def review_policy(blocks: list[dict], profile: dict) -> int:
    """'marker_primary': the flags of MARKER_PRIMARY_WAIVES are recorded but not asked about (b['waived']; review
    lowered, no crop beside settled text) — unless Docling read clearly more words than the chosen reading
    (ENGINE_TEXT_LOSS_SUSPECTED, required). Equations, tables, coverage, empty or partial OCR stay as they are.
    Segments with a reviewer decision are untouched. Returns the number of segments no longer asked about."""
    if profile.get('engine_policy') != 'marker_primary':
        return 0
    waivable = set(MARKER_PRIMARY_WAIVES)
    n = 0
    for b in blocks:
        if b.get('decisions') or b['review'] not in ('required', 'recommended'):
            continue
        hit = [r for r in b['reasons'] if r in waivable]
        if not hit:
            continue
        missing, total = text_loss(b)
        if missing >= LOSS_MIN_CHARS and missing >= LOSS_SHARE * max(total, 1):
            flag(b, 'ENGINE_TEXT_LOSS_SUSPECTED', missing=missing, of=total)
            continue
        rest = [r for r in b['reasons'] if r not in waivable and REASONS.get(r)]
        b['waived'] = hit
        level = max((REVIEW_ORDER.index(REASONS[r].review) for r in rest if REASONS[r].review), default=0)
        b['review'] = REVIEW_ORDER[level]
        b['display_crop'] = any(REASONS[r].crop for r in rest)
        n += b['review'] == 'not_needed'
    return n
