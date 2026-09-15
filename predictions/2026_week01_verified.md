# Verification: 2026 week 1

Model: `odds_xgb_model_production.joblib` (commit `262a04fda4a39cb4c16e568f1d28034a399169e7`)

- Week base rate: 48/972 eligible players spiked (0.0494)
- Precision@10: 0/10 = 0.0000 (0.0x base rate)
- Precision@25: 3/25 = 0.1200 (2.4x base rate)
- Of the top 25: 3 actually spiked
- DNP: 8, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator
- Cumulative across 1 verified week(s): P@10 0/10 = 0.0000, P@25 3/25 = 0.1200

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Cam Skattebo | RB | NYG | 0.8851 | 14.1 | 18.80 | 28.20 | ✗ |
| 2 | Quinshon Judkins | RB | CLE | 0.8825 | 6.0 | 6.57 | 10.00 | ✗ |
| 3 | Davante Adams | WR | LA | 0.8635 | 4.1 | 11.27 | 16.90 | ✗ |
| 4 | Travis Kelce | TE | KC | 0.8568 | 8.6 | 3.30 | 10.00 | ✗ |
| 5 | Devin Singletary | RB | NYG | 0.8551 | 11.8 | 9.57 | 14.35 | ✗ |
| 6 | RJ Harvey | RB | DEN | 0.8480 | 6.1 | 13.33 | 20.00 | ✗ |
| 7 | Ian Thomas | TE | LV | 0.8452 | -- | 3.20 | 10.00 | DNP |
| 8 | Jake Browning | QB | CIN | 0.8452 | -- | 8.37 | 12.55 | DNP |
| 9 | C.J. Stroud | QB | HOU | 0.8444 | 16.5 | 14.17 | 21.25 | ✗ |
| 10 | Marcus Mariota | QB | WAS | 0.8421 | -- | 5.45 | 10.00 | DNP |
| 11 | AJ Barner | TE | SEA | 0.8420 | 2.3 | 9.07 | 13.60 | ✗ |
| 12 | Jameis Winston | QB | NYG | 0.8418 | -- | 14.26 | 21.39 | DNP |
| 13 | Tre Harris | WR | LAC | 0.8403 | 3.5 | 4.40 | 10.00 | ✗ |
| 14 | Tyler Warren | TE | IND | 0.8378 | 8.8 | 5.47 | 10.00 | ✗ |
| 15 | DJ Giddens | RB | IND | 0.8349 | -- | 1.83 | 10.00 | DNP |
| 16 | Breece Hall | RB | NYJ | 0.8345 | 18.8 | 10.63 | 15.95 | ✓ |
| 17 | Pat Freiermuth | TE | PIT | 0.8336 | 13.1 | 6.13 | 10.00 | ✓ |
| 18 | Sam Darnold | QB | SEA | 0.8329 | 0.5 | 10.13 | 15.20 | ✗ |
| 19 | Ollie Gordon II | RB | MIA | 0.8316 | -- | 0.50 | 10.00 | DNP |
| 20 | Bryce Young | QB | CAR | 0.8310 | 31.4 | 14.35 | 21.52 | ✓ |
| 21 | Quentin Johnston | WR | LAC | 0.8304 | 2.7 | 10.83 | 16.25 | ✗ |
| 22 | Xavier Worthy | WR | KC | 0.8292 | 3.3 | 3.23 | 10.00 | ✗ |
| 23 | Tee Higgins | WR | CIN | 0.8281 | 7.4 | 12.13 | 18.20 | ✗ |
| 24 | Raheem Mostert | RB | LV | 0.8272 | -- | 0.93 | 10.00 | DNP |
| 25 | Mo Alie-Cox | TE | IND | 0.8255 | -- | 5.27 | 10.00 | DNP |
