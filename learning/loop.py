# Racing Engine — Learning Loop
# Version: 2.0 — 21 April 2026
#
# How it works:
#
# RECORDING (runs automatically each racing day):
#   1. auto_record_day()  — called by scheduler after morning brief
#      Pulls today's live runners, scores each with OddsModel,
#      saves every recommendation to recommendations.json
#
# SETTLEMENT (runs every 2 minutes):
#   2. auto_settle()  — called by scheduler after each race finishes
#      Checks Sporting Life for races marked WEIGHEDIN/RESULT,
#      fetches the winner (position=1), matches to open recommendations,
#      marks each as won=True/False
#
# LEARNING (runs at 21:00 BST each day):
#   3. adjust_weightings()  — runs after all races settled
#      For each signal, checks: was this signal higher on winners than losers?
#      If yes → nudge weight up. If no → nudge down.
#      Renormalises weights to sum to 1.0.
#      Writes to learned_weights.json — picked up by OddsModel next day.
#
# FEEDBACK TO MODEL:
#   4. OddsModel.__init__ calls LearningLoop.get_current_weights()
#      So every refresh of the dashboard uses the latest learned weights.

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from datetime import datetime, date, timedelta

RECOMMENDATIONS_PATH = os.path.join(os.path.dirname(__file__), "recommendations.json")
RESULTS_PATH         = os.path.join(os.path.dirname(__file__), "results_store.json")
WEIGHTS_PATH         = os.path.join(os.path.dirname(__file__), "learned_weights.json")
PERFORMANCE_PATH     = os.path.join(os.path.dirname(__file__), "performance.json")

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


# ── JSON helpers ──────────────────────────────────────────────

def _load(path, default):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return default

def _save(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"[LearningLoop] Save failed {path}: {e}")


# ── Learning Loop ─────────────────────────────────────────────

