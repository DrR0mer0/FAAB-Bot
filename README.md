# FAAB-Bot

A personal Fantasy Football analysis assistant, built for my own 12-team, half-PPR, single-QB Yahoo league.

## What it does

**O.D.D.S. ("Omega Deep Dart Sleeper")** is an XGBoost model trained on 15 seasons of NFL player statistics (2010–2025, excluding a real schema gap in 2019) across **13 features** to identify low-usage players positioned for a breakout ("spike") week. It's built for waiver-wire and deep-roster decisions — finding the player whose opportunity is about to expand — not for start/sit calls on players you already know are good.

A weekly league recap layer (Yahoo Fantasy API, read-only) is planned but not yet built, pending API access approval.

## What it is and isn't

O.D.D.S. is a **ranking** tool, not a predictor of individual outcomes. Honest numbers from a strict out-of-time test (trained 2010–2024, evaluated once on 2025, using the 10-feature set that predated Group 1 below):

- **Precision@10: ~28%** — of the model's ten highest-ranked candidates in a given week, roughly three actually spike
- Base rate for a spike is ~4.3%, so this is a **~6–7x lift over random**
- PR-AUC ~0.21

That's a useful weekly signal and nothing more. It will be wrong most of the time, by design — it's surfacing candidates worth a closer look, not making calls.

The production model has since been promoted to **13 features** (Group 1, below) and retrained on 2010–2025. It hasn't had a genuine held-out test of its own yet — its promotion test reused 2024, not a virgin year — so the numbers above are the last fully-confirmed result on record, not a claim about the current model's real-world performance. `predictions/verification_log.json` is tracking the current model's actual 2026 results as they come in.

### Known limitations

- **QB scores are unreliable.** The dominant feature is touch share (carries + receptions), which for a QB measures only rushing volume — a proxy for mobility, not for role or passing workload. A simple passing-volume feature was tried (Group 2, below) and rejected, so this limitation stands; a richer, play-by-play-derived version is on the backlog.
- **No concept of teammates competing.** Touch share is computed per-player, so two backs splitting the same backfield can both rank highly without the model registering that they're taking carries from each other.
- **Offseason staleness.** Before a player has games on a new roster, their usage features describe a situation that no longer exists. The scorer handles this by **suppressing** scores rather than guessing: players who changed teams, or whose team added a meaningful same-position competitor, are flagged and excluded from ranking until real data accumulates. Expect a large share of the pool to be suppressed in Week 1 and that share to shrink quickly.

## Repo structure

| Directory | Contents |
|---|---|
| `data/` | Schema, nflverse fetch/load pipeline, roster reconciliation, verification |
| `model/` | Feature engineering, label generation, training, weekly scoring |
| `league/` | Yahoo Fantasy API authentication (read-only) |
| `startsit/` | Standalone start/sit lineup simulator — see below |
| `tracker/` | Baseline tracker: logs O.D.D.S. next to "dumb" baselines and scores them after the games — see below |
| `evaluation/` | Held-out model evaluation and feature experiments |
| `predictions/` | Committed, frozen weekly scoring output (production + shadow), plus the running verification log |
| `legacy/` | Non-functional artifacts from an earlier attempt, kept for context |

## Pipeline order

```
data/init_history_db_schema.py          # create schema
data/refresh_manifest.py                # resolve nflverse asset URLs
data/fetch_nflverse_history_v5.py       # download season stats
data/load_nflverse_into_history_SAFE_v3.py
data/load_players_into_ref.py           # player names/positions
model/generate_labels_and_breakouts.py  # spike labels
model/generate_player_week_features.py  # feature table
model/train_production_model.py         # train
model/score_week.py --season Y --week W # score an upcoming week
evaluation/verify_week.py --season Y --week W  # once the week is over and actuals exist
```

**Every week during the season**, run `data/fetch_weekly_update.py --season Y` first — it refreshes stats, the schedule, and both roster files in one call. `score_week.py`'s suppression checks (team-changed, new-competitor, availability) read the roster file directly, so a stale one silently produces wrong results with no error.

## Start/sit simulator

`startsit/lineup_sim.py` picks the lineup that maximizes **win probability** against this week's opponent — not just projected points — from per-player p10/p50/p90 projections, simulating correlated outcomes (a QB and his WR booming together, two backs on the same team splitting work) and explaining the close calls.

