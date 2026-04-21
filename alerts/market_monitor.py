# Racing Engine — Multi-Source Market Move Monitor
# Version: 1.0
# Date: 21 April 2026
#
# Monitors odds movements across ALL UK and Irish bookmakers simultaneously.
# Detects:
#   - Market-wide steam (multiple bookmakers shortening together)
#   - Single-bookmaker moves (e.g. Bet365 alone shortens significantly)
#   - Exchange money (large Betfair matched volume = confidence signal)
#   - Best-available price improvements (BOG hunters)
#   - Suspicious drifts (market knows something)
#   - Cross-bookmaker arb opportunities (optional)
#
# Runs on a 60-second poll cycle via scheduler.py

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from datetime import datetime, date
from collections import defaultdict

from data.odds_aggregator import get_all_odds
from config.settings import MARKET_MOVE_THRESHOLD

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "learning", "market_state.json")

# ── Bookmaker groups ────────────────────────────────────────
# Used to weight "market-wide" steam vs single-bookie moves

MAJOR_UK_BOOKMAKERS = {
    "bet365", "william hill", "ladbrokes", "coral", "paddy power",
    "betfair sportsbook", "sky bet", "betfred", "unibet", "betway",
    "boylesports", "boyle sports", "888sport", "betvictor", "bet victor",
    "sportingbet", "10bet", "spreadex", "hills", "stan james",
}

IRISH_BOOKMAKERS = {
    "boylesports", "boyle sports", "paddy power", "betdaq",
    "mcgreevys", "done deal bets", "tote ireland",
}

EXCHANGE = {"betfair exchange", "betdaq exchange", "matchbook"}

# Steam confirmed if this many major bookmakers shorten simultaneously
MARKET_WIDE_STEAM_THRESHOLD = 3


def _load_state() -> dict:
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"snapshots": {}, "alerts_fired": []}


def _save_state(state: dict):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        print(f"[MarketMonitor] Could not save state: {e}")


def _runner_key(course: str, race_time: str, horse: str) -> str:
    return f"{date.today().isoformat()}::{race_time}::{course}::{horse.lower().strip()}"


