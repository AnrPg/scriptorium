"""Stage (document level): page furniture — running heads, running feet, page numbers, edge tabs — detected
book-wide and EXCLUDED from every output (kept, with evidence, in canonical.json).

Books differ: chapter title + page number at the top, at the bottom, alternating left/right pages, page numbers
on the outer edge, thumb tabs, ornaments. No fixed band or style is assumed. A segment is furniture only when
it is OUTSIDE THE TEXT FRAME and at least one independent signal supports it:

  necessary  position: outside the page's body frame (the box around its substantial content: long text blocks,
             tables, equations — figures only on pages without them, since picture boxes often swallow the
             margin), on the top, bottom, left or right side; when a page has no
             substantial content, the book's median frame (per odd/even page) is used
             size: short (a line or two; <= MAX_WORDS words) — footnotes and side notes are longer
  support    repeated      same text (numbers folded, fuzzy) at the same place on several pages
             page_number   a number (arabic/roman) whose difference to the PDF page index is shared with other
                           pages (printed page numbering), or — on top/bottom only — a lone number
             running_title the text repeats a heading of the book (running heads quote chapter/section titles)
             engine        Marker or Docling labelled it PageHeader/PageFooter
             position_twin (second pass, top/bottom, ONLY segments without readable text) at the same place — height band
             and horizontal position, directly or mirrored for left/right-hand pages — as furniture confirmed on
             another page. Position alone never removes text that was read: a page-top continuation line sits
             exactly where running heads sit on other pages
  Left/right sides (where exercise numbers and side labels live) need a strong signal: repeated or page_number.

Things that look like furniture but are not, and why they survive: footnotes (long, not repeated), side notes
(long, or not at the same place on several pages), equation numbers "(4.3)" (inside the frame; parentheses
are not a page-number form), a chapter-opening title (large, inside the frame of its own page and not at
the running-head position), exercise numbers in the margin (left/right need a strong signal).
Engine-labelled headers INSIDE the body frame are given back to the text (role cleared) — not excluded.
A segment no engine could read is furniture only by repetition/numbering of the other engine's reading or by a
position twin: an engine label alone does not remove unread print (it stays visible as an image excerpt).

Every exclusion is a document_structure fix with a stable key (dismiss = restore the segment) and the signals.
"""
from __future__ import annotations

import functools
import re
import statistics
from collections import Counter, defaultdict

from ..core.model import CAPTIONABLE_KINDS, EQUATION_KINDS, chosen_text
from ..core.reasons import flag
from ..core.text import fold, similarity, strip_marks, strip_markup
from .structure import key as skey

MAX_WORDS = 15
MAX_HEIGHT_FRAC = 0.05          # a furniture line is at most this share of the page height
SUBSTANTIAL_CHARS = 120         # body text blocks that define the frame
TOL_FRAC = 0.01                 # tolerance at the frame edge (share of page height/width): engine boxes overlap a little
SAME_PLACE = 0.03               # same place across pages: centre within 3 % of the page height/width
TEXT_SIMILAR = 0.7
FURNITURE_ROLES = ('page_header', 'page_footer')
_ARABIC = re.compile(r'^[\W_]*?(?:[-–—|•·]\s*)?(\d{1,4})(?:\s*[-–—|•·])?[\W_]*$')
_ROMAN = re.compile(r'^[\W_]*([ivxlcdm]{1,7})[\W_]*$', re.I)
_EDGE_NUMBER = re.compile(r'(?:^|\s)(\d{1,4})\s*$|^\s*(\d{1,4})(?:\s|$)')
_FOOTNOTE = re.compile(r'^\s*(?:[*†‡§¹²³⁴⁵⁶⁷⁸⁹]|\d{1,2}[.)]?\s+\D)')
_ROLE_DECISIONS = {'set_heading_level', 'move_after_block'}
_ENGINE_LABELS = {'PageHeader', 'PageFooter', 'page_header', 'page_footer'}


def _text(b) -> str:
    """The segment's text; for a segment the primary engine left EMPTY, the other engine's reading (Docling often
    reads running heads Marker returns blank) — used only as evidence here, never as content."""
    t = strip_markup(chosen_text(b))
    if t or b['chosen'].get('type') == 'text':
        return t
    return next((strip_markup(c['text']) for c in b.get('candidates', []) if (c.get('text') or '').strip()), '')


