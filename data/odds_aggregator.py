# Racing Engine — Multi-Source Odds Aggregator
# Version: 1.0
# Date: 21 April 2026
#
# Sources (in priority order):
#   1. Betfair Exchange API  — most accurate real-time market signal
#   2. The Racing API        — Bet365, Paddy Power, Ladbrokes, Coral, William Hill,
#                              Betway, BoyleSports, Sky Bet, Betfred, Unibet, etc.
#   3. Oddschecker feed      — public best-odds aggregator, all UK/Irish bookmakers
#
# Output: unified dict per runner — odds from every available bookmaker,
#         best available price, Betfair SP, exchange money, move signals

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
import json
import time
from datetime import datetime
from config.settings import (
    BETFAIR_APP_KEY, BETFAIR_USERNAME, BETFAIR_PASSWORD,
    RACING_API_USERNAME, RACING_API_PASSWORD,
)

# ── Session caches ────────────────────────────────────────────
_betfair_session_token = None
_racing_api_token      = None


# ════════════════════════════════════════════════════════════════
# SOURCE 1 — BETFAIR EXCHANGE
# ════════════════════════════════════════════════════════════════

def _betfair_login() -> str | None:
    """Log into Betfair and return session token. Cached per session."""
    global _betfair_session_token
    if _betfair_session_token:
        return _betfair_session_token
    if not BETFAIR_USERNAME or not BETFAIR_PASSWORD:
        return None
    try:
        resp = requests.post(
            "https://identitysso.betfair.com/api/login",
            data={"username": BETFAIR_USERNAME, "password": BETFAIR_PASSWORD},
            headers={"X-Application": BETFAIR_APP_KEY, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        data = resp.json()
        if data.get("status") == "SUCCESS":
            _betfair_session_token = data["token"]
            print("[Betfair] Logged in successfully")
            return _betfair_session_token
        else:
            print(f"[Betfair] Login failed: {data.get('error', 'unknown')}")
            return None
    except Exception as e:
        print(f"[Betfair] Login error: {e}")
        return None


def get_betfair_odds(race_id: str = None, horse_name: str = None) -> dict:
    """
    Returns Betfair exchange odds for all UK/Irish horse racing markets.
    If race_id or horse_name provided, filters to that race/runner.
    Returns dict keyed by horse name: {back_price, lay_price, matched_vol, market_id}
    """
    token = _betfair_login()
    if not token:
        return {}

    try:
        headers = {
            "X-Application":    BETFAIR_APP_KEY,
            "X-Authentication": token,
            "Content-Type":     "application/json",
        }

        # List all UK/IE horse racing markets in next 24 hours
        market_filter = {
            "jsonrpc": "2.0",
            "method":  "SportsAPING/v1.0/listMarketCatalogue",
            "params":  {
                "filter": {
                    "eventTypeIds": ["7"],           # Horse Racing
                    "marketCountries": ["GB", "IE"],
                    "marketTypeCodes": ["WIN"],
                    "inPlayOnly": False,
                },
                "marketProjection": ["RUNNER_DESCRIPTION", "EVENT", "MARKET_START_TIME"],
                "maxResults": "200",
            },
            "id": 1
        }

        resp = requests.post(
            "https://api.betfair.com/exchange/betting/json-rpc/v1",
            json=market_filter,
            headers=headers,
            timeout=15,
        )
        markets = resp.json().get("result", [])

        if not markets:
            return {}

        # Get odds for all markets in one call
        market_ids = [m["marketId"] for m in markets[:50]]  # Max 50 per call

        odds_req = {
            "jsonrpc": "2.0",
            "method":  "SportsAPING/v1.0/listMarketBook",
            "params":  {
                "marketIds": market_ids,
                "priceProjection": {
                    "priceData": ["EX_BEST_OFFERS"],
                    "exBestOffersOverrides": {"bestPricesDepth": 1},
                },
            },
            "id": 2
        }

        resp2 = requests.post(
            "https://api.betfair.com/exchange/betting/json-rpc/v1",
            json=odds_req,
            headers=headers,
            timeout=15,
        )
        books = resp2.json().get("result", [])

        # Build runner name → market_id map
        runner_map = {}
        for m in markets:
            for r in m.get("runners", []):
                runner_map[r["selectionId"]] = {
                    "name":      r.get("runnerName", "Unknown"),
                    "market_id": m["marketId"],
                    "race":      m.get("event", {}).get("name", ""),
                    "time":      m.get("marketStartTime", ""),
                }

        result = {}
        for book in books:
            for runner in book.get("runners", []):
                sel_id = runner["selectionId"]
                info   = runner_map.get(sel_id, {})
                name   = info.get("name", str(sel_id))

                ex = runner.get("ex", {})
                backs = ex.get("availableToBack", [])
                lays  = ex.get("availableToLay", [])

                back_price = backs[0]["price"] if backs else None
                lay_price  = lays[0]["price"]  if lays  else None
                matched    = runner.get("totalMatched", 0)

                result[name.lower().strip()] = {
                    "betfair_back":    back_price,
                    "betfair_lay":     lay_price,
                    "betfair_matched": matched,
                    "market_id":       book["marketId"],
                    "race":            info.get("race", ""),
                    "race_time":       info.get("time", ""),
                    "source":          "betfair_exchange",
                }

        print(f"[Betfair] {len(result)} runners fetched from {len(books)} markets")
        return result

    except Exception as e:
        print(f"[Betfair] Odds fetch error: {e}")
        return {}


# ════════════════════════════════════════════════════════════════
# SOURCE 2 — THE RACING API
# ════════════════════════════════════════════════════════════════

def get_racing_api_odds(course: str = None, race_time: str = None) -> dict:
    """
    Returns bookmaker odds for today's UK/IE races from The Racing API.
    Covers: Bet365, Paddy Power, Ladbrokes, Coral, William Hill,
            Betway, BoyleSports, Sky Bet, Betfred, Unibet, Betfair SP.
    Returns dict keyed by horse name: {bookmaker: price, ...}
    """
    if not RACING_API_USERNAME or not RACING_API_PASSWORD:
        return {}   # Not yet verified — returns empty, not an error

    try:
        from datetime import date
        today = date.today().isoformat()

        resp = requests.get(
            "https://api.theracingapi.com/v1/racecards/odds",
            params={
                "date":     today,
                "region":   "gb,ire",
                "course":   course or "",
            },
            auth=(RACING_API_USERNAME, RACING_API_PASSWORD),
            timeout=15,
        )

        if resp.status_code != 200:
            print(f"[RacingAPI] Odds endpoint returned {resp.status_code}")
            return {}

        data = resp.json()
        result = {}

        for race in data.get("racecards", []):
            for runner in race.get("runners", []):
                name = runner.get("horse", "").lower().strip()
                if not name:
                    continue

                bookmaker_odds = {}
                for bm in runner.get("odds", []):
                    bk_name  = bm.get("bookmaker", "")
                    bk_price = bm.get("decimal_odds") or _frac_to_dec(bm.get("fractional_odds", ""))
                    if bk_name and bk_price:
                        bookmaker_odds[bk_name] = bk_price

                if bookmaker_odds:
                    result[name] = {
                        "bookmaker_odds": bookmaker_odds,
                        "best_price":     min(bookmaker_odds.values()),   # Lowest dec = best for backer
                        "best_bookie":    min(bookmaker_odds, key=bookmaker_odds.get),
                        "betfair_sp":     runner.get("sp_dec"),
                        "source":         "racing_api",
                    }

        print(f"[RacingAPI] {len(result)} runners with odds")
        return result

    except Exception as e:
        print(f"[RacingAPI] Odds fetch error: {e}")
        return {}


# ════════════════════════════════════════════════════════════════
# SOURCE 3 — ODDSCHECKER (PUBLIC BEST-ODDS FEED)
# ════════════════════════════════════════════════════════════════

def get_oddschecker_odds(course: str, race_time: str) -> dict:
    """
    Fetches best-available odds from Oddschecker for a specific race.
    Covers ALL UK and Irish bookmakers including smaller independents.
    Returns dict keyed by horse name: {bookmaker: decimal_price, ...}
    """
    try:
        # Normalise course name for URL
        course_slug = course.lower().replace(" ", "-").replace("'", "")
        time_slug   = race_time.replace(":", "")

        url = f"https://www.oddschecker.com/horse-racing/{course_slug}/{time_slug}/winner"

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-GB,en;q=0.9",
        }

        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"[Oddschecker] {course} {race_time} returned {resp.status_code}")
            return {}

        # Parse the embedded JSON data from the page
        import re
        pattern = r'window\.__NEXT_DATA__\s*=\s*(\{.*?\});</script>'
        match   = re.search(pattern, resp.text, re.DOTALL)

        if not match:
            # Try alternate data format
            pattern2 = r'"runners":\s*(\[.*?\])'
            match2 = re.search(pattern2, resp.text, re.DOTALL)
            if not match2:
                print(f"[Oddschecker] Could not parse page for {course} {race_time}")
                return {}
            runners_raw = json.loads(match2.group(1))
        else:
            page_data = json.loads(match.group(1))
            runners_raw = (
                page_data
                .get("props", {})
                .get("pageProps", {})
                .get("data", {})
                .get("runners", [])
            )

        result = {}
        for runner in runners_raw:
            name = runner.get("name", "").lower().strip()
            if not name:
                continue

            bookmaker_odds = {}
            for odds_entry in runner.get("odds", []):
                bk   = odds_entry.get("bookmaker", "")
                frac = odds_entry.get("fractionalOdds") or odds_entry.get("odds", "")
                dec  = odds_entry.get("decimalOdds")
                if not dec and frac:
                    dec = _frac_to_dec(frac)
                if bk and dec:
                    bookmaker_odds[bk] = float(dec)

            if bookmaker_odds:
                best_bk    = min(bookmaker_odds, key=bookmaker_odds.get)
                best_price = bookmaker_odds[best_bk]
                result[name] = {
                    "bookmaker_odds": bookmaker_odds,
                    "best_price":     best_price,
                    "best_bookie":    best_bk,
                    "source":         "oddschecker",
                }

        print(f"[Oddschecker] {len(result)} runners for {course} {race_time}")
        return result

    except Exception as e:
        print(f"[Oddschecker] Error for {course} {race_time}: {e}")
        return {}


