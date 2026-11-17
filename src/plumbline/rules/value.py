"""Value check: every literal a query filters on must be a value the column can hold.

This is Plumbline's main rule. The M0 baseline showed AI models writing filters that run
fine and match nothing, or the wrong rows:

    WHERE FundingType = 'Locally Funded'     data says 'Locally funded'      VALUE_CASE
    WHERE DOCType = 'State Board of Education (SBOE)'   no such value        VALUE_MISSING
    WHERE CDSCode = '1611920113902'          every code has 14 digits        VALUE_FORMAT
    WHERE name LIKE '%Phyrexia%x%'           no value matches the pattern    LIKE_NO_MATCH

How it decides, per comparison `column <op> literal`:

* Column with a COMPLETE value list (at most 200 distinct values, see profiler.py):
  the catalog knows every value, so "not in the list" is proof the filter matches nothing.
  -> error. A case/space-only difference is reported separately (VALUE_CASE) because
  its fix is certain.
* Column with too many values to list (IDs, names, dates): we can't prove a value is
  missing, but we know the exact shortest and longest value and the usual format.
  - literal longer/shorter than every value      -> error (proven: no exact match)
  - literal's format differs from the usual one  -> warning (formats were sampled)

SQLite semantics this follows:
* `=` and `IN` compare text case-sensitively, so 'Boros' never equals 'boros'.
* `LIKE` ignores case for ASCII letters, so `LIKE 'Boros'` DOES match 'boros': no finding.
* A number compared with a text column is compared as text (`CDSCode = 1611920113902`
  is checked as '1611920113902').
* `!=` / `NOT IN` with a missing value isn't wrong, just useless (it excludes nothing):
  warning, not error.

Comparisons where the column is wrapped in a function (`LOWER(name) = 'x'`,
`SUBSTR(...)`) are skipped: the catalog describes raw values, not transformed ones.
"""

from __future__ import annotations

import re
from typing import Any

from sqlglot import exp

from plumbline.catalog import Catalog, ColumnProfile
from plumbline.findings import ERROR, WARNING, Finding
from plumbline.profiler import value_shape
from plumbline.query import RESOLVED, ParsedQuery
from plumbline.rules._match import closest, quote_list

# A sampled format is trusted only if (nearly) every sampled value has it.
DOMINANT_SHAPE_SHARE = 0.95
SHOW_VALUES = 8  # how many real values to show when nothing is close
# Similarity (difflib ratio, 0..1) above which a missing value counts as a typo of a real
# one. 'Directl funded'/'Directly funded' = 0.97; 'li'/'i' = 0.67 (not a typo).
TYPO_CUTOFF = 0.8


def check(query: ParsedQuery, catalog: Catalog) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple] = set()
    for node, col, literal, negated, like in _comparisons(query.tree):
        ref = query.ref_for(col)
        if ref is None or ref.status != RESOLVED or ref.profile is None:
            continue
        f = _judge(
            ref.table.name,
            ref.profile,
            literal,
            negated,
            like,
            node,
            covered=_alternative_matches(node, col, ref.profile),
        )
        if f is None:
            continue
        key = (f.rule, ref.table.name, ref.profile.name, repr(literal))
        if key not in seen:
            seen.add(key)
            findings.append(f)
    return findings


# ---------------------------------------------------------------- finding comparisons
def _literal_value(node: exp.Expression) -> Any:
    """The Python value of a literal, or None if it isn't a plain literal."""
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.this
        try:
            return int(node.this)
        except ValueError:
            try:
                return float(node.this)
            except ValueError:
                return None
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal) and not node.this.is_string:
        v = _literal_value(node.this)
        return -v if v is not None else None
    return None


def _is_negated(node: exp.Expression) -> bool:
    """Is this test inverted? `!=`, `NOT LIKE`, `NOT IN`, `NOT (x = 'a')`, ...

    SQLGlot spells these differently: NOT LIKE is a Like node with negate=True,
    NOT IN is Not(In), NOT (...) is Not(Paren(...)). Every NOT up the tree flips it.
    (Getting this wrong flagged M0 q400's harmless `NOT LIKE '%suspect%'`.)
    """
    neg = isinstance(node, exp.NEQ) or bool(node.args.get("negate"))
    cur = node.parent
    while cur is not None and not isinstance(cur, (exp.Where, exp.Having, exp.Join, exp.Select)):
        if isinstance(cur, exp.Not):
            neg = not neg
        cur = cur.parent
    return neg


def _node_comparisons(node: exp.Expression):
    """(column, literal value, is_like) for one EQ / NEQ / LIKE / IN node."""
    like = isinstance(node, exp.Like)
    if isinstance(node, exp.In):
        if isinstance(node.this, exp.Column) and not node.args.get("query"):
            for item in node.expressions:
                v = _literal_value(item)
                if v is not None:
                    yield node.this, v, False
        return
    left, right = node.this, node.expression
    if isinstance(left, exp.Column):
        col, lit = left, right
    elif isinstance(right, exp.Column) and not like:
        col, lit = right, left
    else:
        return
    v = _literal_value(lit)
    if v is not None:
        yield col, v, like


