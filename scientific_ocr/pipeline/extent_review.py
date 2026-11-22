"""Second pass on ENTITY EXTENTS (automatic groups) with a local language model — PROPOSALS ONLY (pure functions;
strategy for application.llm_review).

pipeline.extents ends a group on evidence; where the evidence was weak (no shading: a gap or an indentation change
ended it — 'ambiguous'), the model is asked ONE question: which element is the LAST one that belongs to the
entity? The answer is a choice among the group's own elements (it may end earlier) and the elements that follow
it (it may continue), shown as typed elements with their text. Answers are validated, voted over samples and
compared with the current end; accepting = groups/<id>/end (the group becomes the reviewer's, one undo) and the
pass re-runs on the new projection.
"""
from __future__ import annotations

import hashlib

from ..core.model import in_output
from .structure_review import element_text, line

NAME, FILE, MAX_TOKENS = 'extents', 'extent_llm.json', 220
FOLLOWING = 8


def key(group_id: str) -> str:
    return 'X' + hashlib.sha1(f'llm-extent|{group_id}'.encode()).hexdigest()[:10]


def targets(c: dict) -> list[dict]:
    return [{'id': g['id'], 'group': g} for g in c.get('groups', [])
            if g.get('source') == 'auto' and g.get('ambiguous')]


SYSTEM = """You check where an element of a scanned science TEXTBOOK (Greek or English) ENDS. The element is a
worked example, exercise, problem, definition, application, box or similar; layout engines split it into many
pieces (its label, title, statement, sub-headings such as Λύση/Solution/Στρατηγική/Δεδομένα, paragraphs,
equations, figures). You see the element's pieces and the pieces that follow it, as TYPED ELEMENTS in reading
order. Choose the LAST element that still belongs to it.

How to decide:
- A solution, answer, strategy, check, proof, the steps of a calculation, the equations and figures used by it,
  and lists of sub-answers (α), (β)… belong to it.
- It ends where the book's own exposition resumes (a general explanation that no longer works on this task), at
  the next labelled element (Παράδειγμα/Άσκηση/Example…), or at a section heading.
- A sentence that refers back to the task ("Άρα το pH είναι 3.") still belongs to it.
- Do not end it in the middle of a calculation or a list.
Answer with one element id. Keep "reason" to one short sentence in Greek citing the ids you relied on."""


def context(c: dict, t: dict) -> dict:
    g = t['group']
    blocks = [b for b in c['blocks'] if in_output(b) and b.get('flow') != 'furniture']
    by_id = {b['id']: b for b in blocks}
    idx = {b['id']: i for i, b in enumerate(blocks)}
    mem = [m for m in g['members'] if m in by_id]
    last = idx[mem[-1]]
    following = blocks[last + 1:last + 1 + FOLLOWING]
    alias = {}
    for k, b in enumerate([by_id[m] for m in mem] + following, 1):
        alias[b['id']] = f'e{k}'
    return {'members': [by_id[m] for m in mem], 'following': following, 'alias': alias, 'by_id': by_id,
            'choices': [alias[b['id']] for b in [by_id[m] for m in mem][1:] + following]}


def prompt(c: dict, t: dict) -> tuple[str, str, dict]:
    ctx = context(c, t)
    a = ctx['alias']
    lines = [f'THE ELEMENT «{t["group"].get("name", "")[:80]}» (as grouped now, {len(ctx["members"])} pieces):']
    lines += ['  ' + line(a[b['id']], b, {}, 220) for b in ctx['members']]
    lines += ['', f'WHAT FOLLOWS IT ({len(ctx["following"])} pieces):']
    lines += ['  ' + line(a[b['id']], b, {}, 220) for b in ctx['following']] or ['  (end of the document)']
    lines += ['', f'Current last piece: {a[ctx["members"][-1]["id"]]}. Which piece is the LAST one that belongs to the '
              f'element? Choose from: {", ".join(ctx["choices"])}. Answer with JSON only.']
    return SYSTEM, '\n'.join(lines), ctx


def schema(ctx: dict) -> dict:
    return {'type': 'object', 'additionalProperties': False, 'required': ['last', 'confidence', 'reason'],
            'properties': {'last': {'type': 'string', 'enum': ctx['choices']},
                           'confidence': {'type': 'string', 'enum': ['low', 'medium', 'high']},
                           'reason': {'type': 'string', 'maxLength': 300}}}


def resolve(answer: dict, ctx: dict, c: dict) -> dict:
    inv = {v: k for k, v in ctx['alias'].items()}
    last = inv.get(answer.get('last'))
    return {'last': last, 'errors': [] if last and answer.get('last') in ctx['choices'] else ['unknown element']}


def vote(resolved: list[dict]) -> tuple[dict | None, int]:
    ok = [r for r in resolved if not r['errors']]
    if not ok:
        return None, 0
    counts: dict[str, list] = {}
    for r in ok:
        counts.setdefault(r['last'], []).append(r)
    best = max(counts.values(), key=len)
    return best[0], len(best)


def proposal(t: dict, choice: dict, votes: int, samples: int, answers: list[dict], ctx: dict) -> dict | None:
    cur = ctx['members'][-1]['id']
    if choice['last'] == cur:
        return None
    order = [b['id'] for b in ctx['members'] + ctx['following']]
    new = order[:order.index(choice['last']) + 1]
    added = [x for x in new if x not in t['group']['members']]
    removed = [x for x in t['group']['members'] if x not in new]
    return {'key': key(t['id']), 'group': t['id'], 'name': t['group'].get('name', ''), 'block_id': t['group']['members'][0],
            'page': ctx['members'][0]['page'], 'current_last': cur, 'proposed_last': choice['last'],
            'added': [element_text(ctx['by_id'][x], 70) for x in added], 'removed': [element_text(ctx['by_id'][x], 70) for x in removed],
            'decision': {'event': 'group_end', 'group': t['id'], 'last': choice['last']},
            'votes': votes, 'samples': samples, 'confidence': [a.get('confidence') for a in answers],
            'reasons': [a.get('reason') for a in answers]}


def agreement(t: dict, choice: dict, votes: int, samples: int) -> dict:
    return {'id': t['id'], 'last': choice['last'], 'votes': votes, 'samples': samples}
