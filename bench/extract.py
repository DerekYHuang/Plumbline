"""Pull the final SQL query out of a model's free-text answer.

The official BIRD prompt asks the model to "think step by step" and then put
the query in a ```sql code block. Models don't always obey, so we try, in order:

1. the LAST ```sql ... ``` block   (reasoning may contain earlier drafts;
                                     the last block is the final answer)
2. the last plain ``` ... ``` block that looks like SQL
3. the last line-run starting with SELECT / WITH

If none match we return None and the question is scored as "no_sql" --
a model failure, kept separate from "wrong SQL" in the results.
"""

from __future__ import annotations

import re

_SQL_BLOCK = re.compile(r"```sql\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)
_ANY_BLOCK = re.compile(r"```[a-zA-Z]*\s*\n(.*?)```", re.DOTALL)
_BARE_SELECT = re.compile(r"^\s*((?:SELECT|WITH)\b.*?)(?:;|\n\s*\n|\Z)",
                          re.IGNORECASE | re.DOTALL | re.MULTILINE)
_STARTS_LIKE_SQL = re.compile(r"^\s*(--[^\n]*\n\s*)*(SELECT|WITH)\b", re.IGNORECASE)


def _clean(sql: str) -> str:
    """Drop comment-only lines and a trailing semicolon; trim whitespace."""
    lines = [ln for ln in sql.strip().splitlines() if not ln.strip().startswith("--")]
    return "\n".join(lines).strip().rstrip(";").strip()


def extract_sql(answer: str) -> str | None:
    """Return the model's final SQL query, or None if we can't find one."""
    blocks = _SQL_BLOCK.findall(answer)
    if blocks:
        sql = _clean(blocks[-1])
        return sql or None

    sqlish = [b for b in _ANY_BLOCK.findall(answer) if _STARTS_LIKE_SQL.match(b)]
    if sqlish:
        return _clean(sqlish[-1]) or None

    bare = _BARE_SELECT.findall(answer)
    if bare:
        return _clean(bare[-1]) or None
    return None
