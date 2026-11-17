# Verification: 2026 week 10

- Week base rate, QB/RB/WR/TE (shared by production and shadow): 51/300 eligible players spiked (0.1700)
- Week base rate, all positions (kept for continuity; pooled lift is measured against the QB/RB/WR/TE rate above): 51/947 (0.0539)
- Cumulative PRODUCTION across 10 verified week(s): P@10 18/100 = 0.1800, P@25 45/250 = 0.1800
- Cumulative head-to-head (precision@10, 8 week(s) with both): production 17/80 = 0.2125, shadow 22/80 = 0.2750

## PRODUCTION: `odds_xgb_model_production.joblib` (commit `7f21e5c966e0d82d049181e3965d661425e01643`)

- Precision@10 (pooled): 3/10 = 0.3000  (1.8x QB/RB/WR/TE base rate)
- Precision@25 (pooled): 5/25 = 0.2000  (1.2x QB/RB/WR/TE base rate)
- Of the top 25: 5 actually spiked
- DNP: 2, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 2/10 | 0.2000 | 0.2812 | 0.7x |
| RB | 3/10 | 0.3000 | 0.1711 | 1.8x |
| WR | 2/10 | 0.2000 | 0.1951 | 1.0x |
| TE | 1/10 | 0.1000 | 0.0725 | 1.4x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Mike Washington Jr. | RB | LV | 0.7527 | 3.2 | 3.57 | 10.00 | ✗ |
| 2 | Justin Jefferson | WR | MIN | 0.7298 | 11.9 | 8.50 | 12.75 | ✗ |
| 3 | Derrick Henry | RB | BAL | 0.7200 | 16.5 | 7.99 | 11.99 | ✓ |
| 4 | Quinshon Judkins | RB | CLE | 0.7071 | 14.6 | 11.27 | 16.90 | ✗ |
| 5 | Devin Singletary | RB | NYG | 0.7061 | 0.3 | 7.23 | 10.85 | ✗ |
| 6 | Carnell Tate | WR | TEN | 0.6921 | -- | 8.17 | 12.25 | DNP |
| 7 | Parker Washington | WR | JAX | 0.6875 | 24.7 | 8.20 | 12.30 | ✓ |
| 8 | Garrett Wilson | WR | NYJ | 0.6855 | 12.1 | 7.10 | 10.65 | ✓ |
| 9 | Caleb Douglas | WR | MIA | 0.6854 | 8.6 | 12.10 | 18.15 | ✗ |
| 10 | Malachi Fields | WR | NYG | 0.6852 | 7.7 | 6.23 | 10.00 | ✗ |
| 11 | Adonai Mitchell | WR | NYJ | 0.6816 | 7.4 | 7.90 | 11.85 | ✗ |
| 12 | Wan'Dale Robinson | WR | TEN | 0.6808 | 2.5 | 6.37 | 10.00 | ✗ |
| 13 | Tyler Allgeier | RB | ARI | 0.6788 | 1.4 | 15.20 | 22.80 | ✗ |
| 14 | A.J. Brown | WR | NE | 0.6784 | 3.7 | 4.87 | 10.00 | ✗ |
| 15 | Xavier Worthy | WR | KC | 0.6777 | 0.0 | 4.40 | 10.00 | ✗ |
| 16 | Jakobi Meyers | WR | JAX | 0.6765 | 4.9 | 7.57 | 11.35 | ✗ |
| 17 | Zay Flowers | WR | BAL | 0.6714 | 8.4 | 9.17 | 13.75 | ✗ |
| 18 | Bhayshul Tuten | RB | JAX | 0.6708 | 18.8 | 20.17 | 30.25 | ✗ |
| 19 | Jameson Williams | WR | DET | 0.6684 | 7.0 | 14.37 | 21.55 | ✗ |
| 20 | Drake London | WR | ATL | 0.6683 | 13.9 | 8.90 | 13.35 | ✓ |
| 21 | Jonathan Taylor | RB | IND | 0.6666 | 11.3 | 9.87 | 14.80 | ✗ |
| 22 | Shedeur Sanders | QB | CLE | 0.6629 | -- | 7.59 | 11.38 | DNP |
| 23 | Tre Harris | WR | LAC | 0.6625 | 11.1 | 3.10 | 10.00 | ✓ |
| 24 | Rico Dowdle | RB | PIT | 0.6605 | 4.4 | 9.87 | 14.80 | ✗ |
| 25 | DeMario Douglas | WR | NE | 0.6584 | 0.9 | 10.53 | 15.80 | ✗ |