def _roman(s: str) -> int | None:
    vals = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100, 'd': 500, 'm': 1000}
    s = s.lower()
    total = 0
    for i, ch in enumerate(s):
        v = vals[ch]
        total += -v if i + 1 < len(s) and vals[s[i + 1]] > v else v
    return total or None


def page_number_value(t: str) -> tuple[int | None, bool]:
    """(number, lone): a lone page number ('375', '- 12 -', 'xiv') or one at the edge of a short running head
    ('9.9 ΤΥΠΙΚΟ ΦΟΡΤΙΟ 375'). Parenthesised numbers (equation numbers) are not page numbers."""
    if '(' in t or ')' in t:
        return None, False
    m = _ARABIC.match(t)
    if m:
        return int(m.group(1)), True
    m = _ROMAN.match(t)
    if m and len(t.strip()) <= 9:
        return _roman(m.group(1)), True
    m = _EDGE_NUMBER.search(t)
    if m and len(t.split()) <= MAX_WORDS:
        n = m.group(1) or m.group(2)
        if not re.match(r'^\s*\d+[.\-]\d', t):          # "9.9 Title" is a section number, not a page number
            return int(n), False
    return None, False


def _substantial(b, figures: bool = False) -> bool:
    """Content that defines the text frame. Figure boxes only when a page has nothing else: layout engines often
    draw picture boxes that swallow the margin (and the running head with it)."""
    if b.get('role') in FURNITURE_ROLES:
        return False
    if b['kind'] == 'table' or b['kind'] in EQUATION_KINDS:
        return True
    if b['kind'] in CAPTIONABLE_KINDS:
        return figures
    return len(_text(b)) >= SUBSTANTIAL_CHARS


MIRROR_MIN = 0.03         # odd/even frame difference (fraction of page) that counts as mirrored margins


def body_frames(blocks, dims) -> dict[int, list[float]]:
    """Per page: the frame around substantial content; pages without any use the book median (per odd/even side
    only when this book measurably has mirrored margins)."""
    frames = {}
    for p in {b['page'] for b in blocks}:
        sub = [b['geometry']['bbox'] for b in blocks if b['page'] == p and _substantial(b)] or \
              [b['geometry']['bbox'] for b in blocks if b['page'] == p and _substantial(b, figures=True)]
        if sub:
            frames[p] = [min(x[0] for x in sub), min(x[1] for x in sub), max(x[2] for x in sub), max(x[3] for x in sub)]
    rel = defaultdict(list)
    for p, f in frames.items():
        W, H = dims[p]
        rel[p % 2].append([f[0] / W, f[1] / H, f[2] / W, f[3] / H])
    median = {par: [statistics.median(c) for c in zip(*fs)] for par, fs in rel.items()}
    every = [f for fs in rel.values() for f in fs]
    book = [statistics.median(c) for c in zip(*every)] if every else [0.08, 0.09, 0.92, 0.91]
    # mirrored (odd/even) margins only when MEASURED in this book: both sides seen and their frames really differ
    mirrored = len(median) == 2 and max(abs(a - b) for a, b in zip(median[0], median[1])) > MIRROR_MIN
    for p in {b['page'] for b in blocks} - set(frames):
        m = median[p % 2] if mirrored else book
        W, H = dims[p]
        frames[p] = [m[0] * W, m[1] * H, m[2] * W, m[3] * H]
    return frames


def _side(bb, frame, W, H) -> str | None:
    tx, ty = TOL_FRAC * W, TOL_FRAC * H
    if bb[3] <= frame[1] + ty:
        return 'top'
    if bb[1] >= frame[3] - ty:
        return 'bottom'
    if bb[2] <= frame[0] + tx:
        return 'left'
    if bb[0] >= frame[2] - tx:
        return 'right'
    return None


def _deep_inside(bb, frame, H) -> bool:
    return bb[1] > frame[1] + 3 * TOL_FRAC * H and bb[3] < frame[3] - 3 * TOL_FRAC * H


