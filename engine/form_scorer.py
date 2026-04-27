# Racing Engine — Trainer & Jockey Form Scorer
# Version: 1.0
# Date: 20 April 2026
# Purpose: Calculates rolling win rates for trainers and jockeys
#          from settled race results captured by live_data.py.
#          Stores rolling stats. Used as two of the 8 model signals.

import json
import os
from datetime import datetime, timedelta, date
from collections import defaultdict


# ── Storage path ─────────────────────────────────────────────
# Results are stored as a simple JSON file in the learning/ folder.
# The learning loop will upgrade this to a proper DB in a future version.
STORE_PATH = os.path.join(os.path.dirname(__file__), "..", "learning", "results_store.json")


def _load_store() -> dict:
    """Load the results store from disk. Returns empty dict if not found."""
    try:
        if os.path.exists(STORE_PATH):
            with open(STORE_PATH, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"results": []}


def _save_store(store: dict):
    """Save results store to disk."""
    try:
        os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
        with open(STORE_PATH, "w") as f:
            json.dump(store, f, indent=2, default=str)
    except Exception as e:
        print(f"[FormScorer] Could not save store: {e}")


def record_result(race_date: str, course: str, race_time: str,
                  winner: str, jockey: str, trainer: str,
                  odds: str = None):
    """
    Records a settled race result into the store.
    Called by the settlement engine after each race.

    Args:
        race_date:  ISO date string e.g. "2026-04-20"
        course:     e.g. "Cheltenham"
        race_time:  e.g. "14:00"
        winner:     Horse name
        jockey:     Jockey name
        trainer:    Trainer name
        odds:       Winning odds (optional)
    """
    store = _load_store()
    store["results"].append({
        "date": race_date,
        "course": course,
        "time": race_time,
        "winner": winner,
        "jockey": jockey,
        "trainer": trainer,
        "odds": odds,
        "recorded_at": datetime.now().isoformat(),
    })
    _save_store(store)


def _get_results_since(days: int) -> list:
    """Return all results from the last N days."""
    store = _load_store()
    cutoff = date.today() - timedelta(days=days)
    results = []
    for r in store.get("results", []):
        try:
            r_date = date.fromisoformat(str(r["date"])[:10])
            if r_date >= cutoff:
                results.append(r)
        except Exception:
            continue
    return results


def _count_runs_for(results: list, name: str, role: str) -> tuple:
    """
    Count wins and total runs for a trainer or jockey from a results list.
    role: "trainer" or "jockey"

    Returns (wins, total_wins_in_period)
    Note: We only have winners in our store. To get total runs we'd need The Racing API.
    Until then, use wins as a proxy (more wins = better recent form).
    """
    wins = sum(1 for r in results if r.get(role, "").lower() == name.lower())
    return wins


def score_trainer_form(trainer_name: str) -> dict:
    """
    Scores a trainer's recent form.

    Returns:
        dict with score (0–1), wins_14d, wins_30d
    """
    if not trainer_name or trainer_name in ("-", "Unknown", ""):
        return {"score": 0.50, "wins_14d": 0, "wins_30d": 0, "note": "unknown"}

    results_14d = _get_results_since(14)
    results_30d = _get_results_since(30)

    wins_14d = _count_runs_for(results_14d, trainer_name, "trainer")
    wins_30d = _count_runs_for(results_30d, trainer_name, "trainer")

    # Score: normalised against typical top-trainer benchmarks
    # A trainer with 5+ wins in 14 days is in excellent form
    # We cap at 1.0. Minimum 0.1 (in the data, just no wins recently).
    score_14d = min(wins_14d / 5.0, 1.0) * 0.60   # 14-day wins weighted 60%
    score_30d = min(wins_30d / 12.0, 1.0) * 0.40  # 30-day wins weighted 40%

    combined = score_14d + score_30d

    # If store is sparse or this trainer has no recorded wins, fall back to
    # neutral. v2.5.45: raised threshold from 5 to 50 — with only ~10 results
    # in store, individual trainers almost always have 0 wins, returning 0.0
    # and dragging confidence to the floor. Until the results store builds
    # meaningful coverage, prefer neutral over false-zero signals.
    if wins_30d == 0 and len(_get_results_since(30)) < 50:
        return {"score": 0.50, "wins_14d": 0, "wins_30d": 0, "note": "insufficient_data"}

    return {
        "score": round(combined, 4),
        "wins_14d": wins_14d,
        "wins_30d": wins_30d,
        "note": "ok",
    }


def score_jockey_form(jockey_name: str) -> dict:
    """
    Scores a jockey's recent form.

    Returns:
        dict with score (0–1), wins_14d, wins_30d
    """
    if not jockey_name or jockey_name in ("-", "Unknown", ""):
        return {"score": 0.50, "wins_14d": 0, "wins_30d": 0, "note": "unknown"}

    results_14d = _get_results_since(14)
    results_30d = _get_results_since(30)

    wins_14d = _count_runs_for(results_14d, jockey_name, "jockey")
    wins_30d = _count_runs_for(results_30d, jockey_name, "jockey")

    # Top jockeys typically ride 4–8 winners per week
    # 5 wins in 14 days = very good form
    score_14d = min(wins_14d / 5.0, 1.0) * 0.60
    score_30d = min(wins_30d / 12.0, 1.0) * 0.40

    combined = score_14d + score_30d

    # v2.5.45: raised threshold from 5 to 50 (see score_trainer_form).
    if wins_30d == 0 and len(_get_results_since(30)) < 50:
        return {"score": 0.50, "wins_14d": 0, "wins_30d": 0, "note": "insufficient_data"}

    return {
        "score": round(combined, 4),
        "wins_14d": wins_14d,
        "wins_30d": wins_30d,
        "note": "ok",
    }


def get_top_trainers(n: int = 10) -> list:
    """Returns top N trainers by wins in the last 30 days."""
    results_30d = _get_results_since(30)
    counts = defaultdict(int)
    for r in results_30d:
        t = r.get("trainer", "")
        if t:
            counts[t] += 1
    sorted_trainers = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [{"trainer": t, "wins_30d": w} for t, w in sorted_trainers[:n]]


def get_top_jockeys(n: int = 10) -> list:
    """Returns top N jockeys by wins in the last 30 days."""
    results_30d = _get_results_since(30)
    counts = defaultdict(int)
    for r in results_30d:
        j = r.get("jockey", "")
        if j:
            counts[j] += 1
    sorted_jockeys = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [{"jockey": j, "wins_30d": w} for j, w in sorted_jockeys[:n]]
