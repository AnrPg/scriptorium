"""Use case — export formats, all derived from canonical.json (Strategy registry EXPORTERS).

  markdown_images  ZIP: <title>.md + the images it shows — a standalone document (reader-facing)
  markdown_text    single .md; images become a short [Εικόνα]/[Figure] placeholder (reader-facing)
  pdf              typeset document printed from the same HTML as the preview (reader-facing)
  normalized_json  flat, ordered list of segments with provenance and review state (data, for programs)
  canonical_json   the full projection: bboxes, all candidates, reasons, findings, provenance (data)
Reader-facing formats come from the reading model (pipeline.reading): content and light separators only, no
ids, review marks, source pages or traces of how the document was produced.
"""
from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from pathlib import Path
from typing import Callable

import pymupdf

from ..adapters import latex_render
from ..adapters.storage import read_json
from ..core.model import in_output
from ..core.reasons import text as reason_text
from ..pipeline import notation
from ..pipeline.html_preview import body_html, document_html
from ..pipeline.markdown import markdown


def normalized(c: dict) -> dict:
    toc = {s['id']: s for s in c.get('sections', [])}
    segs = []
    for b in filter(in_output, c['blocks']):
        ch = b['chosen']
        segs.append({
            'id': b['id'], 'page': b['page'], 'order': b['reading_order'], 'kind': b.get('original_kind') or b['kind'],
            'role': b.get('role'),
            'content_type': ch['type'], 'text': (b.get('text_norm') or ch.get('text')) if ch['type'] == 'text' else ch.get('text'),
            'text_as_written': ch.get('text') if b.get('text_norm') else None, 'latex': ch.get('latex'),
            'content_source': b.get('content_source'),
            'image': (b.get('source_crop') or {}).get('path') if (b.get('display_crop') or ch['type'] != 'text') else None,
            'caption_for': b['relations'].get('caption_for'), 'caption_role': b.get('caption_role'),
            'caption_label': b.get('caption_label'), 'captions': b['relations'].get('captions') or None,
            'flow': b.get('flow'), 'in_reader_output': not b.get('omitted'),
            'references': [{k: r.get(k) for k in ('text', 'target', 'status', 'panel')} for r in b.get('references', [])] or None,
            'referenced_by': b['relations'].get('referenced_by'), 'groups': b.get('groups'),
            'merged_from': [m['id'] for m in b.get('merged_from', [])] or None,
            'continues_from': b['relations'].get('continues_from'), 'continues_in': b['relations'].get('continues_in'),
            'interrupted_by': b['relations'].get('interrupted_by'),
            'continues_strand': b['relations'].get('continues_strand'), 'excursus': b.get('excursus'),
            'heading_level': b.get('heading_level') if b['kind'] == 'heading' else None,
            'section_path': [{'id': i, 'level': toc[i]['level'], 'title': toc[i]['title']}
                             for i in b.get('section_path', []) if i in toc],
            'review': b['review'],
            'verified': b['review'] in ('approved', 'image_only'),
            'notes': [reason_text(r) for r in b['reasons']],
            'bbox_pt': b['geometry']['bbox'],
        })
    return {'document_id': c['document_id'], 'source_sha256': c['source']['sha256'],
            'text_format': notation.FORMAT + ' (text); equation segments: latex field without delimiters',
            'release_status': c['release_status'], 'pages': c['source']['pages_processed'],
            'coordinate_system': c['source']['coord_system'], 'sections': c.get('sections', []),
            'groups': c.get('groups', []), 'segments': segs}


def _title(book_dir: Path, c: dict) -> str:
    return ((read_json(book_dir / 'book.json', {}) or {}).get('title') or Path(c['source'].get('input_path') or 'document').stem)


def _filename(title: str) -> str:
    """Readable, portable file name in the book's own script (letters/digits of any alphabet kept)."""
    return re.sub(r'[\s_]+', '-', re.sub(r'[^\w\s-]+', ' ', title)).strip('-')[:80] or 'document'


