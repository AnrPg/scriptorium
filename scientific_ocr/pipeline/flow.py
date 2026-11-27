"""Stage: reading-flow annotation for linear outputs.

Every block gets a `flow` role:
  main       body text (paragraphs, headings)
  insert     content placed INTO the text flow: figures, tables, equations, excerpts, and every caption
             (a caption follows its figure's role; one without a figure, or of a group, is an insert by default)
Visual content is ALWAYS an insert by default: a figure/chart/table/equation kind, a segment the reviewer
categorised as one (an entity type of the 'insert' family: figure, table, equation…) and an untranscribed
excerpt. A TEXT segment (paragraph, list item, heading…) stays body text even while its output is an image
(unreadable, image only, OCR pending): it is still part of the argument and may continue a paragraph.
The reviewer's set_flow overrides any of these.
  furniture  running headers/footers/page numbers (not part of the argument of the text)
and body paragraphs that the page layout split (a sentence interrupted by an insert or a page break) are linked
with relations.continues_in / relations.continues_from, so a linear reader knows exactly where the interrupted
text resumes. Detection is script-independent: the first part does not end a sentence and the second part
starts in lowercase (or the first ends with a hyphenation).
"""
from __future__ import annotations

import re

from ..core.model import CAPTIONABLE_KINDS, EQUATION_KINDS

INSERT_KINDS = CAPTIONABLE_KINDS | EQUATION_KINDS | {'excerpt'}
_SENTENCE_END = re.compile(r'[.;!?·:»"\')\]]\s*$|[.;!?·]\**\s*$')


def role(b: dict, by_id: dict | None = None, inserts: frozenset = frozenset()) -> str:
    if b.get('excluded'):                              # furniture CONFIRMED by pipeline.furniture (or the reviewer);
        return 'furniture'                             # an engine label alone does not take print out of the text
    if b.get('flow_override') in ('main', 'insert'):   # the reviewer's decision (set_flow)
        return b['flow_override']
    if b['kind'] == 'caption':                         # travels with its figure (inherits its role); a caption
        owner = (by_id or {}).get(b['relations'].get('caption_for'))   # of a group, or still unlinked: an insert
        r = role(owner, by_id, inserts) if owner is not None and owner is not b and owner['kind'] != 'caption' else None
        return r if r in ('main', 'insert') else 'insert'
    if b['kind'] == 'excerpt':                         # an excerpt the reviewer transcribed is text; else an image
        return 'main' if b['chosen']['type'] == 'text' else 'insert'
    if b['kind'] in INSERT_KINDS or b['id'] in inserts:     # visual kind, or categorised as visual by the reviewer
        return 'insert'
    return 'main'                                      # text segments: even when shown as an image for now


def _text(b) -> str:
    return re.sub(r'<[^>]+>|\$[^$]*\$', ' ', b['chosen'].get('text') or '').strip()


_MID_SENTENCE_TOKEN = re.compile(r'^(?:[A-ZΑ-Ω]{2,5}\b(?!\s+[A-ZΑ-Ω]{2,})|\d|[(\[])')   # an acronym, not a capitalised title
_LATIN_WORD = re.compile(r'^[A-Za-z]')


def _greek(t: str) -> bool:
    return any('\u0370' <= ch <= '\u03ff' or '\u1f00' <= ch <= '\u1fff' for ch in t)


def continues(prev: str, nxt: str) -> bool:
    prev, nxt = prev.rstrip(), nxt.lstrip(' *_"«(')
    if not prev or not nxt:
        return False
    if prev.endswith(('-', '‐')) and nxt[0].isalpha() and nxt[0].islower():
        return True                                    # hyphenated word split across the break (its rest is
                                                       # lowercase: «νημα-» + «Αυτό» is a dash, not a split word)
    if _SENTENCE_END.search(prev):
        return False
    # an open sentence continues with a lowercase word — or with a token that never starts a sentence by its form:
    # an acronym («DNA ligase»), a number, a bracket, a Latin word inside Greek text («(Hooke)», «το Golgi»)
    return (nxt[0].isalpha() and nxt[0].islower()) or bool(_MID_SENTENCE_TOKEN.match(nxt)) \
        or (prev[-1:].isalpha() and _greek(prev[-12:]) and _LATIN_WORD.match(nxt) is not None)


