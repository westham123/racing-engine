# Racing Engine — Oddschecker Multi-Bookmaker Odds Fetcher
# Version: 2.5.40
# Fetches live odds across 24+ bookmakers from Oddschecker.com
#
# Oddschecker renders its winner market server-side. Each horse is a
# <tr data-bname="..."> row whose data-initial-odds-state attribute holds
# comma-separated tokens: selection_id_bookie_fractional_decimal_flag
# where flag == "0" means a price is available, "1" means it is not.

import json
import os
import re
import statistics
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

# v2.5.64 — disk + in-memory cache for Oddschecker fetches.
_OC_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "learning", "oc_cache.json")
_OC_CACHE_TTL = 1800   # 30 minutes — fresh-enough for in-day pricing
_OC_CACHE_MAX_AGE = 14400  # 4 hours — anything older is purged on load
_OC_MEM_CACHE: dict = {}   # key -> (timestamp, data)


def _oc_cache_key(course: str, time_str: str) -> str:
    return f"{(course or '').lower().strip()}|{(time_str or '').strip()}"


def _load_oc_cache() -> dict:
    try:
        with open(_OC_CACHE_PATH) as f:
            cache = json.load(f)
        now = time.time()
        return {k: v for k, v in cache.items()
                if isinstance(v, dict) and now - v.get("ts", 0) < _OC_CACHE_MAX_AGE}
    except Exception:
        return {}


