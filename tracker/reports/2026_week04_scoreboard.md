# O.D.D.S. vs the baselines -- 2026 Week 4

_Scored 2026-10-06 14:46 UTC; hit definition `b5b540cb6e`._

**How a pick is scored** (league settings: 0.5 PPR, 5-pt passing TDs, 6-pt rushing/receiving TDs, -2 INT, -2 fumbles lost, +2 per 2-pt conversion):
- **Top-24 finish** -- the player finishes top-24 at his position that week ("does it help me win").
- **Spike week** -- the player scores at least 1.5x his trailing 3-game average and at least 10 points ("does O.D.D.S. beat chance at its own job").
- **Dart percentile** -- where the model's result falls among N random draws from the same pool with the same position mix ("beat 85% of dart throws"). 50% is chance; ties count half.

## All eligible players

### Top 10 -- sun run (2026-10-04 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 5/10 (50%) | 4/10 (40%) | 127.3 | 97% | 98% |
| O.D.D.S. shadow (10-feature) | 6/10 (60%) | 5/10 (50%) | 135.8 | 100% | 100% |
| Dart (avg of 1000 random draws) | 2.3/10 (23%) | 1.4/10 (14%) | 68.5 | - | - |
| Snap-share heuristic | 5/10 (50%) | 2/10 (20%) | 115.6 | 97% | 72% |
| Last week's points | 8/10 (80%) | 1/10 (10%) | 177.9 | 100% | 38% |

### Top 25 -- sun run (2026-10-04 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 10/25 (40%) | 9/25 (36%) | 292.6 | 98% | 100% |
| O.D.D.S. shadow (10-feature) | 10/25 (40%) | 9/25 (36%) | 300.9 | 98% | 100% |
| Dart (avg of 1000 random draws) | 5.4/25 (21%) | 3.6/25 (14%) | 163.5 | - | - |
| Snap-share heuristic | 8/25 (32%) | 4/25 (16%) | 197.9 | 91% | 60% |
| Last week's points | 13/25 (52%) | 3/25 (12%) | 335.9 | 100% | 37% |

## Sleeper pool -- under 50% owned at pick time

_Pool restricted to players under 50% owned at the time of the run; every model re-picks from that smaller pool._

- **Under-50% pool, week 4 `sun` run:** 207 players. 1 pool player(s) with unknown ownership were left OUT of it -- the rule for this run only; later runs keep such players in, flagged.

### Top 10 -- sun run (2026-10-04 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 4/10 (40%) | 5/10 (50%) | 128.5 | 100% | 100% |
| O.D.D.S. shadow (10-feature) | 3/10 (30%) | 2/10 (20%) | 110.6 | 98% | 85% |
| Dart (avg of 1000 random draws) | 0.9/10 (9%) | 1.0/10 (10%) | 34.5 | - | - |
| Snap-share heuristic | 2/10 (20%) | 1/10 (10%) | 40.6 | 88% | 55% |
| Last week's points | 2/10 (20%) | 1/10 (10%) | 83.2 | 88% | 55% |

### Top 25 -- sun run (2026-10-04 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 6/25 (24%) | 6/25 (24%) | 200.5 | 99% | 100% |
| O.D.D.S. shadow (10-feature) | 4/25 (16%) | 3/25 (12%) | 180.9 | 86% | 70% |
| Dart (avg of 1000 random draws) | 2.5/25 (10%) | 2.3/25 (9%) | 85.5 | - | - |
| Snap-share heuristic | 2/25 (8%) | 2/25 (8%) | 69.0 | 38% | 43% |
| Last week's points | 3/25 (12%) | 2/25 (8%) | 148.4 | 66% | 43% |

## Caveats

- **Known leak (flagged, not yet fixed):** the production model's `starter_absent_proxy` feature is built from whether the presumed starter played in the very week being predicted for training rows, but is empty at live scoring time. It's a small feature (~1% of importance) but it breaks the 'strictly before the week' rule and creates train/serve skew. A retrain with it computed strictly as-of (or removed) is queued; the tracker will log the fixed model as a new model_version so old vs fixed can be compared.
- **Scoring mismatch (flagged, not yet fixed):** this tracker scores every hit under the league's real settings (5-pt passing TDs). O.D.D.S.'s own training labels were built with a 4-pt passing TD, which mainly affects how QB 'spikes' were defined. To be corrected in the same retrain.
- **Pool rule:** the week 4 `sun` run was logged before the Out/Doubtful pool rule existed. Players already ruled Out or Doubtful were still in its pool and could be picked -- by any model and by the dart draws -- so some of its picks could not score. Its numbers are left as logged.
