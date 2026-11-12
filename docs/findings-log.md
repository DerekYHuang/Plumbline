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