class LearningLoop:

    def __init__(self):
        self.recommendations = _load(RECOMMENDATIONS_PATH, {"records": []})
        self.results         = _load(RESULTS_PATH, {"results": []})
        self.learned_weights = _load(WEIGHTS_PATH, DEFAULT_WEIGHTS.copy())

    # ─────────────────────────────────────────────────────────
    # 1. AUTO-RECORD — runs after morning brief
    # ─────────────────────────────────────────────────────────

    def auto_record_day(self) -> int:
        """
        Pull today's live runners, score each with the odds model,
        and record a recommendation for every runner.
        Returns count of recommendations recorded.
        """
        today = date.today().isoformat()

        # Don't double-record the same day
        existing_today = [r for r in self.recommendations["records"]
                         if r.get("date") == today]
        if existing_today:
            print(f"[LearningLoop] Already recorded {len(existing_today)} recommendations for {today}")
            return len(existing_today)

        try:
            from dashboard.live_data import get_todays_meetings, get_race_runners
            from engine.odds_model import OddsModel
            model    = OddsModel()
            meetings = get_todays_meetings()
        except Exception as e:
            print(f"[LearningLoop] Could not load data/model: {e}")
            return 0

        count = 0
        for meeting in meetings:
            course = meeting.get("course", "")
            going  = meeting.get("going", "")

            for race in meeting.get("races", []):
                time_  = race.get("time", "")
                name_  = race.get("name", "")
                slug   = race.get("slug")
                stage  = race.get("stage", "")

                # Only record upcoming races
                if stage in ("WEIGHEDIN", "RESULT") or not slug:
                    continue

                race_id = f"{today}::{time_}::{course}"

                try:
                    runners = get_race_runners(slug)
                except Exception:
                    continue

                for runner in runners:
                    if runner.get("status") == "NON_RUNNER":
                        continue

                    horse  = runner.get("horse", "")
                    runner_data = {
                        "odds":    runner.get("odds", "N/A"),
                        "form":    runner.get("form", "-"),
                        "going":   going,
                        "trainer": runner.get("trainer", "-"),
                        "jockey":  runner.get("jockey", "-"),
                        "signal":  runner.get("signal", "Stable"),
                        "tf_stars": runner.get("tf_stars"),
                        "course":  course,
                        "bet_movements": runner.get("bet_movements", []),
                    }

                    confidence = model.calculate_confidence(runner_data)
                    signals    = model.get_signal_breakdown(runner_data)

                    record = {
                        "race_id":        race_id,
                        "race_name":      name_,
                        "runner":         horse,
                        "course":         course,
                        "time":           time_,
                        "date":           today,
                        "confidence":     confidence,
                        "signals":        signals,
                        "odds":           runner.get("odds", "N/A"),
                        "recommended_at": datetime.now().isoformat(),
                        "outcome":        None,
                        "won":            None,
                    }
                    self.recommendations["records"].append(record)
                    count += 1

        _save(RECOMMENDATIONS_PATH, self.recommendations)
        print(f"[LearningLoop] Recorded {count} recommendations for {today}")
        return count

    # ─────────────────────────────────────────────────────────
    # 2. AUTO-SETTLE — runs every 2 minutes
    # ─────────────────────────────────────────────────────────

    def auto_settle(self) -> int:
        """
        Check Sporting Life for races that have finished (stage=WEIGHEDIN/RESULT).
        Find the winner (finish_position=1), record against recommendations.
        Returns count of races settled this poll.
        """
        today   = date.today().isoformat()
        settled = 0

        try:
            from dashboard.live_data import get_todays_meetings, get_race_runners
            meetings = get_todays_meetings()
        except Exception as e:
            print(f"[LearningLoop] Settlement data unavailable: {e}")
            return 0

        for meeting in meetings:
            course = meeting.get("course", "")

            for race in meeting.get("races", []):
                stage  = race.get("stage", "")
                time_  = race.get("time", "")
                slug   = race.get("slug")

                if stage not in ("WEIGHEDIN", "RESULT") or not slug:
                    continue

                race_id = f"{today}::{time_}::{course}"

                # Skip if already settled
                already_settled = any(
                    r.get("race_id") == race_id and r.get("won") is not None
                    for r in self.recommendations["records"]
                )
                if already_settled:
                    continue

                try:
                    runners = get_race_runners(slug)
                except Exception:
                    continue

                # Find winner (position 1)
                winner = None
                for rn in runners:
                    pos = rn.get("finish_position")
                    try:
                        if int(pos) == 1:
                            winner = rn.get("horse", "")
                            break
                    except Exception:
                        pass

                if not winner:
                    continue

                # Store result
                result_record = {
                    "race_id":    race_id,
                    "course":     course,
                    "time":       time_,
                    "date":       today,
                    "winner":     winner,
                    "settled_at": datetime.now().isoformat(),
                }
                self.results["results"].append(result_record)
                _save(RESULTS_PATH, self.results)

                # Update trainer/jockey form stores
                winning_runner = next((r for r in runners if r.get("horse") == winner), {})
                self._update_form_stores(winning_runner, course, today)

                # Match to open recommendations
                for rec in self.recommendations["records"]:
                    if rec.get("race_id") == race_id and rec.get("outcome") is None:
                        rec["outcome"]    = winner
                        rec["won"]        = (rec["runner"].strip().lower() == winner.strip().lower())
                        rec["settled_at"] = datetime.now().isoformat()

                _save(RECOMMENDATIONS_PATH, self.recommendations)
                print(f"[LearningLoop] Settled: {winner} won {race_id}")
                settled += 1

        return settled

    def _update_form_stores(self, winning_runner: dict, course: str, today: str):
        """
        After settlement, record the win in results_store for
        trainer/jockey rolling win rates.
        """
        trainer = winning_runner.get("trainer", "")
        jockey  = winning_runner.get("jockey", "")
        horse   = winning_runner.get("horse", "")
        odds    = winning_runner.get("odds", "N/A")

        win_entry = {
            "date":    today,
            "horse":   horse,
            "trainer": trainer,
            "jockey":  jockey,
            "course":  course,
            "odds":    odds,
            "won":     True,
        }

        # Reload to avoid stale writes
        results = _load(RESULTS_PATH, {"results": []})
        # Avoid duplicates
        already = any(
            r.get("horse") == horse and r.get("date") == today and r.get("won")
            for r in results.get("results", [])
        )
        if not already:
            results["results"].append(win_entry)
            _save(RESULTS_PATH, results)

    # ─────────────────────────────────────────────────────────
    # 3. ADJUST WEIGHTINGS — runs at 21:00 BST
    # ─────────────────────────────────────────────────────────

    def adjust_weightings(self) -> dict:
        """
        Analyse which signals best predicted winners.
        Nudge performing signals up, underperforming signals down.
        Requires >= 20 settled races to avoid noise.
        Max nudge: 1% per signal per daily cycle.
        """
        settled = [r for r in self.recommendations["records"]
                  if r.get("won") is not None]

        if len(settled) < 20:
            print(f"[LearningLoop] {len(settled)}/20 settled races — skipping adjustment")
            return self.learned_weights

        signal_names = list(DEFAULT_WEIGHTS.keys())
        win_sums  = {s: 0.0 for s in signal_names}
        loss_sums = {s: 0.0 for s in signal_names}
        winners   = [r for r in settled if r.get("won")]
        losers    = [r for r in settled if not r.get("won")]

        if not winners or not losers:
            print("[LearningLoop] No win/loss spread — skipping")
            return self.learned_weights

        for rec in winners:
            for s in signal_names:
                win_sums[s] += rec.get("signals", {}).get(s, 0.5)
        for rec in losers:
            for s in signal_names:
                loss_sums[s] += rec.get("signals", {}).get(s, 0.5)

        new_weights = dict(self.learned_weights)
        NUDGE = 0.01
        MIN_W = 0.01
        MAX_W = 0.40

        print(f"[LearningLoop] Adjusting from {len(settled)} races "
              f"({len(winners)} wins, {len(losers)} losses):")

        for sig in signal_names:
            avg_win  = win_sums[sig]  / len(winners)
            avg_loss = loss_sums[sig] / len(losers)
            gap      = avg_win - avg_loss

            old_w = new_weights[sig]
            if gap > 0.05:
                new_weights[sig] = min(old_w + NUDGE, MAX_W)
            elif gap < -0.05:
                new_weights[sig] = max(old_w - NUDGE, MIN_W)

        # Renormalise to sum = 1.0
        total = sum(new_weights.values())
        new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}

        for sig, w in new_weights.items():
            old_w  = self.learned_weights.get(sig, DEFAULT_WEIGHTS[sig])
            change = w - old_w
            arrow  = "↑" if change > 0.001 else "↓" if change < -0.001 else "—"
            print(f"  {sig:20s}: {w:.3f}  {arrow} {abs(change):.3f}")

        self.learned_weights = new_weights
        _save(WEIGHTS_PATH, new_weights)
        return new_weights

    # ─────────────────────────────────────────────────────────
    # 4. DASHBOARD STATS
    # ─────────────────────────────────────────────────────────

    def get_performance_stats(self) -> dict:
        """Returns stats for the dashboard Learning Engine tab."""
        all_recs  = self.recommendations["records"]
        settled   = [r for r in all_recs if r.get("won") is not None]
        winners   = [r for r in settled if r.get("won")]
        losers    = [r for r in settled if not r.get("won")]

        if not settled:
            return {
                "total_recommendations": len(all_recs),
                "settled_races":         0,
                "winners":               0,
                "hit_rate_pct":          0.0,
                "hit_rate_7d_pct":       0.0,
                "avg_confidence_winners":  0.0,
                "avg_confidence_losers":   0.0,
                "weight_adjustments":      0,
                "current_weights":         self.learned_weights,
                "days_until_first_adjust": max(0, 20 - len(settled)),
                "note": "Tracking started. Stats build automatically as races complete each day."
            }

        hit_rate = len(winners) / len(settled) * 100

        cutoff      = (date.today() - timedelta(days=7)).isoformat()
        recent      = [r for r in settled if r.get("date", "") >= cutoff]
        recent_wins = [r for r in recent if r.get("won")]
        hit_7d      = len(recent_wins) / len(recent) * 100 if recent else 0.0

        avg_conf_win  = sum(r["confidence"] for r in winners) / len(winners) if winners else 0
        avg_conf_lose = sum(r["confidence"] for r in losers)  / len(losers)  if losers  else 0

        # Detect weight adjustments (any signal diverged from default)
        adjustments = sum(
            1 for s, w in self.learned_weights.items()
            if abs(w - DEFAULT_WEIGHTS.get(s, 0)) > 0.005
        )

        # Recent form (last 7 days results)
        recent_results = _load(RESULTS_PATH, {"results": []})
        recent_winners = [r for r in recent_results.get("results", [])
                         if r.get("date", "") >= cutoff and r.get("won")]

        return {
            "total_recommendations":   len(all_recs),
            "settled_races":           len(settled),
            "winners":                 len(winners),
            "hit_rate_pct":            round(hit_rate, 1),
            "hit_rate_7d_pct":         round(hit_7d, 1),
            "avg_confidence_winners":  round(avg_conf_win, 3),
            "avg_confidence_losers":   round(avg_conf_lose, 3),
            "weight_adjustments":      adjustments,
            "current_weights":         self.learned_weights,
            "days_until_first_adjust": max(0, 20 - len(settled)),
            "recent_winners":          recent_winners[-10:],   # Last 10 winners for display
            "note": f"Based on {len(settled)} settled races across {len(set(r.get('date') for r in settled))} days"
        }

    # ─────────────────────────────────────────────────────────
    # 5. MANUAL HOOKS (for testing / backfill)
    # ─────────────────────────────────────────────────────────

    def record_recommendation(self, race_id, runner_id, confidence_score, signals):
        """Manual hook — also called by auto_record_day."""
        record = {
            "race_id":        race_id,
            "runner":         runner_id,
            "confidence":     confidence_score,
            "signals":        signals,
            "date":           date.today().isoformat(),
            "recommended_at": datetime.now().isoformat(),
            "outcome":        None,
            "won":            None,
        }
        self.recommendations["records"].append(record)
        _save(RECOMMENDATIONS_PATH, self.recommendations)

    def record_outcome(self, race_id, winner_id):
        """Manual hook — also called by auto_settle."""
        for rec in self.recommendations["records"]:
            if rec["race_id"] == race_id and rec["outcome"] is None:
                rec["outcome"]    = winner_id
                rec["won"]        = rec["runner"].strip().lower() == winner_id.strip().lower()
                rec["settled_at"] = datetime.now().isoformat()
        _save(RECOMMENDATIONS_PATH, self.recommendations)
        print(f"[LearningLoop] Manual outcome: {winner_id} won {race_id}")

    @staticmethod
    def get_current_weights() -> dict:
        """Called by OddsModel — returns latest learned weights."""
        w = _load(WEIGHTS_PATH, None)
        if w and abs(sum(w.values()) - 1.0) < 0.02:
            return w
        return DEFAULT_WEIGHTS.copy()
