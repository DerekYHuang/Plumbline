"""Small statistics helpers, so every reported rate carries its margin of error.

The blueprint's rule: "Know your margin." A rate from a few hundred queries is an
estimate, so we report it with a 95% interval, e.g. "23.1% (18.7-28.1%)".

Wilson score interval, not the textbook p +/- 1.96*sqrt(p(1-p)/n): Wilson stays inside
0..100% and is accurate for small counts and rates near 0, which is exactly where
false-alarm rates live (e.g. 0 of 500). Reference: Brown, Cai & DasGupta (2001),
"Interval Estimation for a Binomial Proportion", Statistical Science 16(2).
"""

from __future__ import annotations

from math import sqrt


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for k successes out of n, as fractions (lo, hi)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def rate(k: int, n: int) -> dict:
    """k/n as a JSON-friendly dict with percent and 95% interval."""
    lo, hi = wilson(k, n)
    return {
        "k": k,
        "n": n,
        "pct": round(100 * k / n, 1) if n else None,
        "ci95": [round(100 * lo, 1), round(100 * hi, 1)],
    }


def fmt(r: dict) -> str:
    """'71/308 = 23.1% (18.7-28.1)' for tables and logs."""
    if not r["n"]:
        return "n/a"
    return f"{r['k']}/{r['n']} = {r['pct']}% ({r['ci95'][0]}-{r['ci95'][1]})"
