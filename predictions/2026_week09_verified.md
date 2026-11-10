# Verification: 2026 week 9

- Week base rate, QB/RB/WR/TE (shared by production and shadow): 39/321 eligible players spiked (0.1215)
- Week base rate, all positions (kept for continuity; pooled lift is measured against the QB/RB/WR/TE rate above): 39/993 (0.0393)
- Cumulative PRODUCTION across 9 verified week(s): P@10 15/90 = 0.1667, P@25 40/225 = 0.1778
- Cumulative head-to-head (precision@10, 7 week(s) with both): production 14/70 = 0.2000, shadow 19/70 = 0.2714

## PRODUCTION: `odds_xgb_model_production.joblib` (commit `7f21e5c966e0d82d049181e3965d661425e01643`)

- Precision@10 (pooled): 1/10 = 0.1000  (0.8x QB/RB/WR/TE base rate)
- Precision@25 (pooled): 1/25 = 0.0400  (0.3x QB/RB/WR/TE base rate)
- Of the top 25: 1 actually spiked
- DNP: 3, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 1/10 | 0.1000 | 0.1667 | 0.6x |
| RB | 0/10 | 0.0000 | 0.1125 | n/a |
| WR | 1/10 | 0.1000 | 0.1504 | 0.7x |
| TE | 0/10 | 0.0000 | 0.0556 | n/a |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Mike Washington Jr. | RB | LV | 0.7485 | -0.3 | 5.50 | 10.00 | ✗ |
| 2 | Drake London | WR | ATL | 0.7444 | 9.3 | 8.53 | 12.80 | ✗ |
| 3 | Josh Jacobs | RB | GB | 0.7444 | 0.5 | 12.87 | 19.30 | ✗ |
| 4 | Kenny Gainwell | RB | TB | 0.7089 | 9.1 | 8.57 | 12.85 | ✗ |
| 5 | Kendre Miller | RB | NO | 0.7070 | -- | 7.10 | 10.65 | DNP |
| 6 | Jordan Mason | RB | MIN | 0.7023 | 5.2 | 6.90 | 10.35 | ✗ |
| 7 | Rhamondre Stevenson | RB | NE | 0.7004 | 13.3 | 11.37 | 17.05 | ✗ |
| 8 | Denzel Boston | WR | CLE | 0.6924 | 12.8 | 4.30 | 10.00 | ✓ |
| 9 | Quinshon Judkins | RB | CLE | 0.6897 | 5.0 | 11.77 | 17.65 | ✗ |
| 10 | Kaelon Black | RB | SF | 0.6896 | 7.0 | 5.40 | 10.00 | ✗ |
| 11 | A.J. Brown | WR | NE | 0.6851 | -- | 4.87 | 10.00 | DNP |
| 12 | Josh Downs | WR | IND | 0.6772 | 3.9 | 6.60 | 10.00 | ✗ |
| 13 | Jaylen Waddle | WR | DEN | 0.6762 | 2.7 | 7.53 | 11.30 | ✗ |
| 14 | Justin Jefferson | WR | MIN | 0.6752 | 7.3 | 11.33 | 17.00 | ✗ |
| 15 | Omarion Hampton | RB | LAC | 0.6720 | 6.0 | 4.67 | 10.00 | ✗ |
| 16 | Zay Flowers | WR | BAL | 0.6686 | 3.8 | 11.13 | 16.70 | ✗ |
| 17 | Jonah Coleman | RB | DEN | 0.6684 | 16.8 | 13.20 | 19.80 | ✗ |
| 18 | Jakobi Meyers | WR | JAX | 0.6634 | 8.5 | 6.30 | 10.00 | ✗ |
| 19 | Jordan Addison | WR | MIN | 0.6605 | 3.8 | 6.17 | 10.00 | ✗ |
| 20 | Parker Washington | WR | JAX | 0.6600 | 13.1 | 12.97 | 19.45 | ✗ |
| 21 | Chris Bell | WR | MIA | 0.6595 | 0.0 | 3.83 | 10.00 | ✗ |
| 22 | Omar Cooper Jr. | WR | NYJ | 0.6582 | 9.8 | 14.27 | 21.40 | ✗ |
| 23 | Tanner McKee | QB | PHI | 0.6581 | -- | 10.04 | 15.06 | DNP |
| 24 | Tre Harris | WR | LAC | 0.6554 | 2.1 | 4.90 | 10.00 | ✗ |
| 25 | Jalen Coker | WR | CAR | 0.6536 | 4.6 | 8.23 | 12.35 | ✗ |

