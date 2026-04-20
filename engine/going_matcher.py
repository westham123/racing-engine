# Racing Engine — Going Matcher
# Version: 1.0
# Date: 20 April 2026
# Purpose: Scores how well a runner's going history matches today's going.
#          Horses that have won on similar going score higher.
#          Horses that have never run on today's going score lower.

# ── Going Groups ────────────────────────────────────────────────────────────
# Groups similar going conditions together.
# A horse that won on Good-Firm counts well for Good, etc.
# Each group is ordered from firmest to softest.

GOING_FIRMNESS = {
    # Flat / AW
    "hard":               0,
    "firm":               1,
    "good to firm":       2,
    "good":               3,
    "good to soft":       4,
    "soft":               5,
    "heavy":              6,
    # Jumps
    "good to yielding":   3,   # Irish equivalent of Good to Soft
    "yielding":           4,   # Irish equivalent of Soft
    "yielding to soft":   5,
    # All-weather
    "standard":           2,
    "standard to slow":   3,
    "slow":               4,
    # Aliases
    "g/f":                2,
    "gd/fm":              2,
    "g/s":                4,
    "gd/sft":             4,
    "g/y":                3,
}

# Maximum firmness distance before score drops to near-zero
MAX_DISTANCE = 3

# Score table: how much credit based on best result on similar going
RESULT_SCORES = {
    "win":   1.00,
    "place": 0.55,
    "run":   0.20,
    "none":  0.05,   # Never run on similar going
}


def _normalise_going(going_str: str) -> str:
    """Lowercase, strip and normalise going string."""
    if not going_str:
        return ""
    return going_str.lower().strip()


def _going_distance(today_going: str, historical_going: str) -> int:
    """
    Returns firmness distance between two going descriptions.
    0 = identical. 1 = adjacent. 3+ = very different.
    Returns MAX_DISTANCE + 1 if either is unknown.
    """
    today_norm = _normalise_going(today_going)
    hist_norm = _normalise_going(historical_going)

    today_val = GOING_FIRMNESS.get(today_norm)
    hist_val = GOING_FIRMNESS.get(hist_norm)

    if today_val is None or hist_val is None:
        return MAX_DISTANCE + 1   # Unknown — treat as dissimilar

    return abs(today_val - hist_val)


def score_going_preference(
    today_going: str,
    going_history: list,     # List of dicts: {"going": str, "position": int or str}
) -> dict:
    """
    Scores how well a runner suits today's going.

    Args:
        today_going:   e.g. "Good to Firm"
        going_history: list of past runs with going and finishing position
                       e.g. [{"going": "Good", "position": 1}, {"going": "Soft", "position": 4}]

    Returns:
        dict with keys:
            score          — 0 to 1 going preference score
            best_result    — "win" / "place" / "run" / "none"
            runs_on_similar — number of runs on similar going
            wins_on_similar — wins on similar going
    """
    if not going_history:
        return _unknown_result(today_going)

    runs_on_similar = 0
    wins_on_similar = 0
    places_on_similar = 0
    best_result = "none"

    for run in going_history:
        run_going = run.get("going", "")
        position = run.get("position")

        dist = _going_distance(today_going, run_going)
        if dist > MAX_DISTANCE:
            continue   # Too different — ignore this run

        runs_on_similar += 1

        # Determine result category
        try:
            pos_int = int(str(position).strip())
        except (ValueError, TypeError):
            pos_int = 99

        if pos_int == 1:
            wins_on_similar += 1
            best_result = "win"
        elif pos_int in (2, 3) and best_result != "win":
            places_on_similar += 1
            best_result = "place"
        elif best_result == "none":
            best_result = "run"

    # Base score from best result on similar going
    base_score = RESULT_SCORES[best_result]

    # Bonus for multiple wins on similar going
    win_bonus = min(wins_on_similar * 0.05, 0.15)

    # Bonus for multiple runs on similar going (proven stayer in conditions)
    run_bonus = min(runs_on_similar * 0.02, 0.08)

    score = min(base_score + win_bonus + run_bonus, 1.0)

    return {
        "score": round(score, 4),
        "best_result": best_result,
        "runs_on_similar": runs_on_similar,
        "wins_on_similar": wins_on_similar,
    }


def _unknown_result(today_going: str):
    """No going history — return neutral result."""
    return {
        "score": 0.30,   # Neutral — no evidence either way
        "best_result": "unknown",
        "runs_on_similar": 0,
        "wins_on_similar": 0,
    }


def score_going_from_form_string(today_going: str, form_string: str = None) -> dict:
    """
    Lightweight fallback: when we only have a form string and no detailed history,
    return a neutral going score. Full scoring requires going_history list.
    This slot is reserved for when The Racing API is wired in (provides full going history).
    """
    # Until The Racing API is verified, return neutral
    return _unknown_result(today_going)
