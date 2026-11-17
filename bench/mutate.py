"""M3: a mutation test set -- inject one known fault into each correct query.

    python -m bench.mutate build    # create + execute mutants (minutes; runs every query)
    python -m bench.mutate score    # run Plumbline on them (seconds)

Why: scoring on the model's mistakes (bench.score_checks) has two weaknesses.
1. We don't know *why* each wrong answer is wrong, so we can't say which fault types
   Plumbline covers.
2. The rules were developed while looking at those same mistakes (see the q496 note in
   docs/findings-log.md), which can flatter the numbers.

A mutation set fixes both. Each mutant is a correct (gold) query with exactly one fault of
a known type. The fault is applied mechanically, by code written without reference to the
rules. Then every mutant is EXECUTED: it's kept only if it crashes or returns a different
answer than the original. A mutant that still returns the right answer ("equivalent
mutant") is discarded, so every kept mutant is a real error.

Fault types (one per mutant, applied to the first place it fits):

    case_flip      a filter value with different capitalisation   'Locally funded' -> 'Locally
                                                                  Funded'
    value_typo     a filter value with one character dropped       'Directly' -> 'Dirctly'
    leading_zero   a zero-padded code with the zeros stripped      '0123' -> '123'
    column_typo    a column name with its last letter dropped      County -> Count
    table_typo     a table name pluralised / singularised          schools -> school
    drop_distinct  COUNT(DISTINCT x) -> COUNT(x) in a query with a join (fan-out)

Honest limits: the operators were written by the same person as the rules, so they
reflect what we expected models to get wrong. graincheck's README makes the same caveat
about its own benchmark. Fault types we know Plumbline can't see (wrong column chosen,
wrong aggregate, missing condition) are deliberately absent: this set measures *coverage
of the targeted fault types*, not overall detection -- that is bench.score_checks' job.

Files: bench/runs/mutants/mutants.jsonl (gitignored), results/<date>_mutation_summary.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date

import sqlglot
from sqlglot import exp

from bench import config
from bench.dataset import find_database, load_questions
from bench.execute import results_match, run_sql
from bench.stats import fmt, rate

OUT_DIR = config.RUNS_DIR / "mutants"
MUTANTS = OUT_DIR / "mutants.jsonl"
DIALECT = "sqlite"
TIMEOUT_S = 30.0


# ------------------------------------------------------------------ fault operators
# Each takes a fresh copy of the parsed gold query and returns (mutated tree, description)
# or None when the fault doesn't apply to this query.


def _filter_literals(tree: exp.Expression):
    """String literals compared with a column by = or IN, in query order."""
    for node in tree.find_all(exp.EQ, exp.In):
        if isinstance(node, exp.In):
            if isinstance(node.this, exp.Column):
                yield from (
                    e for e in node.expressions if isinstance(e, exp.Literal) and e.is_string
                )
            continue
        a, b = node.this, node.expression
        if isinstance(a, exp.Column) and isinstance(b, exp.Literal) and b.is_string:
            yield b
        elif isinstance(b, exp.Column) and isinstance(a, exp.Literal) and a.is_string:
            yield a


def case_flip(tree):
    for lit in _filter_literals(tree):
        v = lit.this
        new = v.title() if v.title() != v else v.lower()
        if new != v and any(c.isalpha() for c in v):
            lit.replace(exp.Literal.string(new))
            return tree, f"{v!r} -> {new!r}"
    return None


def value_typo(tree):
    for lit in _filter_literals(tree):
        v = lit.this
        if len(v) >= 4 and any(c.isalpha() for c in v) and not v.isdigit():
            i = len(v) // 2
            new = v[:i] + v[i + 1 :]
            lit.replace(exp.Literal.string(new))
            return tree, f"{v!r} -> {new!r}"
    return None


def leading_zero(tree):
    for lit in _filter_literals(tree):
        v = lit.this
        if v.isdigit() and len(v) >= 2 and v.startswith("0") and v.lstrip("0"):
            new = v.lstrip("0")
            lit.replace(exp.Literal.string(new))
            return tree, f"{v!r} -> {new!r}"
    return None


def column_typo(tree):
    where = tree.find(exp.Where)
    scope = where if where is not None else tree
    for col in scope.find_all(exp.Column):
        name = col.name
        if len(name) >= 4 and name.isascii():
            col.set("this", exp.to_identifier(name[:-1], quoted=True))
            return tree, f"{name} -> {name[:-1]}"
    return None


def table_typo(tree):
    for t in tree.find_all(exp.Table):
        name = t.name
        if len(name) >= 4 and name.isascii():
            new = name[:-1] if name.endswith("s") else name + "s"
            t.set("this", exp.to_identifier(new, quoted=True))
            return tree, f"{name} -> {new}"
    return None


def drop_distinct(tree):
    if not tree.find(exp.Join):
        return None
    for count in tree.find_all(exp.Count):
        d = count.this
        if isinstance(d, exp.Distinct) and len(d.expressions) == 1:
            count.set("this", d.expressions[0])
            return tree, "COUNT(DISTINCT x) -> COUNT(x)"
    return None


OPERATORS = {
    f.__name__: f
    for f in (case_flip, value_typo, leading_zero, column_typo, table_typo, drop_distinct)
}


# ------------------------------------------------------------------ build
def build(resume: bool = False) -> int:
    """Create and execute mutants. Each question ends with a `_status` row, so with
    --resume a crashed or stopped build continues after the last finished question."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    questions = load_questions()
    stats = Counter()
    done: set[int] = set()
    if resume and MUTANTS.exists():
        rows = [json.loads(line) for line in MUTANTS.open(encoding="utf-8") if line.strip()]
        done = {r["question_id"] for r in rows if r["operator"] == "_status"}
        # Drop mutants of a question that was interrupted halfway (no _status row).
        rows = [r for r in rows if r["question_id"] in done]
        MUTANTS.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        print(f"resuming: {len(done)} questions already done", flush=True)
    with MUTANTS.open("a" if resume else "w", encoding="utf-8") as out:

        def emit(row: dict) -> None:
            out.write(json.dumps(row) + "\n")
            out.flush()

        for i, q in enumerate(questions):
            if q.question_id in done:
                continue
            print(f"q{q.question_id} ({i + 1}/{len(questions)})", flush=True)
            status = {"question_id": q.question_id, "db_id": q.db_id, "operator": "_status"}
            db = find_database(q.db_id)
            try:
                tree = sqlglot.parse_one(q.gold_sql, read=DIALECT)
            except Exception:
                stats["gold_unparseable"] += 1
                emit({**status, "outcome": "gold_unparseable"})
                continue
            gold = run_sql(db, q.gold_sql, timeout_s=60)
            # Mutants are written back out by SQLGlot, so first make sure SQLGlot's
            # rewrite of the UNMUTATED gold query gives the same answer. Otherwise a
            # difference could come from the rewrite, not from the injected fault.
            roundtrip = tree.sql(dialect=DIALECT)
            rt = run_sql(db, roundtrip, timeout_s=60)
            if not (gold.ok and rt.ok and results_match(rt.rows, gold.rows)):
                stats["roundtrip_mismatch"] += 1
                emit({**status, "outcome": "roundtrip_mismatch"})
                continue
            for name, op in OPERATORS.items():
                res = op(tree.copy())
                if res is None:
                    continue
                mutated, desc = res
                sql = mutated.sql(dialect=DIALECT)
                ex = run_sql(db, sql, timeout_s=TIMEOUT_S)
                if ex.timed_out:
                    outcome = "timeout"
                elif not ex.ok:
                    outcome = "crash"
                elif results_match(ex.rows, gold.rows):
                    outcome = "equivalent"  # fault didn't change the answer: discarded
                else:
                    outcome = "silent"
                stats[f"{name}:{outcome}"] += 1
                print(f"   {name}: {outcome} ({ex.seconds:.1f}s)", flush=True)
                emit(
                    {
                        "question_id": q.question_id,
                        "db_id": q.db_id,
                        "operator": name,
                        "change": desc,
                        "outcome": outcome,
                        "sql": sql,
                        "error": ex.error if not ex.ok else None,
                    }
                )
            emit({**status, "outcome": "ok"})
    print(json.dumps(dict(sorted(stats.items())), indent=1))
    print(f"-> {MUTANTS}")
    return 0


