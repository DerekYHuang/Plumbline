"""Go/pivot check (end of M0): do existing SQL checkers already catch the model's silent errors?

Runs three open-source static checkers over the qwen7b-r1 predictions and over the BIRD gold
queries, and reports for each tool:

  * detection  - share of `wrong_result` predictions it flags (the errors Plumbline targets)
  * false alarms - share of gold queries (all correct by definition) and of the model's own
    `correct` predictions that it flags

It also measures one error class none of these tools looks at: a filter on a string value that
does not exist in the column (e.g. FundingType = 'Locally Funded' when the data says
'Locally funded'). That share decides whether Plumbline's value check is new ground.

Every tool gets the same facts, read from each SQLite database itself:
  primary keys + UNIQUE indexes -> grain / unique keys;  foreign keys -> join cardinality.
Nothing is hand-tuned per query.

Run it with the isolated comparison venv (the tools are not project dependencies):

    .compare\\venv\\Scripts\\python compare\\run_competitors.py qwen7b-r1

Output: compare/results/<run_id>/per_query.jsonl and summary.json
"""
from __future__ import annotations

import collections
import json
import sqlite3
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "bird_mini_dev"
QUESTIONS = DATA / "mini_dev_sqlite.jsonl"
DIALECT = "sqlite"


# --------------------------------------------------------------------------- database facts
def find_db(db_id: str) -> Path:
    hits = list(DATA.rglob(f"{db_id}.sqlite"))
    if not hits:
        raise FileNotFoundError(db_id)
    return hits[0]


class DbFacts:
    """Keys and relationships read straight from one SQLite file (read-only)."""

    def __init__(self, path: Path):
        self.path = path
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cur = self.con.cursor()
        self.tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        self.columns: dict[str, list[tuple[str, str]]] = {}   # table -> [(col, declared type)]
        self.keys: dict[str, list[list[str]]] = {}           # table -> list of unique key sets
        self.fks: list[tuple[str, str, list[tuple[str, str]]]] = []  # (child, parent, [(c, p)])
        for t in self.tables:
            info = list(cur.execute(f'PRAGMA table_info("{t}")'))
            self.columns[t] = [(r[1], (r[2] or "").upper()) for r in info]
            keys = []
            pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5] > 0]
            if pk:
                keys.append(pk)
            for idx in cur.execute(f'PRAGMA index_list("{t}")').fetchall():
                if idx[2]:  # unique
                    cols = [r[2] for r in cur.execute(f'PRAGMA index_info("{idx[1]}")')]
                    if cols and cols not in keys:
                        keys.append(cols)
            self.keys[t] = keys
            groups: dict[int, tuple[str, list]] = {}
            for r in cur.execute(f'PRAGMA foreign_key_list("{t}")'):
                gid, parent, frm, to = r[0], r[2], r[3], r[4]
                groups.setdefault(gid, (parent, []))[1].append((frm, to))
            for parent, pairs in groups.values():
                self.fks.append((t, parent, pairs))

    def is_unique(self, table: str, cols: list[str]) -> bool:
        s = {c.lower() for c in cols}
        return any({k.lower() for k in key} <= s for key in self.keys.get(table, []))

    # ---- per-tool encodings ------------------------------------------------
    def sqlsure_model(self):
        from sqlsure.model import SemanticModel
        numeric = ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC")
        tables, joins = {}, []
        for t in self.tables:
            key_cols = {c for k in self.keys[t] for c in k}
            fk_cols = {frm for child, _, pairs in self.fks if child == t for frm, _ in pairs}
            measures = {c: "additive" for c, typ in self.columns[t]
                        if any(n in typ for n in numeric) and c not in key_cols | fk_cols}
            tables[t] = {"grain": self.keys[t][0] if self.keys[t] else [], "measures": measures}
        for child, parent, pairs in self.fks:
            child_unique = self.is_unique(child, [c for c, _ in pairs])
            joins.append({"left": child, "right": parent,
                          "cardinality": "one_to_one" if child_unique else "many_to_one",
                          "keys": [[c, p if p else c] for c, p in pairs]})
        return SemanticModel.from_dict({"tables": tables, "joins": joins})

    def grainguard_facts(self):
        from grainguard.facts import FactStore
        fs = FactStore()
        for t in self.tables:
            fs.add_columns(t, [c for c, _ in self.columns[t]])
            for k in self.keys[t]:
                fs.add_key(t, k)
        return fs

    def graincheck_uniques(self) -> dict:
        return {t.lower(): [frozenset(c.lower() for c in k) for k in ks]
                for t, ks in self.keys.items() if ks}


