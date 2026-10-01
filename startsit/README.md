# FAAB start/sit simulator

Picks the lineup with the **best chance of beating this week's opponent**, not just the most projected points, and explains the close calls.

From the repo root:

```
python startsit/lineup_sim.py startsit/example_week.json --seed 7
python -m unittest discover -s startsit -v      # stdlib only, no pytest needed
```

Only dependency: `numpy` (already in `requirements.txt`). Runs in well under a second for a normal roster.

`example_week.json` uses made-up numbers and is safe to commit. Week files built from real Yahoo data belong in `league_data/`, which is gitignored, e.g. `league_data/2026_week05_thursday.json`. Reports generated from them belong there too.

## How it fits into FAAB

```
league/ (Yahoo fetcher) ──► roster, slots, statuses, opponent ─┐
                                                               ├─► league_data/<week>.json ──► startsit/lineup_sim.py ──► report / JSON
projection models (+ O.D.D.S. signal) ──► p10 / p50 / p90 ─────┘                                                        (newsletter, chat)
```

The simulator doesn't know or care where numbers come from. When the Yahoo fetch works, the fetcher's only job is to write a `week.json` that matches the contract below. Until then, you can hand-write one or fill medians from any projection source.

## What it does

1. **Simulates** every player on both rosters 20,000 times. Each player's points follow their own floor/median/ceiling, with a right-skewed tail for boom weeks.
2. **Correlates** players who share an NFL team or game. For example, a QB and his WR boom together, and two RBs on the same team split work. This is what makes stacks show up correctly in win probability.
3. **Rolls for availability**: a 75%-to-play player scores zero in 25% of simulations.
4. **Tries every legal lineup** for your slots, including the flex, and picks the one that beats the opponent's total most often.
5. **Explains close calls**: every one-swap alternative, with the win-probability margin and its noise (±), labelled *coin flip*, *lean*, or *clear*, plus the reason (ceiling vs floor, availability, your `note`).

As an underdog it leans toward ceiling; as a favorite, toward floor. When that makes it differ from the highest-projected lineup by more than noise, the report explains why.

## Week file contract (`schema_version: 1`)

Unknown keys are ignored, so the fetcher can include extra fields like `_comment` or Yahoo keys.

### Top level

| Field | Required | Notes |
|---|---|---|
| `schema_version` | no | `1` |
| `season`, `week` | no | shown in the report |
| `stage` | no | `"thursday"` (preliminary lean) or `"sunday"` (final, post-inactives) |
| `league.slots` | no* | starting slots; *defaults to QB, 2 WR, 2 RB, TE, W/R/T with a warning |
| `my_team.name`, `my_team.players` | **players required** | your **whole roster**, bench included |
| `opponent.name`, `opponent.starters` | no | opponent's **starters only**; without them it maximizes points instead |

### `league.slots`

Each entry is one of:
- **A Yahoo-style string:** `"QB"`, `"WR"`, `"W/R/T"`, `"W/R"`, `"Q/W/R/T"` (superflex), `"K"`, `"DEF"`, `"D/ST"`. `BN`, `IR`, `IR+`, and `NA` are ignored, so you can pass Yahoo's roster positions straight through.
- **An object:** `{"name": "FLEX", "eligible": ["WR","RB","TE"]}` or `{"position": "WR", "count": 2}`.

### Player fields

