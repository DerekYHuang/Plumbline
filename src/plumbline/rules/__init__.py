"""The checks. One module per rule; each exposes `check(query, catalog) -> list[Finding]`.

    schema.py   tables and columns that don't exist             (SCHEMA_TABLE, SCHEMA_COLUMN)
    value.py    filter values the column never holds             (VALUE_CASE, VALUE_MISSING,
                                                                  VALUE_FORMAT, LIKE_NO_MATCH)
    fanout.py   aggregates inflated by a join (via grainguard)   (FANOUT, CHASM)

Rules read only the catalog and the parsed query, never the database, so a check takes
milliseconds and can't change any data.
"""

from plumbline.rules import fanout, schema, value

RULES = [schema.check, value.check, fanout.check]
