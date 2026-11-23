"""Stage: caption groups. A figure (or any insert) owns an ORDERED group of caption segments, each with a role:
  title  the numbered label alone ("ΣΧΗΜΑ 4.9", "Fig. 2-1")
  text   the caption body (it may start with the label: "ΕΙΚΟΝΑ 5-4 Οι δύο αλυσίδες...")
Steps (language-independent; reviewer decisions override all of them later in the overlay):
  1. associate captions with figures from two independent engines: Marker groups (FigureGroup/PictureGroup) and
     Docling's explicit picture/table -> caption links. Both agree -> confirmed; one engine only -> accepted, source
     recorded; they disagree -> Marker's choice kept and flagged for review; neither -> nearest figure, inferred
  2. chain split caption fragments: an unassigned caption, or a heading that is only a numbered label, directly
     adjacent to an assigned caption joins the same group (layout engines often split title and body)
  2b. an emphasised paragraph that OPENS with a picture/table label ("**ΕΙΚΟΝΑ 5-6** Παραδείγματα…") is a caption
     even when no figure box is next to it — the layout engine may have split the picture into text lines
     (panel labels, empty lines). It stays CAPTION_WITHOUT_FIGURE; pipeline.structure suggests assembling the
     picture from the pieces above it (a reviewer region), never automatically
  3. after reviewer decisions (finalize): caption_label (the "<word> <number>" prefix) and role from the final text
"""
from __future__ import annotations

import re

from ..core import geometry as geo
from ..core.labels import CAPTION_LABEL, FIGURE_LABEL, TABLE_LABEL
from ..core.model import CAPTIONABLE_KINDS
from ..core.reasons import flag
from ..core.text import label_key

# distances in page units (geometry.unit: ~1 pt on an A4/Letter page)
MAX_GAP = 250
CHAIN_GAP = 30
BESIDE_GAP = 40
_LABEL = CAPTION_LABEL


def caption_label(text: str) -> tuple[str | None, str]:
    """('ΕΙΚΟΝΑ 5-4', remaining text) or (None, text)."""
    t = re.sub(r'[*_]', '', text or '').strip()
    m = _LABEL.match(t)
    return (m.group(1), t[m.end():].strip()) if m else (None, t)


def associate(page_blocks: list[dict], docling_blocks: list[dict] | None = None, unit: float = 1.0):
    """`unit`: page scale (geometry.unit); all distances below are multiplied by it."""
    figs = [b for b in page_blocks if b['kind'] in CAPTIONABLE_KINDS]
    caps = [b for b in page_blocks if b['kind'] == 'caption']
    dl = _docling_links(figs, caps, docling_blocks or [])
    for cap in caps:
        m, d = _engine_group(cap, figs), dl.get(cap['id'])
        if m and d and m['id'] != d['id']:
            _attach(cap, m, False, 'marker')
            flag(cap, 'CAPTION_ENGINES_DISAGREE', marker=m['id'], docling=d['id'])
        elif m or d:
            _attach(cap, m or d, False, 'marker+docling' if m and d else ('marker' if m else 'docling'))
        else:
            near = _nearest(cap, figs, unit)
            if near is not None:
                _attach(cap, near, True, 'geometry')
    _chain_fragments(page_blocks, unit)
    _orphan_labelled_captions(page_blocks)


def insert_label(text: str) -> str | None:
    """The picture/table label an emphasised text opens with ('ΕΙΚΟΝΑ 5-6'), else None."""
    raw = (text or '').lstrip()
    label, _ = caption_label(raw)
    if not label:
        return None
    emphasised = raw.startswith('**') or label.split()[0].isupper()
    k = label_key(label)
    return label if emphasised and (FIGURE_LABEL.search(k) or TABLE_LABEL.search(k)) else None


