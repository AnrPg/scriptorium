"""OUTPUT PROFILES: what a book is being prepared FOR, as data — one prepackaged policy per purpose.

A profile is chosen once per book (event `output_profile`, undoable; per-book overrides travel in the same event)
and acts on three things at once:
  content   which kinds of content leave the output (entity types/families, excursus types, pictures);
  review    what the reviewer is NOT asked about, because it never reaches the output (content the profile
            excludes, fidelity of pictures a text-only output drops). Only the BOUNDARY of an automatic exclusion
            stays reviewable (one confirmation, not the content): a wrong boundary would silently lose body text;
  export    the formats and their shape (heading depth, chunking, figure captions…).

'reading' reproduces the book as it is (the pipeline's behaviour before profiles). 'rag' prepares text for a
retrieval pipeline used by a TEXT-ONLY LLM agent. Its choices follow the literature (see README «RAG profile»):
  * hard distractors (text that resembles a query but holds no answer — questions, exercises) are kept out of the
    index; filtering them after retrieval recovers little (Cuconasu et al. EMNLP 2025; arXiv 2605.10828);
  * structure-aligned chunks prefixed by their real heading path beat fixed windows (arXiv 2610.10170);
    a fixed ~200-word size is competitive, semantic chunking is not worth its cost (Qu et al. 2025);
  * light Markdown (headings, lists, tables) keeps structure; tags, image links and emphasis are token cost
    (HtmlRAG WWW 2025 vs PixelRAG 2026; He et al. NAACL 2025); tables as Markdown (arXiv 2402.12869);
  * OCR semantic noise hurts more than formatting noise (OHRBench, ICCV 2025): spelling proposals matter.
Every value here is a DEFAULT: a book's overrides replace it (core.profiles.effective).
"""
from __future__ import annotations

import copy

DEFAULT = 'reading'

PROFILES: dict[str, dict] = {
    'reading': {
        'el': 'Ανάγνωση (το βιβλίο όπως είναι)', 'en': 'Reading (the book as it is)',
        'exclude': [], 'keep_answered': True, 'excursus_remove': [], 'auto_exclude': {},
        'pictures': 'keep', 'heading_depth': 6, 'review_pictures': True, 'engine_policy': 'cross_check',
        'table_default': None,           # tables stay tables (pipeline.tables)
        'exports': ['markdown_images', 'markdown_text', 'pdf', 'json'],
    },
    'rag': {
        'el': 'RAG (κείμενο για ανάκτηση από LLM)', 'en': 'RAG (text for retrieval by an LLM)',
        # practice (questions, exercises, problems, review sets) = hard distractors; their answers go with them,
        # except question+answer pairs (keep_answered): a pair CONTAINS the answer, so it is not a distractor.
        # Frame material that duplicates or does not carry the subject (contents, index, front matter,
        # objectives) and asides about people and history leave too. Applications, experiments, case studies,
        # definitions, laws, summaries, key terms and glossaries carry the subject and stay.
        'exclude': ['family:practice', 'solution', 'answer', 'strategy', 'hint', 'check', 'toc', 'index',
                    'front_matter', 'objectives', 'history', 'biography', 'did_you_know', 'activity'],
        'keep_answered': True,
        'excursus_remove': ['history', 'biography'],
        # detected (not yet verified) entities of an excluded type leave the output automatically only when their
        # BOUNDARY has independent evidence: a box (drawn frame/background) or an engine element, detected with
        # this confidence (or by a trusted rule), or a collective section closing its chapter. A heading's subtree
        # alone is not evidence (a label in the margin set as a heading swallows the body text after it): such
        # detections are only suggested. Each automatic exclusion waits for ONE boundary confirmation.
        'auto_exclude': {'confidence': ['high'], 'granularity': ['box', 'part', 'block'],
                         'collective_placement': ['end_of_chapter']},
        'pictures': 'captions_only',     # no images: a caption becomes a paragraph; pictures without one vanish
        # Marker is the primary engine (measured: wrong in 2-5% of the symbol/notation disagreements it is flagged
        # for, and those are typos the spelling proposals catch): its disagreements with Docling are not asked about
        # unless TEXT may be lost (Docling read clearly more words) — equations and tables are always asked
        'engine_policy': 'marker_primary',
        'review_pictures': False,        # fidelity of a picture that never reaches the output is not asked about
        'heading_depth': 3,              # deeper headings become the lead of their paragraph (and stay in breadcrumbs)
        # every table is read as a list WITH LEVELS: one item per row, named by its 1st column, the other columns its
        # keys (pipeline.tables) — a pipe table reads poorly for an LLM. The reviewer's choice per table wins, and
        # a grid of terms stays a flat list of terms (user, 2026-11-27)
        'table_default': {'layout': 'rows', 'key': 0},
        'strip_emphasis': True,          # bold/italic markup removed; bold terms collected as key_terms
        'caption_credits': [             # trailing permission/credit notes of captions (hints; Greek and English)
            r'\((?:με την )?(?:ευγενική )?άδεια[^()]*\)\s*\.?$', r'\((?:courtesy|by courtesy) of[^()]*\)\s*\.?$',
            r'\((?:from|adapted from|από|τροποποιημένο από)\s[^()]*\d{4}[^()]*\)\s*\.?$'],
        'chunk': {'target_words': 220, 'max_words': 380, 'min_words': 40},
        'unsettled': 'review_first',     # export refuses while text the reviewer must check remains
        'exports': ['rag_markdown', 'rag_chunks', 'rag_zip', 'json'],
    },
}

