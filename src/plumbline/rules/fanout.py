"""Fan-out check: a SUM/COUNT/AVG inflated because a join repeats rows.

Not written from scratch. The M0 go/pivot check found grainguard (MIT) already does this
well: it flagged 14% of the model's silent errors at a 2% false-alarm rate on correct
queries, the best balance of the tools tested. So this module is an *adapter*:

    catalog  ->  grainguard's FactStore  (which column sets are unique in each table)
    query    ->  grainguard's Analyzer   ->  its findings  ->  our Finding format

What we add on top: grainguard normally needs uniqueness *declared* (dbt tests, a YAML
file). We feed it uniqueness *measured* by the profiler -- primary keys plus every column
whose values never repeat -- so it works on a database nobody documented.

grainguard is an optional dependency (`pip install plumbline-sql[fanout]`). Without it
this rule is skipped and the result says so.
"""

from __future__ import annotations

from plumbline.catalog import Catalog
from plumbline.findings import ERROR, Finding
from plumbline.query import DIALECT, ParsedQuery

# grainguard code -> our rule name
_CODES = {"FANOUT_AGG": "FANOUT", "CHASM_TRAP": "CHASM"}

_facts_cache: dict[int, object] = {}


def available() -> bool:
    try:
        import grainguard  # noqa: F401
    except ImportError:
        return False
    return True


def _facts(catalog: Catalog):
    """Build (once per catalog object) grainguard's facts: each table's unique keys."""
    key = id(catalog)
    if key not in _facts_cache:
        from grainguard.facts import FactStore

        fs = FactStore()
        for t in catalog.tables:
            fs.add_columns(t.name, [c.name for c in t.columns])
            if t.primary_key:
                fs.add_key(t.name, t.primary_key)
            for c in t.columns:
                if c.is_unique:
                    fs.add_key(t.name, [c.name])
        _facts_cache[key] = fs
    return _facts_cache[key]


def check(query: ParsedQuery, catalog: Catalog) -> list[Finding]:
    if not available():
        return []
    from grainguard.analyzer import Analyzer

    try:
        result = Analyzer(facts=_facts(catalog), dialect=DIALECT).analyze_sql(query.sql)
    except Exception:
        return []  # grainguard couldn't analyse this SQL: no claim either way
    findings = []
    for f in result.findings:
        rule = _CODES.get(f.code)
        if rule is None:
            continue  # UNKNOWN_GRAIN / PARSE_ERROR: grainguard itself can't tell
        findings.append(
            Finding(
                rule=rule,
                severity=ERROR,
                message=f.message,
                hint=f.fix or "",
                snippet=f.aggregate or "",
                evidence={"join": f.join, "aggregate": f.aggregate, "source": "grainguard 0.1.0"},
            )
        )
    return findings
