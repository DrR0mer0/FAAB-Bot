# Predictions: 2026 week 9

Model: `odds_xgb_model_production_10feature.joblib` (commit `262a04fda4a39cb4c16e568f1d28034a399169e7`)

Generated: 2026-11-08T15:29:48.371001+00:00

- Total eligible: 2160
- After position filter (QB/RB/WR/TE): 658
- Stable scored: 307 (306 released from offseason suppression this week)
- Unavailable (reserve/PUP/NFI/suspended) suppressed: 63
- Long-term roster status overruled by Yahoo's current status (scored): 4
- No game this week (bye), not scored: 39
- Not on an NFL roster (released, retired or unsigned), not scored: 71
- Out/Doubtful on the injury report, not ranked: 21
- Already played as of this run (not re-scored, rank/score carried forward where available): 69
- Team-changed suppressed: 34
- Mixed-team trailing window suppressed: 30
- New-competitor suppressed: 24 (16 via production, 0 via draft, 8 via both)

| Rank | Name | Pos | Team | Score |
|---:|---|---|---|---:|
| 1 | Josh Jacobs | RB | GB | 0.3783 |
| 2 | Kenny Gainwell | RB | TB | 0.3425 |
| 3 | Mike Washington Jr. | RB | LV | 0.3397 |
| 4 | Jadarian Price *(starter back)* | RB | SEA | 0.2994 |
| 5 | Omarion Hampton | RB | LAC | 0.2967 |
| 6 | Jordan Mason | RB | MIN | 0.2941 |
| 7 | Chris Bell *(starter back)* | WR | MIA | 0.2935 |
| 8 | Rachaad White | RB | WAS | 0.2929 |
| 9 | Rhamondre Stevenson | RB | NE | 0.2922 |
| 10 | Jonah Coleman | RB | DEN | 0.2878 |
| 11 | Seth McGowan | RB | IND | 0.2758 |
| 12 | Jordan Addison | WR | MIN | 0.2757 |
| 13 | Kaelon Black | RB | SF | 0.2734 |
| 14 | Alvin Kamara *(starter back)* | RB | NO | 0.2714 |
| 15 | Quinshon Judkins | RB | CLE | 0.2707 |
| 16 | Kendre Miller *(starter back)* | RB | NO | 0.2696 |
| 17 | Josh Downs | WR | IND | 0.2693 |
| 18 | Kimani Vidal | RB | LAC | 0.2670 |
| 19 | Malik Washington | WR | MIA | 0.2661 |
| 20 | Justin Jefferson | WR | MIN | 0.2623 |
| 21 | Corey Kiner | RB | NE | 0.2588 |
| 22 | Dylan Sampson | RB | CLE | 0.2586 |
| 23 | Jaxon Smith-Njigba | WR | SEA | 0.2579 |
| 24 | Mack Hollins | WR | NE | 0.2537 |
| 25 | Matthew Golden | WR | GB | 0.2516 |

## Starter back (display only)

_A same-position teammate who out-snapped him before his trailing window missed game(s) in it and is available this week (model/starter_back.py). It informs, it doesn't decide: scores, ranks and the tracker's pool are unchanged._

