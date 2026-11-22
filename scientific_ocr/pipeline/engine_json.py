"""Anti-corruption layer: raw Marker/Docling JSON -> uniform engine blocks (raw files untouched).

Engine block: {ref, engine, page, label, kind, role, text, html, latex, bbox, order, group,
               raw_pointer, extra}
bbox: PDF points, TOPLEFT origin. `order` is the engine's own reading order on that page.
"""
from __future__ import annotations

import html as htmllib
import re

from bs4 import BeautifulSoup, NavigableString

MARKER_KIND = {
    'Text': 'paragraph', 'TextInlineMath': 'paragraph', 'Handwriting': 'paragraph', 'Form': 'paragraph',
    'Code': 'paragraph', 'Reference': 'paragraph', 'Bibliography': 'paragraph', 'TableOfContents': 'paragraph',
    'ListItem': 'paragraph', 'Footnote': 'paragraph', 'PageHeader': 'paragraph', 'PageFooter': 'paragraph',
    'SectionHeader': 'heading', 'Caption': 'caption', 'Equation': 'math', 'Table': 'table',
    'Picture': 'figure', 'Figure': 'figure', 'Diagram': 'figure', 'ComplexRegion': 'figure',
    'ChemicalBlock': 'molecular_structure',
}
MARKER_ROLE = {'PageHeader': 'page_header', 'PageFooter': 'page_footer', 'Footnote': 'footnote',
               'ListItem': 'list_item'}
MARKER_GROUPS = {'FigureGroup', 'PictureGroup', 'TableGroup', 'ListGroup'}

DOCLING_KIND = {
    'text': 'paragraph', 'paragraph': 'paragraph', 'list_item': 'paragraph', 'footnote': 'paragraph',
    'page_header': 'paragraph', 'page_footer': 'paragraph', 'title': 'heading', 'section_header': 'heading',
    'caption': 'caption', 'formula': 'math', 'code': 'paragraph', 'reference': 'paragraph',
    'checkbox_selected': 'paragraph', 'checkbox_unselected': 'paragraph', 'picture': 'figure',
    'chart': 'figure', 'table': 'table', 'document_index': 'table', 'key_value_region': 'paragraph',
    'form': 'paragraph', 'handwritten_text': 'paragraph', 'empty_value': 'paragraph',
}
DOCLING_ROLE = {'page_header': 'page_header', 'page_footer': 'page_footer', 'footnote': 'footnote',
                'list_item': 'list_item'}


# ---------------------------------------------------------------- label catalog (reviewer re-tagging)
# Every label a reviewer may assign, in each engine's own vocabulary, with its canonical meaning and the Marker
# block type used to re-OCR the region (Marker's --force_layout_block accepts its own block types).
LABEL_EL = {
    'Text': 'Κείμενο', 'TextInlineMath': 'Κείμενο με μαθηματικά', 'SectionHeader': 'Επικεφαλίδα ενότητας',
    'Caption': 'Λεζάντα', 'Equation': 'Εξίσωση', 'Table': 'Πίνακας', 'Picture': 'Εικόνα', 'Figure': 'Σχήμα',
    'Diagram': 'Διάγραμμα', 'ChemicalBlock': 'Χημικό μπλοκ', 'ListItem': 'Στοιχείο λίστας', 'Footnote': 'Υποσημείωση',
    'PageHeader': 'Κεφαλίδα σελίδας', 'PageFooter': 'Υποσέλιδο', 'Code': 'Κώδικας', 'Form': 'Φόρμα',
    'Handwriting': 'Χειρόγραφο', 'TableOfContents': 'Πίνακας περιεχομένων', 'Reference': 'Παραπομπή',
    'Bibliography': 'Βιβλιογραφία', 'ComplexRegion': 'Σύνθετη περιοχή',
    'text': 'Κείμενο', 'paragraph': 'Παράγραφος', 'title': 'Τίτλος', 'section_header': 'Επικεφαλίδα ενότητας',
    'caption': 'Λεζάντα', 'formula': 'Τύπος/εξίσωση', 'table': 'Πίνακας', 'picture': 'Εικόνα', 'chart': 'Διάγραμμα',
    'list_item': 'Στοιχείο λίστας', 'footnote': 'Υποσημείωση', 'page_header': 'Κεφαλίδα σελίδας',
    'page_footer': 'Υποσέλιδο', 'code': 'Κώδικας', 'form': 'Φόρμα', 'handwritten_text': 'Χειρόγραφο',
    'reference': 'Παραπομπή', 'document_index': 'Ευρετήριο', 'key_value_region': 'Ζεύγη κλειδιού-τιμής',
}
DOCLING_TO_MARKER = {
    'text': 'Text', 'paragraph': 'Text', 'title': 'SectionHeader', 'section_header': 'SectionHeader',
    'caption': 'Caption', 'formula': 'Equation', 'table': 'Table', 'picture': 'Picture', 'chart': 'Figure',
    'list_item': 'ListItem', 'footnote': 'Footnote', 'page_header': 'PageHeader', 'page_footer': 'PageFooter',
    'code': 'Code', 'form': 'Form', 'handwritten_text': 'Handwriting', 'reference': 'Reference',
    'document_index': 'TableOfContents', 'key_value_region': 'Form',
}
IMAGE_ONLY_LABELS = {'Picture', 'Figure', 'Diagram', 'ComplexRegion'}   # kept as images, no text OCR


