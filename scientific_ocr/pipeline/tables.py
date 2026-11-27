"""Stage: tables that are really LISTS of items laid out in columns («ΒΑΣΙΚΟΙ ΟΡΟΙ», key-term boxes, word lists).

A grid of terms is not a table: its rows relate nothing, and a Markdown/pipe table of it only litters a text
reader (RAG). Such a table is rendered as a list, one item per cell, read BY COLUMNS (down each column, then the
next one — how a printed term list reads) or BY ROWS.

Layout of a table segment (core: reviewer first, then the automatic hint):
  table_layout   the reviewer's decision (overlay `set_table_layout`): 'rows' | 'cols' | 'table' (keep the grid)
  table_list     the automatic HINT (annotate): {'order', 'evidence', 'sorted'} — applied only when the cells LOOK
                 like terms (no header row, short cells, no numbers) AND independent evidence agrees: the segment is
                 a key-terms/glossary entity, or its cells are in alphabetical order down the columns (or rows).
A label alone ('Βασικοί όροι') is a hint; the alphabetical order is the check (an alphabetised grid of short
phrases is a list whatever the book calls it).

Continuation lines: a term too long for its column wraps onto the next row, INDENTED («ένζυμο που τροποποιεί /
  τις ιστόνες»); the engine reads it as a new cell. Evidence is GEOMETRY, never the words: a cell whose line starts
clearly right of its column's left edge joins the cell above it. Two independent measurements, either suffices:
  1. the scan itself (`ink_lines`, measured at page composition: pipeline.page): the printed lines of the table
     and the runs of ink on each; used when the lines match the grid's rows and each row's runs its cells;
  2. Docling's per-line readings (their bbox), when Docling read the table line by line.
Without such evidence nothing is joined (the reviewer can correct the text).
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from html.parser import HTMLParser
from statistics import median

LAYOUTS = ('rows', 'cols')
MAX_TERM_WORDS = 6
MIN_ITEMS = 4
SORTED_MIN = 0.8                 # share of adjacent pairs in alphabetical order that counts as "alphabetised"
INDENT_MIN = 0.5                 # a continuation starts this many line heights right of its column's left edge
MATCH_MIN = 0.75                 # similarity of a cell's text and a Docling line to take the line's geometry
TERM_ENTITIES = {'key_terms', 'glossary'}


# ---------------------------------------------------------------- grid
class Grid(HTMLParser):
    """An engine table (HTML) as rows of (cell text, is_header); `spans`: some cell spans rows/columns."""

    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell, self.spans = [], None, None, False

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = []
            if any(k in ('colspan', 'rowspan') and v not in (None, '1') for k, v in attrs):
                self.spans = True
        elif tag == 'br' and self.cell is not None:
            self.cell.append(' ')
        elif tag == 'math' and self.cell is not None:
            self.cell.append('$')                            # a formula stays a formula ($…$)
        elif tag in ('sup', 'sub') and self.cell is not None:
            self.cell.append(f'<{tag}>')                      # scripts kept (the renderer turns them into LaTeX)

    def handle_endtag(self, tag):
        if tag in ('math', 'sup', 'sub') and self.cell is not None:
            self.cell.append('$' if tag == 'math' else f'</{tag}>')
            return
        if tag in ('td', 'th') and self.row is not None and self.cell is not None:
            self.row.append((' '.join(''.join(self.cell).split()), tag == 'th'))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def grid(html: str) -> Grid | None:
    if '<t' not in (html or '').lower():
        return None
    g = Grid()
    g.feed(html)
    return g if g.rows else None


# ---------------------------------------------------------------- text keys
def _key(s: str) -> str:
    s = unicodedata.normalize('NFKD', unicodedata.normalize('NFKC', s or '').casefold())
    return ''.join(ch for ch in s if not unicodedata.combining(ch)).replace('ς', 'σ')


def _letters(s: str) -> str:
    return ''.join(ch for ch in _key(re.sub(r'<[^>]+>', '', s)) if ch.isalnum())


def _script(s: str) -> str | None:
    ch = next((c for c in _key(s) if c.isalpha()), '')
    return None if not ch else 'greek' if 'α' <= ch <= 'ω' else 'latin' if 'a' <= ch <= 'z' else None


def sortedness(items: list[str]) -> float | None:
    """Share of adjacent pairs (same script only: Latin names interleave with Greek by pronunciation) in
    alphabetical order; None when too few pairs to tell."""
    pairs = [(a, b) for a, b in zip(items, items[1:]) if _script(a) and _script(a) == _script(b)]
    if len(pairs) < 3:
        return None
    return sum(_key(a) <= _key(b) for a, b in pairs) / len(pairs)


# ---------------------------------------------------------------- continuation evidence
INK_VERSION = 'ink-lines-v2'


def ink_lines(dark, x_off: float, y_off: float, px: float) -> list[dict]:
    """The printed lines of a table crop: [{'y': [y0, y1], 'runs': [[x0, x1], …]}] in page units. `dark`: boolean
    ink mask of the crop; `px`: page units per pixel; offsets: the crop's page position. Accents and descenders
    stay with their line (bands closer than a third of the typical line height merge); a run is ink separated
    from the next by more than one line height (word spaces are far narrower: columns stay apart)."""
    import numpy as np
    rows = dark.sum(axis=1) > max(1, int(0.002 * dark.shape[1]))
    bands, start = [], None
    for y, on in enumerate(list(rows) + [False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            bands.append([start, y])
            start = None
    if not bands:
        return []
    h = float(np.median([b[1] - b[0] for b in bands]))
    # a FRAGMENT (accents above a line, a descender below it) is far shorter than a line: it joins the nearer
    # neighbouring line when close; lines themselves never merge (leading can be much smaller than a line)
    merged: list[list[int]] = []
    for i, b in enumerate(bands):
        if b[1] - b[0] >= 0.6 * h:
            merged.append(list(b))
    for b in bands:
        if b[1] - b[0] >= 0.6 * h or not merged:
            continue
        near = min(merged, key=lambda m: max(m[0] - b[1], b[0] - m[1], 0))
        if max(near[0] - b[1], b[0] - near[1], 0) < h / 3:
            near[0], near[1] = min(near[0], b[0]), max(near[1], b[1])
    merged.sort()
    out = []
    for y0, y1 in merged:
        cols = np.flatnonzero(dark[y0:y1].any(axis=0))
        runs, s0, prev = [], int(cols[0]), int(cols[0])
        for x in cols[1:]:
            if x - prev > h:
                runs.append([s0, prev + 1])
                s0 = int(x)
            prev = int(x)
        runs.append([s0, prev + 1])
        out.append({'y': [round(y_off + y0 * px, 1), round(y_off + y1 * px, 1)],
                    'runs': [[round(x_off + a * px, 1), round(x_off + b * px, 1)] for a, b in runs]})
    return out


def _ink_x0(block: dict, g: 'Grid') -> tuple[dict[tuple[int, int], float], float] | None:
    """Left edge of each cell from the scan's lines, when the lines match the grid's rows one to one and each
    line's ink runs match its row's non-empty cells."""
    lines = block.get('ink_lines') or []
    if not lines or len(lines) != len(g.rows):
        return None
    x0 = {}
    for r, (row, line) in enumerate(zip(g.rows, lines)):
        cols = [c for c, (t, _) in enumerate(row) if t.strip()]
        if len(cols) != len(line['runs']):
            return None
        for c, run in zip(cols, line['runs']):
            x0[(r, c)] = run[0]
    from statistics import median as _m
    return x0, _m(l['y'][1] - l['y'][0] for l in lines)


def _lines(block: dict) -> list[tuple[str, list[float]]]:
    """Docling's per-line readings of the segment (text, bbox)."""
    return [(c.get('text') or '', c['bbox']) for c in block.get('candidates') or []
            if c.get('engine') == 'docling' and c.get('bbox') and (c.get('text') or '').strip()]


