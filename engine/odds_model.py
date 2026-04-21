# Racing Engine — Hybrid Odds Model
# Version: 1.1  (added BSP + race pace signals)
# Date: 21 April 2026
# Purpose: Combines 10 signals into a confidence score (0–1) per runner.
#
# 10 SIGNALS:
#   1. market_odds    (22%) — implied probability from bookmaker odds
#   2. horse_form     (18%) — recent form string score (weighted recency)
#   3. track_form     (14%) — course-specific form
#   4. going          (10%) — going preference match
#   5. trainer_form   (9%)  — trainer's rolling 14/30-day win rate
#   6. jockey_form    (9%)  — jockey's rolling 14/30-day win rate
#   7. market_moves   (7%)  — steam / drift signal from bet movements
#   8. jump_index     (3%)  — jumping ability proxy
#   9. bsp_signal     (5%)  — Betfair BSP projection vs bookmaker price
#  10. race_pace      (3%)  — race time / speed rating vs course par

from config.settings import WEIGHTS
from engine.form_parser import parse_form
from engine.going_matcher import score_going_preference, score_going_from_form_string
from engine.form_scorer import score_trainer_form, score_jockey_form
from engine.race_times_stride import score_race_pace, RaceTimesStore

# Lazy-init global times store (shared across all model instances)
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
    Hybrid weighted scoring model.
    Takes all available data for a runner and returns a confidence score (0–1).
    Higher score = stronger selection.
    """

    def __init__(self):
        # Use learned weights if available, fall back to config defaults
        try:
            from learning.loop import LearningLoop
            self.weights = LearningLoop.get_current_weights()
        except Exception:
            self.weights = WEIGHTS

        # Betfair BSP client — initialised lazily when credentials available
        self._bsp_client = None
        self._bsp_cache: dict = {}   # market_id → bsp data, avoids repeated API calls

    # ── Signal 1: Market Odds ─────────────────────────────────
    def _score_market_odds(self, odds_str) -> float:
        """
        Converts fractional or decimal odds to implied probability.
        High probability (short odds) = high score.
        Caps at 0.95 to avoid certainty inflation on very short-priced horses.
        """
        try:
            odds_str = str(odds_str).strip()
            if "/" in odds_str:
                n, d = odds_str.split("/")
                implied = float(d) / (float(n) + float(d))
            elif odds_str.replace(".", "").isdigit():
                implied = 1.0 / float(odds_str)
            else:
                implied = 0.33
        except Exception:
            implied = 0.33
        return round(min(implied, 0.95), 4)

    # ── Signal 2: Horse Form ──────────────────────────────────
    def _score_horse_form(self, form_string: str, last_ran_days: int = None) -> float:
        """
        Parses form string into a weighted recency score.
        Uses form_parser.py.
        """
        result = parse_form(form_string, last_ran_days)
        return result["score"]

    # ── Signal 3: Track Form ──────────────────────────────────
    def _score_track_form(self, course: str, runner_data: dict) -> float:
        """
        Course-specific win rate.
        Pending full build — The Racing API will provide per-course history.
        For now: if The Racing API data is available in runner_data, use it.
        Otherwise return neutral (0.33).
        """
        # Future: query The Racing API for horse's record at this course
        # Slot is wired and ready — neutral until API is verified.
        track_wins = runner_data.get("track_wins", None)
        track_runs = runner_data.get("track_runs", None)
        if track_wins is not None and track_runs and track_runs > 0:
            return round(min(track_wins / track_runs, 1.0), 4)
        return 0.50   # Neutral

    # ── Signal 4: Going ───────────────────────────────────────
    def _score_going(self, today_going: str, runner_data: dict) -> float:
        """
        Matches runner's going preferences to today's conditions.
        Uses going_matcher.py.
        If detailed going_history is available (from The Racing API), use it.
        Otherwise falls back to neutral.
        """
        going_history = runner_data.get("going_history", [])
        if going_history:
            result = score_going_preference(today_going, going_history)
        else:
            result = score_going_from_form_string(today_going)
        return result["score"]

    # ── Signal 5: Trainer Form ────────────────────────────────
    def _score_trainer_form(self, trainer_name: str, tf_stars=None) -> float:
        """
        Rolling 14/30-day win rate for this trainer.
        Uses form_scorer.py with the local results store.
        Falls back to Timeform stars proxy while results store is building up.
        """
        result = score_trainer_form(trainer_name)
        if result.get("note") in ("unknown", "insufficient_data"):
            # Proxy: top trainers (Mullins, Henderson, O'Brien etc) get slight boost
            # via Timeform stars until real data exists
            return self._tf_stars_to_score(tf_stars, default=0.50)
        return result["score"]

    # ── Signal 6: Jockey Form ─────────────────────────────────
    def _score_jockey_form(self, jockey_name: str, tf_stars=None) -> float:
        """
        Rolling 14/30-day win rate for this jockey.
        Uses form_scorer.py with the local results store.
        Falls back to Timeform stars proxy while results store is building up.
        """
        result = score_jockey_form(jockey_name)
        if result.get("note") in ("unknown", "insufficient_data"):
            return self._tf_stars_to_score(tf_stars, default=0.50)
        return result["score"]

    def _tf_stars_to_score(self, tf_stars, default=0.50) -> float:
        """Convert Timeform stars (1-5) to a 0-1 score proxy."""
        try:
            stars = int(str(tf_stars).strip())
            # 5 stars = 0.80, 4 = 0.65, 3 = 0.50, 2 = 0.35, 1 = 0.25
            mapping = {5: 0.80, 4: 0.65, 3: 0.50, 2: 0.35, 1: 0.25}
            return mapping.get(min(max(stars, 1), 5), default)
        except Exception:
            return default

    # ── Signal 7: Market Moves ────────────────────────────────
    def _score_market_moves(self, signal: str, bet_movements: list = None) -> float:
        """
        Converts the market move signal into a score.
        Steam = positive signal. Drift = negative. Stable = neutral.
        Also analyses the magnitude of the move from bet_movements if available.
        """
        signal_str = str(signal).lower()

        if "steam" in signal_str:
            base = 0.75
        elif "move" in signal_str:
            base = 0.65
        elif "drift" in signal_str:
            base = 0.25
        else:
            base = 0.50   # Stable / unknown

        # Magnitude bonus from actual bet movement data
        if bet_movements and len(bet_movements) >= 2:
            try:
                first = _to_decimal(bet_movements[0].get("odds"))
                last = _to_decimal(bet_movements[-1].get("odds"))
                if first and last and first > 0:
                    move_pct = (first - last) / first  # Positive = price shortened
                    if move_pct > 0.20:
                        base = min(base + 0.10, 1.0)   # Big steam — boost
                    elif move_pct < -0.20:
                        base = max(base - 0.10, 0.0)   # Big drift — penalise
            except Exception:
                pass

        return round(base, 4)

    # ── Signal 8: Jump Index ──────────────────────────────────
    def _score_jump_index(self, runner_data: dict) -> float:
        """
        Jumping ability score for National Hunt races.
        Uses Timeform stars as a proxy (Racing API would improve this).
        """
        tf_stars = runner_data.get("tf_stars")
        try:
            stars = int(str(tf_stars).strip())
            return round(min(stars / 5.0, 1.0), 4)
        except Exception:
            pass
        return 0.50   # Neutral

    # ── Signal 9: Betfair BSP ─────────────────────────────────
    def _score_bsp(self, runner_data: dict) -> float:
        """
        Betfair BSP projection score.
        Compares Betfair's 'smart money' price to the bookmaker price.

        If BSP is SHORTER than bookie → market smarter → value signal → high score
        If BSP roughly matches → fair priced → neutral score
        If BSP is LONGER than bookie → market more cautious → low score

        Falls back to 0.50 (neutral) if no Betfair credentials or data unavailable.
        """
        # Use pre-computed bsp_score if already attached to runner_data
        # (set by the live_data fetcher which calls BetfairBSP separately)
        bsp_result = runner_data.get("bsp_result")
        if bsp_result and isinstance(bsp_result, dict):
            return bsp_result.get("bsp_score", 0.50)
        return 0.50   # Neutral until Betfair credentials wired in

    # ── Signal 10: Race Pace / Times ──────────────────────────
    def _score_race_pace(self, runner_data: dict) -> float:
        """
        Speed rating / race times signal.
        Compares the horse's recorded winning time against course/distance/going par.
        Faster than par = positive signal. Slower = negative.
        Falls back to neutral 0.50 if no time data available.
        """
        try:
            ts = _get_times_store()
            return score_race_pace(runner_data, times_store=ts)
        except Exception:
            return 0.50

    # ── Main Confidence Calculator ────────────────────────────
    def calculate_confidence(self, runner_data: dict) -> float:
        """
        Takes all available data for a runner and returns
        a confidence score between 0 and 1.

        runner_data keys used:
            odds          — fractional or decimal odds string
            form          — form string e.g. "080-141"
            last_ran_days — days since last run (optional)
            going         — today's going e.g. "Good to Firm"
            going_history — list of {going, position} dicts (optional)
            trainer       — trainer name
            jockey        — jockey name
            signal        — "Steam" / "Drift" / "Stable" etc.
            bet_movements — list of movement dicts (optional)
            tf_stars      — Timeform stars (optional)
            track_wins    — wins at this course (optional)
            track_runs    — runs at this course (optional)
            bsp_result    — dict from BetfairBSP.score_bsp_signal() (optional)
            winning_time  — str, horse's recorded winning time e.g. "1m 12.3s" (optional)
            distance      — race distance e.g. "6f" or "1m 2f" (optional)
            race_type     — "Flat", "Hurdle", "Chase" (optional, default Flat)
            best_time_going— going when the best time was set (optional)
        """
        w = self.weights

        s1 = self._score_market_odds(runner_data.get("odds", "N/A"))
        s2 = self._score_horse_form(
            runner_data.get("form", "-"),
            runner_data.get("last_ran_days")
        )
        s3 = self._score_track_form(
            runner_data.get("course", ""),
            runner_data
        )
        s4 = self._score_going(
            runner_data.get("going", ""),
            runner_data
        )
        _tf = runner_data.get("tf_stars")
        s5 = self._score_trainer_form(runner_data.get("trainer", ""), tf_stars=_tf)
        s6 = self._score_jockey_form(runner_data.get("jockey", ""), tf_stars=_tf)
        s7 = self._score_market_moves(
            runner_data.get("signal", "Stable"),
            runner_data.get("bet_movements")
        )
        s8 = self._score_jump_index(runner_data)
        s9 = self._score_bsp(runner_data)
        s10 = self._score_race_pace(runner_data)

        # Updated weights: 22+18+14+10+9+9+7+3+5+3 = 100
        w_market_odds  = w.get("market_odds",  0.22)
        w_horse_form   = w.get("horse_form",   0.18)
        w_track_form   = w.get("track_form",   0.14)
        w_going        = w.get("going",        0.10)
        w_trainer_form = w.get("trainer_form", 0.09)
        w_jockey_form  = w.get("jockey_form",  0.09)
        w_market_moves = w.get("market_moves", 0.07)
        w_jump_index   = w.get("jump_index",   0.03)
        w_bsp          = w.get("bsp_signal",   0.05)
        w_race_pace    = w.get("race_pace",    0.03)

        raw_score = (
            s1  * w_market_odds  +
            s2  * w_horse_form   +
            s3  * w_track_form   +
            s4  * w_going        +
            s5  * w_trainer_form +
            s6  * w_jockey_form  +
            s7  * w_market_moves +
            s8  * w_jump_index   +
            s9  * w_bsp          +
            s10 * w_race_pace
        )

        # Cap at 0.97 — no selection should ever be "certain"
        confidence = round(min(raw_score, 0.97), 4)

        return confidence

    def get_signal_breakdown(self, runner_data: dict) -> dict:
        """
        Returns the individual signal scores for transparency / dashboard display.
        Useful for debugging and for the Signal Breakdown tab.
        """
        return {
            "market_odds":  round(self._score_market_odds(runner_data.get("odds", "N/A")), 3),
            "horse_form":   round(self._score_horse_form(runner_data.get("form", "-"), runner_data.get("last_ran_days")), 3),
            "track_form":   round(self._score_track_form(runner_data.get("course", ""), runner_data), 3),
            "going":        round(self._score_going(runner_data.get("going", ""), runner_data), 3),
            "trainer_form": round(self._score_trainer_form(runner_data.get("trainer", ""), runner_data.get("tf_stars")), 3),
            "jockey_form":  round(self._score_jockey_form(runner_data.get("jockey", ""), runner_data.get("tf_stars")), 3),
            "market_moves": round(self._score_market_moves(runner_data.get("signal", "Stable"), runner_data.get("bet_movements")), 3),
            "jump_index":   round(self._score_jump_index(runner_data), 3),
            "bsp_signal":   round(self._score_bsp(runner_data), 3),
            "race_pace":    round(self._score_race_pace(runner_data), 3),
        }

    def rank_runners(self, race_data: list) -> list:
        """
        Takes a list of runner dicts for a single race.
        Returns them sorted by confidence score, highest first.
        Adds 'confidence' and 'signal_breakdown' keys to each runner.
        """
        scored = []
        for runner in race_data:
            confidence = self.calculate_confidence(runner)
            breakdown = self.get_signal_breakdown(runner)
            runner_copy = dict(runner)
            runner_copy["confidence"] = confidence
            runner_copy["signal_breakdown"] = breakdown
            scored.append(runner_copy)

        return sorted(scored, key=lambda x: x["confidence"], reverse=True)


# ── Utility ───────────────────────────────────────────────────
def _to_decimal(odds_str) -> float:
    """Convert fractional or decimal odds to decimal format."""
    try:
        s = str(odds_str).strip()
        if "/" in s:
            n, d = s.split("/")
            return (float(n) + float(d)) / float(d)
        return float(s)
    except Exception:
        return 0.0