ENGINE_POLICIES = ('cross_check', 'marker_primary')
# flags that are disagreements about HOW a text reads (typos, symbols, notation, picture text, inferred links,
# drawings kept as images), never about text that is missing: what 'marker_primary' does not ask about
MARKER_PRIMARY_WAIVES = ('ENGINE_TEXT_CONFLICT', 'ENGINE_TEXT_CONFLICT_STRONG', 'SYMBOL_READING_CONFLICT',
                         'SYMBOL_READING_MINOR', 'SCRIPT_MIX_UNVERIFIED', 'INLINE_NOTATION_UNVERIFIED',
                         'MIXED_SCRIPT_IN_NOTATION', 'FIGURE_INTERNAL_TEXT', 'FIGURE_INTERNAL_TEXT_WITHHELD',
                         'CAPTION_ASSOCIATION_INFERRED', 'CHEM_STRUCTURE_IMAGE_AUTHORITATIVE',
                         'REACTION_SCHEME_IMAGE_AUTHORITATIVE', 'CHEM_CANDIDATE_IMPLAUSIBLE')

OVERRIDABLE = {'exclude', 'keep_answered', 'excursus_remove', 'auto_exclude', 'pictures', 'heading_depth', 'engine_policy',
               'strip_emphasis', 'chunk', 'table_default'}


def names() -> list[str]:
    return list(PROFILES)


def effective(name: str | None, overrides: dict | None = None) -> dict:
    """The profile's values with a book's overrides applied (unknown keys ignored). Always a fresh copy."""
    p = copy.deepcopy(PROFILES.get(name or DEFAULT) or PROFILES[DEFAULT])
    for k, v in (overrides or {}).items():
        if k in OVERRIDABLE:
            p[k] = copy.deepcopy(v)
    p['name'] = name if name in PROFILES else DEFAULT
    return p


def errors(name, overrides) -> list[str]:
    """Validation of an output_profile event's payload."""
    errs = [] if name in PROFILES else [f'unknown profile {name!r}; one of {names()}']
    if overrides is not None and not isinstance(overrides, dict):
        return errs + ['overrides must be an object']
    for k, v in (overrides or {}).items():
        if k not in OVERRIDABLE:
            errs.append(f'not overridable: {k}')
        elif k == 'auto_exclude' and not (isinstance(v, dict) and all(
                isinstance(x, list) and all(isinstance(y, str) for y in x) for x in v.values())):
            errs.append('auto_exclude must map keys to lists of strings')
        elif k in ('exclude', 'excursus_remove') and not (
                isinstance(v, list) and all(isinstance(x, str) for x in v)):
            errs.append(f'{k} must be a list of strings')
        elif k == 'heading_depth' and not (isinstance(v, int) and 1 <= v <= 6):
            errs.append('heading_depth must be 1..6')
        elif k == 'engine_policy' and v not in ENGINE_POLICIES:
            errs.append(f'engine_policy must be one of {ENGINE_POLICIES}')
        elif k == 'pictures' and v not in ('keep', 'captions_only'):
            errs.append('pictures must be keep or captions_only')
        elif k == 'table_default' and not (v is None or (isinstance(v, dict) and v.get('layout') in ('rows', 'cols')
                                                            and (v.get('key') is None or (isinstance(v.get('key'), int) and v['key'] >= 0)))):
            errs.append('table_default must be null or {"layout": "rows"|"cols", "key": null|column/row number}')
    return errs
