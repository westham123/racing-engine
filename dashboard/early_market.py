# Racing Engine — Early Market Monitor
# Purpose: Track market moves from opening show prices onwards.
#
# Real-world timing:
#   - Horses leave stables 05:00–06:00 BST. By 08:00 BST the card is settled.
#   - First meaningful odds appear 07:30–08:30 BST on next-day races.
#   - 10:00 BST is merely the administrative declarations deadline — not the
#     practical start of market activity.
#
# Two snapshot types:
#   OPENING  — taken at 08:00 BST (first tradeable prices of the day)
#   SHOW     — taken afternoon/evening prior day when bookmakers release early
#              show prices (typically 16:00–19:00 BST the day before)
#
# Workflow:
#   Prior afternoon/evening: take_show_snapshot() — captures early show prices
#   08:00 BST next morning:  take_opening_snapshot() — captures firm morning prices
#   Hourly checks:           get_market_movers() — flags >=15% moves vs opening
#
# "Steamers" shortening = money coming in = follow signal
# "Drifters" lengthening = market cooling = avoid signal

import os, json, datetime, zoneinfo
import requests, re
from datetime import date, timezone

_LONDON        = zoneinfo.ZoneInfo("Europe/London")


def _utc_to_bst(utc_time_str: str) -> str:
    """Convert HH:MM UTC string to HH:MM BST (Europe/London)."""
    if not utc_time_str:
        return utc_time_str
    try:
        _today = date.today()
        _h, _m = map(int, str(utc_time_str).strip().split(":"))
        _utc_dt = datetime.datetime(_today.year, _today.month, _today.day, _h, _m,
                                    tzinfo=timezone.utc)
        return _utc_dt.astimezone(_LONDON).strftime("%H:%M")
    except Exception:
        return utc_time_str


_SNAPSHOT_FILE = os.path.join(os.path.dirname(__file__), "..", "learning", "early_market_snapshot.json")
_SHOW_FILE     = os.path.join(os.path.dirname(__file__), "..", "learning", "show_price_snapshot.json")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_bst() -> str:
    return datetime.datetime.now(_LONDON).strftime("%H:%M")

def _today_bst() -> str:
    return datetime.datetime.now(_LONDON).strftime("%Y-%m-%d")