## SHADOW: `odds_xgb_model_production_10feature.joblib` (commit `262a04fda4a39cb4c16e568f1d28034a399169e7`)

- Precision@10 (pooled): 3/10 = 0.3000  (1.8x QB/RB/WR/TE base rate)
- Precision@25 (pooled): 5/25 = 0.2000  (1.2x QB/RB/WR/TE base rate)
- Of the top 25: 5 actually spiked
- DNP: 2, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 3/10 | 0.3000 | 0.2812 | 1.1x |
| RB | 4/10 | 0.4000 | 0.1711 | 2.3x |
| WR | 1/10 | 0.1000 | 0.1951 | 0.5x |
| TE | 2/10 | 0.2000 | 0.0725 | 2.8x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Devin Singletary | RB | NYG | 0.3593 | 0.3 | 7.23 | 10.85 | ✗ |
| 2 | Aaron Jones | RB | MIN | 0.3140 | 12.0 | 3.57 | 10.00 | ✓ |
| 3 | Puka Nacua | WR | LA | 0.3095 | -- | 21.77 | 32.65 | DNP |
| 4 | Carnell Tate | WR | TEN | 0.3052 | -- | 8.17 | 12.25 | DNP |
| 5 | Mike Washington Jr. | RB | LV | 0.3030 | 3.2 | 3.57 | 10.00 | ✗ |
| 6 | Efton Chism III | WR | NE | 0.2926 | 0.0 | 6.57 | 10.00 | ✗ |
| 7 | Ashton Jeanty | RB | LV | 0.2893 | 8.4 | 10.30 | 15.45 | ✗ |
| 8 | Justin Jefferson | WR | MIN | 0.2872 | 11.9 | 8.50 | 12.75 | ✗ |
| 9 | Derrick Henry | RB | BAL | 0.2864 | 16.5 | 7.99 | 11.99 | ✓ |
| 10 | Jadarian Price | RB | SEA | 0.2832 | 10.9 | 5.07 | 10.00 | ✓ |
| 11 | Denzel Boston | WR | CLE | 0.2781 | 2.1 | 7.90 | 11.85 | ✗ |
| 12 | Tyler Allgeier | RB | ARI | 0.2779 | 1.4 | 15.20 | 22.80 | ✗ |
| 13 | Malachi Fields | WR | NYG | 0.2767 | 7.7 | 6.23 | 10.00 | ✗ |
| 14 | Garrett Wilson | WR | NYJ | 0.2755 | 12.1 | 7.10 | 10.65 | ✓ |
| 15 | Kaelon Black | RB | SF | 0.2747 | 13.6 | 7.20 | 10.80 | ✓ |
| 16 | Justice Hill | RB | BAL | 0.2736 | 6.3 | 9.00 | 13.50 | ✗ |
| 17 | Chris Rodriguez Jr. | RB | JAX | 0.2712 | 0.5 | 9.33 | 14.00 | ✗ |
| 18 | Mack Hollins | WR | NE | 0.2701 | 7.3 | 9.87 | 14.80 | ✗ |
| 19 | Matthew Golden | WR | GB | 0.2698 | 9.5 | 17.23 | 25.85 | ✗ |
| 20 | Jameson Williams | WR | DET | 0.2691 | 7.0 | 14.37 | 21.55 | ✗ |
| 21 | Rico Dowdle | RB | PIT | 0.2677 | 4.4 | 9.87 | 14.80 | ✗ |
| 22 | Zay Flowers | WR | BAL | 0.2662 | 8.4 | 9.17 | 13.75 | ✗ |
| 23 | DeMario Douglas | WR | NE | 0.2646 | 0.9 | 10.53 | 15.80 | ✗ |
| 24 | Jordan Mason | RB | MIN | 0.2641 | 5.9 | 4.67 | 10.00 | ✗ |
| 25 | Wan'Dale Robinson | WR | TEN | 0.2615 | 2.5 | 6.37 | 10.00 | ✗ |