class MultiSourceMarketMonitor:
    """
    Full multi-bookmaker market move monitor.
    Each poll cycle fetches odds from all three sources, compares to
    the previous snapshot, and fires alerts on significant movements.
    """

    def __init__(self):
        self.threshold = MARKET_MOVE_THRESHOLD   # Default 0.20 (20% move)

    def run_poll(self, races: list = None) -> list:
        """
        Main poll cycle.
        races: list of {course, time, runners} dicts from live_data
        Returns list of alert dicts for any significant moves found.
        """
        state  = _load_state()
        alerts = []

        # If no races passed in, pull from live data
        if not races:
            races = self._get_todays_races()

        if not races:
            print(f"[MarketMonitor] No races to monitor ({datetime.now().strftime('%H:%M')})")
            return []

        for race in races:
            course    = race.get("course", "")
            race_time = race.get("time", "")
            runners   = race.get("runners", [])
            stage     = race.get("stage", "")

            # Skip races already off
            if stage in ("WEIGHEDIN", "RESULT", "OFF"):
                continue

            try:
                # Fetch all odds from all sources
                all_odds = get_all_odds(course, race_time, runners)
            except Exception as e:
                print(f"[MarketMonitor] Odds fetch failed for {race_time} {course}: {e}")
                continue

            for horse_name, odds_data in all_odds.items():
                key = _runner_key(course, race_time, horse_name)
                prev_snapshot = state["snapshots"].get(key, {})

                new_alerts = self._analyse_movement(
                    key        = key,
                    course     = course,
                    race_time  = race_time,
                    horse      = horse_name,
                    odds_data  = odds_data,
                    prev       = prev_snapshot,
                    fired_keys = set(state["alerts_fired"]),
                )
                alerts.extend(new_alerts)

                # Update snapshot
                state["snapshots"][key] = {
                    "bookmaker_odds":  odds_data.get("bookmaker_odds", {}),
                    "betfair_back":    odds_data.get("betfair_back"),
                    "betfair_matched": odds_data.get("betfair_matched"),
                    "captured_at":     datetime.now().isoformat(),
                }

        # Record fired alert keys to avoid duplicates
        for a in alerts:
            akey = a.get("alert_key", "")
            if akey and akey not in state["alerts_fired"]:
                state["alerts_fired"].append(akey)

        # Trim state to avoid unbounded growth
        if len(state["alerts_fired"]) > 2000:
            state["alerts_fired"] = state["alerts_fired"][-1000:]

        _save_state(state)

        if alerts:
            print(f"[MarketMonitor] {len(alerts)} alert(s) at {datetime.now().strftime('%H:%M:%S')}")
            for a in alerts:
                print(f"  [{a['level']}] {a['message']}")
        else:
            print(f"[MarketMonitor] Poll complete — no moves ({datetime.now().strftime('%H:%M:%S')})")

        return alerts

    def _analyse_movement(self, key: str, course: str, race_time: str,
                          horse: str, odds_data: dict, prev: dict,
                          fired_keys: set) -> list:
        """
        Compares current odds to previous snapshot for a single runner.
        Returns list of alerts for any significant moves.
        """
        alerts = []
        bm_odds  = odds_data.get("bookmaker_odds", {})
        prev_bm  = prev.get("bookmaker_odds", {})
        bf_back  = odds_data.get("betfair_back")
        prev_bf  = prev.get("betfair_back")
        bf_vol   = odds_data.get("betfair_matched", 0) or 0
        prev_vol = prev.get("betfair_matched", 0) or 0
        race_label = f"{race_time} {course}"

        # ── 1. Market-wide steam check ────────────────────────
        # Count how many major bookmakers have shortened significantly
        bookmakers_steaming = []
        bookmakers_drifting = []

        for bk, current_dec in bm_odds.items():
            prev_dec = prev_bm.get(bk)
            if not prev_dec or not current_dec or prev_dec <= 0:
                continue
            move_pct = (prev_dec - current_dec) / prev_dec   # +ve = shortened

            if move_pct >= self.threshold:
                bookmakers_steaming.append((bk, prev_dec, current_dec, move_pct))
            elif move_pct <= -self.threshold:
                bookmakers_drifting.append((bk, prev_dec, current_dec, abs(move_pct)))

        # Market-wide steam — multiple bookmakers shortening together
        if len(bookmakers_steaming) >= MARKET_WIDE_STEAM_THRESHOLD:
            alert_key = f"mw_steam::{key}"
            if alert_key not in fired_keys:
                avg_move = sum(m[3] for m in bookmakers_steaming) / len(bookmakers_steaming)
                bk_list  = ", ".join(m[0] for m in bookmakers_steaming[:5])
                alerts.append(self._build_alert(
                    level     = "HIGH",
                    alert_type = "market_wide_steam",
                    message   = (
                        f"MARKET-WIDE STEAM: {horse.title()} ({race_label}) — "
                        f"{len(bookmakers_steaming)} bookmakers shortening simultaneously "
                        f"(avg {avg_move*100:.0f}% move). "
                        f"Bookmakers: {bk_list}"
                    ),
                    race      = race_label,
                    horse     = horse,
                    alert_key = alert_key,
                ))

        # Single major bookmaker steam
        for bk, prev_dec, curr_dec, move_pct in bookmakers_steaming:
            if bk.lower() in MAJOR_UK_BOOKMAKERS:
                alert_key = f"bk_steam::{key}::{bk}"
                if alert_key not in fired_keys:
                    alerts.append(self._build_alert(
                        level     = "MEDIUM",
                        alert_type = "bookmaker_steam",
                        message   = (
                            f"STEAM [{bk}]: {horse.title()} ({race_label}) — "
                            f"{_dec_to_frac(prev_dec)} → {_dec_to_frac(curr_dec)} "
                            f"({move_pct*100:.0f}% move)"
                        ),
                        race      = race_label,
                        horse     = horse,
                        alert_key = alert_key,
                    ))

        # Market-wide drift
        if len(bookmakers_drifting) >= MARKET_WIDE_STEAM_THRESHOLD:
            alert_key = f"mw_drift::{key}"
            if alert_key not in fired_keys:
                bk_list = ", ".join(m[0] for m in bookmakers_drifting[:5])
                alerts.append(self._build_alert(
                    level     = "LOW",
                    alert_type = "market_wide_drift",
                    message   = (
                        f"DRIFT: {horse.title()} ({race_label}) — "
                        f"{len(bookmakers_drifting)} bookmakers drifting. "
                        f"Bookmakers: {bk_list}"
                    ),
                    race      = race_label,
                    horse     = horse,
                    alert_key = alert_key,
                ))

        # ── 2. Betfair exchange move ──────────────────────────
        if bf_back and prev_bf and prev_bf > 0:
            bf_move = (prev_bf - bf_back) / prev_bf   # +ve = shortened on exchange
            if bf_move >= self.threshold:
                alert_key = f"bf_steam::{key}::{round(bf_back, 2)}"
                if alert_key not in fired_keys:
                    level = "HIGH" if bf_move >= 0.30 else "MEDIUM"
                    alerts.append(self._build_alert(
                        level     = level,
                        alert_type = "exchange_steam",
                        message   = (
                            f"EXCHANGE STEAM: {horse.title()} ({race_label}) — "
                            f"Betfair {_dec_to_frac(prev_bf)} → {_dec_to_frac(bf_back)} "
                            f"({bf_move*100:.0f}% move)"
                        ),
                        race      = race_label,
                        horse     = horse,
                        alert_key = alert_key,
                    ))

        # ── 3. Betfair volume spike ───────────────────────────
        # Large sudden increase in matched volume = confidence
        if bf_vol and prev_vol > 0:
            vol_increase = (bf_vol - prev_vol) / prev_vol
            if vol_increase >= 1.0 and bf_vol > 5000:   # Volume doubled + >£5k matched
                alert_key = f"bf_vol::{key}::{int(bf_vol)}"
                if alert_key not in fired_keys:
                    alerts.append(self._build_alert(
                        level     = "MEDIUM",
                        alert_type = "exchange_volume",
                        message   = (
                            f"EXCHANGE MONEY: {horse.title()} ({race_label}) — "
                            f"Betfair matched volume up {vol_increase*100:.0f}% "
                            f"(£{bf_vol:,.0f} total matched)"
                        ),
                        race      = race_label,
                        horse     = horse,
                        alert_key = alert_key,
                    ))

        # ── 4. Best price available tracker ──────────────────
        best_price  = odds_data.get("best_price")
        best_bookie = odds_data.get("best_bookie", "")
        if best_price and prev_bm:
            prev_best = max(prev_bm.values()) if prev_bm else None
            if prev_best and best_price > prev_best * 1.10:   # 10% BOG improvement
                alert_key = f"bog::{key}::{round(best_price, 2)}"
                if alert_key not in fired_keys:
                    alerts.append(self._build_alert(
                        level     = "LOW",
                        alert_type = "best_price_improved",
                        message   = (
                            f"PRICE UP: {horse.title()} ({race_label}) — "
                            f"Best available now {_dec_to_frac(best_price)} "
                            f"at {best_bookie} (was {_dec_to_frac(prev_best)})"
                        ),
                        race      = race_label,
                        horse     = horse,
                        alert_key = alert_key,
                    ))

        return alerts

    def _build_alert(self, level: str, alert_type: str, message: str,
                     race: str, horse: str, alert_key: str) -> dict:
        return {
            "level":      level,
            "type":       alert_type,
            "message":    message,
            "race":       race,
            "horse":      horse,
            "alert_key":  alert_key,
            "fired_at":   datetime.now().strftime("%H:%M:%S"),
            "timestamp":  datetime.now().isoformat(),
        }

    def _get_todays_races(self) -> list:
        """Pull today's races from the live data feed."""
        try:
            from dashboard.live_data import get_todays_meetings, get_race_runners
            meetings = get_todays_meetings()
            races = []
            for m in meetings:
                course = m.get("course", "")
                going  = m.get("going", "")
                for r in m.get("races", []):
                    try:
                        runners = get_race_runners(r.get("slug", ""))
                    except Exception:
                        runners = []
                    races.append({
                        "course":  course,
                        "time":    r.get("time", ""),
                        "stage":   r.get("stage", ""),
                        "runners": runners,
                    })
            return races
        except Exception as e:
            print(f"[MarketMonitor] Could not load races: {e}")
            return []

    def reset_state(self):
        """Clear all snapshots — call at start of each racing day."""
        _save_state({"snapshots": {}, "alerts_fired": []})
        print("[MarketMonitor] State reset for new day")

    def get_current_odds_summary(self, course: str, race_time: str,
                                  runners: list = None) -> list:
        """
        Returns a clean odds comparison table for a single race —
        used by the dashboard Odds Comparison tab.
        """
        all_odds = get_all_odds(course, race_time, runners)
        summary  = []

        for horse, data in all_odds.items():
            bm = data.get("bookmaker_odds", {})
            summary.append({
                "horse":          horse.title(),
                "best_price":     _dec_to_frac(data.get("best_price")),
                "best_bookie":    data.get("best_bookie", ""),
                "betfair_back":   _dec_to_frac(data.get("betfair_back")),
                "betfair_lay":    _dec_to_frac(data.get("betfair_lay")),
                "betfair_vol":    f"£{data.get('betfair_matched', 0):,.0f}",
                "bet365":         _dec_to_frac(bm.get("Bet365") or bm.get("bet365")),
                "william_hill":   _dec_to_frac(bm.get("William Hill") or bm.get("william hill")),
                "ladbrokes":      _dec_to_frac(bm.get("Ladbrokes") or bm.get("ladbrokes")),
                "paddy_power":    _dec_to_frac(bm.get("Paddy Power") or bm.get("paddy power")),
                "coral":          _dec_to_frac(bm.get("Coral") or bm.get("coral")),
                "sky_bet":        _dec_to_frac(bm.get("Sky Bet") or bm.get("sky bet")),
                "sources":        ", ".join(data.get("sources", [])),
            })

        return sorted(summary, key=lambda x: x.get("best_price") or "99/1")


# ── Utilities ─────────────────────────────────────────────────
def _dec_to_frac(dec) -> str:
    """Convert decimal price to a clean fractional string for display."""
    if not dec:
        return "—"
    try:
        d = float(dec)
        if d <= 1:
            return "—"
        frac = d - 1
        # Common fractions
        common = {
            0.25: "1/4", 0.333: "1/3", 0.5: "1/2", 0.667: "2/3",
            1.0: "EVS", 1.25: "5/4", 1.5: "6/4", 1.75: "7/4",
            2.0: "2/1", 2.5: "5/2", 3.0: "3/1", 3.5: "7/2",
            4.0: "4/1", 5.0: "5/1", 6.0: "6/1", 7.0: "7/1",
            8.0: "8/1", 9.0: "9/1", 10.0: "10/1", 12.0: "12/1",
            14.0: "14/1", 16.0: "16/1", 20.0: "20/1", 25.0: "25/1",
            33.0: "33/1", 50.0: "50/1",
        }
        for val, label in common.items():
            if abs(frac - val) < 0.04:
                return label
        # Default: show as N/1
        return f"{frac:.1f}/1"
    except Exception:
        return str(dec)
