
#!/usr/bin/env python3
import json, sys, os, gzip, shutil
from pathlib import Path
import argparse, requests

REPO_ROOT = Path(__file__).resolve().parent.parent

def fetch(url):
    r = requests.get(url, timeout=60); r.raise_for_status(); return r.content

def maybe_gunzip_to(bytes_in, out_path: Path):
    try:
        data = gzip.decompress(bytes_in)
        out_path.write_bytes(data); return True
    except Exception:
        out_path.write_bytes(bytes_in); return False

def main():
    ap = argparse.ArgumentParser(description="Minimal fetcher for 2022 using manifest")
    ap.add_argument("--manifest", default=str(REPO_ROOT / "manifest_nflverse.json"))
    ap.add_argument("--outdir", default=str(REPO_ROOT / "nflverse_raw"))
    ap.add_argument("--year", type=int, default=2022)
    args = ap.parse_args()

    out = Path(args.outdir); out.mkdir(exist_ok=True)
    man = json.loads(Path(args.manifest).read_text(encoding="utf-8"))

    # pick 2022 entries
    def pick(entries, key):
        for e in entries:
            name = e["name"].lower()
            if key in name and "2022" in name:
                return e["download_url"]
        return None

    def pick_exact(entries, name):
        for e in entries:
            if e["name"].lower() == name:
                return e["download_url"]
        return None

    ps_url = pick(man.get("player_stats",[]), "stats_player_week_") or pick(man.get("player_stats",[]), "player_stats_")
    # schedules ship as one games.csv covering every season, not a per-year file
    sch_url = pick_exact(man.get("schedules",[]), "games.csv")

    if not ps_url:
        print("[ERROR] Could not find a player-week 2022 CSV in manifest. Re-run refresh with a token.", file=sys.stderr); sys.exit(1)
    print(f"[INFO] Downloading player-week: {ps_url}")
    data = fetch(ps_url)
    maybe_gunzip_to(data, out / "stats_player_week_2022.csv")

    if sch_url:
        print(f"[INFO] Downloading schedules: {sch_url}")
        out.joinpath("games.csv").write_bytes(fetch(sch_url))
    else:
        print("[WARN] No games.csv found in manifest; you can synthesize later.")

    # Also fetch players.csv
    try:
        pu = "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
        print(f"[INFO] Downloading players.csv ...")
        out.joinpath("players.csv").write_bytes(fetch(pu))
    except Exception as e:
        print(f"[WARN] Could not download players.csv automatically: {e}")

    print("[DONE] Wrote files to", out)

if __name__ == "__main__":
    main()
