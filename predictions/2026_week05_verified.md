# Verification: 2026 week 5

- Week base rate (shared by production and shadow): 44/996 eligible players spiked (0.0442)
- Cumulative PRODUCTION across 5 verified week(s): P@10 9/50 = 0.1800, P@25 28/125 = 0.2240
- Cumulative head-to-head (precision@10, 3 week(s) with both): production 8/30 = 0.2667, shadow 11/30 = 0.3667

## PRODUCTION: `odds_xgb_model_production.joblib` (commit `7f21e5c966e0d82d049181e3965d661425e01643`)

- Precision@10 (pooled): 3/10 = 0.3000  (6.8x base rate)
- Precision@25 (pooled): 5/25 = 0.2000  (4.5x base rate)
- Of the top 25: 5 actually spiked
- DNP: 7, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 4/10 | 0.4000 | 0.2647 | 1.5x |
| RB | 3/10 | 0.3000 | 0.1429 | 2.1x |
| WR | 2/10 | 0.2000 | 0.1154 | 1.7x |
| TE | 2/10 | 0.2000 | 0.1013 | 2.0x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Terry McLaurin | WR | WAS | 0.7530 | 8.2 | 8.37 | 12.55 | ✗ |
| 2 | Parker Washington | WR | JAX | 0.7129 | 27.4 | 8.77 | 13.15 | ✓ |
| 3 | Rashee Rice | WR | KC | 0.7045 | -- | 10.50 | 15.75 | DNP |
| 4 | TreVeyon Henderson | RB | NE | 0.6984 | 4.3 | 7.07 | 10.60 | ✗ |
| 5 | Jordan Addison | WR | MIN | 0.6891 | -- | 9.23 | 13.85 | DNP |
| 6 | Jacory Croskey-Merritt | RB | WAS | 0.6867 | 14.2 | 5.37 | 10.00 | ✓ |
| 7 | Xavier Worthy | WR | KC | 0.6786 | -- | 7.67 | 11.50 | DNP |
| 8 | Rhamondre Stevenson | RB | NE | 0.6769 | 16.0 | 7.77 | 11.65 | ✓ |
| 9 | Bryce Young | QB | CAR | 0.6757 | -- | 19.73 | 29.59 | DNP |
| 10 | Bhayshul Tuten | RB | JAX | 0.6753 | 10.6 | 13.10 | 19.65 | ✗ |
| 11 | J.K. Dobbins | RB | DEN | 0.6752 | 14.4 | 5.20 | 10.00 | ✓ |
| 12 | Keaton Mitchell | RB | LAC | 0.6745 | 4.4 | 8.47 | 12.70 | ✗ |
| 13 | Kenny Pickett | QB | CAR | 0.6694 | -- | 0.34 | 10.00 | DNP |
| 14 | Ollie Gordon II | RB | MIA | 0.6688 | 14.8 | 10.23 | 15.35 | ✗ |
| 15 | Darren Waller | TE | CAR | 0.6669 | -- | 9.83 | 14.75 | DNP |
| 16 | Brian Robinson | RB | ATL | 0.6620 | 1.6 | 14.63 | 21.95 | ✗ |
| 17 | Wan'Dale Robinson | WR | TEN | 0.6618 | 8.3 | 8.70 | 13.05 | ✗ |
| 18 | RJ Harvey | RB | DEN | 0.6610 | 0.7 | 9.37 | 14.05 | ✗ |
| 19 | Ryan Flournoy | WR | DAL | 0.6596 | 10.5 | 4.57 | 10.00 | ✓ |
| 20 | David Montgomery | RB | HOU | 0.6593 | 1.1 | 4.50 | 10.00 | ✗ |
| 21 | Justin Jefferson | WR | MIN | 0.6591 | 15.8 | 12.13 | 18.20 | ✗ |
| 22 | Patrick Mahomes | QB | KC | 0.6588 | -- | 20.64 | 30.96 | DNP |
| 23 | Khalil Shakir | WR | BUF | 0.6583 | 9.5 | 5.37 | 10.00 | ✗ |
| 24 | Courtland Sutton | WR | DEN | 0.6582 | 8.8 | 3.67 | 10.00 | ✗ |
| 25 | Jakobi Meyers | WR | JAX | 0.6579 | 4.7 | 8.00 | 12.00 | ✗ |

