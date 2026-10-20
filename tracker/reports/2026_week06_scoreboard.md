# O.D.D.S. vs the baselines -- 2026 Week 6

_Scored 2026-10-20 14:25 UTC; hit definition `476e6708f0`._

**How a pick is scored** (league settings: 0.5 PPR, 5-pt passing TDs, 6-pt rushing/receiving TDs, -2 INT, -2 fumbles lost, +2 per 2-pt conversion, -0.5 per sack taken, 0.02 per return yard, 6-pt return and fumble-return TDs):
- **Top-24 finish** -- the player finishes top-24 at his position that week ("does it help me win").
- **Spike week** -- the player scores at least 1.5x his trailing 3-game average and at least 10 points ("does O.D.D.S. beat chance at its own job").
- **Dart percentile** -- where the model's result falls among N random draws from the same pool with the same position mix ("beat 85% of dart throws"). 50% is chance; ties count half.

## All eligible players

### Top 10 -- thu run (2026-10-15 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 1/10 (10%) | 0/10 (0%) | 51.3 | 24% | 17% |
| O.D.D.S. shadow (10-feature) | 3/10 (30%) | 2/10 (20%) | 78.1 | 79% | 82% |
| Dart (avg of 1000 random draws) | 2.0/10 (20%) | 1.0/10 (10%) | 53.2 | - | - |
| Snap-share heuristic | 3/10 (30%) | 1/10 (10%) | 54.9 | 79% | 53% |
| Last week's points | 4/10 (40%) | 0/10 (0%) | 124.3 | 93% | 17% |

### Top 25 -- thu run (2026-10-15 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 3/25 (12%) | 1/25 (4%) | 97.7 | 13% | 18% |
| O.D.D.S. shadow (10-feature) | 9/25 (36%) | 6/25 (24%) | 216.4 | 96% | 98% |
| Dart (avg of 1000 random draws) | 5.3/25 (21%) | 2.5/25 (10%) | 137.7 | - | - |
| Snap-share heuristic | 5/25 (20%) | 2/25 (8%) | 119.5 | 46% | 40% |
| Last week's points | 12/25 (48%) | 2/25 (8%) | 312.2 | 100% | 40% |

### Top 10 -- sun run (2026-10-18 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 2/10 (20%) | 1/10 (10%) | 52.3 | 47% | 50% |
| O.D.D.S. shadow (10-feature) | 3/10 (30%) | 2/10 (20%) | 84.9 | 74% | 81% |
| Dart (avg of 1000 random draws) | 2.2/10 (22%) | 1.1/10 (11%) | 58.8 | - | - |
| Snap-share heuristic | 3/10 (30%) | 1/10 (10%) | 54.2 | 74% | 50% |
| Last week's points | 5/10 (50%) | 1/10 (10%) | 131.9 | 98% | 50% |

### Top 25 -- sun run (2026-10-18 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 6/25 (24%) | 4/25 (16%) | 155.8 | 34% | 80% |
| O.D.D.S. shadow (10-feature) | 10/25 (40%) | 6/25 (24%) | 236.1 | 93% | 98% |
| Dart (avg of 1000 random draws) | 6.9/25 (28%) | 2.7/25 (11%) | 162.8 | - | - |
| Snap-share heuristic | 6/25 (24%) | 3/25 (12%) | 173.0 | 34% | 60% |
| Last week's points | 14/25 (56%) | 3/25 (12%) | 341.1 | 100% | 60% |

## Sleeper pool -- under 50% owned at pick time

_Pool restricted to players under 50% owned at the time of the run; every model re-picks from that smaller pool._

- **Under-50% pool, week 6 `thu` run:** 260 players, 1 of them with **ownership unknown** (no roster-% row for them in the snapshot used). Unknown is not treated as under 50% or as 0%: those players are kept in the pool and counted here rather than dropped.
- **Under-50% pool, week 6 `sun` run:** 192 players, 1 of them with **ownership unknown** (no roster-% row for them in the snapshot used). Unknown is not treated as under 50% or as 0%: those players are kept in the pool and counted here rather than dropped.

