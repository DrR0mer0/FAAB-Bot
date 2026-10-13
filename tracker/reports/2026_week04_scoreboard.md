# O.D.D.S. vs the baselines -- 2026 Week 4

_Scored 2026-10-13 17:35 UTC; hit definition `476e6708f0`._

**How a pick is scored** (league settings: 0.5 PPR, 5-pt passing TDs, 6-pt rushing/receiving TDs, -2 INT, -2 fumbles lost, +2 per 2-pt conversion, -0.5 per sack taken, 0.02 per return yard, 6-pt return and fumble-return TDs):
- **Top-24 finish** -- the player finishes top-24 at his position that week ("does it help me win").
- **Spike week** -- the player scores at least 1.5x his trailing 3-game average and at least 10 points ("does O.D.D.S. beat chance at its own job").
- **Dart percentile** -- where the model's result falls among N random draws from the same pool with the same position mix ("beat 85% of dart throws"). 50% is chance; ties count half.

## All eligible players

### Top 10 -- sun run (2026-10-04 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 5/10 (50%) | 4/10 (40%) | 127.3 | 97% | 98% |
| O.D.D.S. shadow (10-feature) | 6/10 (60%) | 5/10 (50%) | 135.8 | 100% | 100% |
| Dart (avg of 1000 random draws) | 2.3/10 (23%) | 1.4/10 (14%) | 70.4 | - | - |
| Snap-share heuristic | 5/10 (50%) | 2/10 (20%) | 116.7 | 97% | 72% |
| Last week's points | 7/10 (70%) | 1/10 (10%) | 177.9 | 100% | 38% |

### Top 25 -- sun run (2026-10-04 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 9/25 (36%) | 9/25 (36%) | 290.1 | 95% | 100% |
| O.D.D.S. shadow (10-feature) | 10/25 (40%) | 9/25 (36%) | 300.9 | 98% | 100% |
| Dart (avg of 1000 random draws) | 5.5/25 (22%) | 3.6/25 (14%) | 168.1 | - | - |
| Snap-share heuristic | 8/25 (32%) | 4/25 (16%) | 203.1 | 89% | 60% |
| Last week's points | 12/25 (48%) | 3/25 (12%) | 334.8 | 100% | 37% |

## Sleeper pool -- under 50% owned at pick time

_Pool restricted to players under 50% owned at the time of the run; every model re-picks from that smaller pool._

- **Under-50% pool, week 4 `sun` run:** 207 players. 1 pool player(s) with unknown ownership were left OUT of it -- the rule for this run only; later runs keep such players in, flagged.

### Top 10 -- sun run (2026-10-04 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 4/10 (40%) | 5/10 (50%) | 129.4 | 100% | 100% |
| O.D.D.S. shadow (10-feature) | 3/10 (30%) | 2/10 (20%) | 110.6 | 97% | 85% |
| Dart (avg of 1000 random draws) | 1.0/10 (10%) | 1.0/10 (10%) | 37.3 | - | - |
| Snap-share heuristic | 2/10 (20%) | 1/10 (10%) | 45.5 | 83% | 55% |
| Last week's points | 2/10 (20%) | 1/10 (10%) | 81.3 | 83% | 55% |

### Top 25 -- sun run (2026-10-04 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 6/25 (24%) | 6/25 (24%) | 201.4 | 98% | 100% |
| O.D.D.S. shadow (10-feature) | 4/25 (16%) | 3/25 (12%) | 181.9 | 80% | 70% |
| Dart (avg of 1000 random draws) | 2.8/25 (11%) | 2.3/25 (9%) | 90.9 | - | - |
| Snap-share heuristic | 3/25 (12%) | 2/25 (8%) | 76.2 | 58% | 43% |
| Last week's points | 3/25 (12%) | 2/25 (8%) | 151.3 | 58% | 43% |

## Caveats

- **Known leak (flagged, not yet fixed):** the production model's `starter_absent_proxy` feature is built from whether the presumed starter played in the very week being predicted for training rows, but is empty at live scoring time. It's a small feature (~1% of importance) but it breaks the 'strictly before the week' rule and creates train/serve skew. A retrain with it computed strictly as-of (or removed) is queued; the tracker will log the fixed model as a new model_version so old vs fixed can be compared.
- **Scoring mismatch (flagged, not yet fixed):** this tracker scores every hit under the league's real settings (5-pt passing TDs; from scoring version 2 also sacks and returns). O.D.D.S.'s own training labels were built with a 4-pt passing TD and without the sack and return scoring, which mainly affects how QB 'spikes' were defined. To be corrected in the same retrain.
- **Pick-sixes (not scored):** the league also docks 4 points for a pick-six thrown. nflverse's weekly stats file has no column for it, so it is the one league rule this tracker does not apply; in weeks 1-5 it accounted for the only 2 of 675 rostered player-weeks where the tracker's points differed from Yahoo's own.
- **Pool rule:** the week 4 `sun` run was logged before the Out/Doubtful pool rule existed. Players already ruled Out or Doubtful were still in its pool and could be picked -- by any model and by the dart draws -- so some of its picks could not score. Its numbers are left as logged.
