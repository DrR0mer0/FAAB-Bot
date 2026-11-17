# Predictions: 2026 week 10

Model: `odds_xgb_model_production_10feature.joblib` (commit `262a04fda4a39cb4c16e568f1d28034a399169e7`)

Generated: 2026-11-12T15:00:38.816467+00:00

- Total eligible: 2177
- After position filter (QB/RB/WR/TE): 664
- Stable scored: 347 (327 released from offseason suppression this week)
- Unavailable (reserve/PUP/NFI/suspended) suppressed: 71
- No game this week (bye), not scored: 83
- Not on an NFL roster (released, retired or unsigned), not scored: 74
  - of them, per Yahoo (NA) while the weekly roster still says active: 1
- Out/Doubtful on the injury report, not ranked: 2
- Already played as of this run (not re-scored, rank/score carried forward where available): 0
- Team-changed suppressed: 34
- Mixed-team trailing window suppressed: 32
- New-competitor suppressed: 21 (13 via production, 2 via draft, 6 via both)

| Rank | Name | Pos | Team | Score |
|---:|---|---|---|---:|
| 1 | Devin Singletary *(starter back)* | RB | NYG | 0.3593 |
| 2 | Aaron Jones | RB | MIN | 0.3140 |
| 3 | Puka Nacua | WR | LA | 0.3095 |
| 4 | Carnell Tate | WR | TEN | 0.3052 |
| 5 | Mike Washington Jr. | RB | LV | 0.3030 |
| 6 | Efton Chism III *(starter back)* | WR | NE | 0.2926 |
| 7 | Ashton Jeanty | RB | LV | 0.2893 |
| 8 | Justin Jefferson | WR | MIN | 0.2872 |
| 9 | Derrick Henry | RB | BAL | 0.2864 |
| 10 | Jadarian Price *(starter back)* | RB | SEA | 0.2832 |
| 11 | Denzel Boston | WR | CLE | 0.2781 |
| 12 | Tyler Allgeier | RB | ARI | 0.2779 |
| 13 | Malachi Fields *(starter back)* | WR | NYG | 0.2767 |
| 14 | Garrett Wilson | WR | NYJ | 0.2755 |
| 15 | Kaelon Black | RB | SF | 0.2747 |
| 16 | Justice Hill | RB | BAL | 0.2736 |
| 17 | Chris Rodriguez Jr. | RB | JAX | 0.2712 |
| 18 | Mack Hollins | WR | NE | 0.2701 |
| 19 | Matthew Golden | WR | GB | 0.2698 |
| 20 | Jameson Williams | WR | DET | 0.2691 |
| 21 | Rico Dowdle | RB | PIT | 0.2677 |
| 22 | Zay Flowers | WR | BAL | 0.2662 |
| 23 | DeMario Douglas | WR | NE | 0.2646 |
| 24 | Jordan Mason | RB | MIN | 0.2641 |
| 25 | Wan'Dale Robinson | WR | TEN | 0.2615 |

## Starter back (display only)

_A same-position teammate who out-snapped him before his trailing window missed game(s) in it and is available this week (model/starter_back.py). It informs, it doesn't decide: scores, ranks and the tracker's pool are unchanged._

