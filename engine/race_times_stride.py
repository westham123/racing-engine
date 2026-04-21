# Racing Engine — Race Times & Stride Pattern Signal
# Version: 1.0
# Date: 21 April 2026
#
# What "race times" and "stride patterns" mean in practice:
#
# RACE TIMES (winning time):
#   The time taken to complete a race at a given distance and going.
#   A horse that consistently wins in FASTER times than average = superior ability.
#   A horse whose personal best time is MUCH faster than today's typical time
#   at this course/distance/going = likely to go close.
#
# STRIDE PATTERNS (proxied from pace/sectional data):
#   Full sectional data (split times, furlongs broken) requires the Racing API.
#   We approximate using the data we DO have:
#     - Distance of race (furlongs)
#     - Winning time (seconds)
#     - Going condition
#   From these we can compute:
#     - Speed rating (yards per second, normalised for going)
#     - "Stride efficiency" proxy: how the horse's typical speed rating compares
#       to the field average = a proxy for whether it has the "engine" for today
#
# HOW IT FEEDS INTO THE MODEL:
#   A new signal "race_pace" is added to the OddsModel, sharing weight
#   previously unused (we had 3% spare on jump_index for flat races).
#   The signal works as follows:
#     - Score 0.75+ = horse has strong time figures for today's conditions
#     - Score 0.50  = neutral / unknown
#     - Score 0.25- = poor time figures or slow overall
#
# DATA SOURCE:
#   Winning times are available on Sporting Life results pages (winning_time field).
#   We fetch course/distance/going averages from our own results store, which
#   builds up over time from the settlement engine. On day 1 we use sector
#   benchmarks from published data below.

import re
import math
from collections import defaultdict

# ── Published par times (seconds per furlong) for standard UK going ──────────
# Source: Racing Post speed ratings methodology (public)
# These are approximate — our engine will refine them from live data
# Format: going_code → seconds per furlong (flat)

PAR_SECS_PER_FURLONG = {
    "Firm":              11.6,
    "Good to Firm":      11.8,
    "Good":              12.0,
    "Good to Soft":      12.4,
    "Soft":              12.9,
    "Heavy":             13.6,
    "Standard":          11.8,   # AW (all-weather)
    "Standard to Slow":  12.2,
    "Slow":              12.8,
}

# Jump races are slower per furlong due to obstacles — rough multipliers
JUMP_MULTIPLIER = {
    "Hurdle": 1.08,
    "Chase":  1.12,
    "Flat":   1.00,
    "Bumper": 1.00,
}


def parse_winning_time(time_str: str) -> float | None:
    """
    Convert winning time string to total seconds.
    Handles formats like:
        "2m 14.30s"   → 134.30
        "1m 52.40s"   → 112.40
        "56.20s"      → 56.20
        "2:14.30"     → 134.30
    Returns None if unparseable.
    """
    if not time_str:
        return None
    s = str(time_str).strip()

    # Pattern: "Xm Ys" or "Xm Y.Zs"
    m = re.match(r"(\d+)m\s*([\d.]+)s?", s, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))

    # Pattern: "X:Y.Z" (colon-separated)
    m = re.match(r"(\d+):(\d+\.?\d*)", s)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))

    # Pattern: just seconds "56.20s" or "56.20"
    m = re.match(r"(\d+\.?\d*)\s*s?$", s, re.IGNORECASE)
    if m:
        return float(m.group(1))

    return None


def distance_to_furlongs(distance_str: str) -> float | None:
    """
    Convert distance string to furlongs.
    Handles: "6f", "1m 2f", "1m 4f", "2m", "2m 4f 110y", "5f 110y"
    1 mile = 8 furlongs. 1 furlong = 220 yards.
    """
    if not distance_str:
        return None
    s = str(distance_str).strip().lower()
    total = 0.0

    # Miles
    m = re.search(r"(\d+)\s*m(?:ile)?", s)
    if m:
        total += int(m.group(1)) * 8.0

    # Furlongs
    m = re.search(r"(\d+)\s*f(?:urlong)?", s)
    if m:
        total += int(m.group(1))

    # Yards
    m = re.search(r"(\d+)\s*y(?:ard)?", s)
    if m:
        total += int(m.group(1)) / 220.0

    return round(total, 3) if total > 0 else None


