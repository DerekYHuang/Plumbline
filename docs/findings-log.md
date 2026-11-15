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

### Go/pivot check: existing checkers vs the qwen7b-r1 errors

Harness: `compare/run_competitors.py`. The tools live in an isolated venv under `.compare/`
and their wheels were checked for network and subprocess calls before install. Every tool
got the same facts, read from each SQLite file: primary keys and UNIQUE indexes as grain,
foreign keys as join cardinality. Nothing was tuned per query. All 11 databases have keys
on almost every table (thrombosis_prediction 2/3, superhero 8/10).

- **What's out there.**
  - **sqlsure 0.1.1** (Apache-2.0, Jul 2026): fan-out, chasm, additivity, join-key and
    PII rules, checked against a declared semantic model.
  - **grainguard 0.1.0** (MIT, Oct 2026): infers grain from keys and SQL, and flags
    fan-out and chasm aggregates.
  - **graincheck 0.1.1** (Sep 2026): fan-out, outer-join-in-WHERE, average-of-ratios,
    unguarded-division and similar rules, aimed at dbt projects.
  - **sqlsift:** not on PyPI and not findable; dropped.
  - **schemagate:** selects the schema *before* generation and never checks finished SQL,
    so it isn't a competitor for this check.
  - graincheck's README also names dblect, dbt-assay and Altimate. The fan-out niche is
    crowded.

- **Results.** "Rejected" counts error-level findings only (graincheck: high impact); warnings
  are excluded. Columns: rejected wrong answers out of 308 silent ones, rejected gold
  queries out of 500 (false alarms), and rejected correct answers out of 46.

  | Tool | Wrong answers rejected (/308) | Gold rejected (/500) | Correct preds rejected (/46) |
  |---|---|---|---|
  | sqlsure | 53 (17%) | 41 (8.2%) | 7 |
  | grainguard | 44 (14%) | 10 (2.0%) | 2 |
  | graincheck | 48 (16%) | 17 (3.4%) | 1 |
  | **value check (Plumbline prototype)** | **35 (11%)** | **1 (0.2%)** | **1** |

- **No tool catches most of the silent errors.** The best rejects 17% of them, and
  sqlsure does it at an 8% false-alarm rate on gold. Even all three tools together, counting
  warnings, flag 139/308, much of it noise.
- **Fan-out is solved well enough already.** All three flag q403 and q106. grainguard has
  the best balance: 14% of wrong answers rejected at 2% false alarms. Building a fourth
  fan-out checker adds nothing.
- **Wrong filter values are uncovered.** No tool flags q1 (`'Locally Funded'` vs
  `'Locally funded'`). The value-check prototype (string literal in `=`/`IN` that never
  occurs in that column) flags 35/308 wrong answers. Of those, 33 literals are case or
  whitespace mismatches (`watermark = 'Boros'` vs `'boros'`) and 30 are absent values,
  e.g. CDS codes that lost their leading zero. **15 of the 35 are flagged by no other
  tool.** It raised 1 false alarm on 500 gold queries: q496 filters on elements
  `'mg'`/`'li'` that don't occur in the data, and the gold answer is still right.
- **Caveats.** "Rejected" means a tool flagged the query, not that it named the actual bug.
  Fan-out flags were checked by hand only on q403/q106/q58. The value check is a 60-line
  prototype that only understands `=` and `IN` on string literals.

**Decision (confirmed by Derek, 2026-11-15): GO, repositioned.** Plumbline's core is
value and schema grounding (wrong filter values, invented IDs, case mismatches), which no
existing tool covers. For fan-out, reuse grainguard (MIT) as a dependency or benchmark
against it, instead of writing a rival rule. M0 is closed except the two extra seed runs
(r2, r3), which Derek runs on his own time.
