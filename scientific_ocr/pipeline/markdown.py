"""Stage (publish): the reading model (pipeline.reading) -> Markdown, a standalone document for any reader.

The file reads like the book itself: headings, paragraphs, formulas ($...$, $$...$$), images, tables. It carries
no provenance, review state, ids or source-page references, and nothing that says how it was produced; those
live in canonical.json / the normalized JSON. Only lightweight separators mark what is inserted into the text
flow, so a reader (person, RAG splitter, LLM) can tell a figure and its caption, or a boxed example, from the
running text. Two separator styles:
  tags      semantic HTML elements every Markdown renderer and every LLM knows: <figure> … <figcaption> …
            </figure>, <aside> … </aside> (default; visible to tools that keep HTML)
  comments  invisible HTML comments <!-- figure --> … <!-- /figure -->, caption as an italic line
"""
from __future__ import annotations

import re

from . import reading
from .inline import inline_html

STYLES = ('tags', 'comments')
BOX_NAMES = {'el': 'πλαίσιο', 'en': 'box'}


def _heading(n) -> str:
    level = max(1, n['level'])
    return ('#' * level + ' ' + n['text']) if level <= 6 else f'**{n["text"]}**'


def _caption_text(n) -> str:
    return ' '.join(c['text'].replace('\n', ' ') for c in n['captions'])


def _figure(n, images: bool, style: str, lab: dict, url) -> str:
    alt = next((c['label'] for c in n['captions'] if c.get('label')), None) or lab[n['kind']]   # short, e.g. «Σχήμα 4.9»
    parts = []
    if n['image']:
        parts.append(f'![{alt}]({url(n["image"])})' if images else f'[{lab[n["kind"]]}]')
    if n['body']:
        parts.append(f'$$\n{n["body"]["latex"]}\n$$' if 'latex' in n['body'] else n['body']['text'])
    for path in n.get('caption_images') or []:
        parts.append(f'![{alt}]({url(path)})' if images else f'[{alt}]')
    cap = _caption_text(n)
    if style == 'tags':
        inner = '\n\n'.join(p.strip() for p in parts)
        caption = f'<figcaption>{inline_html(cap)}</figcaption>' if cap else ''
        return '<figure>\n\n' + inner + ('\n\n' if inner else '') + caption + '\n</figure>'
    if cap:
        parts.append('*' + re.sub(r'\*\*', '', cap) + '*')
    return '<!-- figure -->\n' + '\n\n'.join(parts) + '\n<!-- /figure -->'


def markdown(canonical: dict, images: bool = True, style: str = 'tags', pages: set | None = None, image_url=None,
             options: dict | None = None) -> str:
    """image_url(path) -> link target (exports rename images to neutral names; default: the book's own path)."""
    style = style if style in STYLES else 'tags'
    url = image_url or (lambda p: p)
    lang = reading.language(canonical)
    lab = reading.LABELS[lang]
    out = []
    for n in reading.nodes(canonical, pages, options):
        t = n['t']
        if t == 'heading':
            out.append(_heading(n))
        elif t == 'para':
            out.append(n['text'])
        elif t == 'equation':
            out.append(f'$$\n{n["latex"]}\n$$')
        elif t == 'figure':
            out.append(_figure(n, images, style, lab, url))
        elif t == 'image':
            out.append(f'![{lab["figure"]}]({url(n["image"])})' if images else f'[{lab["figure"]}]')
        elif t == 'box_start':
            out.append('<aside>' if style == 'tags' else f'<!-- {BOX_NAMES[lang]} -->')
        elif t == 'box_end':
            out.append('</aside>' if style == 'tags' else f'<!-- /{BOX_NAMES[lang]} -->')
        src = reading.excerpt(n)
        if src:                                       # unsettled machine text never travels without its source
            out.append(f'![{lab["source"]}]({url(src)})' if images else f'[{lab["source"]}: σελ. {n["blocks"][0]["page"]}]')
    return '\n\n'.join(out) + '\n'
