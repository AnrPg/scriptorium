"""Stage (document level): GLOSSARY — the book's dictionary of terms («Λεξικό επιστημονικών όρων», «Γλωσσάριο»,
«Glossary»): pages of ENTRIES, each a bold term followed by its definition, under letter dividers («Α», «Γ / C / G»).

Definitions are what a reader (and a retrieval agent) looks a term up for, so they are kept as entries, not as
anonymous paragraphs:
  annotate(blocks, dismissed, pages=None)  marks b['glossary'] = {'terms': [...]} on the entries' segments and
                                           b['glossary_letter'] = True on the letter dividers; returns the summary
                                           {'label', 'pages', 'evidence'} (canonical 'glossary').
  split(text, terms)                       (leading text, [(term, definition)]): ONE reading of a paragraph's
                                           entries, shared by the RAG units, its audit and the canonical entries.
  entries(c)                               every entry with its segments and pages (canonical 'glossary.entries').

A HINT (feedback: book conventions are checked hints): a page is a glossary page only with evidence — at least
MIN_ENTRIES entries making up most of its body paragraphs, AND a running head naming a glossary or the terms in
alphabetical order; a page between two glossary pages with an entry continues the run. An entry's term is the
bold run that opens a paragraph (or a line of it) and is followed by a definition starting a sentence: a numbered
label («ΑΠΑΝΤΗΣΗ 7-17.»), a colon label («ΠΡΟΣΚΟΛΛΗΣΗ:») or a bold subject inside a sentence («Τα ινίδια ακτίνης
είναι…») is not. The reviewer's word wins (glossary_page event): a page marked a glossary is one without evidence, a page
marked not one reads as text.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from ..core.labels import LABELLED
from ..core.text import strip_emphasis

GLOSSARY_HEAD = re.compile(r'λεξικ|γλωσσαρ|ορολογ|glossary|vocabulary|terminology|definitions', re.I)
_LEAD = re.compile(r'^[ \t]*(?:\*\*(?P<a>(?:[^*\n]|\*(?!\*))+?)\*\*(?!\*)|<b>(?P<b>[^<\n]+?)</b>|<strong>(?P<c>[^<\n]+?)</strong>)'
                   r'[ \t]*(?P<rest>\S.*)?$', re.M)
_DEF_START = re.compile(r'^[(«"$\[]|^[A-ZΑ-ΩΆΈΉΊΌΎΏ0-9]', re.U)
MIN_ENTRIES = 3
MAX_TERM_WORDS = 14
BODY_KINDS = ('paragraph', 'text', 'list')


def source(b: dict) -> str:
    """The segment's text as read, WITH its styling (the RAG profile strips it before review: profile_source)."""
    ch = b.get('chosen') or {}
    if ch.get('type') != 'text':
        return ''
    return ch.get('profile_source') or b.get('text_norm') or ch.get('text') or ''


def _term(raw: str, rest: str | None) -> str | None:
    t = ' '.join(strip_emphasis(raw).split()).rstrip(' .,·')
    if not t or not rest or t.endswith(':') or len(t.split()) > MAX_TERM_WORDS or not re.search(r'\w', t):
        return None
    if LABELLED.match(t) and re.search(r'\d', t):          # «ΑΠΑΝΤΗΣΗ 7-17»: a numbered label, not a term
        return None
    rest = strip_emphasis(rest).lstrip(' .,·—–-')
    return t if rest and _DEF_START.match(rest) else None


def lead_terms(text: str) -> list[str]:
    """The entries' terms of a paragraph, in order: bold runs opening the paragraph or one of its lines."""
    return [t for m in _LEAD.finditer(text or '') if (t := _term(m.group('a') or m.group('b') or m.group('c'), m.group('rest')))]


def _fold(s: str) -> str:
    s = unicodedata.normalize('NFD', s.lower())
    return ''.join(ch for ch in s if not unicodedata.combining(ch))


def _alphabetical(terms: list[str]) -> bool:
    """Most neighbouring terms of the same script are in order (Latin terms are filed among Greek ones)."""
    keys = [_fold(t)[:1] for t in terms]
    pairs = [(a, b) for a, b in zip(keys, keys[1:]) if a.isalpha() and b.isalpha() and (a < 'ά') == (b < 'ά')]
    return len(pairs) >= 3 and sum(a <= b for a, b in pairs) >= 0.85 * len(pairs)


def _head_text(b: dict) -> str:
    texts = [b.get('chosen', {}).get('text') or ''] + [c.get('text') or '' for c in b.get('candidates') or []]
    return next((t for t in texts if GLOSSARY_HEAD.search(t or '')), '')


def _letter(text: str) -> bool:
    parts = [p.strip() for p in strip_emphasis(re.sub(r'^#+\s*', '', text or '')).split('/')]
    return bool(parts) and all(0 < len(p) <= 2 and p.isalpha() for p in parts)