def compute_speed_rating(
    winning_time_secs: float,
    furlongs: float,
    going: str,
    race_type: str = "Flat",
) -> float | None:
    """
    Compute a speed rating (0-100 scale) from winning time.

    Method:
      1. Get par time (secs/furlong) for this going condition
      2. Apply jump multiplier if NH
      3. Compute expected par time for the distance
      4. Speed rating = how much faster than par (positive = fast, negative = slow)
      5. Normalise to 0-1 scale

    A rating of 100 = exactly par. >100 = faster than par (good). <100 = slower.
    """
    par_spf = PAR_SECS_PER_FURLONG.get(going)
    if not par_spf:
        # Try fuzzy match for going variations
        going_lower = going.lower()
        for k, v in PAR_SECS_PER_FURLONG.items():
            if k.lower() in going_lower or going_lower in k.lower():
                par_spf = v
                break
        if not par_spf:
            par_spf = 12.0   # Default to "Good"

    jump_mult = JUMP_MULTIPLIER.get(race_type, 1.0)
    par_total = par_spf * jump_mult * furlongs

    if par_total <= 0:
        return None

    # Speed index: par / actual. >1 = faster than par (good), <1 = slower (bad)
    speed_index = par_total / winning_time_secs
    # Convert to 0-100 where 100 = par, scale ±15% around par
    rating = 50 + (speed_index - 1.0) * 333.3   # 0.15 deviation = ~50 points either way
    return round(max(0.0, min(100.0, rating)), 1)


