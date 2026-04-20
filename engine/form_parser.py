# Racing Engine — Form Parser
# Version: 1.0
# Date: 20 April 2026
# Purpose: Converts a raw form string (e.g. "080-141") into a numerical score (0–1)
#          Recent runs weighted more heavily than older runs.
#          Long lay-offs flagged automatically.

from datetime import date


# Position weights — most recent run is index 0 (rightmost character)
# Weight decays for older runs. Up to 6 runs considered.
POSITION_WEIGHTS = [0.35, 0.25, 0.18, 0.12, 0.07, 0.03]

# Score per finishing position
POSITION_SCORES = {
    "1": 1.00,   # Winner
    "2": 0.70,   # 2nd
    "3": 0.50,   # 3rd
    "4": 0.30,   # 4th
    "5": 0.15,   # 5th
    "6": 0.10,   # 6th
    "7": 0.07,
    "8": 0.05,
    "9": 0.03,
    "0": 0.01,   # Unplaced (double figures)
    "F": 0.00,   # Fell
    "U": 0.00,   # Unseated
    "P": 0.00,   # Pulled up
    "R": 0.00,   # Refused / Refused to race
    "B": 0.00,   # Brought down
    "C": 0.10,   # Carried out (not rider's fault — slight credit)
    "D": 0.00,   # Disqualified
    "V": 0.00,   # Void
    "S": 0.00,   # Slipped
    "W": 0.00,   # Withdrawn
    "-": None,   # Season break separator — skip
    "/": None,   # Year separator — skip
}

LAY_OFF_THRESHOLD_DAYS = 90   # Flag if not run in 90+ days
LAY_OFF_PENALTY = 0.10        # Reduce score by this amount for long lay-offs


def parse_form(form_string: str, last_ran_days: int = None) -> dict:
    """
    Parses a form string into a weighted numerical score.

    Args:
        form_string: Raw form string, e.g. "080-141" or "1F2-P13"
        last_ran_days: Number of days since last run (optional).
                       If provided, long lay-offs are penalised.

    Returns:
        dict with keys:
            score        — weighted form score (0–1)
            runs         — number of qualifying runs parsed
            wins         — number of wins detected
            places       — number of places (2nd/3rd) detected
            lay_off_flag — True if horse hasn't run in 90+ days
            raw_runs     — list of (char, score) pairs for transparency
    """
    if not form_string or form_string in ("-", "–", "N/A", "", None):
        return _empty_result()

    # Clean the string — remove spaces
    cleaned = str(form_string).strip().replace(" ", "")

    # Extract individual run characters (ignore separators)
    runs_raw = []
    for ch in cleaned:
        score = POSITION_SCORES.get(ch.upper())
        if score is not None:          # None = separator, skip
            runs_raw.append((ch.upper(), score))

    if not runs_raw:
        return _empty_result()

    # Most recent run = last character in the string (rightmost)
    # Reverse so index 0 = most recent
    runs_reversed = list(reversed(runs_raw))

    weighted_score = 0.0
    total_weight = 0.0
    wins = 0
    places = 0

    for i, (ch, pos_score) in enumerate(runs_reversed):
        if i >= len(POSITION_WEIGHTS):
            break
        w = POSITION_WEIGHTS[i]
        weighted_score += pos_score * w
        total_weight += w
        if ch == "1":
            wins += 1
        if ch in ("2", "3"):
            places += 1

    # Normalise to 0–1 based on total weight applied
    if total_weight > 0:
        normalised = weighted_score / total_weight
    else:
        normalised = 0.0

    # Lay-off penalty
    lay_off_flag = False
    if last_ran_days is not None and last_ran_days >= LAY_OFF_THRESHOLD_DAYS:
        lay_off_flag = True
        normalised = max(normalised - LAY_OFF_PENALTY, 0.0)

    return {
        "score": round(normalised, 4),
        "runs": len(runs_reversed[:len(POSITION_WEIGHTS)]),
        "wins": wins,
        "places": places,
        "lay_off_flag": lay_off_flag,
        "raw_runs": runs_reversed[:6],
    }


def _empty_result():
    return {
        "score": 0.33,   # No form data — neutral score
        "runs": 0,
        "wins": 0,
        "places": 0,
        "lay_off_flag": False,
        "raw_runs": [],
    }
