# 90-second demo: recording script

What the recording has to show, in order: the problem, the fix, the proof. Record the
terminal only (Windows: Win+G Game Bar, or OBS), font size 18+, dark theme, a window about
110 × 30 characters. Run the commands once beforehand so the catalog exists and nothing
is slow on camera.

**Prep (not recorded)**

```bash
cd "G:\Plumbline Project"
.venv\Scripts\activate
plumbline profile data\bird_mini_dev\dev_databases\minidev\MINIDEV\dev_databases\california_schools\california_schools.sqlite -o demo.json
cls
```

---

**0:00–0:15 · The problem** (say it, with the README's results table on screen)

> "AI writes SQL for people who can't check it. On a 500-question benchmark, 62% of a
> local model's queries ran without any error and returned the wrong answer. The
> database only rejects broken SQL. It runs wrong SQL happily."

**0:15–0:35 · A real wrong query, as the model wrote it**

```bash
plumbline check demo.json "SELECT School FROM schools WHERE FundingType = 'Locally Funded'"
```

> "This is question 1 from the benchmark. It runs fine. Plumbline says it's wrong before
> it runs: the data spells it 'Locally funded', small f, and it tells the AI exactly what
> to change."

**0:35–0:50 · Two more kinds it catches**

```bash
plumbline check demo.json "SELECT * FROM frpm WHERE CDSCode = '1611920113902'"
plumbline check demo.json "SELECT FundingTypes FROM schools"
```

> "A school code missing its leading zero: every code has 14 digits. And an invented
> column, with the closest real one. Each check takes about 7 milliseconds; no model, no
> database connection."

**0:50–1:10 · The proof**

Show the chart in the README (`results/detection_vs_false_alarms.svg`).

> "Measured against the real answers: it catches 22% of the silent wrong answers, and 25%
> on a second model it was never tuned on. Its own rules flagged zero of 500 correct
> queries. Up and to the left of the three existing open-source checkers."

**1:10–1:30 · Anyone can check this**

```bash
python -m bench.reproduce --skip-gold-check
```

Let it start, then cut to the final PASS lines (from a run recorded earlier).

> "One command downloads the benchmark, verifies it, re-runs everything and compares
> with the published numbers. Code, data checksums and every model answer are in the
> repo."

---

**Checks before publishing the video:** every number said aloud matches the README; no
personal paths or usernames visible beyond the project folder; under 90 seconds.
