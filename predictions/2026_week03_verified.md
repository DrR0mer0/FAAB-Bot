# Verification: 2026 week 3

- Week base rate (shared by production and shadow): 49/957 eligible players spiked (0.0512)
- Cumulative PRODUCTION across 3 verified week(s): P@10 3/30 = 0.1000, P@25 15/75 = 0.2000
- Cumulative head-to-head (precision@10, 1 week(s) with both): production 2/10 = 0.2000, shadow 4/10 = 0.4000

## PRODUCTION: `odds_xgb_model_production.joblib` (commit `7f21e5c966e0d82d049181e3965d661425e01643`)

- Precision@10 (pooled): 2/10 = 0.2000  (3.9x base rate)
- Precision@25 (pooled): 6/25 = 0.2400  (4.7x base rate)
- Of the top 25: 6 actually spiked
- DNP: 1, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 2/10 | 0.2000 | 0.2571 | 0.8x |
| RB | 2/10 | 0.2000 | 0.1299 | 1.5x |
| WR | 3/10 | 0.3000 | 0.1719 | 1.7x |
| TE | 3/10 | 0.3000 | 0.1127 | 2.7x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Cam Skattebo | RB | NYG | 0.7162 | 11.5 | 10.37 | 15.55 | ✗ |
| 2 | Matthew Golden | WR | GB | 0.7148 | 18.5 | 7.20 | 10.80 | ✓ |
| 3 | Quinshon Judkins | RB | CLE | 0.7022 | 8.9 | 6.97 | 10.45 | ✗ |
| 4 | Tank Bigsby | RB | PHI | 0.6894 | -1.0 | 9.40 | 14.10 | ✗ |
| 5 | Xavier Worthy | WR | KC | 0.6893 | 3.0 | 4.80 | 10.00 | ✗ |
| 6 | Blake Corum | RB | LA | 0.6876 | 2.5 | 7.50 | 11.25 | ✗ |
| 7 | George Pickens | WR | DAL | 0.6842 | 11.7 | 4.23 | 10.00 | ✓ |
| 8 | Quentin Johnston | WR | LAC | 0.6772 | 5.5 | 5.63 | 10.00 | ✗ |
| 9 | Jaylen Waddle | WR | DEN | 0.6718 | 3.4 | 6.40 | 10.00 | ✗ |
| 10 | Kyle Monangai | RB | CHI | 0.6688 | 3.1 | 9.87 | 14.80 | ✗ |
| 11 | Brian Robinson | RB | ATL | 0.6671 | 11.0 | 3.90 | 10.00 | ✓ |
| 12 | Ryan Flournoy | WR | DAL | 0.6656 | 3.2 | 5.77 | 10.00 | ✗ |
| 13 | James Cook | RB | BUF | 0.6615 | 19.4 | 10.10 | 15.15 | ✓ |
| 14 | Brian Thomas Jr. | WR | JAX | 0.6505 | 1.3 | 5.80 | 10.00 | ✗ |
| 15 | Daniel Jones | QB | IND | 0.6472 | 7.9 | 7.08 | 10.62 | ✗ |
| 16 | Chuba Hubbard | RB | CAR | 0.6452 | 13.0 | 12.77 | 19.15 | ✗ |
| 17 | David Montgomery | RB | HOU | 0.6412 | 5.8 | 12.27 | 18.40 | ✗ |
| 18 | C.J. Stroud | QB | HOU | 0.6410 | 11.7 | 16.91 | 25.37 | ✗ |
| 19 | Caleb Williams | QB | CHI | 0.6393 | -- | 19.85 | 29.78 | DNP |
| 20 | George Kittle | TE | SF | 0.6392 | 23.2 | 7.87 | 11.80 | ✓ |
| 21 | T.J. Hockenson | TE | MIN | 0.6384 | 2.1 | 5.57 | 10.00 | ✗ |
| 22 | Mark Andrews | TE | BAL | 0.6357 | 3.9 | 5.73 | 10.00 | ✗ |
| 23 | Tre Harris | WR | LAC | 0.6320 | 10.6 | 4.37 | 10.00 | ✓ |
| 24 | Jameson Williams | WR | DET | 0.6309 | 6.9 | 7.07 | 10.60 | ✗ |
| 25 | Ladd McConkey | WR | LAC | 0.6251 | 8.6 | 8.43 | 12.65 | ✗ |

