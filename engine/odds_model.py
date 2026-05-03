# Racing Engine — Hybrid Odds Model
# Version: 2.6.0 — Activated 6 previously-dead signals from feed data
# Date: 1 May 2026
#
# DESIGN PRINCIPLES:
#   - Only signals with real data carry meaningful weight
#   - Signals with no data return NEUTRAL (0.50) not a boost
#   - Hard penalties applied for red flags before final score
#   - Market odds are a sanity check only, NOT the dominant signal
#   - Target output: genuinely differentiated 0.45–0.80 range
#
# ACTIVE SIGNALS (working with current data):
#   1. horse_form     (35%) — form string parsed with recency weighting
#   2. tf_stars       (20%) — Timeform stars (official quality rating)
#   3. market_odds    (15%) — implied probability (sanity check, not lead signal)
#   4. market_moves   (15%) — steam/drift from snapshot comparison
#   5. trainer_form   ( 8%) — tf_stars proxy until results store builds
#   6. jockey_form    ( 7%) — tf_stars proxy until results store builds
#
# PLACEHOLDER SIGNALS (return 0.50, weight redistributed when data arrives):
#   7. track_form     — course record (needs Racing API)
#   8. going          — going preference (needs going history per horse)
#   9. bsp_signal     — Betfair exchange (403 on free key)
#  10. race_pace      — speed ratings (needs historical times)
#
# PENALTY SYSTEM (applied after weighted score):
#   RED FLAGS that reduce confidence regardless of other signals:
#   - Poor form (0–2 recent wins in 5+ runs)        → -0.05
#   - Long layoff flag in form string                → -0.03
#   - Very poor TF rating (1 star)                  → -0.05
#   - Trainer/jockey very cold (stars = 1)          → -0.03
#   - No form at all (debut / missing)              → -0.04
#   - Race type mismatch (flat horse in chase etc)  → -0.06
#
# BONUS SYSTEM:
#   - Perfect recent form (4+ wins in last 5)       → +0.04
#   - Steam signal with good form                   → +0.03
#   - Top TF stars (5) + good form                  → +0.02
#
# FILTER LAYER (v2.5.1 — hard exclusions BEFORE scoring):
#   Applied before calculate_confidence() is called from the dashboard.
#   Returns (should_exclude: bool, reason: str).
#   1. LARGE FIELD: 12+ runners → exclude (too unpredictable)
#   2. HANDICAP UPLIFT: handicap races require 65% not 55% threshold
#      (handled in app.py tab1 — threshold raised, not an exclusion)
#   3. NO RECENT FORM: 0 runs AND no tf_stars → exclude (complete unknown)
#   4. DUAL SIGNAL: must have 2+ positive signals (form OR tf_stars≥4)
#      AND (market move OR market odds implied prob ≥ 0.40)

import json
import os

from config.settings import WEIGHTS

# v2.5.53 — minimum decimal price for any selection. Raised from 1.67 (4/6)
# to 2.0 (evens) after sub-evens singles consistently underperformed in
# backtests. Any horse with decimal SP < MIN_DECIMAL_ODDS is hard-excluded.
MIN_DECIMAL_ODDS = 2.0
from engine.form_parser import parse_form
from engine.going_matcher import score_going_preference, score_going_from_form_string
from engine.form_scorer import score_trainer_form, score_jockey_form
from engine.race_times_stride import score_race_pace, RaceTimesStore

# v2.5.55 — course specialist + distance affinity. Optional fetch via
# Sporting Life horse pages; failures return neutral (0.50, 0.50) and
# never block the main pipeline.
try:
    from engine.course_distance import (
        get_course_distance_signals as _get_cd_signals,
        get_course_distance_detail as _get_cd_detail,
    )
    _CD_AVAILABLE = True
except Exception:
    _CD_AVAILABLE = False
    def _get_cd_signals(horse_name, course, dist_f):
        return (0.50, 0.50)
    def _get_cd_detail(horse_name, course, dist_f):
        return {"course_wins": 0, "course_runs": 0, "dist_wins": 0, "dist_runs": 0}

# ── Default scoring weights (v2.5.55) ─────────────────────────
# These are mapped from learning/learned_weights.json at runtime, with
# fallback to these defaults if the file is missing or malformed.
# s_tf shares horse_form's quality-signal slot at 0.15 (a reasonable
# default — tf_stars is a Timeform quality rating, conceptually similar).
# v2.5.55 adds s_course (0.08) and s_distance (0.05) — course specialist
# and distance affinity from horse-level form pages. Other live signals
# rebalanced proportionally so the active set sums to 1.0.
# v2.6.0 — re-balanced weights with 6 previously-dead signals now fed from
# Sporting Life's `previous_results` field (no extra HTTP).
_DEFAULT_SCORING_WEIGHTS = {
    "s_form":     0.22,   # horse_form (reduced)
    "s_tf":       0.05,   # tf_stars (kept small — strong signal but narrow)
    "s_odds":     0.18,   # market_odds (reduced)
    "s_moves":    0.14,   # market_moves
    "s_trainer":  0.10,   # trainer_form
    "s_jockey":   0.07,   # jockey_form
    "s_going":    0.08,   # going_preference (activated v2.6.0)
    "s_course":   0.07,   # course_form (re-enabled v2.6.0)
    "s_distance": 0.05,   # distance_form (re-enabled v2.6.0)
    "s_or_gap":   0.05,   # official_rating_gap (new v2.6.0)
    "s_class":    0.03,   # class_consistency (new v2.6.0)
    "s_fresh":    0.01,   # freshness (new v2.6.0)
}
# Note: total ≈ 1.05 — _load_scoring_weights() renormalises to 1.0 anyway.