NEVER_CONTINUES = INSERT_KINDS | {'caption', 'heading'}
BOX_LABELS = {'labelled_element', 'box_heading'}      # pipeline.sections: titles of boxes and their sub-headings


def strand_of(b: dict, boxes: dict) -> str:
    """The CONTEXT a text continues in: 'main' (the course text), 'x:<id>' (an excursus, pipeline.excursus) or the
    box it is categorised in ('g:<group>' / 'e:<entity>'). The reviewer's 'main text' takes a segment out of its box,
    not out of its excursus."""
    box = boxes.get(b['id']) if b.get('flow_override') != 'main' else None
    if box:
        return box
    return f'x:{b["excursus"]}' if b.get('excursus') else 'main'


def annotate(blocks: list[dict], inserts: frozenset = frozenset(), boxes: dict | None = None):
    """Mutates blocks (in final reading order): sets b['flow'] and continuation relations.

    `boxes`: segment id -> box strand, for segments CATEGORISED as something special (an example, a question, a
    box…: reviewer groups and entities). They are inserts relative to the course text by default, and text in a
    box continues text of the same box. Continuation is decided per strand (strand_of): a course paragraph broken
    by an insert, a box, an excursus of many pages or a page break continues the last COURSE paragraph, and a
    paragraph of an excursus continues the same excursus — both may happen on the same page.
    relations.continues_strand names the context of a continuation; interrupted_by lists what lies between
    (single inserts by id; a whole box, excursus or stretch of course text once)."""
    boxes = boxes or {}
    boxed = frozenset(boxes)
    by_id = {b['id']: b for b in blocks}
    last: dict[str, tuple[dict, int]] = {}
    for i, b in enumerate(blocks):
        b['flow'] = role(b, by_id, inserts | boxed)
        for k in ('continues_in', 'continues_from', 'interrupted_by', 'continues_strand'):
            b['relations'].pop(k, None)
        if b['flow'] == 'furniture':
            continue
        if b.get('flow_override') == 'insert' and b['id'] not in boxed and b['kind'] != 'heading':
            continue                                   # text the reviewer placed as an insert: outside every strand
        key = strand_of(b, boxes)
        if b['kind'] == 'heading':
            if not (key == 'main' and b.get('heading_level_source') in BOX_LABELS):
                last.pop(key, None)                    # a heading starts new text in its own context only
            continue                                   # (a box's label «ΕΡΩΤΗΣΗ 6-2» is not a heading of the course
                                                       # text: the course paragraph it interrupts still continues)
        if b['kind'] in NEVER_CONTINUES:
            continue
        prev = last.get(key)
        forced = b.get('continues_override')          # the reviewer's decision (set_continuation) beats the test
        if prev is not None and (forced if forced is not None else continues(_text(prev[0]), _text(b))):
            a = prev[0]
            a['relations']['continues_in'] = b['id']
            b['relations']['continues_from'] = a['id']
            b['relations']['continues_strand'] = key
            breaks, seen = [], set()
            for x in blocks[prev[1] + 1:i]:
                if x.get('flow') == 'furniture':
                    continue
                k = strand_of(x, boxes)
                if k == key:
                    if x.get('flow') == 'insert':
                        breaks.append(x['id'])
                elif k not in seen:
                    seen.add(k)
                    breaks.append(('excursus:' + k[2:]) if k.startswith('x:') else ('box:' + k[2:]) if k != 'main' else 'course')
            if b['page'] != a['page']:
                breaks.append(f'page:{a["page"]}->{b["page"]}')
            if breaks:
                b['relations']['interrupted_by'] = breaks
        last[key] = (b, i)