def _zip(book_dir: Path, c: dict, style: str, options: dict | None = None) -> bytes:
    names: dict[str, str] = {}                        # neutral image names in reading order: images/001.png …
    md = markdown(c, images=True, style=style, options=options,
                  image_url=lambda p: names.setdefault(p, f'images/{len(names) + 1:03d}{Path(p).suffix}'))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(f'{_filename(_title(book_dir, c))}.md', md)
        for src, name in names.items():
            if (book_dir / src).exists():
                z.write(book_dir / src, name)
    return buf.getvalue()


_PDF_CSS = """body{font-family:sans-serif;font-size:10pt;line-height:1.35}
h2{font-size:13pt;margin:8pt 0 4pt}.meta{color:#666;font-size:8pt}.lx{font-family:monospace;font-size:9pt;
background:#f3f3f3}.warn{color:#9a3412;font-size:8pt}p{margin:0 0 6pt 0}"""


def _pdf(book_dir: Path, c: dict, style: str, options: dict | None = None) -> bytes:
    """PDF = the preview renderer printed by local headless Chrome: identical maths (KaTeX + mhchem), captions
    inside figures, furniture excluded. Without Chrome/Chromium: the basic PDF below, which says so."""
    work = book_dir / 'cache' / 'export'
    work.mkdir(parents=True, exist_ok=True)
    page_file = work / f'document-{uuid.uuid4().hex[:8]}.html'      # per request: concurrent exports never collide
    static = latex_render.KATEX_DIR.parent.parent
    doc = document_html(c, lambda rel: (book_dir / rel).resolve().as_uri(), lambda rel: (static / rel).resolve().as_uri(),
                        title=_title(book_dir, c), options=options)
    page_file.write_text(doc, encoding='utf-8')
    try:
        pdf = latex_render.print_pdf(page_file)
    finally:
        page_file.unlink(missing_ok=True)
    return pdf if pdf is not None else _pdf_basic(book_dir, c, style, options)


def _pdf_basic(book_dir: Path, c: dict, style: str, options: dict | None = None) -> bytes:
    """Fallback without a browser (PyMuPDF Story): the same reader HTML; formulas stay in LaTeX notation."""
    html = f'<h1>{_title(book_dir, c)}</h1>' + body_html(c, lambda rel: rel, reviewer=False, options=options)
    story = pymupdf.Story(html=html, user_css=_PDF_CSS, archive=pymupdf.Archive(str(book_dir)))
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    rect, more = pymupdf.paper_rect('a4'), True
    while more:
        dev = writer.begin_page(rect)
        more, _ = story.place(rect + (42, 42, -42, -42))
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()


EXPORTERS: dict[str, tuple[Callable[..., bytes], str, str]] = {
    'markdown_images': (_zip, 'application/zip', '.zip'),
    'markdown_text': (lambda d, c, st, o=None: markdown(c, images=False, style=st, options=o).encode(),
                      'text/markdown; charset=utf-8', '.md'),
    'pdf': (_pdf, 'application/pdf', '.pdf'),
    'normalized_json': (lambda d, c, st, o=None: json.dumps(normalized(c), ensure_ascii=False, indent=2).encode(),
                        'application/json', '.segments.json'),
    'canonical_json': (lambda d, c, st, o=None: json.dumps(c, ensure_ascii=False, indent=2).encode(), 'application/json',
                       '.canonical.json'),
}
STYLED = {'markdown_images', 'markdown_text'}
CONTENT_OPTIONS = {'markdown_images', 'markdown_text', 'pdf'}     # reader-facing: entity filters + figure placement


def export(book_dir: Path, c: dict, fmt: str, style: str = 'tags', options: dict | None = None) -> tuple[bytes, str, str]:
    fn, ctype, ext = EXPORTERS[fmt]
    return fn(book_dir, c, style, options if fmt in CONTENT_OPTIONS else None), ctype, ext