def _orphan_labelled_captions(page_blocks):
    for b in page_blocks:
        if b['kind'] == 'paragraph' and b['chosen'].get('type') == 'text' and not b['relations'].get('caption_for') \
                and insert_label(b['chosen'].get('text')):
            b['original_kind'], b['kind'] = b['kind'], 'caption'


def _docling_links(figs, caps, docling_blocks) -> dict:
    """caption block id -> figure block, from Docling's own picture/table caption references."""
    def refs(b):
        return {c.get('ref') for c in b['candidates'] if c.get('engine') == 'docling'}
    out = {}
    for d in docling_blocks:
        for cref in d['extra'].get('caption_refs') or []:
            fig = next((f for f in figs if d['ref'] in refs(f)), None)
            cap = next((c for c in caps if 'docling:' + cref in refs(c)), None)
            if fig and cap:
                out[cap['id']] = fig
    return out


def finalize(blocks: list[dict]):
    """After reviewer decisions: label and role come from the FINAL content (a corrected caption must not keep
    the role derived from a misread), unless the reviewer set the role explicitly."""
    reconcile(blocks)
    pos = {b['id']: i for i, b in enumerate(blocks)}
    for f in blocks:                                  # caption parts follow the (possibly reviewer-changed) reading order
        if f['relations'].get('captions'):
            f['relations']['captions'].sort(key=lambda cid: pos.get(cid, 1 << 30))
    for cap in (b for b in blocks if b['kind'] == 'caption'):
        label, rest = caption_label(cap['chosen'].get('text', ''))
        cap['caption_label'] = label
        if cap.get('caption_role_source') != 'human':
            cap['caption_role'] = 'title' if label and len(rest) < 3 else 'text'
        if not cap['relations'].get('caption_for') and not cap['relations'].get('caption_group'):
            flag(cap, 'CAPTION_WITHOUT_FIGURE')


def reconcile(blocks: list[dict]) -> None:
    """ONE source of truth for caption links: the caption's own `caption_for`. Every stage (geometry, reviewer
    decisions, merges, captions across pages) may set it; the figures' caption lists are then DERIVED from it, so
    a caption can never be listed under one figure while pointing to another (shown twice) or point to a segment
    that no longer exists (shown as 'no figure'). A link to a vanished segment is dropped (and flagged later)."""
    by_id = {b['id']: b for b in blocks}
    owner = {}
    for b in blocks:
        f = b['relations'].get('caption_for')
        if f and (f not in by_id or f == b['id']):
            b['relations']['caption_for'] = None
            f = None
        if f:
            owner[b['id']] = f
    for f in blocks:
        old = f['relations'].get('captions') or []
        mine = [c for c in old if owner.get(c) == f['id']] + [c for c, o in owner.items() if o == f['id'] and c not in old]
        if mine or 'captions' in f['relations']:
            f['relations']['captions'] = list(dict.fromkeys(mine))
    _captions_follow_owner(blocks, owner, by_id)


def _captions_follow_owner(blocks: list[dict], owner: dict, by_id: dict) -> None:
    """Reading order: every caption comes right after its figure (in the figure's caption order), on the same
    page — wherever the caption was printed and wherever the reviewer moved the figure. The page outline, the page
    preview and the export (which renders captions inside their figure) then agree. Captions continuing on
    another page keep their place."""
    follows = {c: f for c, f in owner.items() if by_id[c].get('page') == by_id[f].get('page')}
    if not follows:
        return
    pos = {b['id']: i for i, b in enumerate(blocks)}
    out = []
    for b in blocks:
        if b['id'] in follows:
            continue
        out.append(b)                                 # its captions, in their reading order (reviewer moves kept)
        out += sorted((by_id[c] for c, f in follows.items() if f == b['id']), key=lambda x: pos[x['id']])
    if len(out) != len(blocks):                       # defensive: never lose a segment
        return
    blocks[:] = out
    order: dict[int, int] = {}
    for b in blocks:
        order[b.get('page')] = order.get(b.get('page'), 0) + 1
        b['reading_order'] = order[b.get('page')]


