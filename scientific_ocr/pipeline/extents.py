"""Stage: ENTITY EXTENTS — where an example, exercise, definition, application, box… ENDS.

Detection finds where an entity STARTS (a label: "ΠΑΡΑΔΕΙΓΜΑ 4.3", "**Παράδειγμα:**", "Εφαρμογή στην καθημερινή
ζωή"); the layout engines split its body into many segments (title, statement, sub-headings, paragraphs,
equations, figures, the solution). The extent is the ordered run of segments that belong to it, made an
AUTOMATIC GROUP (reported, dismissable, editable — a reviewer group always wins), so every decision on the entity
(move to the end of the unit, exclude from the output, re-order, category) applies to all of it.

Anchors (strongest first): a labelled heading (word + number), a heading or a paragraph whose label is an entity
of the taxonomy (with or without a number), a shaded heading.
Members follow the anchor in reading order while one of these holds:
  shaded box   the segment's background is still shaded (far from the page's paper) and CHAINS with the previous
               member (within TONE_TOLERANCE of it — photographs darken gradually down the page, so the tone is
               compared with the neighbour, not with the heading); figures/equations/excerpts inside the box's
               horizontal span are included
  layout       no background: same column and indentation, no unusually large gap; equations, figures, tables and
               the entity's own PARTS (solution, strategy, answer, check, hint, given/asked) do not end it
Ends: another anchor (the next example/exercise), a section heading that is not a part of the entity, leaving the
shading, a large gap, a change of indentation of text. A box that reaches the end of its page continues on the
next page with the same rule (shading chain, or text that continues the box's last paragraph / a part heading).
The paper colour of a page is its BRIGHTEST text background (a page full of boxes must not make the box grey
the "paper").
"""
from __future__ import annotations

import re

from ..core.model import chosen_text

TONE_TOLERANCE = 14.0       # RGB distance between NEIGHBOURING members of one shaded box
SHADED = 18.0               # a background this far from the paper is shading
INDENT_TOLERANCE = 30.0     # page units
GAP_FACTOR = 2.5            # a gap this many times the page's median gap ends an unshaded entity
PART_FAMILY = 'part'


def tone(b):
    t = (b.get('source_crop') or {}).get('paper_tone')
    return t if isinstance(t, list) else None


