"""Stage: ONE notation for the content of text segments (derived; the source text is never changed).

Engines and reviewers write the same thing in different ways: Marker's prose uses HTML scripts
("H<sub>3</sub>PO<sub>4</sub>"), its math spans LaTeX ("$\\text{H}_2\\text{O}$"), other readings Unicode scripts
("H₂O", "Na⁺"). For readers, search, RAG and LLMs the book uses ONE convention:
  prose      Markdown (as the engines write it: **bold**, *italic*)
  scripts    LaTeX inline math: a token with sub/superscripts becomes "$\\mathrm{H_{3}PO_{4}}$"
  formulas   LaTeX ($...$, $$...$$, equation segments' latex field) — unchanged
  tables     HTML (Markdown tables cannot express merged cells); the scripts inside their cells are converted
Safety: the conversion is mechanical and lossless, and only applies to a token made of letters, digits,
brackets and charge signs around the scripts (the grammar makes the LaTeX valid by construction; every formula
is still typeset by latex_check). Anything else is left exactly as written. Chemistry is NOT converted to
mhchem (\\ce{...}): that needs a chemical judgement and is done only by a validated step or the reviewer.
The result is blk['text_norm'] (with blk['notation'] = what was converted); blk['chosen'] — what the engine or
the reviewer wrote, and what decisions refer to — is untouched. core.model.chosen_text returns the normalized
form, so exports, previews, matching and the model all read one notation.
"""
from __future__ import annotations

import re

FORMAT = 'markdown; math and sub/superscripts as LaTeX ($...$); tables as HTML'

_UNI_SUB = str.maketrans('₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎', '0123456789+-=()')
_UNI_SUP = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾', '0123456789+-=()')
_UNI_RUN = re.compile(r'([₀-₉₊₋₌₍₎]+)|([⁰¹²³⁴-⁹⁺⁻⁼⁽⁾]+)')
_SCRIPT = re.compile(r'<(sub|sup)>([^<>]{1,12})</\1>', re.I)
_BASE = r'[A-Za-z0-9()\[\]·]'
# a token: base characters and scripts, at least one script, nothing else (stops at spaces, punctuation, Greek…)
_TOKEN = re.compile(r'(?<![\w$\\])((?:' + _BASE + r'|<(?:sub|sup)>[^<>]{1,12}</(?:sub|sup)>)+)(?![\w$])', re.I)
_SCRIPT_BODY = re.compile(r'^[A-Za-z0-9+\-=()·.,]{1,12}$')
_MINUS = str.maketrans({'−': '-', '–': '-'})       # Marker writes charges with U+2212
_MATH = re.compile(r'\$\$.+?\$\$|\$[^$\n]+\$', re.S)
_TABLE = re.compile(r'<table\b.*?</table>', re.S | re.I)


def _unicode_to_tags(t: str) -> str:
    return _UNI_RUN.sub(lambda m: f'<sub>{m.group(1).translate(_UNI_SUB)}</sub>' if m.group(1)
                        else f'<sup>{m.group(2).translate(_UNI_SUP)}</sup>', t)


def _base(t: str) -> str:
    return t.replace('[', '{[}').replace(']', '{]}').replace('·', r'\cdot ')


def _balance(tok: str) -> tuple[str, str, str]:
    """(prefix, token, suffix): brackets that belong to the surrounding prose stay outside the formula."""
    pre = suf = ''
    while tok and tok[-1] in ')]' and tok.count('(') + tok.count('[') < tok.count(')') + tok.count(']'):
        tok, suf = tok[:-1], tok[-1] + suf
    while tok and tok[0] in '([' and tok.count('(') + tok.count('[') > tok.count(')') + tok.count(']'):
        tok, pre = tok[1:], pre + tok[0]
    while tok and tok[-1] == '·':
        tok, suf = tok[:-1], '·' + suf
    return pre, tok, suf


def _latex_token(tok: str) -> str | None:
    parts, pos, n_scripts = [], 0, 0
    for m in _SCRIPT.finditer(tok):
        base = tok[pos:m.start()]
        body = m.group(2).strip().translate(_MINUS)
        if not _SCRIPT_BODY.match(body):
            return None
        parts.append(_base(base))
        parts.append(('_' if m.group(1).lower() == 'sub' else '^') + '{' + body.replace('·', r'\cdot ') + '}')
        pos, n_scripts = m.end(), n_scripts + 1
    tail = tok[pos:]
    if not n_scripts or '<' in tail or not parts[0]:
        return None
    parts.append(_base(tail))
    return '$\\mathrm{' + ''.join(parts) + '}$'


def normalize_text(text: str) -> tuple[str, int]:
    """(text in the book's notation, number of tokens converted). Formula spans and tables are left alone."""
    if not text or ('<su' not in text.lower() and not _UNI_RUN.search(text)):
        return text, 0
    keep: list[str] = []

    def hold(m):
        keep.append(m.group(0))
        return f'\x00{len(keep) - 1}\x00'

    def hold_table(html):                              # the table stays HTML; the scripts in its CELLS are converted
        nonlocal count
        def cell(c):
            nonlocal count
            inner, k = normalize_text(c.group(2))
            count += k
            return c.group(1) + inner + c.group(3)
        out = re.sub(r'(<t[dh][^>]*>)(.*?)(</t[dh]>)', cell, html, flags=re.S | re.I)
        keep.append(out)
        return f'\x00{len(keep) - 1}\x00'
    count = 0
    t = _MATH.sub(hold, text)
    t = _TABLE.sub(lambda m: hold_table(m.group(0)), t)
    t = _unicode_to_tags(t)

    def conv(m):
        nonlocal count
        pre, tok, suf = _balance(m.group(1))
        out = _latex_token(tok)
        if out is None:
            return m.group(0)
        count += 1
        return pre + out + suf
    t = _TOKEN.sub(conv, t)
    t = re.sub(r'\x00(\d+)\x00', lambda m: keep[int(m.group(1))], t)
    return (t, count) if count else (text, 0)


def annotate(blocks: list[dict]) -> dict:
    """Derive blk['text_norm'] for text segments whose notation differs; returns a summary."""
    changed = tokens = 0
    for b in blocks:
        b.pop('text_norm', None)
        b.pop('notation', None)
        if b['chosen'].get('type') != 'text':
            continue
        norm, n = normalize_text(b['chosen'].get('text') or '')
        if n:
            b['text_norm'], b['notation'] = norm, {'converted': n, 'format': FORMAT}
            changed += 1
            tokens += n
    return {'format': FORMAT, 'segments': changed, 'tokens': tokens}
