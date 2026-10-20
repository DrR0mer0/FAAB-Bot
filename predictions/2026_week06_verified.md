# Verification: 2026 week 6

- Week base rate (shared by production and shadow): 40/944 eligible players spiked (0.0424)
- Cumulative PRODUCTION across 6 verified week(s): P@10 9/60 = 0.1500, P@25 29/150 = 0.1933
- Cumulative head-to-head (precision@10, 4 week(s) with both): production 8/40 = 0.2000, shadow 13/40 = 0.3250

## PRODUCTION: `odds_xgb_model_production.joblib` (commit `7f21e5c966e0d82d049181e3965d661425e01643`)

- Precision@10 (pooled): 0/10 = 0.0000
- Precision@25 (pooled): 1/25 = 0.0400  (0.9x base rate)
- Of the top 25: 1 actually spiked
- DNP: 6, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 1/10 | 0.1000 | 0.1724 | 0.6x |
| RB | 0/10 | 0.0000 | 0.1688 | n/a |
| WR | 1/10 | 0.1000 | 0.0859 | 1.2x |
| TE | 0/10 | 0.0000 | 0.1429 | n/a |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Braelon Allen | RB | NYJ | 0.7468 | 11.4 | 9.67 | 14.50 | ✗ |
| 2 | Rhamondre Stevenson | RB | NE | 0.6889 | 3.6 | 12.07 | 18.10 | ✗ |
| 3 | Parker Washington | WR | JAX | 0.6828 | 8.4 | 13.47 | 20.20 | ✗ |
| 4 | Josh Downs | WR | IND | 0.6772 | 6.7 | 6.50 | 10.00 | ✗ |
| 5 | Terry McLaurin | WR | WAS | 0.6731 | 3.2 | 10.30 | 15.45 | ✗ |
| 6 | Jaylen Waddle | WR | DEN | 0.6726 | 4.4 | 6.40 | 10.00 | ✗ |
| 7 | MarShawn Lloyd | RB | GB | 0.6675 | 1.0 | 8.80 | 13.20 | ✗ |
| 8 | KC Concepcion | WR | CLE | 0.6648 | 5.9 | 3.93 | 10.00 | ✗ |
| 9 | Courtland Sutton | WR | DEN | 0.6630 | 2.9 | 5.27 | 10.00 | ✗ |
| 10 | Makai Lemon | WR | PHI | 0.6625 | 3.4 | 5.13 | 10.00 | ✗ |
| 11 | Dontayvion Wicks | WR | PHI | 0.6616 | 3.4 | 5.43 | 10.00 | ✗ |
| 12 | Khalil Shakir | WR | BUF | 0.6604 | 11.4 | 6.77 | 10.15 | ✓ |
| 13 | Denzel Boston | WR | CLE | 0.6602 | 2.0 | 9.37 | 14.05 | ✗ |
| 14 | Jacory Croskey-Merritt | RB | WAS | 0.6535 | -- | 8.33 | 12.50 | DNP |
| 15 | Mac Jones | QB | SF | 0.6494 | -- | -0.10 | 10.00 | DNP |
| 16 | Rashee Rice | WR | KC | 0.6489 | -- | 10.50 | 15.75 | DNP |
| 17 | Jalen Hurts | QB | PHI | 0.6487 | -- | 13.05 | 19.57 | DNP |
| 18 | Jakobi Meyers | WR | JAX | 0.6482 | 10.8 | 8.47 | 12.70 | ✗ |
| 19 | Rome Odunze | WR | CHI | 0.6450 | 5.4 | 7.27 | 10.90 | ✗ |
| 20 | Saquon Barkley | RB | PHI | 0.6443 | -- | 4.17 | 10.00 | DNP |
| 21 | DJ Moore | WR | BUF | 0.6430 | -- | 3.93 | 10.00 | DNP |
| 22 | Efton Chism III | WR | NE | 0.6418 | 0.0 | 6.95 | 10.42 | ✗ |
| 23 | J.K. Dobbins | RB | DEN | 0.6405 | 12.1 | 8.80 | 13.20 | ✗ |
| 24 | Rashod Bateman | WR | BAL | 0.6398 | 0.0 | 6.33 | 10.00 | ✗ |
| 25 | Tyjae Spears | RB | TEN | 0.6393 | 1.3 | 2.30 | 10.00 | ✗ |

