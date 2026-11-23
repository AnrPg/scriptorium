"""Stage: book-wide section hierarchy (unit / chapter / section / subsection ...).

Layout engines see one page at a time and their heading tags are not comparable across pages (the same kind of
heading gets h2 on one page and h3 on another). The hierarchy is therefore inferred from the BOOK:

  Style: every heading's print is measured on the scan (pipeline.page -> typography: line height, lines, stroke
  weight, ink colour) RELATIVE to the body text of its page (relative_typography), and grouped book-wide into
  STYLES: same relative size (within SIZE_TOL) and same case. Weight and colour are reported as evidence (to the
  reviewer and the model) but do not split styles: in photographed pages they vary with lighting more than
  between styles; their use must first prove itself (calibration), like any hint. An ALL-CAPS line's ink band is only
  the cap height, so its size is scaled by CAPS_FACTOR to compare with mixed-case titles. Styles are ordered by
  prominence (bigger first; at equal size capitals first). Same style = same level, everywhere in the book.
  Headings without a measurement fall back to Marker's tag ranked book-wide ('engine_rank').

  Level anchors for a style, strongest first (a heading's own evidence beats its style's):
    1. reviewer decision (set_heading_level; 0 = "not a heading")            -> 'human'; its STYLE follows
       ('human_style') unless the reviewer set different levels within one style
    2. numbering at the start: "5", "5.1", "5.1.2"                           -> 'numbering'; numbered headings of a
       style vote its level ('style_of_numbered', >= 2 votes, >= 80 %)
    3. running heads (page furniture quoting titles), grouped into series by their PLACE on the page: a series
       that always quotes the same title is the outer level, a series whose title changes is the level below
       -> 'running_head' (only when both exist; any layout: alternating, centred, top or bottom)
    4. a chapter label as the whole heading ("ΚΕΦΑΛΑΙΟ 5", "Chapter 3", "Unit 2")   -> 'chapter_label' (level 1)
    5. a chapter outline (list lines naming >= 2 headings, hidden ones included): siblings -> 'outline'
    6. the local model's majority vote in the second pass (structure_review)      -> 'model': a reviewer-like
       hint, calibrated on the reviewer's decisions like every other source, never applied alone
  All anchors are OPTIONAL evidence: a book without running heads, numbering or chapter labels simply has fewer
  anchors, and wordings the lexicon does not know are left to the model's second pass (structure_review), which
  only proposes. Runs in the projection AFTER the reviewer's decisions: reviewer levels anchor their style,
  hidden segments give no evidence, segments only excluded from the output (furniture) still do.
  Styles without an anchor take the next level below the more prominent style before them, never deeper than
  the next anchored style below them ('typography').
  Applying: STYLE evidence (same style as a settled heading elsewhere, or the measured typography of a distinct
  style) applies on its own as an automatic, undoable correction — books are consistent in their typography —
  unless this book's calibration shows the source errs (DISTRUST_ERROR); other single hints (running heads,
  outlines, chapter labels, the model) need a second, independent kind of evidence or a calibrated source.
  Structural repair: an inferred heading more than one level deeper than its parent moves up to parent + 1
  ('level_jump_closed'); numbered and reviewer headings keep their level.
Headings that start with a word and a number ("ΠΑΡΑΔΕΙΓΜΑ 4.3", "Exercise 2-1") are labelled elements (boxes)
placed one level below the current section. Headings inside a box (pipeline.document.box_headings, second pass)
are sub-headings of that box ('box_heading'). Every inferred level is a dismissable document fix (dismissing the
style evidence for one heading leaves it to its own typography).
Every block gets section_path (ids of enclosing headings); canonical 'sections' is the table of contents.
"""
from __future__ import annotations

import functools
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from ..core.labels import LABELLED, NUMBERED_HEADING
from ..core.model import MAX_HEADING_LEVEL, chosen_text
from ..core.text import strip_markup
from .structure import key as skey

