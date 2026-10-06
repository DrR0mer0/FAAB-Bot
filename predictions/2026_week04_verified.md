# Verification: 2026 week 4

- Week base rate (shared by production and shadow): 51/1022 eligible players spiked (0.0499)
- Cumulative PRODUCTION across 4 verified week(s): P@10 6/40 = 0.1500, P@25 23/100 = 0.2300
- Cumulative head-to-head (precision@10, 2 week(s) with both): production 5/20 = 0.2500, shadow 9/20 = 0.4500

## PRODUCTION: `odds_xgb_model_production.joblib` (commit `7f21e5c966e0d82d049181e3965d661425e01643`)

- Precision@10 (pooled): 3/10 = 0.3000  (6.0x base rate)
- Precision@25 (pooled): 8/25 = 0.3200  (6.4x base rate)
- Of the top 25: 8 actually spiked
- DNP: 3, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 1/10 | 0.1000 | 0.1250 | 0.8x |
| RB | 4/10 | 0.4000 | 0.1647 | 2.4x |
| WR | 3/10 | 0.3000 | 0.1773 | 1.7x |
| TE | 3/10 | 0.3000 | 0.1143 | 2.6x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Tony Pollard | RB | TEN | 0.7557 | 12.0 | 7.50 | 11.25 | ✓ |
| 2 | Terry McLaurin | WR | WAS | 0.7532 | -- | 8.37 | 12.55 | DNP |
| 3 | Jordan Addison | WR | MIN | 0.7314 | 7.1 | 6.87 | 10.30 | ✗ |
| 4 | J.K. Dobbins | RB | DEN | 0.7257 | 6.2 | 4.33 | 10.00 | ✗ |
| 5 | Jacory Croskey-Merritt | RB | WAS | 0.7219 | 6.0 | 7.40 | 11.10 | ✗ |
| 6 | David Montgomery | RB | HOU | 0.7196 | 4.3 | 12.20 | 18.30 | ✗ |
| 7 | Malik Nabers | WR | NYG | 0.7144 | 20.2 | 5.20 | 10.00 | ✓ |
| 8 | TreVeyon Henderson | RB | NE | 0.7023 | 4.2 | 11.43 | 17.15 | ✗ |
| 9 | Stefon Diggs | WR | WAS | 0.7016 | 6.0 | 12.67 | 19.00 | ✗ |
| 10 | Chuba Hubbard | RB | CAR | 0.6922 | 25.4 | 16.20 | 24.30 | ✓ |
| 11 | DK Metcalf | WR | PIT | 0.6814 | 14.0 | 7.10 | 10.65 | ✓ |
| 12 | Jaylen Warren | RB | PIT | 0.6737 | 14.1 | 11.87 | 17.80 | ✗ |
| 13 | Christian McCaffrey | RB | SF | 0.6716 | 14.5 | 17.17 | 25.75 | ✗ |
| 14 | Brian Robinson | RB | ATL | 0.6663 | 25.2 | 7.27 | 10.90 | ✓ |
| 15 | Quinshon Judkins | RB | CLE | 0.6641 | 18.6 | 7.40 | 11.10 | ✓ |
| 16 | Woody Marks | RB | HOU | 0.6634 | 8.2 | 6.37 | 10.00 | ✗ |
| 17 | Courtland Sutton | WR | DEN | 0.6622 | 0.9 | 4.07 | 10.00 | ✗ |
| 18 | Rashee Rice | WR | KC | 0.6622 | -- | 10.50 | 15.75 | DNP |
| 19 | Josh Downs | WR | IND | 0.6609 | 3.1 | 8.53 | 12.80 | ✗ |
| 20 | Carnell Tate | WR | TEN | 0.6605 | 17.0 | 6.27 | 10.00 | ✓ |
| 21 | Xavier Hutchinson | WR | HOU | 0.6603 | 6.1 | 5.63 | 10.00 | ✗ |
| 22 | Bryce Young | QB | CAR | 0.6602 | 21.5 | 23.05 | 34.58 | ✗ |
| 23 | Justin Jefferson | WR | MIN | 0.6596 | -- | 12.13 | 18.20 | DNP |
| 24 | Tetairoa McMillan | WR | CAR | 0.6574 | 38.2 | 7.77 | 11.65 | ✓ |
| 25 | Rashod Bateman | WR | BAL | 0.6574 | 5.6 | 7.67 | 11.50 | ✗ |

