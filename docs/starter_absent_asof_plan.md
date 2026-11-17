# v3 candidate: fixing the `starter_absent_proxy` leak

Drafted 2026-11-16, before anything runs. The backlog's top-priority model task. **Pre-registered:** the decision rules in section 4 are fixed now. Change them here *before* the dev stage runs, never after.

## 1. What's wrong

`starter_absent_proxy` is 1 when a player's presumed starter (the top trailing-touch-share teammate at his position) **did not play in the week being predicted**. In training that's known for about 94% of rows. Live, the week hasn't happened, so it's NULL for everyone.

That breaks the "strictly before the week" rule and gives production and shadow a gap between training and serving. Live scores only stay consistent because of process: the `--through-week` load and the refusal in `tracker.py log`. It also means the model can't see the one case it was built for: a backup whose starter is ruled out. Week 10's example was Emmett Johnson (KC), who scored 32.10 after production ranked him #171 and shadow #102.

Johnson is also the limit of this fix. **v3 would not have flagged him.** At Thursday's run Walker had no designation. At Sunday's 07:00 run nflverse still had him Questionable (groin, no practice all week); ESPN had him Out, but nflverse decides that disagreement. He was then a game-day inactive. History records that kind of case as "Questionable, then inactive," which isn't knowable before kickoff. Week 10's clean example of what v3 does catch is Gavin Bartholomew (MIN, TE): T.J. Hockenson was designated Out, and Bartholomew went from 9% to 77% of the snaps.

## 2. What the read-only check found (2026-11-16, on a copy of the DB)

These were presumed-starter absences, 2022–2024, RB/WR/TE. The check reproduced the 2026-10-04 count (715, against 712). Each one was classified by the absent starter's week-W status in nflverse's injury report and weekly roster:

| Status of the absent starter | Share |
|---|---:|
| Reserve list (IR etc.) | 40.3% |
| Out | 26.6% |
| Game-day inactive (10.8% of them Questionable, 3.8% undesignated) | 14.6% |
| Active roster, undesignated (likely played with no stat row) | 7.1% |
| Doubtful | 5.7% |
| On another team / practice squad / no roster row | 4.3% |
| Questionable, active | 1.4% |

- **77% are knowable before a Sunday-morning run** (Out, Doubtful, reserve list, gone from the team). By position that's RB 82%, WR 85%, TE 66%.
- **45% are knowable from rosters alone**, which is roughly what a Thursday run sees.
- This settles the Oct 4 open question: most of the 52% that weren't on the injury report were reserve-list players.
- **The week-W roster row doesn't leak.** Of 15,428 RB/WR/TE player-weeks with a stat row, none carried a reserve status in that week's roster row, and only one was designated Doubtful.
- **`INA` (the game-day inactive list) is never used.** It's announced about 90 minutes before kickoff, which is after a Sunday-morning run for every game.
- Two side findings about the existing `_presumed_starter`:
  - It reads the whole season's roster, so in 36 of the 715 cases the "starter" was a player who hadn't joined the team yet.
  - Ties are broken by set order, which changes between Python runs. Regenerating `player_week_features` flipped `starter_absent_proxy` on about 38k rows, all at defensive and offensive-line positions (shared zero touch share). It's left as is, because production reads it.

## 3. The change (patch: `v3_candidate.patch`)

**New feature, `starter_absent_asof`.** It asks the same question from pre-game sources only:
- **Absent** means designated Out or Doubtful, on a reserve list, on the practice squad, released or retired, on another team, or with no weekly-roster row while his team has rows.
- **NULL** when there's no roster data for that week.
- **As-of presumed starter:** a candidate's most recent prior game must have been for this team at this position, and ties are broken by player id.
- **Historical source** (`model/availability.py`): nflverse `injuries_<season>.csv` plus `roster_weekly_<season>.csv`.
- **Live source:** `score_week.py`'s own `teammate_available`, the starter-back check. That's the same designations, weekly roster and Yahoo overrides the run's pool rule uses.

**What stays exactly as it is:**
- Production and shadow inputs. `starter_absent_proxy` is untouched and `PRODUCTION_FEATURE_COLS` is unchanged.
- `labels_player_week`, which stays on the half12 profile.
- The tracker ledger.
- The decision-17 head-to-head.

