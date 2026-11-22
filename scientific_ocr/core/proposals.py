"""Proposals: structured actions inferred from a reviewer's natural-language note (pure domain logic).

A model only PROPOSES. The proposal is validated against `schema(labels)` and against the segment it refers to, rendered
by `describe` into a deterministic sentence (what the code will do, independent of the model's own prose), and
executed only after the reviewer accepts it.
"""
from __future__ import annotations

import re

from .model import MAX_HEADING_LEVEL
from .symbols import strip_math_delimiters

ACTIONS = {
    'replace_text': 'replace the segment text with `value`',
    'replace_latex': 'replace the segment LaTeX with `value`',
    'add_rule': 'add a book-wide rule `rule` (isolated token find -> replace, optional context regex)',
    'relabel': 'change the segment type to engine label `label` and re-OCR it as that type',
    'set_heading_level': f'make the segment a heading of level `level` (1-{MAX_HEADING_LEVEL}) or not a heading (0)',
    'reocr': 're-OCR the segment as one region (text missing or garbled, no exact text given)',
    'mark_image_only': 'keep the segment as an image only',
    'mark_unreadable': 'mark the segment unreadable',
    'approve': 'the current result is correct',
    'none': 'no action can be inferred; ask for clarification',
}

_COMMON = {'understanding': {'type': 'string', 'description': 'Greek paraphrase of what the reviewer asks'},
           'confidence': {'type': 'string', 'enum': ['high', 'medium', 'low']},
           'clarification': {'type': ['string', 'null'], 'description': 'question to the reviewer if unclear'}}
_FIELDS = {   # action -> the fields it REQUIRES (all other fields are absent)
    'replace_text': {'value': {'type': 'string', 'minLength': 1}},
    'replace_latex': {'value': {'type': 'string', 'minLength': 1}},
    'add_rule': {'rule': {'type': 'object', 'additionalProperties': False, 'required': ['find', 'replace', 'context'],
                          'properties': {'find': {'type': 'string', 'minLength': 1}, 'replace': {'type': 'string'},
                                         'context': {'type': 'string'}}}},
    'relabel': {'label': None},                                   # filled from the engine label catalog
    'set_heading_level': {'level': {'type': 'integer', 'minimum': 0, 'maximum': MAX_HEADING_LEVEL}},
    'reocr': {}, 'mark_image_only': {}, 'mark_unreadable': {}, 'approve': {}, 'none': {},
}


def schema(labels: dict) -> dict:
    """Discriminated union: one object shape per action, label names restricted to the engine catalogs.
    Grammar-constrained decoding against this schema cannot emit an inconsistent action/field combination."""
    label = {'anyOf': [{'type': 'object', 'additionalProperties': False, 'required': ['engine', 'name'],
                        'properties': {'engine': {'const': eng}, 'name': {'enum': [x['name'] for x in labels[eng]]}}}
                       for eng in ('marker', 'docling')]}
    variants = []
    for action, fields in _FIELDS.items():
        props = {**_COMMON, 'action': {'const': action},
                 **{k: (label if k == 'label' else v) for k, v in fields.items()}}
        variants.append({'type': 'object', 'additionalProperties': False,
                         'required': ['understanding', 'action', 'confidence', 'clarification', *fields], 'properties': props})
    return {'anyOf': variants}


def normalize(p: dict) -> dict:
    """Uniform shape for storage/UI: every field present (null when unused); LaTeX without $ delimiters."""
    value = p.get('value')
    if p.get('action') == 'replace_latex' and isinstance(value, str):
        value = strip_math_delimiters(value)
    return {'understanding': p.get('understanding'), 'action': p.get('action'), 'value': value,
            'rule': p.get('rule'), 'label': p.get('label'), 'level': p.get('level'),
            'confidence': p.get('confidence'), 'clarification': p.get('clarification')}


def _strip(p: dict) -> dict:
    keep = {'understanding', 'action', 'confidence', 'clarification', *_FIELDS.get(p.get('action'), {})}
    return {k: v for k, v in p.items() if k in keep}


