# Racing Engine — Settlement Engine
# Version: 2.0 — 21 April 2026
#
# What it does:
#   Every 2 minutes the scheduler calls run_settlement_poll().
#   We check Sporting Life for races marked WEIGHEDIN or RESULT.
#   For each newly-finished race we:
#     1. Extract the full result (winner, 2nd, 3rd, SP odds)
#     2. Cross-check against our pre-race recommendations
#     3. Flag hit (we tipped the winner) or miss
#     4. Check for exceptions (dead heats, DQs, walkovers)
#     5. Write winner to results_store for trainer/jockey form scoring
#     6. Trigger learning loop outcome recording
#     7. Store full settled race in settled_races.json (permanent history)
#
# Dashboard feed:
#   get_results_for_dashboard() returns recent settled races
#   with confidence scores, hit/miss flag, and SP odds — powers
#   the Results History tab.

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from datetime import datetime, date, timedelta

SETTLED_PATH = os.path.join(os.path.dirname(__file__), "..", "learning", "settled_races.json")


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
        print(f"[Settlement] Save failed {path}: {e}")

def _safe_int(val, default=99):
    try:
        return int(str(val).strip())
    except Exception:
        return default


class SettlementEngine:
    """
    Polls live feed every 2 minutes.
    Settles finished races, records results, flags exceptions.
    """

    def run_settlement_poll(self) -> list:
        """
        Main poll — called by scheduler every 2 minutes.
        Returns list of newly settled race dicts.
        """
        store      = _load(SETTLED_PATH, {"races": [], "settled_ids": [], "exceptions": []})
        settled_ids = set(store.get("settled_ids", []))
        new_settled = []

        try:
            from dashboard.live_data import get_todays_meetings, get_race_runners
            meetings = get_todays_meetings()
        except Exception as e:
            print(f"[Settlement] Live data unavailable: {e}")
            return []

        today = date.today().isoformat()

        for meeting in meetings:
            course = meeting.get("course", "")
            going  = meeting.get("going", "")

            for race in meeting.get("races", []):
                stage   = race.get("stage", "")
                time_   = race.get("time", "")
                name_   = race.get("name", "")
                slug    = race.get("slug")
                race_id = f"{today}::{time_}::{course}"

                if stage not in ("WEIGHEDIN", "RESULT"):
                    continue
                if race_id in settled_ids or not slug:
                    continue

                try:
                    runners = get_race_runners(slug)
                except Exception as e:
                    print(f"[Settlement] Could not fetch {race_id}: {e}")
                    continue

                result = self._settle_race(race_id, runners, course, going, time_, name_, today)
                if result:
                    store["races"].append(result)
                    store["settled_ids"].append(race_id)
                    if result.get("exceptions"):
                        store["exceptions"].append({
                            "race_id":    race_id,
                            "exceptions": result["exceptions"],
                            "flagged_at": datetime.now().isoformat(),
                        })
                    new_settled.append(result)

        _save(SETTLED_PATH, store)

        if new_settled:
            print(f"[Settlement] Settled {len(new_settled)} race(s) — {datetime.now().strftime('%H:%M')}")
        else:
            print(f"[Settlement] No new results — {datetime.now().strftime('%H:%M')}")

        return new_settled

    def _settle_race(self, race_id, runners, course, going, time_, name_, today) -> dict:
        """Process a single finished race. Returns settlement dict or None."""

        # Sort finishers by position
        finishers = sorted(
            [r for r in runners if r.get("finish_position") is not None],
            key=lambda x: _safe_int(x.get("finish_position"))
        )

        if not finishers:
            print(f"[Settlement] No finish positions available yet for {race_id}")
            return None

        winner = finishers[0]
        second = finishers[1] if len(finishers) > 1 else {}
        third  = finishers[2] if len(finishers) > 2 else {}

        # Check for exceptions
        exceptions = self._check_exceptions(finishers, race_id)

        # Cross-check against our recommendation
        engine_tipped, engine_confidence, engine_odds = self._check_recommendation(
            race_id, winner.get("horse", "")
        )

        # Build top-4 result
        top4 = [
            {
                "pos":     r.get("finish_position"),
                "horse":   r.get("horse", ""),
                "jockey":  r.get("jockey", ""),
                "trainer": r.get("trainer", ""),
                "odds":    r.get("odds", "N/A"),
            }
            for r in finishers[:4]
        ]

        settlement = {
            "race_id":          race_id,
            "race_name":        name_,
            "course":           course,
            "going":            going,
            "time":             time_,
            "date":             today,
            "winner":           winner.get("horse", "Unknown"),
            "winner_jockey":    winner.get("jockey", "-"),
            "winner_trainer":   winner.get("trainer", "-"),
            "winner_odds":      winner.get("odds", "N/A"),
            "second":           second.get("horse", "-"),
            "third":            third.get("horse", "-"),
            "top4":             top4,
            "engine_tipped":    engine_tipped,      # Did our engine recommend the winner?
            "engine_confidence": engine_confidence, # What confidence score did we give the winner?
            "engine_odds":      engine_odds,        # What odds did we record pre-race?
            "result_flag":      "✅ HIT" if engine_tipped else "❌ MISS",
            "exceptions":       exceptions,
            "settled_at":       datetime.now().isoformat(),
        }

        # Write to results store (feeds trainer/jockey form)
        self._write_results_store(settlement)

        # Trigger learning loop
        self._trigger_learning(race_id, winner.get("horse", ""))

        # Send instant alert email if high-confidence hit
        if engine_tipped and engine_confidence and engine_confidence >= 0.65:
            self._send_winner_alert(settlement)

        exc_str = f" [⚠️ {', '.join(exceptions)}]" if exceptions else ""
        print(
            f"[Settlement] {time_} {course}: {winner.get('horse')} "
            f"({winner.get('odds','N/A')}) — "
            f"{'✅ HIT' if engine_tipped else '❌ MISS'}"
            f"{exc_str}"
        )

        return settlement

    def _check_exceptions(self, finishers: list, race_id: str) -> list:
        """Detect dead heats, DQs, and walkovers."""
        exceptions = []
        positions = [_safe_int(r.get("finish_position")) for r in finishers]

        # Dead heat — duplicate position 1
        if positions.count(1) > 1:
            exceptions.append("DEAD_HEAT")

        # Walkover — only 1 runner
        if len(finishers) == 1:
            exceptions.append("WALKOVER")

        # DQ markers in status
        for r in finishers:
            status = str(r.get("status", "")).upper()
            if "DISQ" in status or "DQ" in status:
                exceptions.append("DISQUALIFICATION")
                break

        return exceptions

    def _check_recommendation(self, race_id: str, winner: str):
        """
        Look up whether we recommended the winner before the race.
        Returns (tipped: bool, confidence: float|None, odds: str|None)
        """
        try:
            from learning.loop import _load, RECOMMENDATIONS_PATH
            recs = _load(RECOMMENDATIONS_PATH, {"records": []})
            for rec in recs.get("records", []):
                if rec.get("race_id") == race_id:
                    horse = rec.get("runner", "")
                    if horse.strip().lower() == winner.strip().lower():
                        return True, rec.get("confidence"), rec.get("odds")
        except Exception:
            pass
        return False, None, None

    def _write_results_store(self, s: dict):
        """Write winner to results_store for trainer/jockey rolling win rates."""
        try:
            from engine.form_scorer import record_result
            record_result(
                race_date = s["date"],
                course    = s["course"],
                race_time = s["time"],
                winner    = s["winner"],
                jockey    = s["winner_jockey"],
                trainer   = s["winner_trainer"],
                odds      = s["winner_odds"],
            )
        except Exception as e:
            print(f"[Settlement] Results store write failed: {e}")

    def _trigger_learning(self, race_id: str, winner: str):
        """Tell the learning loop who won."""
        try:
            from learning.loop import LearningLoop
            LearningLoop().record_outcome(race_id, winner)
        except Exception as e:
            print(f"[Settlement] Learning loop trigger failed: {e}")

    def _send_winner_alert(self, s: dict):
        """Email instant alert when a high-confidence selection wins."""
        try:
            from briefs.daily_brief import DailyBrief
            msg = (
                f"✅ WINNER: {s['winner']} — {s['time']} {s['course']} "
                f"@ {s['winner_odds']} — Engine confidence was {s['engine_confidence']:.0%}"
            )
            DailyBrief().send_instant_alert("winner", msg)
        except Exception as e:
            print(f"[Settlement] Winner alert email failed: {e}")

    def flag_exception(self, race_id: str, exception_type: str, details: str):
        """Manually flag an exception."""
        store = _load(SETTLED_PATH, {"races": [], "settled_ids": [], "exceptions": []})
        store["exceptions"].append({
            "race_id":    race_id,
            "type":       exception_type,
            "details":    details,
            "flagged_at": datetime.now().isoformat(),
        })
        _save(SETTLED_PATH, store)
        print(f"[Settlement] Exception flagged: [{exception_type}] {race_id}")

    # ── Dashboard Feed ────────────────────────────────────────

    def get_results_for_dashboard(self, days: int = 7) -> list:
        """
        Returns settled races from the last N days for the Results History tab.
        Sorted most recent first.
        """
        store  = _load(SETTLED_PATH, {"races": []})
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        recent = [
            r for r in store.get("races", [])
            if r.get("date", "") >= cutoff
        ]
        return sorted(recent, key=lambda x: (x.get("date",""), x.get("time","")), reverse=True)

    def get_summary_stats(self) -> dict:
        """Returns hit rate and key stats for the dashboard KPI bar."""
        store = _load(SETTLED_PATH, {"races": []})
        races = store.get("races", [])
        if not races:
            return {"total": 0, "hits": 0, "hit_rate": 0.0, "exceptions": 0}

        hits       = sum(1 for r in races if r.get("engine_tipped"))
        exceptions = sum(1 for r in races if r.get("exceptions"))
        # Rolling 7-day
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        recent = [r for r in races if r.get("date", "") >= cutoff]
        recent_hits = sum(1 for r in recent if r.get("engine_tipped"))

        return {
            "total":          len(races),
            "hits":           hits,
            "hit_rate":       round(hits / len(races) * 100, 1) if races else 0.0,
            "hit_rate_7d":    round(recent_hits / len(recent) * 100, 1) if recent else 0.0,
            "exceptions":     exceptions,
            "last_winner":    next((r["winner"] for r in races if r.get("engine_tipped")), None),
        }
