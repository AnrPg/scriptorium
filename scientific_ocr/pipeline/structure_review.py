"""Second pass on the document hierarchy with a local language model — PROPOSALS ONLY (pure functions).

The deterministic stages (sections, document) decide what evidence decides: numbering, reviewer levels, box
shading. A model is asked only about what stays ambiguous after them:
  * unnumbered headings whose level came from typography (engine_rank, default, style_of_numbered,
    level_jump_closed), labelled boxes and box sub-headings
  * heading-like paragraphs (short, bold or capitals, no sentence end) that may be unrecognised headings
The model also sees the deterministic HINTS for the target (measured print size and its style class, running
heads that quote it, outline lists, the engine tag, and the hint proposals that did not pass their check):
evidence, not answers. A proposal is never about one heading only: it is PROPAGATED to every heading of the
same style at the same current level (same style = same level), and SIMULATED on a copy of the hierarchy to list
its consequences (other headings whose level then changes); the next round asks again about what the simulated
structure changed (ROUNDS). After the reviewer accepts a proposal, the pass re-runs on the new projection.
Each question is answered against STRUCTURED TEXT — the document as typed elements with their inferred levels
and roles, not raw text — and as a choice, not free text:
  role    section_heading | box_label | box_subheading | not_heading
  parent  one of the headings already open at that point (constrained to their ids) or ROOT
so the level follows from the parent (parent level + 1) and can never jump. Every answer is validated, compared
with the current structure (no-ops dropped), voted over several samples, and shown to the reviewer, who accepts
(= an ordinary set_heading_level decision, undoable) or rejects (= a dismissal, remembered).
"""
from __future__ import annotations

import hashlib
import re

from ..core.model import CAPTIONABLE_KINDS, EQUATION_KINDS, in_output
from ..core.text import strip_markup

NAME, FILE, MAX_TOKENS = 'structure', 'structure_llm.json', 300   # strategy for application.llm_review
ROUNDS = 3                 # ask -> propagate -> simulate -> ask about what changed, at most this many rounds
HINT_EL = {'human_style': 'same print style as a heading the reviewer set', 'style_of_numbered': 'same style as numbered headings',
           'typography': 'measured print size/style rank', 'running_head': 'running heads of the book',
           'chapter_label': 'chapter label', 'outline': 'a chapter outline list', 'engine_rank': 'layout engine tag'}
