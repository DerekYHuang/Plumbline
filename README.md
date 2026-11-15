# Plumbline

## The problem

**AI models now write SQL for people who can't check it, and a large share of that SQL
runs without error but returns the wrong answer.**

A database only rejects SQL that is *broken*: a misspelled column, bad syntax. It
happily runs SQL that is *wrong*. Ask an AI "how many locally funded schools...?" and it
writes `WHERE FundingType = 'Locally Funded'`, but the data says `'Locally funded'` (small
f). The query runs, matches nothing or the wrong rows, and the user gets a confident,
wrong number. No error appears anywhere, so nobody finds out.

How big the problem is, measured on this project's own test (BIRD Mini-Dev, 500
questions, local model `qwen2.5-coder:7b`):

| What happened to the 500 AI-written queries | Count |
|---|---|
| Right answer | 46 (9%) |
| Database rejected it (the error is visible) | 144 (29%) |
| **Ran fine, wrong answer (the error is silent)** | **308 (62%)** |

The silent group is the dangerous one. Existing checkers mostly look for one cause,
joins that double-count rows, and the best of them flags 17% of these queries. **None
checks whether the values a query filters on actually exist in the data.** In this test,
35 of the silent errors filter on a value the column never contains, and 15 of those are
caught by no existing tool. Details: [docs/findings-log.md](docs/findings-log.md).

**Who it hurts:** analysts, operations and business teams using "ask your data" tools
and AI agents that query databases directly. Nobody in that loop reads the SQL.

## What Plumbline does

**Checks AI-written SQL against the real data before it runs, and says what to fix.**

Plumbline profiles each database once: which values each column actually holds, their
spelling and case, what IDs look like. Then it checks every AI-written query against that
profile and flags:

- filter values that don't exist (`'Locally Funded'` vs `'Locally funded'`),
- invented IDs and codes (a school code missing its leading zero),
- invented columns and tables,

and returns a repair hint the AI can act on. For join double-counting it relies on the
existing tool [grainguard](https://github.com/rohitchaurasia195-web/GRAINGUARD) rather than
reinventing it.

> Status: milestone M0 (baseline and go/pivot check) is done; M1 (data profiler) is next.
> Every number in this README is measured, with the benchmark and model named.
> Milestone reviews: [docs/milestones/](docs/milestones/).

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
pytest -q
```

## Get the data

The benchmark is [BIRD Mini-Dev](https://huggingface.co/datasets/birdsql/bird_mini_dev)
(500 questions over 11 SQLite databases, CC BY-SA 4.0). It is not redistributed here;
download two things into `data/bird_mini_dev/` (gitignored):

1. **Questions + official prompts** (~5 MB). From the Hugging Face repo, download
   [`baseline_inference/mini_dev_sqlite.jsonl`](https://huggingface.co/datasets/birdsql/bird_mini_dev/resolve/main/baseline_inference/mini_dev_sqlite.jsonl)
   and save it as `data/bird_mini_dev/mini_dev_sqlite.jsonl`.
   Use this file, not `data/mini_dev_sqlite.jsonl`: it has the same questions and gold
   SQL plus the exact prompt BIRD used for its published baselines.
2. **The SQLite databases** (~800 MB zip). The Hugging Face repo holds only the
   questions; the databases come from the
   [BIRD Mini-Dev GitHub repo](https://github.com/bird-bench/mini_dev):
   [`minidev.zip`](https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip).
   Unzip it and move the `dev_databases` folder to `data/bird_mini_dev/dev_databases/`.
   Nesting inside it doesn't matter; the loader finds each `<db_id>.sqlite` itself.

```
data/bird_mini_dev/
├── mini_dev_sqlite.jsonl
└── dev_databases/
    ├── california_schools/california_schools.sqlite
    └── ... (11 databases)
```

Put the data somewhere else by setting `PLUMBLINE_BIRD_DIR`.

## Run the M0 baseline

Needs [Ollama](https://ollama.com) running locally.

```bash
ollama pull qwen2.5-coder:7b
python -m bench.verify_gold                        # all 500 gold queries should PASS
python -m bench.generate --run-id smoke --limit 20 # quick end-to-end check
python -m bench.score smoke
python -m bench.generate --run-id qwen7b-r1        # full run (hours on a GTX 1060; resumable)
python -m bench.score qwen7b-r1
```

See [docs/m0-baseline.md](docs/m0-baseline.md) for what each step does and how to read
the output.

## License

Code: MIT (see [LICENSE](LICENSE)). The BIRD Mini-Dev benchmark used for evaluation is
CC BY-SA 4.0 and is **not** redistributed here.
