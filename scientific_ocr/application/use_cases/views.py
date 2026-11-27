"""Use cases — read views: page, segment history, review queue, output preview, files of a book."""
from __future__ import annotations

import json
from pathlib import Path

from ...adapters import pdf as pdfio
from ...adapters.storage import Book, NotFound
from ...core import lineage
from ...core.model import REVIEW_PRIORITY, chosen_text, in_reader_output
from ...core.reasons import REASONS
from ...pipeline import html_preview, markdown, rag, tables
from ...pipeline.inline import inline_html
from ...pipeline.triage import critical
from .base import PROFILES, ServiceBase, ServiceError, _now  # noqa: F401
from .. import exports
from .export import _export_options


class ViewsService(ServiceBase):
    def page(self, book_id: str, page: int) -> dict:
        b = self._book(book_id)
        c = b.canonical() or {}
        info = next((p for p in c.get('pages', []) if p['page'] == page), None)
        dims = (c.get('source') or {}).get('page_dims_pt', {}).get(str(page))
        if dims is None:
            dims = next((p['dims'] for p in b.pages() if p['page'] == page), None) or _dims(b, page)
        pv = b.page_preview(page)                      # the page rebuilt alone after your last actions
        if pv:
            info = pv['info']
        blocks = [_block_view(x) for x in (pv['blocks'] if pv else [x for x in c.get('blocks', []) if x['page'] == page])]
        st = b.events.state()
        # A dismissal is book-wide (banked until the next full rebuild), but the reviewer's own list must drop the
        # dismissed suggestion at once; undoing the dismissal brings it back just as fast (live event state).
        dismissed = set(st['dismissed'])
        view_info = {k: (info or {}).get(k) for k in ('state', 'suggestions', 'hidden', 'replaced_by_region',
                                                       'engines', 'required', 'recommended')}
        view_info['suggestions'] = [s for s in (view_info['suggestions'] or []) if s.get('key') not in dismissed]
        return {'page': page, 'page_count': b.page_count(), 'dims': dims, 'blocks': blocks,
                'preview': {'applied': pv['applied'], 'built_utc': pv['built_utc']} if pv else None,
                'info': view_info,
                'regions': [r for r in st['regions'] if int(r['page']) == page],
                'profile': st['profiles'].get(page, 'standard')}

    def block_history(self, book_id: str, block_id: str) -> dict:
        """Every version a segment has had: machine readings, model proposals, reviewer decisions (incl. undone)."""
        b = self._book(book_id)
        blk = self._block(b, block_id)
        st = b.events.state()
        evs = [{**e, 'active': e['seq'] in st['active'], 'applies': e['seq'] in st['applies'],
                'scope': e.get('scope') or st['scope_of'].get(e['seq'])} for e in st['history']
               if e.get('block_id') == block_id or (e.get('type') == 'region_add' and e['region']['id'] == blk.get('region_id'))]
        props = [{k: r.get(k) for k in ('ts', 'note', 'proposal', 'valid', 'errors', 'model')}
                 for r in b.proposals() if r.get('block_id') == block_id]
        machine = [{'engine': c['engine'], 'text': c.get('text'), 'latex': c.get('latex') if isinstance(c.get('latex'), str)
                    else ' '.join(c.get('latex') or []) or None} for c in blk['candidates'] if c.get('text') or c.get('latex')]
        return {'machine': machine, 'proposals': props, 'events': evs, 'current': blk['chosen']}

    def render_inline(self, text: str) -> dict:
        """A segment's text rendered EXACTLY as the output renders it (same function as the exports and the page
        preview; formulas are left for KaTeX), for the reviewer's preview of a version or a draft."""
        if not isinstance(text, str) or len(text) > 50000:
            raise ServiceError('text must be a string (<= 50000 chars)')
        return {'html': inline_html(text)}

    def queue(self, book_id: str, level: str = 'required', order: str = 'priority') -> list[dict]:
        """Review queue. level: 'required', 'recommended' or 'all' (both). order 'priority': critical findings
        first, then content where errors matter most (equations, tables, captions, headings), then prose, then
        image-only material, page order within; order 'book': page then reading order (the review loop)."""
        if level not in ('required', 'recommended', 'all') or order not in ('priority', 'book'):
            raise ServiceError("level: required|recommended|all; order: priority|book")
        levels = ('required', 'recommended') if level == 'all' else (level,)
        items = [x for x in self._seen_list(self._book(book_id)) if x['review'] in levels and in_reader_output(x)]
        if order == 'book':
            items.sort(key=lambda x: (x['page'], x['reading_order']))
        else:
            items.sort(key=lambda x: (not any(critical(r) for r in x['reasons']),
                                      REVIEW_PRIORITY.get(x.get('original_kind') or x['kind'], 4), x['page'], x['reading_order']))
        return [{'id': x['id'], 'page': x['page'], 'kind': x['kind'], 'review': x['review'], 'reasons': x['reasons'][:4],
                 'reading_order': x['reading_order'], 'critical': any(critical(r) for r in x['reasons'])} for x in items]

    def preview(self, book_id: str, style: str, images: bool, chars: int = 6000, options: dict | None = None) -> str:
        """First part of the linear output in a given style (for the export dialog)."""
        c = self._book(book_id).canonical()
        if not c:
            raise ServiceError('nothing processed yet')
        if style == 'rag':                             # the RAG text of a book (application.exports.rag_settings)
            return rag.markdown(c, exports.rag_settings(c))[:chars]
        return markdown.markdown(c, images=images, style=style, options=_export_options(options))[:chars]

    def page_output(self, book_id: str, page: int, style: str = 'tags') -> dict:
        """The final output of one page: readable HTML preview + the exact markdown slice of the export."""
        b = self._book(book_id)
        c = b.canonical()
        if not c or page not in c['source']['pages_processed']:
            raise ServiceError('page not processed yet')
        if style not in markdown.STYLES:
            raise ServiceError('unknown style')
        pv = b.page_preview(page)                      # the page as rebuilt after your last actions
        if pv:
            c = {**c, 'blocks': [x for x in c['blocks'] if x['page'] != page] + pv['blocks']}
        url = lambda rel: f'/api/books/{b.id}/{rel}' if rel else ''
        rag_book = ((c.get('profile') or {}).get('settings') or {}).get('name') == 'rag'
        md = rag.markdown(c, exports.rag_settings(c), {page}) if rag_book else html_preview.page_markdown(c, page, style)
        return {'page': page, 'html': html_preview.page_html(c, page, url), 'preview': bool(pv), 'rag': rag_book,
                'markdown': md, 'release_status': c['release_status']}

    def raw_fragment(self, book_id: str, raw_file: str, pointer: str | None) -> dict:
        """The exact piece of an engine's raw output a candidate came from (read-only)."""
        b = self._book(book_id)
        root = (b.dir / 'raw').resolve()
        p = (b.dir / raw_file).resolve()
        if root not in p.parents or not p.is_file() or p.suffix != '.json':
            raise NotFound(raw_file)
        node = json.loads(p.read_text(encoding='utf-8'))
        for part in [x for x in (pointer or '').lstrip('#').split('/') if x]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        return {'file': raw_file, 'pointer': pointer, 'fragment': node}

    def page_image(self, book_id: str, page: int, width: int) -> Path:
        b = self._book(book_id)
        width = max(120, min(2400, int(width)))
        if not 1 <= page <= b.page_count():
            raise ServiceError('page out of range')
        return pdfio.thumbnail(b.source_pdf, page, width, b.dir / 'cache' / 'pages' / f'p{page:04d}-w{width}.jpg')

    def asset(self, book_id: str, rel: str) -> Path:
        b = self._book(book_id)
        root = (b.dir / 'assets').resolve()
        p = (b.dir / rel).resolve()
        if root not in p.parents or not p.is_file():
            raise NotFound(rel)
        return p