_LEARNED_WEIGHTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "learning", "learned_weights.json",
)


def _load_scoring_weights() -> dict:
    """
    Load active scoring weights from learning/learned_weights.json and map
    them onto the six signal slots used by calculate_confidence().
    Always returns a dict whose values sum to 1.0.
    """
    try:
        with open(_LEARNED_WEIGHTS_PATH, "r") as f:
            learned = json.load(f) or {}
        mapped = {
            "s_form":     float(learned.get("horse_form",     _DEFAULT_SCORING_WEIGHTS["s_form"])),
            "s_tf":       _DEFAULT_SCORING_WEIGHTS["s_tf"],   # tf_stars: quality signal, fixed default
            "s_odds":     float(learned.get("market_odds",    _DEFAULT_SCORING_WEIGHTS["s_odds"])),
            "s_moves":    float(learned.get("market_moves",   _DEFAULT_SCORING_WEIGHTS["s_moves"])),
            "s_trainer":  float(learned.get("trainer_form",   _DEFAULT_SCORING_WEIGHTS["s_trainer"])),
            "s_jockey":   float(learned.get("jockey_form",    _DEFAULT_SCORING_WEIGHTS["s_jockey"])),
            "s_going":    float(learned.get("going_preference", _DEFAULT_SCORING_WEIGHTS["s_going"])),
            "s_course":   float(learned.get("course_form",    _DEFAULT_SCORING_WEIGHTS["s_course"])),
            "s_distance": float(learned.get("distance_form",  _DEFAULT_SCORING_WEIGHTS["s_distance"])),
            "s_or_gap":   float(learned.get("official_rating_gap", _DEFAULT_SCORING_WEIGHTS["s_or_gap"])),
            "s_class":    float(learned.get("class_consistency",  _DEFAULT_SCORING_WEIGHTS["s_class"])),
            "s_fresh":    float(learned.get("freshness",          _DEFAULT_SCORING_WEIGHTS["s_fresh"])),
        }
    except Exception:
        mapped = dict(_DEFAULT_SCORING_WEIGHTS)

    # Renormalise so the six active weights always sum to 1.0
    total = sum(mapped.values())
    if total <= 0:
        return dict(_DEFAULT_SCORING_WEIGHTS)
    return {k: v / total for k, v in mapped.items()}

TOP_TRAVELLERS = [
    "henderson", "mullins", "o'brien", "elliott", "nicholls", "o'neill",
    "stoute", "gosden", "appleby"
]

# ── Race-type confidence multipliers (v2.5.35, backtest-driven) ──────────
# Derived from 7-day backtest across 217 races:
#   NHF/Bumper 75.0% strike, 91.5% ROI  → +8%
#   Hurdle     55.8% strike, 40.2% ROI  → +5%
#   Flat       41.6% strike,  8.1% ROI  → baseline
#   Chase      37.5% strike,  4.1% ROI  → -3%
# Keys are normalised to lower-case. Unknown types default to 1.0 (no change).
RACE_TYPE_MULTIPLIERS = {
    "nhf":    1.08,
    "bumper": 1.08,
    "hurdle": 1.05,
    "flat":   1.00,
    "chase":  0.97,
}


_times_store = None
def _get_times_store():
    global _times_store
    if _times_store is None:
        try:
            _times_store = RaceTimesStore()
        except Exception:
            pass
    return _times_store


