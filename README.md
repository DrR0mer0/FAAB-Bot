# FAAB-Bot

A personal Fantasy Football analysis assistant, built for my own 12-team, half-PPR, single-QB Yahoo league.

## What it does

**O.D.D.S. ("Omega Deep Dart Sleeper")** is an XGBoost model trained on 15 seasons of NFL player statistics (2010–2024) to identify low-usage players positioned for a breakout ("spike") week. It's built for waiver-wire and deep-roster decisions — finding the player whose opportunity is about to expand — not for start/sit calls on players you already know are good.

A weekly league recap layer (Yahoo Fantasy API, read-only) is planned but not yet built, pending API access approval.

## What it is and isn't

O.D.D.S. is a **ranking** tool, not a predictor of individual outcomes. Honest numbers from a strict out-of-time test (trained 2010–2024, evaluated once on 2025):

- **Precision@10: ~28%** — of the model's ten highest-ranked candidates in a given week, roughly three actually spike
- Base rate for a spike is ~4.3%, so this is a **~6–7x lift over random**
- PR-AUC ~0.21

That's a useful weekly signal and nothing more. It will be wrong most of the time, by design — it's surfacing candidates worth a closer look, not making calls.

### Known limitations

- **QB scores are unreliable.** The dominant feature is touch share (carries + receptions), which for a QB measures only rushing volume — a proxy for mobility, not for role or passing workload. Adding passing-volume features is on the backlog.
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

**Usage trend (rejected).** The theory was that the *direction* of a player's touch trend would matter more than the level — a rising role predicting a breakout. It ranked near the bottom of feature importance (~0.01). A slope over three games is a much noisier statistic than an average over three games, and whatever signal it carried was likely already captured by touch share itself.

**Season-over-season share delta (rejected, and the more instructive one).** Designed to separate "low share because a role is expanding" from "low share because a career is declining" — the model can't otherwise distinguish an emerging rookie from an aging star. It beat baseline clearly on a 2023 validation slice (+0.033 precision@10) and was adopted on that basis. On the genuinely untouched 2025 test set, it **lost** on precision@10 (0.2667 vs 0.2833), despite winning on PR-AUC and precision@25. The pre-registered metric decided it, and it was dropped.

The lesson kept from this: a win on a single ~18-week validation year is a small sample, and holding out a truly untouched test set is what caught it. The `evaluation/` scripts and the rejected model artifact remain in the repo as the record.

## Data sources

- **Historical NFL statistics** — [nflverse](https://github.com/nflverse/nflverse-data), community-maintained open data
- **League data** — Yahoo Fantasy Sports API, read-only, my own league only

## Tech

Python, SQLite, XGBoost, pandas.

## Status

Active personal project. Single user, single private league, not distributed or hosted publicly.

---

*Personal side project. Not affiliated with, endorsed by, or sponsored by the NFL, Yahoo, or any fantasy sports platform.*