```
python startsit/lineup_sim.py startsit/example_week.json --seed 7
```

It's standalone and model-agnostic; the full week-file contract is in `startsit/README.md`. `example_week.json` uses made-up numbers — it's waiting on the Yahoo fetcher to produce real week files once API access comes through.

## Baseline tracker

How I find out whether O.D.D.S. is actually better than chance. Every run is written to an **append-only** SQLite ledger (UPDATE/DELETE are blocked by triggers) next to three deliberately dumb baselines that draw from the *exact same pool* with the *same position mix*, then everything is scored against what really happened:

- **dart** — random picks, 1,000 draws. Only the seed and the pool snapshot are stored; the distribution is regenerated at scoring time.
- **heuristic** — snap-share increase (mean offense snap % over the last 2 games minus the 2 before that; ties broken by target share).
- **last_week** — last game's half-PPR points.

The shadow (10-feature) model is logged too. Headline is the top 10; the top 25 is scored as well, each with its own matched position mix.

From the repo root (PowerShell), after the usual `data/fetch_weekly_update.py` + loader:

```
python tracker/tracker.py snapshot-ownership                      # daily: ESPN roster-%, Sleeper trending adds
python tracker/tracker.py fetch-snaps                             # nflverse snap counts (the heuristic needs them)
python tracker/tracker.py log --season 2026 --week 5 --slot thu   # Thursday morning, before kickoff
python tracker/tracker.py log --season 2026 --week 5 --slot sun   # Sunday morning, before the first Sunday kickoff
python tracker/tracker.py score --season 2026 --week 5            # after Monday night
python tracker/tracker.py report --season 2026 --week 5           # newsletter-ready scoreboard
python tracker/tracker.py report --season 2026                    # season to date, with bootstrap 95% CIs
python -m unittest discover -s tracker -v                         # tests
```

`log` runs `model/score_week.py` as-is and **refuses** once its slot's first kickoff has passed (a week with no Thursday game has no `thu` slot). Each `log` also exports its rows to `tracker/ledger_export/*.jsonl`, which *is* committed — the database isn't.

**What counts as a hit** (`tracker/hit_config.json`; its hash is stored with every scored week, so any later edit is visible in the data). Everything is scored under my league's settings — 0.5 PPR, 5-pt passing TDs, 6-pt rushing/receiving TDs, −2 INT, −2 fumbles lost, +2 per 2-pt conversion:

- **Top-24 finish** — the pick finishes top-24 at his position that week. "Does it help me win."
- **Spike week** — he scores ≥1.5× his trailing 3-game average and ≥10 points. "Does O.D.D.S. beat chance at its own job."
- **Dart percentile** — where a model's result falls among the 1,000 random draws; 50% is chance.
- **Crowd hit** (secondary) — a pick that started under 50% owned and rose above 50% within 14 days. Ownership is ESPN's `percentOwned` (unofficial endpoint, so it's validated and a failure is logged loudly, never fatal), not Yahoo's; collection started 2026-10-03, so this is excluded from any backtest.
- **Sleeper pool** — every scoreboard is also produced for just the players under 50% owned at pick time, with each model re-picking from that smaller pool. That's the actual sleeper test.

The scoreboard says plainly when the sample is too small to call a winner. Two known issues are flagged in every report rather than hidden: the production model's `starter_absent_proxy` feature leaks (see `CLAUDE.md`), and its training labels used a 4-pt passing TD. Both are queued for a separate retrain. Backtesting (`backtest`) is phase 2 and not built yet.

## Method notes

**Labels.** A "spike" is defined relative to the player's own recent baseline: fantasy points ≥1.5× their trailing 3-game average *and* ≥10 half-PPR points (the floor matters — 1 point to 2 points is a 2× "spike" that means nothing). Requires ≥3 prior games; playoff weeks excluded; baselines never span a missing season.

**No leakage.** Every feature is computable strictly before the week being predicted. Train/test splits are chronological, never random. Hyperparameters were tuned on a validation slice carved from the training period, never on the test set.

**Scoring is data-driven.** League scoring rules live in a `scoring_profiles` table as JSON, so re-scoring history under different league settings needs no code changes.

