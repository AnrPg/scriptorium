"""Use case: second pass on the HIERARCHY with the local model — proposals only (<book>/structure_llm.json).
The strategy is pipeline.structure_review; the runner (sampling, voting, caching) is application.llm_review."""
from __future__ import annotations

from pathlib import Path

from ..pipeline import structure_review
from . import llm_review


def run(out: Path, samples: int = llm_review.SAMPLES, interpreter=None) -> int:
    return llm_review.run(out, structure_review, samples, interpreter)
