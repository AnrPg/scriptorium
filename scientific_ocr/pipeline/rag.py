"""Stage (publish): the RAG serialisation — the reading model (pipeline.reading) shaped for retrieval by a
TEXT-ONLY LLM agent, under the book's output profile (core.profiles; the profile's exclusions are already in the
canonical model: pipeline.profile).

Two outputs from one list of UNITS (heading | text | equation | table | caption):
  markdown(c, profile)  minimal Markdown: '#' headings down to the profile's depth (deeper titles lead their first
                        paragraph), paragraphs, '- ' lists, Markdown tables, $…$/$$…$$ formulas, captions as
                        «Εικόνα 1-4: …» paragraphs. No HTML, no images, no emphasis, no ids, no page numbers.
  chunks(c, profile)    structure-aligned chunks (a heading always opens a new chunk; long sections split at
                        paragraph, then sentence, boundaries; tiny tails merge back), each prefixed by its real
                        heading path. Metadata: breadcrumb, pages (for citation), content types, figure labels,
                        key terms (the book's own bold terms).
What a text-only reader cannot use never appears: pictures (their captions stay), untranscribed crops (the
profile made them reviewable as TEXT_ONLY_LOSS; what the reviewer kept as an image is listed by omissions()).
A picture's caption (the picture itself never travels) goes to the END OF ITS SECTION, right before the next
title, so it never interrupts the text; a table's or an equation's caption stays with it. Trailing credit notes of captions are dropped (the
profile's caption_credits hints). Unicode is NFC; soft hyphens and line-break hyphens are repaired; the words
are the book's own (nothing is rephrased).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from ..core.model import in_reader_output
from ..core.text import MATH_SPAN, MAX_TERM_WORDS, emphasis_terms, strip_emphasis
from . import glossary, reading, tables

_BR = re.compile(r'<br\s*/?>', re.I)
_SUP = re.compile(r'<sup>(.*?)</sup>', re.I | re.S)
_SUB = re.compile(r'<sub>(.*?)</sub>', re.I | re.S)
_LINE_HYPHEN = re.compile(r'(\w)[-‐]\n(\w)')
_HEADING_MARKS = re.compile(r'^#+\s*')
_SENTENCE_END = re.compile(r'(?<=[.;!?·…])\s+(?=[«"(\[]?[A-ZΑ-ΩΆΈΉΊΌΎΏ0-9])')
# headings that label a box or element (pipeline.sections: «Ερώτηση 1-2», a box's sub-heading) are not sections:
# they lead their paragraph and never open a chunk or enter the heading path
BOX_LABEL_SOURCES = {'labelled_element', 'box_heading'}


# ---------------------------------------------------------------- text
def clean(text: str, strip: bool = True) -> tuple[str, list[str]]:
    """(plain text, bold terms). Formula spans are kept verbatim; HTML emphasis and Markdown emphasis removed;
    <sup>/<sub> become LaTeX scripts; NFC; soft hyphens and hyphenated line breaks repaired."""
    terms: list[str] = []
    out = []
    for i, part in enumerate(MATH_SPAN.split(unicodedata.normalize('NFC', text or ''))):
        if i % 2:
            out.append(part)
            continue
        x = part.replace('­', '')
        x = _BR.sub('\n', x)
        x = _SUP.sub(lambda m: f'$^{{{m.group(1).strip()}}}$', x)
        x = _SUB.sub(lambda m: f'$_{{{m.group(1).strip()}}}$', x)
        terms += emphasis_terms(x)
        if strip:
            x = strip_emphasis(x)                         # core.text: one definition of "styling"
        x = _LINE_HYPHEN.sub(lambda m: m.group(1) + m.group(2) if m.group(2).islower() else m.group(0), x)
        out.append(x)
    s = ''.join(out)
    s = '\n'.join(' '.join(ln.split()) for ln in s.split('\n'))
    s = re.sub(r'\n{3,}', '\n\n', s).strip()
    return s, list(dict.fromkeys(terms))


def definition_text(term: str, definition: str) -> str:
    """A glossary entry as the RAG writes it: the term in bold, both introduced by name (pipeline.glossary)."""
    lt, ld = glossary.labels(f'{term} {definition}')
    return f'**{lt}: {term}**\n{ld}: {definition}'


def words(s: str) -> int:
    return len(re.findall(r'\w+', s))


# ---------------------------------------------------------------- tables
def table_text(html_or_text: str) -> str:
    """An engine table (HTML) as Markdown, EVERY cell kept. Spanned cells are spread over the cells they cover
    (pipeline.tables.matrix); leading rows that are one cell across the width are the table's title (a line of their
    own); the leading <th> rows are the header (a column's label joins its header cells: «Πυρήνας / Z»). With a
    header: a pipe table up to 8 columns whose body has no spanned cell, else one line per row 'Header: value; …'
    where a cell spanning columns is said once under their joined labels ('A / B: x'); without a header: 'a: b'
    for two columns, cells joined by ' | ' else."""
    if '<t' not in (html_or_text or '').lower():
        return clean(html_or_text)[0]
    g = tables.grid(html_or_text)
    if g is None:
        return clean(re.sub(r'<[^>]+>', ' ', html_or_text))[0]
    m = [[(clean(t)[0].replace('|', '/'), th) for t, th in r] for r in tables.matrix(g)]
    n_rows = len(m)
    titles = []
    while len(m) > 1 and len(m[0]) > 1 and len({t for t, _ in m[0]}) == 1 and m[0][0][0]:
        titles.append(m.pop(0)[0][0])
    nh = 0
    while nh < len(m) - 1 and all(th for _, th in m[nh]) and any(t for t, _ in m[nh]):
        nh += 1
    width = len(m[0]) if m else 0
    head = [' / '.join(dict.fromkeys(m[r][c][0] for r in range(nh) if m[r][c][0])) for c in range(width)] if nh else None
    body = [[t for t, _ in r] for r in m[nh:]]
    first_body = n_rows - len(body)                    # grid row of the first body row (titles + header above it)
    body_spans = any(r >= first_body and cs > 1 for (r, _), (_, cs) in g.span_of.items())
    if head and width <= 8 and not body_spans:
        lines = ['| ' + ' | '.join(head) + ' |', '|' + '---|' * width] + ['| ' + ' | '.join(r) + ' |' for r in body]
    elif head:
        def pairs(r):                                  # neighbouring columns with one (spanned) value: said once
            out, c = [], 0
            while c < width:
                e = c
                while e + 1 < width and r[e + 1] == r[c]:
                    e += 1
                hs = ' / '.join(dict.fromkeys(h for h in head[c:e + 1] if h))
                if r[c]:
                    out.append(f'{hs}: {r[c]}' if hs else r[c])
                c = e + 1
            return out
        lines = ['- ' + '; '.join(pairs(r)) for r in body if any(r)]
    elif width == 2:
        lines = [f'- {r[0]}: {r[1]}' if r[0] and r[1] else '- ' + (r[0] or r[1]) for r in body if r[0] or r[1]]
    else:
        lines = ['- ' + ' | '.join(x for x in r if x) for r in body if any(r)]
    return '\n'.join(titles + lines)


# ---------------------------------------------------------------- units
def _caption(cap: dict, credits: list[re.Pattern], strip: bool) -> tuple[str, list[str]]:
    text, terms = clean(cap['text'], strip)
    label = clean(cap.get('label') or '', True)[0]
    if label and text.startswith(label):
        rest = text[len(label):].lstrip(' .:—–-')
        text = f'{label}: {rest}' if rest else label
    for rx in credits:
        text = rx.sub('', text).rstrip()
    return text, terms


def _caption_above(n: dict) -> bool:
    """Every caption of a figure node is printed above its element (on its page): a table's title, mostly."""
    blocks = n.get('blocks') or []
    if not blocks or not blocks[0].get('geometry'):
        return False
    el = blocks[0]
    caps = [b for b in blocks[1:] if b.get('geometry')]
    return bool(caps) and all(c['page'] == el['page'] and c['geometry']['bbox'][3] <= el['geometry']['bbox'][1] + 2
                              for c in caps)


def units(c: dict, profile: dict, pages: set | None = None) -> list[dict]:
    """The reading model as RAG units: {'t': heading|subheading|text|list|equation|table|caption, 'text', 'level'?,
    'pages', 'ids', 'terms', 'labels'}. Pictures and untranscribed crops never appear."""
    strip = profile.get('strip_emphasis', True)
    credits = [re.compile(p, re.I) for p in profile.get('caption_credits') or ()]
    depth = int(profile.get('heading_depth') or 6)
    nodes = reading.nodes(c, pages)
    out: list[dict] = []
    held: dict[str, list[dict]] = {}       # picture id -> its caption units, waiting for the end of the section
    lead: list[str] = []                   # titles deeper than the profile's depth: they lead the next paragraph
    gl = {'in': False, 'label': ((c.get('glossary') or {}).get('label') or '')}   # inside the glossary (pipeline.glossary)

    def ids(n) -> list[str]:                      # the segments a unit comes from (views select/show them)
        return [b['id'] for b in n.get('blocks') or []]

    def pages(n) -> list[int]:
        return sorted({b['page'] for b in n.get('blocks') or []})

    def kept_terms(n) -> list[str]:
        return [t for b in n.get('blocks') or [] for t in b.get('emphasis_terms') or []]

    def flush_held(all_=True):
        for fid in list(held):
            out.extend(held.pop(fid, []))

    def put(u: dict) -> None:
        """Emit a unit; pending titles (lead) go in front of it, whatever it is: a paragraph takes them inline
        ('Title. Text'), a table / list / equation / caption on their own line above it. A title is never dropped."""
        if lead:
            head = ' — '.join(lead)
            lead.clear()
            if u['t'] == 'text':
                u['text'] = head + ('' if head.endswith(('.', ':', ';', '?', '!')) else '.') + ' ' + u['text']
            else:
                u['lead'] = head
                u['text'] = head + '\n' + u['text']
        out.append(u)

    def flush_lead() -> None:
        """Titles that nothing followed (the section or the document ends): a text unit of their own."""
        if lead:
            out.append({'t': 'text', 'text': ' — '.join(lead), 'pages': [], 'ids': [], 'terms': [], 'labels': []})
            lead.clear()

    def glossary_title(n) -> None:
        """Entering the glossary: its title (the running head's; the book prints no heading) opens its section."""
        if not gl['in']:
            flush_lead()
            flush_held(all_=True)
            gl['name'] = gl['label'] or ('Γλωσσάριο' if glossary.labels(n.get('text') or '')[0] == 'Όρος' else 'Glossary')
            out.append({'t': 'heading', 'level': 1, 'text': gl['name'], 'pages': pages(n), 'ids': [], 'terms': [], 'labels': [], 'generated': True})
            gl['in'] = True

    for i, n in enumerate(nodes):
        t = n['t']
        first = (n.get('blocks') or [{}])[0]
        if first.get('glossary_letter'):                  # «Α», «Γ / C / G»: a section of the glossary
            glossary_title(n)
            title = clean(_HEADING_MARKS.sub('', n['text']), True)[0]
            if title:
                out.append({'t': 'heading', 'level': 2, 'text': title, 'pages': pages(n), 'ids': ids(n), 'terms': [], 'labels': []})
            continue
        if t == 'para' and any(b.get('glossary') for b in n.get('blocks') or []):
            glossary_title(n)
            terms = [x for b in n['blocks'] for x in (b.get('glossary') or {}).get('terms', [])]
            head, pairs = glossary.split(n['text'], terms)
            head = clean(head, strip)[0] if head else ''
            if head and out and out[-1]['t'] == 'definition':   # the rest of the previous entry (next column/page)
                d = out[-1]
                d['definition'] = (d['definition'] + ' ' + head).strip()
                d['text'] = definition_text(d['term'], d['definition'])
                d['ids'] = list(dict.fromkeys(d['ids'] + ids(n)))
                d['pages'] = sorted(set(d['pages']) | set(pages(n)))
            elif head:
                put({'t': 'text', 'text': head, 'pages': pages(n), 'ids': ids(n), 'terms': [], 'labels': []})
            for term, d in pairs:
                term, d = clean(term, True)[0], clean(d, strip)[0]
                put({'t': 'definition', 'term': term, 'definition': d, 'glossary': gl['name'],
                     'text': definition_text(term, d), 'pages': pages(n), 'ids': ids(n), 'terms': [term], 'labels': []})
            continue
        if t == 'heading' and gl['in']:
            gl['in'] = False                              # a title of the book again: the glossary has ended
        if t == 'heading':
            title = clean(_HEADING_MARKS.sub('', n['text']), True)[0]
            if not title:
                continue
            src = ((n.get('blocks') or [{}])[0]).get('heading_level_source')
            if n.get('level', 1) <= depth and src not in BOX_LABEL_SOURCES:
                flush_lead()                              # a deeper title nothing followed: kept, as text
                flush_held(all_=True)                     # the section ends: its picture captions, then the title
                out.append({'t': 'heading', 'level': n.get('level', 1), 'text': title, 'pages': pages(n), 'ids': ids(n), 'terms': [], 'labels': []})
            else:
                lead.append(title)
                out.append({'t': 'subheading', 'level': n.get('level', depth + 1), 'text': title, 'pages': pages(n), 'ids': ids(n),
                            'terms': [], 'labels': []})
        elif t == 'para':
            text, terms = (table_text(n['text']), []) if '<table' in n['text'].lower() else clean(n['text'], strip)
            if not text:
                continue
            put({'t': 'text', 'text': text, 'pages': pages(n), 'ids': ids(n), 'terms': list(dict.fromkeys(terms + kept_terms(n))),
                        'labels': []})
        elif t == 'equation':
            put({'t': 'equation', 'text': f'$$ {n["latex"].strip()} $$', 'pages': pages(n), 'ids': ids(n), 'terms': [], 'labels': []})
        elif t in ('figure', 'group_caption'):
            caps, terms, labels = [], [], []
            for cap in n.get('captions') or []:
                s, tt = _caption(cap, credits, strip)
                if s:
                    caps.append(s)
                    terms += tt
                    if cap.get('label'):
                        labels.append(clean(cap['label'], True)[0])
            body = n.get('body') if t == 'figure' else None
            body_units = []
            if body and 'latex' in body:
                body_units.append({'t': 'equation', 'text': f'$$ {body["latex"].strip()} $$', 'pages': pages(n), 'ids': ids(n), 'terms': [], 'labels': labels})
            elif body and body.get('items'):                  # a table read as a list (pipeline.tables)
                lst = tables.clean_list(body['list'], lambda s: clean(s, strip)[0])
                if lst:
                    flat = [x for x in lst['items'] if isinstance(x, str)]
                    body_units.append({'t': 'list', 'text': tables.markdown(lst), 'list': lst, 'pages': pages(n), 'ids': ids(n),
                                       'terms': [x for x in flat if len(x.split()) <= MAX_TERM_WORDS], 'labels': labels})
            elif body and body.get('text'):
                kind = 'table' if n.get('kind') == 'table' else 'text'
                txt = table_text(body['text']) if kind == 'table' else clean(body['text'], strip)[0]
                if txt:
                    body_units.append({'t': kind, 'text': txt, 'pages': pages(n), 'ids': ids(n), 'terms': [], 'labels': labels})
            unit = {'t': 'caption', 'text': '\n\n'.join(caps), 'pages': pages(n), 'ids': ids(n), 'terms': terms, 'labels': labels} if caps else None
            fig = (n.get('blocks') or [{}])[0]
            if unit and not body:                         # a picture's caption: at the end of its section
                held.setdefault(fig.get('id') or f'n{i}', []).append(unit)
                unit = None
            # a table's / equation's caption stays with it, where it is printed: above it (a table's title) or below
            first = [unit] if unit and _caption_above(n) else []
            for u in first + body_units + ([unit] if unit and not first else []):
                put(u)
        # 'image' (an untranscribed crop the reviewer kept as an image), box_start/box_end: nothing for a text reader
    flush_lead()
    flush_held(all_=True)
    return out


def expected(c: dict, profile: dict) -> list[tuple[str, int, str]]:
    """What the RAG output must contain, piece by piece: (segment id, page, text as the RAG shapes it). The same
    reading model and cleaning as units(), WITHOUT its arrangement (leads, held captions, chunking) — the audit
    (pipeline.audit.rag_export) checks the arrangement against this: nothing the reader keeps may get lost."""
    strip = profile.get('strip_emphasis', True)
    credits = [re.compile(p, re.I) for p in profile.get('caption_credits') or ()]
    out: list[tuple[str, int, str]] = []

    def add(n_or_b, text):
        b = n_or_b if 'chosen' in n_or_b else ((n_or_b.get('blocks') or [None])[0])
        if b is not None and (text or '').strip():
            out.append((b['id'], b['page'], text))

    def cells(html_text):                            # a table: every cell, as table_text writes it
        g = tables.grid(html_text)
        return [clean(t)[0].replace('|', '/') for r in g.rows for t, _ in r] if g else [table_text(html_text)]

    for n in reading.nodes(c):
        t = n['t']
        if t == 'heading' and not n.get('generated'):
            add(n, clean(_HEADING_MARKS.sub('', n['text']), True)[0])
        elif t == 'para' and any(b.get('glossary') for b in n.get('blocks') or []):
            head, pairs = glossary.split(n['text'], [x for b in n['blocks'] for x in (b.get('glossary') or {}).get('terms', [])])
            for x in [head] + [y for p in pairs for y in p]:
                add(n, clean(x, strip)[0] if x else '')
        elif t == 'para':
            for x in (cells(n['text']) if '<table' in n['text'].lower() else [clean(n['text'], strip)[0]]):
                add(n, x)
        elif t == 'equation':
            add(n, n['latex'].strip())
        elif t in ('figure', 'group_caption'):
            body = n.get('body') if t == 'figure' else None
            if body and 'latex' in body:
                add(n, body['latex'].strip())
            elif body and body.get('items'):
                lst = tables.clean_list(body['list'], lambda s: clean(s, strip)[0])
                for x in tables.texts(lst) if lst else []:
                    add(n, x)
            elif body and body.get('text'):
                for x in (cells(body['text']) if n.get('kind') == 'table' else [clean(body['text'], strip)[0]]):
                    add(n, x)
            caps = n.get('captions') or []
            cap_blocks = [b for b in n.get('blocks') or [] if b.get('kind') == 'caption'] or n.get('blocks') or []
            for i, cap in enumerate(caps):
                add(cap_blocks[min(i, len(cap_blocks) - 1)] if cap_blocks else n, _caption(cap, credits, strip)[0])
    return out


# ---------------------------------------------------------------- outputs
def html(c: dict, profile: dict, pages: set | None = None) -> str:
    """The RAG output as the reviewer sees it in «Αποτέλεσμα»: the SAME units as the export (titles to the
    profile's depth, deeper ones leading their paragraph, picture captions at the end of their section, tables as
    rows), each element carrying the segment it comes from (data-seg: click selects, loops show it)."""
    from html import escape

    from .inline import inline_html, list_html
    out = []
    for u in units(c, profile, pages):
        seg = (u.get('ids') or [''])[0]
        a = f' class="pv-seg" data-seg="{escape(seg)}"' if seg else ''
        t, text = u['t'], u['text']
        if u.get('lead'):                                # titles in front of a table / list / equation / caption
            out.append(f'<div{a}><p class="pv-text pv-lead"><b>{inline_html(u["lead"])}</b></p></div>')
            text = text[len(u['lead']) + 1:]
        lines = text.split('\n')
        if t == 'heading':
            lv = max(1, min(6, u.get('level') or 1))
            out.append(f'<h{lv}{a}>{inline_html(text)}</h{lv}>')
        elif t == 'subheading':
            continue                                     # it leads its paragraph
        elif t == 'equation':
            out.append(f'<div{a}><div class="pv-math">{escape(text)}</div></div>')
        elif t == 'table' and text.startswith('|'):
            out.append(f'<div{a}><pre class="pv-text pv-rag-table">{escape(text)}</pre></div>')
        elif t == 'list' and u.get('list'):
            out.append(f'<div{a}>{list_html(u["list"])}</div>')
        elif t == 'definition':                          # a glossary entry: term (bold) and definition, named
            lt, ld = glossary.labels(f'{u["term"]} {u["definition"]}')
            out.append(f'<div{a}><dl class="pv-def"><dt><span class="pv-def-l">{lt}:</span> <b>{inline_html(u["term"])}</b></dt>'
                       f'<dd><span class="pv-def-l">{ld}:</span> {inline_html(u["definition"])}</dd></dl></div>')
        elif all(x.startswith('- ') for x in lines if x.strip()) and any(x.strip() for x in lines):
            items = ''.join(f'<li>{inline_html(x[2:])}</li>' for x in lines if x.strip())
            out.append(f'<div{a}><ul class="pv-text pv-items">{items}</ul></div>')
        else:
            cls = 'pv-cap' if t == 'caption' else 'pv-text'
            body = '<br>'.join(inline_html(x) for x in lines)
            out.append(f'<div{a}><p class="{cls}">{body}</p></div>')
    return '\n'.join(out)


def markdown(c: dict, profile: dict, pages: set | None = None) -> str:
    depth = int(profile.get('heading_depth') or 6)
    out = []
    for u in units(c, profile, pages):
        if u['t'] == 'heading':
            out.append('#' * max(1, min(u['level'], depth)) + ' ' + u['text'])
        elif u['t'] == 'subheading':
            continue                                     # already leads its paragraph
        else:
            out.append(u['text'])
    return '\n\n'.join(out) + '\n'


def _split_long(text: str, max_words: int, lines: bool = False) -> list[str]:
    """A unit longer than max_words, split at sentence ends (never inside a sentence) — or, for tables, lists and
    captions (lines=True), at line ends; a pipe table repeats its header row in every part."""
    if words(text) <= max_words:
        return [text]
    seps = text.split('\n') if lines else _SENTENCE_END.split(text)
    head = seps[:2] if lines and len(seps) > 2 and seps[0].startswith('|') and seps[1].startswith('|-') else []
    parts, cur = [], '\n'.join(head)
    glue = '\n' if lines else ' '
    for s in seps[len(head):]:
        if cur and words(cur) + words(s) > max_words and cur != '\n'.join(head):
            parts.append(cur)
            cur = glue.join(head + [s]) if head else s
        else:
            cur = (cur + glue + s).strip() if cur else s
    return parts + ([cur] if cur else [])


def chunks(c: dict, profile: dict, title: str = '', context: dict | None = None) -> list[dict]:
    """Structure-aligned chunks with their heading path. context: optional {chunk id: situating sentence} made
    by a local model (application.rag_context); stored apart from the book's text, never mixed into it."""
    cfg = profile.get('chunk') or {}
    target, max_w, min_w = int(cfg.get('target_words', 220)), int(cfg.get('max_words', 380)), int(cfg.get('min_words', 40))
    depth = int(profile.get('heading_depth') or 6)
    path: list[tuple[int, str]] = []
    sub: list[tuple[int, str]] = []
    out: list[dict] = []
    cur: dict | None = None

    def crumbs() -> list[str]:
        return [t for _, t in path] + [t for _, t in sub]

    def close():
        nonlocal cur
        if cur and cur['parts']:
            if out and words(' '.join(cur['parts'])) < min_w and out[-1]['_path'] == cur['path'] and not cur.get('term') \
                    and not out[-1].get('term') \
                    and words(out[-1]['_body']) + words(' '.join(cur['parts'])) <= max_w:
                prev = out[-1]                              # a tiny tail goes back into the previous chunk
                prev['_body'] += '\n\n' + '\n\n'.join(cur['parts'])
                for k in ('pages', 'types', 'figure_refs', 'key_terms'):
                    prev[k] = list(dict.fromkeys(prev[k] + cur[k]))
            else:
                out.append({'_path': cur['path'], 'breadcrumb': cur['crumbs'], '_body': '\n\n'.join(cur['parts']),
                            'pages': cur['pages'], 'types': cur['types'], 'figure_refs': cur['figure_refs'],
                            'key_terms': cur['key_terms'], **({'term': cur['term'], 'glossary': cur['glossary']} if cur.get('term') else {})})
        cur = None

    def start():
        nonlocal cur
        cur = {'path': tuple(t for _, t in path), 'crumbs': crumbs(), 'parts': [], 'pages': [], 'types': [],
               'figure_refs': [], 'key_terms': []}

    for u in units(c, profile):
        if u['t'] == 'heading':
            close()
            path = [x for x in path if x[0] < u['level']] + [(u['level'], u['text'])]
            sub = []
            continue
        if u['t'] == 'subheading':
            sub = [x for x in sub if x[0] < u['level']] + [(u['level'], u['text'])]
            if cur and cur['parts'] and words(' '.join(cur['parts'])) >= min_w:
                close()                                     # a deep title is a good place to cut
            continue
        if u['t'] == 'definition':                         # a glossary entry: ONE chunk of its own, never cut
            close()                                         # (a longer one in sentence parts, each with its term)
            for piece in _split_long(u['definition'], max(max_w - words(u['term']) - 2, 50)):
                start()
                cur.update(term=u['term'], glossary=u.get('glossary') or '', parts=[definition_text(u['term'], piece)],
                           pages=list(u['pages']), types=['definition'], key_terms=[u['term']])
                close()
            continue
        pieces = _split_long(u['text'], max_w, lines=u['t'] != 'text' or '\n- ' in u['text'] or u['text'].startswith('- '))
        for piece in pieces:
            if cur is None:
                start()
            elif cur['parts'] and words(' '.join(cur['parts'])) + words(piece) > target:
                close()
                start()
            cur['parts'].append(piece)
            cur['pages'] = sorted(set(cur['pages']) | set(u['pages']))
            cur['types'] = list(dict.fromkeys(cur['types'] + [u['t']]))
            cur['figure_refs'] = list(dict.fromkeys(cur['figure_refs'] + u['labels']))
            cur['key_terms'] = list(dict.fromkeys(cur['key_terms'] + u['terms']))
    close()
    sha = (c.get('source') or {}).get('sha256', '')
    result, seen = [], {}
    for i, ch in enumerate(out):
        crumb = ' › '.join(ch['breadcrumb'])
        body = ch.pop('_body')
        ch.pop('_path')
        key = f'{sha}|{crumb}|{body}'                     # content-addressed; a repeated chunk gets its occurrence
        seen[key] = seen.get(key, 0) + 1
        cid = hashlib.sha1(f'{key}|{seen[key]}'.encode()).hexdigest()[:16]
        text = (crumb + '\n\n' if crumb else '') + body
        rec = {'id': cid, 'n': i, 'book': title, 'text': text, **ch, 'words': words(body)}
        if context and context.get(cid):
            rec['context'] = context[cid]
        result.append(rec)
    return result


def jsonl(records: list[dict]) -> str:
    return ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records)