## SHADOW: `odds_xgb_model_production_10feature.joblib` (commit `262a04fda4a39cb4c16e568f1d28034a399169e7`)

- Precision@10 (pooled): 2/10 = 0.2000  (4.5x base rate)
- Precision@25 (pooled): 7/25 = 0.2800  (6.3x base rate)
- Of the top 25: 7 actually spiked
- DNP: 5, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 5/10 | 0.5000 | 0.2647 | 1.9x |
| RB | 4/10 | 0.4000 | 0.1429 | 2.8x |
| WR | 2/10 | 0.2000 | 0.1154 | 1.7x |
| TE | 2/10 | 0.2000 | 0.1013 | 2.0x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | CeeDee Lamb | WR | DAL | 0.3398 | 1.9 | 26.27 | 39.40 | ✗ |
| 2 | RJ Harvey | RB | DEN | 0.3347 | 0.7 | 9.37 | 14.05 | ✗ |
| 3 | Keaton Mitchell | RB | LAC | 0.3261 | 4.4 | 8.47 | 12.70 | ✗ |
| 4 | Rhamondre Stevenson | RB | NE | 0.3244 | 16.0 | 7.77 | 11.65 | ✓ |
| 5 | Ollie Gordon II | RB | MIA | 0.3239 | 14.8 | 10.23 | 15.35 | ✗ |
| 6 | TreVeyon Henderson | RB | NE | 0.3136 | 4.3 | 7.07 | 10.60 | ✗ |
| 7 | Carnell Tate | WR | TEN | 0.3119 | 1.1 | 10.00 | 15.00 | ✗ |
| 8 | Brian Robinson | RB | ATL | 0.3004 | 1.6 | 14.63 | 21.95 | ✗ |
| 9 | Rico Dowdle | RB | PIT | 0.2977 | -- | 3.50 | 10.00 | DNP |
| 10 | MarShawn Lloyd | RB | GB | 0.2948 | 16.2 | 5.93 | 10.00 | ✓ |
| 11 | Braelon Allen | RB | NYJ | 0.2926 | 18.1 | 6.30 | 10.00 | ✓ |
| 12 | J.K. Dobbins | RB | DEN | 0.2924 | 14.4 | 5.20 | 10.00 | ✓ |
| 13 | Malik Nabers | WR | NYG | 0.2906 | 23.6 | 8.63 | 12.95 | ✓ |
| 14 | David Montgomery | RB | HOU | 0.2887 | 1.1 | 4.50 | 10.00 | ✗ |
| 15 | DK Metcalf | WR | PIT | 0.2884 | 3.8 | 9.77 | 14.65 | ✗ |
| 16 | Jacory Croskey-Merritt | RB | WAS | 0.2850 | 14.2 | 5.37 | 10.00 | ✓ |
| 17 | Wan'Dale Robinson | WR | TEN | 0.2848 | 8.3 | 8.70 | 13.05 | ✗ |
| 18 | Rashee Rice | WR | KC | 0.2814 | -- | 10.50 | 15.75 | DNP |
| 19 | Darren Waller | TE | CAR | 0.2760 | -- | 9.83 | 14.75 | DNP |
| 20 | Malik Washington | WR | MIA | 0.2756 | 9.1 | 8.27 | 12.40 | ✗ |
| 21 | Jameson Williams | WR | DET | 0.2746 | 17.7 | 8.13 | 12.20 | ✓ |
| 22 | Tyquan Thornton | WR | KC | 0.2728 | -- | 13.13 | 19.70 | DNP |
| 23 | Rachaad White | RB | WAS | 0.2728 | 2.5 | 8.53 | 12.80 | ✗ |
| 24 | Terry McLaurin | WR | WAS | 0.2720 | 8.2 | 8.37 | 12.55 | ✗ |
| 25 | Travis Kelce | TE | KC | 0.2713 | -- | 12.00 | 18.00 | DNP |