PREVIOUS = 12              # elements of context before the target
FOLLOWING_LINES = 100      # lines of following structured text (each element wrapped at LINE_CHARS)
LINE_CHARS = 100
ELEMENT_CHARS = 300        # an element contributes at most this much text (long paragraphs are cut)
ROLES = ('section_heading', 'box_label', 'box_subheading', 'not_heading')
UNCERTAIN_SOURCES = ('engine_rank', 'default', 'style_of_numbered', 'level_jump_closed', 'box_heading', 'labelled_element')
_SENT_END = re.compile(r'[.;:!?·»"\')\]]\s*\**\s*$')
def key(block_id: str) -> str:
    return 'L' + hashlib.sha1(f'llm-structure|{block_id}'.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- current structure of an element
def current(b: dict) -> tuple[str, int]:
    """(role, level) as the deterministic stages left it."""
    if b['kind'] != 'heading':
        return 'not_heading', 0
    src = b.get('heading_level_source')
    role = 'box_label' if src == 'labelled_element' else 'box_subheading' if src == 'box_heading' else 'section_heading'
    return role, b.get('heading_level') or 1


def heading_like(b: dict) -> bool:
    t = (b['chosen'].get('text') or '').strip() if b['chosen'].get('type') == 'text' else ''
    p = strip_markup(t)
    if b['kind'] != 'paragraph' or not p or len(p.split()) > 12 or _SENT_END.search(p):
        return False
    letters = [c for c in p if c.isalpha()]
    bold = bool(re.fullmatch(r'\*\*[^*]+\*\*', t))
    return bold or (len(letters) >= 3 and all(c.isupper() for c in letters))


def targets(c: dict) -> list[dict]:
    out = []
    for b in filter(in_output, c['blocks']):
        if b.get('flow') == 'furniture' or b['relations'].get('caption_for'):
            continue
        if b['kind'] == 'heading' and b['chosen'].get('type') == 'text' and b.get('heading_level_source') in UNCERTAIN_SOURCES:
            out.append(b)
        elif heading_like(b) and not any(d.get('action') == 'set_heading_level' for d in b.get('decisions') or []):
            out.append(b)
    return out


# ---------------------------------------------------------------- structured text
def element_type(b: dict) -> str:
    if b['kind'] == 'heading':
        role, lvl = current(b)
        return {'section_heading': f'HEADING L{lvl}', 'box_label': f'BOX_LABEL L{lvl}',
                'box_subheading': f'BOX_SUBHEADING L{lvl}'}[role]
    if b['kind'] == 'caption':
        return 'CAPTION'
    if b['kind'] in CAPTIONABLE_KINDS:
        return {'table': 'TABLE'}.get(b['kind'], 'FIGURE')
    if b['kind'] in EQUATION_KINDS:
        return 'EQUATION'
    if b.get('role') == 'list_item':
        return 'LIST_ITEM'
    if b['kind'] == 'excerpt' or b['chosen']['type'] not in ('text', 'latex'):
        return 'IMAGE_EXCERPT'
    return 'PARAGRAPH'


def element_text(b: dict, limit: int = ELEMENT_CHARS) -> str:
    ch = b['chosen']
    t = strip_markup(ch.get('text') or '') if ch['type'] == 'text' else (ch.get('latex') or '') if ch['type'] == 'latex' else ''
    return (t[:limit] + '…') if len(t) > limit else t


def line(alias: str, b: dict, boxes: dict, limit: int = ELEMENT_CHARS) -> str:
    extra = []
    if b['id'] in boxes:
        extra.append(f'in box «{boxes[b["id"]][:40]}»')
    if b['relations'].get('continues_from'):
        extra.append('continues previous paragraph')
    tail = f' ({"; ".join(extra)})' if extra else ''
    t = element_text(b, limit)
    return f'[{alias}] p{b["page"]} {element_type(b)}{tail}: {t}' if t else f'[{alias}] p{b["page"]} {element_type(b)}{tail}'


def typography(b: dict) -> str:
    t = (b['chosen'].get('text') or '')
    p = strip_markup(t)
    letters = [c for c in p if c.isalpha()]
    caps = bool(letters) and all(c.isupper() for c in letters)
    tag = (b.get('engine_heading_levels') or {}).get('marker')
    parts = [f'layout engine heading tag: h{tag}' if tag else 'no layout heading tag',
             'ALL CAPITALS' if caps else 'mixed case', 'bold' if '**' in t else 'not marked bold',
             f'{len(p.split())} words', 'ends with sentence punctuation' if _SENT_END.search(p) else 'no sentence end']
    tone = (b.get('source_crop') or {}).get('paper_tone')
    if isinstance(tone, list) and max(tone) - min(tone) < 12 and tone[0] < 235:
        parts.append('on a shaded background')
    ty = b.get('typography') or {}
    if ty.get('text_u'):
        parts.append(f'measured print height {ty["text_u"]:.1f} units over {ty.get("lines", 1)} line(s)')
    rel = b.get('typography_rel') or {}
    if rel.get('size'):
        parts.append(f'print size {rel["size"]:.2f}x the body text of its {rel["ref"]}')
    if rel.get('weight'):
        parts.append('heavier than body text (bold-like)' if rel['weight'] > 1.25 else 'stroke like body text')
    if rel.get('colour') is not None:
        parts.append('ink colour differs from body text' if rel['colour'] > 40 else 'ink colour like body text')
    st = b.get('heading_style')
    if st and st[0] == 't':
        parts.append(f'print style class #{st[1] + 1} (1 = largest)')
    return ', '.join(parts)


def hints_line(b: dict) -> str:
    h = b.get('heading_hints') or {}
    return '; '.join(f'{HINT_EL.get(k, k)} -> level {v}' for k, v in sorted(h.items())) or 'none'


def context(c: dict, target: dict) -> dict:
    """The model's input for one target: open sections, previous elements (typed, with inferred levels), the
    target with its typography, and up to FOLLOWING_LINES lines of what follows. Aliases e1.. keep it short."""
    blocks = [b for b in c['blocks'] if in_output(b) and b.get('flow') != 'furniture']
    by_id = {b['id']: b for b in blocks}
    i = next(k for k, b in enumerate(blocks) if b['id'] == target['id'])
    boxes = {m: g['name'] for g in c.get('groups', []) for m in g['members'][1:] if g.get('type') == 'labelled_box'}
    toc = {s['id']: s for s in c.get('sections', [])}
    path = [toc[s] for s in (target.get('section_path') or []) if s in toc and s != target['id']]
    prev = blocks[max(0, i - PREVIOUS):i]
    following, n_lines = [], 0
    for b in blocks[i + 1:]:
        t = element_text(b)
        n = max(1, -(-len(t) // LINE_CHARS))
        if n_lines + n > FOLLOWING_LINES:
            break
        following.append(b)
        n_lines += n
    alias, seq = {}, 0
    for b in [by_id[s['id']] for s in path if s['id'] in by_id] + prev + [target] + following:
        if b['id'] not in alias:
            seq += 1
            alias[b['id']] = f'e{seq}'
    parents = [s['id'] for s in path if s['id'] in alias] + \
              [b['id'] for b in prev if b['kind'] == 'heading' and b['id'] not in [s['id'] for s in path]]
    return {'alias': alias, 'path': path, 'prev': prev, 'following': following, 'boxes': boxes,
            'parents': parents, 'by_id': by_id}


SYSTEM = """You restore the section hierarchy of a scanned science TEXTBOOK (Greek or English) from OCR output.
You do not see raw text: you see the document as TYPED ELEMENTS in reading order, each with the structure the
pipeline inferred so far. Your job is ONE decision about ONE target element.

Element types: HEADING Lk (book section at level k; L1 = chapter/unit), BOX_LABEL Lk (the label of a boxed
element: Example, Exercise, Problem, Definition, Παράδειγμα, Άσκηση, Πρόβλημα, Ορισμός, Ερώτηση + number),
BOX_SUBHEADING Lk (a heading inside a box: Solution/ΛΥΣΗ, Strategy/ΣΤΡΑΤΗΓΙΚΗ, Answer/Απάντηση, Check/Έλεγχος),
PARAGRAPH, LIST_ITEM, CAPTION, FIGURE, TABLE, EQUATION, IMAGE_EXCERPT (not read).

Decide the target's ROLE:
- section_heading: opens a section of the book's argument; following paragraphs belong to it until a heading
  of the same or higher level.
- box_label: starts a boxed element (example, exercise, problem, question, definition...). A box is NOT a new
  section: it belongs to the section it appears in, one level below it, and the next section heading closes it.
- box_subheading: a heading inside a box (solution, strategy, answer, comment); it never opens a book section.
- not_heading: running text, a bold lead-in term of a paragraph ("**Strong base** is…"), a figure label,
  a fragment, or anything that is not a heading.
Decide its PARENT: the element it belongs under, chosen ONLY from the allowed parent ids, or ROOT for a
top-level (chapter/unit) heading. The level is then parent level + 1.

How to decide (strongest evidence first):
1. Meaning: what the following elements are. A heading followed by "Solve / Calculate / Υπολογίστε / Να βρεθεί"
   and a solution is a box_label; a heading followed by exposition is a section_heading.
2. Numbering already in the text decides the level (e.g. "4.3" is under "4"); you are never asked about
   headings whose number already fixed them — use them as anchors.
3. Consistency: headings of the same kind and style as earlier ones get the same role and level.
4. Typography (capitals, bold, layout tag, shading, measured print size) and the deterministic hints are
   supporting evidence only: books use capitals for both chapter titles and box labels, and every book has its
   own conventions (running heads, outlines and chapter labels may be absent or worded differently).
5. Same print style normally means same level in a book: your decision will be proposed for every heading of
   the target's style at its current level (listed as "same-style headings"), so decide for the style, and say
   so in the reason if the target is an exception.
Rules: answer only about the target; never invent text; if the evidence does not clearly favour a change,
keep the current structure and say so with confidence "low"; keep "reason" short (one sentence, in Greek)
and cite the element ids you relied on.

Example (abridged):
[e1] HEADING L1: Οξέα και βάσεις | [e2] HEADING L2: Ισχυρά και ασθενή οξέα | [e3] PARAGRAPH: ...
TARGET [e4] HEADING L1: ΠΑΡΑΔΕΙΓΜΑ 4.3 — followed by [e5] PARAGRAPH: Κατατάξτε τις παρακάτω ενώσεις...
[e6] BOX_SUBHEADING: ΛΥΣΗ
-> {"role":"box_label","parent":"e2","confidence":"high","reason":"Ακολουθεί εκφώνηση και λύση (e5, e6): πλαίσιο παραδείγματος μέσα στην ενότητα e2."}
"""


def prompt(c: dict, target: dict) -> tuple[str, str, dict]:
    ctx = context(c, target)
    ctx['c'] = c
    a, boxes = ctx['alias'], ctx['boxes']
    role, lvl = current(target)
    lines = ['OPEN SECTIONS at the target (outermost first):']
    lines += [f'  [{a[s["id"]]}] L{s["level"]}: {s["title"][:80]}' for s in ctx['path'] if s['id'] in a] or ['  (none)']
    lines += ['', f'PREVIOUS {len(ctx["prev"])} ELEMENTS:']
    lines += ['  ' + line(a[b['id']], b, boxes, 160) for b in ctx['prev']] or ['  (start of document)']
    lines += ['', 'TARGET:', '  ' + line(a[target['id']], target, boxes),
              f'  current structure: {role}' + (f' at level {lvl}' if lvl else ''),
              f'  typography: {typography(target)}',
              f'  deterministic hints (evidence, not answers): {hints_line(target)}',
              f'  same-style headings elsewhere: {same_style_line(c, target)}', '',
              f'FOLLOWING ELEMENTS (up to {FOLLOWING_LINES} lines):']
    lines += ['  ' + line(a[b['id']], b, boxes) for b in ctx['following']] or ['  (end of document)']
    allowed = ['ROOT'] + [a[p] for p in ctx['parents']]
    lines += ['', f'ALLOWED PARENTS: {", ".join(allowed)}',
              f'Decide the role and parent of [{a[target["id"]]}]. Answer with JSON only.']
    return SYSTEM, '\n'.join(lines), ctx


def schema(ctx: dict) -> dict:
    allowed = ['ROOT'] + [ctx['alias'][p] for p in ctx['parents']]
    return {'type': 'object', 'additionalProperties': False, 'required': ['role', 'parent', 'confidence', 'reason'],
            'properties': {'role': {'type': 'string', 'enum': list(ROLES)},
                           'parent': {'type': 'string', 'enum': allowed},
                           'confidence': {'type': 'string', 'enum': ['low', 'medium', 'high']},
                           'reason': {'type': 'string', 'maxLength': 300}}}


# ---------------------------------------------------------------- answers -> proposal
def resolve(answer: dict, ctx: dict, c: dict) -> dict:
    """Model answer (aliases) -> {role, parent_id, level, errors}."""
    inv = {v: k for k, v in ctx['alias'].items()}
    toc = {s['id']: s for s in c.get('sections', [])}
    role, parent = answer['role'], answer['parent']
    errs = []
    if role == 'not_heading':
        return {'role': role, 'parent': None, 'level': 0, 'errors': []}
    pid = None if parent == 'ROOT' else inv.get(parent)
    if parent != 'ROOT' and pid is None:
        errs.append('unknown parent')
    pb = ctx['by_id'].get(pid) if pid else None
    plevel = (toc[pid]['level'] if pid in toc else (pb or {}).get('heading_level') or 0) if pid else 0
    prole = current(pb)[0] if pb else None
    if role == 'box_subheading' and prole not in ('box_label', 'box_subheading'):
        errs.append('a box sub-heading must sit under a box label')
    if role in ('section_heading', 'box_label') and prole in ('box_label', 'box_subheading'):
        errs.append('a section or a box cannot sit inside a box')
    return {'role': role, 'parent': pid, 'level': plevel + 1, 'errors': errs}


def vote(resolved: list[dict]) -> tuple[dict | None, int]:
    """Most common valid (role, level); returns (choice, votes)."""
    ok = [r for r in resolved if not r['errors']]
    if not ok:
        return None, 0
    counts: dict[tuple, list] = {}
    for r in ok:
        counts.setdefault((r['role'], r['level']), []).append(r)
    best = max(counts.values(), key=len)
    return best[0], len(best)


def decorate(p: dict, ctx: dict) -> None:
    """The model saw aliases (e1, e2…): give the reviewer their texts; and what accepting would also change."""
    p['aliases'] = {v: element_text(ctx['by_id'][k], 60) for k, v in ctx['alias'].items() if k in ctx['by_id']}
    p['consequences'] = simulate(ctx['c'], [p])[1]


def same_style(c: dict, target: dict) -> list[dict]:
    st = target.get('heading_style')
    return [b for b in c['blocks'] if st and b['id'] != target['id'] and b.get('heading_style') == st and in_output(b)]


def same_style_line(c: dict, target: dict) -> str:
    sib = same_style(c, target)
    return '; '.join(f'p{b["page"]} L{b.get("heading_level")} «{element_text(b, 40)}»' for b in sib[:8]) or 'none'


def propagate(c: dict, target: dict, choice: dict) -> list[dict]:
    """Same style = same level: the headings of the target's style at the target's current level, that no
    reviewer or numbering fixed, move with it."""
    if choice['role'] == 'not_heading':
        return [target]
    _, lvl = current(target)
    return [target] + [b for b in same_style(c, target) if current(b) == current(target) and b.get('heading_level') == lvl
                       and b.get('heading_level_source') not in ('human', 'numbering', 'box_heading')]


def simulate(c: dict, proposals: list[dict]) -> tuple[dict, list[dict]]:
    """The hierarchy if these proposals were accepted (a copy): (document, consequences = other headings whose
    level changes as a result)."""
    import copy

    from . import sections
    blocks = copy.deepcopy(c['blocks'])
    by_id = {b['id']: b for b in blocks}
    before = {b['id']: b.get('heading_level') for b in blocks if b['kind'] == 'heading'}
    touched = set()
    for p in proposals:
        for m in p.get('members') or [p['block_id']]:
            b = by_id.get(m)
            if not b:
                continue
            touched.add(m)
            if p['proposed']['level'] == 0:
                b['kind'] = 'paragraph'
            else:
                b['kind'], b['heading_level'], b['heading_level_source'] = 'heading', p['proposed']['level'], 'human'
    toc = sections.annotate(blocks)
    after = {b['id']: b.get('heading_level') for b in blocks if b['kind'] == 'heading'}
    cons = [{'block_id': i, 'text': element_text(by_id[i], 60), 'before': before.get(i), 'after': lv}
            for i, lv in after.items() if i not in touched and before.get(i) != lv]
    return {**c, 'blocks': blocks, 'sections': toc}, cons


def agreement(t: dict, choice: dict, votes: int, samples: int) -> dict:
    """The model agreed with the current structure: recorded with its level, so the agreement is evidence too."""
    return {'id': t['id'], 'role': choice['role'], 'level': choice['level'], 'votes': votes, 'samples': samples}


def model_levels(res: dict, dismissed=()) -> dict[str, int]:
    """block id -> level the model voted for by MAJORITY (agreements and proposals of the last run): a hint source
    for pipeline.sections ('model'), calibrated like the others; never applied alone. A proposal the reviewer
    rejected no longer counts as a hint."""
    dismissed = set(dismissed)
    out = {}
    for a in res.get('agreements') or []:
        if a.get('level') is not None and a.get('votes', 0) * 2 > a.get('samples', 1):
            out[a['id']] = a['level']
    for p in res.get('proposals') or []:
        if p.get('key') in dismissed:
            continue
        if p['votes'] * 2 > p['samples'] and p['proposed'].get('level') is not None:
            for m in p.get('members') or [p['block_id']]:
                out[m] = p['proposed']['level']
    return out


def next_round(c: dict, proposals: list[dict]) -> dict:
    """Runner hook: the document the next round asks about (proposals applied in simulation)."""
    return simulate(c, proposals)[0]


def proposal(target: dict, choice: dict, votes: int, samples: int, answers: list[dict], ctx: dict) -> dict | None:
    role, lvl = current(target)
    if (choice['role'], choice['level']) == (role, lvl) or (choice['role'] == 'not_heading' and role == 'not_heading'):
        return None                                   # the model agrees with the current structure
    pb = ctx['by_id'].get(choice['parent']) if choice['parent'] else None
    ctx['group'] = propagate(ctx['c'], target, choice) if ctx.get('c') else [target]
    return {'key': key(target['id']), 'block_id': target['id'], 'page': target['page'],
            'text': element_text(target, 120), 'current': {'role': role, 'level': lvl},
            'proposed': {'role': choice['role'], 'level': choice['level'], 'parent': choice['parent'],
                         'parent_text': element_text(pb, 80) if pb else None},
            'decision': {'action': 'set_heading_level', 'level': choice['level']},
            'members': [b['id'] for b in ctx.get('group') or [target]],
            'member_texts': [element_text(b, 60) for b in ctx.get('group') or [target]],
            'votes': votes, 'samples': samples,
            'confidence': [a.get('confidence') for a in answers], 'reasons': [a.get('reason') for a in answers]}