## SHADOW: `odds_xgb_model_production_10feature.joblib` (commit `262a04fda4a39cb4c16e568f1d28034a399169e7`)

- Precision@10 (pooled): 2/10 = 0.2000  (4.7x base rate)
- Precision@25 (pooled): 6/25 = 0.2400  (5.7x base rate)
- Of the top 25: 6 actually spiked
- DNP: 2, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 1/10 | 0.1000 | 0.1724 | 0.6x |
| RB | 2/10 | 0.2000 | 0.1688 | 1.2x |
| WR | 2/10 | 0.2000 | 0.0859 | 2.3x |
| TE | 2/10 | 0.2000 | 0.1429 | 1.4x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Puka Nacua | WR | LA | 0.3049 | 11.0 | 20.77 | 31.15 | ✗ |
| 2 | MarShawn Lloyd | RB | GB | 0.3038 | 1.0 | 8.80 | 13.20 | ✗ |
| 3 | Parker Washington | WR | JAX | 0.2900 | 8.4 | 13.47 | 20.20 | ✗ |
| 4 | RJ Harvey | RB | DEN | 0.2851 | 13.1 | 7.57 | 11.35 | ✓ |
| 5 | Rhamondre Stevenson | RB | NE | 0.2833 | 3.6 | 12.07 | 18.10 | ✗ |
| 6 | Xavier Hutchinson | WR | HOU | 0.2769 | 2.0 | 7.20 | 10.80 | ✗ |
| 7 | Denzel Boston | WR | CLE | 0.2761 | 2.0 | 9.37 | 14.05 | ✗ |
| 8 | Jaylin Noel | WR | HOU | 0.2735 | 18.7 | 8.87 | 13.30 | ✓ |
| 9 | Josh Downs | WR | IND | 0.2731 | 6.7 | 6.50 | 10.00 | ✗ |
| 10 | Woody Marks | RB | HOU | 0.2708 | 9.1 | 9.10 | 13.65 | ✗ |
| 11 | Rashee Rice | WR | KC | 0.2694 | -- | 10.50 | 15.75 | DNP |
| 12 | Jakobi Meyers | WR | JAX | 0.2693 | 10.8 | 8.47 | 12.70 | ✗ |
| 13 | Carson Steele | RB | PHI | 0.2682 | 0.9 | 1.23 | 10.00 | ✗ |
| 14 | Tre Tucker | WR | LV | 0.2680 | 24.2 | 6.07 | 10.00 | ✓ |
| 15 | Matthew Golden | WR | GB | 0.2654 | 19.4 | 14.67 | 22.00 | ✗ |
| 16 | Keenan Allen | WR | IND | 0.2631 | 12.2 | 7.77 | 11.65 | ✓ |
| 17 | Kalif Raymond | WR | CHI | 0.2612 | 2.8 | 7.17 | 10.75 | ✗ |
| 18 | Germie Bernard | WR | PIT | 0.2597 | 20.2 | 5.13 | 10.00 | ✓ |
| 19 | Christian Watson | WR | GB | 0.2581 | 7.9 | 18.20 | 27.30 | ✗ |
| 20 | Kaleb Johnson | RB | GB | 0.2576 | -0.2 | 2.30 | 10.00 | ✗ |
| 21 | Deebo Samuel Sr. | WR | SF | 0.2575 | -- | 10.80 | 16.20 | DNP |
| 22 | Tetairoa McMillan | WR | CAR | 0.2559 | 11.6 | 17.83 | 26.75 | ✗ |
| 23 | KC Concepcion | WR | CLE | 0.2556 | 5.9 | 3.93 | 10.00 | ✗ |
| 24 | Rico Dowdle | RB | PIT | 0.2551 | 6.4 | 3.50 | 10.00 | ✗ |
| 25 | DK Metcalf | WR | PIT | 0.2550 | 14.8 | 9.47 | 14.20 | ✓ |
