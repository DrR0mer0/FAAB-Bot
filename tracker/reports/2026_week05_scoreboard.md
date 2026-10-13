# O.D.D.S. vs the baselines -- 2026 Week 5

_Scored 2026-10-13 14:13 UTC; hit definition `b5b540cb6e`._

**How a pick is scored** (league settings: 0.5 PPR, 5-pt passing TDs, 6-pt rushing/receiving TDs, -2 INT, -2 fumbles lost, +2 per 2-pt conversion):
- **Top-24 finish** -- the player finishes top-24 at his position that week ("does it help me win").
- **Spike week** -- the player scores at least 1.5x his trailing 3-game average and at least 10 points ("does O.D.D.S. beat chance at its own job").
- **Dart percentile** -- where the model's result falls among N random draws from the same pool with the same position mix ("beat 85% of dart throws"). 50% is chance; ties count half.

## All eligible players

### Top 10 -- thu run (2026-10-08 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 4/10 (40%) | 3/10 (30%) | 82.7 | 92% | 96% |
| O.D.D.S. shadow (10-feature) | 3/10 (30%) | 2/10 (20%) | 61.0 | 78% | 81% |
| Dart (avg of 1000 random draws) | 2.1/10 (21%) | 1.1/10 (11%) | 55.6 | - | - |
| Snap-share heuristic | 4/10 (40%) | 2/10 (20%) | 84.7 | 92% | 81% |
| Last week's points | 5/10 (50%) | 2/10 (20%) | 115.0 | 98% | 81% |

### Top 25 -- thu run (2026-10-08 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 8/25 (32%) | 5/25 (20%) | 177.3 | 89% | 91% |
| O.D.D.S. shadow (10-feature) | 8/25 (32%) | 7/25 (28%) | 184.0 | 89% | 99% |
| Dart (avg of 1000 random draws) | 5.6/25 (22%) | 2.8/25 (11%) | 142.9 | - | - |
| Snap-share heuristic | 10/25 (40%) | 5/25 (20%) | 210.4 | 98% | 91% |
| Last week's points | 10/25 (40%) | 4/25 (16%) | 229.0 | 98% | 77% |

### Top 10 -- sun run (2026-10-11 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 4/10 (40%) | 3/10 (30%) | 88.2 | 90% | 95% |
| O.D.D.S. shadow (10-feature) | 4/10 (40%) | 3/10 (30%) | 77.2 | 90% | 95% |
| Dart (avg of 1000 random draws) | 2.3/10 (23%) | 1.2/10 (12%) | 58.6 | - | - |
| Snap-share heuristic | 6/10 (60%) | 3/10 (30%) | 114.2 | 99% | 95% |
| Last week's points | 5/10 (50%) | 1/10 (10%) | 105.0 | 97% | 45% |

### Top 25 -- sun run (2026-10-11 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 8/25 (32%) | 6/25 (24%) | 225.1 | 83% | 96% |
| O.D.D.S. shadow (10-feature) | 9/25 (36%) | 7/25 (28%) | 208.8 | 91% | 99% |
| Dart (avg of 1000 random draws) | 6.0/25 (24%) | 3.0/25 (12%) | 158.5 | - | - |
| Snap-share heuristic | 9/25 (36%) | 4/25 (16%) | 207.1 | 91% | 75% |
| Last week's points | 11/25 (44%) | 4/25 (16%) | 297.6 | 98% | 75% |

## Sleeper pool -- under 50% owned at pick time

_Pool restricted to players under 50% owned at the time of the run; every model re-picks from that smaller pool._

- **Under-50% pool, week 5 `thu` run:** 277 players, 1 of them with **ownership unknown** (no roster-% row for them in the snapshot used). Unknown is not treated as under 50% or as 0%: those players are kept in the pool and counted here rather than dropped.
- **Under-50% pool, week 5 `sun` run:** 218 players, 1 of them with **ownership unknown** (no roster-% row for them in the snapshot used). Unknown is not treated as under 50% or as 0%: those players are kept in the pool and counted here rather than dropped.

### Top 10 -- thu run (2026-10-08 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 2/10 (20%) | 2/10 (20%) | 66.6 | 71% | 88% |
| O.D.D.S. shadow (10-feature) | 1/10 (10%) | 1/10 (10%) | 36.5 | 39% | 59% |
| Dart (avg of 1000 random draws) | 1.4/10 (14%) | 0.9/10 (9%) | 38.2 | - | - |
| Snap-share heuristic | 2/10 (20%) | 2/10 (20%) | 46.8 | 71% | 88% |
| Last week's points | 3/10 (30%) | 1/10 (10%) | 81.4 | 90% | 59% |

### Top 25 -- thu run (2026-10-08 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 5/25 (20%) | 5/25 (20%) | 164.1 | 90% | 98% |
| O.D.D.S. shadow (10-feature) | 4/25 (16%) | 4/25 (16%) | 124.1 | 74% | 93% |
| Dart (avg of 1000 random draws) | 3.1/25 (12%) | 2.0/25 (8%) | 87.6 | - | - |
| Snap-share heuristic | 6/25 (24%) | 5/25 (20%) | 132.1 | 97% | 98% |
| Last week's points | 7/25 (28%) | 4/25 (16%) | 162.1 | 99% | 93% |

### Top 10 -- sun run (2026-10-11 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 2/10 (20%) | 2/10 (20%) | 89.2 | 64% | 82% |
| O.D.D.S. shadow (10-feature) | 3/10 (30%) | 3/10 (30%) | 86.5 | 87% | 96% |
| Dart (avg of 1000 random draws) | 1.7/10 (17%) | 1.1/10 (11%) | 47.0 | - | - |
| Snap-share heuristic | 2/10 (20%) | 2/10 (20%) | 56.2 | 64% | 82% |
| Last week's points | 3/10 (30%) | 1/10 (10%) | 80.6 | 87% | 51% |

### Top 25 -- sun run (2026-10-11 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 4/25 (16%) | 4/25 (16%) | 169.4 | 54% | 87% |
| O.D.D.S. shadow (10-feature) | 6/25 (24%) | 5/25 (20%) | 162.0 | 90% | 96% |
| Dart (avg of 1000 random draws) | 3.9/25 (16%) | 2.4/25 (10%) | 112.1 | - | - |
| Snap-share heuristic | 4/25 (16%) | 4/25 (16%) | 107.2 | 54% | 87% |
| Last week's points | 6/25 (24%) | 3/25 (12%) | 169.2 | 90% | 67% |

## Caveats

- **Known leak (flagged, not yet fixed):** the production model's `starter_absent_proxy` feature is built from whether the presumed starter played in the very week being predicted for training rows, but is empty at live scoring time. It's a small feature (~1% of importance) but it breaks the 'strictly before the week' rule and creates train/serve skew. A retrain with it computed strictly as-of (or removed) is queued; the tracker will log the fixed model as a new model_version so old vs fixed can be compared.
- **Scoring mismatch (flagged, not yet fixed):** this tracker scores every hit under the league's real settings (5-pt passing TDs). O.D.D.S.'s own training labels were built with a 4-pt passing TD, which mainly affects how QB 'spikes' were defined. To be corrected in the same retrain.
- **Pool rule:** the week 5 `thu` run was logged before players whose team has no game that week were excluded. 26 of its 403 pool players were on a bye (CAR, KC) and could not score. Top-10 picks among them: O.D.D.S. 3, shadow 0, snap-share heuristic 2, last week's points 3; about 6% of every dart draw. Its numbers are left as logged.