def _comparisons(tree: exp.Expression):
    """Yield (node, column, literal value, negated, is_like) for each column-vs-literal test."""
    for node in tree.find_all(exp.EQ, exp.NEQ, exp.Like, exp.In):
        negated = _is_negated(node)
        for col, v, like in _node_comparisons(node):
            yield node, col, v, negated, like


def _same_column(a: exp.Column, b: exp.Column) -> bool:
    return a.name.lower() == b.name.lower() and a.table.lower() == b.table.lower()


def _matches(p: ColumnProfile, v: Any, like: bool) -> bool:
    if like:
        if not (p.values_complete and isinstance(v, str)):
            return True  # can't tell: assume it matches (never raise a false alarm)
        rx = _like_regex(v)
        return any(rx.fullmatch(str(x)) for x, _ in p.values if x is not None)
    return _membership(p, v)[0] in ("yes", "unknown")


def _alternative_matches(node: exp.Expression, col: exp.Column, p: ColumnProfile) -> bool:
    """Does another condition on the same column, OR-ed with this one, match real rows?

    `element IN ('na', 'k', 'mg')`, 'mg' absent: the list still works (gold query q496).
    `desc LIKE '%water%' OR desc LIKE '%bottled drinking water%'`: the second pattern
    matches nothing, but the first covers it (M0 q315). Either way the missing value
    can't change the result, so it is a warning, not an error.
    """
    alternatives = []
    if isinstance(node, exp.In):
        alternatives.append(node)
    cur = node
    while isinstance(cur.parent, exp.Paren):
        cur = cur.parent
    if isinstance(cur.parent, exp.Or):
        top = cur.parent
        while isinstance(top.parent, (exp.Or, exp.Paren)):
            top = top.parent
        alternatives.extend(top.find_all(exp.EQ, exp.Like, exp.In))
    for other in alternatives:
        for c2, v2, like2 in _node_comparisons(other):
            if not _same_column(c2, col) or (other is node and not isinstance(node, exp.In)):
                continue
            if other is node and isinstance(node, exp.In) and _membership(p, v2)[0] != "yes":
                continue  # an IN item counts as the alternative only if it truly exists
            if _matches(p, v2, like2):
                return True
    return False


# ---------------------------------------------------------------- judging one literal
def _is_text_column(p: ColumnProfile) -> bool:
    return bool(p.storage_types) and set(p.storage_types) <= {"text"}


def _is_numeric_column(p: ColumnProfile) -> bool:
    return bool(p.storage_types) and set(p.storage_types) <= {"integer", "real"}


