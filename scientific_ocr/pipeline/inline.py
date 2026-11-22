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