# ════════════════════════════════════════════════════════════════
# UNIFIED AGGREGATOR
# ════════════════════════════════════════════════════════════════

def get_all_odds(course: str, race_time: str, runners: list = None) -> dict:
    """
    Fetches odds from all three sources and merges into a single
    unified dict per runner.

    Returns dict keyed by normalised horse name:
    {
        "horse":           str,
        "betfair_back":    float | None,
        "betfair_lay":     float | None,
        "betfair_matched": float | None,
        "bookmaker_odds":  {bookmaker: decimal_price, ...},   # All bookmakers
        "best_price":      float | None,
        "best_bookie":     str | None,
        "betfair_sp":      float | None,
        "sources":         list[str],   # Which sources contributed data
    }
    """
    result = {}

    # Seed from runners list if provided
    if runners:
        for r in runners:
            name = r.get("horse", "").lower().strip()
            if name:
                result[name] = {
                    "horse":           r.get("horse", ""),
                    "betfair_back":    None,
                    "betfair_lay":     None,
                    "betfair_matched": None,
                    "bookmaker_odds":  {},
                    "best_price":      None,
                    "best_bookie":     None,
                    "betfair_sp":      None,
                    "sources":         [],
                }

    # ── Source 1: Betfair Exchange ────────────────────────────
    try:
        bf_data = get_betfair_odds()
        for name, bf in bf_data.items():
            if name not in result:
                result[name] = _empty_runner(name)
            result[name]["betfair_back"]    = bf.get("betfair_back")
            result[name]["betfair_lay"]     = bf.get("betfair_lay")
            result[name]["betfair_matched"] = bf.get("betfair_matched")
            if "betfair_exchange" not in result[name]["sources"]:
                result[name]["sources"].append("betfair_exchange")
    except Exception as e:
        print(f"[Aggregator] Betfair source error: {e}")

    # ── Source 2: The Racing API ──────────────────────────────
    try:
        ra_data = get_racing_api_odds(course=course, race_time=race_time)
        for name, ra in ra_data.items():
            if name not in result:
                result[name] = _empty_runner(name)
            result[name]["bookmaker_odds"].update(ra.get("bookmaker_odds", {}))
            result[name]["betfair_sp"] = ra.get("betfair_sp")
            if "racing_api" not in result[name]["sources"]:
                result[name]["sources"].append("racing_api")
    except Exception as e:
        print(f"[Aggregator] Racing API source error: {e}")

    # ── Source 3: Oddschecker ─────────────────────────────────
    try:
        oc_data = get_oddschecker_odds(course=course, race_time=race_time)
        for name, oc in oc_data.items():
            if name not in result:
                result[name] = _empty_runner(name)
            result[name]["bookmaker_odds"].update(oc.get("bookmaker_odds", {}))
            if "oddschecker" not in result[name]["sources"]:
                result[name]["sources"].append("oddschecker")
    except Exception as e:
        print(f"[Aggregator] Oddschecker source error: {e}")

    # ── Compute best available price across all bookmakers ────
    for name, data in result.items():
        bm_odds = data.get("bookmaker_odds", {})
        if bm_odds:
            # Best decimal price = highest decimal (most value for backer)
            best_bk  = max(bm_odds, key=bm_odds.get)
            best_dec = bm_odds[best_bk]
            data["best_price"]  = best_dec
            data["best_bookie"] = best_bk

    return result


def _empty_runner(name: str) -> dict:
    return {
        "horse":           name,
        "betfair_back":    None,
        "betfair_lay":     None,
        "betfair_matched": None,
        "bookmaker_odds":  {},
        "best_price":      None,
        "best_bookie":     None,
        "betfair_sp":      None,
        "sources":         [],
    }


def _frac_to_dec(frac_str: str) -> float | None:
    """Convert fractional odds string to decimal."""
    try:
        s = str(frac_str).strip()
        if "/" in s:
            n, d = s.split("/")
            return round((float(n) + float(d)) / float(d), 3)
        f = float(s)
        return f if f > 1 else None
    except Exception:
        return None
