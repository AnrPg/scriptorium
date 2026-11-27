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
A figure's caption follows the first paragraph of the same section that refers to the figure, when one does
(pipeline.references), else it stays where the figure was. Trailing credit notes of captions are dropped (the
profile's caption_credits hints). Unicode is NFC; soft hyphens and line-break hyphens are repaired; the words
are the book's own (nothing is rephrased).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from ..core.model import in_reader_output
from ..core.text import MATH_SPAN
from . import reading, tables

_BOLD = re.compile(r'\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__', re.S)
_ITALIC = re.compile(r'(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])', re.S)
_TAG = re.compile(r'</?(?:b|i|em|strong|u|span|font|small|big|mark)\b[^>]*>', re.I)
_BR = re.compile(r'<br\s*/?>', re.I)
_SUP = re.compile(r'<sup>(.*?)</sup>', re.I | re.S)
_SUB = re.compile(r'<sub>(.*?)</sub>', re.I | re.S)
_LINE_HYPHEN = re.compile(r'(\w)[-‐]\n(\w)')
_HEADING_MARKS = re.compile(r'^#+\s*')
_SENTENCE_END = re.compile(r'(?<=[.;!?·…])\s+(?=[«"(\[]?[A-ZΑ-ΩΆΈΉΊΌΎΏ0-9])')
MAX_TERM_WORDS = 6
# headings that label a box or element (pipeline.sections: «Ερώτηση 1-2», a box's sub-heading) are not sections:
# they lead their paragraph and never open a chunk or enter the heading path
BOX_LABEL_SOURCES = {'labelled_element', 'box_heading'}


# ---------------------------------------------------------------- text
def clean(text: str, strip_emphasis: bool = True) -> tuple[str, list[str]]:
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
        for m in _BOLD.finditer(x):
            t = ' '.join((m.group(1) or m.group(2) or '').split()).strip(' .,:;·')
            if t and len(t.split()) <= MAX_TERM_WORDS and not re.fullmatch(r'[\d\W]+', t):
                terms.append(t)
        if strip_emphasis:
            x = _BOLD.sub(lambda m: m.group(1) or m.group(2), x)
            x = _ITALIC.sub(r'\1', x)
            x = _TAG.sub('', x)
        x = _LINE_HYPHEN.sub(lambda m: m.group(1) + m.group(2) if m.group(2).islower() else m.group(0), x)
        out.append(x)
    s = ''.join(out)
    s = '\n'.join(' '.join(ln.split()) for ln in s.split('\n'))
    s = re.sub(r'\n{3,}', '\n\n', s).strip()
    return s, list(dict.fromkeys(terms))


def words(s: str) -> int:
    return len(re.findall(r'\w+', s))


# ---------------------------------------------------------------- tables
def table_text(html_or_text: str) -> str:
    """An engine table (HTML) as Markdown: a pipe table when it has a header row and a regular grid; otherwise
    one line per row — 'Header: value; …' when headers exist, 'a: b' for two columns, cells joined by ' | ' else."""
    if '<t' not in (html_or_text or '').lower():
        return clean(html_or_text)[0]
    p = tables.Grid()
    p.feed(html_or_text)
    rows = [[clean(c)[0].replace('|', '/') for c, _ in r] for r in p.rows]
    if not rows:
        return clean(re.sub(r'<[^>]+>', ' ', html_or_text))[0]
    head = rows[0] if all(h for _, h in p.rows[0]) else None
    body = rows[1:] if head else rows
    width = max(len(r) for r in rows)
    regular = not p.spans and all(len(r) == width for r in rows)
    if head and regular and width <= 8:
        lines = ['| ' + ' | '.join(head) + ' |', '|' + '---|' * width]
        lines += ['| ' + ' | '.join(r) + ' |' for r in body]
        return '\n'.join(lines)
    if head:
        return '\n'.join('- ' + '; '.join(f'{h}: {v}' for h, v in zip(head, r) if v) for r in body)
    if width == 2:
        return '\n'.join(f'- {r[0]}: {r[1]}' if len(r) == 2 else '- ' + ' | '.join(r) for r in body)
    return '\n'.join('- ' + ' | '.join(x for x in r if x) for r in body)


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


def units(c: dict, profile: dict, pages: set | None = None) -> list[dict]:
    """The reading model as RAG units: {'t': heading|text|equation|table|caption, 'text', 'level'?, 'pages',
    'terms', 'labels'}. Pictures and untranscribed crops never appear."""
    strip = profile.get('strip_emphasis', True)
    credits = [re.compile(p, re.I) for p in profile.get('caption_credits') or ()]
    depth = int(profile.get('heading_depth') or 6)
    nodes = reading.nodes(c, pages)
    out: list[dict] = []
    held: dict[str, list[dict]] = {}       # figure block id -> its caption units, waiting for the first reference
    lead: list[str] = []                   # titles deeper than the profile's depth: they lead the next paragraph

    def pages(n) -> list[int]:
        return sorted({b['page'] for b in n.get('blocks') or []})

    def refs_in(n) -> list[str]:
        return [t for b in n.get('blocks') or [] for t in (b.get('relations') or {}).get('refers_to') or []]

    def flush_held(all_=False, ids=()):
        for fid in (list(held) if all_ else list(dict.fromkeys(ids))):
            out.extend(held.pop(fid, []))

    def referenced_later(fid: str, i: int) -> bool:
        """A later paragraph of the same section refers to this figure."""
        for n in nodes[i + 1:]:
            if n['t'] == 'heading':
                return False
            if n['t'] == 'para' and fid in refs_in(n):
                return True
        return False

    for i, n in enumerate(nodes):
        t = n['t']
        if t == 'heading':
            flush_held(all_=True)
            title = clean(_HEADING_MARKS.sub('', n['text']), True)[0]
            if not title:
                continue
            src = ((n.get('blocks') or [{}])[0]).get('heading_level_source')
            if n.get('level', 1) <= depth and src not in BOX_LABEL_SOURCES:
                lead.clear()
                out.append({'t': 'heading', 'level': n.get('level', 1), 'text': title, 'pages': pages(n), 'terms': [], 'labels': []})
            else:
                lead.append(title)
                out.append({'t': 'subheading', 'level': n.get('level', depth + 1), 'text': title, 'pages': pages(n),
                            'terms': [], 'labels': []})
        elif t == 'para':
            text, terms = (table_text(n['text']), []) if '<table' in n['text'].lower() else clean(n['text'], strip)
            if not text:
                continue
            if lead:
                head = ' — '.join(lead)
                text = head + ('' if head.endswith(('.', ':', ';', '?', '!')) else '.') + ' ' + text
                lead.clear()
            out.append({'t': 'text', 'text': text, 'pages': pages(n), 'terms': terms, 'labels': []})
            flush_held(ids=refs_in(n))
        elif t == 'equation':
            out.append({'t': 'equation', 'text': f'$$ {n["latex"].strip()} $$', 'pages': pages(n), 'terms': [], 'labels': []})
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
            if body and 'latex' in body:
                out.append({'t': 'equation', 'text': f'$$ {body["latex"].strip()} $$', 'pages': pages(n), 'terms': [], 'labels': labels})
            elif body and body.get('items'):                  # a term grid read as a list (pipeline.tables)
                its = [x for x in (clean(i, strip)[0] for i in body['items']) if x]
                if its:
                    out.append({'t': 'list', 'text': '\n'.join(f'- {x}' for x in its), 'pages': pages(n),
                                'terms': [x for x in its if len(x.split()) <= MAX_TERM_WORDS], 'labels': labels})
            elif body and body.get('text'):
                kind = 'table' if n.get('kind') == 'table' else 'text'
                txt = table_text(body['text']) if kind == 'table' else clean(body['text'], strip)[0]
                if txt:
                    out.append({'t': kind, 'text': txt, 'pages': pages(n), 'terms': [], 'labels': labels})
            if not caps:
                continue
            unit = {'t': 'caption', 'text': '\n\n'.join(caps), 'pages': pages(n), 'terms': terms, 'labels': labels}
            fig = (n.get('blocks') or [{}])[0]
            fid = fig.get('id')
            if t == 'figure' and not body and fid and fig.get('kind') != 'caption' and referenced_later(fid, i):
                held.setdefault(fid, []).append(unit)
            else:
                out.append(unit)
        # 'image' (an untranscribed crop the reviewer kept as an image), box_start/box_end: nothing for a text reader
    flush_held(all_=True)
    return out


# ---------------------------------------------------------------- outputs
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
            if out and words(' '.join(cur['parts'])) < min_w and out[-1]['_path'] == cur['path'] \
                    and words(out[-1]['_body']) + words(' '.join(cur['parts'])) <= max_w:
                prev = out[-1]                              # a tiny tail goes back into the previous chunk
                prev['_body'] += '\n\n' + '\n\n'.join(cur['parts'])
                for k in ('pages', 'types', 'figure_refs', 'key_terms'):
                    prev[k] = list(dict.fromkeys(prev[k] + cur[k]))
            else:
                out.append({'_path': cur['path'], 'breadcrumb': cur['crumbs'], '_body': '\n\n'.join(cur['parts']),
                            'pages': cur['pages'], 'types': cur['types'], 'figure_refs': cur['figure_refs'],
                            'key_terms': cur['key_terms']})
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
