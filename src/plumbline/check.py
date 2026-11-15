"""check_sql: the one function the rest of the world calls.

    from plumbline import check_sql, Catalog
    result = check_sql("SELECT ... WHERE FundingType = 'Locally Funded'",
                       Catalog.load("california_schools.json"))
    result.passed          # False
    result.findings[0]     # VALUE_CASE, with evidence and the hint "Use 'Locally funded'."

Deterministic and offline: no database connection, no model, same input -> same output.
"""

from __future__ import annotations

from plumbline.catalog import Catalog
from plumbline.findings import CheckResult
from plumbline.query import QueryParseError, parse_query
from plumbline.rules import RULES, fanout


def check_sql(sql: str, catalog: Catalog) -> CheckResult:
    result = CheckResult(sql=sql)
    if catalog.is_stale():
        result.notes.append(
            f"The catalog for {catalog.database} may be out of date (the database file "
            "changed since it was profiled). Re-run `plumbline profile`."
        )
    if not fanout.available():
        result.notes.append("Fan-out check skipped: install plumbline-sql[fanout].")
    try:
        query = parse_query(sql, catalog)
    except QueryParseError as e:
        result.parse_error = str(e)
        return result
    if not query.qualified:
        result.notes.append("Column resolution was partial (unusual SQL); some checks may miss.")
    for rule in RULES:
        result.findings.extend(rule(query, catalog))
    return result