# ---------------------------------------------------------------- gates and manifest
def unsettled(c: dict) -> list[dict]:
    """Segments of the RAG output that the reviewer must still check (the profile's 'review_first' policy):
    required or unresolved review among what reaches the output."""
    return [{'id': b['id'], 'page': b['page'], 'kind': b['kind'], 'reasons': b['reasons'][:3]}
            for b in c['blocks'] if in_reader_output(b) and b.get('flow') != 'furniture'
            and b['review'] in ('required', 'unresolved')]


def omissions(c: dict) -> dict:
    """What the RAG text does not contain, stated (never silent): profile exclusions, pictures, and content the
    reviewer kept only as an image."""
    blocks = [b for b in c['blocks'] if b.get('flow') != 'furniture' and not b.get('excluded')]
    prof = c.get('profile') or {}
    return {
        'profile': prof.get('name'),
        'excluded_by_profile': [{k: e.get(k) for k in ('type', 'label', 'pages', 'n', 'confirmed')} for e in prof.get('excluded') or []],
        'unconfirmed_exclusions': prof.get('pending', 0),
        'excluded_by_reviewer': sum(1 for b in blocks if b.get('omitted')),
        'pictures_dropped': sum(1 for b in blocks if in_reader_output(b) and b['kind'] in reading.FIGURE_KINDS
                                and b['chosen'].get('type') == 'image_only'),
        'kept_only_as_image': [{'page': b['page'], 'kind': b['kind']} for b in blocks if in_reader_output(b)
                               and b['kind'] not in reading.FIGURE_KINDS and b['chosen'].get('type') == 'image_only'
                               and b['kind'] != 'caption'],
    }
