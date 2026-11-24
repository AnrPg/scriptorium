"""Stage: reading-flow annotation for linear outputs.

Every block gets a `flow` role:
  main       body text (paragraphs, headings)
  insert     content placed INTO the text flow: figures, tables, equations, excerpts, and every caption
             (a caption follows its figure's role; one without a figure, or of a group, is an insert by default)
Visual content is ALWAYS an insert by default: a figure/chart/table/equation kind, a segment the reviewer
categorised as one (an entity type of the 'insert' family: figure, table, equation…), and any segment whose
output is an image (image only, unreadable, an untranscribed excerpt). The reviewer's set_flow overrides any of these.
  furniture  running headers/footers/page numbers (not part of the argument of the text)
and body paragraphs that the page layout split (a sentence interrupted by an insert or a page break) are linked
with relations.continues_in / relations.continues_from, so a linear reader knows exactly where the interrupted
text resumes. Detection is script-independent: the first part does not end a sentence and the second part
starts in lowercase (or the first ends with a hyphenation).
"""
from __future__ import annotations

import re

from ..core.model import CAPTIONABLE_KINDS, EQUATION_KINDS

INSERT_KINDS = CAPTIONABLE_KINDS | EQUATION_KINDS | {'excerpt'}
_SENTENCE_END = re.compile(r'[.;!?·:»"\')\]]\s*$|[.;!?·]\**\s*$')


def role(b: dict, by_id: dict | None = None, inserts: frozenset = frozenset()) -> str:
    if b.get('excluded'):                              # furniture CONFIRMED by pipeline.furniture (or the reviewer);
        return 'furniture'                             # an engine label alone does not take print out of the text
    if b.get('flow_override') in ('main', 'insert'):   # the reviewer's decision (set_flow)
        return b['flow_override']
    if b['kind'] == 'caption':                         # travels with its figure (inherits its role); a caption
        owner = (by_id or {}).get(b['relations'].get('caption_for'))   # of a group, or still unlinked: an insert
        r = role(owner, by_id, inserts) if owner is not None and owner is not b and owner['kind'] != 'caption' else None
        return r if r in ('main', 'insert') else 'insert'
    if b['kind'] == 'excerpt':                         # an excerpt the reviewer transcribed is text; else an image
        return 'main' if b['chosen']['type'] == 'text' else 'insert'
    if b['kind'] in INSERT_KINDS or b['id'] in inserts:     # visual kind, or categorised as visual by the reviewer
        return 'insert'
    return 'main' if b['chosen']['type'] in ('text', 'latex') else 'insert'     # else its output is an image


def _text(b) -> str:
    return re.sub(r'<[^>]+>|\$[^$]*\$', ' ', b['chosen'].get('text') or '').strip()


def continues(prev: str, nxt: str) -> bool:
    prev, nxt = prev.rstrip(), nxt.lstrip(' *_"«(')
    if not prev or not nxt:
        return False
    if prev.endswith(('-', '‐')) and nxt[0].isalpha():
        return True                                    # hyphenated word split across the break
    return not _SENTENCE_END.search(prev) and nxt[0].isalpha() and nxt[0].islower()


def annotate(blocks: list[dict], inserts: frozenset = frozenset()):
    """Mutates blocks (in final reading order): sets b['flow'] and continuation relations."""
    last_main = None
    between: list[dict] = []
    by_id = {b['id']: b for b in blocks}
    for b in blocks:
        b['flow'] = role(b, by_id, inserts)
        for k in ('continues_in', 'continues_from', 'interrupted_by'):
            b['relations'].pop(k, None)
        if b['flow'] != 'main':
            between.append(b)
            continue
        if b['kind'] == 'heading':
            last_main, between = None, []
            continue
        forced = b.get('continues_override')          # the reviewer's decision (set_continuation) beats the test
        if last_main is not None and (forced if forced is not None else continues(_text(last_main), _text(b))):
            last_main['relations']['continues_in'] = b['id']
            b['relations']['continues_from'] = last_main['id']
            breaks = [x['id'] for x in between if x['flow'] == 'insert']
            if b['page'] != last_main['page']:
                breaks.append(f'page:{last_main["page"]}->{b["page"]}')
            if breaks:
                b['relations']['interrupted_by'] = breaks
        last_main, between = b, []