# --------------------------------------------------------------------------- the three tools
def run_sqlsure(sql: str, facts: DbFacts) -> list[dict]:
    from sqlsure import check
    return [{"rule": v.rule, "severity": v.severity}
            for v in check(sql, facts._sqlsure, dialect=DIALECT)]


def run_grainguard(sql: str, facts: DbFacts) -> list[dict]:
    from grainguard.analyzer import Analyzer
    res = Analyzer(facts=facts._grainguard, dialect=DIALECT).analyze_sql(sql)
    return [{"rule": f.code, "severity": f.severity} for f in res.findings
            if f.code != "PARSE_ERROR"]


def run_graincheck(sql: str, facts: DbFacts) -> list[dict]:
    from graincheck.scanner import scan_sql
    findings, _err = scan_sql(sql, "query", dialect=DIALECT, uniques=facts._graincheck)
    out = []
    for f in findings:
        rule = getattr(f, "rule", None) or getattr(f, "code", None) or getattr(f, "rule_id", "?")
        sev = getattr(f, "impact", None) or getattr(f, "severity", "?")
        out.append({"rule": str(rule), "severity": str(sev).lower()})
    return out


TOOLS = {"sqlsure": run_sqlsure, "grainguard": run_grainguard, "graincheck": run_graincheck}
# What counts as "the tool rejected this query". Warnings that a tool itself describes as
# "can't verify" (UNDECLARED_JOIN, UNKNOWN_GRAIN) are not rejections.
STRONG = {"sqlsure": {"error"}, "grainguard": {"error"}, "graincheck": {"high"}}
SOFT_RULES = {"UNDECLARED_JOIN", "UNKNOWN_GRAIN"}


def safe(fn, sql, facts):
    try:
        return fn(sql, facts), None
    except Exception as e:  # a tool crash is recorded, never counted as a pass or a flag
        return [], f"{type(e).__name__}: {str(e)[:120]}"


# --------------------------------------------------------------------------- value check (Plumbline preview)
def missing_values(sql: str, facts: DbFacts) -> list[dict]:
    """String literals compared with = / IN to a column that never holds that exact value."""
    try:
        tree = sqlglot.parse_one(sql, read=DIALECT)
    except Exception:
        return []
    alias_to_table = {}
    for t in tree.find_all(exp.Table):
        alias_to_table[(t.alias_or_name).lower()] = t.name
    lower_tables = {t.lower(): t for t in facts.tables}

    def owners(col: exp.Column) -> list[str]:
        if col.table:
            t = alias_to_table.get(col.table.lower()) or col.table
            t = lower_tables.get(t.lower())
            return [t] if t else []
        used = [lower_tables[n.lower()] for n in alias_to_table.values() if n.lower() in lower_tables]
        return [t for t in dict.fromkeys(used)
                if any(c.lower() == col.name.lower() for c, _ in facts.columns[t])]

    pairs = []
    for node in tree.find_all(exp.EQ, exp.In):
        col = node.this if isinstance(node.this, exp.Column) else None
        if isinstance(node, exp.EQ):
            lits = [node.expression]
            if col is None and isinstance(node.expression, exp.Column):
                col, lits = node.expression, [node.this]
        else:
            lits = node.expressions
        if col is None:
            continue
        for lit in lits:
            if isinstance(lit, exp.Literal) and lit.is_string:
                pairs.append((col, lit.this))

    out, cur = [], facts.con.cursor()
    for col, value in pairs:
        tabs = owners(col)
        if not tabs:
            continue
        found = case_only = False
        for t in tabs:
            real = next((c for c, _ in facts.columns[t] if c.lower() == col.name.lower()), None)
            if real is None:   # alias of a CTE/subquery column: can't resolve, so don't judge
                found = True
                break
            q = f'SELECT 1 FROM "{t}" WHERE "{real}" = ? LIMIT 1'
            if cur.execute(q, (value,)).fetchone():
                found = True
                break
            q = f'SELECT 1 FROM "{t}" WHERE LOWER(TRIM("{real}")) = LOWER(TRIM(?)) LIMIT 1'
            if cur.execute(q, (value,)).fetchone():
                case_only = True
        if not found:
            out.append({"column": col.sql(), "value": value,
                        "kind": "case_or_space_mismatch" if case_only else "absent"})
    return out


