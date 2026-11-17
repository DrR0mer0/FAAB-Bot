# Predictions: 2026 week 10

Model: `odds_xgb_model_production.joblib` (commit `7f21e5c966e0d82d049181e3965d661425e01643`)

Generated: 2026-11-15T15:00:45.051142+00:00

- Total eligible: 2177
- After position filter (QB/RB/WR/TE): 664
- Stable scored: 288 (281 released from offseason suppression this week)
- Unavailable (reserve/PUP/NFI/suspended) suppressed: 55
- No game this week (bye), not scored: 83
- Not on an NFL roster (released, retired or unsigned), not scored: 71
- Out/Doubtful on the injury report, not ranked: 14
- Game-status designations (Out/Doubtful/Questionable) at generation time: 171 player(s) on 28 of the 28 teams on this week's report -- the week's game statuses were out
- Already played as of this run (not re-scored, rank/score carried forward where available): 77
- Team-changed suppressed: 33
- Mixed-team trailing window suppressed: 27
- New-competitor suppressed: 16 (9 via production, 2 via draft, 5 via both)

| Rank | Name | Pos | Team | Score |
|---:|---|---|---|---:|
| 1 | Mike Washington Jr. | RB | LV | 0.7527 |
| 2 | Justin Jefferson | WR | MIN | 0.7298 |
| 3 | Derrick Henry | RB | BAL | 0.7200 |
| 4 | Quinshon Judkins | RB | CLE | 0.7071 |
| 5 | Travis Etienne *(thin window)* | RB | NO | 0.6945 |
| 6 | Parker Washington | WR | JAX | 0.6875 |
| 7 | Garrett Wilson | WR | NYJ | 0.6855 |
| 8 | Caleb Douglas | WR | MIA | 0.6854 |
| 9 | Adonai Mitchell | WR | NYJ | 0.6816 |
| 10 | Wan'Dale Robinson | WR | TEN | 0.6808 |
| 11 | Tyler Allgeier | RB | ARI | 0.6788 |
| 12 | Xavier Worthy | WR | KC | 0.6777 |
| 13 | Jakobi Meyers | WR | JAX | 0.6765 |
| 14 | Zay Flowers | WR | BAL | 0.6714 |
| 15 | Bhayshul Tuten | RB | JAX | 0.6708 |
| 16 | Drake London | WR | ATL | 0.6683 |
| 17 | Jonathan Taylor | RB | IND | 0.6666 |
| 18 | Shedeur Sanders *(starter back)* | QB | CLE | 0.6629 |
| 19 | Tre Harris | WR | LAC | 0.6625 |
| 20 | Rico Dowdle | RB | PIT | 0.6605 |
| 21 | Kaelon Black | RB | SF | 0.6546 |
| 22 | Denzel Boston | WR | CLE | 0.6524 |
| 23 | Aaron Jones | RB | MIN | 0.6515 |
| 24 | Jordan Mason | RB | MIN | 0.6423 |
| 25 | Devaughn Vele | WR | NO | 0.6373 |

## Starter back (display only)

_A same-position teammate who out-snapped him before his trailing window missed game(s) in it and is available this week (model/starter_back.py). A game he sat out himself counts as 0% only if he was on the team's practice squad, a healthy inactive or a healthy backup that week. It informs, it doesn't decide: scores, ranks and the tracker's pool are unchanged._