def _as_number(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _membership(p: ColumnProfile, v: Any) -> tuple[str, Any]:
    """('yes'|'case'|'no'|'unknown', the real value that matched case-insensitively)."""
    stored = [x for x, _ in p.values if x is not None]
    if _is_numeric_column(p):
        n = _as_number(v)
        if n is None:
            return ("no", None) if p.values_complete else ("unknown", None)
        nums = {float(x) for x in stored if isinstance(x, (int, float))}
        if float(n) in nums:
            return "yes", None
        return ("no", None) if p.values_complete else ("unknown", None)
    s = str(v)
    if s in {str(x) for x in stored}:
        return "yes", None
    if not p.values_complete:
        return "unknown", None
    key = s.strip().lower()
    for x in stored:
        if str(x).strip().lower() == key:
            return "case", x
    return "no", None


def _evidence(table: str, p: ColumnProfile, v: Any, **extra) -> dict:
    return {
        "table": table,
        "column": p.name,
        "value": v,
        "distinct_values": p.distinct_count,
        "values_complete": p.values_complete,
        **extra,
    }


def _judge(
    table: str,
    p: ColumnProfile,
    v: Any,
    negated: bool,
    like: bool,
    node: exp.Expression,
    covered: bool = False,
) -> Finding | None:
    """covered: another OR-ed condition on the same column matches, so a value that simply
    doesn't exist can't change the result (a capitalisation slip still can: it drops rows)."""
    where = f"{table}.{p.name}"
    snippet = node.sql(dialect="sqlite")
    if like:
        return _judge_like(table, p, v, snippet, negated, covered)

    state, real = _membership(p, v)
    if state == "yes":
        return None
    sev = WARNING if negated else ERROR
    useless = ", so this filter excludes nothing" if negated else ""

    if state == "case":
        return Finding(
            rule="VALUE_CASE",
            severity=sev,
            message=(
                f"{v!r} is not a value of {where}; the data has {real!r} "
                f"(differs only in capitalisation or spaces){useless}."
            ),
            hint=f"Use {real!r}.",
            snippet=snippet,
            evidence=_evidence(table, p, v, actual=real),
        )

    if state == "no":
        stored = [x for x, _ in p.values if x is not None]
        near = closest(str(v), [str(x) for x in stored]) if stored else []
        # OR-ed with a value that does occur: harmless ONLY if this one isn't a near-miss
        # of a real value. `IN ('na', 'k', 'mg')` with 'mg' absent lists something that
        # doesn't occur (gold q496: fine). `IN ('Directl funded', 'Locally funded')` is a
        # typo that silently drops the 'Directly funded' rows. The M3 mutation set showed
        # the first version of this rule (warn whenever covered) hid such typos.
        typo_of = closest(str(v), [str(x) for x in stored], n=1, cutoff=TYPO_CUTOFF)
        if not negated and covered and not typo_of:
            sev = WARNING
            useless = "; another value it is OR-ed with does occur, so the result is unaffected"
        if not stored:
            hint = f"{where} is always NULL."
        elif near:
            hint = f"Did you mean {quote_list(near)}?"
        else:
            hint = f"Values of {where}: {quote_list([str(x) for x in stored[:SHOW_VALUES]])}" + (
                " ..." if len(stored) > SHOW_VALUES else "."
            )
        return Finding(
            rule="VALUE_MISSING",
            severity=sev,
            message=(
                f"{v!r} never occurs in {where} "
                f"(it has {p.distinct_count} distinct values){useless}."
            ),
            hint=hint,
            snippet=snippet,
            evidence=_evidence(table, p, v, closest=near),
        )

    # 'unknown': too many values to list. Use exact length bounds, then sampled formats.
    if negated or not _is_text_column(p):
        return None
    s = str(v)
    example = next((str(x) for x, _ in p.values if x is not None), None)
    if p.text_len_min is not None and not (p.text_len_min <= len(s) <= p.text_len_max):
        span = (
            f"{p.text_len_min}"
            if p.text_len_min == p.text_len_max
            else f"{p.text_len_min} to {p.text_len_max}"
        )
        return Finding(
            rule="VALUE_FORMAT",
            severity=ERROR,
            message=(
                f"{s!r} has {len(s)} characters, but every value of {where} has "
                f"{span}, so it can never match."
            ),
            hint=_format_hint(p, s, example),
            snippet=snippet,
            evidence=_evidence(
                table,
                p,
                v,
                length=len(s),
                length_range=[p.text_len_min, p.text_len_max],
                example=example,
            ),
        )
    if p.shapes and p.shapes[0][1] >= DOMINANT_SHAPE_SHARE:
        usual, lit_shape = p.shapes[0][0], value_shape(s)
        if lit_shape not in {sh for sh, _ in p.shapes}:
            return Finding(
                rule="VALUE_FORMAT",
                severity=WARNING,
                message=(
                    f"Values of {where} look like {usual} (e.g. {example!r}); "
                    f"{s!r} looks like {lit_shape}."
                ),
                hint=_format_hint(p, s, example),
                snippet=snippet,
                evidence=_evidence(
                    table, p, v, usual_format=usual, literal_format=lit_shape, example=example
                ),
            )
    return None


def _format_hint(p: ColumnProfile, s: str, example: str | None) -> str:
    n = p.text_len_max
    if s.isdigit() and n and len(s) < n and p.shapes and p.shapes[0][0] == f"9{{{n}}}":
        return f"Codes here are {n} digits with leading zeros; try {s.zfill(n)!r}."
    return f"Match the stored format, e.g. {example!r}." if example else ""


def _like_regex(pattern: str) -> re.Pattern:
    """SQLite LIKE as a regex: % = any run, _ = one char, ASCII case-insensitive."""
    out = []
    for ch in pattern:
        out.append(".*" if ch == "%" else "." if ch == "_" else re.escape(ch))
    return re.compile("".join(out), re.IGNORECASE | re.DOTALL)


def _judge_like(
    table: str, p: ColumnProfile, v: Any, snippet: str, negated: bool, covered: bool
) -> Finding | None:
    if not p.values_complete or not isinstance(v, str):
        return None
    stored = [str(x) for x, _ in p.values if x is not None]
    rx = _like_regex(v)
    if any(rx.fullmatch(x) for x in stored):
        return None
    core = v.replace("%", " ").replace("_", " ").strip()
    near = closest(core, stored) if core else []
    where = f"{table}.{p.name}"
    # NOT LIKE a pattern nothing matches excludes nothing; an OR-ed pattern that does
    # match covers for this one. Both are useless rather than wrong: warning.
    harmless = negated or covered
    why = (
        ", so this NOT LIKE excludes nothing"
        if negated
        else "; another pattern it is OR-ed with does match, so the result is unaffected"
        if covered
        else ""
    )
    return Finding(
        rule="LIKE_NO_MATCH",
        severity=WARNING if harmless else ERROR,
        message=(
            f"No value of {where} matches LIKE {v!r} (checked all {len(stored)} values){why}."
        ),
        hint=(
            f"Did you mean {quote_list(near)}?"
            if near
            else f"Values of {where}: {quote_list(stored[:SHOW_VALUES])}"
            + (" ..." if len(stored) > SHOW_VALUES else ".")
        ),
        snippet=snippet,
        evidence=_evidence(table, p, v, closest=near),
    )