def _tomorrow_bst() -> str:
    return (datetime.datetime.now(_LONDON).date() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

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

def _load_json(path: str) -> dict:
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ── Card Fetcher ──────────────────────────────────────────────────────────────

def get_next_day_card(target_date: str = None) -> list:
    """
    Fetch race card from Sporting Life for target_date (default: tomorrow).
    Uses the same slug-building logic as live_data.py to ensure runners populate.
    Returns list of races with runners and current odds.
    """
    import re as _re
    if not target_date:
        target_date = _tomorrow_bst()

    url  = f"https://www.sportinglife.com/racing/racecards/{target_date}"
    data = _get_page_json(url)
    meetings = data.get("props", {}).get("pageProps", {}).get("meetings", [])

    UK_COUNTRIES = {"ENG", "SCO", "Scot", "Scotland", "WAL", "Wale", "Wales",
                   "IRL", "IRE", "IE", "Ire", "Eire", "GB", "UK", "Northern Ireland", "NI"}
    races_out = []

    def _make_slug(course: str, rc_id: str, rc_name: str) -> str:
        """Matches live_data.py slug builder exactly."""
        course_slug = _re.sub(r"[^a-z0-9]+", "-", course.lower()).strip("-")
        name_slug   = _re.sub(r"[^a-z0-9]+", "-", rc_name.lower()).strip("-")
        return f"/racing/racecards/{target_date}/{course_slug}/racecard/{rc_id}/{name_slug}"

    for mt in meetings:
        ms      = mt.get("meeting_summary", {})
        date    = ms.get("date", "")
        if date != target_date:
            continue
        course  = ms.get("course", {}).get("name", "")
        country = ms.get("course", {}).get("country", {}).get("short_name", "")
        if country not in UK_COUNTRIES:
            continue
        going   = ms.get("going", "TBC")

        for rc in mt.get("races", []):
            rc_id    = str(rc.get("race_summary_reference", {}).get("id", ""))
            rc_name  = rc.get("name", "")
            time_str = _utc_to_bst(rc.get("time", ""))   # convert UTC → BST
            is_hcap  = any(x in rc_name.lower() for x in ["handicap", "hcap", "h'cap"])

            slug = _make_slug(course, rc_id, rc_name) if rc_id and rc_name else None
            runners = []

            if slug:
                full_url    = f"https://www.sportinglife.com{slug}"
                rdata       = _get_page_json(full_url)
                race_detail = rdata.get("props", {}).get("pageProps", {}).get("race", {}) if rdata else {}
                for ride in race_detail.get("rides", []):
                    if ride.get("ride_status", "") == "NON_RUNNER":
                        continue
                    horse     = ride.get("horse", {}).get("name", "Unknown")
                    betting   = ride.get("betting", {})
                    curr_odds = betting.get("current_odds", "N/A")
                    tf_stars  = ride.get("timeform_stars", "-")
                    form      = ride.get("horse", {}).get("formsummary", {}).get("display_text", "-") or "-"
                    trainer   = ride.get("trainer", {}).get("name", "-")
                    jockey    = ride.get("jockey", {}).get("name", "-")
                    runners.append({
                        "horse":   horse,
                        "odds":    curr_odds,
                        "decimal": _to_decimal(curr_odds),
                        "tf_stars": tf_stars,
                        "form":    form,
                        "trainer": trainer,
                        "jockey":  jockey,
                    })

            races_out.append({
                "date":        target_date,
                "course":      course,
                "time":        time_str,
                "name":        rc_name,
                "going":       going,
                "is_handicap": is_hcap,
                "field_size":  len(runners),
                "runners":     runners,
            })

    return races_out


# ── Snapshot Functions ────────────────────────────────────────────────────────

def _build_snapshot(target_date: str, label: str, save_path: str) -> dict:
    """Internal: fetch card and save snapshot to disk."""
    races = get_next_day_card(target_date)
    snap  = {
        "date":     target_date,
        "label":    label,
        "taken_at": _now_bst(),
        "horses":   {},
    }
    for race in races:
        for rn in race["runners"]:
            key = f"{target_date}::{race['time']}::{race['course']}::{rn['horse'].lower().strip()}"
            snap["horses"][key] = {
                "horse":       rn["horse"],
                "course":      race["course"],
                "time":        race["time"],
                "odds":        rn["odds"],
                "decimal":     rn["decimal"],
                "tf_stars":    rn["tf_stars"],
                "form":        rn["form"],
                "is_handicap": race["is_handicap"],
            }
    _save_json(save_path, snap)
    print(f"{label} snapshot taken at {snap['taken_at']} BST — {len(snap['horses'])} runners for {target_date}")
    return snap


def take_show_snapshot(target_date: str = None) -> dict:
    """
    Capture afternoon/evening show prices (16:00–19:00 BST, day before racing).
    These are the bookmakers' first published prices — often before smart money moves.
    Call this once in the afternoon/evening prior to race day.
    """
    if not target_date:
        target_date = _tomorrow_bst()
    return _build_snapshot(target_date, "SHOW", _SHOW_FILE)


def take_opening_snapshot(target_date: str = None) -> dict:
    """
    Capture firm morning prices at 08:00 BST on race day.
    By this time horses have left stables and the card is settled.
    This is the primary baseline for detecting intraday moves.
    Call this once at ~08:00 BST.
    """
    if not target_date:
        target_date = _tomorrow_bst()
    return _build_snapshot(target_date, "OPENING", _SNAPSHOT_FILE)


# ── Market Movers ─────────────────────────────────────────────────────────────

def get_market_movers(target_date: str = None, min_move_pct: float = 0.15,
                      vs: str = "opening") -> list:
    """
    Compare current odds vs baseline snapshot.
    vs: "opening" (08:00 BST snapshot) or "show" (prior-day show prices).
    Returns horses that have moved >= min_move_pct (default 15%).
    Steamers (shortened) = STEAM, Drifters (lengthened) = DRIFT.
    """
    if not target_date:
        target_date = _tomorrow_bst()

    snap_path = _SHOW_FILE if vs == "show" else _SNAPSHOT_FILE
    snap      = _load_json(snap_path)

    if not snap or snap.get("date") != target_date:
        baseline_label = "show price" if vs == "show" else "opening"
        return [{"error": f"No {baseline_label} snapshot for {target_date} — take snapshot first."}]

    races  = get_next_day_card(target_date)
    movers = []

    for race in races:
        for rn in race["runners"]:
            key      = f"{target_date}::{race['time']}::{race['course']}::{rn['horse'].lower().strip()}"
            baseline = snap["horses"].get(key)
            if not baseline:
                continue
            open_dec = baseline.get("decimal", 0)
            curr_dec = rn["decimal"]
            if open_dec <= 0 or curr_dec <= 0:
                continue

            move_pct  = (open_dec - curr_dec) / open_dec   # positive = shortened
            if abs(move_pct) >= min_move_pct:
                direction = "STEAM" if move_pct > 0 else "DRIFT"
                movers.append({
                    "horse":          rn["horse"],
                    "course":         race["course"],
                    "time":           race["time"],
                    "baseline_odds":  baseline["odds"],
                    "baseline_dec":   open_dec,
                    "current_odds":   rn["odds"],
                    "current_dec":    curr_dec,
                    "move_pct":       round(abs(move_pct) * 100, 1),
                    "direction":      direction,
                    "tf_stars":       baseline["tf_stars"],
                    "form":           baseline["form"],
                    "is_handicap":    race["is_handicap"],
                    "snapshot_label": snap.get("label","?"),
                    "snapshot_time":  snap.get("taken_at","?"),
                })

    steamers = sorted([m for m in movers if m["direction"] == "STEAM"],
                      key=lambda x: x["move_pct"], reverse=True)
    drifters = sorted([m for m in movers if m["direction"] == "DRIFT"],
                      key=lambda x: x["move_pct"], reverse=True)
    return steamers + drifters


def get_show_vs_morning_moves(target_date: str = None, min_move_pct: float = 0.10) -> list:
    """
    Compare afternoon/evening SHOW prices vs 08:00 BST OPENING prices.
    Reveals how much the market moved overnight — key intelligence for
    identifying horses backed before the morning rush.
    min_move_pct default 10% (tighter threshold — overnight moves are significant).
    """
    if not target_date:
        target_date = _tomorrow_bst()

    show_snap    = _load_json(_SHOW_FILE)
    opening_snap = _load_json(_SNAPSHOT_FILE)

    if not show_snap or show_snap.get("date") != target_date:
        return [{"error": f"No show price snapshot for {target_date}."}]
    if not opening_snap or opening_snap.get("date") != target_date:
        return [{"error": f"No opening snapshot for {target_date}."}]

    movers = []
    for key, show_data in show_snap["horses"].items():
        opening_data = opening_snap["horses"].get(key)
        if not opening_data:
            continue
        show_dec    = show_data.get("decimal", 0)
        opening_dec = opening_data.get("decimal", 0)
        if show_dec <= 0 or opening_dec <= 0:
            continue

        move_pct  = (show_dec - opening_dec) / show_dec   # positive = shortened overnight
        if abs(move_pct) >= min_move_pct:
            direction = "STEAM" if move_pct > 0 else "DRIFT"
            movers.append({
                "horse":        show_data["horse"],
                "course":       show_data["course"],
                "time":         show_data["time"],
                "show_odds":    show_data["odds"],
                "show_dec":     show_dec,
                "morning_odds": opening_data["odds"],
                "morning_dec":  opening_dec,
                "move_pct":     round(abs(move_pct) * 100, 1),
                "direction":    direction,
                "tf_stars":     show_data["tf_stars"],
                "form":         show_data["form"],
                "is_handicap":  show_data["is_handicap"],
                "note":         "Backed overnight before morning open" if direction == "STEAM"
                                else "Drifted overnight — market cooling",
            })

    steamers = sorted([m for m in movers if m["direction"] == "STEAM"],
                      key=lambda x: x["move_pct"], reverse=True)
    drifters = sorted([m for m in movers if m["direction"] == "DRIFT"],
                      key=lambda x: x["move_pct"], reverse=True)
    return steamers + drifters


# ── Console Reports ───────────────────────────────────────────────────────────

def print_movers_report(target_date: str = None, vs: str = "opening"):
    """Print a readable market movers report to console."""
    movers = get_market_movers(target_date, vs=vs)
    if not movers:
        print("No significant market moves detected yet.")
        return
    if "error" in movers[0]:
        print(movers[0]["error"])
        return

    label    = "SHOW PRICES" if vs == "show" else "08:00 BST OPENING"
    now      = _now_bst()
    date_str = target_date or _tomorrow_bst()

    print(f"\n{'='*70}")
    print(f"MARKET MOVERS vs {label} — {date_str}  (checked {now} BST)")
    print(f"{'='*70}")

    steamers = [m for m in movers if m["direction"] == "STEAM"]
    drifters = [m for m in movers if m["direction"] == "DRIFT"]

    if steamers:
        print(f"\n  SHORTENERS — money coming in:")
        print(f"  {'Horse':<22} {'Course':<14} {'Time':<6} {'Was':<8} {'Now':<8} {'Move':>6}  TF")
        print(f"  {'-'*72}")
        for m in steamers:
            print(f"  {m['horse']:<22} {m['course']:<14} {m['time']:<6} "
                  f"{m['baseline_odds']:<8} {m['current_odds']:<8} "
                  f"{'+'+str(m['move_pct'])+'%':>6}  {m['tf_stars']}")

    if drifters:
        print(f"\n  DRIFTERS — market cooling:")
        print(f"  {'Horse':<22} {'Course':<14} {'Time':<6} {'Was':<8} {'Now':<8} {'Move':>6}  TF")
        print(f"  {'-'*72}")
        for m in drifters:
            print(f"  {m['horse']:<22} {m['course']:<14} {m['time']:<6} "
                  f"{m['baseline_odds']:<8} {m['current_odds']:<8} "
                  f"{'-'+str(m['move_pct'])+'%':>6}  {m['tf_stars']}")
    print()


def print_show_vs_morning_report(target_date: str = None):
    """Print overnight show→morning move report."""
    movers = get_show_vs_morning_moves(target_date)
    if not movers:
        print("No significant overnight moves detected.")
        return
    if "error" in movers[0]:
        print(movers[0]["error"])
        return

    date_str = target_date or _tomorrow_bst()
    print(f"\n{'='*70}")
    print(f"OVERNIGHT MOVES (Show → 08:00 BST) — {date_str}")
    print(f"Key: these horses were backed BEFORE the morning market opened.")
    print(f"{'='*70}")

    steamers = [m for m in movers if m["direction"] == "STEAM"]
    drifters = [m for m in movers if m["direction"] == "DRIFT"]

    if steamers:
        print(f"\n  BACKED OVERNIGHT (show price → morning):")
        print(f"  {'Horse':<22} {'Course':<14} {'Time':<6} {'Show':<8} {'8am':<8} {'Move':>6}  Note")
        print(f"  {'-'*80}")
        for m in steamers:
            print(f"  {m['horse']:<22} {m['course']:<14} {m['time']:<6} "
                  f"{m['show_odds']:<8} {m['morning_odds']:<8} "
                  f"{'+'+str(m['move_pct'])+'%':>6}  {m['note']}")

    if drifters:
        print(f"\n  DRIFTED OVERNIGHT:")
        print(f"  {'Horse':<22} {'Course':<14} {'Time':<6} {'Show':<8} {'8am':<8} {'Move':>6}  Note")
        print(f"  {'-'*80}")
        for m in drifters:
            print(f"  {m['horse']:<22} {m['course']:<14} {m['time']:<6} "
                  f"{m['show_odds']:<8} {m['morning_odds']:<8} "
                  f"{'-'+str(m['move_pct'])+'%':>6}  {m['note']}")
    print()
