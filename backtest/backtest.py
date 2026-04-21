"""
backtest.py — Historical Backtest Engine v1.0

Strategy:
  - Fetch 28 days of UK/IRE results from Sporting Life results pages
  - For each race, identify the "engine tip" = shortest SP horse (market favourite proxy)
  - Simulate bets at level stakes £1 for: Singles, Doubles, Trebles, Lucky 15
  - Track hit rate, P&L, ROI across the full 28-day window
  - Generate a full text report

Data source: https://www.sportinglife.com/racing/results/{YYYY-MM-DD}
Actual structure discovered:
  pageProps → meetings[] → races[] → {course_name, country_short_name, time, top_horses[]}
  top_horses: [{name, position, odds, favourite}]
  UK/IRE country codes: 'ENG', 'SCO', 'WAL', 'Eire', 'NI'
"""

import requests
import json
import re
import itertools
import os
from datetime import date, timedelta
from collections import defaultdict

# UK + Ireland country codes as they appear in the Sporting Life data
UK_IRE_COUNTRIES = {"ENG", "SCO", "WAL", "Eire", "NI", "IRE"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.sportinglife.com/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ──────────────────────────────────────────────────────────────────────────────
# 1. DATA FETCHING
# ──────────────────────────────────────────────────────────────────────────────

def parse_sp(sp_str):
    """Convert SP string like '5/2', '11/4', 'evs', '11/10' → decimal float."""
    if not sp_str:
        return None
    s = str(sp_str).strip().lower()
    if s in ("evs", "evens", "1/1"):
        return 2.0
    m = re.match(r"(\d+)/(\d+)", s)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        return round((num / den) + 1, 4)
    try:
        v = float(s)
        # If it looks like a fractional already expressed as decimal (e.g. 1.5 → 1.5x)
        return v if v > 1 else None
    except ValueError:
        return None


def fetch_day_results(ds: str):
    """
    Fetch Sporting Life results for a given date string YYYY-MM-DD.
    Returns list of race dicts with keys: date, course, country, race_time, runners
      where each runner = {name, position, sp_str, sp_dec, favourite}
    Only UK/IRE races are included.
    """
    url = f"https://www.sportinglife.com/racing/results/{ds}"
    races_out = []
    try:
        r = SESSION.get(url, timeout=20)
        if r.status_code != 200:
            print(f"  [WARN] {ds}: HTTP {r.status_code}")
            return races_out

        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
        if not m:
            print(f"  [WARN] {ds}: no __NEXT_DATA__ found")
            return races_out

        data = json.loads(m.group(1))
        meetings = data["props"]["pageProps"].get("meetings", [])

        if not meetings:
            return races_out

        for meeting in meetings:
            for race in meeting.get("races", []):
                country = race.get("country_short_name", "")
                if country not in UK_IRE_COUNTRIES:
                    continue

                course_name = race.get("course_name", "Unknown")
                race_time = race.get("time", race.get("off_time", ""))
                top_horses = race.get("top_horses", [])

                runners = []
                for horse in top_horses:
                    name = horse.get("name", "")
                    pos_raw = horse.get("position", 99)
                    sp_raw = horse.get("odds", "")
                    fav = horse.get("favourite", False)

                    try:
                        pos = int(str(pos_raw).strip())
                    except (ValueError, TypeError):
                        pos = 99

                    runners.append({
                        "name": name,
                        "position": pos,
                        "sp_str": str(sp_raw),
                        "sp_dec": parse_sp(str(sp_raw)),
                        "favourite": bool(fav),
                    })

                # Need at least 2 runners with SP data for a meaningful race
                valid_runners = [r for r in runners if r["sp_dec"] is not None]
                if len(valid_runners) >= 2:
                    races_out.append({
                        "date": ds,
                        "course": course_name,
                        "country": country,
                        "race_time": race_time,
                        "runners": runners,
                    })

    except Exception as e:
        print(f"  [ERROR] {ds}: {e}")

    return races_out


# ──────────────────────────────────────────────────────────────────────────────
# 2. SELECTION ENGINE (SP-proxy)
# ──────────────────────────────────────────────────────────────────────────────

def pick_selection(race):
    """
    Identify the engine's tip for a race.
    Strategy: shortest SP = highest market confidence = engine selection.
    If there's a tie, take the one flagged as favourite; if still tied, first listed.
    Returns the chosen runner dict, or None if no valid SP data.
    """
    valid = [r for r in race["runners"] if r["sp_dec"] is not None and r["sp_dec"] > 0]
    if not valid:
        return None
    # Sort: shortest SP (lowest decimal) first; use favourite flag as tiebreaker
    valid.sort(key=lambda r: (r["sp_dec"], not r["favourite"]))
    return valid[0]


# ──────────────────────────────────────────────────────────────────────────────
# 3. BET CALCULATIONS
# ──────────────────────────────────────────────────────────────────────────────

STAKE = 1.0  # £1 level stakes

def single_pnl(sel):
    """Return (won: bool, pnl: float) for a single at £1 stake."""
    won = (sel["position"] == 1)
    if won:
        return True, round((sel["sp_dec"] - 1) * STAKE, 4)
    return False, -STAKE


def double_pnl(sel_a, sel_b):
    """£1 double — both must win."""
    if sel_a["position"] == 1 and sel_b["position"] == 1:
        returns = STAKE * sel_a["sp_dec"] * sel_b["sp_dec"]
        return True, round(returns - STAKE, 4)
    return False, -STAKE


def treble_pnl(sel_a, sel_b, sel_c):
    """£1 treble — all three must win."""
    if sel_a["position"] == 1 and sel_b["position"] == 1 and sel_c["position"] == 1:
        returns = STAKE * sel_a["sp_dec"] * sel_b["sp_dec"] * sel_c["sp_dec"]
        return True, round(returns - STAKE, 4)
    return False, -STAKE


def lucky15_pnl(sels):
    """
    Lucky 15 = 4 selections, 15 bets: 4 singles + 6 doubles + 4 trebles + 1 four-fold.
    Total stake: £15 (15 × £1).
    Returns total P&L net of total £15 outlay.
    """
    assert len(sels) == 4
    total_return = 0.0

    # 4 singles
    for s in sels:
        if s["position"] == 1:
            total_return += STAKE * s["sp_dec"]

    # 6 doubles
    for combo in itertools.combinations(sels, 2):
        if all(s["position"] == 1 for s in combo):
            r = STAKE
            for s in combo:
                r *= s["sp_dec"]
            total_return += r

    # 4 trebles
    for combo in itertools.combinations(sels, 3):
        if all(s["position"] == 1 for s in combo):
            r = STAKE
            for s in combo:
                r *= s["sp_dec"]
            total_return += r

    # 1 four-fold accumulator
    if all(s["position"] == 1 for s in sels):
        r = STAKE
        for s in sels:
            r *= s["sp_dec"]
        total_return += r

    pnl = round(total_return - 15 * STAKE, 4)
    return pnl


# ──────────────────────────────────────────────────────────────────────────────
# 4. SIMULATION ENGINE
# ──────────────────────────────────────────────────────────────────────────────

def run_backtest(days: int = 28):
    today = date.today()
    date_range = [
        (today - timedelta(days=d)).strftime("%Y-%m-%d")
        for d in range(1, days + 1)  # yesterday back 28 days
    ]

    all_races = []
    print(f"\n{'='*65}")
    print(f"  RACING ENGINE — 28-DAY HISTORICAL BACKTEST")
    print(f"  Fetching results: {date_range[-1]} → {date_range[0]}")
    print(f"{'='*65}")

    for ds in sorted(date_range):
        races = fetch_day_results(ds)
        print(f"  {ds}: {len(races)} UK/IRE races")
        all_races.extend(races)

    print(f"\n  Total races fetched: {len(all_races)}")

    # Build selections
    selections = []
    for race in all_races:
        sel = pick_selection(race)
        if sel:
            selections.append({"race": race, "sel": sel})

    print(f"  Selectable races (valid SP data): {len(selections)}")

    # ── SINGLES ───────────────────────────────────────────────────────────────
    single_results = []
    for item in selections:
        won, pnl = single_pnl(item["sel"])
        single_results.append({
            "race": item["race"],
            "sel": item["sel"],
            "won": won,
            "pnl": pnl,
        })

    single_wins = sum(1 for r in single_results if r["won"])
    single_total = len(single_results)
    single_pnl_total = round(sum(r["pnl"] for r in single_results), 2)
    single_roi = round(single_pnl_total / (single_total * STAKE) * 100, 1) if single_total else 0

    # ── DOUBLES ───────────────────────────────────────────────────────────────
    by_day = defaultdict(list)
    for item in selections:
        by_day[item["race"]["date"]].append(item["sel"])

    double_results = []
    for ds, day_sels in sorted(by_day.items()):
        if len(day_sels) < 2:
            continue
        for combo in itertools.combinations(day_sels, 2):
            won, pnl = double_pnl(*combo)
            double_results.append({"date": ds, "won": won, "pnl": pnl})

    dbl_wins = sum(1 for r in double_results if r["won"])
    dbl_total = len(double_results)
    dbl_pnl_total = round(sum(r["pnl"] for r in double_results), 2)
    dbl_roi = round(dbl_pnl_total / (dbl_total * STAKE) * 100, 1) if dbl_total else 0

    # ── TREBLES ───────────────────────────────────────────────────────────────
    treble_results = []
    for ds, day_sels in sorted(by_day.items()):
        if len(day_sels) < 3:
            continue
        for combo in itertools.combinations(day_sels, 3):
            won, pnl = treble_pnl(*combo)
            treble_results.append({"date": ds, "won": won, "pnl": pnl})

    tbl_wins = sum(1 for r in treble_results if r["won"])
    tbl_total = len(treble_results)
    tbl_pnl_total = round(sum(r["pnl"] for r in treble_results), 2)
    tbl_roi = round(tbl_pnl_total / (tbl_total * STAKE) * 100, 1) if tbl_total else 0

    # ── LUCKY 15 ──────────────────────────────────────────────────────────────
    lucky15_results = []
    for ds, day_sels in sorted(by_day.items()):
        if len(day_sels) < 4:
            continue
        # All combinations of 4 from the day's selections (cap at 8 to avoid explosion)
        for combo in itertools.combinations(day_sels[:8], 4):
            pnl = lucky15_pnl(list(combo))
            lucky15_results.append({"date": ds, "pnl": pnl})

    l15_positive = sum(1 for r in lucky15_results if r["pnl"] > 0)
    l15_total = len(lucky15_results)
    l15_pnl_total = round(sum(r["pnl"] for r in lucky15_results), 2)
    l15_roi = round(l15_pnl_total / (l15_total * 15 * STAKE) * 100, 1) if l15_total else 0

    # ── SUMMARY DICT ──────────────────────────────────────────────────────────
    results = {
        "date_from": date_range[-1],
        "date_to": date_range[0],
        "total_races": len(all_races),
        "selectable_races": len(selections),
        "singles": {
            "bets": single_total,
            "wins": single_wins,
            "hit_rate_pct": round(single_wins / single_total * 100, 1) if single_total else 0,
            "pnl": single_pnl_total,
            "roi_pct": single_roi,
        },
        "doubles": {
            "bets": dbl_total,
            "wins": dbl_wins,
            "hit_rate_pct": round(dbl_wins / dbl_total * 100, 1) if dbl_total else 0,
            "pnl": dbl_pnl_total,
            "roi_pct": dbl_roi,
        },
        "trebles": {
            "bets": tbl_total,
            "wins": tbl_wins,
            "hit_rate_pct": round(tbl_wins / tbl_total * 100, 1) if tbl_total else 0,
            "pnl": tbl_pnl_total,
            "roi_pct": tbl_roi,
        },
        "lucky_15": {
            "tickets": l15_total,
            "profitable_tickets": l15_positive,
            "pnl": l15_pnl_total,
            "roi_pct": l15_roi,
        },
        "daily_singles": {},
    }

    # Daily breakdown
    for r in single_results:
        ds = r["race"]["date"]
        if ds not in results["daily_singles"]:
            results["daily_singles"][ds] = []
        results["daily_singles"][ds].append({
            "course": r["race"]["course"],
            "time": r["race"]["race_time"],
            "horse": r["sel"]["name"],
            "sp": r["sel"]["sp_str"],
            "won": r["won"],
            "pnl": r["pnl"],
        })

    return results


# ──────────────────────────────────────────────────────────────────────────────
# 5. REPORT GENERATOR
# ──────────────────────────────────────────────────────────────────────────────

def generate_report(results: dict, out_path: str):
    lines = []
    lines.append("=" * 65)
    lines.append("  RACING ENGINE — 28-DAY HISTORICAL BACKTEST REPORT")
    lines.append(f"  Period: {results['date_from']} to {results['date_to']}")
    lines.append("=" * 65)
    lines.append("")
    lines.append("METHOD")
    lines.append("  Selection = shortest SP runner in each race (market favourite proxy)")
    lines.append("  Level stakes: £1 per bet")
    lines.append("  SP = actual Starting Price")
    lines.append("  UK + Ireland races only")
    lines.append("")
    lines.append(f"  Total UK/IRE races fetched : {results['total_races']}")
    lines.append(f"  Races with valid SP data   : {results['selectable_races']}")
    lines.append("")
    lines.append("-" * 65)
    lines.append("  SINGLES  (1 x £1 bet per race)")
    lines.append("-" * 65)
    s = results["singles"]
    lines.append(f"  Bets placed  : {s['bets']}")
    lines.append(f"  Winners      : {s['wins']}")
    lines.append(f"  Hit rate     : {s['hit_rate_pct']}%")
    lines.append(f"  Total P&L    : £{s['pnl']:+.2f}")
    lines.append(f"  ROI          : {s['roi_pct']:+.1f}%")
    lines.append("")
    lines.append("-" * 65)
    lines.append("  DOUBLES  (all same-day pairs, £1 each)")
    lines.append("-" * 65)
    d = results["doubles"]
    lines.append(f"  Bets placed  : {d['bets']}")
    lines.append(f"  Winners      : {d['wins']}")
    lines.append(f"  Hit rate     : {d['hit_rate_pct']}%")
    lines.append(f"  Total P&L    : £{d['pnl']:+.2f}")
    lines.append(f"  ROI          : {d['roi_pct']:+.1f}%")
    lines.append("")
    lines.append("-" * 65)
    lines.append("  TREBLES  (all same-day 3-way combos, £1 each)")
    lines.append("-" * 65)
    t = results["trebles"]
    lines.append(f"  Bets placed  : {t['bets']}")
    lines.append(f"  Winners      : {t['wins']}")
    lines.append(f"  Hit rate     : {t['hit_rate_pct']}%")
    lines.append(f"  Total P&L    : £{t['pnl']:+.2f}")
    lines.append(f"  ROI          : {t['roi_pct']:+.1f}%")
    lines.append("")
    lines.append("-" * 65)
    lines.append("  LUCKY 15  (all 4-selection combos, £15 ticket each)")
    lines.append("-" * 65)
    l = results["lucky_15"]
    lines.append(f"  Tickets placed    : {l['tickets']}")
    lines.append(f"  Profitable tickets: {l['profitable_tickets']}")
    lines.append(f"  Total P&L         : £{l['pnl']:+.2f}")
    lines.append(f"  ROI               : {l['roi_pct']:+.1f}%")
    lines.append("")
    lines.append("-" * 65)
    lines.append("  DAILY SINGLES BREAKDOWN")
    lines.append("-" * 65)

    for ds in sorted(results["daily_singles"].keys()):
        day_bets = results["daily_singles"][ds]
        if not day_bets:
            continue
        day_pnl = round(sum(b["pnl"] for b in day_bets), 2)
        day_wins = sum(1 for b in day_bets if b["won"])
        lines.append(f"\n  {ds}  |  {len(day_bets)} bets  |  {day_wins} wins  |  P&L £{day_pnl:+.2f}")
        for b in day_bets:
            flag = "WON " if b["won"] else "LOST"
            lines.append(
                f"    {b['time']:7s}  {b['course']:22s}  {b['horse']:28s}  "
                f"SP {b['sp']:>7s}  {flag}  £{b['pnl']:+.2f}"
            )

    lines.append("")
    lines.append("=" * 65)
    lines.append("  END OF REPORT")
    lines.append("=" * 65)

    report_text = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    return report_text


# ──────────────────────────────────────────────────────────────────────────────
# 6. MAIN
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    results = run_backtest(days=28)

    out_dir = os.path.dirname(os.path.abspath(__file__))
    report_path = os.path.join(out_dir, "backtest_report.txt")
    json_path = os.path.join(out_dir, "backtest_results.json")

    report_text = generate_report(results, report_path)
    print(report_text)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\nReport saved: {report_path}")
    print(f"JSON saved:   {json_path}")
