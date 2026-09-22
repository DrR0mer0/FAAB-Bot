# FAAB-Bot

A personal Fantasy Football analysis assistant, built for one person's 12-team Yahoo league. This file is standing context for Claude Code sessions in this repo — written for a reader who knows nothing about the project yet.

## Project

**O.D.D.S. ("Omega Deep Dart Sleeper")** is an XGBoost model that ranks low-usage NFL players by their likelihood of a breakout ("spike") week. It's built for waiver-wire and deep-roster decisions — finding the player whose opportunity is about to expand — not for start/sit calls on players already known to be good.

Honest performance, from a strict out-of-time test (trained 2010–2024, evaluated once on 2025): **precision@10 ≈ 28%**, against a base spike rate of **≈4.3%** — roughly a 6–7x lift over random. That's a useful weekly signal, not a predictor of individual outcomes. It's a **ranking** tool: it will be wrong most of the time, by design.

That number describes the **10-feature** model actually used for that held-out test. The current production model has since been promoted to **13 features** (Group 1 — see Methodology rules) and trained on 2010–2025; it has not yet had a genuine held-out test of its own (see Backlog), so treat its real-world precision as unconfirmed until a future season settles it.

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
| `predictions/` | Committed, frozen scoring output per week (JSON + markdown) from the production model, plus a matching `_shadow` JSON + markdown pair from the shadow model (`score_week.py`'s `--shadow-model`, default `odds_xgb_model_production_10feature.joblib`) when one was generated, plus `verification_log.json` — the running record of predicted-vs-actual for both |
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

**Weekly in-season cycle**: run `data/fetch_weekly_update.py --season Y` first, every week, before `generate_labels_and_breakouts.py`/`generate_player_week_features.py`/`score_week.py` -- it refreshes stats, games.csv, AND both roster files (`roster_<season>.csv`, `roster_weekly_<season>.csv`) in one idempotent call. **Roster files specifically must be refreshed every week, not just stats/games**: `score_week.py`'s team-changed and new-competitor checks (and the availability gate) read `roster_weekly_<season>.csv` directly, and any cut, signing, or trade since the last fetch is invisible to them until it's refreshed -- a stale roster file silently produces wrong suppressions with no error, which is exactly what happened for two full weeks before anyone noticed. `score_week.py` now warns loudly (not silently) if that file looks stale, but the warning is a backstop, not a substitute for refreshing it.

The SQLite database, `nflverse_raw/`, and all `.joblib` model files are gitignored (regeneratable/binary) — every script resolves their paths relative to the repo root via `Path(__file__).resolve().parent[.parent]`, not a bare relative string, so they work regardless of which directory a script is invoked from.

## Branches

- **`main`** is frozen at tag **`v1.0-week1`** — the model and pipeline state whose predictions are committed in `predictions/2026_week01.json`. Don't commit to `main`.
- **`v2`** is where all current and future work happens (feature additions, model changes, `verify_week.py`, this file).

## Known model limitations

- **QB scores are unreliable.** The dominant feature, touch share (carries + receptions), measures only rushing volume for a QB — a proxy for mobility, not for role or passing workload. A simple passing-volume feature (Group 2: trailing pass attempts, trailing pass air yards) was tried and **rejected** — see Methodology rules — so this limitation stands; a richer, play-by-play-derived version remains on the backlog.
- **No concept of teammates competing.** Touch share is computed per player, so two backs splitting one backfield can both rank highly without the model registering that they're taking carries from each other. A candidate fix (Group 3: `trailing_position_group_rank`, `trailing_share_of_position_group`) was tried and **rejected** — see Methodology rules — so this limitation stands.
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
- **Training scripts must specify their training seasons explicitly** — never train on "whatever is in `player_week_features`." That table now grows with live in-season data (`generate_player_week_features.py` rebuilds it across every loaded season, current one included, once it's been run for a week), so an unfiltered train silently leaks current-season weeks into a model meant to score that same season live. `train_production_model.py`'s `TRAIN_SEASONS` constant is the pattern to follow — this is exactly the bug caught (and fixed) during the Group 1 promotion.
- **Evaluation slices (standing designation):**
  - **2024** is the designated development slice for feature-group hypothesis testing (train 2010–2023, evaluate on 2024). It has already been reused across Group 1 and Group 2, so it is **not virgin** — a win on 2024 is a green light to keep developing a group, never by itself a green light to promote it. **Promotion to production requires prospective confirmation on live 2026 weeks** (real weeks scored and verified as they happen), in addition to a 2024 win, because 2024 can no longer distinguish a genuine effect from a group that happens to fit this one reused year.
  - **2023** is held in reserve as a **second-opinion slice for one future decision** — not for routine group testing. Spend it deliberately, once, when a genuinely close call needs a second read; don't fold it into the normal train range for a hypothesis test the way 2024 gets used.
  - **2025 is closed** — it already decided the 10-feature model's held-out test (see the top of this file). Don't re-run comparisons against it.
  - **2026 weeks cannot be re-scored** for hypothesis testing — `score_week.py` reads live state (current rosters, current injury/suppression signals), not a frozen historical snapshot, so a 2026 week can only be scored once, going forward, as it actually happens. Prospective confirmation therefore means watching new 2026 results accumulate via `verify_week.py`, not re-running `score_week.py` against an already-played 2026 week.

Seven feature candidates have been **rejected** by this discipline: a usage-trend feature (negligible importance); a season-over-season touch-share delta (won on a 2023 validation slice, then lost on the 2025 held-out test — the result that actually decided it); Group 2, QB volume (trailing pass attempts, trailing pass air yards, team WR-target rate) — it won pooled precision@10 on a 2024 re-test, but a per-position re-evaluation showed that win traced entirely to the model reallocating picks toward QBs (a ~4x higher base rate than the pooled population), with essentially no movement at RB, WR, or TE; Group 3, teammate competition (`trailing_position_group_rank`, `trailing_share_of_position_group` — rank/share of trailing touches among a player's own team's players at the same position, competition-style ranking on ties, single-player groups rank 1 with share 1.0; NULL for QB) — the same pattern as Group 2: it won pooled precision@10 on the 2024 development slice (0.2889 → 0.3167, +0.0278), but *lost* at every one of RB/WR/TE, the primary per-position metrics (RB −0.0167, WR −0.0167, TE −0.0056); Group 4, Vegas lines (`implied_team_total`, `game_total`, `team_spread`, looked up from `team_week_stats` for the player's own upcoming game — not a trailing window, so not leakage) — the opposite pattern: it *lost* pooled precision@10 on 2024 (0.2944 → 0.2722, −0.0222), with a mixed, mostly-noise-level per-position result (RB −0.0111, WR +0.0000, TE −0.0056, QB +0.0167), and even setting that weak signal aside, its 2026 NULL rate is a decisive **82.4% overall** (0% for the three already-played weeks, 100% from week 4 on) — Vegas lines aren't posted more than about two weeks out, which would make the group largely unusable for live scoring most of the season regardless of test results; and Group 5, trailing efficiency (`trailing_racr`, `trailing_receiving_epa`, `trailing_rushing_epa` — per-game trailing averages straight from `player_week_stats`, where a NULL raw value already encodes "no qualifying denominator that game"; `trailing_racr`/`trailing_receiving_epa` NULL for QB, `trailing_rushing_epa` NOT position-gated since it's real signal for a rushing QB too) — this one won BOTH pooled (0.3222 → 0.3333, +0.0111) AND RB/WR precision@10 (RB +0.0556, WR +0.0389; TE −0.0111, QB −0.0333), which would normally be the strongest result on record after Group 1. It's rejected anyway on a different, pre-registered concern: `spike_flag` is defined relative to a player's own trailing-3-game **points** baseline (`generate_labels_and_breakouts.py`), so a depressed efficiency stretch mechanically *lowers* that baseline and makes an unrelated point total easier to clear as a "spike" — a labeling artifact, not real signal. `evaluation/test_efficiency_features.py`'s confound check confirmed the artifact's fingerprint directly: spike rows have **systematically lower** trailing efficiency than non-spike rows on all three features (racr −0.107, receiving EPA −0.174, rushing EPA −0.275), the wrong direction for a "efficient players are due for more volume" story and exactly the direction the baseline-depression mechanism predicts. Pooled precision@k alone can't be trusted to catch a reallocation artifact, and — as Group 5 shows — neither can a good per-position result on its own when the label itself has a mechanical dependency on the candidate feature; per-position precision (`evaluation/metrics.py`) plus this kind of label-mechanism check are both now standard for a feature-group test. Group 7, line quality (`trailing_team_sack_rate_allowed`, `trailing_team_stuff_rate_allowed`, `trailing_opp_sack_rate_generated`, `trailing_opp_stuff_rate_generated`, sourced from `team_week_pbp_stats`; not position-gated) is the first group where all four features are TEAM-level rather than player-level — every player sharing a team, or facing the same upcoming opponent, gets the identical value that week (confirmed directly: ~19 rows/value for the team-side features, ~30 for the opponent-side ones, which have zero player-specific dispersion by construction). It won pooled precision@10 on 2024 (0.3056 → 0.3167, +0.0111), but the per-position read is a genuine mixed result, not a clean artifact or a clean win: RB dropped sharply (−0.0556), while WR gained (+0.0389) and TE was flat (+0.0056). Feature importances show the model does use it somewhat — 2.2% combined gain, ranked 9th/12th/13th/15th of 17 features, ahead of several established production features — so this isn't a case of the model ignoring a duplicated-value column; it's a real but net-negative effect concentrated at RB, the position where blocking quality plausibly matters most, which is what makes the drop notable rather than a favorable modeling choice.

Separately, a **recency-weighting modification** to 4 existing production features was tested and rejected in both variants: Group 6 applies exponential recency decay to the trailing-window computation behind `trailing_touches_avg`, `trailing_team_touch_share`, `trailing_target_share`, and `trailing_air_yards_share` — same feature *names*, not new columns, via `FeatureEngine.compute_recency_weighted_usage` (decay constant 0.5 per game further back, a 1-game half-life, chosen because usage/role can shift quickly and a flat mean already responds slowly) — against the unweighted production baseline on 2024. Variant A (same 3-game window, weighted) lost pooled precision@10 (0.3056 → 0.2889, −0.0167), RB −0.0333, WR −0.0111, TE +0.0000. Variant B (widened to a 6-game window, same decay) also lost pooled (−0.0278), RB −0.0333, WR +0.0000, TE +0.0111. Neither variant showed a per-position win worth weighing against its pooled loss. `evaluation/test_recency_decay_features.py` is the record of this test.

One candidate has been **adopted**: Group 1, receiving opportunity (`trailing_target_share`, `trailing_air_yards_share`, `trailing_adot`; NULL for QB rows). The same per-position re-test confirmed a genuine, position-appropriate win — WR +0.10 precision@10, TE +0.033 — not a reallocation artifact, and it's now part of `PRODUCTION_FEATURE_COLS` (13 features total). The production model trained on 2010–2025 with this set is `odds_xgb_model_production.joblib`; the superseded 10-feature model is preserved as `odds_xgb_model_production_10feature.joblib` for comparison. `predictions/verification_log.json` carries an explicit `model_changes` marker at this boundary — the production filename didn't change, so `git_commit` (not the filename) is what distinguishes weeks scored before vs. after this promotion.

`evaluation/` holds the record of all nine tests; don't re-litigate a rejected feature or modification without a genuinely new held-out test.

## Data sources

- **nflverse** ([nflverse-data](https://github.com/nflverse/nflverse-data)) for historical NFL stats. Current fetch source is the `stats_player` release tag — the older `player_stats` tag is deprecated/frozen and lacks 2025+ data. **2019 has a real schema gap** (its `stats_player_week` file predates the modern column layout) and is excluded from training; the season-gap logic throughout the pipeline treats it as a genuinely missing season, not something to bridge across.
- **Yahoo Fantasy Sports API**, read-only, for league data — access application submitted, approval pending. `league/` has the OAuth2 setup ready to go once it lands.

## Reproducibility note

XGBoost training is **not bit-reproducible** across runs, even with a fixed `random_state` (histogram-building order isn't strictly deterministic). A model's metadata sidecar (`*.meta.json`) describes how it was trained — seasons, features, hyperparameters, git commit — not a guarantee that retraining reproduces its exact scores. **Committed files under `predictions/` are the authoritative record of what a given model actually predicted**; never assume a regenerated model will reproduce them.

## Backlog

- Play-by-play-derived features: an O-line quality proxy, red-zone touch/target share, true QB aDOT and passing volume (a simpler passing-volume version was tried as Group 2 and rejected — see Methodology rules; this play-by-play version is a different, richer attempt at the same QB-unreliability limitation)
- A positional leaderboard split (separate rankings per position, rather than one pooled ranking) — the evaluation layer already does this (`evaluation/metrics.py` per-position precision); the live weekly ranking in `score_week.py` still pools everything into one list
- Weekly narrative/newsletter layer, once Yahoo API access is approved
- A genuine held-out test for the 13-feature production model — 2024 was reused for its promotion (not virgin), so its real out-of-time performance isn't confirmed yet; the last honest, fully-held-out number on record is the 10-feature model's 2025 result