class OddsModel:
    """
    Rebuilt hybrid scoring model v2.0.
    Confidence score (0–1): higher = stronger selection.
    Only active signals carry real weight. Neutrals don't inflate scores.
    """

    def __init__(self):
        try:
            from learning.loop import LearningLoop
            self.weights = LearningLoop.get_current_weights()
        except Exception:
            self.weights = WEIGHTS
        self._bsp_client = None
        self._bsp_cache: dict = {}

    # ── Signal 1: Horse Form (35%) ────────────────────────────
    def _score_horse_form(self, form_string: str, last_ran_days: int = None) -> float:
        """
        Form string parsed with recency weighting.
        This is the primary differentiator — most horses will vary 0.10–1.0.
        """
        result = parse_form(form_string, last_ran_days)
        return result["score"]

    def _get_form_detail(self, form_string: str) -> dict:
        """Return full form detail for penalty/bonus system."""
        return parse_form(form_string)

    # ── Signal 2: Timeform Stars (20%) ───────────────────────
    def _score_tf_stars(self, tf_stars) -> float:
        """
        Timeform stars: official quality rating from racing's most
        trusted form analyst. Directly usable, no history needed.
        5★ = Timeform tip for the race (one per race). Treated as a
        corroborating signal only — cannot push a horse over 0.60 threshold
        without supporting form/market evidence. Recalibrated downward.
        5★ = tip (0.72), 4★ = good (0.60), 3★ = average (0.50),
        2★ = below average (0.35), 1★ = poor (0.20), None = neutral (0.45)
        """
        try:
            stars = int(str(tf_stars).strip())
            mapping = {5: 0.72, 4: 0.60, 3: 0.50, 2: 0.35, 1: 0.20}
            return mapping.get(min(max(stars, 1), 5), 0.45)
        except Exception:
            return 0.45  # Slightly below neutral — unknown quality

    # ── Signal 3: Market Odds (15%) ───────────────────────────
    def _score_market_odds(self, odds_str) -> float:
        """
        Implied probability — sanity check signal only.
        Deliberately down-weighted vs v1 to stop short prices dominating.
        Capped at 0.80 so 1/10 shots don't score 0.91 and pull everything up.
        """
        try:
            s = str(odds_str).strip()
            if "/" in s:
                n, d = s.split("/")
                implied = float(d) / (float(n) + float(d))
            elif s.replace(".", "").isdigit():
                implied = 1.0 / float(s)
            else:
                implied = 0.35
        except Exception:
            implied = 0.35
        return round(min(implied, 0.80), 4)

    # ── Signal 4: Market Moves (15%) ──────────────────────────
    def _score_market_moves(self, signal: str, bet_movements: list = None) -> float:
        """
        Steam/drift signal. Genuinely informative — money talks.
        Steam = smart money arriving. Drift = market going cold.
        Neutral (no snapshot yet) = 0.50. Does NOT inflate scores artificially.
        """
        s = str(signal).lower()
        if "steam" in s:   base = 0.82
        elif "move" in s:  base = 0.68
        elif "drift" in s: base = 0.22
        else:              base = 0.50  # Stable / no snapshot yet

        # Magnitude bonus from actual bet_movements data
        if bet_movements and len(bet_movements) >= 2:
            try:
                first = _to_decimal(bet_movements[0].get("odds")) if isinstance(bet_movements[0], dict) else 0
                last  = _to_decimal(bet_movements[-1].get("odds")) if isinstance(bet_movements[-1], dict) else 0
                if first > 0 and last > 0:
                    move_pct = (first - last) / first
                    if move_pct > 0.20:   base = min(base + 0.08, 1.0)
                    elif move_pct < -0.20: base = max(base - 0.08, 0.0)
            except Exception:
                pass
        return round(base, 4)

    # ── Signal 5: Trainer Form (8%) ───────────────────────────
    def _score_trainer_form(self, trainer_name: str, tf_stars=None) -> float:
        """
        Rolling win rate from results store (building up).
        Falls back to tf_stars proxy — reasonable stand-in until data exists.
        """
        result = score_trainer_form(trainer_name)
        if result.get("note") in ("unknown", "insufficient_data"):
            return self._tf_stars_to_trainer_score(tf_stars)
        return result["score"]

    # ── Signal 6: Jockey Form (7%) ────────────────────────────
    def _score_jockey_form(self, jockey_name: str, tf_stars=None) -> float:
        """Rolling win rate. Falls back to tf_stars proxy."""
        result = score_jockey_form(jockey_name)
        if result.get("note") in ("unknown", "insufficient_data"):
            return self._tf_stars_to_trainer_score(tf_stars)
        return result["score"]

    def _tf_stars_to_trainer_score(self, tf_stars) -> float:
        """
        Previously used tf_stars as a proxy for trainer/jockey — removed.
        tf_stars already carries 20% weight; using it again in trainer/jockey
        (8% + 7%) caused one tip signal to dominate 35% of the score.
        Return neutral 0.50 until real results data builds up.
        """
        return 0.50

    # ── Signals 7+8: Course Form (8%) + Distance Form (5%) ────
    def _score_course_distance(self, runner_data: dict):
        """
        Fetch course-specialist and distance-affinity signals from the
        horse's form page. Wrapped in try/except — any failure returns
        the neutral (0.50, 0.50) so the main pipeline never blocks.
        """
        try:
            horse = str(runner_data.get("horse", "") or "").strip()
            course = str(runner_data.get("course", "") or "").strip()
            try:
                dist_f = float(runner_data.get("race_dist_f") or 0.0)
            except Exception:
                dist_f = 0.0
            if not horse:
                return (0.50, 0.50)
            return _get_cd_signals(horse, course, dist_f)
        except Exception:
            return (0.50, 0.50)

    # ── Placeholder Signals (all return 0.50) ─────────────────
    def _score_track_form(self, course: str, runner_data: dict) -> float:
        """Course record — needs Racing API. Returns 0.50 neutral."""
        track_wins = runner_data.get("track_wins")
        track_runs = runner_data.get("track_runs")
        if track_wins is not None and track_runs and track_runs > 0:
            return round(min(track_wins / track_runs, 1.0), 4)
        return 0.50

    # ── v2.6.0 helpers — going / course / distance / OR / class / freshness ─

    @staticmethod
    def _classify_going(going_str: str) -> str:
        """Map a going_shortcode or full going string to a coarse group."""
        if not going_str:
            return ""
        s = str(going_str).strip().upper()
        # Shortcodes first
        if s in ("F", "GF"):       return "FAST"
        if s == "G":               return "GOOD"
        if s in ("GS", "S", "H", "VS", "YS"):  return "SOFT"
        if s in ("ST", "SL", "SF"):return "AW"
        # Full string — case-insensitive substring checks
        sl = s.lower()
        if "firm" in sl:           return "FAST"
        if "heavy" in sl or "soft" in sl: return "SOFT"
        if "standard" in sl or "slow" in sl or "polytrack" in sl or "tapeta" in sl or "all weather" in sl or "all-weather" in sl:
            return "AW"
        if "good" in sl:
            # "Good to Firm" handled above by 'firm', "Good to Soft" by 'soft'
            return "GOOD"
        return ""

    def _score_going_preference(self, today_going, previous_results) -> float:
        """v2.6.0 — replaces neutral stub. Uses previous_results from feed."""
        if not previous_results:
            return 0.50
        today_group = self._classify_going(today_going or "")
        if not today_group:
            return 0.50
        runs = []
        for r in previous_results:
            g = r.get("going_shortcode") or r.get("going") or ""
            if self._classify_going(g) == today_group:
                runs.append(r)
        if not runs:
            return 0.50
        wins = sum(1 for r in runs
                   if self._safe_int(r.get("position")) == 1)
        places = sum(1 for r in runs
                     if 1 <= (self._safe_int(r.get("position")) or 99) <= 3)
        n = len(runs)
        win_rate = wins / n
        place_rate = places / n
        if win_rate >= 0.40: return 0.85
        if win_rate >= 0.25: return 0.70
        if place_rate >= 0.20: return 0.60
        if place_rate > 0:    return 0.50
        return 0.30

    def _score_course_form(self, course, previous_results) -> float:
        """v2.6.0 — course strike rate from previous_results."""
        if not previous_results or not course:
            return 0.50
        target = str(course).lower().strip()
        runs = [r for r in previous_results
                if str(r.get("course_name", "")).lower().strip() == target]
        if not runs:
            return 0.50
        wins = sum(1 for r in runs if self._safe_int(r.get("position")) == 1)
        places = sum(1 for r in runs
                     if 1 <= (self._safe_int(r.get("position")) or 99) <= 3)
        n = len(runs)
        win_rate = wins / n
        place_rate = places / n
        if win_rate >= 0.33: return 0.85
        if win_rate > 0:     return 0.70
        if place_rate >= 0.33: return 0.60
        if place_rate > 0:   return 0.50
        return 0.30

    @staticmethod
    def _parse_furlongs(dist_str) -> float:
        """Parse '1m 2f', '6f', '5f 3y' into furlongs (float)."""
        if not dist_str:
            return 0.0
        s = str(dist_str).lower().strip()
        try:
            f = 0.0
            import re as _re
            m = _re.search(r'(\d+)\s*m', s)
            if m: f += int(m.group(1)) * 8
            fm = _re.search(r'(\d+)\s*f', s)
            if fm: f += int(fm.group(1))
            ym = _re.search(r'(\d+)\s*y', s)
            if ym: f += int(ym.group(1)) / 220.0  # 220 yards per furlong
            return f
        except Exception:
            return 0.0

    def _score_distance_form(self, today_dist_f, previous_results) -> float:
        """v2.6.0 — distance affinity from previous_results within 0.5f tol."""
        if not previous_results:
            return 0.50
        try:
            target = float(today_dist_f or 0.0)
        except Exception:
            target = 0.0
        if target <= 0:
            return 0.50
        runs = []
        for r in previous_results:
            d = self._parse_furlongs(r.get("distance", ""))
            if d > 0 and abs(d - target) <= 0.5:
                runs.append(r)
        if not runs:
            return 0.50
        wins = sum(1 for r in runs if self._safe_int(r.get("position")) == 1)
        places = sum(1 for r in runs
                     if 1 <= (self._safe_int(r.get("position")) or 99) <= 3)
        n = len(runs)
        win_rate = wins / n
        if win_rate >= 0.33: return 0.80
        if win_rate > 0:     return 0.65
        if places > 0:       return 0.55
        return 0.35

    def _score_official_rating_gap(self, runner_rating, all_ratings_in_race, is_handicap) -> float:
        """v2.6.0 — OR gap (handicaps only).
        v2.6.2 — Sporting Life's `rating123` field sometimes returns Timeform
        star ratings (1-5) instead of BHA Official Ratings (typically 50-115
        flat, 80-165 jumps). Filter out values <= 5 — they're stars, not OR,
        and the gap calculation is meaningless on a 1-5 scale.
        """
        if not is_handicap:
            return 0.50
        try:
            rr = int(runner_rating) if runner_rating not in (None, "", "-") else None
        except Exception:
            rr = None
        if rr is None or rr <= 5:
            return 0.50
        ratings = []
        for v in (all_ratings_in_race or []):
            try:
                if v in (None, "", "-"):
                    continue
                iv = int(v)
                if iv > 5:
                    ratings.append(iv)
            except Exception:
                continue
        if len(ratings) < 2:
            return 0.50
        top = max(ratings)
        if top <= 5:
            return 0.50  # whole field on star scale — not real OR data
        if rr == top:           return 0.80
        gap = top - rr
        if gap <= 3:            return 0.65
        if gap <= 7:            return 0.55
        if gap <= 12:           return 0.45
        return 0.35

    def _score_class_consistency(self, previous_results, today_class) -> float:
        """v2.6.0 — class drop = good, class rise = bad. Last 4 runs."""
        if not previous_results:
            return 0.50
        recent = previous_results[:4]
        prev_classes = []
        for r in recent:
            c = str(r.get("race_class", "")).strip()
            if c.isdigit():
                prev_classes.append(int(c))
        if not prev_classes:
            return 0.50
        try:
            today_class_int = int(today_class) if str(today_class).isdigit() else 4
        except Exception:
            today_class_int = 4
        avg_prev = sum(prev_classes) / len(prev_classes)
        if avg_prev < today_class_int - 1:
            return 0.75   # dropping in class
        if avg_prev == today_class_int:
            return 0.55
        if avg_prev > today_class_int + 1:
            return 0.35   # stepping up
        return 0.50

    @staticmethod
    def _score_freshness(last_ran_days) -> float:
        """v2.6.0 — days since last run."""
        try:
            d = int(last_ran_days) if last_ran_days is not None else None
        except Exception:
            d = None
        if d is None:           return 0.50
        if d <= 14:             return 0.65
        if d <= 28:             return 0.60
        if d <= 60:             return 0.50
        if d <= 120:            return 0.40
        return 0.35

    @staticmethod
    def _safe_int(v):
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    # ── Snapshot-based market move (v2.6.0) ─────────────────────
    def _score_market_moves_from_snapshot(self, horse_name, course, race_time,
                                          today_str, current_dec) -> float:
        """v2.6.0 — compare today's price vs the 15:30 SHOW snapshot.
        Cached load of the JSON file. Returns 0.50 if no baseline."""
        try:
            snap = OddsModel._load_show_snapshot()
        except Exception:
            return 0.50
        if not snap or not horse_name or current_dec <= 0:
            return 0.50
        # v2.6.3 — snapshot stores keys keyed by the target racing date,
        # which may be today or tomorrow (15:30 capture is for tomorrow).
        snap_date_str = OddsModel._SHOW_SNAPSHOT_CACHE.get("snap_date") or today_str
        key = f"{snap_date_str}::{race_time}::{course}::{str(horse_name).lower().strip()}"
        entry = snap.get(key)
        if not entry:
            return 0.50
        try:
            show_dec = float(entry.get("decimal", 0.0))
        except Exception:
            show_dec = 0.0
        if show_dec <= 0:
            return 0.50
        ratio = current_dec / show_dec
        if ratio < 0.85:        return 0.75   # steamed 15%+
        if ratio > 1.15:        return 0.30   # drifted 15%+
        return 0.50

    _SHOW_SNAPSHOT_CACHE = {"loaded": False, "data": {}}
    _SHOW_SNAPSHOT_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "learning", "show_price_snapshot.json",
    )

    @classmethod
    def _load_show_snapshot(cls) -> dict:
        if cls._SHOW_SNAPSHOT_CACHE["loaded"]:
            return cls._SHOW_SNAPSHOT_CACHE["data"]
        try:
            with open(cls._SHOW_SNAPSHOT_PATH, "r") as f:
                raw = json.load(f) or {}
            cls._SHOW_SNAPSHOT_CACHE["data"] = raw.get("horses", {}) or {}
            # v2.6.3 — snapshot is captured at 15:30 BST for tomorrow's racing,
            # so a snap_date of today OR tomorrow is correct. Only warn if the
            # snapshot is from before today (something broke).
            from datetime import date as _date, timedelta
            snap_date_str = str(raw.get("date", ""))
            cls._SHOW_SNAPSHOT_CACHE["snap_date"] = snap_date_str
            today = _date.today()
            if snap_date_str:
                try:
                    snap_date = _date.fromisoformat(snap_date_str)
                    if snap_date < today:
                        print(f"[OddsModel] WARN show_price_snapshot.json is STALE "
                              f"(date={snap_date_str}, today={today.isoformat()}) — market_moves neutral")
                except ValueError:
                    pass
        except Exception:
            cls._SHOW_SNAPSHOT_CACHE["data"] = {}
        cls._SHOW_SNAPSHOT_CACHE["loaded"] = True
        return cls._SHOW_SNAPSHOT_CACHE["data"]

    def _score_going(self, today_going: str, runner_data: dict) -> float:
        """v2.6.0 — now reads previous_results from feed (was 0.50 stub)."""
        prev = runner_data.get("previous_results", []) or []
        if prev:
            return self._score_going_preference(today_going, prev)
        # Legacy fallback
        going_history = runner_data.get("going_history", [])
        if going_history:
            return score_going_preference(today_going, going_history)["score"]
        return 0.50

    def _score_bsp(self, runner_data: dict) -> float:
        """Betfair BSP — 403 on free key. Returns 0.50 neutral."""
        bsp_result = runner_data.get("bsp_result")
        if bsp_result and isinstance(bsp_result, dict):
            return bsp_result.get("bsp_score", 0.50)
        return 0.50

    def _score_race_pace(self, runner_data: dict) -> float:
        """Speed ratings — needs historical times. Returns 0.50 neutral."""
        try:
            ts = _get_times_store()
            return score_race_pace(runner_data, times_store=ts)
        except Exception:
            return 0.50

    # ── Penalty / Bonus System ────────────────────────────────
    def _calculate_adjustments(self, runner_data: dict, form_detail: dict) -> float:
        """
        Apply hard adjustments AFTER weighted score.
        Catches red flags that pure weighted scoring misses.
        Returns a delta (positive = bonus, negative = penalty).
        """
        delta = 0.0
        signal = str(runner_data.get("signal", "")).lower()
        tf_stars = runner_data.get("tf_stars")

        # ── RED FLAGS ─────────────────────────────────────────
        runs  = form_detail.get("runs", 0)
        wins  = form_detail.get("wins", 0)
        places= form_detail.get("places", 0)
        form_score = form_detail.get("score", 0.50)

        # No form at all — unknown quantity
        if runs == 0:
            delta -= 0.04

        # Poor recent record — in 5+ runs, winning less than 20%
        if runs >= 5 and wins == 0:
            delta -= 0.06   # Consistent loser
        elif runs >= 4 and wins == 0 and places <= 1:
            delta -= 0.04   # Poor place record too

        # Lay-off flag — returning from a long absence
        if form_detail.get("lay_off_flag", False):
            delta -= 0.03

        # Very poor Timeform rating
        try:
            stars = int(str(tf_stars).strip())
            if stars == 1:
                delta -= 0.05
            elif stars == 2:
                delta -= 0.02
        except Exception:
            pass

        # Market drifting — negative signal
        if "drift" in signal:
            delta -= 0.04

        # Race type: if it's a chase/hurdle, form on flat is irrelevant
        # (we don't have race_type per runner yet — parked for next build)

        # ── OUTLIER CROSS-CHECK ───────────────────────────────
        # tf_stars=5 (Timeform tip) but contradicted by other signals:
        # big price + poor form + no market move = likely false positive
        try:
            stars = int(str(tf_stars).strip())
            if stars == 5:
                odds_dec = 0.0
                try:
                    _o = str(runner_data.get("current_odds") or runner_data.get("odds","N/A"))
                    if "/" in _o:
                        _n,_d = _o.split("/"); odds_dec = float(_n)/float(_d)+1
                    else:
                        odds_dec = float(_o)
                except Exception:
                    pass
                _contradictions = 0
                if odds_dec >= 6.0:                          _contradictions += 1  # big price
                if runs >= 3 and wins == 0:                  _contradictions += 1  # winless
                if form_score < 0.45:                        _contradictions += 1  # poor form score
                if "drift" in signal:                        _contradictions += 1  # market going cold
                if "stable" in signal and odds_dec >= 8.0:   _contradictions += 1  # big price, no move
                if _contradictions >= 2:
                    delta -= (0.04 * _contradictions)  # -0.08 for 2, -0.12 for 3 etc
        except Exception:
            pass

        # ── BONUSES ───────────────────────────────────────────
        # Perfect or near-perfect recent form
        if runs >= 4 and wins >= 3:
            delta += 0.04
        elif runs >= 3 and wins >= 2:
            delta += 0.02

        # Steam signal on top of good form
        if "steam" in signal and form_score >= 0.60:
            delta += 0.03
        elif "move" in signal and form_score >= 0.55:
            delta += 0.02

        # Top Timeform rating + good form
        try:
            stars = int(str(tf_stars).strip())
            if stars == 5 and form_score >= 0.60:
                delta += 0.02
        except Exception:
            pass

        return round(delta, 4)

    # ── Filter Layer (v2.5.1) ─────────────────────────────────
    def should_exclude(self, runner_data: dict, race_name: str = None) -> tuple:
        """
        Hard exclusion check — called BEFORE scoring.
        Returns (exclude: bool, reason: str).
        Horses that pass all checks proceed to calculate_confidence().
        """
        form_str   = str(runner_data.get("form", "-"))
        tf_stars   = runner_data.get("tf_stars")
        signal     = str(runner_data.get("signal", "Stable")).lower()
        field_size = int(runner_data.get("field_size", 0) or 0)
        form_det   = self._get_form_detail(form_str)
        runs       = form_det.get("runs", 0)

        # ── Filter 0: Group/Listed race exclusion (v2.5.52) ──
        # Group 1/2/3, Grade 1/2/3, and Listed races are too competitive —
        # compressed class, multiple well-backed rivals, odds-on favs regularly
        # beaten. 24 Apr Sandown wiped out the day; exclude these entirely.
        if race_name is None:
            race_name = runner_data.get("race_name", "")
        race_name_lower = (race_name or "").lower()
        if race_name_lower:
            import re as _re
            GROUP_LISTED_PATTERNS = [
                r'\(gr(?:oup|ade)?\s*[123]\)',
                r'\(gr\s*[123]\)',
                r'\(grade\s*[123]\)',
                r'\(listed\)',
                r'\(listed race\)',
                r'grade\s+[123]',
                r'group\s+[123]',
                r'\bgr\.?\s*[123]\b',
                r'\bg[123]\b',
            ]
            for pattern in GROUP_LISTED_PATTERNS:
                if _re.search(pattern, race_name_lower, _re.IGNORECASE):
                    return (True, "group_listed_race")

        # ── Filter 1: Large field ─────────────────────────────
        # 16+ runners = highly unpredictable, exclude entirely
        # (12 was too tight — manageable 12-15 runner fields were being excluded)
        if field_size >= 16:
            return (True, f"Large field ({field_size} runners)")

        # ── Filter 2: Complete unknown ────────────────────────
        # No form at all AND no TF rating = zero signal quality
        try:
            stars = int(str(tf_stars).strip())
        except Exception:
            stars = 0
        if runs == 0 and stars == 0:
            return (True, "No form and no TF rating — insufficient data")

        # ── Filter 3: Weak-on-all-fronts exclusion (v2.5.44) ──
        # v2.5.44: relaxed from 2-of-4 dual-signal rule. With 4 dead signals
        # zero-weighted, the prior gate excluded almost every horse. Now we
        # only exclude horses that are weak on EVERY live signal:
        #   - form_score < 0.45 (weak/no form)        AND
        #   - tf_stars None or ≤ 1 (no quality rating) AND
        #   - no steam/move signal (market not warming)
        # Horses with any one decent signal pass through to scoring.
        form_score = form_det.get("score", 0.50)
        weak_form    = form_score < 0.45
        weak_tf      = (stars is None) or (stars <= 1)
        no_steam     = ("steam" not in signal) and ("move" not in signal)

        if weak_form and weak_tf and no_steam:
            return (True, "Weak on all live signals (form < 0.45, no TF rating, no market move)")

        return (False, "")

    def get_handicap_threshold(self, runner_data: dict, base_threshold: float) -> float:
        """
        Handicap uplift: raise the required threshold for handicap races.
        Handicaps have larger, more competitive fields — harder to predict.
        Flat conditions races: base_threshold (default 50% — v2.5.45)
        Handicaps: base_threshold + 0.10 (default 60% — v2.5.45)
        """
        if runner_data.get("is_handicap", False):
            return round(base_threshold + 0.10, 2)
        return base_threshold

    def _trainer_travel_signal(self, trainer_name: str) -> float:
        """Returns 0.05 uplift if a top-travelling trainer name is detected."""
        try:
            if not trainer_name:
                return 0.0
            tn = str(trainer_name).lower()
            for t in TOP_TRAVELLERS:
                if t in tn:
                    return 0.05
            return 0.0
        except Exception:
            return 0.0

    # ── Main Confidence Calculator ────────────────────────────
    def calculate_confidence(self, runner_data: dict) -> float:
        """
        Returns a confidence score (0–1) using active signals only.
        Weights: form(35) + tf_stars(20) + odds(15) + moves(15) + trainer(8) + jockey(7)
        Penalties/bonuses applied after weighted score.
        """
        form_str    = runner_data.get("form", "-")
        tf_stars    = runner_data.get("tf_stars")
        signal      = runner_data.get("signal", "Stable")
        bet_moves   = runner_data.get("bet_movements", [])
        trainer     = runner_data.get("trainer", "")
        jockey      = runner_data.get("jockey", "")
        # Use current_odds (live market price) if available; fall back to best bk odds
        _raw_odds  = runner_data.get("current_odds") or runner_data.get("odds", "N/A")
        odds_str   = str(_raw_odds) if _raw_odds and str(_raw_odds) not in ("None","N/A","") else runner_data.get("odds", "N/A")
        last_ran   = runner_data.get("last_ran_days")

        s_form    = self._score_horse_form(form_str, last_ran)
        s_tf      = self._score_tf_stars(tf_stars)
        s_odds    = self._score_market_odds(odds_str)
        s_moves   = self._score_market_moves(signal, bet_moves)
        s_trainer = self._score_trainer_form(trainer, tf_stars)
        s_jockey  = self._score_jockey_form(jockey, tf_stars)

        # v2.6.0 — feed-driven signals (no extra HTTP calls)
        prev_results = runner_data.get("previous_results", []) or []
        going_str    = runner_data.get("going", "") or ""
        course_name  = runner_data.get("course", "") or ""
        dist_f       = runner_data.get("race_dist_f", 0.0) or 0.0
        is_handicap  = bool(runner_data.get("is_handicap", False))
        race_class   = runner_data.get("race_class", "")
        rating123    = runner_data.get("rating123")
        all_ratings  = runner_data.get("all_ratings_in_race", []) or []
        last_ran_days = runner_data.get("last_ran_days")

        s_going    = self._score_going_preference(going_str, prev_results)
        s_course   = self._score_course_form(course_name, prev_results)
        s_distance = self._score_distance_form(dist_f, prev_results)
        s_or_gap   = self._score_official_rating_gap(rating123, all_ratings, is_handicap)
        s_class    = self._score_class_consistency(prev_results, race_class)
        s_fresh    = self._score_freshness(last_ran_days)

        # Snapshot-based market moves layered on top of bet_movements signal:
        # take whichever is farther from neutral (so a clear snapshot move
        # wins over a stale 'Stable' tag, but a live Steam still counts).
        try:
            today_str = runner_data.get("today_str", "")
            race_time = runner_data.get("time", "")
            current_dec = _to_decimal(runner_data.get("current_odds") or odds_str)
            s_moves_snap = self._score_market_moves_from_snapshot(
                runner_data.get("horse", ""), course_name, race_time,
                today_str, current_dec,
            )
            if abs(s_moves_snap - 0.50) > abs(s_moves - 0.50):
                s_moves = s_moves_snap
        except Exception:
            pass

        # Active weights — loaded from learning/learned_weights.json at
        # runtime (with default fallback). Always renormalised to sum to 1.0.
        w = _load_scoring_weights()

        # v2.5.45: signals returning EXACTLY 0.50 (no-data neutral) drag the
        # weighted average toward the centre. Drop neutral signals and
        # renormalise weights across signals that have real data.
        # Note: _score_tf_stars(None) returns 0.45 (not 0.50), so missing
        # tf_stars stays in the active set as a slight negative.
        signal_scores = {
            "s_form":     s_form,
            "s_tf":       s_tf,
            "s_odds":     s_odds,
            "s_moves":    s_moves,
            "s_trainer":  s_trainer,
            "s_jockey":   s_jockey,
            "s_going":    s_going,
            "s_course":   s_course,
            "s_distance": s_distance,
            "s_or_gap":   s_or_gap,
            "s_class":    s_class,
            "s_fresh":    s_fresh,
        }
        active = {k: v for k, v in signal_scores.items() if abs(v - 0.50) > 0.01}
        if not active:
            raw = 0.50
        else:
            total_active_weight = sum(w[k] for k in active)
            raw = sum(v * w[k] / total_active_weight for k, v in active.items())

        # Apply penalty/bonus
        form_detail = self._get_form_detail(form_str)
        adjustment  = self._calculate_adjustments(runner_data, form_detail)
        travel      = self._trainer_travel_signal(trainer)
        scored      = raw + adjustment + travel

        # Race-type multiplier (fails silently if race_type missing)
        try:
            rt_key = str(runner_data.get("race_type", "") or "").strip().lower()
            mult   = RACE_TYPE_MULTIPLIERS.get(rt_key, 1.0)
        except Exception:
            mult = 1.0
        scored *= mult

        final = round(min(max(scored, 0.05), 1.0), 4)
        return final

    def get_signal_breakdown(self, runner_data=None, course=None, time=None) -> dict:
        """
        Returns individual signal scores for dashboard transparency.

        Accepts either:
          - get_signal_breakdown(runner_dict)                  — preferred
          - get_signal_breakdown(horse_name, course, time)     — lookup-style;
            looks up the runner in today's live selections feed.
        """
        if not isinstance(runner_data, dict):
            _horse_name = str(runner_data or "")
            runner_data = {}
            try:
                from dashboard.live_data import get_todays_selections as _gts
                _df = _gts()
                if _df is not None and len(_df) > 0:
                    for _, _r in _df.iterrows():
                        if str(_r.get("Horse", "")).lower().strip() != _horse_name.lower().strip():
                            continue
                        if course and str(_r.get("Course", "")).lower().strip() != str(course).lower().strip():
                            continue
                        if time and str(_r.get("Time", "")).strip() != str(time).strip():
                            continue
                        runner_data = {
                            "form":         str(_r.get("Form", "-")),
                            "tf_stars":     _r.get("TF Stars"),
                            "signal":       str(_r.get("Signal", "Stable")),
                            "bet_movements": [],
                            "trainer":      str(_r.get("Trainer", "")),
                            "jockey":       str(_r.get("Jockey", "")),
                            "odds":         str(_r.get("Odds", "N/A")),
                            "current_odds": str(_r.get("Current Odds", "")),
                            "is_handicap":  bool(_r.get("Is Handicap", False)),
                        }
                        break
            except Exception:
                runner_data = {}

        form_str  = runner_data.get("form", "-")
        tf_stars  = runner_data.get("tf_stars")
        signal    = runner_data.get("signal", "Stable")
        bet_moves = runner_data.get("bet_movements", [])
        trainer   = runner_data.get("trainer", "")
        jockey    = runner_data.get("jockey", "")
        form_det  = self._get_form_detail(form_str)

        # v2.6.0 — feed-driven signals
        prev_results = runner_data.get("previous_results", []) or []
        going_str    = runner_data.get("going", "") or ""
        course_name  = runner_data.get("course", "") or ""
        dist_f       = runner_data.get("race_dist_f", 0.0) or 0.0
        is_handicap  = bool(runner_data.get("is_handicap", False))
        race_class   = runner_data.get("race_class", "")
        rating123    = runner_data.get("rating123")
        all_ratings  = runner_data.get("all_ratings_in_race", []) or []
        last_ran_days = runner_data.get("last_ran_days")

        return {
            "horse_form":   round(self._score_horse_form(form_str), 3),
            "tf_stars":     round(self._score_tf_stars(tf_stars), 3),
            "market_odds":  round(self._score_market_odds(runner_data.get("odds", "N/A")), 3),
            "market_moves": round(self._score_market_moves(signal, bet_moves), 3),
            "trainer_form": round(self._score_trainer_form(trainer, tf_stars), 3),
            "jockey_form":  round(self._score_jockey_form(jockey, tf_stars), 3),
            "going":         round(self._score_going_preference(going_str, prev_results), 3),
            "course_form":   round(self._score_course_form(course_name, prev_results), 3),
            "distance_form": round(self._score_distance_form(dist_f, prev_results), 3),
            "or_gap":        round(self._score_official_rating_gap(rating123, all_ratings, is_handicap), 3),
            "class_consistency": round(self._score_class_consistency(prev_results, race_class), 3),
            "freshness":     round(self._score_freshness(last_ran_days), 3),
            "trainer_travel": round(self._trainer_travel_signal(trainer), 3),
            "adjustment":   round(self._calculate_adjustments(runner_data, form_det), 3),
            # Placeholder signals — shown as N/A until data available
            "track_form":   "N/A (needs Racing API)",
            "bsp_signal":   "N/A (BSP unavailable)",
            "race_pace":    "N/A (building history)",
        }

    def rank_runners(self, race_data: list) -> list:
        """Rank runners in a race by confidence score."""
        scored = []
        for runner in race_data:
            confidence = self.calculate_confidence(runner)
            breakdown  = self.get_signal_breakdown(runner)
            rc = dict(runner)
            rc["confidence"] = confidence
            rc["signal_breakdown"] = breakdown
            scored.append(rc)
        return sorted(scored, key=lambda x: x["confidence"], reverse=True)


# ── Utility ───────────────────────────────────────────────────
def _to_decimal(odds_str) -> float:
    try:
        s = str(odds_str).strip()
        if "/" in s:
            n, d = s.split("/")
            return (float(n) + float(d)) / float(d)
        return float(s)
    except Exception:
        return 0.0
