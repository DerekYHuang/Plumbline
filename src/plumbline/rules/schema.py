"""Schema check: every table and column the query names must exist.

The database itself rejects these queries too ("no such column"), so this rule is less
about catching silent errors and more about the *hint*: an AI that is told
"no column FundingTypes; did you mean FundingType?" can repair the query in one step,
without first running it. In M0, 107 of the model's 144 crashing queries were invented
columns or tables.

Findings (all severity error):
    SCHEMA_ALIAS    one alias names two tables       SQLite *runs* this, silently picking a
                    (`disp d JOIN district d`)       table per column: a silent error, found
                                                     in 2 of M0's wrong answers
    SCHEMA_TABLE    the table doesn't exist          hint: closest real table names
    SCHEMA_COLUMN   the column isn't in its table    hint: closest names in the tables the
                                                     query uses, or the other table that
                                                     really has it (a missing join)
"""

from __future__ import annotations

from plumbline.catalog import Catalog
from plumbline.findings import ERROR, Finding
from plumbline.query import UNKNOWN_COLUMN, ParsedQuery
from plumbline.rules._match import closest, quote_list


def check(query: ParsedQuery, catalog: Catalog) -> list[Finding]:
    findings: list[Finding] = []
    all_tables = [t.name for t in catalog.tables]

    seen_tables: set[str] = set()
    for ref in query.tables:
        if ref.profile is not None or ref.name.lower() in seen_tables:
            continue
        seen_tables.add(ref.name.lower())
        near = closest(ref.name, all_tables)
        findings.append(
            Finding(
                rule="SCHEMA_TABLE",
                severity=ERROR,
                message=f"Table {ref.name!r} does not exist in {catalog.database}.",
                hint=(f"Did you mean {quote_list(near)}?" if near else
                      f"Tables in this database: {quote_list(all_tables)}."),
                snippet=ref.name,
                evidence={"table": ref.name, "closest": near},
            )
        )

    for alias, names in dict(query.duplicate_aliases).items():
        findings.append(
            Finding(
                rule="SCHEMA_ALIAS",
                severity=ERROR,
                message=(f"Alias {alias!r} is used for {len(names)} tables "
                         f"({', '.join(names)}). SQLite runs this anyway and silently picks "
                         f"one table per column, so columns and joins can come from the "
                         f"wrong table."),
                hint=f"Give each table its own alias, e.g. {alias}1, {alias}2.",
                snippet=alias,
                evidence={"alias": alias, "tables": names},
            )
        )

    seen_cols: set[tuple[str, str]] = set()
    for ref in query.columns:
        if ref.status != UNKNOWN_COLUMN:
            continue
        key = (ref.name.lower(), ",".join(t.name for t in ref.candidates))
        if key in seen_cols:
            continue
        seen_cols.add(key)
        where = " or ".join(t.name for t in ref.candidates)
        in_scope = [c.name for t in ref.candidates for c in t.columns]
        near = closest(ref.name, in_scope)
        # Is it a real column of some *other* table? Then the query forgot a join.
        elsewhere = [
            t.name
            for t in catalog.tables
            if t not in ref.candidates and t.column(ref.name) is not None
        ]
        # Spell it the way the database does (the parser lower-cases names).
        real_name = (catalog.column(elsewhere[0], ref.name).name if elsewhere else ref.name)
        if near:
            hint = f"Did you mean {quote_list(near)}?"
        elif elsewhere:
            hint = (f"{real_name!r} is a column of {quote_list(elsewhere)}, which this "
                    f"query does not join.")
        else:
            hint = f"Columns of {where}: {quote_list(in_scope[:15])}."
        if near and elsewhere:
            hint += f" ({real_name!r} also exists in {quote_list(elsewhere)}.)"
        findings.append(
            Finding(
                rule="SCHEMA_COLUMN",
                severity=ERROR,
                message=f"Column {ref.name!r} does not exist in {where}.",
                hint=hint,
                snippet=ref.node.sql(dialect="sqlite"),
                evidence={
                    "column": ref.name,
                    "tables": [t.name for t in ref.candidates],
                    "closest": near,
                    "exists_in": elsewhere,
                },
            )
        )
    return findings
