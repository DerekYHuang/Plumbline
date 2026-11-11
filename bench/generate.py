"""M0 step 2: have the model write SQL for every BIRD Mini-Dev question.

For each question we send BIRD's official prompt to the local model, pull the
SQL out of the answer, and append one JSON line to

    bench/runs/<run_id>/predictions.jsonl

alongside a `run.json` describing exactly what ran (model tag + digest,
options, timestamp). Nothing is executed or scored here -- that's score.py.
Keeping generation and scoring separate means you can re-score old runs
(e.g. after fixing a bug in scoring) without paying for generation again.

Resumable: if the run folder already has predictions, finished question IDs
are skipped. A 500-question run on a GTX 1060 takes hours, so you can stop it
(Ctrl+C) and pick up later with the same --run-id.

Usage (from the repo root):

    python -m bench.generate --run-id qwen7b-r1
    python -m bench.generate --run-id smoke --limit 20
    python -m bench.generate --run-id qwen7b-r2 --seed 43   # 2nd of 3 runs
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime

from bench import config
from bench.dataset import load_questions
from bench.extract import extract_sql
from bench.ollama_client import generate, model_digest


def _done_ids(path) -> set[int]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as f:
        return {json.loads(line)["question_id"] for line in f if line.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True, help="name of the output folder")
    parser.add_argument("--model", default=config.DEFAULT_MODEL)
    parser.add_argument("--seed", type=int, default=config.DEFAULT_OPTIONS["seed"])
    parser.add_argument("--limit", type=int, help="only the first N questions")
    args = parser.parse_args(argv)

    run_dir = config.RUNS_DIR / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    preds_path = run_dir / "predictions.jsonl"
    options = {**config.DEFAULT_OPTIONS, "seed": args.seed}

    # Record run metadata once, at the start. If it already exists (resuming),
    # refuse to continue with different settings -- that would mix two runs.
    meta = {
        "model": args.model,
        "model_digest": model_digest(args.model),
        "options": options,
        "questions_file": str(config.QUESTIONS_FILE),
    }
    meta_path = run_dir / "run.json"
    if meta_path.exists():
        old = json.loads(meta_path.read_text(encoding="utf-8"))
        old_core = {k: old.get(k) for k in meta}
        if old_core != meta:
            print(f"Run {args.run_id} exists with different settings:\n{old_core}\n"
                  "Use a new --run-id.", file=sys.stderr)
            return 2
    else:
        meta["started_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    questions = load_questions()[: args.limit]
    done = _done_ids(preds_path)
    todo = [q for q in questions if q.question_id not in done]
    print(f"{len(done)} already done, {len(todo)} to go -> {preds_path}")

    start = time.perf_counter()
    # Append mode + flush after every line = a crash loses at most one answer.
    with preds_path.open("a", encoding="utf-8") as out:
        for i, q in enumerate(todo, 1):
            gen = generate(q.prompt, model=args.model, options=options)
            record = {
                "question_id": q.question_id,
                "db_id": q.db_id,
                "difficulty": q.difficulty,
                "predicted_sql": extract_sql(gen.text),
                "raw_answer": gen.text,
                "prompt_tokens": gen.prompt_tokens,
                "output_tokens": gen.output_tokens,
                "gen_seconds": round(gen.seconds, 2),
                "truncated": gen.truncated,
            }
            out.write(json.dumps(record) + "\n")
            out.flush()

            elapsed = time.perf_counter() - start
            eta_min = elapsed / i * (len(todo) - i) / 60
            print(f"[{i}/{len(todo)}] q{q.question_id} {gen.seconds:5.1f}s "
                  f"{'(no SQL found)' if record['predicted_sql'] is None else ''}"
                  f"  ETA {eta_min:.0f} min", flush=True)

    print(f"Done. Next: python -m bench.score {args.run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
