# Racing Engine — Learning Loop
# Version: 1.0
# Date: 20 April 2026
# Purpose: Records recommendations vs outcomes.
#          Compares confidence scores to actual results.
#          Adjusts signal weightings over time to improve accuracy.
#          Feeds improved weightings back into odds_model.py via config/settings.py.

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from datetime import datetime, date, timedelta

RECOMMENDATIONS_PATH = os.path.join(os.path.dirname(__file__), "recommendations.json")
PERFORMANCE_PATH     = os.path.join(os.path.dirname(__file__), "performance.json")
WEIGHTS_PATH         = os.path.join(os.path.dirname(__file__), "learned_weights.json")


# ── Store helpers ────────────────────────────────────────────
def _load_json(path: str, default) -> dict:
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _save_json(path: str, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"[LearningLoop] Could not save {path}: {e}")


# ── Default weights (mirrors config/settings.py) ─────────────
DEFAULT_WEIGHTS = {
    "market_odds":  0.25,
    "horse_form":   0.20,
    "track_form":   0.15,
    "going":        0.10,
    "trainer_form": 0.10,
    "jockey_form":  0.10,
    "market_moves": 0.07,
    "jump_index":   0.03,
}


class LearningLoop:
    """
    Tracks every recommendation the engine makes and what actually happened.
    After enough data accumulates, adjusts signal weightings to improve accuracy.
    Weightings are written to learned_weights.json and picked up by odds_model.py.

    How it works:
    1. Before each race: record_recommendation() logs horse + confidence + signal scores
    2. After each race:  record_outcome() logs the actual winner
    3. adjust_weightings() runs daily — looks at which signals predicted winners best
       and nudges those weights up (and underperforming signals down)
    4. Minimum 20 races before any adjustment (avoid noise)
    """

    def __init__(self):
        self.recommendations = _load_json(RECOMMENDATIONS_PATH, {"records": []})
        self.performance     = _load_json(PERFORMANCE_PATH, {"signal_hits": {}, "signal_misses": {}})
        self.learned_weights = _load_json(WEIGHTS_PATH, DEFAULT_WEIGHTS.copy())

    # ── Record a pre-race recommendation ─────────────────────
    def record_recommendation(self, race_id: str, runner_id: str,
                               confidence_score: float, signals: dict):
        """
        Log a recommendation before the race.

        Args:
            race_id:          Unique race identifier e.g. "2026-04-20::14:00::Cheltenham"
            runner_id:        Horse name
            confidence_score: Overall model confidence (0–1)
            signals:          Dict of individual signal scores e.g. {"market_odds": 0.85, ...}
        """
        record = {
            "race_id":          race_id,
            "runner":           runner_id,
            "confidence":       confidence_score,
            "signals":          signals,
            "recommended_at":   datetime.now().isoformat(),
            "date":             date.today().isoformat(),
            "outcome":          None,   # Filled in by record_outcome()
            "won":              None,
        }
        self.recommendations["records"].append(record)
        _save_json(RECOMMENDATIONS_PATH, self.recommendations)
        print(f"[LearningLoop] Recommendation logged: {runner_id} ({race_id}) — confidence {confidence_score:.2f}")

    # ── Record the actual outcome ─────────────────────────────
    def record_outcome(self, race_id: str, winner_id: str):
        """
        Log the actual result after the race.
        Matches against all recommendations for this race.

        Args:
            race_id:   Race identifier (must match what was used in record_recommendation)
            winner_id: Winning horse name
        """
        updated = 0
        for rec in self.recommendations["records"]:
            if rec["race_id"] == race_id and rec["outcome"] is None:
                rec["outcome"]  = winner_id
                rec["won"]      = (rec["runner"].lower().strip() == winner_id.lower().strip())
                rec["settled_at"] = datetime.now().isoformat()
                updated += 1

        if updated > 0:
            _save_json(RECOMMENDATIONS_PATH, self.recommendations)
            print(f"[LearningLoop] Outcome recorded: {winner_id} won {race_id} "
                  f"— {updated} recommendation(s) updated")
        else:
            print(f"[LearningLoop] No open recommendations found for {race_id}")

    # ── Adjust signal weightings ──────────────────────────────
    def adjust_weightings(self) -> dict:
        """
        Recalculate signal weightings based on historical accuracy.
        Only runs if >= 20 settled races available — avoids noise.
        Nudges weights by up to 2% per signal per cycle.
        Returns updated weightings dict.
        """
        settled = [r for r in self.recommendations["records"] if r.get("won") is not None]

        if len(settled) < 20:
            print(f"[LearningLoop] Only {len(settled)} settled races — need 20 to adjust. Skipping.")
            return self.learned_weights

        signal_names = list(DEFAULT_WEIGHTS.keys())
        signal_correct_sum   = {s: 0.0 for s in signal_names}
        signal_incorrect_sum = {s: 0.0 for s in signal_names}

        for rec in settled:
            signals = rec.get("signals", {})
            won     = rec.get("won", False)
            for sig in signal_names:
                score = signals.get(sig, 0.5)
                if won:
                    signal_correct_sum[sig]   += score
                else:
                    signal_incorrect_sum[sig] += score

        # For each signal: if it scored higher on winners than losers, it's predictive
        winners_count = sum(1 for r in settled if r.get("won"))
        losers_count  = len(settled) - winners_count

        if winners_count == 0 or losers_count == 0:
            print("[LearningLoop] Insufficient win/loss spread — skipping adjustment")
            return self.learned_weights

        new_weights = dict(self.learned_weights)
        NUDGE = 0.01   # Max 1% nudge per signal per cycle
        MIN_W = 0.01   # No signal below 1%
        MAX_W = 0.40   # No signal above 40%

        for sig in signal_names:
            avg_when_winning = signal_correct_sum[sig] / winners_count
            avg_when_losing  = signal_incorrect_sum[sig] / losers_count
            predictive_gap   = avg_when_winning - avg_when_losing

            # Positive gap = signal was higher when horse won = useful signal
            if predictive_gap > 0.05:
                new_weights[sig] = min(new_weights[sig] + NUDGE, MAX_W)
            elif predictive_gap < -0.05:
                new_weights[sig] = max(new_weights[sig] - NUDGE, MIN_W)
            # Within ±0.05 gap: no change this cycle

        # Renormalise so all weights sum to 1.0
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}

        self.learned_weights = new_weights
        _save_json(WEIGHTS_PATH, new_weights)

        print(f"[LearningLoop] Weightings adjusted from {len(settled)} races:")
        for sig, w in new_weights.items():
            old_w = DEFAULT_WEIGHTS.get(sig, 0)
            change = w - old_w
            direction = "↑" if change > 0.001 else "↓" if change < -0.001 else "—"
            print(f"  {sig:20s}: {w:.3f} ({direction} {abs(change):.3f})")

        return new_weights

    # ── Performance Stats ─────────────────────────────────────
    def get_performance_stats(self) -> dict:
        """
        Returns summary performance stats for the dashboard Learning Engine tab.
        """
        settled = [r for r in self.recommendations["records"] if r.get("won") is not None]
        if not settled:
            return {
                "total_recommendations": 0,
                "settled_races":         0,
                "winners":               0,
                "hit_rate_pct":          0.0,
                "avg_confidence_winners":   0.0,
                "avg_confidence_losers":    0.0,
                "current_weights":       self.learned_weights,
                "note": "No settled data yet — stats build up automatically as races complete"
            }

        winners   = [r for r in settled if r.get("won")]
        losers    = [r for r in settled if not r.get("won")]
        hit_rate  = len(winners) / len(settled) * 100 if settled else 0

        avg_conf_win  = sum(r["confidence"] for r in winners) / len(winners) if winners else 0
        avg_conf_lose = sum(r["confidence"] for r in losers) / len(losers) if losers else 0

        # Rolling 7-day hit rate
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        recent = [r for r in settled if r.get("date", "") >= cutoff]
        recent_winners = [r for r in recent if r.get("won")]
        hit_rate_7d = len(recent_winners) / len(recent) * 100 if recent else 0

        return {
            "total_recommendations":    len(self.recommendations["records"]),
            "settled_races":            len(settled),
            "winners":                  len(winners),
            "hit_rate_pct":             round(hit_rate, 1),
            "hit_rate_7d_pct":          round(hit_rate_7d, 1),
            "avg_confidence_winners":   round(avg_conf_win, 3),
            "avg_confidence_losers":    round(avg_conf_lose, 3),
            "current_weights":          self.learned_weights,
            "note": f"Based on {len(settled)} settled races"
        }

    # ── Load learned weights for odds model ───────────────────
    @staticmethod
    def get_current_weights() -> dict:
        """
        Returns the current learned weights for use by OddsModel.
        Falls back to DEFAULT_WEIGHTS if no learned weights exist yet.
        """
        weights = _load_json(WEIGHTS_PATH, None)
        if weights and sum(weights.values()) > 0.99:
            return weights
        return DEFAULT_WEIGHTS.copy()
