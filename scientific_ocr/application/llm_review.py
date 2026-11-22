"""Use case: a SECOND PASS with the local model over the projection (background job) — PROPOSALS ONLY.

One runner, several review strategies (pipeline.structure_review, pipeline.entity_review). A strategy is a module
of pure functions:
  NAME, FILE, MAX_TOKENS
  targets(c)                       what to ask about (only what the deterministic stages left ambiguous)
  prompt(c, t) -> (system, user, ctx)        schema(ctx) -> JSON schema of one constrained answer
  resolve(answer, ctx, c) -> {..., errors}   validate one answer against the document
  vote(resolved) -> (choice, votes)          majority over the valid samples
  proposal(t, choice, votes, n, answers, ctx) -> dict | None   None = the model agrees with the current state
  decorate(p, ctx) (optional)                reviewer-facing extras
  agreement(t, choice, votes, n) (optional)  a record of an agreement (used by the statistics)
  ROUNDS, next_round(c, proposals) (optional)  iterate: the document as if the proposals were accepted; targets
                                   that this simulated document makes ambiguous are asked in the next round
Each target is asked `samples` times (temperature 0, then sampling) and voted: the vote count travels with every
proposal. Answers are cached by (model, prompt, schema, sample), so a rerun asks only about what changed. Nothing
is applied: the reviewer accepts (an ordinary undoable event) or rejects (a remembered dismissal).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ..adapters import local_llm
from ..adapters.storage import atomic_write, read_json
from .processing import file_logger, progress_for

SAMPLES = 3


def run(out: Path, spec, samples: int = SAMPLES, interpreter=None) -> int:
    log = file_logger(out)
    progress = progress_for(out, spec.NAME)
    c = read_json(out / 'canonical.json')
    if not c:
        log(f'{spec.NAME} review: no canonical.json (process the book first)')
        return 2
    it = interpreter or local_llm.interpreter()
    if it is None:
        atomic_write(out / spec.FILE, json.dumps({'status': 'UNAVAILABLE', 'error': 'no local model installed'}))
        log(f'{spec.NAME} review: UNAVAILABLE (no local model installed)')
        return 3
    cache = out / 'cache' / spec.FILE.removesuffix('.json')
    cache.mkdir(parents=True, exist_ok=True)
    targets = spec.targets(c)
    proposals, agreements, invalid, failed = [], [], 0, []
    asked = 0
    doc, seen = c, set()
    for rnd in range(1, getattr(spec, 'ROUNDS', 1) + 1):
        todo = [t for t in (targets if rnd == 1 else spec.targets(doc)) if t['id'] not in seen]
        if not todo:
            break
        new = []
        for i, t in enumerate(todo):
            seen.add(t['id'])
            asked += 1
            progress(spec.NAME, i, len(todo), block=t['id'], round=rnd)
            system, user, ctx = spec.prompt(doc, t)
            sch = spec.schema(ctx)
            answers = []
            for s_ in range(samples):
                # every input of the answer is in the key: a larger MAX_TOKENS must not reuse truncated answers
                k = hashlib.sha1(json.dumps([it.name, system, user, sch, s_, spec.MAX_TOKENS,
                                             0.0 if s_ == 0 else 0.7, 1000 + s_]).encode()).hexdigest()
                f = cache / f'{k}.json'
                ans = read_json(f)
                if ans is None:
                    try:
                        r = it.complete_json(system, user, sch, temperature=0.0 if s_ == 0 else 0.7, seed=1000 + s_,
                                             max_tokens=spec.MAX_TOKENS)
                    except Exception as exc:                      # recorded, the run continues
                        failed.append({'block_id': t['id'], 'error': str(exc)[:200]})
                        break
                    ans = r['json']
                    atomic_write(f, json.dumps(ans, ensure_ascii=False))
                answers.append(ans)
            if not answers:
                continue
            resolved = [spec.resolve(a, ctx, doc) for a in answers]
            invalid += sum(1 for r in resolved if r['errors'])
            choice, votes = spec.vote(resolved)
            p = spec.proposal(t, choice, votes, len(answers), answers, ctx) if choice else None
            if p is None:
                if choice and hasattr(spec, 'agreement'):
                    agreements.append(spec.agreement(t, choice, votes, len(answers)))
                elif choice:
                    agreements.append({'id': t['id']})
                continue
            if hasattr(spec, 'decorate'):
                spec.decorate(p, ctx)
            p.update(prompt=user, model=it.name, round=rnd)
            seen.update(p.get('members') or [])
            new.append(p)
        proposals += new
        if not new or not hasattr(spec, 'next_round'):
            break
        doc = spec.next_round(c, proposals)              # what the structure would be: ask again about the changes
    progress(spec.NAME, len(targets), len(targets))
    total_failure = bool(failed) and len({f['block_id'] for f in failed}) >= asked > 0
    atomic_write(out / spec.FILE, json.dumps({
        'status': 'FAILED' if total_failure else 'OK', **({'error': failed[0]['error']} if total_failure else {}), 'built_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'canonical_built': (c.get('run') or {}).get('built_utc'), 'model': it.name, 'samples': samples,
        'asked': asked, 'agreed_with_current': len(agreements), 'agreements': agreements,
        'invalid_answers': invalid, 'failed': failed, 'proposals': proposals}, ensure_ascii=False, indent=2))
    log(f'{spec.NAME} review: {len(targets)} asked, {len(proposals)} proposals, {len(agreements)} agree, {len(failed)} failed')
    return 1 if total_failure else 0
