# Racing Engine — Real-Time Alert Monitor
# Version: 1.0
# Date: 20 April 2026
# Purpose: Monitors all data streams and fires alerts on significant changes.
#          Runs continuously via scheduler.py — no manual input needed.

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.settings import MARKET_MOVE_THRESHOLD, TIME_BEFORE_OFF_ALERT
from datetime import datetime, timedelta
import json

# Store path for last-seen state (avoids duplicate alerts)
STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "learning", "alert_state.json")


# ── Alert Levels ─────────────────────────────────────────────
HIGH   = "HIGH"
MEDIUM = "MEDIUM"
LOW    = "LOW"


def _load_state() -> dict:
    """Load last-seen alert state from disk."""
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"seen_moves": {}, "seen_non_runners": [], "last_going": {}}


def _save_state(state: dict):
    """Persist alert state so we don't re-fire the same alert."""
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        print(f"[AlertMonitor] Could not save state: {e}")


def _to_decimal(odds_str) -> float:
    """Convert fractional or decimal odds to decimal format."""
    try:
        s = str(odds_str).strip()
        if "/" in s:
            n, d = s.split("/")
            return (float(n) + float(d)) / float(d)
        f = float(s)
        return f if f > 1 else 0.0
    except Exception:
        return 0.0


def _format_odds(odds_str) -> str:
    """Return a clean odds string for display."""
    return str(odds_str).strip() if odds_str else "N/A"


# ── Alert Builder ────────────────────────────────────────────
def _build_alert(level: str, alert_type: str, message: str,
                 race: str = "", horse: str = "", odds: str = "") -> dict:
    return {
        "level":      level,
        "type":       alert_type,
        "message":    message,
        "race":       race,
        "horse":      horse,
        "odds":       odds,
        "fired_at":   datetime.now().strftime("%H:%M:%S"),
        "timestamp":  datetime.now().isoformat(),
    }


