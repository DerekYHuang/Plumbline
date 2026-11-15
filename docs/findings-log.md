# Findings log

A dated, append-only record of things learned while benchmarking: suspicious gold
answers, surprising model behaviour, tool comparisons and decisions. Interview material.
Keep each entry short: what, evidence (question id, query) and what it means.

## 2026-11-11

- **Setup.** BIRD Mini-Dev "Mini-Dev 1024" annotations (Hugging Face, revised Oct 2026).
  The databases come from `minidev.zip` (bird-bench, last modified 2024-06-20); the
  Hugging Face repo holds only questions and prompts.
- **Name.** `plumbline` is taken on PyPI by an unrelated lidar tool, so we publish as
  `plumbline-sql`. The import and CLI name stays `plumbline`.
- **Competitor to check.** The BIRD website links *schemagate* (github.com/ashishsinha1602/schemagate),
  which has its own BIRD Mini-Dev benchmark. It isn't in the blueprint's Section B; add
  it to the week-1 kill-criterion comparison.
- **Gold queries verified.** `python -m bench.verify_gold`: 500/500 gold queries run
  and return rows on the local copy (201.5 s total, median 11 ms). The slowest took
  28.0 s, close to the original 30 s timeout, so the timeout was raised to 60 s.
- **GPU backend.** Ollama's CUDA backend crashed on the GTX 1060 with driver 555.85
  (CUDA 12.5): "PTX was compiled with an unsupported toolchain". The Vulkan backend
  (`OLLAMA_VULKAN=1`) works: 28/29 layers on the GPU, about 21 tokens/s, about 29 s per
  question, so a full 500-question run takes about 4 hours.
- **Smoke run (q0 to q19, all `california_schools`), qwen2.5-coder:7b: 0/20 correct**
  (11 `wrong_result`, 9 `exec_error`, none truncated). Reading the answers confirmed
  the pipeline is fine and these are genuine model errors. Still not representative:
  one database, mostly challenging questions. Examples:
  - **q1: a wrong filter value, Plumbline's headline case.** The model wrote
    `FundingType = 'Locally Funded'`, but the data holds `'Locally funded'`. The query
    runs and silently returns the wrong rows; a value check would catch it.
  - **q3:** the unquoted column `District Type` gives a syntax error. The database
    catches this one.
  - **q0:** integer division `NumGE1500 / NumTstTakr` turns every rate into 0 or 1.
    This is a type issue, not schema or value: a candidate for a later rule.
- **GPU fixed.** Updating the NVIDIA driver to 582.78 (CUDA 13.0) fixed the CUDA crash.
  The normal Ollama app now works, at about 20 tokens/s with 26/29 layers on the GPU.

## 2026-11-15

- **Baseline run `qwen7b-r1`: 46/500 = 9.2% EX** (308 `wrong_result`, 144 `exec_error`,
  2 `timeout`, 0 `no_sql`). Summary: `results/2026-11-15_qwen7b-r1_summary.json`.
- **Why so low: this release is almost all hard questions.** Mini-Dev 1024 has 460
  challenging, 32 moderate and 8 simple questions, so the per-difficulty numbers
  (simple 50% = 4/8) rest on tiny samples. Don't compare it with the original Mini-Dev
  leaderboard, which has a 30/50/20 mix.
- **Sanity checks passed.** No `no_sql`, so extraction works. Only 6/500 answers hit the
  1024-token cap. In 6 random `wrong_result` rows checked by hand, the gold answer was
  right and the model was wrong each time.
- **`exec_error` breakdown (144):** 94 `no such column`, 13 `no such table`,
  15 syntax errors, 9 aggregate misuse, 6 ambiguous column. Most are schema
  hallucinations, which a schema check can catch before execution, but the database
  already rejects them.
- **Fan-out is common among the silent errors.** Two of the six sampled
  `wrong_result` rows are join fan-out, Plumbline's fan-out rule target:
  - q403: `COUNT(p.ID)` after joining Patient to Laboratory counts lab rows, not
    patients. Gold uses `COUNT(DISTINCT ...)`.
  - q106: `COUNT(st.id)` over a join to `set_translations` counts translations, not sets.
  Others are question-reading errors, e.g. q117 counted `uuid` (printings) when the
  question says to count cards by name, which a rule can't catch.
- **Per database:** thrombosis_prediction 1/86 and california_schools 1/49 are worst,
  card_games 10/69 best.
