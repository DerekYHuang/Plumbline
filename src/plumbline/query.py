"""Parse a SQL query and work out which real table each column belongs to.

Every rule needs the same thing first: "this `s.FundingType` is column FundingType of
table schools". Text matching can't do that reliably (aliases, subqueries, CTEs, columns
written without a table prefix), so we use SQLGlot:

1. `sqlglot.parse_one` turns the SQL text into a syntax tree.
2. `qualify` fills in what the author left implicit, using the catalog as the schema:
   `SELECT County FROM schools` becomes `SELECT schools.county FROM schools AS schools`.
   Columns it can't place (because they don't exist) are left unqualified, which is
   exactly what the schema rule looks for.
3. `traverse_scope` walks every SELECT (the main one, CTEs, subqueries) and, for each
   column, tells us its *source*: a real table, or a CTE/subquery ("derived").

The result is a `ParsedQuery` whose `columns` list says, for every column reference,
which catalog table and column it is -- or why it couldn't be resolved.

SQLGlot lower-cases names while qualifying (SQLite ignores case), so every lookup back
into the catalog goes through the catalog's case-insensitive `table()` / `column()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from plumbline.catalog import Catalog, ColumnProfile, TableProfile

DIALECT = "sqlite"

# Why a column reference could or couldn't be tied to a catalog column.
RESOLVED = "resolved"  # found: .table and .profile are set
UNKNOWN_COLUMN = "unknown_column"  # its table is known, but has no such column
UNKNOWN_TABLE = "unknown_table"  # its table isn't in the database (reported separately)
DERIVED = "derived"  # comes from a CTE or subquery: not a real table column
AMBIGUOUS = "ambiguous"  # no prefix, and several tables in scope have that name
UNPLACED = "unplaced"  # no prefix and can't tell (e.g. a CTE is also in scope)


class QueryParseError(ValueError):
    pass


@dataclass
class ColumnRef:
    node: exp.Column  # the node in the (qualified) tree
    name: str  # the name as written (lower-cased by qualify)
    status: str
    table: TableProfile | None = None  # the real table, when known
    profile: ColumnProfile | None = None  # the real column, when resolved
    # For an unknown column: the real tables that were in scope, for "did you mean".
    candidates: list[TableProfile] = field(default_factory=list)


@dataclass
class TableRef:
    node: exp.Table
    name: str
    profile: TableProfile | None  # None -> table doesn't exist in the database


@dataclass
class ParsedQuery:
    sql: str
    tree: exp.Expression
    tables: list[TableRef]
    columns: list[ColumnRef]
    cte_names: set[str]
    qualified: bool  # False if qualify() failed and we fell back to the raw tree
    # One alias given to several tables in the same FROM (`disp d ... district d`).
    # SQLite accepts this and silently picks a table per column. [(alias, [tables])]
    duplicate_aliases: list[tuple[str, list[str]]] = field(default_factory=list)

    def ref_for(self, node: exp.Column) -> ColumnRef | None:
        """The ColumnRef for a column node of `tree` (identity, not equality)."""
        return self._by_id.get(id(node))

    def __post_init__(self) -> None:
        self._by_id = {id(c.node): c for c in self.columns}


def _schema(catalog: Catalog) -> dict[str, dict[str, str]]:
    """The catalog as the {table: {column: type}} mapping qualify() wants."""
    return {
        t.name: {c.name: (c.declared_type or "TEXT") for c in t.columns} for t in catalog.tables
    }


def parse_query(sql: str, catalog: Catalog) -> ParsedQuery:
    try:
        raw = sqlglot.parse_one(sql, read=DIALECT)
    except SqlglotError as e:
        # ParseError (bad grammar) and TokenError (e.g. an unterminated string) both
        # derive from SqlglotError. Found by running the checks over M0's model answers.
        raise QueryParseError(str(e).splitlines()[0][:300]) from e
    if raw is None:
        raise QueryParseError("empty query")

    try:
        tree = qualify(
            raw.copy(),
            schema=_schema(catalog),
            dialect=DIALECT,
            validate_qualify_columns=False,  # unknown columns: leave them, we report them
            quote_identifiers=False,
            identify=False,
        )
        qualified = True
    except Exception:
        # qualify() gives up on some unusual SQL. Fall back to the raw tree: resolution
        # below still works for prefixed columns and simple unprefixed ones.
        tree, qualified = raw, False

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables = [
        TableRef(t, t.name, catalog.table(t.name))
        for t in tree.find_all(exp.Table)
        if t.name and t.name.lower() not in cte_names
    ]

    columns: list[ColumnRef] = []
    try:
        scopes = traverse_scope(tree)
    except Exception:
        scopes = []
    done: set[int] = set()
    duplicates: list[tuple[str, list[str]]] = []
    # traverse_scope yields inner queries before outer ones. A column the inner query
    # can't place is also listed by the outer one (it might be a correlated reference),
    # so resolve each column node once, in the innermost scope that lists it.
    for scope in scopes:
        select_aliases = _select_aliases(scope)
        dup = _duplicate_aliases(scope)
        duplicates.extend((a, [t.name for t in ts]) for a, ts in dup.items())
        for col in scope.columns:
            if id(col) in done:
                continue
            done.add(id(col))
            if (
                not col.table
                and col.name.lower() in select_aliases
                and not _inside_projection(col, scope)
            ):
                continue  # ORDER BY n, where n is `COUNT(*) AS n`: not a table column
            if col.table and col.table.lower() in dup:
                columns.append(_resolve_among(col, dup[col.table.lower()], catalog))
            else:
                columns.append(_resolve(col, scope, catalog))
    return ParsedQuery(sql, tree, tables, columns, cte_names, qualified, duplicates)


def _duplicate_aliases(scope: Scope) -> dict[str, list[exp.Table]]:
    """Aliases used for more than one table in this SELECT's own FROM/JOIN list."""
    sel = scope.expression
    if not isinstance(sel, exp.Select):
        return {}
    by_alias: dict[str, list[exp.Table]] = {}
    for t in sel.find_all(exp.Table):
        if t.find_ancestor(exp.Select) is sel:
            by_alias.setdefault(t.alias_or_name.lower(), []).append(t)
    return {a: ts for a, ts in by_alias.items() if len(ts) > 1}


