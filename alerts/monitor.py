# Racing Engine — Real-Time Market Move Monitor
# Version: 2.0 — 21 April 2026
#
# How it works:
#   Every 60 seconds the scheduler calls run_poll().
#   We fetch live runner odds for all upcoming UK/Irish races.
#   We snapshot each runner's current odds to disk.
#   On the NEXT poll we compare current odds to the snapshot.
#   If the move exceeds MARKET_MOVE_THRESHOLD (20%), we fire an alert.
#   State is persisted to disk so polls survive restarts.
#
# Alert types fired:
#   HIGH   — Big steam (>=30% price move in) within 60 mins of off
#   HIGH   — Non-runner declared
#   MEDIUM — Moderate steam (20-29% move in)
#   MEDIUM — Going report change at a course
#   LOW    — Drift (>=20% move out)

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from datetime import datetime, date, timedelta
from config.settings import MARKET_MOVE_THRESHOLD, TIME_BEFORE_OFF_ALERT

SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "..", "learning", "market_state.json")
STATE_PATH    = os.path.join(os.path.dirname(__file__), "..", "learning", "alert_state.json")

HIGH   = "HIGH"
MEDIUM = "MEDIUM"
LOW    = "LOW"


# ── Utilities ─────────────────────────────────────────────────

def _to_decimal(odds_str) -> float:
    try:
        s = str(odds_str).strip()
        if "/" in s:
            n, d = s.split("/")
            return (float(n) + float(d)) / float(d)
        f = float(s)
        return f if f > 1 else 0.0
    except Exception:
        return 0.0

def _load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return default

def _save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"[Monitor] Save failed {path}: {e}")

def _build_alert(level, alert_type, message, race="", horse="", odds=""):
    return {
        "level":     level,
        "type":      alert_type,
        "message":   message,
        "race":      race,
        "horse":     horse,
        "odds":      odds,
        "fired_at":  datetime.now().strftime("%H:%M:%S"),
        "timestamp": datetime.now().isoformat(),
    }

def _mins_to_off(race_time_str: str) -> float:
    """Returns minutes until the race based on time string like '14:30'."""
    try:
        now = datetime.now()
        h, m = race_time_str.strip().split(":")
        off = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        diff = (off - now).total_seconds() / 60
        return diff
    except Exception:
        return 999


# ── Alert Monitor ─────────────────────────────────────────────