def annotate(blocks: list[dict], marked: dict | None = None, pages: list[int] | None = None) -> dict:
    """Marks the glossary's entries and letter dividers; returns {'label', 'pages', 'evidence', 'marked'}.
    marked: {page: True/False}, the reviewer's word (glossary_page events). pages: the pages already known (a page
    preview keeps the full build's decision); None detects them."""
    marked = {int(p): v for p, v in (marked or {}).items()}
    for b in blocks:
        b.pop('glossary', None)
        b.pop('glossary_letter', None)
    by_page: dict[int, list[dict]] = {}
    for b in blocks:
        by_page.setdefault(b['page'], []).append(b)
    terms = {b['id']: lead_terms(source(b)) for b in blocks       # a region read again (an excerpt with text) too
             if (b['kind'] in BODY_KINDS or (b['kind'] == 'excerpt' and source(b))) and not b.get('role') and not b.get('excluded')}
    heads = {p: t for p, bs in by_page.items() for b in bs if b.get('role') == 'page_header' and (t := _head_text(b))}
    evidence: dict[int, str] = {}
    if pages is None:
        for p, bs in sorted(by_page.items()):
            body = [b for b in bs if b['id'] in terms]
            ts = [t for b in body for t in terms[b['id']]]
            with_terms = sum(bool(terms[b['id']]) for b in body)
            if len(ts) < MIN_ENTRIES or with_terms < 0.5 * len(body):
                continue
            if p in heads:
                evidence[p] = f'running head «{heads[p].strip()}», {len(ts)} entries'
            elif len(ts) >= 5 and with_terms >= 0.7 * len(body) and _alphabetical(ts):
                evidence[p] = f'{len(ts)} entries in alphabetical order'
        found = sorted(evidence)
        for p in sorted(by_page):                              # a page inside the run with an entry continues it
            if p not in evidence and any(a < p for a in found) and any(a > p for a in found) \
                    and (p - 1 in evidence or p + 1 in evidence) and any(terms.get(b['id']) for b in by_page[p]):
                evidence[p] = 'between glossary pages'
        evidence.update({p: 'marked by the reviewer' for p, v in marked.items() if v and p in by_page})
        pages = sorted(p for p in evidence if marked.get(p, True))
    pages = sorted(set(pages))
    for p in pages:
        for b in by_page.get(p, []):
            if b['kind'] in ('heading', *BODY_KINDS) and not b.get('role') and _letter(source(b)):
                b['glossary_letter'] = True               # «Α», «Γ / C / G»: a divider, whatever the engine called it
            elif b['id'] in terms:                        # an entry, or the rest of one (continued from a column)
                b['glossary'] = {'terms': terms[b['id']]}
    return {'label': _label([heads[p] for p in pages if p in heads]), 'pages': pages,
            'evidence': {str(p): evidence.get(p, 'known') for p in pages},
            'marked': {str(p): v for p, v in sorted(marked.items())}}


def _label(texts: list[str]) -> str:
    """The glossary's name as its running head prints it (the most frequent; mixed case preferred), else generic."""
    names = [' '.join(re.sub(r'[\d■•|]+', ' ', t).split()) for t in texts]
    names = [n for n in (' '.join(re.sub(r'[^\w\s]', ' ', x).split()) for x in names) if GLOSSARY_HEAD.search(n)]
    if not names:
        return ''
    if mixed := [n for n in names if not n.isupper()]:   # the clean title (OCR of capitals drops spaces, letters)
        return Counter(mixed).most_common(1)[0][0]
    common = Counter(_fold(n) for n in names).most_common(1)[0][0]
    same = [n for n in names if _fold(n) == common]
    return next((n for n in same if not n.isupper()), same[0]).strip()


def split(text: str, terms: list[str]) -> tuple[str, list[tuple[str, str]]]:
    """(text before the first entry, [(term, definition)]): each known term opening the text or one of its lines
    starts an entry; its definition runs to the next entry. Terms are matched with or without their styling."""
    if not terms or not text:
        return text or '', []
    from .rag import clean
    starts = []
    pos = 0
    for t in terms:
        m = None
        for form in dict.fromkeys((t, clean(t)[0])):  # as read, or in the output's notation (<sub> → LaTeX)
            rx = re.compile(r'(?:^|\n)[ \t]*(?:\*\*|<b>|<strong>)?' + re.escape(form) + r'\.?(?:\*\*|</b>|</strong>)?[.,]?[ \t]*')
            if (m := rx.search(text, pos)):
                break
        if not m:
            continue
        starts.append((m.start() + (1 if text[m.start()] == '\n' else 0), m.end(), t))
        pos = m.end()
    if not starts:
        return text, []
    head = text[:starts[0][0]].strip()
    out = []
    for i, (s, e, t) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        d = ' '.join(text[e:end].split())
        if d:
            out.append((t, d))
    return head, out


def labels(text: str) -> tuple[str, str]:
    """The words that introduce a term and its definition, in the entry's language."""
    greek = len(re.findall(r'[α-ωά-ώΑ-ΩΆ-Ώ]', text)) >= len(re.findall(r'[A-Za-z]', text))
    return ('Όρος', 'Ορισμός') if greek else ('Term', 'Definition')


def entries(c: dict) -> list[dict]:
    """Every entry of the glossary in reading order: {'term', 'definition', 'letter', 'ids', 'pages'} (plain text,
    as the outputs read it). A paragraph continued on the next page is one entry (pipeline.reading joins it)."""
    from . import reading
    from .rag import clean
    out: list[dict] = []
    letter = ''
    for n in reading.nodes(c):
        bs = n.get('blocks') or []
        if bs and bs[0].get('glossary_letter'):
            letter = clean(re.sub(r'^#+\s*', '', n['text']))[0]
            continue
        if n['t'] != 'para' or not any(b.get('glossary') for b in bs):
            continue
        terms = [t for b in bs for t in (b.get('glossary') or {}).get('terms', [])]
        head, pairs = split(n['text'], terms)
        if head and out:
            out[-1]['definition'] = (out[-1]['definition'] + ' ' + clean(head)[0]).strip()
        for t, d in pairs:
            out.append({'term': clean(t)[0], 'definition': clean(d)[0], 'letter': letter,
                        'ids': [b['id'] for b in bs], 'pages': sorted({b['page'] for b in bs})})
    return out
