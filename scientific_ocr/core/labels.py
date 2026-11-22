"""Label patterns shared by every stage that reads numbered titles and captions (one definition each)."""
from __future__ import annotations

import re

# "5", "5.1", "5.1.2 Title" — a heading that starts with its section number (depth = number of parts)
NUMBERED_HEADING = re.compile(r'^\s*\**\s*(\d+(?:[.\-]\d+)*)[.)]?\s+\S')
# "<word> <number>" at the start: "ΠΑΡΑΔΕΙΓΜΑ 4.3", "Exercise 2-1" (group 1 = the label)
LABELLED = re.compile(r'^\W*([^\W\d_]+\s+\d+(?:[.\-–]\d+)*)\b', re.U)
# caption labels: "ΕΙΚΟΝΑ 5-4", "Fig. 2.1a", "ΣΧΗΜΑ 4.9" (group 1 = the label)
CAPTION_LABEL = re.compile(r'^\W*([^\W\d_]{2,}\.?\s+\d+(?:[.\-–]\d+)*[A-Za-zΑ-Ωα-ω]?)\b\W*')


def label_prefix(text: str) -> str | None:
    m = LABELLED.match(text or '')
    return m.group(1) if m else None

# label words (matched on core.text.label_key-normalised labels: no accents, lower case) that name an INSERT:
# pictures ("ΣΧΗΜΑ 4.9", "Εικ. 2", "Fig. 3-1", "Graph 2") and tables — never equations or boxes
FIGURE_LABEL = re.compile(r'^(σχημ|εικον|εικ\.|φωτογραφ|διαγραμμ|γραφημ|χαρτ|σκιτσ|fig|figure|photo|diagram|chart|graph'
                          r'|plot|illustration|image|picture|scheme|map)')
TABLE_LABEL = re.compile(r'^(πιν|tab)')
