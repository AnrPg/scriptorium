"""Stage: data-driven review triage. Human attention goes where the machine is measurably wrong.

1. Outcomes: every reviewer decision on a flagged segment says whether the MACHINE result was right
   (approve; image-only where the machine already kept the image) or wrong (edit, other candidate, unreadable,
   re-OCR request, revert to... ). Each outcome is attributed to the flags the segment carried. Outcomes of
   calibration books (same series/publisher) are added as prior evidence.
2. Policy per flag: with >= MIN_EVIDENCE outcomes and a 95% upper error bound <= MAX_ERROR, an undecided segment
   whose only mandatory reason is that flag is lowered from 'required' to 'recommended' (TRIAGE_DOWNGRADED).
3. Sampling acceptance: a reviewer audited a random sample of a flag class (class_accept event); if the sample's
   upper error bound passed the threshold, the remaining undecided segments of that class become 'accepted'
   (statistically accepted; never VERIFIED individually).
Guardrail: flags whose finding is CRITICAL (symbols, arrows, charges, numbers, equations) are never lowered or
accepted statistically.
"""
from __future__ import annotations

from collections import defaultdict

from ..core.decisions import CONTENT_ACTIONS
from ..core.reasons import REASONS, flag
from ..core.stats import wilson_upper

MIN_EVIDENCE = 20
MAX_ERROR = 0.05
RIGHT, WRONG = 'right', 'wrong'
HUMAN_REASONS = {'DECISION_STALE', 'HUMAN_REPORTED_WRONG', 'HUMAN_REGION', 'BOOK_RULE_APPLIED', 'MERGED_SEGMENT',
                 'RESOLVED_BY_HUMAN_DECISION', 'TRIAGE_DOWNGRADED', 'ACCEPTED_BY_SAMPLING'}


def critical(code: str) -> bool:
    r = REASONS.get(code)
    return bool(r and r.finding and r.finding[1] == 'critical')


def outcome(block: dict) -> str | None:
    """Was the machine result right, judged by the latest content decision on the block?"""
    # only decisions about CONTENT count: a later heading level, output switch or move says nothing about whether
    # the machine text was right (and must not hide an earlier correction)
    content = [d['action'] for d in block.get('decisions') or [] if d['action'] in CONTENT_ACTIONS | {'report_wrong'}]
    acts = [a for a in content if a != 'report_wrong'] or content
    if not acts:
        return None
    a = acts[-1]
    if a == 'approve':
        return RIGHT
    if a == 'mark_image_only':
        return RIGHT if block.get('machine_image_only') else WRONG
    if a in ('replace_text', 'replace_latex', 'approve_candidate', 'mark_unreadable', 'report_wrong'):
        return WRONG
    return None


def collect(blocks: list[dict]) -> list[tuple[str, str]]:
    out = []
    for b in blocks:
        o = outcome(b)
        if o:
            out += [(r, o) for r in b.get('machine_reasons', b['reasons']) if r not in HUMAN_REASONS]
    return out


def stats(outcomes: list[tuple[str, str]]) -> dict:
    agg = defaultdict(lambda: {'n': 0, 'wrong': 0})
    for r, o in outcomes:
        agg[r]['n'] += 1
        agg[r]['wrong'] += o == WRONG
    return {r: {**v, 'error_rate': v['wrong'] / v['n'], 'upper': wilson_upper(v['wrong'], v['n']),
                'critical': critical(r)} for r, v in agg.items()}


def apply(blocks: list[dict], prior: list[tuple[str, str]], accepts: list[dict]) -> dict:
    st = stats(collect(blocks) + prior)
    reliable = {r for r, v in st.items() if not v['critical'] and v['n'] >= MIN_EVIDENCE and v['upper'] <= MAX_ERROR}
    accepted = {a['reason']: a for a in accepts if not critical(a['reason'])}
    lowered, sampled = 0, 0
    for b in blocks:
        if b.get('decisions') or b['review'] not in ('required', 'recommended'):
            continue
        if any(critical(r) for r in b['reasons']):
            continue
        mandatory = {r for r in b['reasons'] if REASONS.get(r) and REASONS[r].review == 'required'}
        open_reasons = {r for r in b['reasons'] if REASONS.get(r) and REASONS[r].review in ('required', 'recommended')}
        if open_reasons and open_reasons <= set(accepted):
            b['review'] = 'accepted'
            flag(b, 'ACCEPTED_BY_SAMPLING', events=[accepted[r]['seq'] for r in open_reasons])
            sampled += 1
        elif b['review'] == 'required' and mandatory and mandatory <= reliable:
            b['review'] = 'recommended'
            flag(b, 'TRIAGE_DOWNGRADED', evidence={r: st[r] for r in mandatory})
            lowered += 1
    return {'stats': st, 'reliable': sorted(reliable), 'accepted_classes': sorted(accepted),
            'lowered': lowered, 'accepted': sampled, 'min_evidence': MIN_EVIDENCE, 'max_error': MAX_ERROR}
