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
