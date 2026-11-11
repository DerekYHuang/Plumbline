"""M0 step 3: execute each predicted query, compare with gold, label it.

Every question gets exactly one label:

    correct       ran, and the result set equals the gold result set (BIRD EX)
    wrong_result  ran fine but returned a different answer   <-- Plumbline's target
    exec_error    the database rejected it (bad column, syntax error, ...)
    timeout       ran longer than config.SQL_TIMEOUT_S
    no_sql        we couldn't find any SQL in the model's answer
    gold_error    the GOLD query failed -- a data/benchmark problem, excluded
                  from accuracy and investigated separately

Why split "wrong" into four kinds? Because they are different problems.
exec_error is caught by the database itself. wrong_result is the silent one:
it runs, returns something plausible, and nobody notices. That set (plus the
correct set, for false alarms) is what Plumbline's rules get scored against
in M3.

Writes into the run folder:
    labels.jsonl    one row per question: label, predicted + gold SQL, error,
                    row counts, timings
    summary.json    accuracy overall and by difficulty, label counts

Usage:
    python -m bench.score qwen7b-r1
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict

from bench import config
from bench.dataset import find_database, load_questions
from bench.execute import ExecResult, results_match, run_sql

LABELS = ["correct", "wrong_result", "exec_error", "timeout", "no_sql", "gold_error"]


def label_for(predicted_sql: str | None, pred: ExecResult | None, gold: ExecResult) -> str:
    """Pure decision logic, separated out so it's easy to unit-test."""
    if not gold.ok:
        return "gold_error"
    if predicted_sql is None or pred is None:
        return "no_sql"
    if pred.timed_out:
        return "timeout"
    if not pred.ok:
        return "exec_error"
    return "correct" if results_match(pred.rows, gold.rows) else "wrong_result"


def summarize(labels: list[dict]) -> dict:
    """Accuracy = correct / (all questions except gold_error)."""
    scored = [r for r in labels if r["label"] != "gold_error"]
    by_diff: dict[str, list[dict]] = defaultdict(list)
    for r in scored:
        by_diff[r["difficulty"]].append(r)

    def acc(rows):
        if not rows:
            return None
        return round(100 * sum(r["label"] == "correct" for r in rows) / len(rows), 2)

    return {
        "n_questions": len(labels),
        "n_scored": len(scored),
        "accuracy_pct": acc(scored),
        "accuracy_by_difficulty_pct": {d: acc(rows) for d, rows in sorted(by_diff.items())},
        "label_counts": {lab: Counter(r["label"] for r in labels)[lab] for lab in LABELS},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id")
    args = parser.parse_args(argv)

    run_dir = config.RUNS_DIR / args.run_id
    preds_path = run_dir / "predictions.jsonl"
    if not preds_path.exists():
        print(f"No predictions at {preds_path}", file=sys.stderr)
        return 2

    gold_by_id = {q.question_id: q for q in load_questions()}
    with preds_path.open(encoding="utf-8") as f:
        preds = [json.loads(line) for line in f if line.strip()]

    labels = []
    for p in sorted(preds, key=lambda r: r["question_id"]):
        q = gold_by_id[p["question_id"]]
        db = find_database(q.db_id)
        gold = run_sql(db, q.gold_sql, config.SQL_TIMEOUT_S)
        sql = p["predicted_sql"]
        pred = run_sql(db, sql, config.SQL_TIMEOUT_S) if sql else None
        lab = label_for(sql, pred, gold)
        labels.append({
            "question_id": q.question_id,
            "db_id": q.db_id,
            "difficulty": q.difficulty,
            "label": lab,
            "question": q.question,
            "predicted_sql": sql,
            "gold_sql": q.gold_sql,
            "error": (pred.error if pred and not pred.ok else None)
                     or (gold.error if not gold.ok else None),
            "pred_rows": len(pred.rows) if pred and pred.ok else None,
            "gold_rows": len(gold.rows) if gold.ok else None,
            "pred_exec_seconds": round(pred.seconds, 4) if pred else None,
        })
        print(f"q{q.question_id:<4} {lab}", flush=True)

    with (run_dir / "labels.jsonl").open("w", encoding="utf-8") as f:
        for row in labels:
            f.write(json.dumps(row) + "\n")

    summary = summarize(labels)
    meta_path = run_dir / "run.json"
    if meta_path.exists():
        summary["run"] = json.loads(meta_path.read_text(encoding="utf-8"))
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n" + json.dumps({k: v for k, v in summary.items() if k != "run"}, indent=2))
    if summary["label_counts"]["gold_error"]:
        print("WARNING: some gold queries failed -- run `python -m bench.verify_gold`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