- Jadarian Price (#4): usage built while Zach Charbonnet missed weeks 2-3; Zach Charbonnet back in week 8 at 37% of snaps (Jadarian Price 48%)
- Chris Bell (#7): usage built while Caleb Douglas missed week 5; Caleb Douglas back in week 8 at 86% of snaps (Chris Bell 69%)
- Alvin Kamara (#14): usage built while Audric Estimé missed weeks 5, 7; Audric Estimé not back yet, available this week
- Kendre Miller (#16): usage built while Audric Estimé missed weeks 5, 7; Audric Estimé not back yet, available this week

## Out/Doubtful (not ranked)

_Designated on the NFL injury report (nflverse) when this was generated; Questionable players are ranked as usual._

- Cam Skattebo (RB, NYG): Doubtful
- A.J. Brown (WR, NE): Out
- Bam Knight (RB, ARI): Out
- Britain Covey (WR, PHI): Out
- CJ Donaldson (RB, NO): Out
- David Montgomery (RB, HOU): Out
- DeVonta Smith (WR, PHI): Out
- Deebo Samuel Sr. (WR, SF): Out
- Demond Claiborne (RB, MIN): Out
- Dontayvion Wicks (WR, PHI): Out
- Dyami Brown (WR, WAS): Out
- Garrett Wilson (WR, NYJ): Out
- Jauan Jennings (WR, MIN): Out
- Josh Palmer (WR, BUF): Out
- KaVontae Turpin (WR, DAL): Out
- Kyler Murray (QB, MIN): Out
- Mike Evans (WR, SF): Out
- Rashid Shaheed (WR, SEA): Out
- Sam Darnold (QB, SEA): Out
- TreVeyon Henderson (RB, NE): Out
- Treylon Burks (WR, WAS): Out

## Available in my league

The 25 highest-ranked players of this run's pool who were free agents or on waivers in my Yahoo league at 2026-11-08 15:29 UTC (208 of the pool's 307 were). Rank and score are this model's, as in the table above; a waiver date is when Yahoo processes claims on that player. Display only -- scores, ranks and the tracker's pool are unchanged. 904 of the 971 players Yahoo lists as available could be matched to a player id; one that couldn't be cannot appear here.

| Rank | Name | Pos | Team | Score | Status |
|---:|---|---|---|---:|---|
| 2 | Kenny Gainwell | RB | TB | 0.3425 | Free agent |
| 3 | Mike Washington Jr. | RB | LV | 0.3397 | Free agent |
| 7 | Chris Bell *(starter back)* | WR | MIA | 0.2935 | Free agent |
| 16 | Kendre Miller *(starter back)* | RB | NO | 0.2696 | Free agent |
| 18 | Kimani Vidal | RB | LAC | 0.2670 | Free agent |
| 19 | Malik Washington | WR | MIA | 0.2661 | Free agent |
| 21 | Corey Kiner | RB | NE | 0.2588 | Free agent |
| 22 | Dylan Sampson | RB | CLE | 0.2586 | Free agent |
| 24 | Mack Hollins | WR | NE | 0.2537 | Free agent |
| 26 | DeMario Douglas | WR | NE | 0.2491 | Free agent |
| 27 | Jaleel McLaughlin *(starter back)* | RB | CLE | 0.2490 | Free agent |
| 28 | Caleb Douglas | WR | MIA | 0.2471 | Free agent |
| 29 | DJ Giddens | RB | IND | 0.2467 | Free agent |
| 35 | Savion Williams | WR | GB | 0.2427 | Free agent |
| 42 | Tre Harris | WR | LAC | 0.2366 | Free agent |
| 43 | Omar Cooper Jr. *(starter back)* | WR | NYJ | 0.2355 | Free agent |
| 44 | Daniel Jones *(starter back)* | QB | IND | 0.2333 | Free agent |
| 47 | Greg Dulcich | TE | MIA | 0.2283 | Free agent |
| 49 | Efton Chism III *(starter back)* | WR | NE | 0.2275 | Free agent |
| 50 | Cooper Kupp | WR | SEA | 0.2274 | Free agent |
| 52 | Trevor Etienne | RB | CAR | 0.2270 | Free agent |
| 54 | Courtland Sutton | WR | DEN | 0.2254 | Free agent |
| 56 | Xavier Hutchinson | WR | HOU | 0.2249 | Free agent |
| 59 | Sione Vaki | RB | DET | 0.2235 | Free agent |
| 60 | AJ Dillon | RB | CAR | 0.2235 | Free agent |

_Starter back:_
- Chris Bell (#7): usage built while Caleb Douglas missed week 5; Caleb Douglas back in week 8 at 86% of snaps (Chris Bell 69%)
- Kendre Miller (#16): usage built while Audric Estimé missed weeks 5, 7; Audric Estimé not back yet, available this week
- Jaleel McLaughlin (#27): usage built while Dylan Sampson missed weeks 5-6; Dylan Sampson back in week 8 at 56% of snaps (Jaleel McLaughlin 3%)
- Jaleel McLaughlin (#27): usage built while Quinshon Judkins missed week 6; Quinshon Judkins back in week 8 at 39% of snaps (Jaleel McLaughlin 3%)
- Omar Cooper Jr. (#43): usage built while Adonai Mitchell missed week 6; Adonai Mitchell back in week 8 at 61% of snaps (Omar Cooper Jr. 78%)
- Daniel Jones (#44): usage built while Riley Leonard missed weeks 6-8; Riley Leonard not back yet, available this week
- Efton Chism III (#49): usage built while Mack Hollins missed weeks 6-8; Mack Hollins not back yet, available this week
