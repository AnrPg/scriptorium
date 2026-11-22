"""Stage: every formula in the final content (equations and inline $...$ in text) is rendered with the same
KaTeX + mhchem build the preview and the PDF export use. A formula that does not render is flagged
LATEX_NOT_RENDERABLE: its source image is then shown next to it in every export, so no reader is left with raw
LaTeX that the preview silently rendered (or failed to). The LaTeX itself is never changed."""
from __future__ import annotations

import re

from ..core.model import in_output
from ..core.reasons import flag

INLINE = re.compile(r'(?<!\\)\$\$(.+?)(?<!\\)\$\$|(?<!\\)\$([^$\n]+?)(?<!\\)\$', re.S)


def formulas(b: dict) -> list[tuple[str, bool]]:
    ch = b['chosen']
    if ch['type'] == 'latex':
        return [(ch.get('latex') or '', True)]
    if ch['type'] == 'text':
        text = b.get('text_norm') or ch.get('text') or ''       # the notation that is exported (pipeline.notation)
        return [((m.group(1) or m.group(2)).strip(), bool(m.group(1))) for m in INLINE.finditer(text)]
    return []


def annotate(blocks: list[dict], check) -> dict:
    """`check`: ports.LatexChecker (the app's KaTeX + mhchem renderer)."""
    items, owner = [], {}
    for b in filter(in_output, blocks):
        for i, (tex, display) in enumerate(formulas(b)):
            key = f'{b["id"]}#{i}'
            items.append({'id': key, 'latex': tex, 'display': display})
            owner[key] = (b, tex)
    try:
        errors = check(items)
    except Exception as exc:                          # recorded, never fatal
        return {'status': 'failed', 'error': str(exc)[:300], 'formulas': len(items)}
    if errors is None:
        return {'status': 'unavailable', 'formulas': len(items),
                'note': 'node not found: formulas were not checked against the renderer'}
    for key, err in errors.items():
        b, tex = owner[key]
        flag(b, 'LATEX_NOT_RENDERABLE', latex=tex[:200], error=err)
    return {'status': 'checked', 'formulas': len(items), 'not_renderable': len(errors)}