def _attach(cap, fig, inferred: bool, source: str = 'geometry'):
    cap['relations'].update(caption_for=fig['id'], inferred=inferred, caption_link_source=source)
    fig['relations'].setdefault('captions', []).append(cap['id'])
    if inferred:
        flag(cap, 'CAPTION_ASSOCIATION_INFERRED')


def _chain_fragments(page_blocks: list[dict], unit: float):
    by_id = {b['id']: b for b in page_blocks}
    changed = True
    while changed:
        changed = False
        assigned = [b for b in page_blocks if b['kind'] == 'caption' and b['relations'].get('caption_for')]
        figs = [b for b in page_blocks if b['kind'] in CAPTIONABLE_KINDS]
        for b in page_blocks:
            if b['kind'] not in ('caption', 'heading', 'paragraph') or b['relations'].get('caption_for'):
                continue
            label, rest = caption_label(b['chosen'].get('text', '')) if b['chosen'].get('type') == 'text' else (None, '')
            label_only_heading = b['kind'] == 'heading' and label and len(rest) < 3
            labelled_paragraph = b['kind'] == 'paragraph' and label and label.split()[0].isupper()
            if not (b['kind'] == 'caption' or label_only_heading or labelled_paragraph):
                continue
            near = next((c for c in assigned if _adjacent(b, c, unit)), None)
            fig = by_id[near['relations']['caption_for']] if near else \
                (next((f for f in figs if _adjacent(b, f, unit) or _beside(b, f, unit)), None)
                 if labelled_paragraph else None)
            if fig:
                if b['kind'] != 'caption':                # a caption the layout model typed as heading/paragraph
                    b['original_kind'], b['kind'] = b['kind'], 'caption'
                _attach(b, fig, True, 'adjacent fragment' if near else 'labelled text next to figure')
                fig['relations']['captions'].sort(key=lambda cid: page_blocks.index(by_id[cid]))
                changed = True


def _adjacent(a, b, unit: float) -> bool:
    ba, bb = a['geometry']['bbox'], b['geometry']['bbox']
    vgap = max(ba[1], bb[1]) - min(ba[3], bb[3])
    return vgap <= CHAIN_GAP * unit and geo.same_column(ba, bb, 0.2)


def _beside(a, b, unit: float) -> bool:
    """Side captions (common in textbooks): next to the figure, sharing part of its height."""
    ba, bb = a['geometry']['bbox'], b['geometry']['bbox']
    hgap = max(ba[0], bb[0]) - min(ba[2], bb[2])
    return hgap <= BESIDE_GAP * unit and geo.vertical_overlap(ba, bb) >= 0.3


def _engine_group(cap, figs):
    g = cap['relations'].get('marker_group')
    return next((f for f in figs if g and f['relations'].get('marker_group') == g), None)


def _nearest(cap, figs, unit: float):
    cb = cap['geometry']['bbox']
    best, best_d = None, float('inf')
    for f in figs:
        fb = f['geometry']['bbox']
        d = max(0.0, max(fb[1], cb[1]) - min(fb[3], cb[3])) + max(0.0, max(fb[0], cb[0]) - min(fb[2], cb[2]))
        if d < best_d:
            best, best_d = f, d
    return best if best_d < MAX_GAP * unit else None


# ---------------------------------------------------------------- caption continuations (document level)
_NOTE_MARK = re.compile(r'^\s*(?:\*(?!\*)|†|‡|\(\*\))')          # footnote marks under a table/figure
_SOURCE = re.compile(r'^\s*(?:πηγ[ηή]|source|σημ\.?|σημείωση|note)\b', re.I)
_CONT_START = re.compile(r'^\s*(?:[•■▪◦\-–]\s|\(?[α-ωa-zΑ-ΩA-Z0-9]{1,2}[).]\s|(?:πηγ[ηή]|source|σημ\.?|note|key)\b)', re.I)
CONT_GAP = 30                  # page units between the caption and its continuation
SIZE_TOL = 0.10


