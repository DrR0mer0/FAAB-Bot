# FAAB-Bot

A personal Fantasy Football analysis assistant, built for one person's 12-team Yahoo league. This file is standing context for Claude Code sessions in this repo — written for a reader who knows nothing about the project yet.

## Project

**O.D.D.S. ("Omega Deep Dart Sleeper")** is an XGBoost model that ranks low-usage NFL players by their likelihood of a breakout ("spike") week. It's built for waiver-wire and deep-roster decisions — finding the player whose opportunity is about to expand — not for start/sit calls on players already known to be good.

Honest performance, from a strict out-of-time test (trained 2010–2024, evaluated once on 2025): **precision@10 ≈ 28%**, against a base spike rate of **≈4.3%** — roughly a 6–7x lift over random. That's a useful weekly signal, not a predictor of individual outcomes. It's a **ranking** tool: it will be wrong most of the time, by design.

A weekly league recap layer (Yahoo Fantasy API, read-only) is planned but not yet built, pending API access approval.

## League settings

- 12 teams, head-to-head
- **Half-PPR**: 0.5 points per reception
- **Single-QB** (not superflex)
- Scoring: 6 pt rushing/receiving TDs, 5 pt passing TDs
- Roster: QB / WR / WR / RB / RB / TE / FLEX + 4 bench + IR
- 6-team playoffs, weeks 15–17
- Waivers run on **FAAB** (Free Agent Acquisition Budget bidding), not priority order

The small bench (4 spots) is why waiver-wire churn matters more than draft night in this format — there's little room to stash upside, so identifying the right add *this week* is high-leverage. That's the whole reason O.D.D.S. exists.

## Repo structure

| Directory | Contents |
|---|---|
| `data/` | Schema (`_init_schema.sql`), nflverse fetch/load pipeline, `crosscheck_roster_2026.py` (offseason roster reconciliation), `quick_verify.py` |
| `model/` | `features_lib.py` (shared feature computation, used by both training and live scoring), label/feature generators, training scripts, `score_week.py` (live weekly scoring), `model_metadata.py` |
| `league/` | Yahoo Fantasy API OAuth2 setup and read-only access (not yet wired to a live recap feature) |
| `evaluation/` | Held-out model evaluation, feature experiments, `verify_week.py` (scores committed predictions against what actually happened) |
| `predictions/` | Committed, frozen scoring output per week (JSON + markdown), plus `verification_log.json` — the running record of predicted-vs-actual |
| `legacy/` | Non-functional artifacts from an earlier, abandoned attempt at this project. Kept for historical context only; nothing in here is used by the current pipeline |

**Pipeline run order** (from repo root):
```
data/init_history_db_schema.py             # create schema
data/refresh_manifest.py                   # resolve nflverse asset URLs
data/fetch_nflverse_history_v5.py          # download season stats
data/load_nflverse_into_history_SAFE_v3.py # load into SQLite
data/load_players_into_ref.py              # player names/positions
model/generate_labels_and_breakouts.py     # spike labels
model/generate_player_week_features.py     # feature table
model/train_production_model.py            # train
model/score_week.py --season Y --week W --json-out predictions/<Y>_week<NN>.json
evaluation/verify_week.py --season Y --week W   # after the week completes and labels exist
```

The SQLite database, `nflverse_raw/`, and all `.joblib` model files are gitignored (regeneratable/binary) — every script resolves their paths relative to the repo root via `Path(__file__).resolve().parent[.parent]`, not a bare relative string, so they work regardless of which directory a script is invoked from.

## Branches

- **`main`** is frozen at tag **`v1.0-week1`** — the model and pipeline state whose predictions are committed in `predictions/2026_week01.json`. Don't commit to `main`.
- **`v2`** is where all current and future work happens (feature additions, model changes, `verify_week.py`, this file).

## Known model limitations

- **QB scores are unreliable.** The dominant feature, touch share (carries + receptions), measures only rushing volume for a QB — a proxy for mobility, not for role or passing workload. A passing-volume feature is on the backlog, not yet built.
- **No concept of teammates competing.** Touch share is computed per player, so two backs splitting one backfield can both rank highly without the model registering that they're taking carries from each other.
- **Offseason staleness is handled by suppression, not guessing.** Before a player has games on a new roster, usage features describe a role that no longer exists. `score_week.py` detects this (confirmed team change, or a meaningful new same-position competitor added this offseason — judged by prior-season production or early-round draft capital) and excludes the player from ranking with `score: null` and a reason, rather than publishing a number built on stale data.

## Git discipline (standing rules)

- Run `git status --ignored` before staging, every time.
- Stage exactly the intended files. **Never `git add -A`** — it silently sweeps in generated artifacts (`manifest_nflverse.json`, ad hoc report files, etc.) that don't belong in a commit.
- One logical change per commit.
- After pushing, verify against the remote with `git ls-remote` (or `git ls-remote --tags` for tags) — don't just trust the push command's own output.
- Never commit `.env`, `yahoo_token.json`, the `.db` file, `nflverse_raw/`, or `.joblib` model artifacts. (Model *metadata* sidecars — `*.meta.json` — are committed; the binaries they describe are not.)
- Use `git mv` for any file move/rename, so history is preserved.

## Methodology rules (standing)

- Train/test splits are always **chronological**, never random.
- Every feature must be computable strictly **before** the week being predicted — no leakage.
- Hyperparameters are tuned on a validation slice carved from the **training** period, never on the test set.
- A test set is used **once**, then considered closed. Don't re-run comparisons against an already-closed test year.
- A new feature is adopted only if it **wins on held-out data against a pre-registered metric** — never because the theory behind it sounds right.

Two features were built, tested, and **rejected** by this discipline: a usage-trend feature (negligible importance) and a season-over-season touch-share delta (won on a 2023 validation slice, then lost on the 2025 held-out test — the result that actually decided it). `evaluation/` holds that record; don't re-litigate a rejected feature without a genuinely new held-out test.

## Data sources

- **nflverse** ([nflverse-data](https://github.com/nflverse/nflverse-data)) for historical NFL stats. Current fetch source is the `stats_player` release tag — the older `player_stats` tag is deprecated/frozen and lacks 2025+ data. **2019 has a real schema gap** (its `stats_player_week` file predates the modern column layout) and is excluded from training; the season-gap logic throughout the pipeline treats it as a genuinely missing season, not something to bridge across.
- **Yahoo Fantasy Sports API**, read-only, for league data — access application submitted, approval pending. `league/` has the OAuth2 setup ready to go once it lands.

## Reproducibility note

XGBoost training is **not bit-reproducible** across runs, even with a fixed `random_state` (histogram-building order isn't strictly deterministic). A model's metadata sidecar (`*.meta.json`) describes how it was trained — seasons, features, hyperparameters, git commit — not a guarantee that retraining reproduces its exact scores. **Committed files under `predictions/` are the authoritative record of what a given model actually predicted**; never assume a regenerated model will reproduce them.

## Backlog

- Play-by-play-derived features: an O-line quality proxy, red-zone touch/target share, QB aDOT and passing volume (to address the QB-unreliability limitation above)
- A positional leaderboard split (separate rankings per position, rather than one pooled ranking)
- Weekly narrative/newsletter layer, once Yahoo API access is approved
