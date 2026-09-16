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
| `evaluation/` | Held-out model evaluation and feature experiments |
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
```

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

The lesson kept from all three rejections: a pooled or single-validation-year win isn't enough on its own. Holding out a truly untouched test set caught the share-delta regression; splitting precision by position caught the QB-volume reallocation. The `evaluation/` scripts and the rejected model artifacts remain in the repo as the record.

## Data sources

- **Historical NFL statistics** — [nflverse](https://github.com/nflverse/nflverse-data), community-maintained open data
- **League data** — Yahoo Fantasy Sports API, read-only, my own league only

## Tech

Python, SQLite, XGBoost, pandas.

## Status

Active personal project. Single user, single private league, not distributed or hosted publicly.

---

*Personal side project. Not affiliated with, endorsed by, or sponsored by the NFL, Yahoo, or any fantasy sports platform.*