def distance(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def page_paper(blocks) -> dict:
    """Paper colour per page: the BRIGHTEST of its text backgrounds — shading is always darker than the paper,
    and boxes covering most of a page must not become the 'paper'."""
    out = {}
    for p in {b['page'] for b in blocks}:
        tones = [t for b in blocks if b['page'] == p and b['kind'] in ('paragraph', 'heading', 'caption') and (t := tone(b))]
        if tones:
            tones.sort(key=lambda t: 0.299 * t[0] + 0.587 * t[1] + 0.114 * t[2])
            out[p] = tones[-1]
    return out


def _classify(text: str):
    from .entities import (  # lazy: entities -> furniture -> structure -> extents
        TYPES,
        classify,
    )
    t = classify(text or '')
    return t, (TYPES[t][0] if t else None)


def is_anchor(b, labelled_ids: set) -> bool:
    if b['id'] in labelled_ids:
        return True
    text = chosen_text(b)
    if b['kind'] == 'heading':
        t, fam = _classify(text)
        return bool(t) and fam not in (PART_FAMILY, 'insert', 'frame')
    if b['kind'] == 'paragraph':
        lead = re.match(r'^\s*\*\*([^*]{2,40})\*\*', text)
        if lead:
            t, fam = _classify(lead.group(1))
            return bool(t) and fam in ('worked', 'practice', 'theory', 'activity', 'aside')
    if text and b['kind'] not in ('caption',):
        # any segment with text whose FIRST LINE is an entity label with a number: layout engines type boxes as
        # figures / complex regions ("ΕΡΩΤΗΣΗ 5-1 …" read inside a 'figure')
        from .entities import number_of
        first = text.split('\n', 1)[0][:60]
        t, fam = _classify(first)
        return bool(t) and bool(number_of(first)) and fam in ('worked', 'practice', 'theory', 'activity', 'aside')
    return False


def _is_part(b, head_type: str | None = None) -> bool:
    """A part of the entity: solution, strategy, answer, check, hint (any entity), a proof (of a theorem, law or
    definition), or a heading the box stage made a box sub-heading."""
    if b['kind'] not in ('heading', 'paragraph'):
        return False
    text = chosen_text(b)
    lead = re.match(r'^\s*\**([^*\n]{2,40}?)\**\s*[:.]?\s*(?:\n|$|\*\*)', text) if b['kind'] == 'paragraph' else None
    t, fam = _classify(lead.group(1) if lead else text if b['kind'] == 'heading' else '')
    return fam == PART_FAMILY or b.get('heading_level_source') == 'box_heading' or \
        (t == 'proof' and head_type in ('theorem', 'law', 'definition'))


def _more_prominent(b, head) -> bool:
    """b is printed as a HIGHER heading than the entity's label: larger relative print, or (unmeasured) a higher
    engine heading tag."""
    rb, rh = (b.get('typography_rel') or {}).get('size'), (head.get('typography_rel') or {}).get('size')
    if rb and rh:
        return rb > rh * 1.15
    tb, th = (b.get('engine_heading_levels') or {}).get('marker'), (head.get('engine_heading_levels') or {}).get('marker')
    return head['kind'] == 'heading' and tb is not None and th is not None and tb < th


MAX_SKIP = 3                # foreign segments (another column, a page figure) the reading order may interleave


def _median_gap(page_blocks, unit):
    gaps = sorted(max(0.0, b['geometry']['bbox'][1] - a['geometry']['bbox'][3])
                  for a, b in zip(page_blocks, page_blocks[1:]) if b['geometry']['bbox'][1] > a['geometry']['bbox'][1])
    return gaps[len(gaps) // 2] if gaps else 10 * unit


def _overlaps(bb, x0, x1) -> bool:
    return min(bb[2], x1) - max(bb[0], x0) > 0.2 * min(bb[2] - bb[0], x1 - x0)


def extent(head: dict, flow: list[dict], idx: dict, paper: dict, units: dict, anchors: set) -> dict:
    """{'members', 'rule', 'stop', 'ambiguous'} for the entity starting at `head`. `stop` says what ended it;
    `ambiguous` = it ended on a weak signal (a gap or an indentation change without shading), which the model's
    second pass is asked about."""
    pp = paper.get(head['page'])
    ht = tone(head)
    head_type = _classify(chosen_text(head).split('\n', 1)[0][:60])[0]
    shaded = ht is not None and pp is not None and distance(ht, pp) >= SHADED
    members, prev, prev_tone = [head], head, ht
    x0, x1 = head['geometry']['bbox'][0], head['geometry']['bbox'][2]
    left = None
    page = head['page']
    unit = units.get(page, 1.0)
    med = _median_gap([b for b in flow if b['page'] == page], unit)
    i = idx[head['id']] + 1
    rule = 'background shading' if shaded else 'layout'
    stop, ambiguous, skipped = 'end of the document', False, 0
    while i < len(flow):
        b = flow[i]
        bb = b['geometry']['bbox']
        if b['page'] != prev['page']:                  # the entity reached the end of its page: continue?
            rest_same_page = [x for x in flow[idx[prev['id']] + 1:i] if x['page'] == prev['page']]
            if any(_overlaps(x['geometry']['bbox'], x0, x1) for x in rest_same_page) or b['page'] != prev['page'] + 1:
                stop = 'the entity ends before its page does'
                break
            if shaded:
                t, pp2 = tone(b), paper.get(b['page'])
                if t is None or pp2 is None or distance(t, pp2) < SHADED:
                    stop = 'the next page does not continue the shading'
                    break
                prev_tone = t                          # new page, new lighting: restart the chain
            elif not (prev['relations'].get('continues_in') == b['id'] or _is_part(b, head_type)):
                stop, ambiguous = 'the next page does not continue it', True
                break
            rule += ' + next page'
            page, unit = b['page'], units.get(b['page'], 1.0)
            med = _median_gap([x for x in flow if x['page'] == page], unit)
            left = None
        if b['id'] in anchors and not _is_part(b, head_type):
            stop = 'the next labelled entity'           # (a part — a theorem's proof — does not end it)
            break
        if b['kind'] == 'heading' and _more_prominent(b, head):
            stop = 'a heading printed more prominently than the entity\'s label'
            break                                      # a book section ("Απαντήσεις"), even if its word names a part
        if b['kind'] == 'heading' and not shaded and not _is_part(b, head_type) and not (prev is head and b['page'] == head['page']):
            stop = 'a section heading'                 # (the heading right after the label is the entity's title;
            break                                      #  inside a shaded box the shading delimits)
        member = True
        if shaded:
            t = tone(b)
            inside_span = bb[0] >= x0 - 20 * unit and bb[2] <= x1 + 40 * unit
            if t is not None:
                member = distance(t, paper.get(b['page'], pp)) >= SHADED and distance(t, prev_tone) <= TONE_TOLERANCE * 1.6
            else:
                member = inside_span and b['kind'] in ('excerpt', 'figure', 'chart', 'math', 'chemical_reaction', 'table')
            if not member:
                if not _overlaps(bb, x0, x1) and skipped < MAX_SKIP:
                    skipped += 1                       # another column / a page figure interleaved: look past it
                    i += 1
                    continue
                stop = 'the shading ends'
                break
            prev_tone = t if t is not None else prev_tone
        else:
            if not _overlaps(bb, x0, x1) and len(members) > 1 and skipped < MAX_SKIP:
                skipped += 1                           # a segment of another column interleaved by the reading order
                i += 1
                continue
            if bb[1] - prev['geometry']['bbox'][3] > GAP_FACTOR * max(med, 2 * unit) and b['page'] == prev['page']:
                stop, ambiguous = 'an unusually large gap', True
                break
            insert = b.get('flow') == 'insert' or b['kind'] in ('math', 'chemical_reaction', 'figure', 'chart', 'table', 'excerpt')
            if not insert and not _is_part(b, head_type):
                if left is not None and abs(bb[0] - left) > INDENT_TOLERANCE * unit:
                    stop, ambiguous = 'the indentation changes', True
                    break
                left = bb[0] if left is None else left
        skipped = 0
        members.append(b)
        x0, x1 = min(x0, bb[0]), max(x1, bb[2])
        prev = b
        i += 1
    return {'members': [m['id'] for m in members], 'rule': rule, 'stop': stop, 'ambiguous': ambiguous}


EXPLANATION = re.compile(r'^\W{0,3}(όπου|οπου|where|in which|for which|με\s|στην οποία|στο οποίο|στην οποια)', re.I)


def equation_groups(flow: list[dict], claimed: set, units: dict) -> list[dict]:
    """An equation and the paragraph that explains its symbols ("όπου x είναι…", "where k is…") stay together."""
    out = []
    for i, b in enumerate(flow[:-1]):
        nxt = flow[i + 1]
        if b['kind'] not in ('math', 'chemical_reaction') or b['id'] in claimed or nxt['id'] in claimed:
            continue
        gap = nxt['geometry']['bbox'][1] - b['geometry']['bbox'][3]
        if nxt['page'] == b['page'] and nxt['kind'] == 'paragraph' and gap <= 40 * units.get(b['page'], 1.0) \
                and EXPLANATION.match(chosen_text(nxt).replace('*', '')):
            out.append({'head': b['id'], 'members': [b['id'], nxt['id']], 'rule': 'equation + its explanation',
                        'stop': 'end of the explanation', 'ambiguous': False, 'name': 'Εξίσωση με επεξήγηση',
                        'type': 'equation_group'})
            claimed.update([b['id'], nxt['id']])
    return out


def extents(blocks: list[dict], labelled_ids: set, units: dict) -> list[dict]:
    """Automatic groups for every anchor: [{head, members, rule, name}] (members >= 2)."""
    flow = [b for b in blocks if b.get('flow') != 'furniture' and not b.get('excluded')]
    idx = {b['id']: i for i, b in enumerate(flow)}
    paper = page_paper(flow)
    anchors = {b['id'] for b in flow if is_anchor(b, labelled_ids)}
    out, claimed = [], set()
    for b in flow:
        if b['id'] not in anchors or b['id'] in claimed:
            continue
        x = extent(b, flow, idx, paper, units, anchors - {b['id']})
        if len(x['members']) >= 2:
            claimed.update(x['members'])
            out.append({'head': b['id'], **x, 'name': chosen_text(b).split('\n', 1)[0][:120], 'type': 'labelled_box'})
    return out + equation_groups(flow, claimed, units)
