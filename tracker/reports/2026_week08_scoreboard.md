# O.D.D.S. vs the baselines -- 2026 Week 8

_Scored 2026-11-03 15:53 UTC; hit definition `476e6708f0`._

_Week 8's breakout-trend windows (observation only) resolve after week 11 at the latest (role growth after week 10); they appear in the season summary, never in this scoreboard._

**How a pick is scored** (league settings: 0.5 PPR, 5-pt passing TDs, 6-pt rushing/receiving TDs, -2 INT, -2 fumbles lost, +2 per 2-pt conversion, -0.5 per sack taken, 0.02 per return yard, 6-pt return and fumble-return TDs):
- **Top-24 finish** -- the player finishes top-24 at his position that week ("does it help me win").
- **Spike week** -- the player scores at least 1.5x his trailing 3-game average and at least 10 points ("does O.D.D.S. beat chance at its own job").
- **Dart percentile** -- where the model's result falls among N random draws from the same pool with the same position mix ("beat 85% of dart throws"). 50% is chance; ties count half.

## All eligible players

### Top 10 -- thu run (2026-10-29 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 4/10 (40%) | 3/10 (30%) | 81.4 | 90% | 93% |
| O.D.D.S. shadow (10-feature) | 6/10 (60%) | 4/10 (40%) | 111.1 | 99% | 98% |
| Dart (avg of 1000 random draws) | 2.2/10 (22%) | 1.2/10 (12%) | 60.9 | - | - |
| Snap-share heuristic | 5/10 (50%) | 4/10 (40%) | 116.1 | 97% | 98% |
| Last week's points | 6/10 (60%) | 2/10 (20%) | 146.8 | 99% | 78% |

### Top 25 -- thu run (2026-10-29 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 9/25 (36%) | 8/25 (32%) | 224.6 | 98% | 100% |
| O.D.D.S. shadow (10-feature) | 11/25 (44%) | 5/25 (20%) | 265.5 | 100% | 89% |
| Dart (avg of 1000 random draws) | 5.0/25 (20%) | 3.0/25 (12%) | 143.1 | - | - |
| Snap-share heuristic | 6/25 (24%) | 5/25 (20%) | 184.3 | 69% | 89% |
| Last week's points | 10/25 (40%) | 4/25 (16%) | 276.0 | 100% | 74% |

### Top 10 -- sun run (2026-11-01 15:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 5/10 (50%) | 3/10 (30%) | 108.2 | 97% | 94% |
| O.D.D.S. shadow (10-feature) | 5/10 (50%) | 3/10 (30%) | 105.8 | 97% | 94% |
| Dart (avg of 1000 random draws) | 2.4/10 (24%) | 1.2/10 (12%) | 65.4 | - | - |
| Snap-share heuristic | 5/10 (50%) | 4/10 (40%) | 113.3 | 97% | 99% |
| Last week's points | 7/10 (70%) | 3/10 (30%) | 177.7 | 100% | 94% |

### Top 25 -- sun run (2026-11-01 15:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 9/25 (36%) | 6/25 (24%) | 211.9 | 96% | 95% |
| O.D.D.S. shadow (10-feature) | 11/25 (44%) | 4/25 (16%) | 269.1 | 99% | 70% |
| Dart (avg of 1000 random draws) | 5.4/25 (22%) | 3.2/25 (13%) | 152.3 | - | - |
| Snap-share heuristic | 7/25 (28%) | 4/25 (16%) | 186.9 | 80% | 70% |
| Last week's points | 12/25 (48%) | 4/25 (16%) | 303.6 | 100% | 70% |

## Sleeper pool -- under 50% owned at pick time

_Pool restricted to players under 50% owned at the time of the run; every model re-picks from that smaller pool._

