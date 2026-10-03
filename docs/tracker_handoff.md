# Baseline tracker — handoff note

Written at the end of the session that built phase 1 (commit `03c5fce`, 2026-10-03). Read this before touching `tracker/`. This note adds the *why* and the traps beyond `CLAUDE.md`/`README.md`.

## Purpose
Answer one question honestly: **is O.D.D.S. better than chance?** Every run is logged to an append-only ledger beside three dumb baselines drawn from the same pool, then scored against actual results.

## Design decisions (from the user's answers) and why
1. **Headline K=10, also K=25, each with its own matched position mix.** Top-10 matches the P@10 history; baselines copy O.D.D.S. production's mix per pick set so a position-mix edge can't pass as skill (Group 2's artifact). The shadow model is logged but is its own model, not a matched baseline.
2. **Per-slot refusal.** `thu` must be logged before the first game that precedes the Sunday slate; `sun` before the first Sunday kickoff (a 9:30am international game counts). A week with no Thursday game has no `thu` slot. A whole-week refusal would block every Sunday run (Thursday's game has started); already-kicked-off players are excluded by `score_week.py`'s kickoff guard instead.
3. **League scoring for the tracker; flag the label mismatch only.** The model's labels were built with a 4-pt passing TD; fixing that changes the model, so it waits for the separate retrain.
4. **Report two hit types, both under league scoring.** *Top-24 at position* = "does it help me win"; *spike week* (≥1.5× trailing-3 avg and ≥10) = "does O.D.D.S. beat chance at its own job." Spike is recomputed in the tracker from league-scored points — **never read from `labels_player_week.spike_flag`** (4-pt labels).
5. **Roster-%: ESPN `percentOwned` daily (primary), Sleeper trending adds (secondary), raw responses stored, failures loud but never fatal.** Yahoo is blocked; Sleeper has no ownership field. ESPN's endpoint is unofficial, so every fetch is shape-validated.
6. **Ownership is snapshotted into the pool at `log` time, and the under-50% subset is its own full scoreboard** (every model re-picks from that smaller pool) — the actual sleeper test, not a footnote. It can't be backfilled, so backtests can't have it.
7. **Snap counts from nflverse `snap_counts`** (new `tr_snap_counts`, PFR→gsis via `players.csv`). Heuristic = mean offense snap % of last 2 games minus the 2 before; ties → target share, then player_id; no 4 prior games → ranks last. Each run records the newest snap week it had (`tr_runs.snap_week_used`); the release lags ~a day.
8. **All five models logged:** `odds_prod`, `odds_shadow`, `dart`, `heuristic`, `last_week`.
9. **Backtest (phase 2): train as production does, score with as-of features, flag the leak, no model-logic changes.** Separately a TODO to retrain with `starter_absent_proxy` as-of (or removed), logged as a new model_version so old vs fixed compare.
- Also: committed JSONL export of every run's rows (`tracker/ledger_export/`), because the DB is gitignored and triggers aren't tamper-proof.

## Confirmed league scoring
0.5 PPR · 6-pt rushing/receiving TD · **5-pt passing TD** · −2 INT · 0.04/passing yd · 0.1/rush & rec yd · −2 fumble lost · +2 per 2-pt conversion. Lives in `tracker/hit_config.json`; `scoring`, `hit` and `crowd_hit` sections are hashed and the hash is stored with every scored week.

## Current state
- **Built and tested (65 tests):** ledger + triggers on all 10 append-only tables; `log`, `score`, `report`, `snapshot-ownership`, `fetch-snaps`; baselines; league scoring (cross-checked against nflverse's own `fantasy_points_ppr` on real rows); crowd-hit; bootstrap CIs (shown only from 3+ weeks; "too early to call" until 8, configurable).
- **Validated on real data** via a scratch DB copy only. **The real ledger had 0 runs at handoff**; snap counts (2022–26) and 4 ownership snapshots are in the real DB.
- **Not built:** `backtest` (stub, exits 2), the as-of feature mode, the walk-forward training wrapper, the label-shuffle check. Both phase-2 pieces touch model code — **show the diff to the user first.**

## Known issues / open TODOs
- **`starter_absent_proxy` leaks.** In the training table it uses whether the presumed starter played in the week being predicted (~94% populated); at live scoring it is `None`. It's a production feature. The planned label-shuffle check **cannot** catch this (shuffled labels stop matching a leaking feature too); use an as-of recomputation diff (week W's features with week W-onward data removed). Flagged in every report.
- **4-vs-5 label mismatch:** `half12` profile has a 4-pt passing TD. Fix in the same retrain.
- **Sleeper/ESPN name-match fallback:** when the `espn_id` crosswalk misses, ids are matched by *unique* normalized name+position against `ref_players` (never guesses on ambiguity). It lifted Sleeper trending from 27→87 of 100 mapped, ESPN to 957/960. Unaudited for wrong matches, and ids are frozen at snapshot time (append-only), so the earliest snapshots have lower coverage (snapshots 3-4 ran with a broken name-suffix regex that matched nothing, fixed afterward — they simply under-match, they don't mis-match). The Sleeper feed isn't used in any metric yet.
- ESPN snapshots are ~3.6 MB compressed each (~400 MB/season) and can't be pruned. Daily collection is **not scheduled** — it's one command; ask before creating a scheduled task.

## What a fresh session would get wrong
- **`log` runs `score_week.py` itself and writes the committed prediction files.** Don't hand-run it for the same snapshot. First run of a week keeps the bare name (`2026_week05.json`, what `verify_week.py` reads); a later one becomes `_sunday`. It never overwrites.
- **The ledger is append-only and `log` refuses re-logging a (season, week, slot).** Never test against the real DB — use `--db <copy> --predictions-dir <scratch> --export-dir <scratch>` (and `--as-of` only for testing). A bad real run is permanent.
- **`nfl_games.kickoff_utc` is US/Eastern, not UTC.** Reuse `score_week.parse_kickoff_utc`; don't re-derive it.
- **Scoring reads the raw `stats_player_week_<season>.csv`, not the DB** — the DB loader never carried the 2-pt columns. Spike baselines walk back through consecutive on-disk seasons and stop at the 2019 gap.
- **The tracker doesn't replace `verify_week.py` / `verification_log.json`** — both still run and still use the 4-pt `spike_flag`; numbers can differ slightly.
- **`u50` is strictly `<50`; players with unknown ownership are excluded from it** (not treated as 0%).
- **A 1-week report is not a result.** Don't cite tracker numbers until the small-sample note goes away.
- Windows: native Python won't resolve Git-Bash `/c/...` paths inside Python code; use `C:\...` or relative paths. Tests: `python -m unittest discover -s tracker -v` from the repo root.
- First loggable slot at handoff: week 4 `sun`, before 9:30am ET Sunday 2026-10-04; otherwise week 5 `thu`.