| File | Change |
|---|---|
| `model/availability.py` | new: pre-game availability (historical builder plus a live wrapper) |
| `model/features_lib.py` | `starter_absent_asof`, `_presumed_starter_asof` (cached, deterministic), `CANDIDATE_V3_FEATURE_COLS` / `CANDIDATE_V3_REMOVED_COLS`; `FeatureEngine(con, availability=None)` |
| `model/generate_player_week_features.py` | `--availability-raw-dir`, `--download-missing` (completed seasons only); adds the column if missing |
| `model/generate_labels_and_breakouts.py` | profile `league_v1` (half12 with a 5-pt passing TD); `--table`; refuses to write a non-half12 profile into `labels_player_week` |
| `model/train_candidate_v3.py` | new: dev / test / final stages, three arms, the pre-registered rule, a one-run guard per stage |
| `model/score_week.py` | `--candidate-model` (default `odds_xgb_model_candidate_v3.joblib`, skipped if missing) and `--no-candidate`; writes `<week>_candidate.json/.md` plus the feature's source and values |
| `evaluation/verify_week.py` | verifies `_candidate.json` against the same half12 labels and logs it under `"candidate"`; cumulative line for the weeks it exists |
| `tracker/tracker.py` | cleanup only: `_candidate` files are deleted on a failed `log`, like the others |

**Why the labels get their own table.** `labels_player_week.fantasy_points_half` also feeds `opponent_position_matchup`, a production and shadow feature. Rewriting it to a 5-pt passing TD would move their live scores in the middle of decision 17. On the copy, the switch flips 176 spike flags (169 QB, 6 WR, 1 TE), so it's essentially a QB-only change.

### Correction, 2026-11-17 (before the dev stage ran): no availability before 2016

The 2022–2024 check in section 2 didn't reach the older seasons: the cloud copy only had the 2022–2025 files (section 7). With every file present, the read-only check was repeated for all seasons. It looked at QB/RB/WR/TE player-weeks with a stat row, so the player **played**, and counted the ones this rule would still call absent:

| Seasons | Played, but called absent |
|---|---:|
| 2010–2015 | 21–26% each season |
| 2016–2025 | 0.0–0.2% each season |

There are two causes, both in the 2010–2015 weekly-roster files.

1. **The rosters are season-level, not weekly.** The files repeat one season roster every week: about 1,750 rows a week, and only 6–9% of players change status within a season (33% in 2018). A starter who went on IR in week 12 shows `RES` from week 1. That's 7–10% of played player-weeks. It's noise, and it's also future information, the kind of leak this change exists to remove.
2. **Five teams use GSIS codes.** Arizona, Baltimore, Cleveland, Houston and St. Louis appear as `ARZ`, `BLT`, `CLV`, `HST` and `SL`, which `data/team_crosswalk.py` doesn't map. Their players read as "on another team" every week, which is about 14% of played player-weeks.

The NULL-coverage check couldn't see either, because the values aren't NULL.

**Choice (option B): availability before 2016 is unknown.**
- `model/availability.py` gets `WEEKLY_ROSTER_FIRST_SEASON = 2016`. Before it, `has_week()` is False and `absent()` returns None, so `starter_absent_asof` is NULL for every row of 2010–2015, the presumed starter's own rows included.
- The five codes are mapped anyway, for robustness (`GSIS_TEAM_ALIASES`).

**Why B rather than the other two options:**
- **Designations only for 2010–2015** would code every reserve-list absence there (about 40% of all absences, section 2) as 0. The feature would then mean something different by era, and live runs never produce that narrower meaning.
- **NULL** is the module's own rule for "not knowable", so wherever the feature has a value it has one meaning.
- **Dropping the seasons** would change the registered training seasons.

Arms, seasons and the decision rule (section 4) are unchanged.

**Guards in `model/train_candidate_v3.py`,** both over RB/WR/TE rows from 2016 on, with both rates reported per season and stored with each stage's record:
- the NULL check (20%), as before;
- **new:** a hard stop if more than 1% of player-weeks in which the player actually played are called absent in any season. That check would have caught this problem.

**Changed in the same correction (no effect on the rule):**
- **Status codes.** History and live runs use one status set (`availability.ABSENT_ROSTER_STATUSES`). The exempt-list codes `E01` and `E14` and the suspended code `RSN` count as absent. The trade codes (`TRC`, `TRD`, `TRT`) are left to the team check.
- **Error handling.** `score_week.py` runs the whole candidate step after production and shadow are written, wrapped so that any error prints `[CANDIDATE SKIPPED]`, removes partial `_candidate` files, and lets the run finish.
- **No new live-DB write.** `generate_labels_and_breakouts.py` writes only the scoring profile actually requested.
- **A pinned feature list.** `evaluation/eval_share_delta_on_2025.py` has its 14 features fixed, so the new column can't change what a re-run trains on.

