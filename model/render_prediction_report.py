#!/usr/bin/env python3
"""Render predictions/<season>_week<NN>.md from an already-written
predictions/<season>_week<NN>.json, without recomputing or altering the
JSON. score_week.py now writes this automatically alongside every JSON it
produces; this script is for backfilling a report for a JSON that predates
that, or re-rendering one if the markdown template changes later.
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PREDICTIONS_DIR = REPO_ROOT / "predictions"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_week import render_markdown_report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--predictions-dir", default=str(PREDICTIONS_DIR))
    args = ap.parse_args()

    json_path = Path(args.predictions_dir) / f"{args.season}_week{args.week:02d}.json"
    with open(json_path, encoding="utf-8") as f:
        output = json.load(f)

    md_path = json_path.with_suffix(".md")
    md_path.write_text(render_markdown_report(output), encoding="utf-8")
    print(f"[SAVED] {md_path}")


if __name__ == "__main__":
    main()