**Training isn't bit-reproducible.** XGBoost's histogram-building isn't strictly deterministic across runs even with a fixed `random_state`, so retraining from a model's metadata sidecar reproduces the same architecture, hyperparameters, and data — not the same scores. Committed prediction artifacts under `predictions/` are the authoritative record of what a given model actually predicted; a regenerated model is not expected to reproduce them exactly.

## What didn't work

Kept deliberately, because the failures were as informative as the wins.

Two feature groups were tested against the production baseline in the same cycle: **Group 1 (receiving opportunity)** won a per-position re-test and was adopted — it's the 13-feature set mentioned above. **Group 2 (QB volume)**, detailed below, lost that same re-test.

**Usage trend (rejected).** The theory was that the *direction* of a player's touch trend would matter more than the level — a rising role predicting a breakout. It ranked near the bottom of feature importance (~0.01). A slope over three games is a much noisier statistic than an average over three games, and whatever signal it carried was likely already captured by touch share itself.

**Season-over-season share delta (rejected, and the more instructive one).** Designed to separate "low share because a role is expanding" from "low share because a career is declining" — the model can't otherwise distinguish an emerging rookie from an aging star. It beat baseline clearly on a 2023 validation slice (+0.033 precision@10) and was adopted on that basis. On the genuinely untouched 2025 test set, it **lost** on precision@10 (0.2667 vs 0.2833), despite winning on PR-AUC and precision@25. The pre-registered metric decided it, and it was dropped.

**QB volume, Group 2 (rejected).** Trailing pass attempts, trailing pass air yards, and a team-level WR-vs-RB target-rate feature, built to address the QB-unreliability limitation above. On a pooled precision@10 re-test (2010–2023 train, 2024 test) it looked like a clear win: +0.044. Splitting that result out by position told a different story: RB moved -0.006, WR +0.000, TE +0.006 — all noise — while QB alone moved +0.061. The entire pooled win was the model reallocating picks toward QBs, who spike at roughly 4x the pooled base rate in this data; RB/WR/TE, where this league's actual waiver decisions happen, saw no benefit at all. This is exactly why `evaluation/metrics.py` now reports per-position precision on every feature test, not just pooled — a pooled win can be a base-rate reallocation artifact rather than genuine discrimination, and the only way to catch that is to check.

Since then, more feature groups have been tested the same way. Teammate competition, line quality, and red-zone opportunity each won pooled precision@10 on 2024, but the per-position breakdown — the metric that actually decides adoption — didn't support it: teammate competition lost at every one of RB/WR/TE, while line quality and red-zone opportunity showed a mixed picture with RB notably weaker. Vegas lines (upcoming-game spread/total) lost on pooled precision@10 outright, and separately aren't posted far enough ahead of kickoff to be usable for most of a season. Trailing efficiency won decisively on pooled and RB/WR precision — the strongest result since Group 1 — but was traced to a labeling confound: the spike threshold is defined relative to a player's own recent points, so a cold stretch lowers the bar it has to clear, which isn't the same thing as real signal. A recency-weighted version of the existing usage features lost against the flat-average original it was meant to replace. Red-zone opportunity's initial 2024 win was then re-checked against a decision rule pre-registered for the project's one reserved second-opinion test year (2023) and failed it. None of the above were adopted; `CLAUDE.md`'s Methodology rules section has the complete, test-by-test record.

The lesson kept from all these rejections: a pooled or single-validation-year win isn't enough on its own. Holding out a truly untouched test set caught the share-delta regression; splitting precision by position caught the QB-volume reallocation and several groups after it. The `evaluation/` scripts and the rejected model artifacts remain in the repo as the record.

## Data sources

- **Historical NFL statistics** — [nflverse](https://github.com/nflverse/nflverse-data), community-maintained open data
- **League data** — Yahoo Fantasy Sports API, read-only, my own league only

## Tech

Python, SQLite, XGBoost, pandas, numpy, pyarrow (for nflverse's play-by-play parquet releases).

## Status

Active personal project. Single user, single private league, not distributed or hosted publicly.

---

*Personal side project. Not affiliated with, endorsed by, or sponsored by the NFL, Yahoo, or any fantasy sports platform.*
