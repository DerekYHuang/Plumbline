# M3 hand review: reading the queries, not just counting them

The blueprint asks for this step because a detection rate alone can hide two problems:
flags that fire for the wrong reason, and an answer key that is itself wrong. Everything
below was read query by query on 2026-11-16 (run `qwen7b-r1`, checks as of the M3
commit). Samples were drawn at random with a fixed seed (30) from the 308 silent wrong
answers (`wrong_result`).

**Derek: please spot-check at least 5 rows of each table.** These verdicts were written
by Claude, and an independent second reader is what makes them credible in an interview.

## 1. The 9 gold queries Plumbline flags: is the answer key wrong?

All 9 flags come from the grainguard fan-out adapter. Plumbline's own rules flag 0 of 500
gold queries.

| q | Flag | Verdict | Why the gold query is actually right |
|---|---|---|---|
| 57 | FANOUT | false alarm | `WHERE format = 'commander'` leaves one legality row per card |
| 91 | FANOUT | false alarm | same: `format = 'pauper'` makes the join one-to-one |
| 132 | FANOUT | false alarm | `DISTINCT u.Id, u.Reputation` is one row per user (Reputation depends on Id) |
| 201 | FANOUT | false alarm | `disp.type = 'OWNER'`: one owner per account (checked in the data) |
| 235 | CHASM | false alarm in this data | would double-count only if a client held two classic cards; the data has none (checked) |
| 276 | FANOUT | false alarm | `DISTINCT raceId, year` is one row per race |
| 430 | FANOUT | false alarm | the question asks for the number of *lab measurements*, i.e. the many side |
| 477 | FANOUT | false alarm | "percentage of all atoms": counting atom rows is the point |
| 482 | FANOUT | false alarm | `atom_id = 'TR041_10'` pins the left side to one row |

**Result: the answer key is right in all 9 cases.** The false alarms fall into three
patterns grainguard can't see:
- a `WHERE` filter on part of a key (4 queries);
- a column that depends on the key, included in `DISTINCT`/`GROUP BY` (2);
- a question that really does count the many side (2).

## 2. 30 flagged silent errors: does the flag name a real defect?

| Verdict | Count | Queries |
|---|---|---|
| Flag names the real cause | 23 | 1, 9, 17, 21, 23, 32, 60, 106, 157, 163, 171, 178, 193, 198, 212, 229, 328, 361, 379, 390, 402, 468, 497 |
| Flag names a real defect, but not the main cause | 2 | 51 (fan-out real; wrong table is the bigger error), 204 (fan-out possible; missing `''` check is the cause) |
| Flag fires for the wrong reason | 5 | 214, 246, 313 (grainguard: filter or intended count); **315, 400 (value rule bugs, fixed in M3)** |

So **25 of 30 (83%) flags point at a real defect**. Examples of the real causes:
- `'Locally Funded'` (q1), `'East Bohemia'` vs `'east Bohemia'` (q198), `'Medium'` vs
  `'medium'` (q193): capitalisation.
- `'Scottish Premier League'` vs `'Scotland Premier League'` (q163), `'SANCO'` vs
  `'SANKC. UROK'` (q229), DOCType `'State Board of Education (SBOE)'` (q23): invented values.
- 13-digit CDS codes copied from the question without the leading zero (q9, q17, q32).
- A set *name* `'Coldsnap'` used where 3–6 letter set *codes* live (q60).
- `'Blackwulf'`, a hero, used as a publisher name (q361).
- `'2013-07-12'` against datetimes stored as `'2013-07-12 00:00:00'` (q193).
- One alias for two tables (q212).
- Real fan-outs (q106, q171, q178, q328, q379, q390, q468, q497).

**Bugs this review found, now fixed:**
- **q400:** `NOT LIKE '%suspect%'` was flagged. SQLGlot marks `NOT LIKE` with a flag on
  the `Like` node, which the negation check missed. Negation is now detected
  everywhere (`!=`, `NOT LIKE`, `NOT IN`, `NOT (...)`).
- **q315:** `LIKE '%water%' OR LIKE '%bottled drinking water%'`: the second pattern
  matches nothing, but the first covers it. A missing value OR-ed with a matching
  condition on the same column is now a warning. This generalises the q496 `IN`-list
  rule. Capitalisation slips stay errors, because they really do drop rows.

Effect: silent-error detection went from 71 to 68. All 3 lost "catches" (q79, q315, q400)
had been flagged for a harmless reason, so no real catch was lost. Gold false alarms are
unchanged.

## 3. 30 unflagged silent errors: what does Plumbline miss?

| Cause | Count | Queries | Catchable by a data check? |
|---|---|---|---|
| Misread question / wrong logic / wrong output shape | 15 | 15, 52, 61, 142, 155, 179, 181, 290, 308, 335, 408, 438, 450, 458, 490 | No: needs the user's intent |
| Text column used as a number (`MAX`, `ORDER BY`, `<` on text) | 5 | 70, 76, 80, 263, 396 | **Yes: a type rule** |
| Placeholder values meaning "unknown" (`'NaN'`, `'-'`, `0`, `''`) | 5 | 83, 140, 252, 352, 371 | **Yes: a placeholder rule** |
| Inner join silently drops rows whose key is NULL | 2 | 334, 359 | **Yes: the profile knows null rates** |
| Fan-out / wrong counting unit missed by grainguard | 3 | 380, 406, 489 | Partly |

**What this means:**
- **Half the misses need intent.** "Youngest" means lowest age, not oldest account; the
  user wanted a count, not a list. No data check can know that. This sets a ceiling
  on what a pre-execution validator can catch, and it's the honest answer to "why not
  higher than 23%?".
- **12 of the 30 point to three concrete next rules,** all using facts the M1 profile
  already has (stored types, null rates, value lists):
  1. **Text compared or sorted as a number,** e.g. `MAX(DNA)` on a text column returns
     `'9.8'` over `'10.2'`.
  2. **Placeholder values:** the column holds `'NaN'` / `'-'` / `''` / `0` as "unknown",
     and the query treats them as real.
  3. **Inner join on a nullable key:** rows with a NULL key vanish silently.

## 4. Against the blueprint's targets

| Target | Result | Met? |
|---|---|---|
| Detection 40% or more of silent errors | 22.1% (17.8–27.0) | **No** |
| 70% or more of detections caused by a bad filter value | value + schema rules: 30 of 68 (44%) | **No** |
| False alarms 5% or less | 1.8% gold (all fan-out); 0% from own rules | Yes |
| Latency under 50 ms at the 95th percentile | 16.3 ms | Yes |
| Profiling all 11 databases in under 10 minutes | 6.2 min | Yes |

Detection misses the target, and section 3 explains why: half of the silent errors are
intent errors no data check can see. The honest claim is "catches about a fifth of silent
errors, at under 2% false alarms, and when it fails a model answer it's right 99.5% of
the time", plus the measured coverage of the fault types it targets (mutation set).