STYLE_MIN_VOTES = 2
STYLE_AGREEMENT = 0.8
CAPS_FACTOR = 1.35          # mixed-case ink band (ascenders + descenders) / all-caps ink band (cap height)
SIZE_TOL = 0.12             # sizes within 12 % are the same style
MAX_TITLE_LINES = 4         # longer blocks are not measured as titles
RUNNING_MATCH = 0.8
CHAPTER_LABEL = re.compile(r'^\W*(κεφαλαιο|chapter|unit|ενοτητα|μερος|part|module)\s+([0-9]+|[ivxlc]+)\W*$', re.I)
INFERRED = ('engine_rank', 'default', 'style_of_numbered', 'typography', 'human_style', 'running_head', 'chapter_label',
            'style_of_checked', 'shifted_with_parent')

_NUMBERED = NUMBERED_HEADING
_LABELLED = LABELLED
_CONFUSABLE = str.maketrans('ΑΒΕΖΗΙΚΜΝΟΡΤΥΧ', 'ABEZHIKMNOPTYX')


def _title(b) -> str:
    return strip_markup(chosen_text(b))


def _caps(t: str) -> bool:
    letters = [c for c in t if c.isalpha()]
    return len(letters) >= 2 and all(c.isupper() for c in letters)


@functools.lru_cache(maxsize=65536)
def skeleton(t: str) -> str:
    """Case-, accent- and script-insensitive form for matching OCR readings of the same title (Greek capitals
    that look Latin are mapped to Latin: 'ΚΑΙ' ~ 'KAI'). Cached: the same titles and lines are compared many times."""
    t = unicodedata.normalize('NFD', t or '')
    t = ''.join(c for c in t if not unicodedata.combining(c)).upper().translate(_CONFUSABLE)
    return re.sub(r'[^0-9A-ZΑ-Ω]', '', t)


def contains(head: str, title: str) -> bool:
    """The running head quotes the title (fuzzy: OCR of small print is noisy): some window of the head, as long
    as the title, has SequenceMatcher ratio >= RUNNING_MATCH with it.

    Fast but EXACT: ratio = 2M/(|w|+|t|), and the matches M of any window are bounded by the multiset overlap of the
    window (hence of the whole head) with the title. A head that cannot reach the threshold even on that bound is
    rejected without running SequenceMatcher; per window, quick_ratio (the same bound) is checked first. The
    matcher indexes the title once (set_seq2) and only swaps windows. Results are identical to the plain loop."""
    h, t = skeleton(head), skeleton(title)
    if len(t) < 6 or len(t) > len(h) + 2:
        return False
    if t in h:
        return True
    n = len(t)
    windows = [h[i:i + n] for i in range(0, max(1, len(h) - n + 1))]
    best_len = len(windows[0]) + n                        # all windows have the same length
    if 2 * _overlap(h, t) < RUNNING_MATCH * best_len:     # upper bound over every window
        return False
    sm = SequenceMatcher(None, '', t)                     # same settings as SequenceMatcher(None, w, t)
    for w in windows:
        sm.set_seq1(w)
        if sm.quick_ratio() >= RUNNING_MATCH and sm.ratio() >= RUNNING_MATCH:
            return True
    return False


@functools.lru_cache(maxsize=262144)
def _overlap(a: str, b: str) -> int:
    """Characters the two strings share, as multisets (an upper bound of SequenceMatcher's matches)."""
    return sum((Counter(a) & Counter(b)).values())


MIN_BAND_PX = 8             # fewer pixels per text line than this: the measurement is not reliable (low resolution)


