# O.D.D.S. vs the baselines -- 2026 Week 5

_Scored 2026-10-13 17:35 UTC; hit definition `476e6708f0`._

**How a pick is scored** (league settings: 0.5 PPR, 5-pt passing TDs, 6-pt rushing/receiving TDs, -2 INT, -2 fumbles lost, +2 per 2-pt conversion, -0.5 per sack taken, 0.02 per return yard, 6-pt return and fumble-return TDs):
- **Top-24 finish** -- the player finishes top-24 at his position that week ("does it help me win").
- **Spike week** -- the player scores at least 1.5x his trailing 3-game average and at least 10 points ("does O.D.D.S. beat chance at its own job").
- **Dart percentile** -- where the model's result falls among N random draws from the same pool with the same position mix ("beat 85% of dart throws"). 50% is chance; ties count half.

## All eligible players

### Top 10 -- thu run (2026-10-08 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 4/10 (40%) | 3/10 (30%) | 82.9 | 92% | 96% |
| O.D.D.S. shadow (10-feature) | 3/10 (30%) | 2/10 (20%) | 63.4 | 77% | 81% |
| Dart (avg of 1000 random draws) | 2.1/10 (21%) | 1.1/10 (11%) | 57.6 | - | - |
| Snap-share heuristic | 4/10 (40%) | 2/10 (20%) | 86.8 | 92% | 81% |
| Last week's points | 4/10 (40%) | 2/10 (20%) | 114.0 | 92% | 81% |

### Top 25 -- thu run (2026-10-08 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 7/25 (28%) | 5/25 (20%) | 180.0 | 76% | 91% |
| O.D.D.S. shadow (10-feature) | 9/25 (36%) | 7/25 (28%) | 189.9 | 95% | 99% |
| Dart (avg of 1000 random draws) | 5.6/25 (22%) | 2.8/25 (11%) | 146.6 | - | - |
| Snap-share heuristic | 10/25 (40%) | 5/25 (20%) | 212.0 | 98% | 91% |
| Last week's points | 9/25 (36%) | 4/25 (16%) | 227.2 | 95% | 77% |

### Top 10 -- sun run (2026-10-11 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 4/10 (40%) | 3/10 (30%) | 90.6 | 89% | 95% |
| O.D.D.S. shadow (10-feature) | 4/10 (40%) | 3/10 (30%) | 79.6 | 89% | 95% |
| Dart (avg of 1000 random draws) | 2.3/10 (23%) | 1.2/10 (12%) | 61.2 | - | - |
| Snap-share heuristic | 6/10 (60%) | 3/10 (30%) | 117.8 | 99% | 95% |
| Last week's points | 4/10 (40%) | 1/10 (10%) | 105.7 | 89% | 45% |

### Top 25 -- sun run (2026-10-11 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 8/25 (32%) | 6/25 (24%) | 222.0 | 82% | 96% |
| O.D.D.S. shadow (10-feature) | 10/25 (40%) | 7/25 (28%) | 214.7 | 96% | 99% |
| Dart (avg of 1000 random draws) | 6.1/25 (25%) | 3.0/25 (12%) | 162.6 | - | - |
| Snap-share heuristic | 9/25 (36%) | 4/25 (16%) | 213.4 | 91% | 75% |
| Last week's points | 10/25 (40%) | 4/25 (16%) | 296.8 | 96% | 75% |

## Sleeper pool -- under 50% owned at pick time

_Pool restricted to players under 50% owned at the time of the run; every model re-picks from that smaller pool._

- **Under-50% pool, week 5 `thu` run:** 277 players, 1 of them with **ownership unknown** (no roster-% row for them in the snapshot used). Unknown is not treated as under 50% or as 0%: those players are kept in the pool and counted here rather than dropped.
- **Under-50% pool, week 5 `sun` run:** 218 players, 1 of them with **ownership unknown** (no roster-% row for them in the snapshot used). Unknown is not treated as under 50% or as 0%: those players are kept in the pool and counted here rather than dropped.