| Field | Required | Notes |
|---|---|---|
| `id` | recommended | stable id (Yahoo `player_key` is ideal). Default: side + name |
| `name`, `pos` | **yes** | `pos` is one of QB / RB / WR / TE / K / DEF (`DST`, `D/ST` accepted) |
| `team`, `opp` | no | NFL team and this week's NFL opponent; turns on correlation. Without them players are treated as independent |
| `p10`, `p50`, `p90` | `p50` **yes** | floor / median / ceiling in **your league's scoring**. `proj` is accepted for `p50` |
| `play_prob` | no | 0–1. Default 1, or from `status` |
| `status` | no | `Q` → 0.80, `GTD` → 0.70, `D` → 0.30 (if no `play_prob`). `O`, `IR`, `SUSP`, `PUP`, `NFI`, `BYE`, `EXEMPT` → 0 |
| `actual` | no | points already scored (game played); replaces the simulation |
| `locked` | no | game has kicked off, so the lineup spot can't change. Defaults to true when `actual` is set |
| `started` | no | with `locked`: was he in your lineup at kickoff? Locked starters must stay; locked bench players can't come in |
| `locked_slot` | no | the slot he's locked into, e.g. `"W/R/T"` |
| `note` | no | short reason shown in the close-call explanation, e.g. `"opportunity edge"` |

If only `p50` is given, floor and ceiling come from rough position defaults (`DEFAULT_SPREADS`), and the report says so. That means you can plug in plain point projections today, and the output gets better once the models produce real quantiles.

Invalid files fail with **every** problem listed at once (`WeekFileError.problems`, exit code 2), so a half-working fetcher gets one complete report instead of a crash on the first bad field.

## Mapping from Yahoo (for the fetcher)

Check these against what the API actually returns once it's working.

| week.json | Yahoo roster data |
|---|---|
| `league.slots` | league settings → roster positions (each with a count) |
| `id` | `player_key` |
| `pos` | the player's display/eligible position |
| `status` | injury status (`Q`, `D`, `O`, `IR`, `SUSP`, `PUP-R`, …) |
| `started`, `locked_slot` | selected position (anything but `BN`/`IR` = started) |
| `locked` | player's lineup spot no longer editable (game started) |
| `actual` | player points for the week, once the game is final |
| opponent `starters` | opponent team's roster for the week, filtered to non-`BN`/`IR` selected positions |

## Output

`run_week(week_dict, n_sims=20000, seed=None)` returns a dict with: `win_prob`, `my_median`, `opp_median`, `lineup` (slot → player), `bench`, `out`, `max_points_lineup` + `max_points_win_prob`, `close_calls` (start / over / margin / se / label / why), `warnings`, and `report_md` (ready for the newsletter or chat). The CLI writes the same with `--json` and `--md`.

Use `--seed` when you want repeatable output, such as a newsletter run you might re-generate.

## Knobs to tune later

All are module-level constants at the top of `lineup_sim.py`:
- `DEFAULT_SPREADS`: fallback floor/ceiling multipliers. Retire them once the opportunity × efficiency models output quantiles.
- `SAME_TEAM_CORR`, `OPP_TEAM_CORR`: correlation priors. You can fit these from `player_week_stats` in the history database (correlation of weekly fantasy points by position pair, same team / same game).
- `STATUS_PLAY_PROB`: injury-designation priors. You can fit these from historical designations vs. actual snaps.

As with the model, any fitted constant should come from a chronological split, never from the season it will be used on.

## Where O.D.D.S. plugs in

Upstream, in the projections, and only as a signal. O.D.D.S. ranks low-usage players by spike-week likelihood; it isn't a start/sit model for established starters and shouldn't be used as one. Where it matters here is the edges of the lineup: the flex, bench depth, and a waiver add you're deciding whether to start. A high O.D.D.S. rank can widen that player's `p90` and set `note` (e.g. `"O.D.D.S. top-10"`) so the reason shows up in the close-call explanation. The simulator stays model-agnostic, so you can A/B projection sources by running the same week file twice.

## Backtesting idea

For past weeks, build week files with the projections you *would have had* plus real `actual`s. Run the simulator with actuals removed, then score its lineup with the actuals. Track two numbers: **points left on the bench** vs. the hindsight-optimal lineup, and **record on calls labelled lean/clear** vs. just starting Yahoo's top projections. As with O.D.D.S., 2026 weeks can only be judged prospectively, as they happen.
