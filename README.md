# FAAB-Bot

A personal Fantasy Football analysis assistant, built for my own 12-team, half-PPR, single-QB Yahoo league.

## What it does

FAAB-Bot combines two independent pieces:

- **O.D.D.S. ("Omega Deep Dart Sleeper")** — a machine learning model trained on 15+ years of historical NFL player statistics to identify low-usage players likely to have a breakout ("spike") performance. It's aimed at surfacing deep-roster and waiver-wire targets, not obvious start/sit calls.
- **Weekly league recap** — pulls real league data (standings, matchups, scores) via the Yahoo Fantasy Sports API and turns it into a short, readable weekly write-up for my league.

## Status

This is an active personal project, not a public product or service. It's built and used by a single person (me), for a single private league, with **read-only** access to my own Yahoo Fantasy data. It isn't distributed, hosted publicly, or intended for other users.

## Data sources

- **Historical NFL statistics** — [nflverse](https://github.com/nflverse/nflverse-data), a public, community-maintained open data project.
- **League data** — Yahoo Fantasy Sports API, read-only, authorized for my own account and league only.

## Tech

Python, SQLite, XGBoost.

## Project history

An earlier, abandoned attempt at this project (mostly unimplemented placeholder scripts) is kept for historical context in [`legacy/`](legacy/).

---

*Personal side project. Not affiliated with, endorsed by, or sponsored by the NFL, Yahoo, or any fantasy sports platform.*
