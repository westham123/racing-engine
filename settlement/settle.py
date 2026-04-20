# Racing Engine — Settlement Engine
# Version: 1.0
# Date: 20 April 2026
# Purpose: Picks up settled race results from the live feed,
#          records them into the results store, feeds the learning loop,
#          and flags exceptions for manual review.

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, date
import json

SETTLED_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "learning", "settled_races.json")


def _load_settled() -> dict:
    """Load the log of already-settled races to avoid double-counting."""
    try:
        if os.path.exists(SETTLED_LOG_PATH):
            with open(SETTLED_LOG_PATH, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"settled_race_ids": [], "exceptions": []}


def _save_settled(data: dict):
    try:
        os.makedirs(os.path.dirname(SETTLED_LOG_PATH), exist_ok=True)
        with open(SETTLED_LOG_PATH, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"[SettlementEngine] Could not save settled log: {e}")


class SettlementEngine:
    """
    Polls the live results feed after each race.
    Settles each result into the learning store.
    Flags exceptions (dead heats, DQs, SEs) for review.
    Triggers the learning loop after each settlement.
    Designed to run on a 2-minute poll cycle via scheduler.py.
    """

    # ── Main Poll ─────────────────────────────────────────────
    def run_settlement_poll(self) -> list:
        """
        Poll for newly settled races and process each one.
        Returns list of settlement dicts processed this cycle.
        Called every 2 minutes by scheduler.py.
        """
        settled_log = _load_settled()
        already_settled = set(settled_log["settled_race_ids"])
        new_settlements = []

        try:
            from dashboard.live_data import get_todays_meetings, get_race_runners
            meetings = get_todays_meetings()
        except Exception as e:
            print(f"[SettlementEngine] Could not load live data: {e}")
            return []

        for meeting in meetings:
            course = meeting.get("course", "")
            going  = meeting.get("going", "")

            for race in meeting.get("races", []):
                slug  = race.get("slug")
                time  = race.get("time", "")
                stage = race.get("stage", "")
                name  = race.get("name", "")
                race_id = f"{date.today().isoformat()}::{time}::{course}"

                # Only process finished races not already settled
                if stage not in ("WEIGHEDIN", "RESULT"):
                    continue
                if race_id in already_settled:
                    continue
                if not slug:
                    continue

                try:
                    runners = get_race_runners(slug)
                except Exception as e:
                    print(f"[SettlementEngine] Could not fetch runners for {race_id}: {e}")
                    continue

                result = self.settle_race(race_id, runners, course, going, time, name)
                if result:
                    new_settlements.append(result)
                    already_settled.add(race_id)
                    settled_log["settled_race_ids"].append(race_id)

        _save_settled(settled_log)

        if new_settlements:
            print(f"[SettlementEngine] Settled {len(new_settlements)} new race(s)")
        else:
            print(f"[SettlementEngine] Poll complete — no new results ({datetime.now().strftime('%H:%M')})")

        return new_settlements

    # ── Settle a Single Race ──────────────────────────────────
    def settle_race(self, race_id: str, runners: list,
                    course: str, going: str, time: str, name: str) -> dict:
        """
        Process official result and write to results store + learning loop.
        Returns settlement dict, or None if result not determinable.
        """
        # Sort by finish position
        finishers = sorted(
            [r for r in runners if r.get("finish_position") is not None],
            key=lambda x: int(str(x["finish_position"]).strip() or 99)
        )

        if not finishers:
            print(f"[SettlementEngine] No finish positions for {race_id}")
            return None

        winner = finishers[0]
        second = finishers[1] if len(finishers) > 1 else None
        third  = finishers[2] if len(finishers) > 2 else None

        # Check for exceptions
        exceptions = self._check_exceptions(finishers, race_id)

        settlement = {
            "race_id":     race_id,
            "race_name":   name,
            "course":      course,
            "going":       going,
            "time":        time,
            "date":        date.today().isoformat(),
            "winner":      winner.get("horse", "Unknown"),
            "winner_jockey":  winner.get("jockey", "-"),
            "winner_trainer": winner.get("trainer", "-"),
            "winner_odds":    winner.get("odds", "N/A"),
            "second":      second.get("horse", "-") if second else "-",
            "third":       third.get("horse", "-") if third else "-",
            "full_result": [
                {
                    "position": r.get("finish_position"),
                    "horse":    r.get("horse"),
                    "jockey":   r.get("jockey"),
                    "trainer":  r.get("trainer"),
                    "odds":     r.get("odds"),
                }
                for r in finishers[:4]
            ],
            "exceptions":  exceptions,
            "settled_at":  datetime.now().isoformat(),
        }

        # Write to results store (feeds trainer/jockey form scorer)
        self._write_to_results_store(settlement)

        # Trigger learning loop update
        self._trigger_learning_loop(race_id, winner.get("horse"), runners)

        print(f"[SettlementEngine] Settled: {time} {course} — Winner: {winner.get('horse')} "
              f"({winner.get('odds', 'N/A')})"
              + (f" [EXCEPTION: {', '.join(exceptions)}]" if exceptions else ""))

        return settlement

    # ── Exception Checker ─────────────────────────────────────
    def _check_exceptions(self, finishers: list, race_id: str) -> list:
        """
        Flags unusual results that may need manual review before acting on.
        Returns list of exception type strings (empty = clean result).
        """
        exceptions = []

        if not finishers:
            return exceptions

        # Dead heat — two horses with same finish position
        positions = [str(r.get("finish_position", "")).strip() for r in finishers]
        if len(positions) != len(set(positions)):
            exceptions.append("DEAD_HEAT")
            print(f"[SettlementEngine] Dead heat detected in {race_id}")

        # Disqualification — any runner flagged as DQ
        for r in finishers:
            status = str(r.get("status", "")).upper()
            if "DISQ" in status or "DQ" in status:
                exceptions.append("DISQUALIFICATION")
                print(f"[SettlementEngine] DQ detected: {r.get('horse')} in {race_id}")
                break

        return exceptions

    def flag_exception(self, race_id: str, exception_type: str, details: str):
        """
        Manually flag an exception for a race.
        Logged to settled_races.json for review.
        """
        settled_log = _load_settled()
        settled_log["exceptions"].append({
            "race_id":        race_id,
            "exception_type": exception_type,
            "details":        details,
            "flagged_at":     datetime.now().isoformat(),
        })
        _save_settled(settled_log)
        print(f"[SettlementEngine] Exception flagged: [{exception_type}] {race_id} — {details}")

    # ── Results Store Writer ──────────────────────────────────
    def _write_to_results_store(self, settlement: dict):
        """
        Writes the winner to the results store used by form_scorer.py.
        This feeds the trainer/jockey rolling win rate calculations.
        """
        try:
            from engine.form_scorer import record_result
            record_result(
                race_date = settlement["date"],
                course    = settlement["course"],
                race_time = settlement["time"],
                winner    = settlement["winner"],
                jockey    = settlement["winner_jockey"],
                trainer   = settlement["winner_trainer"],
                odds      = settlement["winner_odds"],
            )
        except Exception as e:
            print(f"[SettlementEngine] Could not write to results store: {e}")

    # ── Learning Loop Trigger ─────────────────────────────────
    def _trigger_learning_loop(self, race_id: str, winner: str, all_runners: list):
        """
        After settling, tell the learning loop which horse won
        so it can compare against the engine's pre-race recommendation.
        """
        try:
            from learning.loop import LearningLoop
            loop = LearningLoop()
            loop.record_outcome(race_id, winner)
        except Exception as e:
            print(f"[SettlementEngine] Could not trigger learning loop: {e}")
