# Verification: 2026 week 2

- Week base rate (shared by production and shadow): 43/953 eligible players spiked (0.0451)
- Cumulative PRODUCTION across 2 verified week(s): P@10 1/20 = 0.0500, P@25 9/50 = 0.1800

## PRODUCTION: `odds_xgb_model_production.joblib` (commit `262a04fda4a39cb4c16e568f1d28034a399169e7`)

- Precision@10 (pooled): 1/10 = 0.1000  (2.2x base rate)
- Precision@25 (pooled): 6/25 = 0.2400  (5.3x base rate)
- Of the top 25: 6 actually spiked
- DNP: 4, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 3/10 | 0.3000 | 0.2632 | 1.1x |
| RB | 1/10 | 0.1000 | 0.0750 | 1.3x |
| WR | 3/10 | 0.3000 | 0.1395 | 2.1x |
| TE | 2/10 | 0.2000 | 0.1324 | 1.5x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Corey Kiner | RB | NE | 0.3093 | 0.0 | 1.70 | 10.00 | ✗ |
| 2 | Blake Corum | RB | LA | 0.3023 | 9.7 | 4.87 | 10.00 | ✗ |
| 3 | James Cook | RB | BUF | 0.2944 | 20.4 | 6.03 | 10.00 | ✓ |
| 4 | David Montgomery | RB | HOU | 0.2903 | 3.4 | 12.80 | 19.20 | ✗ |
| 5 | Quinshon Judkins | RB | CLE | 0.2872 | 7.3 | 5.60 | 10.00 | ✗ |
| 6 | D'Andre Swift | RB | CHI | 0.2871 | 10.4 | 19.37 | 29.05 | ✗ |
| 7 | J.K. Dobbins | RB | DEN | 0.2861 | 3.6 | 6.30 | 10.00 | ✗ |
| 8 | Justice Hill | RB | BAL | 0.2792 | 2.8 | 3.70 | 10.00 | ✗ |
| 9 | Jordan Mason | RB | MIN | 0.2787 | -- | 7.63 | 11.45 | DNP |
| 10 | Kyren Williams | RB | LA | 0.2765 | 14.7 | 12.70 | 19.05 | ✗ |
| 11 | Rhamondre Stevenson | RB | NE | 0.2725 | 3.1 | 23.67 | 35.50 | ✗ |
| 12 | Josh Allen | QB | BUF | 0.2714 | 40.8 | 21.91 | 32.87 | ✓ |
| 13 | Kyler Murray | QB | MIN | 0.2648 | -- | 10.34 | 15.51 | DNP |
| 14 | Jameson Williams | WR | DET | 0.2646 | 4.3 | 7.20 | 10.80 | ✗ |
| 15 | Baker Mayfield | QB | TB | 0.2635 | 12.2 | 14.10 | 21.15 | ✗ |
| 16 | Kyle Monangai | RB | CHI | 0.2621 | 6.8 | 9.67 | 14.50 | ✗ |
| 17 | Rashee Rice | WR | KC | 0.2603 | 10.3 | 7.63 | 11.45 | ✗ |
| 18 | Chuba Hubbard | RB | CAR | 0.2573 | 13.4 | 8.73 | 13.10 | ✓ |
| 19 | RJ Harvey | RB | DEN | 0.2559 | -- | 8.67 | 13.00 | DNP |
| 20 | Joe Burrow | QB | CIN | 0.2543 | 16.2 | 18.57 | 27.85 | ✗ |
| 21 | Puka Nacua | WR | LA | 0.2532 | -- | 14.70 | 22.05 | DNP |
| 22 | Dalton Kincaid | TE | BUF | 0.2525 | 19.0 | 7.27 | 10.90 | ✓ |
| 23 | Cam Skattebo | RB | NYG | 0.2500 | 7.5 | 13.50 | 20.25 | ✗ |
| 24 | Tank Bigsby | RB | PHI | 0.2496 | 10.8 | 6.27 | 10.00 | ✓ |
| 25 | Xavier Worthy | WR | KC | 0.2482 | 11.0 | 2.83 | 10.00 | ✓ |