### Top 10 -- thu run (2026-10-08 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 1/10 (10%) | 2/10 (20%) | 64.5 | 36% | 88% |
| O.D.D.S. shadow (10-feature) | 2/10 (20%) | 1/10 (10%) | 40.4 | 68% | 59% |
| Dart (avg of 1000 random draws) | 1.5/10 (15%) | 0.9/10 (9%) | 39.0 | - | - |
| Snap-share heuristic | 2/10 (20%) | 2/10 (20%) | 48.4 | 68% | 88% |
| Last week's points | 3/10 (30%) | 1/10 (10%) | 80.1 | 89% | 59% |

### Top 25 -- thu run (2026-10-08 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 4/25 (16%) | 5/25 (20%) | 164.1 | 71% | 98% |
| O.D.D.S. shadow (10-feature) | 5/25 (20%) | 4/25 (16%) | 131.0 | 88% | 93% |
| Dart (avg of 1000 random draws) | 3.2/25 (13%) | 2.0/25 (8%) | 92.5 | - | - |
| Snap-share heuristic | 6/25 (24%) | 5/25 (20%) | 136.7 | 96% | 98% |
| Last week's points | 7/25 (28%) | 4/25 (16%) | 167.5 | 99% | 93% |

### Top 10 -- sun run (2026-10-11 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 2/10 (20%) | 2/10 (20%) | 87.8 | 59% | 82% |
| O.D.D.S. shadow (10-feature) | 4/10 (40%) | 3/10 (30%) | 88.7 | 96% | 96% |
| Dart (avg of 1000 random draws) | 1.8/10 (18%) | 1.1/10 (11%) | 47.6 | - | - |
| Snap-share heuristic | 2/10 (20%) | 2/10 (20%) | 59.5 | 59% | 82% |
| Last week's points | 3/10 (30%) | 1/10 (10%) | 82.8 | 84% | 51% |

### Top 25 -- sun run (2026-10-11 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 4/25 (16%) | 4/25 (16%) | 165.4 | 44% | 87% |
| O.D.D.S. shadow (10-feature) | 7/25 (28%) | 5/25 (20%) | 168.9 | 94% | 96% |
| Dart (avg of 1000 random draws) | 4.3/25 (17%) | 2.4/25 (10%) | 114.3 | - | - |
| Snap-share heuristic | 4/25 (16%) | 4/25 (16%) | 111.8 | 44% | 87% |
| Last week's points | 6/25 (24%) | 3/25 (12%) | 174.9 | 84% | 67% |

## Caveats

- **Known leak (flagged, not yet fixed):** the production model's `starter_absent_proxy` feature is built from whether the presumed starter played in the very week being predicted for training rows, but is empty at live scoring time. It's a small feature (~1% of importance) but it breaks the 'strictly before the week' rule and creates train/serve skew. A retrain with it computed strictly as-of (or removed) is queued; the tracker will log the fixed model as a new model_version so old vs fixed can be compared.
- **Scoring mismatch (flagged, not yet fixed):** this tracker scores every hit under the league's real settings (5-pt passing TDs; from scoring version 2 also sacks and returns). O.D.D.S.'s own training labels were built with a 4-pt passing TD and without the sack and return scoring, which mainly affects how QB 'spikes' were defined. To be corrected in the same retrain.
- **Pick-sixes (not scored):** the league also docks 4 points for a pick-six thrown. nflverse's weekly stats file has no column for it, so it is the one league rule this tracker does not apply; in weeks 1-5 it accounted for the only 2 of 675 rostered player-weeks where the tracker's points differed from Yahoo's own.
- **Pool rule:** the week 5 `thu` run was logged before players whose team has no game that week were excluded. 26 of its 403 pool players were on a bye (CAR, KC) and could not score. Top-10 picks among them: O.D.D.S. 3, shadow 0, snap-share heuristic 2, last week's points 3; about 6% of every dart draw. Its numbers are left as logged.
