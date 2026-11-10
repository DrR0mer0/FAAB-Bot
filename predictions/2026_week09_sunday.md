# Predictions: 2026 week 9

Model: `odds_xgb_model_production.joblib` (commit `7f21e5c966e0d82d049181e3965d661425e01643`)

Generated: 2026-11-08T15:29:48.342446+00:00

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
| 1 | Mike Washington Jr. | RB | LV | 0.7485 |
| 2 | Josh Jacobs | RB | GB | 0.7444 |
| 3 | Kenny Gainwell | RB | TB | 0.7089 |
| 4 | Kendre Miller *(starter back)* | RB | NO | 0.7070 |
| 5 | Jordan Mason | RB | MIN | 0.7023 |
| 6 | Rhamondre Stevenson | RB | NE | 0.7004 |
| 7 | Denzel Boston | WR | CLE | 0.6924 |
| 8 | Quinshon Judkins | RB | CLE | 0.6897 |
| 9 | Kaelon Black | RB | SF | 0.6896 |
| 10 | Josh Downs | WR | IND | 0.6772 |
| 11 | Jaylen Waddle | WR | DEN | 0.6762 |
| 12 | Justin Jefferson | WR | MIN | 0.6752 |
| 13 | Omarion Hampton | RB | LAC | 0.6720 |
| 14 | Jonah Coleman | RB | DEN | 0.6684 |
| 15 | Jordan Addison | WR | MIN | 0.6605 |
| 16 | Chris Bell *(starter back)* | WR | MIA | 0.6595 |
| 17 | Omar Cooper Jr. *(starter back)* | WR | NYJ | 0.6582 |
| 18 | Tanner McKee | QB | PHI | 0.6581 |
| 19 | Tre Harris | WR | LAC | 0.6554 |
| 20 | Jalen Coker | WR | CAR | 0.6536 |
| 21 | Kevin Coleman Jr. *(starter back)* | WR | MIA | 0.6511 |
| 22 | Greg Dulcich | TE | MIA | 0.6474 |
| 23 | Saquon Barkley | RB | PHI | 0.6473 |
| 24 | Tyler Allgeier | RB | ARI | 0.6463 |
| 25 | Antonio Williams *(starter back)* | WR | WAS | 0.6446 |

## Starter back (display only)

_A same-position teammate who out-snapped him before his trailing window missed game(s) in it and is available this week (model/starter_back.py). It informs, it doesn't decide: scores, ranks and the tracker's pool are unchanged._

- Kendre Miller (#4): usage built while Audric Estimé missed weeks 5, 7; Audric Estimé not back yet, available this week
- Chris Bell (#16): usage built while Caleb Douglas missed week 5; Caleb Douglas back in week 8 at 86% of snaps (Chris Bell 69%)
- Omar Cooper Jr. (#17): usage built while Adonai Mitchell missed week 6; Adonai Mitchell back in week 8 at 61% of snaps (Omar Cooper Jr. 78%)
- Kevin Coleman Jr. (#21): usage built while Caleb Douglas missed week 5; Caleb Douglas back in week 8 at 86% of snaps (Kevin Coleman Jr. 26%)
- Antonio Williams (#25): usage built while Luke McCaffrey missed week 5; Luke McCaffrey back in week 8 at 5% of snaps (Antonio Williams 83%)

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
| 1 | Mike Washington Jr. | RB | LV | 0.7485 | Free agent |
| 3 | Kenny Gainwell | RB | TB | 0.7089 | Free agent |
| 4 | Kendre Miller *(starter back)* | RB | NO | 0.7070 | Free agent |
| 16 | Chris Bell *(starter back)* | WR | MIA | 0.6595 | Free agent |
| 17 | Omar Cooper Jr. *(starter back)* | WR | NYJ | 0.6582 | Free agent |
| 18 | Tanner McKee | QB | PHI | 0.6581 | Free agent |
| 19 | Tre Harris | WR | LAC | 0.6554 | Free agent |
| 21 | Kevin Coleman Jr. *(starter back)* | WR | MIA | 0.6511 | Free agent |
| 22 | Greg Dulcich | TE | MIA | 0.6474 | Free agent |
| 26 | Anthony Richardson | QB | IND | 0.6439 | Free agent |
| 27 | Mack Hollins | WR | NE | 0.6433 | Free agent |
| 29 | Caleb Douglas | WR | MIA | 0.6417 | Free agent |
| 31 | DeMario Douglas | WR | NE | 0.6389 | Free agent |
| 32 | Mac Jones | QB | SF | 0.6377 | Free agent |
| 36 | Khalil Shakir | WR | BUF | 0.6295 | Free agent |
| 40 | Courtland Sutton | WR | DEN | 0.6236 | Free agent |
| 41 | Adonai Mitchell | WR | NYJ | 0.6229 | Free agent |
| 43 | Daniel Jones *(starter back)* | QB | IND | 0.6227 | Free agent |
| 44 | Tyson Bagent *(starter back)* | QB | CHI | 0.6221 | Free agent |
| 45 | Drew Lock | QB | SEA | 0.6204 | Free agent |
| 46 | Kenny Pickett | QB | CAR | 0.6201 | Free agent |
| 50 | Efton Chism III *(starter back)* | WR | NE | 0.6139 | Free agent |
| 54 | Dylan Sampson | RB | CLE | 0.6063 | Free agent |
| 59 | Marcus Mariota *(starter back)* | QB | WAS | 0.6046 | Free agent |
| 61 | Justin Herbert | QB | LAC | 0.6022 | Free agent |

_Starter back:_
- Kendre Miller (#4): usage built while Audric Estimé missed weeks 5, 7; Audric Estimé not back yet, available this week
- Chris Bell (#16): usage built while Caleb Douglas missed week 5; Caleb Douglas back in week 8 at 86% of snaps (Chris Bell 69%)
- Omar Cooper Jr. (#17): usage built while Adonai Mitchell missed week 6; Adonai Mitchell back in week 8 at 61% of snaps (Omar Cooper Jr. 78%)
- Kevin Coleman Jr. (#21): usage built while Caleb Douglas missed week 5; Caleb Douglas back in week 8 at 86% of snaps (Kevin Coleman Jr. 26%)
- Daniel Jones (#43): usage built while Riley Leonard missed weeks 6-8; Riley Leonard not back yet, available this week
- Tyson Bagent (#44): usage built while Caleb Williams missed weeks 4-5; Caleb Williams back in week 8 at 100% of snaps (Tyson Bagent 0%)
- Efton Chism III (#50): usage built while Mack Hollins missed weeks 6-8; Mack Hollins not back yet, available this week
- Marcus Mariota (#59): usage built while Jayden Daniels missed weeks 3-4; Jayden Daniels back in week 8 at 100% of snaps (Marcus Mariota 0%)
