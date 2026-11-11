# Plumbline

**Catches AI-written SQL that runs fine but returns the wrong answer, before it runs.**

> Status: early development (milestone M0, baseline). No results yet. Every number
> in this README will be a measured one, with the benchmark and model named.

A database rejects broken SQL but happily runs wrong SQL. An agent filters on
`status = 'Finished'` when the column holds `'Completed'`, gets zero rows, and reports
zero. Nothing errors, so nobody notices. Plumbline profiles the real data, parses each
agent-written query, and flags invented columns, filter values that don't exist, and
joins that double-count, returning a repair hint the agent can act on.

## License

Code: MIT (see [LICENSE](LICENSE)). The BIRD Mini-Dev benchmark used for evaluation is
CC BY-SA 4.0 and is **not** redistributed here.
