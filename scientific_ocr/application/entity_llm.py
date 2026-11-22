"""Use case: second pass on ENTITY CLASSIFICATION with the local model — proposals only (<book>/entity_llm.json).
The strategy is pipeline.entity_review; the runner (sampling, voting, caching) is application.llm_review."""
from __future__ import annotations

from pathlib import Path

from ..pipeline import entity_review
from . import llm_review


def run(out: Path, samples: int = llm_review.SAMPLES, interpreter=None) -> int:
    return llm_review.run(out, entity_review, samples, interpreter)
