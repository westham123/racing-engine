"""
Racing API client — The Racing API (theracingapi.com)
Supports free tier (/racecards/free, /results/today/free) and Standard tier.

Authentication: HTTP Basic Auth (username:password base64 encoded).
Credentials stored in environment or passed directly.

Free tier rate limit: 1 req/s
Standard tier rate limit: 5 req/s (bulk endpoints: 2 req/s)
"""

import os
import time
import json
import base64
import logging
import datetime
import zoneinfo
from typing import Optional
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

_BASE_URL   = "https://api.theracingapi.com/v1"
_LONDON     = zoneinfo.ZoneInfo("Europe/London")

# ── credentials ───────────────────────────────────────────────────────────────
def _get_credentials() -> Optional[tuple[str, str]]:
    """Return (username, password) from env vars, or None if not configured."""
    username = os.environ.get("RACING_API_USERNAME") or os.environ.get("RACING_API_USER")
    password = os.environ.get("RACING_API_PASSWORD") or os.environ.get("RACING_API_PASS")
    if username and password:
        return username, password
    return None


def _auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


# ── HTTP helper ───────────────────────────────────────────────────────────────
def _get(path: str, params: dict = None, timeout: int = 15) -> Optional[dict]:
    """Make a GET request to the Racing API. Returns parsed JSON or None on error."""
    creds = _get_credentials()
    if not creds:
        logger.warning("[RacingAPI] No credentials configured — set RACING_API_USERNAME and RACING_API_PASSWORD")
        return None

    url = f"{_BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"

    req = urllib.request.Request(url)
    req.add_header("Authorization", _auth_header(*creds))
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        logger.warning(f"[RacingAPI] HTTP {e.code} for {path}: {e.reason}")
        return None
    except Exception as e:
        logger.warning(f"[RacingAPI] Request failed for {path}: {e}")
        return None


# ── free tier endpoints ────────────────────────────────────────────────────────
def get_free_racecards(date: str = None) -> list[dict]:
    """
    GET /racecards/free — today's racecards (free tier).
    Returns list of runners with: horse, course, time, jockey, trainer,
    weight, draw, official_rating, form, going.
    No SP, no odds.
    date: YYYY-MM-DD (defaults to today BST)
    """
    if date is None:
        date = datetime.datetime.now(_LONDON).strftime("%Y-%m-%d")

    data = _get("/racecards/free", {"date": date})
    if not data:
        return []

    runners = []
    for race in data.get("racecards", []):
        course   = race.get("course", "")
        time_bst = race.get("off_time", "")
        going    = race.get("going", "")
        for r in race.get("runners", []):
            runners.append({
                "source":          "racing_api_free",
                "date":            date,
                "course":          course,
                "time":            time_bst,
                "horse":           r.get("horse", ""),
                "jockey":          r.get("jockey", ""),
                "trainer":         r.get("trainer", ""),
                "weight_lbs":      r.get("lbs", ""),
                "draw":            r.get("draw", ""),
                "official_rating": r.get("official_rating", ""),
                "form":            r.get("form", ""),
                "going":           going,
                "age":             r.get("age", ""),
                "sex":             r.get("sex", ""),
            })
    return runners


def get_free_results(date: str = None) -> list[dict]:
    """
    GET /results/today/free — today's results (free tier).
    Returns position, weight, draw. No SP, no beaten lengths.
    date: YYYY-MM-DD (defaults to today BST)
    """
    if date is None:
        date = datetime.datetime.now(_LONDON).strftime("%Y-%m-%d")

    data = _get("/results/today/free", {"date": date})
    if not data:
        return []

    results = []
    for race in data.get("results", []):
        course   = race.get("course", "")
        time_bst = race.get("off_time", "")
        for r in race.get("runners", []):
            results.append({
                "source":   "racing_api_free",
                "date":     date,
                "course":   course,
                "time":     time_bst,
                "horse":    r.get("horse", ""),
                "position": r.get("position", ""),
                "draw":     r.get("draw", ""),
                "weight_lbs": r.get("lbs", ""),
            })
    return results


# ── standard tier endpoints (£59.99/mo) ──────────────────────────────────────
def get_results_with_sp(date: str = None) -> list[dict]:
    """
    Standard tier: GET /results — results with SP, beaten lengths, winning time.
    """
    if date is None:
        date = datetime.datetime.now(_LONDON).strftime("%Y-%m-%d")

    data = _get("/results", {"date": date, "region": "gb,ire"})
    if not data:
        return []

    results = []
    for race in data.get("results", []):
        course   = race.get("course", "")
        time_bst = race.get("off_time", "")
        for r in race.get("runners", []):
            results.append({
                "source":          "racing_api_standard",
                "date":            date,
                "course":          course,
                "time":            time_bst,
                "horse":           r.get("horse", ""),
                "position":        r.get("position", ""),
                "sp":              r.get("sp", ""),
                "bsp":             r.get("bsp", ""),
                "beaten_lengths":  r.get("btn", ""),
                "trainer_14days":  r.get("trainer_14_days", {}).get("wins", ""),
            })
    return results


def get_racecards_standard(date: str = None) -> list[dict]:
    """
    Standard tier: GET /racecards — full racecards with 20+ bookie live odds,
    going_detailed, trainer_14_days win rate per runner.
    """
    if date is None:
        date = datetime.datetime.now(_LONDON).strftime("%Y-%m-%d")

    data = _get("/racecards", {"date": date, "region": "gb,ire"})
    if not data:
        return []

    runners = []
    for race in data.get("racecards", []):
        course        = race.get("course", "")
        time_bst      = race.get("off_time", "")
        going_detail  = race.get("going_detailed", race.get("going", ""))
        for r in race.get("runners", []):
            t14 = r.get("trainer_14_days", {})
            runners.append({
                "source":           "racing_api_standard",
                "date":             date,
                "course":           course,
                "time":             time_bst,
                "horse":            r.get("horse", ""),
                "jockey":           r.get("jockey", ""),
                "trainer":          r.get("trainer", ""),
                "weight_lbs":       r.get("lbs", ""),
                "draw":             r.get("draw", ""),
                "official_rating":  r.get("official_rating", ""),
                "form":             r.get("form", ""),
                "going_detailed":   going_detail,
                "age":              r.get("age", ""),
                "trainer_14d_wins": t14.get("wins", ""),
                "trainer_14d_runs": t14.get("runs", ""),
                "best_odds":        r.get("odds", {}).get("best", ""),
            })
    return runners


# ── connection test ────────────────────────────────────────────────────────────
def test_connection() -> dict:
    """Quick connectivity and credentials check. Returns status dict."""
    creds = _get_credentials()
    if not creds:
        return {"ok": False, "error": "No credentials in environment"}

    data = _get("/racecards/free")
    if data is None:
        return {"ok": False, "error": "Request failed — check credentials or network"}

    races = data.get("racecards", [])
    return {
        "ok":    True,
        "tier":  "free",
        "races": len(races),
        "msg":   f"Connected. {len(races)} racecards returned for today.",
    }


if __name__ == "__main__":
    # Quick connection test
    import sys
    if len(sys.argv) > 1:
        os.environ["RACING_API_USERNAME"] = sys.argv[1]
        os.environ["RACING_API_PASSWORD"] = sys.argv[2]
    result = test_connection()
    print(json.dumps(result, indent=2))