def _resolve_among(col: exp.Column, tables: list[exp.Table], catalog: Catalog) -> ColumnRef:
    """Resolve `d.x` when alias d names several tables: whichever of them has column x."""
    real = [tp for t in tables if (tp := catalog.table(t.name)) is not None]
    hits = [tp for tp in real if tp.column(col.name) is not None]
    if len(hits) == 1:
        return ColumnRef(col, col.name, RESOLVED, table=hits[0], profile=hits[0].column(col.name))
    if hits:
        return ColumnRef(col, col.name, AMBIGUOUS, candidates=hits)
    return ColumnRef(col, col.name, UNKNOWN_COLUMN, candidates=real)


def _select_aliases(scope: Scope) -> set[str]:
    sel = scope.expression
    if not isinstance(sel, exp.Select):
        return set()
    return {e.alias.lower() for e in sel.expressions if e.alias}


def _inside_projection(col: exp.Column, scope: Scope) -> bool:
    """True if `col` sits in the SELECT list itself (where an alias can't be referenced).

    qualify() gives every projection an alias (`SELECT foo` -> `SELECT foo AS foo`), so a
    bare name matching an alias is only an alias *reference* when it appears elsewhere:
    ORDER BY, GROUP BY, HAVING.
    """
    sel = scope.expression
    if not isinstance(sel, exp.Select):
        return False
    projections = {id(e) for e in sel.expressions}
    node = col
    while node is not None and node is not sel:
        if id(node) in projections:
            return True
        node = node.parent
    return False


def _resolve(col: exp.Column, scope: Scope, catalog: Catalog) -> ColumnRef:
    name = col.name
    if col.table:
        source = scope.sources.get(col.table)
        if source is None:
            # Prefix that names nothing in this scope (e.g. a typo'd alias), or a
            # correlated reference to an outer query. Try the outer scopes.
            source = _outer_source(scope, col.table)
        if isinstance(source, exp.Table):
            tp = catalog.table(source.name)
            if tp is None:
                return ColumnRef(col, name, UNKNOWN_TABLE)
            cp = tp.column(name)
            if cp is None:
                return ColumnRef(col, name, UNKNOWN_COLUMN, table=tp, candidates=[tp])
            return ColumnRef(col, name, RESOLVED, table=tp, profile=cp)
        if source is not None:
            return ColumnRef(col, name, DERIVED)
        return ColumnRef(col, name, UNPLACED)

    # No prefix: look for the column in every real table this scope reads from.
    real, derived = [], False
    for src in scope.sources.values():
        if isinstance(src, exp.Table):
            tp = catalog.table(src.name)
            if tp is not None:
                real.append(tp)
        else:
            derived = True
    hits = [tp for tp in real if tp.column(name) is not None]
    if len(hits) == 1:
        return ColumnRef(col, name, RESOLVED, table=hits[0], profile=hits[0].column(name))
    if len(hits) > 1:
        return ColumnRef(col, name, AMBIGUOUS, candidates=hits)
    if derived or not real:
        return ColumnRef(col, name, UNPLACED)
    # Not in this query's tables: maybe a correlated reference to an outer query's table.
    outer = scope.parent
    while outer is not None:
        for src in outer.sources.values():
            if isinstance(src, exp.Table):
                tp = catalog.table(src.name)
                if tp is not None and tp.column(name) is not None:
                    return ColumnRef(col, name, RESOLVED, table=tp, profile=tp.column(name))
        outer = outer.parent
    return ColumnRef(col, name, UNKNOWN_COLUMN, candidates=real)


def _outer_source(scope: Scope, alias: str):
    s = scope.parent
    while s is not None:
        if alias in s.sources:
            return s.sources[alias]
        s = s.parent
    return None
