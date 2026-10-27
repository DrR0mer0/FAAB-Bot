# Verification: 2026 week 7

- Week base rate (shared by production and shadow): 40/915 eligible players spiked (0.0437)
- Cumulative PRODUCTION across 7 verified week(s): P@10 11/70 = 0.1571, P@25 31/175 = 0.1771
- Cumulative head-to-head (precision@10, 5 week(s) with both): production 10/50 = 0.2000, shadow 15/50 = 0.3000

## PRODUCTION: `odds_xgb_model_production.joblib` (commit `7f21e5c966e0d82d049181e3965d661425e01643`)

- Precision@10 (pooled): 2/10 = 0.2000  (4.6x base rate)
- Precision@25 (pooled): 2/25 = 0.0800  (1.8x base rate)
- Of the top 25: 2 actually spiked
- DNP: 3, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 0/10 | 0.0000 | 0.1613 | n/a |
| RB | 2/10 | 0.2000 | 0.1781 | 1.1x |
| WR | 1/10 | 0.1000 | 0.1083 | 0.9x |
| TE | 2/10 | 0.2000 | 0.1385 | 1.4x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Dameon Pierce | RB | PHI | 0.7149 | 11.3 | 5.40 | 10.00 | ✓ |
| 2 | Jordan Addison | WR | MIN | 0.7113 | 3.3 | 9.23 | 13.85 | ✗ |
| 3 | Kendre Miller | RB | NO | 0.7072 | 4.3 | 5.97 | 10.00 | ✗ |
| 4 | Woody Marks | RB | HOU | 0.6939 | 8.6 | 9.27 | 13.90 | ✗ |
| 5 | Tyler Allgeier | RB | ARI | 0.6855 | 6.4 | 6.60 | 10.00 | ✗ |
| 6 | Bam Knight | RB | ARI | 0.6827 | 7.8 | 4.67 | 10.00 | ✗ |
| 7 | Ollie Gordon II | RB | MIA | 0.6826 | 16.8 | 14.93 | 22.40 | ✗ |
| 8 | Jaylen Waddle | WR | DEN | 0.6795 | 6.8 | 6.73 | 10.10 | ✗ |
| 9 | Justin Jefferson | WR | MIN | 0.6786 | 6.4 | 9.00 | 13.50 | ✗ |
| 10 | Makai Lemon | WR | PHI | 0.6776 | 16.2 | 4.80 | 10.00 | ✓ |
| 11 | Wan'Dale Robinson | WR | TEN | 0.6718 | 2.7 | 7.17 | 10.75 | ✗ |
| 12 | Denzel Boston | WR | CLE | 0.6672 | 4.2 | 8.33 | 12.50 | ✗ |
| 13 | Kevin Coleman Jr. | WR | MIA | 0.6641 | 0.0 | 4.03 | 10.00 | ✗ |
| 14 | D'Andre Swift | RB | CHI | 0.6637 | 17.0 | 14.23 | 21.35 | ✗ |
| 15 | Rashee Rice | WR | KC | 0.6622 | -- | 10.50 | 15.75 | DNP |
| 16 | Dontayvion Wicks | WR | PHI | 0.6600 | -- | 5.17 | 10.00 | DNP |
| 17 | Jerry Jeudy | WR | CLE | 0.6481 | 4.1 | 5.80 | 10.00 | ✗ |
| 18 | Rome Odunze | WR | CHI | 0.6468 | 8.9 | 7.10 | 10.65 | ✗ |
| 19 | Courtland Sutton | WR | DEN | 0.6430 | 6.0 | 4.20 | 10.00 | ✗ |
| 20 | DeMario Douglas | WR | NE | 0.6420 | 3.8 | 13.03 | 19.55 | ✗ |
| 21 | Jordan Love | QB | GB | 0.6404 | 17.0 | 18.87 | 28.30 | ✗ |
| 22 | Darius Cooper | WR | PHI | 0.6401 | 2.2 | 10.77 | 16.15 | ✗ |
| 23 | Tanner McKee | QB | PHI | 0.6397 | 10.5 | 8.11 | 12.17 | ✗ |
| 24 | Garrett Wilson | WR | NYJ | 0.6396 | 3.6 | 10.17 | 15.25 | ✗ |
| 25 | Audric Estimé | RB | CLE | 0.6376 | -- | 12.43 | 18.65 | DNP |

