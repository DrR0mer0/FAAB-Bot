# Verification: 2026 week 8

- Week base rate, QB/RB/WR/TE (shared by production and shadow): 37/296 eligible players spiked (0.1250)
- Week base rate, all positions (kept for continuity; pooled lift is measured against the QB/RB/WR/TE rate above): 37/946 (0.0391)
- Cumulative PRODUCTION across 8 verified week(s): P@10 14/80 = 0.1750, P@25 39/200 = 0.1950
- Cumulative head-to-head (precision@10, 6 week(s) with both): production 13/60 = 0.2167, shadow 19/60 = 0.3167

> **Scoring note (added 2026-11-03; nothing below is changed).** The official record is nflverse's: Jalen Coker (WR, CAR, production #8) scored 10.6 in the DB labels, a spike (baseline 5.63, threshold 10.00). Yahoo scores him 8.6: on CAR's last play he caught a lateral and fumbled, GB recovered, and the league docks 2 for a fumble lost. nflverse's play-by-play charges him with the fumble and its weekly `fumbles_lost_total` counts it, but the receiving, rushing and sack fumble columns the loader sums don't. At 8.6 he is not a spike, and this week would read: QB/RB/WR/TE base rate 36/296 (0.1216), all positions 36/946; production precision@10 2/10 (1.6x), @25 7/25 (2.3x), WR 2/10; shadow unchanged at 4/10 (3.3x) and 5/25 (1.6x); cumulative production precision@10 13/80 and @25 38/200, head-to-head production 12/60 vs shadow 19/60. Decision 17 is applied as written, from `verification_log.json` as logged (week 8: production 3, shadow 4); this note rides alongside. If nflverse corrects the stats, check-stats flags the week and the normal re-score applies.

## PRODUCTION: `odds_xgb_model_production.joblib` (commit `7f21e5c966e0d82d049181e3965d661425e01643`)

- Precision@10 (pooled): 3/10 = 0.3000  (2.4x QB/RB/WR/TE base rate)
- Precision@25 (pooled): 8/25 = 0.3200  (2.6x QB/RB/WR/TE base rate)
- Of the top 25: 8 actually spiked
- DNP: 4, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 1/10 | 0.1000 | 0.1471 | 0.7x |
| RB | 3/10 | 0.3000 | 0.1154 | 2.6x |
| WR | 3/10 | 0.3000 | 0.1322 | 2.3x |
| TE | 2/10 | 0.2000 | 0.1111 | 1.8x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Emeka Egbuka | WR | TB | 0.7034 | -- | 10.27 | 15.40 | DNP |
| 2 | Jaylen Waddle | WR | DEN | 0.7019 | 11.4 | 5.00 | 10.00 | ✓ |
| 3 | J.K. Dobbins | RB | DEN | 0.6918 | 1.3 | 11.60 | 17.40 | ✗ |
| 4 | Blake Corum | RB | LA | 0.6898 | 13.3 | 14.77 | 22.15 | ✗ |
| 5 | CeeDee Lamb | WR | DAL | 0.6870 | 9.3 | 11.77 | 17.65 | ✗ |
| 6 | Omar Cooper Jr. | WR | NYJ | 0.6857 | -- | 14.27 | 21.40 | DNP |
| 7 | Jordan Mason | RB | MIN | 0.6765 | 2.0 | 9.37 | 14.05 | ✗ |
| 8 | Jalen Coker | WR | CAR | 0.6731 | 10.6 | 5.63 | 10.00 | ✓ |
| 9 | Garrett Wilson | WR | NYJ | 0.6723 | 11.3 | 9.97 | 14.95 | ✗ |
| 10 | Josh Jacobs | RB | GB | 0.6683 | 20.2 | 7.40 | 11.10 | ✓ |
| 11 | Bam Knight | RB | ARI | 0.6651 | 0.3 | 6.47 | 10.00 | ✗ |
| 12 | Adonai Mitchell | WR | NYJ | 0.6651 | 23.7 | 5.60 | 10.00 | ✓ |
| 13 | Tyler Allgeier | RB | ARI | 0.6649 | 33.6 | 4.77 | 10.00 | ✓ |
| 14 | KeAndre Lambert-Smith | WR | LAC | 0.6648 | 0.0 | 2.37 | 10.00 | ✗ |
| 15 | A.J. Brown | WR | NE | 0.6622 | -- | 4.87 | 10.00 | DNP |
| 16 | Treylon Burks | WR | WAS | 0.6599 | 9.6 | 7.80 | 11.70 | ✗ |
| 17 | Wan'Dale Robinson | WR | TEN | 0.6589 | 12.7 | 4.90 | 10.00 | ✓ |
| 18 | Parker Washington | WR | JAX | 0.6568 | 3.1 | 12.43 | 18.65 | ✗ |
| 19 | Antonio Williams | WR | WAS | 0.6560 | 7.9 | 4.90 | 10.00 | ✗ |
| 20 | Rashee Rice | WR | KC | 0.6545 | 14.8 | 10.50 | 15.75 | ✗ |
| 21 | DJ Moore | WR | BUF | 0.6506 | 14.6 | 3.93 | 10.00 | ✓ |
| 22 | Jakobi Meyers | WR | JAX | 0.6495 | 3.4 | 6.77 | 10.15 | ✗ |
| 23 | Dontayvion Wicks | WR | PHI | 0.6485 | -- | 5.17 | 10.00 | DNP |
| 24 | Ashton Jeanty | RB | LV | 0.6485 | 8.8 | 13.53 | 20.30 | ✗ |
| 25 | Josh Downs | WR | IND | 0.6450 | 10.3 | 5.23 | 10.00 | ✓ |