def validate(p: dict, segment: dict, labels: dict) -> list[str]:
    """Schema + domain validation of a normalized proposal. Returns readable errors (empty = valid)."""
    import jsonschema
    if p.get('action') not in _FIELDS:
        return [f'unknown action {p.get("action")!r}']
    v = jsonschema.Draft202012Validator(schema(labels))
    if not v.is_valid(_strip(p)):
        best = jsonschema.exceptions.best_match(v.iter_errors(_strip(p)))
        variant = next(x for x in schema(labels)['anyOf'] if x['properties']['action']['const'] == p['action'])
        errs = [e.message for e in jsonschema.Draft202012Validator(variant).iter_errors(_strip(p))]
        return errs or [best.message if best else 'does not match the proposal schema']
    errors: list[str] = []
    a = p['action']
    if a in ('replace_text', 'replace_latex') and not (p['value'] or '').strip():
        errors.append(f'{a} needs a non-empty value')
    if a == 'replace_latex' and p['value'] and p['value'].count('{') != p['value'].count('}'):
        errors.append('LaTeX value has unbalanced braces')
    if a in ('replace_text', 'replace_latex') and p['value']:
        squash = lambda t: re.sub(r'\s+|\$', '', t or '')
        if squash(p['value']) in (squash(segment.get('text')), squash(segment.get('latex'))):
            errors.append('the proposed value is identical to the current result (nothing would change)')
    if a == 'add_rule':
        r = p['rule'] or {}
        if not (r.get('find') or '').strip() or r.get('replace') is None:
            errors.append('add_rule needs rule.find and rule.replace')
        elif r['find'] == r['replace']:
            errors.append('rule.find equals rule.replace')
        else:
            if r.get('context'):
                try:
                    re.compile(r['context'])
                except re.error as exc:
                    errors.append(f'rule.context is not a valid regex: {exc}')
            text = segment.get('text') or ''
            if not re.search(r'(?<!\w)' + re.escape(r['find']) + r'(?!\w)', text):
                errors.append(f'rule.find {r["find"]!r} does not occur as an isolated token in this segment')
    if a == 'relabel':
        lab = p['label'] or {}
        names = {x['name'] for x in labels.get(lab.get('engine'), [])}
        if lab.get('name') not in names:
            errors.append(f'label {lab.get("engine")}:{lab.get("name")} is not in the engine catalog')
    if a == 'set_heading_level' and p['level'] is None:
        errors.append(f'set_heading_level needs level 0..{MAX_HEADING_LEVEL}')
    return errors


def describe(p: dict, segment: dict, labels: dict) -> str:
    """What the code will do, in Greek, derived ONLY from the structured proposal."""
    a = p.get('action')
    cur = re.sub(r'\$', '', segment.get('text') or segment.get('latex') or '').strip()
    if a == 'replace_text':
        return f'Το κείμενο του τμήματος θα αντικατασταθεί από: «{p["value"]}» (σήμερα: «{cur[:200]}»).'
    if a == 'replace_latex':
        return f'Το LaTeX του τμήματος θα γίνει: {p["value"]} (σήμερα: {cur[:200] or "μόνο εικόνα"}).'
    if a == 'add_rule':
        r = p['rule']
        ctx = f', μόνο σε προτάσεις που ταιριάζουν με «{r["context"]}»' if r.get('context') else ''
        return (f'Νέος κανόνας για όλο το βιβλίο: το απομονωμένο σύμβολο «{r["find"]}» γίνεται «{r["replace"]}»{ctx}. '
                'Δεν εφαρμόζεται όπου η δεύτερη μηχανή επιβεβαιώνει το αρχικό σύμβολο. Θα δεις προεπισκόπηση.')
    if a == 'relabel':
        lab = p['label']
        el = next((x['el'] for x in labels.get(lab['engine'], []) if x['name'] == lab['name']), lab['name'])
        return f'Το τμήμα θα σημανθεί ως «{el}» ({lab["engine"]}: {lab["name"]}) και θα γίνει νέο OCR ως τέτοιο.'
    if a == 'set_heading_level':
        return 'Το τμήμα δεν θα θεωρείται επικεφαλίδα.' if p['level'] == 0 else \
            f'Το τμήμα θα γίνει επικεφαλίδα επιπέδου {p["level"]} στη δομή του βιβλίου.'
    if a == 'reocr':
        return 'Θα γίνει νέο OCR του τμήματος ως ενιαία περιοχή· η σημείωση μένει μαζί του για τον έλεγχο.'
    return {'mark_image_only': 'Το τμήμα θα κρατηθεί μόνο ως εικόνα.',
            'mark_unreadable': 'Το τμήμα θα σημειωθεί ως δυσανάγνωστο.',
            'approve': 'Το τρέχον αποτέλεσμα θα εγκριθεί ως σωστό.',
            'none': 'Καμία ενέργεια· χρειάζεται διευκρίνιση.'}.get(a, 'Άγνωστη ενέργεια.')


