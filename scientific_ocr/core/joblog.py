"""Facts in job logs: the pipeline writes, next to its free-text log lines, FACT lines — one JSON object each,
prefixed with FACT_MARK — so a job's outcome can be shown to people in plain language (the UI renders them) while
the raw log stays complete for debugging. Pure: formatting and parsing only (no I/O).

Kinds written today:
  trigger   {count, events: [{type, action?, block_id?, entity_type?, pages?}], first?}   why a rebuild ran
  changes   {review: {name: [before, after]}, entities: {total: [b, a], verified: [b, a]},
             levels: [{title, before, after}], findings: {type: [b, a]}, pages: [{page, before, after}],
             release: [b, a], heading_proposals}                                    what the rebuild changed
  built     {blocks, required, recommended, critical, release_status, seconds, timings: {stage: s}}
  extraction {pages, failed: [page], profile_pages?}       pages read by the OCR engines
  diligent  {pages: [page]}                                 automatic careful re-examination started
  models    {engine, crops}                                 a specialised model ran
"""
from __future__ import annotations

import json
import re

FACT_MARK = '@@fact '
_LINE = re.compile(r'^(?:\S+\s)?' + re.escape(FACT_MARK) + r'(\{.*\})\s*$')


def fact(kind: str, **data) -> str:
    """One log line carrying a fact (pass it to the job's `log`)."""
    return FACT_MARK + json.dumps({'kind': kind, **data}, ensure_ascii=False, default=str)


def parse(text: str) -> list[dict]:
    """Facts of a log, in order (lines may carry the logger's timestamp prefix). Malformed lines are skipped."""
    out = []
    for line in (text or '').splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get('kind'):
            out.append(d)
    return out