## SHADOW: `odds_xgb_model_production_10feature.joblib` (commit `262a04fda4a39cb4c16e568f1d28034a399169e7`)

- Precision@10 (pooled): 2/10 = 0.2000  (4.6x base rate)
- Precision@25 (pooled): 4/25 = 0.1600  (3.7x base rate)
- Of the top 25: 4 actually spiked
- DNP: 3, no label produced: 0 -- both counted as non-hits above, not dropped from the denominator

*Per-position base rate is each position's own spike rate among all eligible players at that position -- a different, position-specific denominator from the pooled base rate above. Not directly comparable across positions or against the pooled figure; each row's lift is only meaningful against that row's own base rate.*

| Pos | Hits/N | Precision | Base rate | Lift |
|---|---:|---:|---:|---:|
| QB | 3/10 | 0.3000 | 0.1613 | 1.9x |
| RB | 1/10 | 0.1000 | 0.1781 | 0.6x |
| WR | 2/10 | 0.2000 | 0.1083 | 1.8x |
| TE | 2/10 | 0.2000 | 0.1385 | 1.4x |

| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |
|---:|---|---|---|---:|---:|---:|---:|:---:|
| 1 | Dameon Pierce | RB | PHI | 0.3395 | 11.3 | 5.40 | 10.00 | ✓ |
| 2 | Kendre Miller | RB | NO | 0.3355 | 4.3 | 5.97 | 10.00 | ✗ |
| 3 | Tyler Allgeier | RB | ARI | 0.3108 | 6.4 | 6.60 | 10.00 | ✗ |
| 4 | Woody Marks | RB | HOU | 0.3079 | 8.6 | 9.27 | 13.90 | ✗ |
| 5 | Pierre Strong | RB | GB | 0.2938 | -- | 6.27 | 10.00 | DNP |
| 6 | Makai Lemon | WR | PHI | 0.2910 | 16.2 | 4.80 | 10.00 | ✓ |
| 7 | Puka Nacua | WR | LA | 0.2826 | 17.8 | 21.13 | 31.70 | ✗ |
| 8 | Ollie Gordon II | RB | MIA | 0.2768 | 16.8 | 14.93 | 22.40 | ✗ |
| 9 | Bam Knight | RB | ARI | 0.2748 | 7.8 | 4.67 | 10.00 | ✗ |
| 10 | DeVonta Smith | WR | PHI | 0.2729 | -- | 16.40 | 24.60 | DNP |
| 11 | Justin Jefferson | WR | MIN | 0.2712 | 6.4 | 9.00 | 13.50 | ✗ |
| 12 | Dontayvion Wicks | WR | PHI | 0.2693 | -- | 5.17 | 10.00 | DNP |
| 13 | Jerry Jeudy | WR | CLE | 0.2691 | 4.1 | 5.80 | 10.00 | ✗ |
| 14 | Carnell Tate | WR | TEN | 0.2688 | 7.4 | 11.37 | 17.05 | ✗ |
| 15 | Isaiah Davis | RB | NYJ | 0.2676 | 6.9 | 1.77 | 10.00 | ✗ |
| 16 | KC Concepcion | WR | CLE | 0.2671 | 19.2 | 5.27 | 10.00 | ✓ |
| 17 | George Pickens | WR | DAL | 0.2659 | 13.7 | 14.10 | 21.15 | ✗ |
| 18 | D'Andre Swift | RB | CHI | 0.2627 | 17.0 | 14.23 | 21.35 | ✗ |
| 19 | Jordan Addison | WR | MIN | 0.2623 | 3.3 | 9.23 | 13.85 | ✗ |
| 20 | Jaylen Waddle | WR | DEN | 0.2617 | 6.8 | 6.73 | 10.10 | ✗ |
| 21 | Germie Bernard | WR | PIT | 0.2615 | 4.2 | 11.23 | 16.85 | ✗ |
| 22 | Wan'Dale Robinson | WR | TEN | 0.2611 | 2.7 | 7.17 | 10.75 | ✗ |
| 23 | DeMario Douglas | WR | NE | 0.2580 | 3.8 | 13.03 | 19.55 | ✗ |
| 24 | Samaje Perine | RB | CIN | 0.2574 | 7.6 | 5.80 | 10.00 | ✗ |
| 25 | Jaxon Smith-Njigba | WR | SEA | 0.2561 | 26.8 | 16.60 | 24.90 | ✓ |
