"""Reproduce the headline numbers from a fresh clone, with one command.

    python -m bench.reproduce                    # qwen2.5-coder:7b (the headline run)
    python -m bench.reproduce --run qwen35-4b-r1 # the held-out second model
    python -m bench.reproduce --all              # both

What it does, in order (about 25 minutes on a home PC, plus the one-off download):

    1. fetch_data     download BIRD Mini-Dev from its official sources, check SHA-256
    2. verify_gold    all 500 gold queries run on this copy
    3. profile_all    build the catalogs Plumbline checks against
    4. score          execute the committed model answers, label each one
    5. score_checks   run Plumbline on every answer and every gold query
    6. compare        the new numbers against the committed ones -> PASS / MISMATCH

What it does NOT do: ask the model again. Generating 500 answers takes about 4 hours on a
GPU, and the answers are the input to the experiment, not its result. So the exact answers
the published numbers came from are committed under results/predictions/<run>/ (with the
model digest and settings in run.json), and everything after generation is recomputed.
To regenerate them too: python -m bench.generate --run-id <new id> (needs Ollama).

Nothing here touches your own runs or the committed results: it works in
bench/runs/<run>-repro/ and writes its summaries to reproduce_out/ (both gitignored).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time

from bench import config

COMMITTED_RESULTS = config.REPO_ROOT / "results"
PREDICTIONS_DIR = COMMITTED_RESULTS / "predictions"
OUT_DIR = config.REPO_ROOT / "reproduce_out"
RUNS = ["qwen7b-r1", "qwen35-4b-r1"]


def latest(folder, pattern: str) -> dict:
    files = sorted(folder.glob(pattern))
    if not files:
        raise FileNotFoundError(f"{folder.name}/{pattern}")
    return json.loads(files[-1].read_text(encoding="utf-8"))


def headline(model: dict, checks: dict) -> dict:
    """The numbers that must match: model labels and Plumbline's verdict counts."""
    d, fa = checks["detection_silent"], checks["false_alarms"]
    lc = model["label_counts"]
    return {
        "model correct": lc["correct"],
        "model silent wrong": lc["wrong_result"],
        "model crashed or timed out": lc["exec_error"] + lc["timeout"],
        "silent errors caught": d["all_rules"]["k"],
        "caught by own rules": d["own_rules_schema_value"]["k"],
        "gold false alarms": fa["gold_all_rules"]["k"],
        "gold false alarms, own rules": fa["gold_own_rules"]["k"],
    }


def committed(run: str) -> dict:
    return headline(latest(COMMITTED_RESULTS, f"*_{run}_summary.json"),
                    latest(COMMITTED_RESULTS, f"*_{run}_checks_summary.json"))


def step(title: str, fn, *args) -> None:
    print(f"\n=== {title} " + "=" * max(0, 66 - len(title)), flush=True)
    t = time.perf_counter()
    code = fn(*args)
    print(f"--- {'ok' if code == 0 else f'FAILED (exit {code})'} "
          f"in {time.perf_counter() - t:.0f}s", flush=True)
    if code != 0:
        raise SystemExit(f"Stopped: '{title}' failed.")


def reproduce(run: str) -> bool:
    from bench import score, score_checks

    want = committed(run)
    work = config.RUNS_DIR / f"{run}-repro"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    for name in ("predictions.jsonl", "run.json"):
        shutil.copy(PREDICTIONS_DIR / run / name, work / name)

    step(f"score {run}: execute and label the committed answers", score.main, [work.name])
    step(f"score_checks {run}: run Plumbline on every query", score_checks.main, [work.name])
    model = json.loads((work / "summary.json").read_text(encoding="utf-8"))
    got = headline(model, latest(OUT_DIR, f"*_{work.name}_checks_summary.json"))

    print(f"\n{run}: committed vs reproduced")
    ok = True
    for key, w in want.items():
        g = got[key]
        ok &= g == w
        print(f"  {'PASS' if g == w else 'MISMATCH':9s} {key:30s} committed {w:>4}   "
              f"reproduced {g:>4}")
    lc = model["label_counts"]
    if not ok and (lc.get("timeout") or lc.get("gold_error")):
        print("  note: timeouts depend on machine speed (a busy PC can push a slow query past "
              "60 s). Re-run on an idle machine before reading this as a real difference.")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", choices=RUNS, default=RUNS[0])
    ap.add_argument("--all", action="store_true", help="reproduce every committed run")
    ap.add_argument("--skip-gold-check", action="store_true",
                    help="skip step 2 (saves ~4 minutes; the data hashes already match)")
    args = ap.parse_args(argv)
    runs = RUNS if args.all else [args.run]
    for run in runs:
        committed(run)  # fail in seconds, not after 20 minutes, if a result file is missing
    t0 = time.perf_counter()

    from bench import fetch_data, profile_all, verify_gold

    step("fetch_data: download and verify BIRD Mini-Dev", fetch_data.main, [])
    if not args.skip_gold_check:
        step("verify_gold: all 500 gold queries run", verify_gold.main, [])

    # Every summary written from here on goes to reproduce_out/, never over results/.
    OUT_DIR.mkdir(exist_ok=True)
    config.RESULTS_DIR = OUT_DIR
    try:
        step("profile_all: build the catalogs", profile_all.main)
        outcome = {run: reproduce(run) for run in runs}
    finally:
        config.RESULTS_DIR = COMMITTED_RESULTS

    print("\n" + "=" * 70)
    for run, ok in outcome.items():
        print(f"{'PASS' if ok else 'MISMATCH':9s} {run}")
    print(f"Total {(time.perf_counter() - t0) / 60:.0f} min. Details: {OUT_DIR.name}/, "
          "bench/runs/*-repro/")
    return 0 if all(outcome.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
