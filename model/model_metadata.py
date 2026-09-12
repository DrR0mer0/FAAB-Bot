#!/usr/bin/env python3
"""Shared helper for writing a metadata sidecar next to a trained model
.joblib file. The .joblib itself is gitignored (regeneratable, binary);
the sidecar is a small, committed JSON file, so provenance -- training
seasons, feature list, hyperparameters, and the exact code commit that
produced the model -- survives even though the model artifact doesn't.
"""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def git_commit_hash(repo_root):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root),
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def metadata_path_for(model_path):
    """odds_xgb_model_production.joblib -> odds_xgb_model_production.meta.json"""
    model_path = Path(model_path)
    return model_path.parent / (model_path.stem + ".meta.json")


def write_metadata(model_path, repo_root, feature_cols, model_params, scale_pos_weight, train_seasons, extra=None):
    meta = {
        "model_file": Path(model_path).name,
        "train_seasons": train_seasons,
        "feature_cols": feature_cols,
        "hyperparameters": {**model_params, "scale_pos_weight": scale_pos_weight},
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit_hash(repo_root),
    }
    if extra:
        meta.update(extra)
    meta_path = metadata_path_for(model_path)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return meta_path, meta


def read_metadata(model_path):
    """Returns the metadata dict for a model, or None if no sidecar exists."""
    meta_path = metadata_path_for(model_path)
    if not meta_path.exists():
        return None
    with open(meta_path, encoding="utf-8") as f:
        return json.load(f)
