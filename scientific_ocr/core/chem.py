"""Chemistry-specific pure helpers: classification hints and equation comparison."""
from __future__ import annotations

import re

ELEMENT_LABEL = re.compile(r'^(?:[CHNOPS]|CH[23]?|NH[23]?|OH|HO|H[23]?C|O[-−]?|P=O|C=O|N[-−]H|[-−]O)$')
CHEM_CLASS_HINTS = ('chem', 'molec', 'structur', 'reaction')
_ARROWS = [(r'\\(?:long)?rightarrow|\\to(?![a-z])|→|\\xrightarrow', '->'),
           (r'\\(?:right)?leftharpoons|\\rightleftharpoons|⇌|\\leftrightharpoons', '<=>'),
           (r'\\(?:long)?leftarrow|←', '<-')]
_LEWIS = re.compile(r':\s*[A-Z][a-z]?\b|\b[A-Z][a-z]?\s*:|[·•∙]')


def element_label_count(texts: list[str]) -> int:
    return sum(1 for t in texts for tok in re.split(r'[\s,;()]+', t) if ELEMENT_LABEL.match(tok.strip()))


def classify_visual(figure_texts: list[str], picture_classes: list) -> dict:
    """Heuristic routing of a picture region (never authoritative)."""
    labels = [(pc.get('class_name', ''), pc.get('confidence'))
              for c in picture_classes or [] if isinstance(c, dict) for pc in c.get('predicted_classes', [])]
    top = labels[0][0].lower() if labels else ''
    n = element_label_count(figure_texts)
    ev = {'top_class': labels[0] if labels else None, 'element_label_tokens': n}
    if looks_like_lewis(figure_texts):
        return {'kind': 'lewis_structure', 'evidence': ev}
    if n >= 6 or any(h in top for h in CHEM_CLASS_HINTS):
        return {'kind': 'molecular_structure', 'evidence': ev}
    return {'kind': 'figure', 'evidence': ev}


def looks_like_lewis(labels: list[str]) -> bool:
    """Electron-dot drawings: figure OCR shows colons/dots attached to element symbols."""
    return any(_LEWIS.search(t or '') for t in labels) and sum((t or '').count(':') for t in labels) >= 3


def is_equation(latex: str) -> bool:
    s = latex or ''
    return bool(re.search(r'\\(?:long)?rightarrow|\\rightleftharpoons|\\xrightarrow|→|⇌', s)) and \
        bool(re.search(r'[A-Z][a-z]?(?:_|\}|\^|\(aq|\(s\)|\(l\)|\(g\))', s))


def equation_key(s: str) -> str:
    """Comparison key for chemical equations: ignores font macros, braces and spacing, but keeps every
    symbol, charge sign, state label, coefficient and the arrow TYPE (-> differs from <=>)."""
    s = s or ''
    for pat, rep in _ARROWS:
        s = re.sub(pat, rep, s)
    s = re.sub(r'\\(?:text|mathrm|mathbf|rm|ce|operatorname|ensuremath)\s*', '', s)
    s = re.sub(r'\\(?:,|;|:|!| |quad|qquad|left|right)', '', s)   # spacing macros incl. '\ '
    s = s.replace('{', '').replace('}', '').replace('\\\\', '').replace('−', '-')
    return re.sub(r'\s+', '', s)