def _save_oc_cache(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_OC_CACHE_PATH), exist_ok=True)
        with open(_OC_CACHE_PATH, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Course name → Oddschecker slug.
COURSE_SLUGS = {
    "sandown":         "sandown",
    "perth":           "perth",
    "cork":            "cork",
    "doncaster":       "doncaster",
    "chepstow":        "chepstow",
    "fontwell":        "fontwell",
    "cheltenham":      "cheltenham",
    "ascot":           "ascot",
    "newmarket":       "newmarket",
    "pontefract":      "pontefract",
    "haydock":         "haydock",
    "york":            "york",
    "lingfield":       "lingfield",
    "kempton":         "kempton",
    "leicester":       "leicester",
    "nottingham":      "nottingham",
    "windsor":         "windsor",
    "goodwood":        "goodwood",
    "wolverhampton":   "wolverhampton",
    "chester":         "chester",
    "carlisle":        "carlisle",
    "catterick":       "catterick",
    "ripon":           "ripon",
    "redcar":          "redcar",
    "ayr":             "ayr",
    "hamilton":        "hamilton",
    "musselburgh":     "musselburgh",
}

# Bookmaker code → friendly display name.
BOOKIE_NAMES = {
    "B3": "Bet365", "WH": "WilliamHill", "UN": "Unibet", "FR": "Betfred",
    "LD": "Ladbrokes", "CE": "Coral", "SX": "SportingBet", "VC": "BetVictor",
    "S6": "SkyBet", "PUP": "PaddyPower", "PP": "PaddyPower2", "BY": "Betway",
    "BF": "BetfairExchange", "MA": "Matchbook", "KN": "Karamba", "OE": "OddsEd",
    "SI": "SportingIndex", "QN": "QuinnBet", "WA": "Watcher", "G5": "Gentingbet",
    "BTT": "Betit", "BRS": "BetRivers", "SK": "SportingKings", "AKB": "AkBet",
    "GY": "Greyhound",
}


def _slugify_course(course: str) -> str:
    key = (course or "").strip().lower()
    if key in COURSE_SLUGS:
        return COURSE_SLUGS[key]
    return re.sub(r"\s+", "-", key)


def _decimal_from_fraction(frac: str) -> Optional[float]:
    try:
        if not frac:
            return None
        frac = frac.strip()
        if frac.lower() in ("evs", "evens"):
            return 2.0
        if "/" in frac:
            n, d = frac.split("/", 1)
            return float(n) / float(d) + 1.0
        return float(frac)
    except Exception:
        return None


def _parse_odds_state(state: str) -> dict:
    """Parse the data-initial-odds-state attribute into {bookie_code: decimal}."""
    out = {}
    if not state:
        return out
    for token in state.split(","):
        parts = token.split("_")
        if len(parts) < 5:
            continue
        bookie = parts[1]
        frac   = parts[2]
        dec    = parts[3]
        flag   = parts[4]
        if flag != "0":
            continue
        if not frac:
            continue
        decimal = None
        try:
            if dec:
                decimal = float(dec)
        except Exception:
            decimal = None
        if decimal is None or decimal <= 1.0:
            decimal = _decimal_from_fraction(frac)
        if decimal and decimal > 1.0:
            out[bookie] = {"decimal": round(decimal, 3), "fractional": frac}
    return out


def get_oddschecker_odds(course: str, time_str: str, timeout: int = 8) -> dict:
    """
    Cached wrapper around _fetch_oddschecker_odds.
    Memory cache → disk cache → live fetch. TTL = 30 min.
    """
    key = _oc_cache_key(course, time_str)
    now = time.time()

    mem = _OC_MEM_CACHE.get(key)
    if mem and now - mem[0] < _OC_CACHE_TTL:
        return mem[1]

    disk_cache = _load_oc_cache()
    entry = disk_cache.get(key)
    if entry and now - entry.get("ts", 0) < _OC_CACHE_TTL:
        data = entry.get("data", {}) or {}
        _OC_MEM_CACHE[key] = (now, data)
        return data

    result = _fetch_oddschecker_odds(course, time_str, timeout) or {}
    _OC_MEM_CACHE[key] = (now, result)
    disk_cache[key] = {"ts": now, "data": result}
    _save_oc_cache(disk_cache)
    return result


def _fetch_oddschecker_odds(course: str, time_str: str, timeout: int = 8) -> dict:
    """
    Fetch live odds from Oddschecker for a specific race.
    Returns {horse_name: {best_decimal, best_fractional, best_bookmakers,
                          best_bookmaker_names, bookmaker_count, odds_by_bookie,
                          consensus_decimal, shortening_count}}.
    Returns {} on any failure — callers must fall back to their existing source.
    """
    try:
        slug = _slugify_course(course)
        time_clean = (time_str or "").strip()
        url = f"https://www.oddschecker.com/horse-racing/{slug}/{time_clean}/winner"
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return {}
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.find_all("tr", attrs={"data-bname": True})
        out = {}
        for row in rows:
            horse = (row.get("data-bname") or "").strip()
            if not horse:
                continue
            state = row.get("data-initial-odds-state", "")
            bookie_prices = _parse_odds_state(state)
            if not bookie_prices:
                continue

            best_dec  = 0.0
            best_frac = ""
            best_codes = []
            for code, pr in bookie_prices.items():
                dec = pr["decimal"]
                if dec > best_dec:
                    best_dec  = dec
                    best_frac = pr["fractional"]
                    best_codes = [code]
                elif dec == best_dec:
                    best_codes.append(code)

            decimals = [pr["decimal"] for pr in bookie_prices.values()]
            consensus = round(statistics.median(decimals), 3) if decimals else 0.0

            out[horse] = {
                "best_decimal":         round(best_dec, 3),
                "best_fractional":      best_frac,
                "best_bookmakers":      best_codes,
                "best_bookmaker_names": [BOOKIE_NAMES.get(c, c) for c in best_codes],
                "bookmaker_count":      len(bookie_prices),
                "odds_by_bookie":       {k: v["decimal"] for k, v in bookie_prices.items()},
                "consensus_decimal":    consensus,
                "shortening_count":     0,  # requires snapshot history — wire in later
            }
        return out
    except Exception as e:
        try:
            print(f"[Oddschecker] Failed for {course} {time_str}: {e}")
        except Exception:
            pass
        return {}


def augment_runner(runner: dict, oc_entry: Optional[dict]) -> dict:
    """
    Merge Oddschecker best-price data into an existing Sporting Life runner dict.
    Silently leaves the runner unchanged if the entry is empty.
    """
    if not oc_entry:
        return runner
    runner["best_odds_decimal"]    = oc_entry.get("best_decimal")
    runner["best_odds_fractional"] = oc_entry.get("best_fractional")
    names = oc_entry.get("best_bookmaker_names") or []
    runner["best_bookmaker"]       = names[0] if names else ""
    runner["best_bookmakers"]      = names
    runner["odds_consensus"]       = oc_entry.get("consensus_decimal")
    runner["bookmaker_count"]      = oc_entry.get("bookmaker_count")
    return runner
