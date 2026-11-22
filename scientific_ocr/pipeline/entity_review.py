"""Second pass on ENTITY CLASSIFICATION with a local language model — PROPOSALS ONLY (pure functions; strategy
for application.llm_review).

The deterministic detectors (pipeline.entities) classify by labels, boxes, hierarchy and geometry. The model is
asked only where that evidence is weak or missing:
  verify    an unverified detected entity whose rule is not statistically trusted and is not a layout-engine
            kind (pictures, tables and equations are what the engines SAW, not an interpretation)
  classify  a box (automatic or reviewer group) that no detector could type (no label in the lexicon)
The question is a CHOICE from the taxonomy (core.vocabulary): a fine type, a whole family when only the coarse
category is clear ("family:practice"), or "none" (ordinary running text, not a distinct element). The input is
the document as TYPED ELEMENTS (structure_review.line) around the target, the target's full text, its placement
and the detector's evidence. Answers are validated (a box part needs an enclosing box), voted over samples and
compared with the current type; agreements are recorded for the statistics. The reviewer accepts (= entity_confirm
/ entity_assign, undoable) or rejects (= dismissal, remembered).
"""
from __future__ import annotations

import hashlib

from ..core.model import in_output
from ..core.vocabulary import FAMILIES, TYPES, family_of
from .structure_review import element_text, line

NAME, FILE, MAX_TOKENS = 'entities', 'entity_llm.json', 260
PREVIOUS, FOLLOWING = 6, 4
TARGET_CHARS = 1600                 # text of the target's members shown in full up to this much
ASKABLE = [t for t, (f, _, _) in TYPES.items() if f != 'insert']          # what the model may answer
CHOICES = ASKABLE + [f'family:{f}' for f in FAMILIES if f != 'insert'] + ['none']
WEAK_RULES = ('para', 'block', 'part', 'section', 'box')


