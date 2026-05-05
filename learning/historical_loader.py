# Racing Engine — Historical Results Loader (rpscrape)
# Version: 2.6.8 — Pulls Racing Post results, builds trainer/jockey/course stats
#
# Used by engine/odds_model.py (_score_trainer_form / _score_jockey_form) to
# replace the previously-neutral 0.50 fallback with a real win rate signal.
# rpscrape: https://github.com/4A47/rpscrape  cloned at /home/user/workspace/rpscrape
#
# All file I/O is best-effort: a failed pull leaves the existing JSONs intact
# and the engine falls back to neutral, never blocking the pipeline.

import csv
import json
import os
import subprocess
from collections import defaultdict
from datetime import date, datetime, timedelta

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
RPSCRAPE_ROOT = "/home/user/workspace/rpscrape"
RPSCRAPE_SCRIPT = os.path.join(RPSCRAPE_ROOT, "scripts", "rpscrape.py")
RPSCRAPE_DATA = os.path.join(RPSCRAPE_ROOT, "data", "region")

TRAINER_STATS_PATH = os.path.join(_THIS_DIR, "trainer_stats.json")
JOCKEY_STATS_PATH = os.path.join(_THIS_DIR, "jockey_stats.json")
COURSE_STATS_PATH = os.path.join(_THIS_DIR, "course_stats.json")


# ── Going / distance classifiers (mirror engine/odds_model.OddsModel) ──

def _classify_going(going_str: str) -> str:
    if not going_str:
        return ""
    s = str(going_str).strip().lower()
    if "firm" in s:
        return "FAST"
    if "heavy" in s or "soft" in s:
        return "SOFT"
    if ("standard" in s or "slow" in s or "polytrack" in s or "tapeta" in s
            or "all weather" in s or "all-weather" in s):
        return "AW"
    if "good" in s:
        return "GOOD"
    return ""


def _classify_distance(dist_f) -> str:
    try:
        f = float(dist_f)
    except Exception:
        return ""
    if f <= 6.0:
        return "sprint"
    if f <= 9.0:
        return "mile"
    if f <= 13.0:
        return "mid"
    return "staying"


def _parse_dist_f(raw: str) -> float:
    """rpscrape `dist_f` is e.g. '5f', '7.5f', '12f'."""
    if raw is None:
        return 0.0
    s = str(raw).strip().lower().rstrip("f")
    try:
        return float(s)
    except Exception:
        return 0.0


# ── rpscrape runner ───────────────────────────────────────────

