# Predictions: 2026 week 10

Model: `odds_xgb_model_production_10feature.joblib` (commit `262a04fda4a39cb4c16e568f1d28034a399169e7`)

Generated: 2026-11-15T15:00:45.080719+00:00

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
| 1 | Aaron Jones | RB | MIN | 0.3140 |
| 2 | Mike Washington Jr. | RB | LV | 0.3030 |
| 3 | Travis Etienne *(thin window)* | RB | NO | 0.2898 |
| 4 | Ashton Jeanty | RB | LV | 0.2893 |
| 5 | Justin Jefferson | WR | MIN | 0.2872 |
| 6 | Derrick Henry | RB | BAL | 0.2864 |
| 7 | Jadarian Price | RB | SEA | 0.2832 |
| 8 | Denzel Boston | WR | CLE | 0.2781 |
| 9 | Tyler Allgeier | RB | ARI | 0.2779 |
| 10 | Garrett Wilson | WR | NYJ | 0.2755 |
| 11 | Kaelon Black | RB | SF | 0.2747 |
| 12 | Justice Hill | RB | BAL | 0.2736 |
| 13 | Chris Rodriguez Jr. | RB | JAX | 0.2712 |
| 14 | Matthew Golden | WR | GB | 0.2698 |
| 15 | Rico Dowdle | RB | PIT | 0.2677 |
| 16 | Zay Flowers | WR | BAL | 0.2662 |
| 17 | Jordan Mason | RB | MIN | 0.2641 |
| 18 | Wan'Dale Robinson | WR | TEN | 0.2615 |
| 19 | Jakobi Meyers | WR | JAX | 0.2591 |
| 20 | Jordan Addison | WR | MIN | 0.2554 |
| 21 | Xavier Worthy | WR | KC | 0.2554 |
| 22 | Tre Harris | WR | LAC | 0.2547 |
| 23 | Deebo Samuel Sr. *(thin window)* | WR | SF | 0.2537 |
| 24 | Quinshon Judkins | RB | CLE | 0.2532 |
| 25 | KC Concepcion | WR | CLE | 0.2506 |

## Thin window (display only)

_He played fewer than 2 of his team's last 3 games, so the 3 games his score is built on reach further back (model/thin_window.py). Display only: scores and ranks are unchanged._

- Travis Etienne (#3): played 0 of NO's last 3 games (missed weeks 6-7, 9); the model's 3-game window is weeks 1-3
- Deebo Samuel Sr. (#23): played 0 of SF's last 3 games (missed weeks 6-7, 9); the model's 3-game window is weeks 3-5

## Practice report (display only)

_Latest practice participation on the NFL injury report (nflverse) when this was generated, for ranked players with no game status yet. **DNP** (did not participate) and **Limited** are highlighted. Display only: scores and ranks are unchanged._

- **Chris Rodriguez Jr. (#13): Limited** (Hand)
- **Jakobi Meyers (#19): Limited** (Ankle)
- Xavier Worthy (#21): Full (Hamstring)
- Tre Harris (#22): Full (Shoulder)
- Deebo Samuel Sr. (#23): Full (Hamstring)

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
| 2 | Mike Washington Jr. | RB | LV | 0.3030 | Free agent |
| 12 | Justice Hill | RB | BAL | 0.2736 | Free agent |
| 21 | Xavier Worthy | WR | KC | 0.2554 | Waivers (2026-11-16) |
| 22 | Tre Harris | WR | LAC | 0.2547 | Free agent |
| 29 | Caleb Douglas | WR | MIA | 0.2477 | Free agent |
| 30 | Velus Jones Jr. *(starter back)* *(thin window)* | RB | SEA | 0.2473 | Free agent |
| 31 | Chris Brooks *(thin window)* | RB | GB | 0.2435 | Free agent |
| 32 | Rashid Shaheed *(thin window)* | WR | SEA | 0.2434 | Free agent |
| 40 | AJ Dillon *(thin window)* | RB | CAR | 0.2372 | Free agent |
| 41 | David Montgomery *(thin window)* | RB | HOU | 0.2371 | Free agent |
| 42 | Adonai Mitchell | WR | NYJ | 0.2366 | Free agent |
| 47 | Jordan Watkins *(starter back)* | WR | SF | 0.2323 | Free agent |
| 48 | Khalil Herbert | RB | ATL | 0.2321 | Free agent |
| 49 | Devaughn Vele | WR | NO | 0.2321 | Free agent |
| 50 | Nick Mullens *(thin window)* | QB | JAX | 0.2312 | Free agent |
| 51 | Savion Williams | WR | GB | 0.2283 | Free agent |
| 54 | Xavier Hutchinson | WR | HOU | 0.2261 | Free agent |
| 55 | Malik Washington | WR | MIA | 0.2260 | Free agent |
| 61 | Jalen Nailor | WR | LV | 0.2222 | Free agent |
| 62 | Elic Ayomanor | WR | TEN | 0.2221 | Free agent |
| 63 | DJ Giddens *(thin window)* | RB | IND | 0.2207 | Free agent |
| 65 | Cam Ward | QB | TEN | 0.2201 | Free agent |
| 67 | Daniel Jones *(starter back)* | QB | IND | 0.2183 | Free agent |
| 69 | Jerry Jeudy | WR | CLE | 0.2179 | Free agent |
| 73 | Trevor Etienne | RB | CAR | 0.2141 | Free agent |

_Starter back:_
- Velus Jones Jr. (#30): usage built while Zach Charbonnet missed weeks 5-7; Zach Charbonnet back in week 9 at 57% of snaps (Velus Jones Jr. did not play)
- Velus Jones Jr. (#30): usage built while Jadarian Price missed weeks 5-7; Jadarian Price back in week 9 at 36% of snaps (Velus Jones Jr. did not play)
- Jordan Watkins (#47): usage built while Demarcus Robinson missed weeks 6-7; Demarcus Robinson back in week 9 at 51% of snaps (Jordan Watkins 77%)
- Daniel Jones (#67): usage built while Riley Leonard missed weeks 7-9; Riley Leonard not back yet, available this week

_Thin window (he missed most of his team's recent games, so the games his score is built on reach further back; see the main report's section):_
- Velus Jones Jr. (#30): played 1 of SEA's last 3 games (missed weeks 8-9); the model's 3-game window is weeks 5-7
- Chris Brooks (#31): played 0 of GB's last 3 games (missed weeks 7-9); the model's 3-game window is weeks 2-4
- Rashid Shaheed (#32): played 1 of SEA's last 3 games (missed weeks 8-9); the model's 3-game window is weeks 5-7
- AJ Dillon (#40): played 0 of CAR's last 3 games (missed weeks 7-9); the model's 3-game window is weeks 2-4
- David Montgomery (#41): played 0 of HOU's last 3 games (missed weeks 6-7, 9); the model's 3-game window is weeks 3-5
- Nick Mullens (#50): played 0 of JAX's last 3 games (missed weeks 6, 8-9); the model's 3-game window is 2025 weeks 15, 18 and week 1
- DJ Giddens (#63): played 1 of IND's last 3 games (missed weeks 7, 9); the model's 3-game window is 2025 weeks 5, 18 and week 8