def key(target_id: str) -> str:
    return 'M' + hashlib.sha1(f'llm-entity|{target_id}'.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- what to ask
def targets(c: dict) -> list[dict]:
    flow = {b['id'] for b in c['blocks'] if in_output(b) and b.get('flow') != 'furniture'}
    out = []
    ents = c.get('entities') or []
    for e in ents:
        if e['verified'] or e.get('trusted') or e['granularity'] not in WEAK_RULES or e['rule'].startswith('engine kind'):
            continue
        out.append({'id': e['key'], 'mode': 'verify', 'entity_key': e['key'], 'members': e['members'],
                    'current': e['type'], 'placement': e['placement'], 'label': e['label'], 'evidence': e['rule'],
                    'confidence': e['confidence'], 'page': e['pages'][0]})
    typed = {frozenset(e['members']) for e in ents}
    for g in c.get('groups') or []:
        mem = [m for m in g['members'] if m in flow]
        if len(mem) < 2 or frozenset(mem) in typed:
            continue
        page = next((b['page'] for b in c['blocks'] if b['id'] == mem[0]), 0)
        out.append({'id': g['id'], 'mode': 'classify', 'entity_key': None, 'members': mem, 'current': None,
                    'placement': 'inline', 'label': g.get('name') or '', 'evidence': f'box ({g.get("type")}) with no recognised label',
                    'confidence': None, 'page': page})
    return out


# ---------------------------------------------------------------- prompt
def _taxonomy() -> str:
    rows = []
    for f, (fel, fen) in FAMILIES.items():
        if f == 'insert':
            continue
        types = ', '.join(f'{t} ({el} / {en})' for t, (ff, el, en) in TYPES.items() if ff == f)
        rows.append(f'- family:{f} — {fen} / {fel}: {types}')
    return '\n'.join(rows)


SYSTEM = f"""You classify the elements of a scanned science TEXTBOOK (Greek or English; mathematics, physics,
chemistry, biology) from OCR output. You see the document as TYPED ELEMENTS in reading order (HEADING Lk,
BOX_LABEL, BOX_SUBHEADING, PARAGRAPH, LIST_ITEM, CAPTION, FIGURE, TABLE, EQUATION, IMAGE_EXCERPT) and ONE TARGET:
a group of consecutive elements. Decide what KIND OF TEXTBOOK ELEMENT the target is.

TAXONOMY (answer with one key):
{_taxonomy()}
- none — ordinary running text of the section (exposition), not a distinct element.

How to tell them apart (strongest evidence first):
1. The text's FUNCTION, not its looks:
   - example: a worked problem — a task AND its worked solution in the same element ("Λύση", "Solution",
     steps, a result). application: shows the topic in everyday life/technology/medicine, usually no task.
   - exercise / problem / question: a task for the READER with no worked solution. question = short,
     conceptual, answered in words; exercise = routine practice/calculation; problem = multi-step or
     context-rich. review = a collection of such items closing a chapter or section ("Ερωτήσεις επανάληψης").
   - solution / answer / strategy / hint / check: a PART inside an example or exercise box, never on its own.
   - definition: introduces a term ("ονομάζεται", "is called", "ορίζεται ως"). law: a general rule/principle
     (named laws, rules, principles, axioms). theorem: a stated mathematical result; proof: its derivation.
     formula: a box collecting key equations.
   - note: a remark that qualifies the text; caution: safety or a common mistake; tip: study advice;
     did_you_know: a curiosity/fun fact; history: historical background; biography: a person's life.
   - experiment: a procedure with materials/steps to perform; activity: a task/project to do or discuss;
     case_study: a real case analysed.
   - objectives / summary / key_terms / glossary / references: chapter frame — goals at the start; recap,
     term lists, definitions lists, reading lists at the end.
   - sidenote: unlabelled text in the margin that comments on the main text.
2. The LABEL printed on it (Παράδειγμα, Άσκηση, Example 2.1…) is strong evidence, but a heading can be an
   ordinary section title that only contains such a word ("Παραδείγματα οξέων" in an exposition = none).
3. Position: margin elements are asides or sidenotes; collections at the end of a chapter are review/frame.
4. Typography and boxes support but never decide alone: books box many different kinds.
Rules:
- If only the family is clear (e.g. a practice item, but question vs exercise is unclear) answer "family:<f>".
- If the detector's current type is plausible and the evidence does not clearly favour another, keep it.
- Never invent text. Keep "reason" to one short sentence in Greek, citing the element ids you relied on.

Example (abridged):
TARGET [e5..e7]: [e5] BOX_LABEL: Παράδειγμα 3.2 [e6] PARAGRAPH: Πόσα mol NaOH απαιτούνται για…
[e7] BOX_SUBHEADING: Λύση … ; current type: exercise
-> {{"type":"example","confidence":"high","reason":"Εκφώνηση με λυμένη λύση (e6, e7): λυμένο παράδειγμα, όχι άσκηση."}}
"""


def context(c: dict, t: dict) -> dict:
    blocks = [b for b in c['blocks'] if in_output(b) and b.get('flow') != 'furniture']
    by_id = {b['id']: b for b in blocks}
    idx = {b['id']: i for i, b in enumerate(blocks)}
    mem = [m for m in t['members'] if m in by_id]
    first, last = min(idx[m] for m in mem), max(idx[m] for m in mem)
    prev, following = blocks[max(0, first - PREVIOUS):first], blocks[last + 1:last + 1 + FOLLOWING]
    toc = {s['id']: s for s in c.get('sections', [])}
    path = [toc[s] for s in (by_id[mem[0]].get('section_path') or []) if s in toc and s not in mem]
    boxes = {m: g['name'] for g in c.get('groups', []) for m in g['members'][1:] if g.get('type') == 'labelled_box'}
    enclosing = [e for e in c.get('entities') or [] if e['key'] != t.get('entity_key') and set(mem) < set(e['members'])
                 and family_of(e['type']) in ('worked', 'practice')]
    alias, seq = {}, 0
    for b in prev + [by_id[m] for m in mem] + following:
        if b['id'] not in alias:
            seq += 1
            alias[b['id']] = f'e{seq}'
    return {'alias': alias, 'prev': prev, 'members': [by_id[m] for m in mem], 'following': following,
            'path': path, 'boxes': boxes, 'enclosing': enclosing, 'by_id': by_id}


def prompt(c: dict, t: dict) -> tuple[str, str, dict]:
    ctx = context(c, t)
    a, boxes = ctx['alias'], ctx['boxes']
    lines = ['OPEN SECTIONS (outermost first): ' + (' > '.join(s['title'][:60] for s in ctx['path']) or '(none)'), '',
             f'PREVIOUS {len(ctx["prev"])} ELEMENTS:']
    lines += ['  ' + line(a[b['id']], b, boxes, 160) for b in ctx['prev']] or ['  (start of document)']
    ids = [a[b['id']] for b in ctx['members']]
    lines += ['', f'TARGET [{ids[0]}..{ids[-1]}] ({len(ids)} element(s)):']
    budget = TARGET_CHARS
    for b in ctx['members']:
        txt = element_text(b, max(80, budget))
        budget -= len(txt)
        lines.append('  ' + line(a[b['id']], b, boxes, max(80, budget + len(txt))))
        if budget <= 0:
            lines.append('  … (rest of the target omitted)')
            break
    placement = {'margin': 'in the page margin, outside the text column', 'end_of_chapter': 'inside a collection at the end of the chapter'}
    lines += [f'  placement: {placement.get(t["placement"], "in the text column")}',
              f'  inside: {ctx["enclosing"][0]["type"]} box' if ctx['enclosing'] else '  inside: no example/exercise box',
              f'  printed label: «{t["label"][:80]}»' if t['label'] else '  printed label: (none)',
              f'  detector: {t["evidence"]}' + (f'; current type: {t["current"]}' if t['current'] else '; no type yet'),
              '', f'FOLLOWING {len(ctx["following"])} ELEMENTS:']
    lines += ['  ' + line(a[b['id']], b, boxes, 160) for b in ctx['following']] or ['  (end of document)']
    lines += ['', 'Which kind of textbook element is the TARGET? Answer with JSON only.']
    return SYSTEM, '\n'.join(lines), ctx


def schema(ctx: dict) -> dict:
    return {'type': 'object', 'additionalProperties': False, 'required': ['type', 'confidence', 'reason'],
            'properties': {'type': {'type': 'string', 'enum': CHOICES},
                           'confidence': {'type': 'string', 'enum': ['low', 'medium', 'high']},
                           'reason': {'type': 'string', 'maxLength': 300}}}


# ---------------------------------------------------------------- answers -> proposal
def resolve(answer: dict, ctx: dict, c: dict) -> dict:
    t = answer.get('type')
    errs = [] if t in CHOICES else ['unknown type']
    if family_of(t) == 'part' and not ctx['enclosing']:
        errs.append('a box part (solution, hint…) needs an enclosing example/exercise')
    return {'type': t, 'errors': errs}


def vote(resolved: list[dict]) -> tuple[dict | None, int]:
    ok = [r for r in resolved if not r['errors']]
    if not ok:
        return None, 0
    counts: dict[str, list] = {}
    for r in ok:
        counts.setdefault(r['type'], []).append(r)
    best = max(counts.values(), key=len)
    return best[0], len(best)


def proposal(t: dict, choice: dict, votes: int, samples: int, answers: list[dict], ctx: dict) -> dict | None:
    new = choice['type']
    if new == t['current'] or (t['mode'] == 'classify' and new == 'none'):
        return None                                   # agrees (or nothing to propose for an untyped box)
    first = ctx['members'][0]
    if new == 'none':
        decision = {'event': 'dismiss', 'key': t['entity_key']}
    elif t['mode'] == 'verify':
        decision = {'event': 'entity_confirm', 'key': t['entity_key'], 'entity_type': new}
    else:
        decision = {'event': 'entity_assign', 'members': t['members'], 'entity_type': new}
    return {'key': key(t['id']), 'mode': t['mode'], 'target': t['id'], 'block_id': first['id'], 'page': t['page'],
            'text': element_text(first, 140), 'size': len(t['members']), 'current': t['current'], 'proposed': new,
            'decision': decision, 'votes': votes, 'samples': samples,
            'confidence': [a.get('confidence') for a in answers], 'reasons': [a.get('reason') for a in answers]}


def agreement(t: dict, choice: dict, votes: int, samples: int) -> dict:
    return {'id': t['id'], 'entity_key': t['entity_key'], 'type': choice['type'], 'votes': votes, 'samples': samples}