## SHADOW: `odds_xgb_model_production_10feature.joblib` (commit `262a04fda4a39cb4c16e568f1d28034a399169e7`)

- Precision@10 (pooled): 0/10 = 0.0000
- Precision@25 (pooled): 1/25 = 0.0400  (0.3x QB/RB/WR/TE base rate)
- Of the top 25: 1 actually spiked
- DNP: 2, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 1/10 | 0.1000 | 0.1667 | 0.6x |
| RB | 1/10 | 0.1000 | 0.1125 | 0.9x |
| WR | 0/10 | 0.0000 | 0.1504 | n/a |
| TE | 1/10 | 0.1000 | 0.0556 | 1.8x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Josh Jacobs | RB | GB | 0.3783 | 0.5 | 12.87 | 19.30 | ✗ |
| 2 | Kenny Gainwell | RB | TB | 0.3425 | 9.1 | 8.57 | 12.85 | ✗ |
| 3 | Mike Washington Jr. | RB | LV | 0.3397 | -0.3 | 5.50 | 10.00 | ✗ |
| 4 | Jadarian Price | RB | SEA | 0.2994 | 9.8 | 3.30 | 10.00 | ✗ |
| 5 | DeVonta Smith | WR | PHI | 0.2969 | -- | 16.40 | 24.60 | DNP |
| 6 | Omarion Hampton | RB | LAC | 0.2967 | 6.0 | 4.67 | 10.00 | ✗ |
| 7 | Jordan Mason | RB | MIN | 0.2941 | 5.2 | 6.90 | 10.35 | ✗ |
| 8 | Chris Bell | WR | MIA | 0.2935 | 0.0 | 3.83 | 10.00 | ✗ |
| 9 | Rachaad White | RB | WAS | 0.2929 | 7.9 | 9.73 | 14.60 | ✗ |
| 10 | Rhamondre Stevenson | RB | NE | 0.2922 | 13.3 | 11.37 | 17.05 | ✗ |
| 11 | Jonah Coleman | RB | DEN | 0.2878 | 16.8 | 13.20 | 19.80 | ✗ |
| 12 | Seth McGowan | RB | IND | 0.2758 | 10.7 | 3.63 | 10.00 | ✓ |
| 13 | Jordan Addison | WR | MIN | 0.2757 | 3.8 | 6.17 | 10.00 | ✗ |
| 14 | Kaelon Black | RB | SF | 0.2734 | 7.0 | 5.40 | 10.00 | ✗ |
| 15 | Alvin Kamara | RB | NO | 0.2714 | 6.4 | 8.40 | 12.60 | ✗ |
| 16 | Quinshon Judkins | RB | CLE | 0.2707 | 5.0 | 11.77 | 17.65 | ✗ |
| 17 | Parker Washington | WR | JAX | 0.2703 | 13.1 | 12.97 | 19.45 | ✗ |
| 18 | Kendre Miller | RB | NO | 0.2696 | -- | 7.10 | 10.65 | DNP |
| 19 | Josh Downs | WR | IND | 0.2693 | 3.9 | 6.60 | 10.00 | ✗ |
| 20 | Kimani Vidal | RB | LAC | 0.2670 | 4.1 | 7.20 | 10.80 | ✗ |
| 21 | Malik Washington | WR | MIA | 0.2661 | 3.1 | 8.87 | 13.30 | ✗ |
| 22 | Zay Flowers | WR | BAL | 0.2659 | 3.8 | 11.13 | 16.70 | ✗ |
| 23 | Jakobi Meyers | WR | JAX | 0.2645 | 8.5 | 6.30 | 10.00 | ✗ |
| 24 | Drake London | WR | ATL | 0.2631 | 9.3 | 8.53 | 12.80 | ✗ |
| 25 | Justin Jefferson | WR | MIN | 0.2623 | 7.3 | 11.33 | 17.00 | ✗ |
