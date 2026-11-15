""""Did you mean ...?" helpers shared by the rules.

Edit-distance matching via the standard library's difflib (the blueprint's choice:
"embeddings only if edit distance proves too weak"). Comparison is case-insensitive,
because the most common near-miss is a capitalisation difference.
"""

from __future__ import annotations

import difflib


def closest(word: str, options: list[str], n: int = 3, cutoff: float = 0.6) -> list[str]:
    """Up to n options most similar to `word`, best first, keeping their original spelling."""
    by_lower: dict[str, str] = {}
    for o in options:
        by_lower.setdefault(o.lower(), o)
    hits = difflib.get_close_matches(word.lower(), list(by_lower), n=n, cutoff=cutoff)
    return [by_lower[h] for h in hits]


def quote_list(items: list[str]) -> str:
    return ", ".join(repr(i) for i in items)
