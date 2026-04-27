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
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

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
    "market_moves": 0.10,
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
        Record only OFFICIAL selections for today — those that cleared
        BOTH the confidence threshold AND the 4/6 price cut-off via
        _get_official_selections() in briefs/daily_brief.py.

        Previously this recorded every runner in every race (~268/day),
        which drowned the learning signal in noise. v2.5.42: official
        selections only (typically 5–15/day).
        """
        today = date.today().isoformat()

        # Don't double-record the same day
        existing_today = [r for r in self.recommendations["records"]
                         if r.get("date") == today]
        if existing_today:
            print(f"[LearningLoop] Already recorded {len(existing_today)} recommendations for {today}")
            return len(existing_today)

        try:
            # Use the same official-selection pipeline that drives the
            # morning brief and the app — single source of truth.
            from briefs.daily_brief import _get_official_selections
            from engine.odds_model import OddsModel
            selections = _get_official_selections()
            model      = OddsModel()
        except Exception as e:
            print(f"[LearningLoop] Could not load official selections: {e}")
            return 0

        if not selections:
            print(f"[LearningLoop] No official selections for {today} — nothing to record")
            return 0

        count = 0
        for sel in selections:
            horse  = str(sel.get("horse", ""))
            course = str(sel.get("course", ""))
            time_  = str(sel.get("time", ""))
            if not horse or not course or not time_:
                continue

            race_id = f"{today}::{time_}::{course}"

            # Recompute signal breakdown so each record has full signal context
            runner_data = {
                "odds":    sel.get("curr_odds", sel.get("odds", "N/A")),
                "form":    sel.get("form", "-"),
                "going":   sel.get("going", ""),
                "trainer": sel.get("trainer", "-"),
                "jockey":  sel.get("jockey", "-"),
                "signal":  sel.get("signal", "Stable"),
                "tf_stars": sel.get("tf_stars"),
                "course":  course,
                "bet_movements": [],
            }
            try:
                signals = model.get_signal_breakdown(runner_data)
            except Exception:
                signals = {}

            try:
                confidence = float(sel.get("confidence", 0.0) or 0.0)
            except Exception:
                confidence = 0.0

            record = {
                "race_id":        race_id,
                "race_name":      str(sel.get("race_name", "")),
                "runner":         horse,
                "course":         course,
                "time":           time_,
                "date":           today,
                "confidence":     confidence,
                "signals":        signals,
                "odds":           str(sel.get("odds", "N/A")),
                "current_odds":   str(sel.get("curr_odds", "")),
                "recommended_at": datetime.now().isoformat(),
                "outcome":        None,
                "won":            None,
            }
            self.recommendations["records"].append(record)
            count += 1

        _save(RECOMMENDATIONS_PATH, self.recommendations)
        print(f"[LearningLoop] Recorded {count} OFFICIAL selections for {today}")
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

    # ─────────────────────────────────────────────────────────
    # 2b. HISTORICAL SETTLEMENT — for past dates not covered by auto_settle
    # ─────────────────────────────────────────────────────────

    def settle_historical_date(self, date_str: str) -> int:
        """
        Fetch Sporting Life results for a past date and settle open recommendations.
        date_str: YYYY-MM-DD string
        Returns count of races settled.
        """
        url = f"https://www.sportinglife.com/racing/results/{date_str}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                print(f"[LearningLoop] Historical fetch {date_str} status {r.status_code}")
                return 0
            soup = BeautifulSoup(r.text, "html.parser")
            nd = soup.find("script", id="__NEXT_DATA__")
            if not nd:
                print(f"[LearningLoop] No __NEXT_DATA__ for {date_str}")
                return 0
            data = json.loads(nd.get_text())
        except Exception as e:
            print(f"[LearningLoop] Historical fetch failed {date_str}: {e}")
            return 0

        meetings = data.get("props", {}).get("pageProps", {}).get("meetings", []) or []
        settled = 0

        for meeting in meetings:
            for race in meeting.get("races", []) or []:
                course = race.get("course_name", "")
                time_  = race.get("time", "")
                if not course or not time_:
                    continue

                winner = None
                for th in race.get("top_horses", []) or []:
                    try:
                        if int(th.get("position", 0)) == 1:
                            winner = th.get("name", "")
                            break
                    except Exception:
                        pass
                if not winner:
                    continue

                race_id = f"{date_str}::{time_}::{course}"

                already_settled = any(
                    r.get("race_id") == race_id and r.get("won") is not None
                    for r in self.recommendations["records"]
                )
                if already_settled:
                    continue

                # Match to open recs
                matched_any = False
                for rec in self.recommendations["records"]:
                    if rec.get("race_id") == race_id and rec.get("outcome") is None:
                        rec["outcome"]    = winner
                        rec["won"]        = (str(rec.get("runner", "")).strip().lower()
                                             == str(winner).strip().lower())
                        rec["settled_at"] = datetime.now().isoformat()
                        matched_any = True

                if not matched_any:
                    continue

                # Store result
                result_record = {
                    "race_id":    race_id,
                    "course":     course,
                    "time":       time_,
                    "date":       date_str,
                    "winner":     winner,
                    "settled_at": datetime.now().isoformat(),
                }
                self.results.setdefault("results", []).append(result_record)

                print(f"[LearningLoop] Settled: {winner} won {race_id}")
                settled += 1

        if settled:
            _save(RECOMMENDATIONS_PATH, self.recommendations)
            _save(RESULTS_PATH, self.results)
        print(f"[LearningLoop] Historical settlement {date_str}: {settled} races")
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

        # `won` may come back from JSON as bool, None, or string ("True"/"False").
        # Normalise to a real bool before partitioning so arithmetic later can't
        # accidentally see a string in the signal sums.
        def _won(rec) -> bool:
            v = rec.get("won")
            if isinstance(v, bool):
                return v
            return str(v).strip().lower() == "true"

        winners = [r for r in settled if _won(r)]
        losers  = [r for r in settled if not _won(r)]

        if not winners or not losers:
            print("[LearningLoop] No win/loss spread — skipping")
            return self.learned_weights

        def _sigval(rec, s) -> float:
            try:
                return float(rec.get("signals", {}).get(s, 0.5) or 0.5)
            except (TypeError, ValueError):
                return 0.5

        for rec in winners:
            for s in signal_names:
                win_sums[s] += _sigval(rec, s)
        for rec in losers:
            for s in signal_names:
                loss_sums[s] += _sigval(rec, s)

        # Cast learned weights through float() — any value re-read from JSON
        # may surface as a string and break the renormalise arithmetic.
        new_weights = {k: float(v or 0.0) for k, v in self.learned_weights.items()}
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
        def _won(rec) -> bool:
            v = rec.get("won")
            if isinstance(v, bool):
                return v
            return str(v).strip().lower() == "true"

        all_recs  = self.recommendations["records"]
        settled   = [r for r in all_recs if r.get("won") is not None]
        winners   = [r for r in settled if _won(r)]
        losers    = [r for r in settled if not _won(r)]

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
        recent_wins = [r for r in recent if _won(r)]
        hit_7d      = len(recent_wins) / len(recent) * 100 if recent else 0.0

        def _conf(rec) -> float:
            try:
                return float(rec.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        avg_conf_win  = sum(_conf(r) for r in winners) / len(winners) if winners else 0
        avg_conf_lose = sum(_conf(r) for r in losers)  / len(losers)  if losers  else 0

        # Detect weight adjustments (any signal diverged from default)
        adjustments = sum(
            1 for s, w in self.learned_weights.items()
            if abs(w - DEFAULT_WEIGHTS.get(s, 0)) > 0.005
        )

        # Recent form (last 7 days results)
        recent_results = _load(RESULTS_PATH, {"results": []})
        recent_winners = [r for r in recent_results.get("results", [])
                         if r.get("date", "") >= cutoff and _won(r)]

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


# ── Standalone helpers (for scripts / dashboard auto-heal) ─────────

def run_historical_settlement(date_str: str) -> int:
    """Instantiate a LearningLoop and settle a single past date."""
    loop = LearningLoop()
    return loop.settle_historical_date(date_str)


def settle_outstanding_recommendations() -> int:
    """
    Settle all pending recommendations by fetching their historical dates.
    Skips today's date (auto_settle handles that). Returns total races settled.
    """
    data = _load(RECOMMENDATIONS_PATH, {"records": []})
    recs = data.get("records", []) if isinstance(data, dict) else []
    today = date.today().isoformat()
    pending_dates = sorted({
        r.get("date") for r in recs
        if r.get("won") is None and r.get("date") and r.get("date") != today
    })
    if not pending_dates:
        return 0

    loop = LearningLoop()
    total = 0
    for d in pending_dates:
        try:
            total += loop.settle_historical_date(d)
        except Exception as e:
            print(f"[LearningLoop] settle_outstanding_recommendations error {d}: {e}")
    return total