## SHADOW: `odds_xgb_model_production_10feature.joblib` (commit `262a04fda4a39cb4c16e568f1d28034a399169e7`)

- Precision@10 (pooled): 4/10 = 0.4000  (7.8x base rate)
- Precision@25 (pooled): 7/25 = 0.2800  (5.5x base rate)
- Of the top 25: 7 actually spiked
- DNP: 3, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 3/10 | 0.3000 | 0.2571 | 1.2x |
| RB | 3/10 | 0.3000 | 0.1299 | 2.3x |
| WR | 2/10 | 0.2000 | 0.1719 | 1.2x |
| TE | 2/10 | 0.2000 | 0.1127 | 1.8x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | James Cook | RB | BUF | 0.3870 | 19.4 | 10.10 | 15.15 | ✓ |
| 2 | Cam Skattebo | RB | NYG | 0.3435 | 11.5 | 10.37 | 15.55 | ✗ |
| 3 | Brian Robinson | RB | ATL | 0.3175 | 11.0 | 3.90 | 10.00 | ✓ |
| 4 | Kyle Monangai | RB | CHI | 0.3084 | 3.1 | 9.87 | 14.80 | ✗ |
| 5 | Jordan Mason | RB | MIN | 0.3081 | -- | 7.63 | 11.45 | DNP |
| 6 | Quinshon Judkins | RB | CLE | 0.2952 | 8.9 | 6.97 | 10.45 | ✗ |
| 7 | Keaton Mitchell | RB | LAC | 0.2929 | 12.2 | 1.83 | 10.00 | ✓ |
| 8 | Matthew Golden | WR | GB | 0.2928 | 18.5 | 7.20 | 10.80 | ✓ |
| 9 | Chuba Hubbard | RB | CAR | 0.2859 | 13.0 | 12.77 | 19.15 | ✗ |
| 10 | Tank Bigsby | RB | PHI | 0.2835 | -1.0 | 9.40 | 14.10 | ✗ |
| 11 | Rico Dowdle | RB | PIT | 0.2755 | -- | 3.50 | 10.00 | DNP |
| 12 | George Pickens | WR | DAL | 0.2743 | 11.7 | 4.23 | 10.00 | ✓ |
| 13 | Jaylen Waddle | WR | DEN | 0.2700 | 3.4 | 6.40 | 10.00 | ✗ |
| 14 | Jayden Reed | WR | GB | 0.2659 | -- | 3.50 | 10.00 | DNP |
| 15 | Raheim Sanders | RB | CLE | 0.2656 | 7.1 | 3.17 | 10.00 | ✗ |
| 16 | Rhamondre Stevenson | RB | NE | 0.2634 | 5.3 | 16.47 | 24.70 | ✗ |
| 17 | Amon-Ra St. Brown | WR | DET | 0.2623 | 9.9 | 24.60 | 36.90 | ✗ |
| 18 | Christian Watson | WR | GB | 0.2612 | 19.1 | 20.53 | 30.80 | ✗ |
| 19 | Rachaad White | RB | WAS | 0.2599 | 10.6 | 6.70 | 10.05 | ✓ |
| 20 | Travis Etienne | RB | NO | 0.2595 | 8.0 | 6.57 | 10.00 | ✗ |
| 21 | Blake Corum | RB | LA | 0.2584 | 2.5 | 7.50 | 11.25 | ✗ |
| 22 | D'Andre Swift | RB | CHI | 0.2583 | 9.8 | 15.87 | 23.80 | ✗ |
| 23 | Geno Smith | QB | NYJ | 0.2555 | 24.0 | 10.67 | 16.01 | ✓ |
| 24 | Ladd McConkey | WR | LAC | 0.2544 | 8.6 | 8.43 | 12.65 | ✗ |
| 25 | Quentin Johnston | WR | LAC | 0.2532 | 5.5 | 5.63 | 10.00 | ✗ |
