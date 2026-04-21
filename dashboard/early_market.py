# Racing Engine — Early Market Monitor
# Purpose: Track next-day market moves from morning declarations onwards.
# The "smart money" often backs big-priced horses early before prices shorten.
# This module snapshots early morning odds, then compares hourly to detect
# significant moves BEFORE they become obvious on-the-day shorteners.
#
# Workflow:
#   1. ~10:00 BST: declarations published — take OPENING SNAPSHOT
#   2. ~11:00, 12:00: compare vs snapshot — flag horses shortening > 20%
#   3. ~13:00 onwards: cross-reference with next-day selections engine
#
# "Drifters" going the other way are also flagged — avoid them.

import os, json, datetime, zoneinfo
import requests, re

_LONDON = zoneinfo.ZoneInfo("Europe/London")
_SNAPSHOT_FILE = os.path.join(os.path.dirname(__file__), "..", "learning", "early_market_snapshot.json")

def _now_bst() -> str:
    return datetime.datetime.now(_LONDON).strftime("%H:%M")

def _today_bst() -> str:
    return datetime.datetime.now(_LONDON).strftime("%Y-%m-%d")

def _to_decimal(odds_str) -> float:
    try:
        s = str(odds_str).strip()
        if "/" in s:
            n, d = s.split("/")
            return round((float(n) + float(d)) / float(d), 3)
        return round(float(s), 3)
    except Exception:
        return 0.0

def _get_page_json(url: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                      r.text, re.DOTALL)
        if m:
            return json.loads(m.group(1))
    except Exception:
        pass
    return {}