class AlertMonitor:
    """
    Polls every 60s. Snapshots odds and fires alerts on:
    - Steam (price shortening >= 20%)
    - Drift (price drifting >= 20%)
    - Non-runner declarations
    - Going report changes
    """

    def __init__(self):
        self.threshold    = MARKET_MOVE_THRESHOLD   # 0.20
        self.alert_window = TIME_BEFORE_OFF_ALERT   # 30 mins

    def run_poll(self) -> list:
        alerts    = []
        state     = _load_json(STATE_PATH, {"seen_moves": {}, "seen_non_runners": [], "last_going": {}})
        snapshots = _load_json(SNAPSHOT_PATH, {"snapshots": {}, "alerts_fired": []})
        now_str   = datetime.now().strftime("%H:%M:%S")

        try:
            from dashboard.live_data import get_todays_meetings, get_race_runners
            meetings = get_todays_meetings()
        except Exception as e:
            print(f"[Monitor] Live data unavailable: {e}")
            return []

        for meeting in meetings:
            course = meeting.get("course", "")
            going  = meeting.get("going", "")

            # ── Going change ──────────────────────────────────
            prev_going = state["last_going"].get(course)
            if prev_going and prev_going != going and going:
                key = f"going::{course}::{going}"
                if key not in state["seen_moves"]:
                    alerts.append(_build_alert(
                        MEDIUM, "going_change",
                        f"Going update at {course}: {prev_going} → {going}",
                        race=course
                    ))
                    state["seen_moves"][key] = now_str
            if going:
                state["last_going"][course] = going

            for race in meeting.get("races", []):
                slug   = race.get("slug")
                time   = race.get("time", "")
                stage  = race.get("stage", "")
                label  = f"{time} {course}"
                mins   = _mins_to_off(time)

                if not slug or stage in ("WEIGHEDIN", "RESULT"):
                    continue

                try:
                    runners = get_race_runners(slug)
                except Exception:
                    continue

                for runner in runners:
                    horse  = runner.get("horse", "")
                    status = runner.get("status", "RUNNER")
                    odds   = runner.get("odds", "N/A")
                    key    = f"{label}::{horse}"

                    # ── Non-runner ────────────────────────────
                    if status == "NON_RUNNER":
                        nr_key = f"NR::{key}"
                        if nr_key not in state["seen_non_runners"]:
                            alerts.append(_build_alert(
                                HIGH, "non_runner",
                                f"NON-RUNNER: {horse} removed from {label}. Review permutations.",
                                race=label, horse=horse
                            ))
                            state["seen_non_runners"].append(nr_key)
                        continue

                    # ── Odds snapshot & move detection ────────
                    current_dec = _to_decimal(odds)
                    prev_snap   = snapshots["snapshots"].get(key)

                    if prev_snap:
                        prev_dec  = _to_decimal(prev_snap.get("odds", "0"))
                        snap_time = prev_snap.get("time", "")

                        if prev_dec > 0 and current_dec > 0:
                            move_pct = (prev_dec - current_dec) / prev_dec  # + = steam, - = drift

                            if abs(move_pct) >= self.threshold:
                                move_key = f"MOVE::{key}::{odds}"

                                if move_key not in state["seen_moves"]:
                                    if move_pct > 0:
                                        # STEAM — price shortened
                                        level = HIGH if (move_pct >= 0.30 or mins <= self.alert_window) else MEDIUM
                                        alerts.append(_build_alert(
                                            level, "steam",
                                            f"{'🔥 BIG ' if move_pct >= 0.30 else ''}STEAM: {horse} ({label}) "
                                            f"— {prev_snap['odds']} → {odds} "
                                            f"({move_pct*100:.0f}% in)"
                                            + (f" — {mins:.0f} mins to off" if mins < 60 else ""),
                                            race=label, horse=horse, odds=odds
                                        ))
                                    else:
                                        # DRIFT — price lengthened
                                        alerts.append(_build_alert(
                                            LOW, "drift",
                                            f"DRIFT: {horse} ({label}) "
                                            f"— {prev_snap['odds']} → {odds} "
                                            f"({abs(move_pct)*100:.0f}% out)",
                                            race=label, horse=horse, odds=odds
                                        ))

                                    state["seen_moves"][move_key] = now_str

                    # Update snapshot with current odds
                    snapshots["snapshots"][key] = {
                        "odds":        odds,
                        "decimal":     current_dec,
                        "time":        now_str,
                        "race":        label,
                        "mins_to_off": round(mins, 1),
                    }

        _save_json(STATE_PATH, state)
        _save_json(SNAPSHOT_PATH, snapshots)

        if alerts:
            print(f"[Monitor] {len(alerts)} alert(s) — {now_str}")
            for a in alerts:
                print(f"  [{a['level']}] {a['message']}")
        else:
            print(f"[Monitor] Poll complete — no new moves — {now_str}")

        return alerts

    def get_current_moves(self) -> list:
        """
        Returns all runners where a move has been detected in the current session.
        Used by the dashboard Live Alerts tab.
        """
        snapshots = _load_json(SNAPSHOT_PATH, {"snapshots": {}})
        state     = _load_json(STATE_PATH, {"seen_moves": {}})
        moves     = []

        for move_key in state.get("seen_moves", {}):
            if move_key.startswith("MOVE::"):
                parts = move_key.split("::")
                if len(parts) >= 3:
                    race_horse = parts[1]   # "14:30 Pontefract::Lady Youmzain"
                    fired_time = state["seen_moves"][move_key]
                    # Find current snapshot
                    snap = snapshots["snapshots"].get(race_horse, {})
                    moves.append({
                        "race_horse": race_horse,
                        "current_odds": snap.get("odds", "N/A"),
                        "mins_to_off":  snap.get("mins_to_off", "-"),
                        "detected_at":  fired_time,
                    })

        return moves

    def fire_alert(self, alert_type: str, message: str, race_id: str = "") -> dict:
        """Manually fire an alert and email it."""
        alert = _build_alert(HIGH, alert_type, message, race=race_id)
        print(f"[Monitor] Manual alert: [{alert_type}] {message}")
        try:
            from briefs.daily_brief import DailyBrief
            DailyBrief().send_instant_alert(alert_type, message)
        except Exception as e:
            print(f"[Monitor] Email trigger failed: {e}")
        return alert

    def reset_state(self):
        """Clear state at start of each racing day."""
        _save_json(STATE_PATH, {"seen_moves": {}, "seen_non_runners": [], "last_going": {}})
        _save_json(SNAPSHOT_PATH, {"snapshots": {}, "alerts_fired": []})
        print("[Monitor] State reset for new racing day")