def _indented(x0: dict[tuple[int, int], float], h: float, cells) -> set[tuple[int, int]]:
    left: dict[int, float] = {}
    for (r, c), x in x0.items():
        left[c] = min(left.get(c, x), x)
    return {(r, c) for (r, c), x in x0.items() if r > 0 and (r - 1, c) in cells and x - left[c] > INDENT_MIN * h}


def _continuations(block: dict, cells: dict[tuple[int, int], str], g: 'Grid') -> set[tuple[int, int]]:
    """Cells (row, col) that continue the cell above them: their printed line is indented in its column."""
    ink = _ink_x0(block, g)
    if ink:
        return _indented(ink[0], ink[1], cells)
    lines = _lines(block)
    if not lines:
        return set()
    keys = [_letters(t) for t, _ in lines]
    used: set[int] = set()
    x0: dict[tuple[int, int], float] = {}
    for rc, text in cells.items():
        k = _letters(text)
        if not k:
            continue
        best, score = None, MATCH_MIN
        for i, lk in enumerate(keys):
            if i in used or not lk:
                continue
            s = 1.0 if lk == k else difflib.SequenceMatcher(None, k, lk).ratio()
            if s > score or (s == score == 1.0 and best is None):
                best, score = i, s
        if best is not None:
            used.add(best)
            x0[rc] = lines[best][1][0]
    if not x0:
        return set()
    return _indented(x0, median(abs(b[3] - b[1]) for _, b in lines) or 1.0, cells)