- Devin Singletary (#1): usage built while Cam Skattebo missed week 9; Cam Skattebo not back yet, available this week
- Efton Chism III (#6): usage built while Mack Hollins missed weeks 7-8; Mack Hollins back in week 9 at 68% of snaps (Efton Chism III 51%)
- Jadarian Price (#10): usage built while Zach Charbonnet missed week 3; Zach Charbonnet back in week 9 at 57% of snaps (Jadarian Price 36%)
- Malachi Fields (#13): usage built while Jalin Hyatt missed week 6; Jalin Hyatt back in week 9 at 13% of snaps (Malachi Fields 84%)

## Out/Doubtful (not ranked)

_Designated on the NFL injury report (nflverse) when this was generated; Questionable players are ranked as usual._

- Colson Yankoff (TE, WAS): Out
- TreVeyon Henderson (RB, NE): Out

## Available in my league

The 25 highest-ranked players of this run's pool who were free agents or on waivers in my Yahoo league at 2026-11-12 15:00 UTC (239 of the pool's 347 were). Rank and score are this model's, as in the table above; a waiver date is when Yahoo processes claims on that player. Display only -- scores, ranks and the tracker's pool are unchanged. 903 of the 970 players Yahoo lists as available could be matched to a player id; one that couldn't be cannot appear here.

| Rank | Name | Pos | Team | Score | Status |
|---:|---|---|---|---:|---|
| 1 | Devin Singletary *(starter back)* | RB | NYG | 0.3593 | Waivers (2026-11-13) |
| 5 | Mike Washington Jr. | RB | LV | 0.3030 | Free agent |
| 6 | Efton Chism III *(starter back)* | WR | NE | 0.2926 | Free agent |
| 13 | Malachi Fields *(starter back)* | WR | NYG | 0.2767 | Free agent |
| 16 | Justice Hill | RB | BAL | 0.2736 | Free agent |
| 18 | Mack Hollins | WR | NE | 0.2701 | Free agent |
| 30 | Tre Harris | WR | LAC | 0.2547 | Free agent |
| 33 | Darnell Mooney *(starter back)* | WR | NYG | 0.2509 | Free agent |
| 36 | Dylan Sampson | RB | CLE | 0.2487 | Free agent |
| 38 | Caleb Douglas | WR | MIA | 0.2477 | Free agent |
| 39 | Velus Jones Jr. *(starter back)* | RB | SEA | 0.2473 | Free agent |
| 41 | Rashid Shaheed | WR | SEA | 0.2434 | Free agent |
| 43 | Jameis Winston | QB | NYG | 0.2416 | Free agent |
| 46 | Treylon Burks *(starter back)* | WR | WAS | 0.2400 | Free agent |
| 52 | AJ Dillon | RB | CAR | 0.2372 | Free agent |
| 53 | David Montgomery | RB | HOU | 0.2371 | Free agent |
| 54 | Adonai Mitchell *(starter back)* | WR | NYJ | 0.2366 | Free agent |
| 63 | Jordan Watkins *(starter back)* | WR | SF | 0.2323 | Free agent |
| 64 | Khalil Herbert | RB | ATL | 0.2321 | Free agent |
| 65 | Devaughn Vele | WR | NO | 0.2321 | Waivers (2026-11-13) |
| 66 | Nick Mullens | QB | JAX | 0.2312 | Free agent |
| 67 | CJ Donaldson *(starter back)* | RB | NO | 0.2286 | Free agent |
| 68 | Savion Williams | WR | GB | 0.2283 | Free agent |
| 70 | Austin Ekeler | RB | WAS | 0.2271 | Free agent |
| 73 | Antonio Williams | WR | WAS | 0.2264 | Waivers (2026-11-13) |

_Starter back:_
- Devin Singletary (#1): usage built while Cam Skattebo missed week 9; Cam Skattebo not back yet, available this week
- Efton Chism III (#6): usage built while Mack Hollins missed weeks 7-8; Mack Hollins back in week 9 at 68% of snaps (Efton Chism III 51%)
- Malachi Fields (#13): usage built while Jalin Hyatt missed week 6; Jalin Hyatt back in week 9 at 13% of snaps (Malachi Fields 84%)
- Darnell Mooney (#33): usage built while Jalin Hyatt missed week 6; Jalin Hyatt back in week 9 at 13% of snaps (Darnell Mooney 55%)
- Velus Jones Jr. (#39): usage built while Zach Charbonnet missed weeks 5-7; Zach Charbonnet back in week 9 at 57% of snaps (Velus Jones Jr. 0%)
- Velus Jones Jr. (#39): usage built while Jadarian Price missed weeks 5-7; Jadarian Price back in week 9 at 36% of snaps (Velus Jones Jr. 0%)
- Treylon Burks (#46): usage built while Luke McCaffrey missed week 5; Luke McCaffrey back in week 9 at 15% of snaps (Treylon Burks 0%)
- Adonai Mitchell (#54): usage built while Tim Patrick missed weeks 8-9; Tim Patrick not back yet, available this week
- Adonai Mitchell (#54): usage built while Garrett Wilson missed week 9; Garrett Wilson not back yet, available this week
- Jordan Watkins (#63): usage built while Demarcus Robinson missed weeks 6-7; Demarcus Robinson back in week 9 at 51% of snaps (Jordan Watkins 77%)
- CJ Donaldson (#67): usage built while Audric Estimé missed weeks 4-5; Audric Estimé back in week 9 at 47% of snaps (CJ Donaldson 0%)