# ------------------------------------------------------------------ score
def score() -> int:
    from bench.score_checks import family, load_catalog
    from plumbline import check_sql

    rows = [json.loads(line) for line in MUTANTS.open(encoding="utf-8")]
    kept = [r for r in rows if r["outcome"] in ("silent", "crash")]
    catalogs = {}
    per = defaultdict(lambda: defaultdict(list))
    for r in kept:
        cat = catalogs.get(r["db_id"]) or catalogs.setdefault(r["db_id"], load_catalog(r["db_id"]))
        res = check_sql(r["sql"], cat)
        caught = not res.passed
        fams = sorted({family(f.rule) for f in res.errors})
        r.update(caught=caught, rules=[f.rule for f in res.errors], families=fams)
        per[r["operator"]][r["outcome"]].append(r)

    summary = {
        "date": date.today().isoformat(),
        "operators": {},
        "discarded_equivalent": sum(r["outcome"] == "equivalent" for r in rows),
        "discarded_timeout": sum(r["outcome"] == "timeout" for r in rows),
        "questions": dict(Counter(r["outcome"] for r in rows if r["operator"] == "_status")),
    }
    print(f"{'fault type':15s} {'silent mutants caught':32s} {'crashing mutants caught':32s}")
    for op in OPERATORS:
        d = {}
        for outcome in ("silent", "crash"):
            rs = per[op][outcome]
            d[outcome] = {
                **rate(sum(r["caught"] for r in rs), len(rs)),
                "rules": dict(Counter(x for r in rs for x in r["rules"]).most_common()),
            }
        summary["operators"][op] = d
        print(f"{op:15s} {fmt(d['silent']):32s} {fmt(d['crash']):32s}")
    all_silent = [r for op in per.values() for r in op["silent"]]
    summary["all_silent"] = rate(sum(r["caught"] for r in all_silent), len(all_silent))
    print(f"{'ALL silent':15s} {fmt(summary['all_silent'])}")
    summary["missed_examples"] = [
        {k: r[k] for k in ("question_id", "operator", "change", "outcome")}
        for r in kept
        if not r["caught"]
    ][:40]
    out = config.RESULTS_DIR / f"{summary['date']}_mutation_summary.json"
    out.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"-> {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["build", "score"])
    ap.add_argument("--resume", action="store_true", help="build: continue a stopped build")
    args = ap.parse_args(argv)
    return build(args.resume) if args.step == "build" else score()


if __name__ == "__main__":
    sys.exit(main())