def prompt(segment: dict, note: str, labels: dict) -> tuple[str, str]:
    """System + user prompt for the interpreter model (the image of the segment is attached separately)."""
    names = {e: ', '.join(x['name'] for x in labels[e]) for e in labels}
    system = (
        'You convert a proofreader\'s note about one OCR segment of a scanned (often Greek) science textbook into '
        'ONE structured action. You see the original image of the segment, its current OCR text and the note. '
        'Never invent content that is not visible in the image. If the note gives the exact correction, or the image '
        'makes it unambiguous, put the full corrected text/LaTeX in `value`. Use add_rule only for a systematic '
        'symbol swap the reviewer says applies to the whole book. Use relabel when the reviewer says the segment is '
        'a different kind (e.g. an equation, a caption, a header). `understanding` must restate the request in '
        'Greek, specifically. If the note is unclear, use action "none" and ask in `clarification` (Greek).\n'
        f'Actions: {"; ".join(f"{k}: {v}" for k, v in ACTIONS.items())}.\n'
        f'Marker labels: {names.get("marker")}.\nDocling labels: {names.get("docling")}.\n'
        'Unused fields must be null. Answer with JSON only.')
    user = (f'Segment type: {segment.get("kind")} (engine label {segment.get("engine_label")})\n'
            f'Current result ({segment.get("chosen_type")}):\n{segment.get("text") or segment.get("latex") or "(image only)"}\n'
            f'Other engine readings:\n{segment.get("candidates") or "(none)"}\n'
            f'Flags: {segment.get("reasons")}\n\nReviewer note:\n{note}')
    return system, user



# ---------------------------------------------------------------- page-structure proposals (group / merge)
def structure_schema(block_ids: list[str], labels: dict, group_types: list[str]) -> dict:
    """Members restricted to the page's segment ids; merge labels to the engine catalogs; group types to the
    configured vocabulary. Constrained decoding cannot reference a segment that does not exist."""
    member = {'enum': block_ids}
    label = {'anyOf': [{'type': 'object', 'additionalProperties': False, 'required': ['engine', 'name'],
                        'properties': {'engine': {'const': e}, 'name': {'enum': [x['name'] for x in labels[e]]}}}
                       for e in ('marker', 'docling')]}
    merge = {'type': 'object', 'additionalProperties': False, 'required': ['op', 'members', 'label', 'reason'],
             'properties': {'op': {'const': 'merge'}, 'members': {'type': 'array', 'items': member, 'minItems': 2},
                            'label': label, 'reason': {'type': 'string'}}}
    group = {'type': 'object', 'additionalProperties': False, 'required': ['op', 'members', 'type', 'name', 'reason'],
             'properties': {'op': {'const': 'group'}, 'members': {'type': 'array', 'items': member, 'minItems': 2},
                            'type': {'enum': group_types}, 'name': {'type': 'string'}, 'reason': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['understanding', 'operations'],
            'properties': {'understanding': {'type': 'string'},
                           'operations': {'type': 'array', 'maxItems': 12, 'items': {'anyOf': [merge, group]}}}}


def structure_prompt(segments: list[dict], group_types: dict, labels: dict) -> tuple[str, str]:
    system = (
        'You look at one page of a scanned textbook and the list of segments an OCR pipeline produced for it, in '
        'reading order. Propose ONLY clearly justified structure operations:\n'
        '- merge: several segments are really ONE entity that the layout split (a paragraph cut into pieces, a '
        'caption cut into fragments). Members must be on this page and contiguous in meaning. Choose the label of '
        'the merged entity from the engine catalogs.\n'
        '- group: several segments belong together but stay separate (an example with its solution, an exercise, '
        'a definition box, a sidebar, a figure with its notes). Choose the group type; give a short name if the page '
        'prints one (e.g. "Example 4.3").\n'
        'Never merge segments of different nature (a figure with a paragraph); never invent segments. If nothing is '
        'clearly needed, return an empty list. Write `understanding` and each `reason` in Greek. JSON only.\n'
        f'Group types: {", ".join(f"{k} ({v})" for k, v in group_types.items())}.')
    user = 'Segments (id | type | text):\n' + '\n'.join(
        f'{x["id"]} | {x["kind"]} | {x["text"]}' for x in segments)
    return system, user


def validate_structure(op: dict, page_blocks: dict) -> list[str]:
    errs = []
    members = op.get('members') or []
    if len(set(members)) < 2:
        errs.append('needs at least two different segments')
    if any(m not in page_blocks for m in members):
        errs.append('refers to a segment that is not on this page')
    if op.get('op') == 'merge':
        kinds = {page_blocks[m]['kind'] for m in members if m in page_blocks}
        visual = {'figure', 'chart', 'molecular_structure', 'reaction_scheme', 'lewis_structure'}
        if kinds & visual and kinds - visual:
            errs.append('a merge cannot mix a figure with text (group them instead)')
    return errs