### Top 10 -- thu run (2026-10-15 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 0/10 (0%) | 0/10 (0%) | 21.1 | 11% | 22% |
| O.D.D.S. shadow (10-feature) | 5/10 (50%) | 5/10 (50%) | 103.4 | 100% | 100% |
| Dart (avg of 1000 random draws) | 1.4/10 (14%) | 0.8/10 (8%) | 36.9 | - | - |
| Snap-share heuristic | 1/10 (10%) | 1/10 (10%) | 16.8 | 40% | 63% |
| Last week's points | 4/10 (40%) | 4/10 (40%) | 93.9 | 98% | 100% |

### Top 25 -- thu run (2026-10-15 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 8/25 (32%) | 5/25 (20%) | 171.6 | 99% | 98% |
| O.D.D.S. shadow (10-feature) | 7/25 (28%) | 6/25 (24%) | 158.0 | 95% | 100% |
| Dart (avg of 1000 random draws) | 4.2/25 (17%) | 2.0/25 (8%) | 97.4 | - | - |
| Snap-share heuristic | 4/25 (16%) | 3/25 (12%) | 87.3 | 46% | 78% |
| Last week's points | 8/25 (32%) | 4/25 (16%) | 176.7 | 99% | 92% |

### Top 10 -- sun run (2026-10-18 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 1/10 (10%) | 1/10 (10%) | 38.3 | 35% | 60% |
| O.D.D.S. shadow (10-feature) | 4/10 (40%) | 3/10 (30%) | 79.9 | 99% | 98% |
| Dart (avg of 1000 random draws) | 1.5/10 (15%) | 0.8/10 (8%) | 41.5 | - | - |
| Snap-share heuristic | 2/10 (20%) | 2/10 (20%) | 32.1 | 69% | 88% |
| Last week's points | 3/10 (30%) | 3/10 (30%) | 81.1 | 91% | 98% |

### Top 25 -- sun run (2026-10-18 14:00 UTC)

| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |
|---|---:|---:|---:|---:|---:|
| O.D.D.S. (13-feature) | 7/25 (28%) | 5/25 (20%) | 172.6 | 92% | 96% |
| O.D.D.S. shadow (10-feature) | 6/25 (24%) | 4/25 (16%) | 152.9 | 79% | 88% |
| Dart (avg of 1000 random draws) | 4.7/25 (19%) | 2.4/25 (10%) | 111.8 | - | - |
| Snap-share heuristic | 4/25 (16%) | 3/25 (12%) | 115.7 | 33% | 68% |
| Last week's points | 9/25 (36%) | 5/25 (20%) | 177.6 | 99% | 96% |

## Caveats

- **Known leak (flagged, not yet fixed):** the production model's `starter_absent_proxy` feature is built from whether the presumed starter played in the very week being predicted for training rows, but is empty at live scoring time. It's a small feature (~1% of importance) but it breaks the 'strictly before the week' rule and creates train/serve skew. A retrain with it computed strictly as-of (or removed) is queued; the tracker will log the fixed model as a new model_version so old vs fixed can be compared.
- **Scoring mismatch (flagged, not yet fixed):** this tracker scores every hit under the league's real settings (5-pt passing TDs; from scoring version 2 also sacks and returns). O.D.D.S.'s own training labels were built with a 4-pt passing TD and without the sack and return scoring, which mainly affects how QB 'spikes' were defined. To be corrected in the same retrain.
- **Pick-sixes (not scored):** the league also docks 4 points for a pick-six thrown. nflverse's weekly stats file has no column for it, so it is the one league rule this tracker does not apply; in weeks 1-5 it accounted for the only 2 of 675 rostered player-weeks where the tracker's points differed from Yahoo's own.
- **Pool rule (not on an NFL roster):** the week 6 `thu` run was logged before players not on an NFL roster were excluded. 20 of its 372 pool players were released, retired or unsigned per nflverse's weekly roster, and 1 more (Josh Jacobs) was on the exempt list; none of them could score. Tracked picks among them -- O.D.D.S. (13-feature): under-50% top 25 #20 Andy Dalton; O.D.D.S. shadow (10-feature): none; Snap-share heuristic: all top 10 #5 KhaDarel Hodge, all top 25 #7 KhaDarel Hodge, under-50% top 10 #5 KhaDarel Hodge, under-50% top 25 #5 KhaDarel Hodge, under-50% top 25 #16 Albert Okwuegbunam, under-50% top 25 #17 Andy Dalton, under-50% top 25 #22 Teddy Bridgewater; Last week's points: none. They were about 6% of the pool, so of every dart draw. Its numbers are left as logged.