## 4. Pre-registered rules

Every arm trains on `labels_player_week_league` with the same hyperparameters as production (`scale_pos_weight` 20.13, not re-tuned).

**Population:** every eligible, labeled QB/RB/WR/TE player-week of the evaluation season.

**Arms:**
- `live`: production's 13 features, trained as production was and scored with `starter_absent_proxy` = NULL, which is how production really runs. This is the honest baseline.
- `removed`: 12 features, without the proxy.
- `asof`: 13 features, with the proxy swapped for `starter_absent_asof`.

**Stages:**
1. **dev:** train 2010–2023, evaluate on 2024. Pick `asof` only if its pooled mean precision@10 is **strictly higher** than `removed`'s **and** it is **not lower at RB or WR**. Precision@10 here is the top 10 per position per week, summed. Otherwise pick `removed`. TE and QB are reported but don't decide. Both arms fix the leak; the rule only decides whether the new column earns its place.
2. **test:** train 2010–2024, evaluate on 2025, once, for the record. 2025 isn't virgin: the 10-feature baseline and the share-delta test have both read it.
3. **final:** train 2010–2025 on the picked arm. This writes `odds_xgb_model_candidate_v3.joblib`, with role `candidate`.

**Also reported, not decisive:** PR-AUC, precision@25, calibration (mean top-25 score against realized) and importances.

**Live:** the candidate is the third tracked model from **2026 week 12 `thu`**. It never replaces production in 2026 unless decision 17 is amended in writing first. Weeks 15–17 are decided between production and shadow only, as already registered.

## 5. Run order

Run this after Tuesday's week-10 verification, on a copy of the DB and on a branch.

```
git checkout -b v3-candidate
git apply v3_candidate.patch
copy faab_history_core_v0_1.db faab_history_v3_work.db        # gitignored; never the live DB

python model/generate_labels_and_breakouts.py --db faab_history_v3_work.db ^
    --seasons 2010 2011 2012 2013 2014 2015 2016 2017 2018 2020 2021 2022 2023 2024 2025 2026 ^
    --through-week 10 --profile league_v1 --table labels_player_week_league
python model/generate_player_week_features.py --db faab_history_v3_work.db ^
    --availability-raw-dir nflverse_raw --download-missing      # fetches 2010-2021 rosters + injuries (24 files)

python model/train_candidate_v3.py --db faab_history_v3_work.db --stage dev      # read [DECISION]
python model/train_candidate_v3.py --db faab_history_v3_work.db --stage test  --arm <picked>
python model/train_candidate_v3.py --db faab_history_v3_work.db --stage final --arm <picked>
```

- **Committed records:** `evaluation/results/candidate_v3_dev.json` and `candidate_v3_test.json`.
- **Going live:** once the model file is in the repo root, the next `tracker.py log` scores it automatically and writes the `_candidate` files.
- **Verification:** `verify_week.py` picks the candidate up from the following Tuesday.
- **Docs:** update CLAUDE.md (backlog item, Known model limitations, the prediction-file naming) in the same PR.

## 6. Known gaps, stated up front

- **Thursday runs** see the roster half (45%) but not most designations, because the historical files hold only final designations. Training matches the Sunday slot.
- **The flag is coarse.** It's set for every non-starter at the position (in week 10 that included Brashard Smith and Ben VanSumeren as well as Johnson). Even when leaky, the proxy carried about 1% of the model's gain, so **don't expect this alone to lift a Johnson-type player into the top 25.** That's what the next test, vacated share, is for.
- **Saturday downgrades and game-day inactives are out of reach.** The official report is final on Friday for Sunday games, and nflverse doesn't reliably pick up later downgrades. That's the Walker case. Of 2022–2024 presumed starters who were Questionable and didn't practice on the last practice day, 42% sat (Questionable plus limited practice: 25%; plus full practice: 9%). That's too weak for a yes/no "absent," so it's left to section 8.
- **QB "starter" by touch share is noisy** (rushing volume). It's the same limitation as the rest of the QB scoring.
- **The live Yahoo override has no history.** It only matters where nflverse's roster lags.
- **Out of scope:** vacated share (next, as its own pre-registered group test against this candidate), full league scoring in the labels (sacks, returns, pick-sixes), any calibration fix (reported only), and tracker ledger integration (after the season, or on promotion).