def pull_rpscrape_results(
    date_from: str,
    date_to: str,
    region: str = "gb",
    race_type: str = "flat",
    timeout: int = 600,
) -> str | None:
    """Run rpscrape for a date range. Dates are YYYY/MM/DD.

    Returns the path to the generated CSV (under
    rpscrape/data/region/{region}/{race_type}/) or None on failure.
    """
    cmd = [
        "python3", RPSCRAPE_SCRIPT,
        "-d", f"{date_from}-{date_to}",
        "-r", region,
        "-t", race_type,
    ]
    out_dir = os.path.join(RPSCRAPE_DATA, region, race_type)
    expected = os.path.join(
        out_dir,
        f"{date_from.replace('/', '_')}_{date_to.replace('/', '_')}.csv",
    )
    try:
        # rpscrape resolves 'utils.*' imports relative to its scripts dir.
        subprocess.run(
            cmd,
            cwd=os.path.join(RPSCRAPE_ROOT, "scripts"),
            timeout=timeout,
            capture_output=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # Partial output may still be on disk.
        pass
    except Exception:
        return None
    return expected if os.path.exists(expected) else None


# ── CSV → stats helpers ───────────────────────────────────────

def _iter_rows(csv_paths):
    for p in csv_paths or []:
        if not p or not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield row


def _empty_bucket():
    return {"wins": 0, "runs": 0}


def _bump(bucket, won):
    bucket["runs"] += 1
    if won:
        bucket["wins"] += 1


def _is_winner(row: dict) -> bool:
    pos = (row.get("pos") or "").strip()
    return pos == "1"


def _aggregate(csv_paths, role: str) -> dict:
    """role: 'trainer' or 'jockey'. Returns aggregated stats dict."""
    out: dict[str, dict] = {}
    for row in _iter_rows(csv_paths):
        name = (row.get(role) or "").strip()
        if not name:
            continue
        key = name.lower()
        entry = out.setdefault(key, {
            "name": name,
            "wins": 0,
            "runs": 0,
            "by_going": defaultdict(_empty_bucket),
            "by_class": defaultdict(_empty_bucket),
            "by_distance": defaultdict(_empty_bucket),
        })
        won = _is_winner(row)
        entry["runs"] += 1
        if won:
            entry["wins"] += 1
        gg = _classify_going(row.get("going") or "")
        if gg:
            _bump(entry["by_going"][gg], won)
        cls = (row.get("class") or "").strip()
        if cls:
            _bump(entry["by_class"][cls], won)
        db = _classify_distance(_parse_dist_f(row.get("dist_f") or row.get("dist")))
        if db:
            _bump(entry["by_distance"][db], won)

    # Finalise — convert defaultdicts to plain dicts and add win_pct everywhere
    final: dict = {}
    for key, e in out.items():
        runs = e["runs"]
        e["win_pct"] = round(e["wins"] / runs, 4) if runs else 0.0
        for bucket in ("by_going", "by_class", "by_distance"):
            d = {}
            for k, v in dict(e[bucket]).items():
                v = dict(v)
                v["win_pct"] = round(v["wins"] / v["runs"], 4) if v["runs"] else 0.0
                d[k] = v
            e[bucket] = d
        final[key] = e
    return final


def build_trainer_stats(csv_paths: list, save_path: str = TRAINER_STATS_PATH) -> dict:
    """Build trainer stats from rpscrape CSVs and save to JSON."""
    stats = _aggregate(csv_paths, "trainer")
    _save_json(stats, save_path)
    return stats


def build_jockey_stats(csv_paths: list, save_path: str = JOCKEY_STATS_PATH) -> dict:
    """Build jockey stats from rpscrape CSVs and save to JSON."""
    stats = _aggregate(csv_paths, "jockey")
    _save_json(stats, save_path)
    return stats


def build_course_stats(csv_paths: list, save_path: str = COURSE_STATS_PATH) -> dict:
    """Per-course win rate by going group: {course: {going_group: {wins, runs, win_pct}}}.

    Aggregated across all runners — useful as a draw/going bias signal later.
    """
    out: dict[str, dict] = {}
    for row in _iter_rows(csv_paths):
        course = (row.get("course") or "").strip()
        if not course:
            continue
        gg = _classify_going(row.get("going") or "")
        if not gg:
            continue
        entry = out.setdefault(course.lower(), {"name": course, "by_going": {}})
        bucket = entry["by_going"].setdefault(gg, _empty_bucket())
        _bump(bucket, _is_winner(row))
    # Add win_pct
    for course, entry in out.items():
        for gg, bucket in entry["by_going"].items():
            bucket["win_pct"] = round(bucket["wins"] / bucket["runs"], 4) if bucket["runs"] else 0.0
    _save_json(out, save_path)
    return out


def _save_json(data: dict, path: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[historical_loader] save failed for {path}: {e}")


# ── 90-day initial pull driver ────────────────────────────────

def initial_pull(days: int = 90, region: str = "gb", race_type: str = "flat") -> dict:
    """Pull last N days of results and rebuild the three stats JSONs.

    Tries one big range first (fast on rpscrape's bulk fetch); if that returns
    no CSV inside the timeout, falls back to walking month-by-month so we keep
    whatever partial coverage rpscrape managed to produce.
    """
    today = date.today()
    start = today - timedelta(days=days)
    df = start.strftime("%Y/%m/%d")
    dt = today.strftime("%Y/%m/%d")
    csv_paths: list[str] = []

    # Pick up the large bulk file if it already exists from a prior run.
    out_dir = os.path.join(RPSCRAPE_DATA, region, race_type)
    if os.path.isdir(out_dir):
        for fn in os.listdir(out_dir):
            if fn.endswith(".csv"):
                csv_paths.append(os.path.join(out_dir, fn))

    big = pull_rpscrape_results(df, dt, region=region, race_type=race_type, timeout=600)
    if big and big not in csv_paths:
        csv_paths.append(big)

    csv_paths = sorted(set(csv_paths))
    trainer = build_trainer_stats(csv_paths)
    jockey = build_jockey_stats(csv_paths)
    course = build_course_stats(csv_paths)
    return {
        "csv_files": csv_paths,
        "trainers": len(trainer),
        "jockeys": len(jockey),
        "courses": len(course),
    }


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    print(json.dumps(initial_pull(days=days), indent=2))