def relative_typography(blocks: list[dict], page_refs: dict) -> None:
    """blk['typography_rel'] for measured titles: print size, stroke weight and ink colour RELATIVE to the body
    text of the same page (pipeline.page typography_ref), or of the book when the page has no body text. Relative
    measures cancel what differs between pages and books but says nothing about the style: photo distance, scan
    resolution, page size, lighting, a book set in small type. Unreliable measurements (too few pixels) are dropped."""
    refs = {int(p): r for p, r in (page_refs or {}).items() if r}
    book = None
    if refs:
        vals = list(refs.values())
        med = lambda k: sorted(r[k] for r in vals)[len(vals) // 2]
        book = {'text_h': med('text_h'), 'density': med('density'),
                'ink_rgb': [sorted(r['ink_rgb'][i] for r in vals)[len(vals) // 2] for i in range(3)]}
    for b in blocks:
        ty = b.get('typography')
        b.pop('typography_rel', None)
        if not ty or not ty.get('text_h') or ty.get('band_px', MIN_BAND_PX) < MIN_BAND_PX:
            continue
        ref = refs.get(b['page']) or book
        if not ref:
            continue
        b['typography_rel'] = {
            'size': round(ty['text_h'] / ref['text_h'], 3), 'ref': 'page' if b['page'] in refs else 'book',
            'weight': round(ty['density'] / ref['density'], 3) if ref.get('density') else None,
            'colour': round(sum((a - c) ** 2 for a, c in zip(ty['ink_rgb'], ref['ink_rgb'])) ** 0.5)}


def _size(b) -> float | None:
    """Comparable print size of a title: relative to its page's body text when known (see relative_typography),
    else the absolute height in page units; all-caps scaled to mixed-case equivalents."""
    ty = b.get('typography') or {}
    if not ty.get('text_u') or ty.get('lines', 1) > MAX_TITLE_LINES or ty.get('band_px', MIN_BAND_PX) < MIN_BAND_PX:
        return None
    rel = b.get('typography_rel')
    base = rel['size'] if rel else ty['text_u']
    return base * (CAPS_FACTOR if _caps(_title(b)) else 1.0)


def styles(headings: list[dict]) -> dict[str, tuple]:
    """heading id -> style; style = ('t', size class, caps) ordered by prominence, or ('m', marker tag, caps)."""
    sized = sorted(((s, b) for b in headings if (s := _size(b))), key=lambda x: -x[0])
    out, cls, ref = {}, -1, None
    for s, b in sized:
        if ref is None or s < ref * (1 - SIZE_TOL):
            cls, ref = cls + 1, s
        out[b['id']] = ('t', cls, 0 if _caps(_title(b)) else 1)
    for b in headings:
        if b['id'] not in out and (tag := b.get('engine_heading_levels', {}).get('marker')) is not None:
            out[b['id']] = ('m', tag, 0 if _caps(_title(b)) else 1)
    return out


def running_heads(blocks: list[dict]) -> list[dict]:
    """Running heads/feet (page furniture that is not a page number) as evidence: {page, text, series}. A SERIES is
    the place on the page (side + horizontal position): books put heads left/right, centred, top or bottom, on
    alternating pages or on all — no layout is assumed; the place groups what belongs together. Hidden segments
    (the reviewer removed the box) are not here; segments merely EXCLUDED from the output still are."""
    width = defaultdict(float)
    for b in blocks:
        width[b['page']] = max(width[b['page']], b['geometry']['bbox'][2])
    out = []
    for b in blocks:
        ex = b.get('excluded') or {}
        if ex.get('as') not in ('running_title', 'running_text') and b.get('role') not in ('page_header', 'page_footer'):
            continue
        if ex.get('as') == 'page_number':
            continue
        t = _title(b) or next((strip_markup(c['text']) for c in b.get('candidates', []) if (c.get('text') or '').strip()), '')
        if not t or re.fullmatch(r'[\W\divxlc]*', t, re.I):
            continue
        bb = b['geometry']['bbox']
        side = ex.get('side') or ('bottom' if b.get('role') == 'page_footer' else 'top')
        xc = ((bb[0] + bb[2]) / 2) / (width[b['page']] or 1)
        out.append({'page': b['page'], 'text': t, 'series': (side, min(3, int(xc * 4)))})
    return out


def _running_anchors(headings, heads) -> dict[str, int]:
    """heading id -> level from running heads. Within one series (same place on the page) the quoted title either
    CHANGES (>= 2 different titles: the series follows the sections) or stays the same. Titles of a changing
    series are one level below titles quoted only by an unchanging series (e.g. the chapter). Needs both;
    otherwise running heads give no level (style consistency still applies)."""
    series = defaultdict(lambda: defaultdict(set))       # series -> heading id -> pages quoting it
    for b in headings:
        t = _title(b)
        for h in heads:
            if contains(h['text'], t):
                series[h['series']][b['id']].add(h['page'])
    changing = [s for s in series.values() if len(s) > 1]
    inner = {h for s in changing for h in s}
    outer = {h for s in series.values() if len(s) == 1 for h in s} - inner
    if not changing or not outer:
        return {}
    return {**{h: 1 for h in outer}, **{h: 2 for h in inner}}


# ---------------------------------------------------------------- hints -> checked decision -> proposals
SEPARATION = 0.2            # a size class is distinct when it is >= 20 % larger than the next one (batch check)
TRUST_MIN = 10              # calibration: outcomes needed before a hint source applies on its own
TRUST_MAX_ERROR = 0.10      # ... and its 95 % upper error bound
DISTRUST_ERROR = 0.20       # a style source erring more often than this (>= TRUST_MIN outcomes) stops applying alone
FAMILY = {'human_style': 'style', 'style_of_numbered': 'style', 'typography': 'style', 'running_head': 'running',
          'chapter_label': 'label', 'engine_rank': 'engine', 'outline': 'outline', 'model': 'model',
          'style_of_checked': 'sibling'}


def revoke_key(block_id: str, level) -> str:
    """Undo of ONE automatic level of one heading (undoing level 2 does not block a later, different level 3)."""
    return skey('doc:heading_style', [block_id, str(level)])


def jump_key(block_id: str, before, after) -> str:
    """Undo of ONE jump repair (from this level to that one), not of every future repair of the heading."""
    return skey('doc:level_jump', [block_id, str(before), str(after)])


def _engine_levels(headings) -> dict[str, int]:
    """Baseline: Marker's tag refined by capitals, ranked over the book (what the engines alone say)."""
    def sig(b):
        tag = b.get('engine_heading_levels', {}).get('marker')
        return None if tag is None else (tag, 0 if _caps(_title(b)) else 1)
    rank = {s: i + 1 for i, s in enumerate(sorted({sig(b) for b in headings if sig(b)}))}
    return {b['id']: rank[sig(b)] for b in headings if sig(b)}


def style_quality(headings, style) -> dict:
    """Batch statistics of the measured styles: members, size spread, and SEPARATION from the next smaller class.
    A style whose sizes blur into its neighbour is not distinct: its typography hint is not used."""
    sizes = defaultdict(list)
    for b in headings:
        s = style.get(b['id'])
        if s and s[0] == 't' and (z := _size(b)):
            sizes[s[1]].append(z)
    classes = sorted(sizes)
    out = {}
    for i, c in enumerate(classes):
        lo, hi = min(sizes[c]), max(sizes[c])
        nxt = max(sizes[classes[i + 1]]) if i + 1 < len(classes) else None
        prv = min(sizes[classes[i - 1]]) if i > 0 else None
        sep = min([x for x in ((lo / nxt - 1) if nxt else None, (prv / hi - 1) if prv else None) if x is not None], default=1.0)
        out[c] = {'members': len(sizes[c]), 'size': [round(lo, 2), round(hi, 2)], 'separation': round(sep, 3),
                  'distinct': sep >= SEPARATION}
    return out


OUTLINE_MIN = 2              # an outline (list of titles) must name at least this many headings of the book


def outlines(blocks: list[dict], hidden_texts: list) -> list[list[str]]:
    """Per page, the texts of list-like lines (list items, short paragraphs/excerpts; the reviewer's HIDDEN
    segments included — hidden means "not in the output", the print is still evidence): a chapter outline names
    its sections."""
    per = defaultdict(list)
    for b in blocks:
        t = _title(b) or next((strip_markup(c['text']) for c in b.get('candidates', []) if (c.get('text') or '').strip()), '')
        if t and len(t) <= 120 and b['kind'] != 'heading' and (b.get('role') == 'list_item' or b['kind'] in ('paragraph', 'excerpt')):
            per[b['page']].append(t)
    for p, t in hidden_texts:
        if t and len(t) <= 120:
            per[p].append(strip_markup(t))
    return list(per.values())


def _outline_siblings(headings, page_lines) -> list[set]:
    """Sets of heading ids that one page's outline lines name together (>= OUTLINE_MIN): siblings."""
    out = []
    heads = [(b['id'], t, len(skeleton(t))) for b in headings for t in (_title(b),)]   # titles computed once
    for lines in page_lines:
        sized = [(ln, len(skeleton(ln))) for ln in lines]
        named = {i for i, t, lt in heads for ln, ll in sized          # cheap length test first, then the match
                 if ll <= lt + 8 and contains(ln, t)}
        if len(named) >= OUTLINE_MIN:
            out.append(named)
    return out


def _hints(headings, style, quality, heads, exclude: str | None = None, outline_sets=(), model=None) -> dict[str, dict[str, int]]:
    """heading id -> {source: level}. `exclude`: compute as if this heading's own reviewer decision did not exist
    (leave-one-out, for calibration)."""
    order = sorted(set(style.values()), key=lambda s: (s[0] == 'm', s[1], s[2]))
    by_style = defaultdict(list)
    for b in headings:
        if b['id'] in style:
            by_style[style[b['id']]].append(b)
    human = defaultdict(Counter)
    for b in headings:
        if b.get('heading_level_source') == 'human' and b['id'] in style and b.get('heading_level') and b['id'] != exclude:
            human[style[b['id']]][b['heading_level']] += 1
    votes = defaultdict(Counter)
    for b in headings:
        m = _NUMBERED.match(_title(b))
        if m and b['id'] in style:
            votes[style[b['id']]][len(re.split(r'[.\-]', m.group(1)))] += 1
    run = _running_anchors([b for b in headings if b['id'] in style], heads)
    anchor = {}
    for s in order:
        if len(human[s]) == 1:
            anchor[s] = next(iter(human[s]))
        elif (c := votes[s]) and sum(c.values()) >= STYLE_MIN_VOTES and c.most_common(1)[0][1] >= STYLE_AGREEMENT * sum(c.values()):
            anchor[s] = c.most_common(1)[0][0]
    rel, cur = {}, 0                                      # typography: levels by prominence between anchors
    for i, s in enumerate(order):
        if s in anchor:
            rel[s] = anchor[s]
        else:
            below = [anchor[x] for x in order[i + 1:] if x in anchor]
            rel[s] = max(1, min(cur + 1, below[0] if below else 10 ** 6))
        cur = rel[s]
    engine = _engine_levels(headings)
    out = {}
    for b in headings:
        h, s = {}, style.get(b['id'])
        if b['id'] in engine:
            h['engine_rank'] = engine[b['id']]
        if s is not None:
            if len(human[s]) == 1:
                h['human_style'] = next(iter(human[s]))
            if s in anchor and votes[s]:
                h['style_of_numbered'] = anchor[s]
            if s[0] == 't' and (quality.get(s[1]) or {}).get('distinct'):
                h['typography'] = rel[s]
        if b['id'] in run:
            h['running_head'] = run[b['id']]
        if CHAPTER_LABEL.match(strip_marks_lower(_title(b))):
            h['chapter_label'] = 1
        if model and model.get(b['id']):                  # the local model's majority vote (2nd pass) as one more hint
            h['model'] = model[b['id']]
        out[b['id']] = h
    fixed = {b['id']: b['heading_level'] for b in headings if b['id'] != exclude and b.get('heading_level')
             and (b.get('heading_level_source') == 'human' or _NUMBERED.match(_title(b)))}
    for sib in outline_sets:                              # an outline's titles are siblings: one settled sibling's
        known = {fixed[x] for x in sib if x in fixed}     # level (reviewer or numbering) is the hint for the others
        if len(known) == 1:
            lv = known.pop()
            for x in sib:
                if x in out and x not in fixed:
                    out[x]['outline'] = lv
    return out


def calibration(headings, style, quality, heads, dismissed, proposals_seen, outline_sets=(), model=None) -> list[tuple[str, str]]:
    """Outcomes of the hint sources against the reviewer: for every heading whose level the reviewer set, each
    source's hint (computed WITHOUT that decision) is right or wrong; a rejected proposal is wrong for its sources."""
    outs = []
    for b in headings:
        if b.get('heading_level_source') != 'human' or not b.get('heading_level'):
            continue
        for src, lv in _hints(headings, style, quality, heads, b['id'], outline_sets, model)[b['id']].items():
            outs.append((src, 'right' if lv == b['heading_level'] else 'wrong'))
    for p in proposals_seen:
        if p['key'] in dismissed:
            outs += [(src, 'wrong') for src in p['sources']]
    return outs


def source_stats(outs) -> dict:
    from ..core.stats import wilson_upper
    agg = {}
    for src, o in outs:
        a = agg.setdefault(src, {'n': 0, 'wrong': 0})
        a['n'] += 1
        a['wrong'] += o == 'wrong'
    for a in agg.values():
        a['error_upper'] = round(wilson_upper(a['wrong'], a['n']), 3)
        a['trusted'] = a['n'] >= TRUST_MIN and a['error_upper'] <= TRUST_MAX_ERROR
    return agg


def infer_levels(headings: list[dict], dismissed=frozenset(), heads: list | None = None, prior=None,
                 page_lines=(), model=None) -> dict:
    """Decide each inferred heading level from its hints; returns {'proposals', 'stats', 'styles', 'outcomes'}.
    A hint APPLIES only when it passes a check: its source is calibrated (trusted on this reviewer's decisions,
    incl. calibration books), or two independent kinds of evidence (style / running heads / chapter label /
    engine) agree. Otherwise the engine baseline stays and the hint becomes a PROPOSAL, grouped by style and
    level, for the reviewer (or the model's second pass) to accept or reject."""
    heads = heads or []
    eligible = [b for b in headings if b.get('heading_level_source') != 'box_heading'
                and not (_LABELLED.match(_title(b)) and not _NUMBERED.match(_title(b)))]
    style = styles(eligible)
    quality = style_quality(eligible, style)
    outline_sets = _outline_siblings(eligible, page_lines)
    hints = _hints(eligible, style, quality, heads, None, outline_sets, model)
    sugg_seen = []
    pending = defaultdict(list)                           # (level, sources) -> headings proposed
    stats = None

    def decide(b, h, trusted, distrusted=frozenset(), independent_only=False):
        """The level a heading's hints support, by tier: a calibrated source (4); independent kinds of evidence
        agreeing (3); its STYLE with a settled level elsewhere — reviewer, checked or numbered (2); its measured
        typography alone in a distinct style (1). Style evidence applies without the engine's agreement unless
        the calibration on this book's decisions shows it errs (distrusted); other single hints never apply alone."""
        by_level = defaultdict(set)
        for src, lv in h.items():
            by_level[lv].add(src)
        best = None
        for lv, srcs in by_level.items():
            fams = {FAMILY[x] for x in srcs}
            tr = [x for x in srcs if x in trusted]
            styled = [x for x in ('human_style', 'style_of_checked', 'style_of_numbered') if x in srcs and x not in distrusted]
            if tr:
                tier, first = 4, tr[0]
            elif len(fams) >= 2:
                tier, first = 3, next(x for x in srcs if FAMILY[x] != 'engine')
            elif independent_only:
                continue
            elif styled:
                tier, first = 2, styled[0]
            elif 'typography' in srcs and 'typography' not in distrusted:
                tier, first = 1, 'typography'
            else:
                continue
            cand = (tier, len(fams), lv, sorted(srcs, key=lambda x: x != first))
            if best is None or cand[:2] > best[:2]:
                best = cand
        return best
    # same style = same level, carried from CHECKED levels: when a heading of a style passed the check (e.g. its
    # typography and the engine agree), that independent confirmation is evidence for every heading of the style
    checked = defaultdict(set)
    for b in eligible:
        if b.get('heading_level_source') in ('human', 'box_heading') or _NUMBERED.match(_title(b)) or b['id'] not in style:
            continue
        best = decide(b, hints.get(b['id'], {}), set(), independent_only=True)   # confirmed independently only
        if best and revoke_key(b['id'], best[2]) not in dismissed:
            checked[style[b['id']]].add((best[2], b['id']))
    for b in eligible:
        s = style.get(b['id'])
        if s is None or (s[0] == 't' and not (quality.get(s[1]) or {}).get('distinct')):
            continue
        lv = {x for x, i in checked[s] if i != b['id']}
        if len(lv) == 1 and b['id'] in hints:
            hints[b['id']]['style_of_checked'] = lv.pop()
    outs_prior = list(prior or [])
    revoked: list = []
    for _ in range(2):                                    # stats need the proposals of this run (rejections)
        stats = source_stats(calibration(eligible, style, quality, heads, dismissed, sugg_seen, outline_sets, model)
                             + [(src, 'wrong') for _, _, srcs in revoked for src in srcs] + outs_prior)
        revoked = []
        trusted = {s for s, a in stats.items() if a['trusted']}
        distrusted = {s for s, a in stats.items() if a['n'] >= TRUST_MIN and a['wrong'] / a['n'] > DISTRUST_ERROR}
        pending.clear()
        for b in headings:
            if b.get('heading_level_source') in ('human', 'box_heading'):
                continue
            t = _title(b)
            m = _NUMBERED.match(t)
            h = hints.get(b['id'], {})
            if m:
                b['heading_level'], b['heading_level_source'] = len(re.split(r'[.\-]', m.group(1))), 'numbering'
                continue
            if _LABELLED.match(t) and not CHAPTER_LABEL.match(strip_marks_lower(t)):
                b['heading_level'], b['heading_level_source'] = None, 'labelled_element'   # resolved in annotate
                continue
            best = decide(b, h, trusted, distrusted)
            if best and revoke_key(b['id'], best[2]) in dismissed:
                # the reviewer UNDID this automatic level: back to the original state exactly (the engine's level,
                # 'revoked': no other automatic repair rewrites it), and the evidence that caused it was wrong
                revoked.append((b['id'], best[2], [x for x in best[3] if x != 'engine_rank']))
                b['heading_level'], b['heading_level_source'] = h.get('engine_rank') or best[2], 'revoked'
                b['heading_hints'] = h
                if b['id'] in style:
                    b['heading_style'] = list(style[b['id']])
                continue
            if best:
                b['heading_level'], b['heading_level_source'] = best[2], best[3][0]
                b['heading_evidence'] = best[3]
            elif 'engine_rank' in h:
                b['heading_level'], b['heading_level_source'] = h['engine_rank'], 'engine_rank'
            else:
                b['heading_level'], b['heading_level_source'] = h.get('typography', 1), 'typography' if 'typography' in h else 'default'
            b['heading_hints'] = h
            if b['id'] in style:
                b['heading_style'] = list(style[b['id']])
            for lv in {v for x, v in h.items() if x != 'engine_rank'}:
                if lv != b['heading_level'] and not any(r[0] == b['id'] and r[1] == lv for r in revoked):
                    pending[(lv, tuple(sorted(x for x, v in h.items() if v == lv and x != 'engine_rank')))].append(b['id'])
        props = []
        for (lv, srcs), ids in pending.items():
            ids = list(dict.fromkeys(ids))
            k = skey('doc:heading_proposal', ids + [str(lv)])
            props.append({'key': k, 'level': lv, 'sources': list(srcs), 'members': ids})
        sugg_seen = props
    return {'proposals': [p for p in sugg_seen if p['key'] not in dismissed], 'stats': stats,
            'styles': {str(k): v for k, v in quality.items()},
            'outcomes': calibration(eligible, style, quality, heads, dismissed, sugg_seen, outline_sets, model)
            + [(src, 'wrong') for _, _, srcs in revoked for src in srcs],
            'revoked': [{'block': i, 'level': lv, 'sources': srcs} for i, lv, srcs in revoked]}


SHIFTABLE = ('engine_rank', 'default', 'typography', 'style_of_numbered', 'human_style', 'style_of_checked',
             'running_head', 'chapter_label', 'level_jump_closed')


def shift_subtrees(headings: list[dict], dismissed=frozenset(), base: dict | None = None) -> None:
    """A reviewer who moves a heading by N levels moves its SECTION: every inferred heading of its subtree (as it
    was before the change: the following headings deeper than its inferred level) moves by the same N, so the
    relative structure is kept (H4 -> H2 makes its H6 sub-section an H4). Nested reviewer decisions take over for
    their own subtree. Numbered headings keep their numbering level; labelled elements and box sub-headings follow
    by construction (one level below their section / box). Each shift is reported and undoable per level."""
    base = base or {}
    stack: list[tuple[int, int, str]] = []                   # (base level, delta, heading id)
    for b in headings:
        lv = b.get('heading_level')
        if lv is None or b.get('heading_level_source') in ('labelled_element', 'box_heading'):
            continue
        orig = base.get(b['id']) or b.get('heading_level_inferred') or lv
        while stack and orig <= stack[-1][0]:
            stack.pop()
        if b.get('heading_level_source') == 'human':
            stack.append((orig, lv - orig, b['id']))
            continue
        if not stack or not stack[-1][1] or b.get('heading_level_source') not in SHIFTABLE:
            continue
        _, delta, parent = stack[-1]
        new = max(1, min(MAX_LEVEL, orig + delta))          # its level WITHOUT the decision, moved by the same amount
        if new == lv or revoke_key(b['id'], new) in dismissed:
            continue                                         # (already there — e.g. via its style — or undone)
        b['heading_shift'] = {'from': lv, 'by': delta, 'parent': parent}
        b['heading_level'], b['heading_level_source'] = new, 'shifted_with_parent'


MAX_LEVEL = MAX_HEADING_LEVEL


def strip_marks_lower(t: str) -> str:
    t = unicodedata.normalize('NFD', t or '')
    return ''.join(c for c in t if not unicodedata.combining(c)).lower()


def annotate(blocks: list[dict], dismissed=frozenset(), prior=None, report: dict | None = None,
             hidden_texts: list | None = None, model: dict | None = None, page_refs: dict | None = None) -> list[dict]:
    """Mutates blocks; returns the table of contents. `report` receives the hint proposals and statistics;
    `prior`: hint outcomes of calibration books."""
    headings = [b for b in blocks if b['kind'] == 'heading' and b.get('role') not in ('page_header', 'page_footer')
                and b['chosen']['type'] == 'text' and _title(b)]       # withheld/empty headings are not sections
    relative_typography(headings, page_refs or {})
    heads, lines = running_heads(blocks), outlines(blocks, hidden_texts or [])
    # BASE: the levels without the reviewer's own level decisions (what they changed FROM)
    human = [(b, b['heading_level']) for b in headings if b.get('heading_level_source') == 'human']
    for b, _ in human:
        b['heading_level_source'] = None
    infer_levels(headings, dismissed, heads, prior, lines, model)
    base = {b['id']: b.get('heading_level') for b in headings}
    for b, lv in human:
        b['heading_level'], b['heading_level_source'] = lv, 'human'
        for k in ('heading_evidence', 'heading_hints', 'heading_shift'):
            b.pop(k, None)
    rep = infer_levels(headings, dismissed, heads, prior, lines, model)
    shift_subtrees(headings, dismissed, base)
    if report is not None:
        report.clear()
        report.update(rep)
    for b in headings:
        b.pop('heading_level_before', None)
    heading_ids = {b['id'] for b in headings}
    stack: list[dict] = []
    toc = []
    for b in blocks:
        if b['id'] in heading_ids:
            box = next((x for x in toc if x['id'] == b.get('box_head')), None)
            if b['heading_level_source'] == 'box_heading':              # a sub-heading inside a box
                lvl = box['level'] + 1 if box else (stack[-1]['level'] + 1 if stack else 1)
            elif b['heading_level'] is None:            # labelled element: one level below the enclosing SECTION
                sections_only = [x for x in stack if x['source'] not in ('labelled_element', 'box_heading')]
                lvl = (sections_only[-1]['level'] + 1) if sections_only else 1
            else:
                lvl = b['heading_level']
            lvl = max(1, lvl)          # true depth (renderers cap at their own limit, e.g. Markdown h6)
            parent = stack[-1]['level'] if stack else 0
            if lvl > parent + 1 and b['heading_level_source'] in INFERRED \
                    and jump_key(b['id'], lvl, parent + 1) not in dismissed:
                b['heading_level_before'], lvl = lvl, parent + 1          # close the jump (recorded)
                b['heading_level_source'] = 'level_jump_closed'
            b['heading_level'] = lvl
            while stack and stack[-1]['level'] >= lvl:
                stack.pop()
            entry = {'id': b['id'], 'level': lvl, 'title': _title(b)[:200], 'page': b['page'],
                     'parent': stack[-1]['id'] if stack else None, 'source': b['heading_level_source']}
            toc.append(entry)
            stack.append(entry)
        b['section_path'] = [s['id'] for s in stack]
    if report is not None:                                # proposals against the FINAL levels (after jump repair)
        final = {b['id']: b.get('heading_level') for b in headings}
        keep = []
        for prop in report.get('proposals', []):
            prop['members'] = [m for m in prop['members'] if final.get(m) != prop['level']]
            if prop['members']:
                keep.append(prop)
        report['proposals'] = keep
    return toc
