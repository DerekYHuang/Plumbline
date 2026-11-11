# Plumbline

**Catches AI-written SQL that runs fine but returns the wrong answer, before it runs.**

> Status: early development (milestone M0, baseline). No results yet. Every number
> in this README will be a measured one, with the benchmark and model named.

A database rejects broken SQL but happily runs wrong SQL. An agent filters on
`status = 'Finished'` when the column holds `'Completed'`, gets zero rows, and reports
zero. Nothing errors, so nobody notices. Plumbline profiles the real data, parses each
agent-written query, and flags invented columns, filter values that don't exist, and
joins that double-count, returning a repair hint the agent can act on.

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