def _place(bb, side, W, H) -> float:
    """Coordinate that must agree across pages: vertical centre for top/bottom, horizontal for left/right."""
    return ((bb[1] + bb[3]) / 2 / H) if side in ('top', 'bottom') else ((bb[0] + bb[2]) / 2 / W)


def _key(t: str) -> str:
    """Comparison form: no accents, one case, Greek/Latin lookalikes unified (running heads are often in
    capitals while the heading they quote is not; OCR mixes scripts in capitals)."""
    return fold(strip_marks(t).upper())


@functools.lru_cache(maxsize=65536)
def _repeat_text(t: str) -> str:                     # pure, and compared pairwise across all pages: cached
    return re.sub(r'\d+', '#', _key(t)).strip()


def _engine_says(b) -> bool:
    if b.get('role') in FURNITURE_ROLES and b.get('source') != 'region':
        return True
    return any(c.get('label') in _ENGINE_LABELS for c in b.get('candidates', []))


def _human_marked(b) -> bool:
    """A reviewer re-tagged this box as header/footer (reviewer region with that label)."""
    return b.get('source') == 'region' and b.get('role') in FURNITURE_ROLES


def classify(blocks, dims, dismissed) -> list[dict]:
    """Mutates blocks: furniture gets role + `excluded`; returns document_structure fixes."""
    pages = sorted({b['page'] for b in blocks})
    frames = body_frames(blocks, dims)
    min_repeat = max(2, min(3, (len(pages) + 1) // 2))
    cands = []
    for b in blocks:
        W, H = dims[b['page']]
        bb = b['geometry']['bbox']
        if any(d.get('action') in _ROLE_DECISIONS for d in b.get('decisions', [])) or b['kind'] in CAPTIONABLE_KINDS:
            continue
        side = _side(bb, frames[b['page']], W, H)
        t = _text(b)
        short = len(t.split()) <= MAX_WORDS and (bb[3] - bb[1]) <= MAX_HEIGHT_FRAC * H
        if _human_marked(b):
            cands.append({'b': b, 'side': side or 'top', 't': t, 'human': True, 'place': _place(bb, side or 'top', W, H)})
        elif side and short:
            cands.append({'b': b, 'side': side, 't': t, 'place': _place(bb, side, W, H)})
        elif _engine_says(b) and not side and _deep_inside(bb, frames[b['page']], H):
            b['role'] = None                              # engine header well inside the text frame: keep as text
            flag(b, 'ENGINE_FURNITURE_IN_BODY')
    # printed page numbering: offsets shared by >= 2 pages
    offsets = Counter()
    for c in cands:
        n, lone = page_number_value(c['t']) if c['t'] else (None, False)
        c['num'], c['lone'] = n, lone
        if n is not None:
            offsets[n - c['b']['page']] += 1
    shared = {o for o, k in offsets.items() if k >= 2}
    headings = [_tokens(_text(b)) for b in blocks if b['kind'] == 'heading' and not any(c['b'] is b for c in cands)]
    for c in cands:                                       # pass 1: content signals
        c['sig'] = ['reviewer'] if c.get('human') else _signals(c, cands, shared, headings, min_repeat)
    confirmed = [c for c in cands if c['sig']]
    for c in cands:                                       # pass 2: same place as furniture confirmed elsewhere
        if c['sig'] or c['t'] or c['side'] not in ('top', 'bottom'):
            continue                                      # position alone never removes text that was read
        if any(o['b']['page'] != c['b']['page'] and _same_place(c, o, dims) for o in confirmed):
            c['sig'] = ['position_twin']
    fixes = []
    for c in cands:
        b, side, t, sig = c['b'], c['side'], c['t'], c['sig']
        if not sig:
            continue
        kind = 'page_number' if c['num'] is not None and c['lone'] else ('running_title' if 'running_title' in sig else 'running_text')
        role = 'page_header' if side == 'top' else 'page_footer' if side == 'bottom' else 'page_header'
        k = skey('doc:furniture', [b['id']])
        if k in dismissed:
            if b.get('role') in FURNITURE_ROLES and not c.get('human'):
                b['role'] = None                          # the reviewer said: this is text
            continue
        b['role'] = role
        b['excluded'] = {'as': kind, 'side': side, 'signals': sig, 'key': k}
        flag(b, 'EXCLUDED_AS_PAGE_FURNITURE', side=side, signals=sig)
        b['review'], b['display_crop'] = 'not_needed', False   # nothing of it is used: nothing to review
        fixes.append({'key': k, 'kind': 'furniture', 'rule': f'outside the text frame ({side}); ' + ', '.join(sig),
                      'blocks': [b['id']], 'pages': [b['page']], 'before': None, 'after': kind,
                      'subject': t[:90], 'before_el': 'στο κείμενο', 'after_el': f'εκτός εξόδου ({_KIND_EL.get(kind, kind)})',
                      'explanation_el': _explain(kind, side, sig, t), 'evidence': {'signals': sig, 'side': side}})
    return fixes


def _signals(c, cands, shared, headings, min_repeat) -> list[str]:
    b, side, t = c['b'], c['side'], c['t']
    sig = []
    if t:
        twins = {o['b']['page'] for o in cands if o['side'] == side and o['t'] and o['b']['page'] != b['page']
                 and abs(o['place'] - c['place']) <= SAME_PLACE
                 and similarity(_repeat_text(o['t']), _repeat_text(t)) >= TEXT_SIMILAR}
        if len(twins) + 1 >= min_repeat:
            sig.append('repeated')
    if c['num'] is not None and c['num'] - b['page'] in shared:
        sig.append('page_number')
    elif c['lone'] and side in ('top', 'bottom'):
        sig.append('page_number_form')
    tk = _tokens(t)
    if len(tk) >= 2 and any(h and len(tk & h) >= 0.8 * len(tk) for h in headings):
        sig.append('running_title')
    if _engine_says(b) and t:
        sig.append('engine')       # an UNREAD segment (no text from any engine) is not excluded on a label alone: a
                                   # chapter-opening band ("ΚΕΦΑΛΑΙΟ 5") is labelled PageHeader too; it needs a
                                   # position twin of confirmed furniture (pass 2)
    strong = {'repeated', 'page_number'}
    if (side in ('left', 'right') or (side == 'bottom' and _FOOTNOTE.match(t) and not c['lone'])) \
            and not strong & set(sig):
        return []                                     # side labels / footnote-like text need a strong signal
    return sig


def _same_place(c, o, dims) -> bool:
    """Same side, same height band, and the same horizontal position — directly or mirrored (verso/recto)."""
    if o['side'] != c['side'] or abs(o['place'] - c['place']) > SAME_PLACE:
        return False
    W, Wo = dims[c['b']['page']][0], dims[o['b']['page']][0]
    a, q = c['b']['geometry']['bbox'], o['b']['geometry']['bbox']
    ca, cq = (a[0] + a[2]) / 2 / W, (q[0] + q[2]) / 2 / Wo
    return min(abs(ca - cq), abs(ca - (1 - cq))) <= 2 * SAME_PLACE


_SIDE_EL = {'top': 'πάνω', 'bottom': 'κάτω', 'left': 'αριστερό', 'right': 'δεξί'}
_SIG_EL = {'repeated': 'επαναλαμβάνεται στην ίδια θέση σε πολλές σελίδες', 'page_number': 'ακολουθεί την αρίθμηση σελίδων',
           'page_number_form': 'είναι μεμονωμένος αριθμός', 'running_title': 'επαναλαμβάνει τίτλο του βιβλίου',
           'engine': 'η μηχανή διάταξης το χαρακτήρισε κεφαλίδα/υποσέλιδο', 'reviewer': 'το σημείωσες εσύ',
           'position_twin': 'ίδια θέση με κεφαλίδες άλλων σελίδων'}
_KIND_EL = {'page_number': 'Αριθμός σελίδας', 'running_title': 'Τίτλος κεφαλίδας', 'running_text': 'Κεφαλίδα/υποσέλιδο'}


def _explain(kind, side, sig, t) -> str:
    return (f'{_KIND_EL[kind]} «{t[:40]}» στο {_SIDE_EL.get(side, "")} περιθώριο: ' + '; '.join(_SIG_EL[s] for s in sig)
            + '. Εξαιρείται από την έξοδο (μένει στο canonical.json).')


def _tokens(t: str) -> set:
    return {w for w in re.findall(r'\w+', _key(t)) if not w.isdigit() and len(w) > 1}