# ---------------------------------------------------------------- items
def columns(block: dict, html: str) -> tuple[list[list[tuple[int, str]]], Grid] | None:
    """The table's items per column, [(row, text)], continuation lines joined to their item."""
    g = grid(html)
    if g is None:
        return None
    cells = {(r, c): t for r, row in enumerate(g.rows) for c, (t, _) in enumerate(row) if t.strip()}
    cont = _continuations(block, cells, g)
    width = max(len(r) for r in g.rows)
    cols: list[list[tuple[int, str]]] = [[] for _ in range(width)]
    for c in range(width):
        for r in range(len(g.rows)):
            t = cells.get((r, c))
            if not t:
                continue
            if (r, c) in cont and cols[c]:
                pr, pt = cols[c][-1]
                # a word hyphenated at the line end continues in lowercase: one word again
                joined = pt[:-1] + t if pt.endswith(('-', '‐')) and t[:1].islower() else f'{pt} {t}'
                cols[c][-1] = (pr, joined)
            else:
                cols[c].append((r, t))
    return cols, g


def items(block: dict, html: str, order: str) -> list[str]:
    """The cells as list items, by columns ('cols') or by rows ('rows')."""
    res = columns(block, html)
    if res is None:
        return []
    cols, _ = res
    if order == 'cols':
        return [t for col in cols for _, t in col]
    return [t for _, _, t in sorted((r, c, t) for c, col in enumerate(cols) for r, t in col)]


def _looks_like_terms(g: Grid, cells: list[str]) -> bool:
    if all(h for _, h in g.rows[0]) and len(g.rows) > 1:
        return False                                         # a header row: a real table
    if len(cells) < MIN_ITEMS:
        return False
    for t in cells:
        if len(t.split()) > MAX_TERM_WORDS or t.rstrip().endswith(('.', ':', ';')):
            return False
        digits = sum(ch.isdigit() for ch in t)
        if digits * 2 >= len(t.replace(' ', '')):
            return False
    return True


def source_text(block: dict) -> tuple[str, str | None] | None:
    """(table HTML, engine) the hint is computed on: the chosen text, or — when the segment is kept as an image
    (e.g. approved as image before a text-only profile) — the engine reading that has a table (Marker first);
    the engine is then named, so the reviewer adopts that reading together with the list layout."""
    ch = block.get('chosen') or {}
    if ch.get('type') == 'text':
        return ch.get('text') or '', None
    cands = sorted((c for c in block.get('candidates') or [] if '<table' in (c.get('text') or '').lower()),
                   key=lambda c: c.get('engine') != 'marker')
    return (cands[0]['text'], cands[0]['engine']) if cands else None


def suggest(block: dict) -> dict | None:
    """The automatic hint for one table segment (see module doc), or None."""
    src = source_text(block) if block.get('kind') == 'table' else None
    if src is None:
        return None
    html, engine = src
    res = columns(block, html)
    if res is None:
        return None
    cols, g = res
    by_cols = [t for col in cols for _, t in col]
    if not _looks_like_terms(g, by_cols):
        return None
    by_rows = items(block, html, 'rows')
    sc = sortedness(by_cols) if len(cols) > 1 else sortedness(by_rows)
    sr = sortedness(by_rows)
    evidence = []
    ents = {e.get('type') for e in block.get('entities') or []}
    if ents & TERM_ENTITIES:
        evidence.append('entity:' + sorted(ents & TERM_ENTITIES)[0])
    best = max((s for s in (sc, sr) if s is not None), default=None)
    if best is not None and best >= SORTED_MIN:
        evidence.append('alphabetical')
    if not evidence:
        return None
    order = 'rows' if (sr or 0) > (sc or 0) else 'cols'
    out = {'order': order, 'evidence': evidence, 'sorted': round(best, 2) if best is not None else None,
           'count': len(by_cols)}
    if engine:
        out['candidate'] = engine                            # applies only once the reviewer adopts this reading
    return out


def annotate(blocks: list[dict]) -> int:
    """Sets/clears `table_list` on every table segment; returns how many are lists."""
    n = 0
    for b in blocks:
        if b.get('kind') != 'table':
            continue
        s = suggest(b)
        if s:
            b['table_list'] = s
            n += 1
        else:
            b.pop('table_list', None)
    return n


def layout(block: dict) -> str | None:
    """'rows' | 'cols' when the segment is rendered as a list, None when as a table."""
    over = block.get('table_layout')
    if over in LAYOUTS:
        return over
    if over == 'table':
        return None
    hint = block.get('table_list') or {}
    return None if hint.get('candidate') else hint.get('order')


def as_list(block: dict, text: str) -> list[str] | None:
    """The segment's text as list items when its layout is a list (else None)."""
    order = layout(block)
    if not order or (block.get('chosen') or {}).get('type') != 'text':
        return None
    return items(block, text, order) or None
