"""Stage (publish): a static, self-contained review report for offline sharing (read-only).

Interactive review happens in the app (`python -m scientific_ocr ui`); this file is a snapshot.
"""
from __future__ import annotations

import html
import json

from ..core.reasons import text as reason_text

CSS = """body{font:14px/1.45 -apple-system,Helvetica,Arial,sans-serif;margin:24px;max-width:1200px;color:#222}
.item{border:1px solid #ddd;border-radius:8px;margin:16px 0;padding:12px}.required{border-left:5px solid #b42318}
.recommended{border-left:5px solid #b54708}.grid{display:grid;grid-template-columns:minmax(280px,48%) 1fr;gap:16px}
img{max-width:100%;border:1px solid #ccc}.c{background:#f6f6f4;border-radius:6px;padding:6px 8px;margin:6px 0;
white-space:pre-wrap;font-size:13px}.meta{color:#666;font-size:12px}"""


def report_html(c: dict) -> str:
    e = html.escape
    items = sorted((b for b in c['blocks'] if b['review'] in ('required', 'recommended')),
                   key=lambda b: (b['review'] != 'required', b['page'], b['reading_order']))
    parts = [f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Review snapshot</title>'
             f'<style>{CSS}</style></head><body><h1>Review snapshot — {e(c["release_status"])}</h1>'
             f'<p class="meta">sha256 {e(c["source"]["sha256"])} · pages {e(str(c["source"]["pages_processed"]))} · '
             f'built {e(c["run"]["built_utc"])} · {len(items)} open items. Review interactively in the app.</p>']
    for b in items:
        ch = b['chosen']
        body = ch.get('text') or ch.get('latex') or f'[{ch["type"]}]'
        cands = ''.join(f'<div class="c"><b>{e(x["engine"])}</b> {e(str(x.get("text") or x.get("latex") or x.get("smiles") or ""))[:1500]}</div>'
                        for x in b['candidates'])
        crop = (b.get('source_crop') or {}).get('path', '')
        parts.append(f'<div class="item {b["review"]}"><b>p.{b["page"]} · {e(b["kind"])}</b> <span class="meta">{e(b["id"])}</span>'
                     f'<ul>{"".join(f"<li>{e(reason_text(r))}</li>" for r in b["reasons"])}</ul>'
                     f'<div class="grid"><div><img src="{e(crop)}" loading="lazy"></div><div>'
                     f'<div class="c"><b>chosen</b> {e(body)}</div>{cands}'
                     f'<p class="meta">{e(json.dumps(b.get("evidence") or {}, ensure_ascii=False))[:600]}</p></div></div></div>')
    parts.append('</body></html>')
    return '\n'.join(parts)