## SHADOW: `odds_xgb_model_production_10feature.joblib` (commit `262a04fda4a39cb4c16e568f1d28034a399169e7`)

- Precision@10 (pooled): 5/10 = 0.5000  (10.0x base rate)
- Precision@25 (pooled): 8/25 = 0.3200  (6.4x base rate)
- Of the top 25: 8 actually spiked
- DNP: 4, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 1/10 | 0.1000 | 0.1250 | 0.8x |
| RB | 5/10 | 0.5000 | 0.1647 | 3.0x |
| WR | 4/10 | 0.4000 | 0.1773 | 2.3x |
| TE | 2/10 | 0.2000 | 0.1143 | 1.8x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | J.K. Dobbins | RB | DEN | 0.3392 | 6.2 | 4.33 | 10.00 | ✗ |
| 2 | Emanuel Wilson | RB | SEA | 0.3236 | 25.5 | 3.63 | 10.00 | ✓ |
| 3 | Chuba Hubbard | RB | CAR | 0.3202 | 25.4 | 16.20 | 24.30 | ✓ |
| 4 | Tony Pollard | RB | TEN | 0.3179 | 12.0 | 7.50 | 11.25 | ✓ |
| 5 | Christian McCaffrey | RB | SF | 0.3109 | 14.5 | 17.17 | 25.75 | ✗ |
| 6 | David Montgomery | RB | HOU | 0.3096 | 4.3 | 12.20 | 18.30 | ✗ |
| 7 | Jaylen Warren | RB | PIT | 0.3001 | 14.1 | 11.87 | 17.80 | ✗ |
| 8 | Ollie Gordon II | RB | MIA | 0.2943 | 17.0 | 5.10 | 10.00 | ✓ |
| 9 | TreVeyon Henderson | RB | NE | 0.2930 | 4.2 | 11.43 | 17.15 | ✗ |
| 10 | Brian Robinson | RB | ATL | 0.2921 | 25.2 | 7.27 | 10.90 | ✓ |
| 11 | Jacory Croskey-Merritt | RB | WAS | 0.2911 | 6.0 | 7.40 | 11.10 | ✗ |
| 12 | Jadarian Price | RB | SEA | 0.2908 | -- | 4.00 | 10.00 | DNP |
| 13 | Aaron Rodgers | QB | PIT | 0.2809 | 20.0 | 13.07 | 19.60 | ✓ |
| 14 | Terry McLaurin | WR | WAS | 0.2786 | -- | 8.37 | 12.55 | DNP |
| 15 | Saquon Barkley | RB | PHI | 0.2786 | 1.5 | 6.50 | 10.00 | ✗ |
| 16 | Rico Dowdle | RB | PIT | 0.2770 | -- | 3.50 | 10.00 | DNP |
| 17 | Chris Godwin Jr. | WR | TB | 0.2765 | 6.1 | 5.87 | 10.00 | ✗ |
| 18 | Sione Vaki | RB | DET | 0.2744 | 2.2 | 3.00 | 10.00 | ✗ |
| 19 | Kyle Monangai | RB | CHI | 0.2732 | 27.5 | 9.77 | 14.65 | ✓ |
| 20 | Bhayshul Tuten | RB | JAX | 0.2717 | 10.1 | 12.83 | 19.25 | ✗ |
| 21 | Rachaad White | RB | WAS | 0.2701 | -- | 8.53 | 12.80 | DNP |
| 22 | Woody Marks | RB | HOU | 0.2697 | 8.2 | 6.37 | 10.00 | ✗ |
| 23 | Jaylen Waddle | WR | DEN | 0.2667 | 12.0 | 7.30 | 10.95 | ✓ |
| 24 | DJ Moore | WR | BUF | 0.2661 | 2.2 | 9.37 | 14.05 | ✗ |
| 25 | Michael Wilson | WR | ARI | 0.2650 | 13.0 | 10.43 | 15.65 | ✗ |