def continuations(blocks: list[dict], page_refs: dict, units: dict, dismissed) -> list[dict]:
    """A caption's text often continues below it as list items, a legend or a "Source:" line that the layout
    engine typed as paragraphs; a table or figure without caption often has notes under it ("* …", "Πηγή: …"). A paragraph directly under the LAST part of a caption (same column, small gap)
    joins the caption when it reads like a continuation (list marker, legend/source/note) or is printed in the
    caption's size and smaller than the page's body text. Returns document fixes (dismissable)."""
    from .structure import key as skey
    out = []
    by_id = {b['id']: b for b in blocks}
    flow = [b for b in blocks if b.get('flow') != 'furniture' and not b.get('excluded')]
    joined: set = set()
    for i, b in enumerate(flow):
        if b['id'] in joined:
            continue                                   # joined in this pass: not a start of another continuation
        if b['kind'] in CAPTIONABLE_KINDS and not b['relations'].get('captions'):
            fig, notes_only = b, True                  # a table/figure WITHOUT caption: its notes ("*", "Πηγή:")
        elif b['kind'] == 'caption' and b['relations'].get('caption_for'):
            fig, notes_only = by_id.get(b['relations']['caption_for']), False
            if not fig or (fig['relations'].get('captions') or [None])[-1] != b['id']:
                continue                               # only after the LAST caption part
        else:
            continue
        last = b
        for nxt in flow[i + 1:]:
            if nxt['page'] != last['page'] or nxt['kind'] != 'paragraph' or nxt['chosen'].get('type') != 'text':
                break
            u = units.get(nxt['page'], 1.0)
            a, c = last['geometry']['bbox'], nxt['geometry']['bbox']
            if c[1] - a[3] > CONT_GAP * u or not geo.same_column(a, c, 0.6):
                break
            raw = nxt['chosen'].get('text') or ''
            text = re.sub(r'[*_]', '', raw)
            note = bool(_NOTE_MARK.match(raw))
            ty, tc, ref = nxt.get('typography') or {}, b.get('typography') or {}, page_refs.get(nxt['page']) or {}
            same_size = ty.get('text_h') and tc.get('text_h') and abs(ty['text_h'] / tc['text_h'] - 1) <= SIZE_TOL \
                and (not ref.get('text_h') or ty['text_h'] < ref['text_h'] * 0.97)
            if not (note or (not notes_only and (_CONT_START.match(text) or same_size))
                    or (notes_only and _SOURCE.match(text))):
                break
            k = skey('doc:caption_continues', [b['id'], nxt['id']])
            if k in dismissed or any(d.get('action') in ('detach_caption', 'associate_caption') for d in nxt.get('decisions', [])):
                break
            nxt['original_kind'], nxt['kind'] = nxt['kind'], 'caption'
            nxt['caption_role'] = nxt.get('caption_role') or 'text'
            _attach(nxt, fig, True, 'caption continuation')
            flag(nxt, 'CAPTION_CONTINUATION')
            joined.add(nxt['id'])
            out.append({'key': k, 'kind': 'caption_continuation', 'rule': 'list/legend/source line or caption-size text directly under the caption',
                        'blocks': [b['id'], nxt['id']], 'pages': [nxt['page']], 'before': 'paragraph', 'after': 'caption',
                        'subject': text[:90], 'before_el': 'παράγραφος στο κείμενο', 'after_el': 'μέρος της λεζάντας',
                        'explanation_el': 'Συνέχεια της λεζάντας (λίστα/υπόμνημα/πηγή ή ίδιο μέγεθος γραμμάτων): ενώθηκε με τη λεζάντα και πάει με την εικόνα',
                        'evidence': {}})
            last = nxt
    return out