def label_catalog() -> dict:
    def entry(engine, name):
        kind = (MARKER_KIND if engine == 'marker' else DOCLING_KIND).get(name, 'unknown')
        role = (MARKER_ROLE if engine == 'marker' else DOCLING_ROLE).get(name)
        return {'engine': engine, 'name': name, 'el': LABEL_EL.get(name, name), 'kind': kind, 'role': role}
    return {'marker': [entry('marker', n) for n in LABEL_EL if n[0].isupper()],
            'docling': [entry('docling', n) for n in LABEL_EL if n[0].islower()]}


def region_spec(label: dict) -> dict:
    """Engine label -> canonical kind/role + how to re-OCR the region (Marker block type or image only)."""
    engine, name = label.get('engine'), label.get('name')
    table, roles = (MARKER_KIND, MARKER_ROLE) if engine == 'marker' else (DOCLING_KIND, DOCLING_ROLE)
    if name not in LABEL_EL or name not in table:
        raise ValueError(f'unknown {engine} label {name!r}')
    force = name if engine == 'marker' else DOCLING_TO_MARKER[name]
    return {'kind': table[name], 'role': roles.get(name), 'force_block': force,
            'image_only': force in IMAGE_ONLY_LABELS}


LEGACY_REGION_KIND = {'text': ('marker', 'Text'), 'heading': ('marker', 'SectionHeader'), 'math': ('marker', 'Equation'),
                      'table': ('marker', 'Table'), 'caption': ('marker', 'Caption'), 'figure': ('marker', 'Picture')}


def region_label(region: dict) -> dict:
    """Label of a region event (older events stored a plain 'kind')."""
    if region.get('label'):
        return region['label']
    eng, name = LEGACY_REGION_KIND.get(region.get('kind', 'text'), ('marker', 'Text'))
    return {'engine': eng, 'name': name}


def html_to_md(html: str) -> tuple[str, list[str]]:
    """Marker block HTML -> Markdown-ish text; returns (text, latex_spans). Math kept verbatim."""
    if not html:
        return '', []
    soup = BeautifulSoup(html, 'html.parser')
    latex: list[str] = []

    def conv(node) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        name = node.name
        if name == 'content-ref':
            return ''
        if name == 'math':
            tex = node.get_text()
            latex.append(tex)
            return f'\n$$\n{tex}\n$$\n' if node.get('display') == 'block' else f'${tex}$'
        if name == 'table':
            return '\n' + str(node) + '\n'
        inner = ''.join(conv(c) for c in node.children)
        if name in ('b', 'strong'):
            return f'**{inner.strip()}**' if inner.strip() else inner
        if name in ('i', 'em'):
            return f'*{inner.strip()}*' if inner.strip() else inner
        if name in ('sup', 'sub'):
            return f'<{name}>{inner}</{name}>'
        if name == 'br':
            return '\n'
        if name == 'li':
            return f'- {inner.strip()}\n'
        if name in ('p', 'div', 'ul', 'ol', 'blockquote') or re.fullmatch(r'h[1-6]', name or ''):
            return inner.strip() + '\n'
        return inner
    text = conv(soup)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return htmllib.unescape(text), latex