- Shedeur Sanders (#18): usage built while Deshaun Watson missed week 9; Deshaun Watson not back yet, available this week

## Thin window (display only)

_He played fewer than 2 of his team's last 3 games, so the 3 games his score is built on reach further back (model/thin_window.py). Display only: scores and ranks are unchanged._

- Travis Etienne (#5): played 0 of NO's last 3 games (missed weeks 6-7, 9); the model's 3-game window is weeks 1-3

## Practice report (display only)

_Latest practice participation on the NFL injury report (nflverse) when this was generated, for ranked players with no game status yet. **DNP** (did not participate) and **Limited** are highlighted. Display only: scores and ranks are unchanged._

- Xavier Worthy (#12): Full (Hamstring)
- **Jakobi Meyers (#13): Limited** (Ankle)
- **Bhayshul Tuten (#15): Limited** (Ankle)
- Jonathan Taylor (#17): Full (Not injury related - resting player)
- Tre Harris (#19): Full (Shoulder)

## Out/Doubtful (not ranked)

_Designated on the NFL injury report (nflverse) when this was generated; Questionable players are ranked as usual._

- Chris Bell (WR, MIA): Doubtful
- Brevin Jordan (TE, HOU): Out
- CJ Donaldson (RB, NO): Out
- Carnell Tate (WR, TEN): Out
- Jake Ferguson (TE, DAL): Out
- Jauan Jennings (WR, MIN): Out
- Jonathan Mingo (WR, DAL): Out
- Josh Jacobs (RB, GB): Out
- Kyler Murray (QB, MIN): Out
- Marvin Harrison Jr. (WR, HOU): Out
- Mike Evans (WR, SF): Out
- Sam Darnold (QB, SEA): Out
- T.J. Hockenson (TE, MIN): Out
- Zachariah Branch (WR, ATL): Out

## Available in my league

The 25 highest-ranked players of this run's pool who were free agents or on waivers in my Yahoo league at 2026-11-15 15:00 UTC (198 of the pool's 288 were). Rank and score are this model's, as in the table above; a waiver date is when Yahoo processes claims on that player. Display only -- scores, ranks and the tracker's pool are unchanged. 903 of the 970 players Yahoo lists as available could be matched to a player id; one that couldn't be cannot appear here.

| Rank | Name | Pos | Team | Score | Status |
|---:|---|---|---|---:|---|
| 1 | Mike Washington Jr. | RB | LV | 0.7527 | Free agent |
| 8 | Caleb Douglas | WR | MIA | 0.6854 | Free agent |
| 9 | Adonai Mitchell | WR | NYJ | 0.6816 | Free agent |
| 12 | Xavier Worthy | WR | KC | 0.6777 | Waivers (2026-11-16) |
| 18 | Shedeur Sanders *(starter back)* | QB | CLE | 0.6629 | Free agent |
| 19 | Tre Harris | WR | LAC | 0.6625 | Free agent |
| 25 | Devaughn Vele | WR | NO | 0.6373 | Free agent |
| 30 | Greg Dulcich | TE | MIA | 0.6310 | Free agent |
| 31 | Drew Lock *(thin window)* | QB | SEA | 0.6298 | Free agent |
| 32 | Cam Ward | QB | TEN | 0.6282 | Free agent |
| 35 | Anthony Richardson *(thin window)* | QB | IND | 0.6227 | Free agent |
| 36 | Mac Jones *(thin window)* | QB | SF | 0.6215 | Free agent |
| 43 | Elic Ayomanor | WR | TEN | 0.6138 | Free agent |
| 45 | Rashid Shaheed *(thin window)* | WR | SEA | 0.6109 | Free agent |
| 46 | Malik Washington | WR | MIA | 0.6100 | Free agent |
| 47 | Justice Hill | RB | BAL | 0.6094 | Free agent |
| 51 | Demarcus Robinson *(thin window)* | WR | SF | 0.6056 | Free agent |
| 52 | Xavier Hutchinson | WR | HOU | 0.6051 | Free agent |
| 53 | Keon Coleman | WR | BUF | 0.6048 | Free agent |
| 54 | Jalen Nailor | WR | LV | 0.6038 | Free agent |
| 55 | Nick Mullens *(thin window)* | QB | JAX | 0.6014 | Free agent |
| 58 | Kenny Pickett *(thin window)* | QB | CAR | 0.6001 | Free agent |
| 59 | David Montgomery *(thin window)* | RB | HOU | 0.5994 | Free agent |
| 60 | Jahan Dotson | WR | ATL | 0.5977 | Free agent |
| 61 | Jordan Watkins *(starter back)* | WR | SF | 0.5975 | Free agent |

_Starter back:_
- Shedeur Sanders (#18): usage built while Deshaun Watson missed week 9; Deshaun Watson not back yet, available this week
- Jordan Watkins (#61): usage built while Demarcus Robinson missed weeks 6-7; Demarcus Robinson back in week 9 at 51% of snaps (Jordan Watkins 77%)

_Thin window (he missed most of his team's recent games, so the games his score is built on reach further back; see the main report's section):_
- Drew Lock (#31): played 1 of SEA's last 3 games (missed weeks 7-8); the model's 3-game window is weeks 1-2, 9
- Anthony Richardson (#35): played 1 of IND's last 3 games (missed weeks 7, 9); the model's 3-game window is 2025 weeks 3, 5 and week 8
- Mac Jones (#36): played 1 of SF's last 3 games (missed weeks 6-7); the model's 3-game window is 2025 week 16 and weeks 2, 9
- Rashid Shaheed (#45): played 1 of SEA's last 3 games (missed weeks 8-9); the model's 3-game window is weeks 5-7
- Demarcus Robinson (#51): played 1 of SF's last 3 games (missed weeks 6-7); the model's 3-game window is weeks 1-2, 9
- Nick Mullens (#55): played 0 of JAX's last 3 games (missed weeks 6, 8-9); the model's 3-game window is 2025 weeks 15, 18 and week 1
- Kenny Pickett (#58): played 1 of CAR's last 3 games (missed weeks 8-9); the model's 3-game window is weeks 1-2, 7
- David Montgomery (#59): played 0 of HOU's last 3 games (missed weeks 6-7, 9); the model's 3-game window is weeks 3-5
