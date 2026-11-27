"""Engine text -> safe inline HTML (shared by the Markdown and HTML serialisers)."""
from __future__ import annotations

import re
from html import escape

from ..core.text import MATH_SPAN

HEADING_MARKS = re.compile(r'^#+\s*')
_TAGS = re.compile(r'&lt;(/?)(sub|sup|b|i|em|strong|u|table|thead|tbody|tr|th|td)((?:\s+(?:colspan|rowspan)="\d{1,2}")*)\s*&gt;'
                   r'|&lt;br\s*/?&gt;', re.I)          # only these tags, only numeric span attributes


def inline_html(text: str) -> str:
    """OCR text -> safe inline HTML. Engines emit Markdown emphasis and a few HTML tags (Marker: **bold**,
    *italic*, <sub>, <sup>); they are turned into real formatting, everything else is escaped. Formula spans
    ($...$, $$...$$) are left untouched for KaTeX."""
    out = []
    for i, part in enumerate(MATH_SPAN.split(text or '')):
        if i % 2:
            out.append(escape(part, quote=False))
            continue
        x = escape(part, quote=False)
        x = _TAGS.sub(lambda m: f'<{m.group(1)}{m.group(2).lower()}{m.group(3) or ""}>' if m.group(2) else '<br>', x)
        x = re.sub(r'\*\*(?=\S)(.+?)(?<=\S)\*\*', r'<b>\1</b>', x, flags=re.S)
        x = re.sub(r'(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])', r'<i>\1</i>', x, flags=re.S)
        out.append(x.replace('\n', '<br>'))
    return ''.join(out)




def list_html(lst: dict, fmt=inline_html) -> str:
    """A table read as a list (pipeline.tables.as_list) as HTML: the key's header, then one <li> per cell, or per
    item with its 'header: value' pairs nested under it."""
    lead = (f'<div class="pv-text pv-list-label">{fmt(lst["title"])}</div>' if lst.get('title') else '') + \
        (f'<div class="pv-text pv-list-label">{fmt(lst["label"])}:</div>' if lst.get('label') else '')
    lis = []
    for it in lst['items']:
        if isinstance(it, str):
            lis.append(f'<li>{fmt(it)}</li>')
        else:
            sub = ''.join(f'<li><span class="pv-k">{fmt(k)}:</span> {fmt(v)}</li>' if k else f'<li>{fmt(v)}</li>' for k, v in it['fields'])
            lis.append(f'<li>{fmt(it["key"])}:' + (f'<ul class="pv-kv">{sub}</ul>' if sub else '') + '</li>')
    return f'{lead}<ul class="pv-text pv-items">{"".join(lis)}</ul>'