## 7. What was tested before handing this over

These checks were run in a cloud copy. xgboost couldn't be installed there, so the training arithmetic wasn't run.

**Correction, 2026-11-17:** every check below covered **2022–2025 only**, the seasons whose nflverse files the cloud copy had. Nothing earlier was exercised. That's how the 2010–2015 problem in section 3's correction went unseen.

- **Features:** generation ran on a DB copy, with 2022–2025 files present and other seasons NULL. Every existing column was identical to the live table, except the known tie-order flips in `starter_absent_proxy` at non-skill positions.
- **Feature agreement:** on 2022–2024 the new feature agreed with the proxy on 15k of 16.4k rows. Spike rate with the flag set: RB 18.8% against 13.1%, WR 14.7% against 13.7%, TE 6.5% against 9.7%.
- **Labels:** the league profile went to its own table, and the guard refused a write to `labels_player_week`.
- **Training script:** run end to end with a stand-in classifier, which proves only the code paths. That covered the dev decision path, the one-run guard, the NULL-coverage refusal and the final save with its metadata.
- **Scoring:** `score_week.py`, 2026 week 10, Thursday as-of, with a **hand-made** designation (Walker Out). The candidate pass ran and Emmett Johnson got `starter_absent_asof = 1`. 35 of the 350 scored players were flagged. This tests the code path only: with the real Sunday data Walker was Questionable in nflverse, so Johnson would have been 0 (section 1).
- **Verification:** `verify_week.py` logged a `candidate` entry and the cumulative line.

## 8. Next: an availability layer and expected vacated share

This is the design for the step after v3. **It isn't pre-registered yet**; its rules get fixed in their own doc before anything runs, and v3's final model is the baseline it has to beat.

It splits the Walker problem in two:
1. **Will the starter sit?** An availability layer that turns every pre-game source into a probability the player sits.
2. **Where do his touches go?** A model feature, expected vacated share, that the model learns from.

**The availability layer.**
- **Historical mapping** (what training can see), from nflverse's final designation plus the last practice day. From 2022–2024 presumed starters: Out is close to 100%, Questionable with no practice 42%, Questionable with limited practice 25%, Questionable with full practice 9%. Doubtful and the reserve-list and off-roster statuses get their own rates from the same data. The fitting seasons and the test seasons are fixed in the pre-registration.
- **Live** (what a run can see), the same mapping, sharpened by sources history doesn't have: ESPN's injury status and Yahoo's player status. The rule for combining them is fixed in advance.
- **Calibration of the live sources** comes from 2026's own snapshots. These are already captured, so nothing new has to be built to start:
  - `raw/injuries/`: the nflverse injury file pinned at every `log` run since 2026-10-08, practice statuses included.
  - ESPN's `injuryStatus`: inside the daily 06:30 roster-% snapshots (`raw/ownership/`), since 2026-10-03.
  - Yahoo's status for every rostered player: the daily 06:35 league snapshot, since 2026-10-22. Free agents' Yahoo statuses are read at `log` time; if they aren't saved anywhere, saving them is the one small logging gap.
  - The first piece of work is a read-only join of those snapshots with who actually played, to measure how often an ESPN Out (and each other status) really sat. Weeks 5–10 are already there.

**Expected vacated share (the feature).** For each player, add up, over same-position teammates, each teammate's trailing touch share times that teammate's chance of sitting. A backup like Johnson gets about Walker's 45% times a high sit chance. v3's yes/no flag gives every backup at the position the same 1, and it gave Johnson a 0 because Walker wasn't known to be out. A WR4 on a team missing its RB1 gets nothing.

- **Train/serve gap, stated up front.** Training sees probabilities built from designations; live runs see sharper ones built from ESPN and Yahoo as well. The gap is explicit and can be measured week by week, and that's the trade being made.
- **Shown when it fires.** Next to a ranked player, a line such as "Walker likely out (ESPN Out, no practice all week) → about 45% of KC's RB touches open; Johnson is next in line." That's display only, and it's where free-text news fits later: in the explanation and the planned chat layer, not in the ranking.
- **Test.** A pre-registered group test against v3's final model, with precision@10 per position and the label-mechanism check, on the same discipline as every other feature group.