def _dims(b: Book, page: int):
    info = pdfio.page_info(b.source_pdf)
    if not 1 <= page <= len(info):
        raise ServiceError('page out of range')
    return info[page - 1]['width'], info[page - 1]['height']


def _block_view(x: dict) -> dict:
    keep = ('id', 'page', 'kind', 'original_kind', 'role', 'source', 'region_id', 'reading_order', 'review', 'reasons',
            'human_notes', 'heading_level', 'heading_level_source', 'section_path', 'flow', 'engine_label',
            'lineage', 'content_source', 'caption_role', 'caption_label', 'caption_role_source', 'references',
            'merge_id', 'merged_from', 'groups', 'entities', 'excluded', 'omitted', 'profile_omitted', 'kept', 'text_norm', 'notation',
            'chosen', 'display_crop', 'rule_corrections', 'evidence', 'decisions', 'figure_text_candidates', 'relations',
            'match', 'flow_override', 'continues_override', 'excursus', 'table_layout', 'table_list')
    v = {k: x.get(k) for k in keep if x.get(k) not in (None, [], {})}
    v['reasons'] = x['reasons']                        # always present in the view model, even when empty
    if x['kind'] == 'table' and (items := tables.as_list(x, chosen_text(x))):
        v['list_items'] = items                        # how the outputs read it (pipeline.tables): shown as such
    v['lineage_dag'] = lineage.dag(x)
    v['bbox'] = x['geometry']['bbox']
    v['crop'] = (x.get('source_crop') or {}).get('path')
    v['candidates'] = [{k: (json.dumps(c[k], ensure_ascii=False)[:300] if k == 'rdkit' else c[k])
                        for k in ('engine', 'status', 'text', 'latex', 'smiles', 'error', 'rdkit')
                        if c.get(k) not in (None, '', [])} for c in x['candidates']]
    for c in v['candidates']:
        if isinstance(c.get('latex'), list):
            c['latex'] = ' '.join(c['latex'])
    v['severity'] = 'critical' if any(REASONS[r].finding and REASONS[r].finding[1] == 'critical'
                                      for r in x['reasons'] if r in REASONS) else None
    return v