class RaceTimesStore:
    """
    Builds a local store of race times from the settlement engine's results.
    Used to:
      1. Record winning times as races settle each day
      2. Compute course/distance/going averages over time
      3. Score a horse's personal best time vs those averages
    """

    def __init__(self, store_path: str = None):
        import os, json
        if store_path is None:
            base = os.path.dirname(os.path.abspath(__file__))
            store_path = os.path.join(base, "..", "learning", "race_times.json")
        self.store_path = os.path.abspath(store_path)
        self._data: dict = {}
        self._load()

    def _load(self):
        import json, os
        if os.path.exists(self.store_path):
            try:
                with open(self.store_path) as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}

    def _save(self):
        import json
        try:
            with open(self.store_path, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    def _key(self, course: str, distance: str, going: str) -> str:
        """Build a normalised lookup key."""
        return f"{course.lower().strip()}|{distance.lower().strip()}|{going.lower().strip()}"

    def record_result(self, course: str, distance: str, going: str,
                      winning_time_str: str, race_type: str = "Flat"):
        """
        Record a winning time result into the store.
        Called by the settlement engine after each result.
        """
        secs = parse_winning_time(winning_time_str)
        if not secs:
            return
        key = self._key(course, distance, going)
        if key not in self._data:
            self._data[key] = {
                "course": course, "distance": distance, "going": going,
                "race_type": race_type, "times": [], "count": 0,
            }
        self._data[key]["times"].append(secs)
        self._data[key]["count"] += 1
        # Keep last 50 races per combination
        self._data[key]["times"] = self._data[key]["times"][-50:]
        self._save()

    def get_par_time(self, course: str, distance: str, going: str) -> float | None:
        """
        Return the average winning time for this course/distance/going.
        Returns None if fewer than 3 results in store (fall back to published pars).
        """
        key = self._key(course, distance, going)
        entry = self._data.get(key)
        if entry and len(entry["times"]) >= 3:
            return round(sum(entry["times"]) / len(entry["times"]), 2)
        return None

    def get_all_par_times(self) -> dict:
        """Return all stored par times (for dashboard display)."""
        out = {}
        for key, entry in self._data.items():
            if len(entry["times"]) >= 3:
                out[key] = {
                    "avg_time": round(sum(entry["times"]) / len(entry["times"]), 2),
                    "count": entry["count"],
                    "course": entry["course"],
                    "distance": entry["distance"],
                    "going": entry["going"],
                }
        return out


# ── Main Scoring Function ─────────────────────────────────────────────────────

def score_race_pace(
    runner_data: dict,
    times_store: RaceTimesStore = None,
    default_score: float = 0.50,
) -> float:
    """
    Score a runner's pace/time suitability for today's race.

    Uses (in priority order):
      1. If times_store has a local par for this course/distance/going → use it
      2. If runner has a recorded personal best time → compare vs published pars
      3. No data → return default_score (neutral 0.50)

    runner_data keys used:
        course        — e.g. "Pontefract"
        distance      — e.g. "6f" or "1m 2f"
        going         — e.g. "Good to Firm"
        race_type     — "Flat", "Hurdle", "Chase" (default "Flat")
        winning_time  — str, the horse's recorded winning time at this dist/going
                        (from our own results store or Sporting Life card data)
        par_time      — float, manual override of par time in seconds (optional)

    Returns a float 0.0–1.0 score.
    """
    course    = runner_data.get("course", "")
    distance  = runner_data.get("distance", "")
    going     = runner_data.get("going", "")
    race_type = runner_data.get("race_type", "Flat")

    furlongs = distance_to_furlongs(distance)
    if not furlongs:
        return default_score

    # ── Step 1: Get par time ──────────────────────────────────────────────────
    par_time = runner_data.get("par_time")

    if not par_time and times_store:
        par_time = times_store.get_par_time(course, distance, going)

    if not par_time:
        # Fall back to published pars
        par_spf = PAR_SECS_PER_FURLONG.get(going, 12.0)
        jump_mult = JUMP_MULTIPLIER.get(race_type, 1.0)
        par_time = par_spf * jump_mult * furlongs

    # ── Step 2: Get horse's personal best time ────────────────────────────────
    winning_time_str = runner_data.get("winning_time")
    if not winning_time_str:
        # No time data for this horse — return neutral
        return default_score

    horse_time = parse_winning_time(winning_time_str)
    if not horse_time:
        return default_score

    # ── Step 3: Score ─────────────────────────────────────────────────────────
    # How much faster/slower than par?
    # ratio > 1 = horse was FASTER than par (good)
    # ratio < 1 = horse was SLOWER than par (bad)
    ratio = par_time / horse_time

    # Scale to 0-1:
    #   ratio 1.05+ = 0.80 (5% faster than par = strong figure)
    #   ratio 1.00  = 0.60 (exactly par = solid)
    #   ratio 0.97  = 0.50 (3% slower = neutral)
    #   ratio 0.93  = 0.35 (7% slower = weak)
    #   ratio 0.88- = 0.20 (12%+ slower = very slow)
    if ratio >= 1.05:
        score = 0.80
    elif ratio >= 1.02:
        score = 0.70
    elif ratio >= 1.00:
        score = 0.60
    elif ratio >= 0.97:
        score = 0.50
    elif ratio >= 0.94:
        score = 0.38
    elif ratio >= 0.90:
        score = 0.28
    else:
        score = 0.18

    # ── Step 4: Going adjustment ──────────────────────────────────────────────
    # If the horse set its best time in the SAME going as today → higher confidence
    best_time_going = runner_data.get("best_time_going", "")
    if best_time_going and going:
        if best_time_going.lower() == going.lower():
            score = min(score + 0.08, 0.95)   # Bonus: same going
        elif "good" in best_time_going.lower() and "good" in going.lower():
            score = min(score + 0.03, 0.95)   # Partial match

    return round(score, 4)