# --------------------------------------------------------------------------- main
def main(run_id: str) -> int:
    Q = {q["question_id"]: q for q in map(json.loads, QUESTIONS.open(encoding="utf-8"))}
    run_dir = ROOT / "bench" / "runs" / run_id
    P = {p["question_id"]: p for p in map(json.loads, (run_dir / "predictions.jsonl").open(encoding="utf-8"))}
    L = {l["question_id"]: l for l in map(json.loads, (run_dir / "labels.jsonl").open(encoding="utf-8"))}

    dbs: dict[str, DbFacts] = {}
    for db_id in sorted({q["db_id"] for q in Q.values()}):
        f = DbFacts(find_db(db_id))
        f._sqlsure, f._grainguard, f._graincheck = f.sqlsure_model(), f.grainguard_facts(), f.graincheck_uniques()
        dbs[db_id] = f
    key_cov = {d: {"tables": len(f.tables), "with_key": sum(bool(k) for k in f.keys.values()),
                   "fks": len(f.fks)} for d, f in dbs.items()}

    out_dir = ROOT / "compare" / "results" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for qid in sorted(Q):
        facts = dbs[Q[qid]["db_id"]]
        items = [("gold", Q[qid]["SQL"], "correct")]
        if qid in P and P[qid].get("predicted_sql"):
            items.append(("pred", P[qid]["predicted_sql"], L[qid]["label"]))
        for kind, sql, label in items:
            row = {"question_id": qid, "db_id": Q[qid]["db_id"], "kind": kind, "label": label}
            for name, fn in TOOLS.items():
                row[name], row[name + "_crash"] = safe(fn, sql, facts)
            row["missing_values"] = missing_values(sql, facts)
            rows.append(row)

    with (out_dir / "per_query.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    def flagged(r, tool, strong):
        fs = [f for f in r[tool] if f["rule"] not in SOFT_RULES]
        return any(f["severity"] in STRONG[tool] for f in fs) if strong else bool(fs)

    groups = {
        "pred_wrong_result": [r for r in rows if r["kind"] == "pred" and r["label"] == "wrong_result"],
        "pred_correct": [r for r in rows if r["kind"] == "pred" and r["label"] == "correct"],
        "gold": [r for r in rows if r["kind"] == "gold"],
    }
    summary = {"run_id": run_id, "key_coverage": key_cov, "tools": {}, "value_check": {}}
    for tool in TOOLS:
        t = {}
        for g, rs in groups.items():
            n = len(rs)
            t[g] = {"n": n,
                    "flagged_any": sum(flagged(r, tool, False) for r in rs),
                    "flagged_strong": sum(flagged(r, tool, True) for r in rs),
                    "crashed": sum(bool(r[tool + "_crash"]) for r in rs)}
        t["rules_on_wrong_result"] = dict(collections.Counter(
            f["rule"] for r in groups["pred_wrong_result"] for f in r[tool]).most_common())
        summary["tools"][tool] = t
    any_tool = lambda r: any(flagged(r, tool, False) for tool in TOOLS)
    summary["any_tool_flags_wrong_result"] = sum(map(any_tool, groups["pred_wrong_result"]))
    for g, rs in groups.items():
        kinds = collections.Counter(m["kind"] for r in rs for m in r["missing_values"])
        summary["value_check"][g] = {"n": len(rs),
                                     "queries_with_missing_value": sum(bool(r["missing_values"]) for r in rs),
                                     "by_kind": dict(kinds)}
    wr = groups["pred_wrong_result"]
    summary["value_vs_tools_on_wrong_result"] = {
        "value_flag_only": sum(bool(r["missing_values"]) and not any_tool(r) for r in wr),
        "tool_flag_only": sum(any_tool(r) and not r["missing_values"] for r in wr),
        "both": sum(any_tool(r) and bool(r["missing_values"]) for r in wr),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "qwen7b-r1"))
