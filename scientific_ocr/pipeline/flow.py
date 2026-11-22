"""Stage: reading-flow annotation for linear outputs.

Every block gets a `flow` role:
  main       body text (paragraphs, headings, captions without a figure)
  insert     content placed INTO the text flow: figures (with their captions), tables, equations, excerpts
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


def role(b: dict) -> str:
    if b.get('excluded'):                              # furniture CONFIRMED by pipeline.furniture (or the reviewer);
        return 'furniture'                             # an engine label alone does not take print out of the text
    if b['kind'] == 'caption' and b['relations'].get('caption_for'):
        return 'insert'                                # travels with its figure
    if b['kind'] in INSERT_KINDS and b['chosen']['type'] != 'text':
        return 'insert'
    if b['kind'] == 'excerpt':                         # reviewer turned an excerpt into text
        return 'main'
    return 'insert' if b['kind'] in INSERT_KINDS else 'main'


def _text(b) -> str:
    return re.sub(r'<[^>]+>|\$[^$]*\$', ' ', b['chosen'].get('text') or '').strip()


def continues(prev: str, nxt: str) -> bool:
    prev, nxt = prev.rstrip(), nxt.lstrip(' *_"«(')
    if not prev or not nxt:
        return False
    if prev.endswith(('-', '‐')) and nxt[0].isalpha():
        return True                                    # hyphenated word split across the break
    return not _SENTENCE_END.search(prev) and nxt[0].isalpha() and nxt[0].islower()


def annotate(blocks: list[dict]):
    """Mutates blocks (in final reading order): sets b['flow'] and continuation relations."""
    last_main = None
    between: list[dict] = []
    for b in blocks:
        b['flow'] = role(b)
        for k in ('continues_in', 'continues_from', 'interrupted_by'):
            b['relations'].pop(k, None)
        if b['flow'] != 'main':
            between.append(b)
            continue
        if b['kind'] == 'heading':
            last_main, between = None, []
            continue
        if last_main is not None and continues(_text(last_main), _text(b)):
            last_main['relations']['continues_in'] = b['id']
            b['relations']['continues_from'] = last_main['id']
            breaks = [x['id'] for x in between if x['flow'] == 'insert']
            if b['page'] != last_main['page']:
                breaks.append(f'page:{last_main["page"]}->{b["page"]}')
            if breaks:
                b['relations']['interrupted_by'] = breaks
        last_main, between = b, []