- **Under-50% pool, week 8 `thu` run:** 239 players, 1 of them with **ownership unknown** (no roster-% row for them in the snapshot used). Unknown is not treated as under 50% or as 0%: those players are kept in the pool and counted here rather than dropped.
- **Under-50% pool, week 8 `sun` run:** 215 players, 1 of them with **ownership unknown** (no roster-% row for them in the snapshot used). Unknown is not treated as under 50% or as 0%: those players are kept in the pool and counted here rather than dropped.

### Top 10 -- thu run (2026-10-29 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 2/10 (20%) | 2/10 (20%) | 79.4 | 87% | 90% |
| O.D.D.S. shadow (10-feature) | 4/10 (40%) | 3/10 (30%) | 107.6 | 100% | 99% |
| Dart (avg of 1000 random draws) | 0.8/10 (8%) | 0.7/10 (7%) | 34.6 | - | - |
| Snap-share heuristic | 1/10 (10%) | 1/10 (10%) | 29.0 | 60% | 64% |
| Last week's points | 1/10 (10%) | 0/10 (0%) | 46.2 | 60% | 23% |

### Top 25 -- thu run (2026-10-29 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 6/25 (24%) | 4/25 (16%) | 176.9 | 93% | 94% |
| O.D.D.S. shadow (10-feature) | 6/25 (24%) | 5/25 (20%) | 179.3 | 93% | 99% |
| Dart (avg of 1000 random draws) | 3.6/25 (14%) | 1.9/25 (7%) | 89.7 | - | - |
| Snap-share heuristic | 3/25 (12%) | 1/25 (4%) | 74.4 | 38% | 28% |
| Last week's points | 6/25 (24%) | 2/25 (8%) | 145.4 | 93% | 57% |

### Top 10 -- sun run (2026-11-01 15:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 2/10 (20%) | 2/10 (20%) | 85.7 | 84% | 87% |
| O.D.D.S. shadow (10-feature) | 4/10 (40%) | 3/10 (30%) | 107.6 | 99% | 97% |
| Dart (avg of 1000 random draws) | 1.0/10 (10%) | 0.9/10 (9%) | 38.7 | - | - |
| Snap-share heuristic | 1/10 (10%) | 1/10 (10%) | 29.2 | 55% | 59% |
| Last week's points | 1/10 (10%) | 0/10 (0%) | 49.3 | 55% | 20% |

### Top 25 -- sun run (2026-11-01 15:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 6/25 (24%) | 4/25 (16%) | 181.2 | 86% | 90% |
| O.D.D.S. shadow (10-feature) | 6/25 (24%) | 5/25 (20%) | 205.9 | 86% | 97% |
| Dart (avg of 1000 random draws) | 4.2/25 (17%) | 2.2/25 (9%) | 98.7 | - | - |
| Snap-share heuristic | 3/25 (12%) | 1/25 (4%) | 72.7 | 24% | 21% |
| Last week's points | 6/25 (24%) | 2/25 (8%) | 152.5 | 86% | 49% |

## Caveats

- **Known leak (flagged, not yet fixed):** the production model's `starter_absent_proxy` feature is built from whether the presumed starter played in the very week being predicted for training rows, but is empty at live scoring time. It's a small feature (~1% of importance) but it breaks the 'strictly before the week' rule and creates train/serve skew. A retrain with it computed strictly as-of (or removed) is queued; the tracker will log the fixed model as a new model_version so old vs fixed can be compared.
- **Scoring mismatch (flagged, not yet fixed):** this tracker scores every hit under the league's real settings (5-pt passing TDs; from scoring version 2 also sacks and returns). O.D.D.S.'s own training labels were built with a 4-pt passing TD and without the sack and return scoring, which mainly affects how QB 'spikes' were defined. To be corrected in the same retrain.
- **Pick-sixes (not scored):** the league also docks 4 points for a pick-six thrown. nflverse's weekly stats file has no column for it, so it is the one league rule this tracker does not apply; in weeks 1-5 it accounted for the only 2 of 675 rostered player-weeks where the tracker's points differed from Yahoo's own.
