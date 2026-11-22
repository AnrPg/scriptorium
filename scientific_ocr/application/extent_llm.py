"""Use case: second pass on ENTITY EXTENTS with the local model — proposals only (<book>/extent_llm.json).
The strategy is pipeline.extent_review; the runner (sampling, voting, caching) is application.llm_review."""
from __future__ import annotations

from pathlib import Path

from ..pipeline import extent_review
from . import llm_review


def run(out: Path, samples: int = llm_review.SAMPLES, interpreter=None) -> int:
    return llm_review.run(out, extent_review, samples, interpreter)