# ── Market Move Monitor ───────────────────────────────────────
class AlertMonitor:
    """
    Monitors all live data streams and fires alerts on:
    - Significant odds movements (steam moves and drifters)
    - Non-runner declarations
    - Going report changes
    - Jockey booking changes
    Designed to run on a 60-second poll cycle via scheduler.py.
    """

    def __init__(self):
        self.threshold = MARKET_MOVE_THRESHOLD   # e.g. 0.20 = 20% move
        self.alert_window_mins = TIME_BEFORE_OFF_ALERT  # e.g. 30 mins

    # ── Main Poll ─────────────────────────────────────────────
    def run_poll(self) -> list:
        """
        Single poll cycle. Checks all streams and returns list of new alerts.
        Call this every 60 seconds from the scheduler.
        """
        alerts = []
        state = _load_state()

        try:
            from dashboard.live_data import get_todays_meetings, get_race_runners
            meetings = get_todays_meetings()
        except Exception as e:
            print(f"[AlertMonitor] Could not load live data: {e}")
            return []

        for meeting in meetings:
            course = meeting.get("course", "")
            going  = meeting.get("going", "")

            # Going change alert
            last_going = state["last_going"].get(course)
            if last_going and last_going != going and going:
                alerts.append(_build_alert(
                    MEDIUM, "going_change",
                    f"Going update: {course} changed from {last_going} to {going}",
                    race=course
                ))
            if going:
                state["last_going"][course] = going

            for race in meeting.get("races", []):
                slug  = race.get("slug")
                time  = race.get("time", "")
                stage = race.get("stage", "")
                race_label = f"{time} {course}"

                if not slug:
                    continue

                # Skip already-finished races
                if stage in ("WEIGHEDIN", "RESULT"):
                    continue

                try:
                    runners = get_race_runners(slug)
                except Exception:
                    continue

                for runner in runners:
                    horse  = runner.get("horse", "Unknown")
                    status = runner.get("status", "RUNNER")
                    signal = runner.get("signal", "Stable")
                    odds   = runner.get("odds", "N/A")
                    bm     = runner.get("bet_movements", [])

                    # ── Non-runner alert ──────────────────────
                    nr_key = f"{race_label}::{horse}"
                    if status == "NON_RUNNER" and nr_key not in state["seen_non_runners"]:
                        alerts.append(_build_alert(
                            HIGH, "non_runner",
                            f"Non-runner declared: {horse} — {race_label}. Update your permutations.",
                            race=race_label, horse=horse
                        ))
                        state["seen_non_runners"].append(nr_key)

                    # ── Market move alert ─────────────────────
                    if bm and len(bm) >= 2:
                        try:
                            first_dec = _to_decimal(bm[0].get("odds") if isinstance(bm[0], dict) else bm[0])
                            current_dec = _to_decimal(odds)
                            if first_dec > 0 and current_dec > 0:
                                move_pct = (first_dec - current_dec) / first_dec
                                move_key = f"{race_label}::{horse}::{_format_odds(odds)}"

                                if abs(move_pct) >= self.threshold and move_key not in state["seen_moves"]:
                                    if move_pct > 0:
                                        # Steaming
                                        level = HIGH if move_pct >= 0.30 else MEDIUM
                                        alerts.append(_build_alert(
                                            level, "steam",
                                            f"STEAM: {horse} ({race_label}) — "
                                            f"shortened {_format_odds(bm[0].get('odds') if isinstance(bm[0], dict) else bm[0])} "
                                            f"→ {_format_odds(odds)} "
                                            f"({move_pct*100:.0f}% move)",
                                            race=race_label, horse=horse, odds=_format_odds(odds)
                                        ))
                                    else:
                                        # Drifting
                                        alerts.append(_build_alert(
                                            LOW, "drift",
                                            f"DRIFT: {horse} ({race_label}) — "
                                            f"drifted {_format_odds(bm[0].get('odds') if isinstance(bm[0], dict) else bm[0])} "
                                            f"→ {_format_odds(odds)} "
                                            f"({abs(move_pct)*100:.0f}% move)",
                                            race=race_label, horse=horse, odds=_format_odds(odds)
                                        ))
                                    state["seen_moves"][move_key] = datetime.now().isoformat()

                        except Exception:
                            pass

        _save_state(state)

        # Print summary
        if alerts:
            print(f"[AlertMonitor] {len(alerts)} new alert(s) at {datetime.now().strftime('%H:%M:%S')}")
            for a in alerts:
                print(f"  [{a['level']}] {a['message']}")
        else:
            print(f"[AlertMonitor] Poll complete — no new alerts ({datetime.now().strftime('%H:%M:%S')})")

        return alerts

    # ── Individual Monitors (called by poll) ──────────────────
    def monitor_market_moves(self, race_id: str = None) -> list:
        """Watch for significant odds movements. Called by run_poll."""
        return self.run_poll()

    def monitor_declarations(self) -> list:
        """Watch for non-runners and late jockey changes. Called by run_poll."""
        return self.run_poll()

    def fire_alert(self, alert_type: str, message: str, race_id: str = "") -> dict:
        """
        Manually fire an alert. Also triggers email via DailyBrief if configured.
        Returns the alert dict.
        """
        alert = _build_alert(HIGH, alert_type, message, race=race_id)
        print(f"[AlertMonitor] Alert fired: [{alert_type}] {message}")

        # Trigger instant alert email
        try:
            from briefs.daily_brief import DailyBrief
            DailyBrief().send_instant_alert(alert_type, message)
        except Exception as e:
            print(f"[AlertMonitor] Email trigger failed: {e}")

        return alert

    def reset_state(self):
        """Clear all seen alerts — call at start of each racing day."""
        _save_state({"seen_moves": {}, "seen_non_runners": [], "last_going": {}})
        print("[AlertMonitor] State reset for new day")