## SHADOW: `odds_xgb_model_production_10feature.joblib` (commit `262a04fda4a39cb4c16e568f1d28034a399169e7`)

- Precision@10 (pooled): 4/10 = 0.4000  (3.2x QB/RB/WR/TE base rate)
- Precision@25 (pooled): 5/25 = 0.2000  (1.6x QB/RB/WR/TE base rate)
- Of the top 25: 5 actually spiked
- DNP: 3, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 1/10 | 0.1000 | 0.1471 | 0.7x |
| RB | 3/10 | 0.3000 | 0.1154 | 2.6x |
| WR | 1/10 | 0.1000 | 0.1322 | 0.8x |
| TE | 2/10 | 0.2000 | 0.1111 | 1.8x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Josh Jacobs | RB | GB | 0.3363 | 20.2 | 7.40 | 11.10 | ✓ |
| 2 | Blake Corum | RB | LA | 0.3167 | 13.3 | 14.77 | 22.15 | ✗ |
| 3 | Omar Cooper Jr. | WR | NYJ | 0.2920 | -- | 14.27 | 21.40 | DNP |
| 4 | Rico Dowdle | RB | PIT | 0.2840 | 11.6 | 7.80 | 11.70 | ✗ |
| 5 | Tyler Allgeier | RB | ARI | 0.2804 | 33.6 | 4.77 | 10.00 | ✓ |
| 6 | Jonah Coleman | RB | DEN | 0.2785 | 18.4 | 7.07 | 10.60 | ✓ |
| 7 | Bam Knight | RB | ARI | 0.2767 | 0.3 | 6.47 | 10.00 | ✗ |
| 8 | Carnell Tate | WR | TEN | 0.2740 | -- | 8.17 | 12.25 | DNP |
| 9 | Malik Washington | WR | MIA | 0.2725 | 12.5 | 7.87 | 11.80 | ✓ |
| 10 | Keon Coleman | WR | BUF | 0.2713 | 0.9 | 13.90 | 20.85 | ✗ |
| 11 | KC Concepcion | WR | CLE | 0.2698 | 5.2 | 8.70 | 13.05 | ✗ |
| 12 | CeeDee Lamb | WR | DAL | 0.2697 | 9.3 | 11.77 | 17.65 | ✗ |
| 13 | DeVonta Smith | WR | PHI | 0.2663 | -- | 16.40 | 24.60 | DNP |
| 14 | Puka Nacua | WR | LA | 0.2657 | 19.5 | 19.33 | 29.00 | ✗ |
| 15 | Kendrick Bourne | WR | ARI | 0.2653 | 3.4 | 6.37 | 10.00 | ✗ |
| 16 | Ryan Flournoy | WR | DAL | 0.2612 | 11.6 | 11.00 | 16.50 | ✗ |
| 17 | Antonio Williams | WR | WAS | 0.2603 | 7.9 | 4.90 | 10.00 | ✗ |
| 18 | Justin Jefferson | WR | MIN | 0.2602 | 11.8 | 8.80 | 13.20 | ✗ |
| 19 | Jaxon Smith-Njigba | WR | SEA | 0.2601 | 20.8 | 22.17 | 33.25 | ✗ |
| 20 | Ja'Marr Chase | WR | CIN | 0.2600 | 25.6 | 11.87 | 17.80 | ✓ |
| 21 | Ashton Jeanty | RB | LV | 0.2594 | 8.8 | 13.53 | 20.30 | ✗ |
| 22 | Jordan Mason | RB | MIN | 0.2574 | 2.0 | 9.37 | 14.05 | ✗ |
| 23 | Makai Lemon | WR | PHI | 0.2572 | 8.7 | 8.80 | 13.20 | ✗ |
| 24 | Jordan Addison | WR | MIN | 0.2572 | 8.1 | 9.30 | 13.95 | ✗ |
| 25 | Khalil Shakir | WR | BUF | 0.2569 | 8.9 | 10.20 | 15.30 | ✗ |
