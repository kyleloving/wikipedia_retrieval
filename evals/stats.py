"""Lightweight statistics for eval summaries (stdlib only).

Wilson score interval for a binomial proportion. It is more reliable than the
normal approximation for small n and for rates near 0 or 1, which is exactly the
regime of a small eval suite. No SciPy dependency.

Bootstrap CIs and paired run comparison are separate, later steps.
"""

import math

# Standard normal quantile for a two-sided 95% interval.
Z_95 = 1.959963984540054


def wilson_interval(passed: int, total: int, z: float = Z_95):
    """Wilson score interval for `passed` successes out of `total` trials.

    Returns (low, high) as floats in [0, 1], or None when total == 0 (no data,
    so no interval). Raises ValueError if passed is outside [0, total].
    """
    if total <= 0:
        return None
    if passed < 0 or passed > total:
        raise ValueError(f"passed ({passed}) must be in [0, {total}]")

    n = total
    p = passed / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for paired binary outcomes.

    b and c are the discordant counts (b: pass->fail, c: fail->pass). Concordant
    pairs are ignored. Returns 1.0 when there are no discordant pairs. This is a
    directional signal on a small suite — do not treat it as a strong claim.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) * (0.5**n)
    return min(1.0, 2.0 * tail)
