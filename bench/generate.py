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

Re-doing cut-off answers (added for thinking models such as qwen3.5:4b, whose
reasoning used up the 1,024-token answer limit and left 105 answers empty):

    python -m bench.generate --run-id qwen35-4b-r1 --redo-truncated \\
        --num-predict 4096 --num-ctx 10240

Only answers marked `truncated` are generated again, with the bigger limit; every other
answer is kept. That is fair because an answer that finished under the old limit would
come out the same under a bigger one (temperature 0). The context grows too, so that the
longest prompt (5,515 tokens) plus 4,096 answer tokens still fits. Progress goes to
redo.jsonl (resumable with the same command); the original predictions are kept in
predictions.before_redo.jsonl, and run.json records what was redone and with what.
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
    parser.add_argument("--redo-truncated", action="store_true",
                        help="re-generate only the answers that hit the token limit")
    parser.add_argument("--num-predict", type=int, help="answer token limit (redo only)")
    parser.add_argument("--num-ctx", type=int, help="context window in tokens (redo only)")
    args = parser.parse_args(argv)

    if args.redo_truncated:
        return redo_truncated(args)
    if args.num_predict or args.num_ctx:
        parser.error("--num-predict / --num-ctx are only for --redo-truncated; "
                     "a new run uses config.DEFAULT_OPTIONS")

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
            record = _record(q, gen)
            out.write(json.dumps(record) + "\n")
            out.flush()

            elapsed = time.perf_counter() - start
            eta_min = elapsed / i * (len(todo) - i) / 60
            print(f"[{i}/{len(todo)}] q{q.question_id} {gen.seconds:5.1f}s "
                  f"{'(no SQL found)' if record['predicted_sql'] is None else ''}"
                  f"  ETA {eta_min:.0f} min", flush=True)

    print(f"Done. Next: python -m bench.score {args.run_id}")
    return 0


def _record(q, gen) -> dict:
    return {
        "question_id": q.question_id,
        "db_id": q.db_id,
        "difficulty": q.difficulty,
        "predicted_sql": extract_sql(gen.text),
        "raw_answer": gen.text,
        "prompt_tokens": gen.prompt_tokens,
        "output_tokens": gen.output_tokens,
        "gen_seconds": round(gen.seconds, 2),
        "truncated": gen.truncated,
        "thinking_chars": gen.thinking_chars,
    }


def redo_truncated(args) -> int:
    """Re-generate only the cut-off answers of an existing run, with a bigger limit."""
    run_dir = config.RUNS_DIR / args.run_id
    preds_path = run_dir / "predictions.jsonl"
    backup = run_dir / "predictions.before_redo.jsonl"
    redo_path = run_dir / "redo.jsonl"
    meta_path = run_dir / "run.json"
    if not preds_path.exists():
        print(f"No predictions for {args.run_id}.", file=sys.stderr)
        return 2
    if not args.num_predict:
        print("Give --num-predict (e.g. 4096).", file=sys.stderr)
        return 2
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    # Same model, same seed and temperature as the original run; only the limits change.
    options = {**meta["options"], "num_predict": args.num_predict}
    if args.num_ctx:
        options["num_ctx"] = args.num_ctx

    # The ORIGINAL predictions decide what to redo, so re-running this command never
    # re-targets an answer that was already redone (even if it is still too long).
    if not backup.exists():
        backup.write_bytes(preds_path.read_bytes())
    original = [json.loads(line) for line in backup.open(encoding="utf-8") if line.strip()]
    targets = [r["question_id"] for r in original if r["truncated"]]
    done = _done_ids(redo_path)
    by_id = {q.question_id: q for q in load_questions()}
    todo = [by_id[qid] for qid in targets if qid not in done]
    print(f"{len(targets)} cut-off answers; {len(done)} already redone, {len(todo)} to go "
          f"(num_predict={options['num_predict']}, num_ctx={options['num_ctx']})")

    start = time.perf_counter()
    with redo_path.open("a", encoding="utf-8") as out:
        for i, q in enumerate(todo, 1):
            gen = generate(q.prompt, model=meta["model"], options=options)
            rec = _record(q, gen)
            out.write(json.dumps(rec) + "\n")
            out.flush()
            elapsed = time.perf_counter() - start
            eta_min = elapsed / i * (len(todo) - i) / 60
            note = ("(no SQL found)" if rec["predicted_sql"] is None else
                    "(still cut off)" if rec["truncated"] else "")
            print(f"[{i}/{len(todo)}] q{q.question_id} {gen.seconds:5.1f}s "
                  f"{gen.output_tokens} tokens {note}  ETA {eta_min:.0f} min", flush=True)

    # Merge: original answers, with every redone one replaced.
    redone = {r["question_id"]: r for r in map(json.loads, redo_path.open(encoding="utf-8"))}
    merged = [redone.get(r["question_id"], r) for r in original]
    preds_path.write_text("".join(json.dumps(r) + "\n" for r in merged), encoding="utf-8")
    meta["redo_truncated"] = {
        "question_ids": sorted(redone),
        "options": options,
        "still_truncated": sorted(q for q, r in redone.items() if r["truncated"]),
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    still = len(meta["redo_truncated"]["still_truncated"])
    print(f"Merged {len(redone)} redone answers into predictions.jsonl "
          f"({still} still hit the limit). Next: python -m bench.score {args.run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