def _load_snapshot() -> dict:
    try:
        if os.path.exists(_SNAPSHOT_FILE):
            with open(_SNAPSHOT_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_snapshot(snap: dict):
    os.makedirs(os.path.dirname(_SNAPSHOT_FILE), exist_ok=True)
    with open(_SNAPSHOT_FILE, "w") as f:
        json.dump(snap, f, indent=2)

def get_next_day_card(target_date: str = None) -> list:
    """
    Fetch next day's race card from Sporting Life.
    Returns list of races with runners and opening odds.
    target_date: YYYY-MM-DD, defaults to tomorrow.
    """
    if not target_date:
        target_date = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    url = f"https://www.sportinglife.com/racing/racecards/{target_date}"
    data = _get_page_json(url)
    meetings = data.get("props", {}).get("pageProps", {}).get("meetings", [])

    UK_COUNTRIES = {"ENG", "SCO", "WAL", "IRL", "IRE"}
    races_out = []

    for mt in meetings:
        ms   = mt.get("meeting_summary", {})
        date = ms.get("date", "")
        if date != target_date:
            continue

        course  = ms.get("course", {}).get("name", "")
        country = ms.get("course", {}).get("country", {}).get("short_name", "")
        if country not in UK_COUNTRIES:
            continue

        going = ms.get("going", "TBC")

        for rc in mt.get("races", []):
            slug = rc.get("url", "") or rc.get("slug", "")
            # Build slug from race_summary_reference if needed
            if not slug:
                rc_id = rc.get("race_summary_reference", {}).get("id", "")
                rc_name = rc.get("name", "")
                if rc_id:
                    slug = f"/racing/racecards/{target_date}/{course.lower().replace(' ','-')}/{rc_id}"

            time_str = rc.get("time", "")
            name     = rc.get("name", "")
            is_hcap  = rc.get("has_handicap", False) or "handicap" in name.lower() or "hcap" in name.lower()
            runners  = []

            # Fetch runner list if slug available
            if slug:
                full_url = f"https://www.sportinglife.com{slug}" if slug.startswith("/") else slug
                rdata = _get_page_json(full_url)
                race_detail = rdata.get("props",{}).get("pageProps",{}).get("race",{})
                rides = race_detail.get("rides", [])
                for ride in rides:
                    if ride.get("ride_status","") == "NON_RUNNER":
                        continue
                    horse   = ride.get("horse",{}).get("name","Unknown")
                    betting = ride.get("betting",{})
                    curr_odds = betting.get("current_odds","N/A")
                    tf_stars  = ride.get("timeform_stars","-")
                    form      = ride.get("horse",{}).get("formsummary",{}).get("display_text","-") or "-"
                    trainer   = ride.get("trainer",{}).get("name","-")
                    jockey    = ride.get("jockey",{}).get("name","-")
                    dec       = _to_decimal(curr_odds)
                    runners.append({
                        "horse":     horse,
                        "odds":      curr_odds,
                        "decimal":   dec,
                        "tf_stars":  tf_stars,
                        "form":      form,
                        "trainer":   trainer,
                        "jockey":    jockey,
                    })

            races_out.append({
                "date":       target_date,
                "course":     course,
                "time":       time_str,
                "name":       name,
                "going":      going,
                "is_handicap":is_hcap,
                "field_size": len(runners),
                "runners":    runners,
            })

    return races_out


def take_opening_snapshot(target_date: str = None) -> dict:
    """
    Take the opening market snapshot for tomorrow.
    Call this once when declarations first appear (~10:00 BST).
    Returns snapshot dict and saves to disk.
    """
    if not target_date:
        target_date = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    races = get_next_day_card(target_date)
    snap = {
        "date":      target_date,
        "taken_at":  _now_bst(),
        "horses":    {},
    }

    for race in races:
        for rn in race["runners"]:
            key = f"{target_date}::{race['time']}::{race['course']}::{rn['horse'].lower().strip()}"
            snap["horses"][key] = {
                "horse":   rn["horse"],
                "course":  race["course"],
                "time":    race["time"],
                "opening_odds": rn["odds"],
                "opening_dec":  rn["decimal"],
                "tf_stars":     rn["tf_stars"],
                "form":         rn["form"],
                "is_handicap":  race["is_handicap"],
            }

    _save_snapshot(snap)
    print(f"Snapshot taken at {snap['taken_at']} BST — {len(snap['horses'])} runners logged for {target_date}")
    return snap


def get_market_movers(target_date: str = None, min_move_pct: float = 0.15) -> list:
    """
    Compare current odds vs opening snapshot.
    Returns horses that have shortened >= min_move_pct (default 15%).
    Also flags big drifters (>20% out).
    """
    if not target_date:
        target_date = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    snap = _load_snapshot()
    if not snap or snap.get("date") != target_date:
        return [{"error": "No snapshot for this date — take opening snapshot first after 10:00 BST"}]

    races = get_next_day_card(target_date)

    movers = []
    for race in races:
        for rn in race["runners"]:
            key = f"{target_date}::{race['time']}::{race['course']}::{rn['horse'].lower().strip()}"
            baseline = snap["horses"].get(key)
            if not baseline:
                continue

            open_dec  = baseline.get("opening_dec", 0)
            curr_dec  = rn["decimal"]
            if open_dec <= 0 or curr_dec <= 0:
                continue

            move_pct = (open_dec - curr_dec) / open_dec  # positive = shortened (steamed)

            if abs(move_pct) >= min_move_pct:
                direction = "STEAM" if move_pct > 0 else "DRIFT"
                movers.append({
                    "horse":        rn["horse"],
                    "course":       race["course"],
                    "time":         race["time"],
                    "opening_odds": baseline["opening_odds"],
                    "current_odds": rn["odds"],
                    "opening_dec":  open_dec,
                    "current_dec":  curr_dec,
                    "move_pct":     round(abs(move_pct) * 100, 1),
                    "direction":    direction,
                    "tf_stars":     baseline["tf_stars"],
                    "form":         baseline["form"],
                    "is_handicap":  race["is_handicap"],
                    "snapshot_time": snap.get("taken_at","?"),
                })

    # Sort: steamers first by move %, then drifters
    steamers = sorted([m for m in movers if m["direction"] == "STEAM"],
                      key=lambda x: x["move_pct"], reverse=True)
    drifters = sorted([m for m in movers if m["direction"] == "DRIFT"],
                      key=lambda x: x["move_pct"], reverse=True)

    return steamers + drifters


def print_movers_report(target_date: str = None):
    """Print a readable market movers report to console / for logging."""
    movers = get_market_movers(target_date)
    if not movers:
        print("No significant market moves detected yet.")
        return

    if movers and "error" in movers[0]:
        print(movers[0]["error"])
        return

    now = _now_bst()
    print(f"\n{'='*65}")
    print(f"EARLY MARKET MOVERS — {target_date or 'Tomorrow'}  (checked {now} BST)")
    print(f"{'='*65}")

    steamers = [m for m in movers if m["direction"] == "STEAM"]
    drifters = [m for m in movers if m["direction"] == "DRIFT"]

    if steamers:
        print(f"\n  SHORTENERS (market backing these):")
        print(f"  {'Horse':<22} {'Course':<14} {'Time':<6} {'Open':<8} {'Now':<8} {'Move':>6}  TF")
        print(f"  {'-'*75}")
        for m in steamers:
            print(f"  {m['horse']:<22} {m['course']:<14} {m['time']:<6} "
                  f"{m['opening_odds']:<8} {m['current_odds']:<8} "
                  f"{'+'+str(m['move_pct'])+'%':>6}  {m['tf_stars']}")

    if drifters:
        print(f"\n  DRIFTERS (market cooling on these):")
        print(f"  {'Horse':<22} {'Course':<14} {'Time':<6} {'Open':<8} {'Now':<8} {'Move':>6}  TF")
        print(f"  {'-'*75}")
        for m in drifters:
            print(f"  {m['horse']:<22} {m['course']:<14} {m['time']:<6} "
                  f"{m['opening_odds']:<8} {m['current_odds']:<8} "
                  f"{'-'+str(m['move_pct'])+'%':>6}  {m['tf_stars']}")

    print()
