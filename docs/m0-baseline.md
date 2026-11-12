# M0: Baseline

**Goal:** before building any checks, find out how often the model under test gets BIRD
Mini-Dev wrong, and *how*. That gives us:

1. a **baseline accuracy** to improve on later (the repair loop in M5), and
2. a **labelled set of wrong queries**. The checks built in M2 are scored on whether they
   flag these (detection rate) without flagging the correct ones (false-alarm rate).

M0 ends with a **go/pivot decision** (see the bottom of this page).

## How the code fits together

```
bench/config.py         paths + model settings (env-var overridable)
bench/dataset.py        Question objects from the JSONL; finds <db_id>.sqlite
bench/execute.py        run_sql(): read-only, timeout, never raises
                        results_match(): BIRD's strict EX rule (set of row tuples)
bench/verify_gold.py    step 1  - do the 500 gold queries run on my data?
bench/ollama_client.py  generate(): one prompt -> one answer from local Ollama
bench/extract.py        extract_sql(): find the final SQL in the answer text
bench/generate.py       step 2  - model writes SQL for every question -> predictions.jsonl
bench/score.py          step 3  - execute + compare -> labels.jsonl, summary.json
```

Data flow for one question:

```
mini_dev_sqlite.jsonl --prompt--> Ollama --answer text--> extract_sql --SQL-->
    predictions.jsonl --run_sql on pred + gold--> results_match --> label
```

Design choices worth being able to defend in an interview:

- **Official prompt, official metric.** We send BIRD's own `prompt` field and score with
  BIRD's strict EX. That makes our baseline comparable to the dataset card's table
  instead of to a home-made setup.
- **Generation and scoring are separate steps.** A run takes hours. Scoring takes
  seconds and can be re-run after fixing a bug without regenerating.
- **Resumable, append-only output.** Each answer is flushed to disk as it arrives, so
  a crash or Ctrl+C loses at most one answer.
- **Read-only database plus a timeout.** Model-written SQL is untrusted: it could write
  (`DELETE ...`) or never finish (accidental cross join).
- **Wrong answers are split into kinds.** `exec_error` is caught by the database anyway.
  `wrong_result` is the silent kind and the set Plumbline exists to catch.

## Labels

| Label | Meaning |
|---|---|
| `correct` | Result set equals gold (order and duplicates ignored) |
| `wrong_result` | Ran fine and returned a different answer: **Plumbline's target** |
| `exec_error` | The database rejected it (unknown column, syntax error, ...) |
| `timeout` | Took longer than 60 s |
| `no_sql` | No SQL found in the model's answer |
| `gold_error` | The gold query failed: a data problem, excluded from accuracy |

## Runbook

```bash
python -m bench.verify_gold                 # expect PASS; anything else = fix the data setup
python -m bench.generate --run-id smoke --limit 20
python -m bench.score smoke                 # sanity-check labels by reading a few
python -m bench.generate --run-id qwen7b-r1 # full 500
python -m bench.score qwen7b-r1
```

The blueprint asks for **three runs** so we can report a spread, not a single number.
At temperature 0, vary the seed (`--seed 43`, `--seed 44`) and use run ids `-r2` and `-r3`.

Outputs land in `bench/runs/<run_id>/` (gitignored). When a run is final, copy its
`summary.json` to `results/YYYY-MM-DD_<run_id>_summary.json` and commit it.

## Sanity checks before trusting the numbers

- `verify_gold` passes: 500/500 run and return rows.
- Accuracy is in a believable range. The dataset card lists no 7B coder model, so
  there is no direct reference. Expect it well below the hosted frontier models
  (about 55 to 90%). If it's near 0%, suspect the SQL extraction, not the model.
- Read 10 `wrong_result` rows in `labels.jsonl` by hand. Are they really wrong, or is
  the gold answer debatable? Log anything odd in [findings-log.md](findings-log.md).
- Check `truncated` in `predictions.jsonl`. If many answers hit the token cap, raise
  `num_predict` in `bench/config.py` and rerun.

## Go/pivot decision (kill criterion)

Run the existing open-source SQL checkers (blueprint Section B) on the `wrong_result`
set. **If one already catches most of what Plumbline would, especially wrong filter
values, stop building a rival.** Publish the benchmark and contribute upstream instead.

Tools to try: sqlsure, grainguard, graincheck, sqlsift. Also look at **schemagate**:
the BIRD website lists it with its own Mini-Dev benchmark, and it isn't in the
blueprint's table yet.

Record the decision and the evidence in [findings-log.md](findings-log.md).
