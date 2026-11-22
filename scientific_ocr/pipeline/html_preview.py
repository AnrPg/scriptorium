"""Stage (publish): the reading model (pipeline.reading) -> HTML, for the PDF export and the in-app preview.

One serialiser, two audiences:
  reviewer=False  the document as a reader gets it (PDF): content and inserts only — no ids, review states,
                  source references or anything that says how the file was produced
  reviewer=True   the in-app "Αποτέλεσμα" view: the SAME rendering plus what a reviewer needs — data-seg links to
                  the page scan and the inspector, review chips, the source image of unsettled text, and the
                  list of excluded page furniture
Maths is rendered client-side by the shared KaTeX + mhchem setup (static/render-math.js).
"""
from __future__ import annotations

from html import escape

from ..core.model import in_output
from . import reading
from .inline import HEADING_MARKS, inline_html
from .markdown import markdown

STATUS_EL = {'required': 'απαιτείται έλεγχος', 'recommended': 'συνιστάται έλεγχος', 'approved': 'εγκρίθηκε',
             'image_only': 'μόνο εικόνα', 'unresolved': 'δυσανάγνωστο', 'accepted': 'δεκτό στατιστικά', 'not_needed': ''}


def page_markdown(c: dict, page: int, style: str = 'tags', images: bool = True) -> str:
    """Exactly the part of the exported Markdown that comes from one page."""
    return markdown(c, images=images, style=style, pages={page})


def _img(path, asset_url, alt='') -> str:
    return f'<img class="pv-img" src="{escape(asset_url(path))}" alt="{escape(alt)}">' if path else ''


def _review(n, reviewer: bool) -> tuple[str, str]:
    """(attributes, trailing chips) for the reviewer view; nothing for readers."""
    if not reviewer or not n['blocks']:
        return '', ''
    b = n['blocks'][0]
    worst = max(n['blocks'], key=lambda x: ('not_needed', 'accepted', 'approved', 'image_only', 'recommended',
                                            'unresolved', 'required').index(x['review']) if x['review'] in STATUS_EL else 0)
    st = STATUS_EL.get(worst['review'], '')
    chip = f'<span class="pv-st pv-{worst["review"]}">{escape(st)}</span>' if st else ''
    return f' class="pv-seg" data-seg="{escape(b["id"])}"', chip


def _source(n, asset_url, reviewer: bool) -> str:
    """The source image of machine text that is not settled yet (reading.excerpt): collapsed in the reviewer view,
    visible in reader outputs (PDF/HTML), as in the Markdown."""
    path = reading.excerpt(n)
    if not path:
        return ''
    if reviewer:
        return f'<details class="pv-src"><summary>εικόνα πηγής</summary>{_img(path, asset_url)}</details>'
    return f'<div class="pv-src-shown">{_img(path, asset_url)}</div>'


def body_html(c: dict, asset_url, reviewer: bool = False, pages: set | None = None, options: dict | None = None) -> str:
    lab = reading.LABELS[reading.language(c)]
    out = []
    for n in reading.nodes(c, pages, options):
        attrs, chip = _review(n, reviewer)
        src = _source(n, asset_url, reviewer)
        t = n['t']
        if t == 'heading':
            lvl = min(6, max(1, n['level'])) + (1 if reviewer else 0)
            out.append(f'<h{lvl}{attrs}>{inline_html(HEADING_MARKS.sub("", n["text"]))} {chip}</h{lvl}>')
        elif t == 'para':
            tag = 'div' if '<table' in n['text'] else 'p'
            out.append(f'<div{attrs}><{tag} class="pv-text">{inline_html(n["text"])}</{tag}>{src}{chip}</div>')
        elif t == 'equation':
            out.append(f'<div{attrs}><div class="pv-math">$${escape(n["latex"])}$$</div>{src}{chip}</div>')
        elif t == 'figure':
            body = _img(n['image'], asset_url, lab[n['kind']]) if n['image'] else ''
            if n['body']:
                body += (f'<div class="pv-math">$${escape(n["body"]["latex"])}$$</div>' if 'latex' in n['body']
                         else f'<div class="pv-text">{inline_html(n["body"]["text"])}</div>')
            body += ''.join(_img(p, asset_url, lab[n['kind']]) for p in n.get('caption_images') or [])
            caps = ''.join(f'<figcaption class="pv-cap pv-cap-{escape(cp["role"])}">{inline_html(cp["text"])}</figcaption>'
                           for cp in n['captions'])
            cls = attrs.replace('class="pv-seg"', 'class="pv-seg pv-figure"') if attrs else ' class="pv-figure"'
            out.append(f'<figure{cls}>{body}{src}{caps}{chip}</figure>')
        elif t == 'image':
            out.append(f'<div{attrs}>{_img(n["image"], asset_url, lab["figure"])}{chip}</div>')
        elif t == 'box_start':
            out.append('<aside class="pv-box">')
        elif t == 'box_end':
            out.append('</aside>')
    return '\n'.join(out)


def page_html(c: dict, page: int, asset_url, reviewer: bool = True) -> str:
    html = body_html(c, asset_url, reviewer, {page})
    gone = [b for b in c['blocks'] if b['page'] == page and not in_output(b)] if reviewer else []
    left = [b for b in c['blocks'] if b['page'] == page and in_output(b) and b.get('omitted')] if reviewer else []
    if left:                                            # reviewer only: omitted by the reviewer, never in the output
        items = ''.join(f'<li class="pv-seg" data-seg="{escape(b["id"])}">{escape((b["chosen"].get("text") or "").strip()[:80]) or "[εικόνα]"}</li>'
                        for b in left)
        html_left = (f'\n<details class="pv-excluded"><summary>Εκτός εξόδου από εσένα ({len(left)})</summary><ul>{items}</ul></details>')
    else:
        html_left = ''
    if gone:                                            # reviewer only; never part of the output
        items = ''.join(f'<li class="pv-seg" data-seg="{escape(b["id"])}">{escape(b["excluded"]["as"])} · '
                        f'{escape(b["excluded"]["side"])}: {escape((b["chosen"].get("text") or "").strip()[:80]) or "[εικόνα]"}</li>'
                        for b in gone)
        html += (f'\n<details class="pv-excluded"><summary>Εξαιρέθηκαν ως στοιχεία σελίδας ({len(gone)}) — δεν '
                 f'μπαίνουν στην έξοδο</summary><ul>{items}</ul></details>')
    return html + html_left


def document_html(c: dict, asset_url, static_url, title: str = '', options: dict | None = None) -> str:
    """The whole document as one standalone HTML page for printing (PDF export)."""
    k = lambda rel: escape(static_url(rel))
    lang = reading.language(c)
    head = f'<h1 class="pv-title">{escape(title)}</h1>' if title else ''
    return f"""<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8"><title>{escape(title)}</title>
<link rel="stylesheet" href="{k('vendor/katex/katex.min.css')}"><link rel="stylesheet" href="{k('tokens.css')}">
<link rel="stylesheet" href="{k('preview.css')}">
<style>@page{{size:A4;margin:18mm 16mm}} body{{margin:0;background:#fff}}</style></head>
<body><main class="pv-sheet pv-doc">{head}{body_html(c, asset_url, reviewer=False, options=options)}</main>
<script src="{k('vendor/katex/katex.min.js')}"></script><script src="{k('vendor/katex/contrib/mhchem.min.js')}"></script>
<script src="{k('vendor/katex/contrib/auto-render.min.js')}"></script>
<script src="{k('render-math.js')}"></script><script>renderMath(document.body);</script></body></html>"""
