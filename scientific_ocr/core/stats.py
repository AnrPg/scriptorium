"""Statistics for review triage (pure)."""
from __future__ import annotations

import math


def wilson_upper(errors: int, n: int, z: float = 1.96) -> float:
    """Upper bound of the Wilson score interval for an error rate (95% by default). n=0 -> 1.0."""
    if n == 0:
        return 1.0
    p = errors / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (centre + margin) / den)


def sample_size(max_error: float, z: float = 1.96) -> int:
    """Smallest n such that zero errors in n samples puts the Wilson upper bound below max_error."""
    n = 1
    while wilson_upper(0, n, z) > max_error and n < 5000:
        n += 1
    return n
