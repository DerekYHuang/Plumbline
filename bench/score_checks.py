"""M3: score Plumbline's checks against the M0 labels.

    python -m bench.score_checks qwen7b-r1

For every question it checks two queries -- the supplied (gold) answer and the model's
answer -- and compares the verdict with what actually happened when the query ran (the
label from `bench.score`). That gives the blueprint's metrics:

    detection          share of the model's silent wrong answers (wrong_result) that
                       Plumbline fails BEFORE they run.         target: 40% or more
    false alarms       share of correct queries it fails: the 500 gold queries, and the
                       model's own correct answers.             target: 5% or less
    wasted executions  share of the model's crashing answers (exec_error, timeout)
    avoided            stopped before they ran.                 report, no target
    latency            time per check, median and 95th pct.     target: under 50 ms p95

Each rate comes with a 95% interval (bench/stats.py). Detection is also broken down by
rule family, difficulty and database, and reported for Plumbline's own rules alone
(schema + value) versus the grainguard fan-out adapter, so one can't hide the other.

Only error-level findings count as "failing a query"; warnings are reported separately.

Outputs:
    bench/runs/<run_id>/checks.jsonl              one row per checked query (gitignored)
    results/<date>_<run_id>_checks_summary.json   the summary (committed)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import date

from bench import config
from bench.dataset import find_database, load_questions
from bench.stats import fmt, rate
from plumbline import Catalog, check_sql
from plumbline.profiler import profile_database

CATALOGS_DIR = config.REPO_ROOT / "data" / "catalogs"
FANOUT_RULES = {"FANOUT", "CHASM"}


def family(rule: str) -> str:
    if rule in FANOUT_RULES:
        return "fanout"
    return "schema" if rule.startswith("SCHEMA") else "value"


def load_catalog(db_id: str) -> Catalog:
    path = CATALOGS_DIR / f"{db_id}.json"
    if path.exists():
        return Catalog.load(path)
    print(f"  (profiling {db_id}: no catalog yet)")
    cat = profile_database(find_database(db_id))
    cat.save(path)
    return cat


def check_one(sql: str, cat: Catalog) -> dict:
    t = time.perf_counter()
    r = check_sql(sql, cat)
    ms = (time.perf_counter() - t) * 1000
    return {
        "passed": r.passed,
        "parse_error": r.parse_error,
        "errors": [f.rule for f in r.errors],
        "warnings": [f.rule for f in r.warnings],
        "messages": [f"{f.rule}: {f.message}" for f in r.errors],
        "ms": round(ms, 2),
    }


def flagged(row: dict, families: set[str] | None = None) -> bool:
    """Did Plumbline fail this query (optionally counting only some rule families)?"""
    if families is None:
        return not row["passed"]
    return any(family(r) in families for r in row["errors"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("run_id")
    args = ap.parse_args(argv)

    run_dir = config.RUNS_DIR / args.run_id
    labels_path = run_dir / "labels.jsonl"
    if not labels_path.exists():
        print(f"No labels for {args.run_id}. Run `python -m bench.score {args.run_id}` first.")
        return 2
    labels = {r["question_id"]: r for r in map(json.loads, labels_path.open(encoding="utf-8"))}
    questions = {q.question_id: q for q in load_questions()}
    catalogs: dict[str, Catalog] = {}

    rows = []
    for qid in sorted(labels):
        q, lab = questions[qid], labels[qid]
        cat = catalogs.get(q.db_id) or catalogs.setdefault(q.db_id, load_catalog(q.db_id))
        rows.append(
            {
                "question_id": qid,
                "db_id": q.db_id,
                "difficulty": q.difficulty,
                "kind": "gold",
                "label": "correct",
                **check_one(q.gold_sql, cat),
            }
        )
        if lab.get("predicted_sql"):
            rows.append(
                {
                    "question_id": qid,
                    "db_id": q.db_id,
                    "difficulty": q.difficulty,
                    "kind": "pred",
                    "label": lab["label"],
                    **check_one(lab["predicted_sql"], cat),
                }
            )

    with (run_dir / "checks.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    gold = [r for r in rows if r["kind"] == "gold"]
    pred = [r for r in rows if r["kind"] == "pred"]
    silent = [r for r in pred if r["label"] == "wrong_result"]
    crashed = [r for r in pred if r["label"] in ("exec_error", "timeout")]
    pred_ok = [r for r in pred if r["label"] == "correct"]

    def det(rs, fams=None):
        return rate(sum(flagged(r, fams) for r in rs), len(rs))

    by = defaultdict(list)
    for r in silent:
        by[("difficulty", r["difficulty"])].append(r)
        by[("db", r["db_id"])].append(r)

    times = sorted(r["ms"] for r in rows)
    pred_flagged = [r for r in pred if flagged(r)]
    summary = {
        "run_id": args.run_id,
        "date": date.today().isoformat(),
        "counts": {
            "gold": len(gold),
            "silent_wrong": len(silent),
            "crashed": len(crashed),
            "pred_correct": len(pred_ok),
        },
        "detection_silent": {
            "all_rules": det(silent),
            "own_rules_schema_value": det(silent, {"schema", "value"}),
            "value_rules": det(silent, {"value"}),
            "schema_rules": det(silent, {"schema"}),
            "fanout_grainguard": det(silent, {"fanout"}),
            "own_rules_only_not_fanout": rate(
                sum(flagged(r, {"schema", "value"}) and not flagged(r, {"fanout"}) for r in silent),
                len(silent),
            ),
        },
        "detection_by_difficulty": {
            k[1]: det(v) for k, v in sorted(by.items()) if k[0] == "difficulty"
        },
        "detection_by_db": {k[1]: det(v) for k, v in sorted(by.items()) if k[0] == "db"},
        "false_alarms": {
            "gold_all_rules": det(gold),
            "gold_own_rules": det(gold, {"schema", "value"}),
            "gold_fanout": det(gold, {"fanout"}),
            "pred_correct_all_rules": det(pred_ok),
        },
        "wasted_executions_avoided": rate(
            sum(flagged(r) or bool(r["parse_error"]) for r in crashed), len(crashed)
        ),
        # Of the model answers Plumbline fails, how many really were wrong?
        "precision_on_pred": rate(
            sum(r["label"] != "correct" for r in pred_flagged), len(pred_flagged)
        ),
        "warnings": {
            "silent_with_warning": sum(bool(r["warnings"]) for r in silent),
            "gold_with_warning": sum(bool(r["warnings"]) for r in gold),
        },
        "rules_firing_on_silent": dict(
            Counter(x for r in silent for x in r["errors"]).most_common()
        ),
        "latency_ms": {
            "median": round(statistics.median(times), 2),
            "p95": round(times[int(0.95 * (len(times) - 1))], 2),
            "max": round(times[-1], 2),
            "n": len(times),
        },
        "gold_flagged": [
            {"question_id": r["question_id"], "errors": r["errors"]} for r in gold if flagged(r)
        ],
    }
    out = config.RESULTS_DIR / f"{summary['date']}_{args.run_id}_checks_summary.json"
    out.write_text(json.dumps(summary, indent=1), encoding="utf-8")

    d, fa = summary["detection_silent"], summary["false_alarms"]
    lat = summary["latency_ms"]
    print(f"Plumbline checks on {args.run_id}\n")
    print(f"  Detection, silent wrong answers   {fmt(d['all_rules'])}")
    print(f"    own rules (schema + value)      {fmt(d['own_rules_schema_value'])}")
    print(f"    fan-out (grainguard)            {fmt(d['fanout_grainguard'])}")
    print(f"  False alarms, gold queries        {fmt(fa['gold_all_rules'])}")
    print(f"    own rules only                  {fmt(fa['gold_own_rules'])}")
    print(f"  False alarms, model's correct     {fmt(fa['pred_correct_all_rules'])}")
    print(f"  Crashing answers stopped early    {fmt(summary['wasted_executions_avoided'])}")
    print(f"  Precision on model answers        {fmt(summary['precision_on_pred'])}")
    print(f"  Latency  median {lat['median']} ms   p95 {lat['p95']} ms   max {lat['max']} ms")
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