def marker_blocks(raw: dict, page: int) -> list[dict]:
    """`page` is the canonical 1-based page (Marker ids are 0-based: /page/2/... == page 3)."""
    out: list[dict] = []
    pages = [c for c in raw.get('children') or [] if c.get('block_type') == 'Page']
    if len(pages) != 1:
        raise ValueError(f'expected 1 Marker page, got {len(pages)}')
    pg = pages[0]
    m = re.match(r'/page/(\d+)/', pg.get('id', ''))
    if not m or int(m.group(1)) != page - 1:
        raise ValueError(f'Marker page id {pg.get("id")} does not match canonical page {page}')

    def walk(block, pointer, group):
        bt = block.get('block_type')
        if bt in MARKER_GROUPS:
            for i, c in enumerate(block.get('children') or []):
                walk(c, f'{pointer}/children/{i}', block['id'])
            return
        text, latex = html_to_md(block.get('html') or '')
        out.append({'ref': 'marker:' + block['id'], 'engine': 'marker', 'page': page, 'label': bt,
                    'kind': MARKER_KIND.get(bt, 'unknown'), 'role': MARKER_ROLE.get(bt), 'text': text,
                    'html': block.get('html') or '', 'latex': latex,
                    'bbox': [float(v) for v in block['bbox']], 'order': len(out), 'group': group,
                    'raw_pointer': pointer, 'extra': {'images': sorted((block.get('images') or {}).keys()),
                                                      **_heading_level(bt, block.get('html') or '')}})
    for i, c in enumerate(pg.get('children') or []):
        walk(c, f'/children/0/children/{i}', None)
    return out


def _heading_level(block_type: str, html: str) -> dict:
    m = re.search(r'<h([1-6])\b', html) if block_type == 'SectionHeader' else None
    return {'heading_level': int(m.group(1))} if m else {}


def _resolve(doc: dict, ref: str):
    parts = ref.lstrip('#/').split('/')
    node = doc
    for p in parts:
        node = node[int(p)] if isinstance(node, list) else node[p]
    return node


def docling_blocks(raw: dict, page: int, page_height: float) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()

    def add(item, group):
        ref = item['self_ref']
        if ref in seen:
            return
        seen.add(ref)
        provs = [p for p in item.get('prov') or [] if p.get('page_no') == page]
        label = item.get('label', '')
        if provs:
            bb = provs[0]['bbox']
            if bb.get('coord_origin', 'BOTTOMLEFT') == 'BOTTOMLEFT':
                bbox = [bb['l'], page_height - bb['t'], bb['r'], page_height - bb['b']]
            else:
                bbox = [bb['l'], bb['t'], bb['r'], bb['b']]
            text = item.get('text') or ''
            extra = {'heading_level': item['level']} if label == 'section_header' and item.get('level') else {}
            if ref.startswith('#/pictures/'):
                classes = [a for a in item.get('annotations') or [] if a.get('kind') == 'classification']
                if not classes and item.get('meta', {}).get('classification'):
                    classes = [item['meta']['classification']]
                extra['classification'] = [{**c, 'predicted_classes': (c.get('predicted_classes') or [])[:3]}
                                           for c in classes]          # top-3 only; full list stays in raw
                extra['caption_refs'] = [c['$ref'] for c in item.get('captions') or []]
            if ref.startswith('#/tables/'):
                cells = item.get('data', {}).get('table_cells') or []
                text = ' | '.join(c.get('text', '') for c in cells)
                extra['caption_refs'] = [c['$ref'] for c in item.get('captions') or []]
            out.append({'ref': 'docling:' + ref, 'engine': 'docling', 'page': page, 'label': label,
                        'kind': DOCLING_KIND.get(label, 'unknown'), 'role': DOCLING_ROLE.get(label),
                        'text': text, 'html': '', 'latex': [text] if label == 'formula' else [],
                        'bbox': [round(v, 2) for v in bbox], 'order': len(out), 'group': group,
                        'raw_pointer': ref, 'extra': extra})
        for ch in item.get('children') or []:
            add(_resolve(raw, ch['$ref']), ref if ref.startswith('#/groups/') else group)

    for root in ('body', 'furniture'):
        if root in raw:
            for ch in raw[root].get('children') or []:
                add(_resolve(raw, ch['$ref']), None)
    # Anything not reachable from the tree is still kept (never silently dropped).
    for coll in ('texts', 'pictures', 'tables'):
        for item in raw.get(coll) or []:
            add(item, None)
    return out
