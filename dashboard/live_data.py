# Racing Engine — Live Data Fetcher
# Version: 2.6.0 — 1 May 2026
# v2.6.0: pass previous_results / race_history_stats / rating123 / last_ran_days
# through to scoring; collect all_ratings_in_race per race for OR-gap signal.
# Fixes: BSP fail-fast, snapshot-based signal detection, Time+Course columns

import requests
import json
import json as _json
import re
from bs4 import BeautifulSoup
from datetime import date, datetime, timezone, timedelta
import pandas as pd
import sys, os

# ── Timezone helper ───────────────────────────────────────────────────
# Feed from Sporting Life provides times in UTC. Convert to BST (Europe/London).
try:
    import zoneinfo as _zoneinfo
    _LONDON = _zoneinfo.ZoneInfo("Europe/London")
except Exception:
    _LONDON = None

def _utc_to_bst(utc_time_str):
    """Convert a HH:MM UTC string to HH:MM BST (Europe/London). Returns original on failure."""
    if not utc_time_str:
        return utc_time_str
    try:
        if _LONDON:
            # Use today's actual date so DST offset is correct (strptime alone gives year 1900)
            _today = date.today()
            _h, _m = map(int, str(utc_time_str).strip().split(":"))
            _utc_dt = datetime(_today.year, _today.month, _today.day, _h, _m,
                               tzinfo=timezone.utc)
            return _utc_dt.astimezone(_LONDON).strftime("%H:%M")
        else:
            # Fallback: manual UTC+1 (correct for BST/summer)
            _h, _m = map(int, str(utc_time_str).strip().split(":"))
            _bst_h = (_h + 1) % 24
            return f"{_bst_h:02d}:{_m:02d}"
    except Exception:
        return utc_time_str  # return as-is if anything fails

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from engine.odds_model import OddsModel as _OddsModel
    _odds_model = _OddsModel()
    MODEL_AVAILABLE = True
except Exception:
    MODEL_AVAILABLE = False
    _odds_model = None

# Oddschecker multi-bookmaker odds (v2.5.40). Soft import — failures fall back
# silently to Sporting Life odds so the main pipeline keeps running.
try:
    from engine.oddschecker import get_oddschecker_odds as _get_oc_odds, augment_runner as _oc_augment
    OC_AVAILABLE = True
except Exception:
    OC_AVAILABLE = False
    _get_oc_odds = None
    _oc_augment  = None

# Per-race Oddschecker cache — avoids re-fetching the same race within one pipeline run
_oc_race_cache: dict = {}

# ── Betfair BSP client — fail-fast, single attempt only ──────────────────────
# The free delay key returns 403 from identitysso — attempt once, then skip.
_bsp_client = None
_bsp_logged_in = False
_bsp_login_attempted = False

def _get_bsp_client():
    """Return authenticated BetfairBSP client, or None. Tries exactly once."""
    global _bsp_client, _bsp_logged_in, _bsp_login_attempted
    if _bsp_logged_in:
        return _bsp_client
    if _bsp_login_attempted:
        return None
    _bsp_login_attempted = True
    try:
        try:
            import streamlit as st
            app_key  = st.secrets.get("BETFAIR_APP_KEY",  "1Bj49mxBZBQ961WM")
            username = st.secrets.get("BETFAIR_USERNAME", "")
            password = st.secrets.get("BETFAIR_PASSWORD", "")
        except Exception:
            try:
                from config.settings import BETFAIR_APP_KEY, BETFAIR_USERNAME, BETFAIR_PASSWORD
                app_key, username, password = BETFAIR_APP_KEY, BETFAIR_USERNAME, BETFAIR_PASSWORD
            except Exception:
                return None
        if not username or not password:
            return None
        from data.betfair_bsp import BetfairBSP
        _bsp_client = BetfairBSP(app_key, username, password)
        if _bsp_client.login():
            _bsp_logged_in = True
            return _bsp_client
        return None
    except Exception:
        return None

# Per-race BSP cache
_bsp_race_cache: dict = {}

# ── Odds snapshot — persisted between Streamlit reloads for signal detection ─
_SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "..", "learning", "odds_snapshot.json")

def _load_snapshot() -> dict:
    try:
        if os.path.exists(_SNAPSHOT_PATH):
            with open(_SNAPSHOT_PATH, "r") as f:
                return _json.load(f)
    except Exception:
        pass
    return {}

def _save_snapshot(snap: dict):
    try:
        os.makedirs(os.path.dirname(_SNAPSHOT_PATH), exist_ok=True)
        with open(_SNAPSHOT_PATH, "w") as f:
            _json.dump(snap, f)
    except Exception:
        pass

def _to_decimal(odds_str) -> float:
    try:
        s = str(odds_str).strip()
        if "/" in s:
            n, d = s.split("/")
            return float(n) / float(d) + 1.0
        return float(s)
    except Exception:
        return 0.0

def _detect_signal(horse_key: str, current_dec: float, snapshot: dict) -> str:
    """
    Compare current decimal price against stored snapshot.
    Steam  = shortening > 15%  |  Move = shortening 5-15%
    Drift  = lengthening > 12% |  Stable = everything else
    """
    prev = snapshot.get(horse_key)
    if not prev or current_dec <= 0:
        return "Stable"
    try:
        prev_dec = float(prev)
        if prev_dec <= 0:
            return "Stable"
        pct = (current_dec - prev_dec) / prev_dec   # negative = shortening
        if pct <= -0.15:  return "⬆ Steam"
        if pct <= -0.05:  return "⬆ Move"
        if pct >= 0.12:   return "⬇ Drift"
        return "Stable"
    except Exception:
        return "Stable"

# ── HTTP headers ──────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

UK_IRE_COUNTRIES = {
    "GB", "UK", "IRE", "IE",
    "Redcar", "Lingfield", "Kelso", "Newcastle", "Newmarket", "Ascot",
    "Cheltenham", "Epsom", "Goodwood", "York", "Chester", "Haydock",
    "Sandown", "Kempton", "Windsor", "Leicester", "Nottingham",
    "Wolverhampton", "Southwell", "Carlisle", "Catterick", "Doncaster",
    "Musselburgh", "Perth", "Ayr", "Hamilton", "Ripon", "Thirsk",
    "Beverley", "Brighton", "Chepstow", "Exeter", "Ffos Las",
    "Huntingdon", "Market Rasen", "Plumpton", "Stratford", "Uttoxeter",
    "Warwick", "Wincanton", "Worcester",
    "Tramore", "Leopardstown", "The Curragh", "Punchestown", "Fairyhouse",
    "Navan", "Naas", "Dundalk", "Galway", "Cork", "Tipperary",
    "Limerick", "Listowel", "Killarney", "Ballinrobe", "Clonmel",
    "Down Royal", "Downpatrick", "Roscommon", "Sligo", "Thurles", "Wexford"
}


def _get_page_json(url):
    """Fetch a Sporting Life page and return the embedded __NEXT_DATA__ JSON."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        nd = soup.find("script", id="__NEXT_DATA__")
        if nd:
            return json.loads(nd.get_text())
        for script in soup.find_all("script"):
            txt = script.get_text(strip=True)
            if txt.startswith('{"props"') and '"pageProps"' in txt:
                try:
                    return json.loads(txt)
                except Exception:
                    pass
        for script in soup.find_all("script"):
            txt = script.get_text(strip=True)
            if '"meetings"' in txt and '"races"' in txt and txt.startswith('{'):
                try:
                    return json.loads(txt)
                except Exception:
                    pass
    except Exception:
        pass
    return None


def get_todays_meetings():
    """
    Returns list of today's UK + Irish meetings with going and race slugs.
    Source: Sporting Life racecards (free, public).

    Slug is always built from race ID + slugified race name from NEXT_DATA
    (avoids 404s caused by using bare /racecard/{id} without the name suffix).
    """
    import re as _re
    today = date.today().strftime("%Y-%m-%d")
    url   = "https://www.sportinglife.com/racing/racecards"
    data  = _get_page_json(url)
    if not data:
        return []

    meetings_raw = data.get("props", {}).get("pageProps", {}).get("meetings", [])

    UK_IRE_LIVE = {"ENG", "SCO", "Scot", "Scotland", "IRE", "IE", "WAL", "Wale", "Wales",
                   "GB", "UK", "Northern Ireland", "NI", "Eire", "Ire"}

    def _make_slug(course: str, rc_id: str, rc_name: str) -> str:
        """Build full Sporting Life racecard slug from components."""
        course_slug = _re.sub(r"[^a-z0-9]+", "-", course.lower()).strip("-")
        name_slug   = _re.sub(r"[^a-z0-9]+", "-", rc_name.lower()).strip("-")
        return f"/racing/racecards/{today}/{course_slug}/racecard/{rc_id}/{name_slug}"

    meetings = []
    for m in meetings_raw:
        ms          = m.get("meeting_summary", {})
        course_data = ms.get("course", {})
        course      = course_data.get("name", "")
        country     = course_data.get("country", {}).get("short_name", "")
        if country not in UK_IRE_LIVE:
            continue

        going     = ms.get("going", "Unknown")
        races_raw = m.get("races", [])
        races     = []
        for rc in races_raw:
            rc_id   = str(rc.get("race_summary_reference", {}).get("id", ""))
            rc_name = rc.get("name", "")
            slug    = _make_slug(course, rc_id, rc_name) if rc_id and rc_name else None
            races.append({
                "id":     rc_id,
                "time":   rc.get("time", ""),
                "name":   rc_name,
                "stage":  rc.get("race_stage", ""),
                "slug":   slug,
                "course": course,
                "going":  going,
            })

        meetings.append({"course": course, "going": going, "races": races})

    return meetings


def get_race_runners(slug):
    """
    Fetches full runner list for a single race from Sporting Life.
    """
    if not slug:
        return []
    url  = f"https://www.sportinglife.com{slug}"
    data = _get_page_json(url)
    if not data:
        return []

    race         = data.get("props", {}).get("pageProps", {}).get("race", {})
    rides        = race.get("rides", [])
    race_summary = race.get("race_summary", {})
    going        = race_summary.get("going", "")
    # Extra race metadata for filter layer
    # NR check: case-insensitive — feed may return "NONRUNNER", "NonRunner", "non_runner", etc.
    field_size   = len([r for r in rides if str(r.get("ride_status","")).upper().replace("_","").replace("-","") != "NONRUNNER"])
    race_type    = str(race_summary.get("race_type",  "")).lower()   # flat/hurdle/chase/bumper
    race_class   = str(race_summary.get("race_class", "")).lower()   # class 1-6, group 1-3 etc
    race_name    = str(race_summary.get("race_name", ""))   # full race name (for Group/Listed filter)
    is_handicap  = any(x in race_name.lower()
                       for x in ["handicap", "hcap", "h'cap"])
    race_dist_f  = race_summary.get("distance_furlongs", 0)  # distance in furlongs

    runners = []
    for ride in rides:
        horse   = ride.get("horse",   {})
        jockey  = ride.get("jockey",  {})
        trainer = ride.get("trainer", {})
        betting = ride.get("betting", {})
        bm      = ride.get("bet_movements", [])
        bk_odds = ride.get("bookmakerOdds", [])

        current_odds = betting.get("current_odds", "N/A")
        best_bk_odds = None
        for bk in bk_odds:
            if "betfair" in bk.get("bookmakerName", "").lower():
                best_bk_odds = bk.get("fractionalOdds")
                break
        if not best_bk_odds and bk_odds:
            best_bk_odds = bk_odds[0].get("fractionalOdds")

        # Sporting Life bet_movements signal (present near race time)
        sl_signal = "Stable"
        if bm:
            def to_dec(o):
                try:
                    if "/" in str(o):
                        n, d = str(o).split("/")
                        return (float(n) + float(d)) / float(d)
                    return float(o)
                except Exception:
                    return 0
            first_p = bm[0].get("odds") if isinstance(bm[0], dict) else None
            last_p  = betting.get("current_odds")
            if first_p and last_p:
                if to_dec(last_p) < to_dec(first_p):
                    sl_signal = "⬆ Steam"
                elif to_dec(last_p) > to_dec(first_p):
                    sl_signal = "⬇ Drift"

        form         = horse.get("formsummary", {}).get("display_text", "-") or "-"
        finish_pos   = ride.get("finish_position")

        runners.append({
            "horse":          horse.get("name", "Unknown"),
            "jockey":         jockey.get("name", "-"),
            "trainer":        trainer.get("name", "-"),
            "form":           form,
            "odds":           best_bk_odds or current_odds or "N/A",
            "current_odds":   current_odds,
            "signal":         sl_signal,
            "going":          going,
            "age":            horse.get("age", "-"),
            "cloth":          ride.get("cloth_number", "-"),
            "draw":           ride.get("draw_number", "-"),
            "tf_stars":       ride.get("timeform_stars", "-"),
            "rating":         ride.get("rating123", "-"),
            # v2.6.0 — pass through full feed data for new signals
            "rating123":          ride.get("rating123"),
            "previous_results":   horse.get("previous_results", []) or [],
            "horse_lifetime_stats": ride.get("horse_lifetime_stats", []) or [],
            "race_history_stats": ride.get("race_history_stats", []) or [],
            "headgear":           ride.get("headgear", []) or [],
            "last_ran_days":      horse.get("last_ran_days"),
            # Case-insensitive NR check — normalise to uppercase, strip _ and - so
            # "NONRUNNER", "Non_Runner", "non-runner", "NonRunner" all match.
            "status":         "NON_RUNNER" if str(ride.get("ride_status","")).upper().replace("_","").replace("-","") == "NONRUNNER" else "RUNNER",
            "finish_position":finish_pos,
            "bet_movements":  bm,
            # Filter layer fields
            "field_size":     field_size,
            "race_type":      race_type,
            "race_class":     race_class,
            "race_name":      race_name,
            "is_handicap":    is_handicap,
            "race_dist_f":    race_dist_f,
        })

    return runners


_OC_CONFIDENCE_GATE = 0.48  # v2.5.64 — only races with a runner ≥ this in pass 1 get OC prices


def _score_runner(rn, course, going, time, stage, today_str,
                  snapshot, new_snapshot, bsp_race_data, oddschecker_enabled):
    """
    Score a single runner. Returns a row dict (or None if non-runner).
    When oddschecker_enabled=False, OC fields are left as None and the model
    runs on Sporting Life prices only — fast path for pass 1.
    """
    if rn.get("status") == "NON_RUNNER":
        return None

    odds_str    = rn.get("odds", "N/A")
    current_dec = _to_decimal(odds_str)

    horse_key = f"{today_str}::{time}::{course}::{rn['horse'].lower().strip()}"
    signal    = _detect_signal(horse_key, current_dec, snapshot)
    if signal == "Stable" and rn.get("signal", "Stable") != "Stable":
        signal = rn["signal"]
    if current_dec > 0:
        new_snapshot[horse_key] = current_dec

    bsp_result = None
    if bsp_race_data:
        try:
            bsp_cli = _get_bsp_client()
            if bsp_cli:
                bsp_result = bsp_cli.score_bsp_signal(rn["horse"], odds_str, bsp_race_data)
        except Exception:
            pass

    if MODEL_AVAILABLE and _odds_model is not None:
        runner_input = {
            "horse": rn.get("horse", ""),
            "odds": odds_str, "form": rn.get("form", "-"),
            "going": going, "trainer": rn.get("trainer", ""),
            "jockey": rn.get("jockey", ""), "signal": signal,
            "bet_movements": rn.get("bet_movements", []),
            "tf_stars": rn.get("tf_stars"), "course": course,
            "bsp_result": bsp_result,
            "field_size":  rn.get("field_size", 0),
            "race_type":   rn.get("race_type", ""),
            "race_class":  rn.get("race_class", ""),
            "race_name":   rn.get("race_name", ""),
            "is_handicap": rn.get("is_handicap", False),
            "current_odds": rn.get("current_odds", odds_str),
            "race_dist_f": rn.get("race_dist_f", 0.0),
            # v2.6.0 — feed fields for new scoring signals
            "previous_results":     rn.get("previous_results", []),
            "race_history_stats":   rn.get("race_history_stats", []),
            "horse_lifetime_stats": rn.get("horse_lifetime_stats", []),
            "rating123":            rn.get("rating123"),
            "last_ran_days":        rn.get("last_ran_days"),
            "all_ratings_in_race":  rn.get("all_ratings_in_race", []),
            "today_str":            today_str,
            "time":                 time,
        }
        confidence = _odds_model.calculate_confidence(runner_input)
        # v2.6.0 — course/distance signals now derived from feed's previous_results
        # (no extra HTTP). _COURSE_DISTANCE_ENABLED gate kept for legacy fallback.
        try:
            prev_res = rn.get("previous_results", []) or []
            _cs = _odds_model._score_course_form(course, prev_res)
            _ds = _odds_model._score_distance_form(rn.get("race_dist_f", 0.0), prev_res)
            # Build detail counts directly from previous_results
            _course_runs = [r for r in prev_res
                            if str(r.get("course_name","")).lower().strip()
                               == str(course).lower().strip()]
            _course_wins = sum(1 for r in _course_runs
                               if str(r.get("position","")).strip() == "1")
            _today_d = float(rn.get("race_dist_f", 0.0) or 0.0)
            _dist_runs = []
            if _today_d > 0:
                for r in prev_res:
                    _d = _odds_model._parse_furlongs(r.get("distance",""))
                    if _d > 0 and abs(_d - _today_d) <= 0.5:
                        _dist_runs.append(r)
            _dist_wins = sum(1 for r in _dist_runs
                             if str(r.get("position","")).strip() == "1")
            _cd = {
                "course_wins": _course_wins,
                "course_runs": len(_course_runs),
                "dist_wins":   _dist_wins,
                "dist_runs":   len(_dist_runs),
            }
        except Exception:
            _cs, _ds = 0.50, 0.50
            _cd = {"course_wins":0,"course_runs":0,"dist_wins":0,"dist_runs":0}
    else:
        confidence = _estimate_confidence(odds_str, rn.get("tf_stars"), rn.get("rating"))
        _cs, _ds = 0.50, 0.50
        _cd = {"course_wins":0,"course_runs":0,"dist_wins":0,"dist_runs":0}

    bsp_price   = bsp_result.get("bsp_price")    if bsp_result else None
    bsp_flag    = bsp_result.get("value_flag")   if bsp_result else ""
    bsp_vol     = bsp_result.get("vol_signal")   if bsp_result else ""
    bsp_matched = bsp_result.get("total_matched")if bsp_result else None

    return {
        "Time":        time,
        "Course":      course,
        "Race":        f"{time} {course}",
        "Horse":       rn["horse"],
        "Jockey":      rn["jockey"],
        "Trainer":     rn["trainer"],
        "Form":        rn["form"],
        "Going":       going,
        "Odds":        odds_str,
        "Current Odds": rn.get("current_odds", odds_str),
        "Best Odds Decimal":    rn.get("best_odds_decimal"),
        "Best Odds Fractional": rn.get("best_odds_fractional"),
        "Best Bookmaker":       rn.get("best_bookmaker", ""),
        "Odds Consensus":       rn.get("odds_consensus"),
        "Bookmaker Count":      rn.get("bookmaker_count"),
        "Confidence":  confidence,
        "Signal":      signal,
        "TF Stars":    rn.get("tf_stars", "-"),
        "Stage":       stage,
        "Cloth":       rn.get("cloth", "-"),
        "Draw":        rn.get("draw", "-"),
        "Finish":      rn.get("finish_position"),
        "BSP Price":   bsp_price,
        "BSP Flag":    bsp_flag,
        "BSP Volume":  bsp_vol,
        "BSP Matched": bsp_matched,
        "Field Size":  rn.get("field_size", 0),
        "Race Type":   rn.get("race_type", ""),
        "Race Class":  rn.get("race_class", ""),
        "Race Name":   rn.get("race_name", ""),
        "Is Handicap": rn.get("is_handicap", False),
        "Race Dist F":     rn.get("race_dist_f", 0.0),
        "Course Signal":   _cs,
        "Distance Signal": _ds,
        "Course Wins":     _cd.get("course_wins", 0),
        "Course Runs":     _cd.get("course_runs", 0),
        "Distance Wins":   _cd.get("dist_wins", 0),
        "Distance Runs":   _cd.get("dist_runs", 0),
        # v2.6.0 — feed-driven fields carried for downstream "why selected"
        "Previous Results":   rn.get("previous_results", []),
        "Race History Stats": rn.get("race_history_stats", []),
        "Rating123":          rn.get("rating123"),
        "Last Ran Days":      rn.get("last_ran_days"),
        "All Ratings In Race": rn.get("all_ratings_in_race", []),
    }


def get_todays_selections():
    """
    Master function — pulls all UK/Irish races, detects steam/drift via
    odds-snapshot comparison, returns DataFrame with Time + Course columns.

    v2.5.64 — confidence-gated Oddschecker fetch:
      Pass 1: score every runner using Sporting Life prices only (no OC calls).
      Identify candidate races where max confidence > 0.48 (~6-10 of ~30+).
      Pass 2: parallel-fetch OC for candidate races and re-score those runners.
    """
    meetings = get_todays_meetings()
    today_str = date.today().isoformat()

    # --- parallel prefetch of course/distance signals (cheap, do for all) ---
    try:
        from engine.course_distance import prefetch_signals as _prefetch_cd
        _all_runners_flat = []
        for _m in meetings:
            for _r in _m.get('races', []):
                for _rn in _r.get('runners', []):
                    _all_runners_flat.append({
                        'horse':      _rn.get('horse', ''),
                        'course':     _m.get('course', ''),
                        'race_dist_f': _rn.get('race_dist_f', 0.0),
                    })
        _prefetch_cd(_all_runners_flat)
    except Exception:
        pass

    snapshot     = _load_snapshot()
    new_snapshot = {}

    # ── Pass 1: fetch runners + score with no Oddschecker ─────────────────
    # Cache (course, time) → race context so pass 2 can re-score in place.
    race_ctx = {}     # key -> {meeting, race, runners, time, course, going, stage, slug}
    rows_by_race = {} # key -> list of row dicts

    for meeting in meetings:
        course = meeting["course"]
        going  = meeting["going"]
        for race in meeting["races"]:
            stage = race.get("stage", "")
            time  = _utc_to_bst(race.get("time", ""))
            slug  = race.get("slug")
            if not slug:
                continue

            runners = get_race_runners(slug)

            # v2.6.0 — collect all rating123 values in this race so the OR-gap
            # signal can compare each runner's rating to the field max.
            _all_ratings = []
            for _rn in runners:
                _rv = _rn.get("rating123")
                if _rv not in (None, "", "-"):
                    try:
                        _all_ratings.append(int(_rv))
                    except Exception:
                        pass
            for _rn in runners:
                _rn["all_ratings_in_race"] = _all_ratings

            bsp_key       = f"{course}|{time}"
            bsp_race_data = _bsp_race_cache.get(bsp_key, "UNCHECKED")
            if bsp_race_data == "UNCHECKED":
                try:
                    bsp_cli       = _get_bsp_client()
                    bsp_race_data = bsp_cli.get_race_bsp(course, time) if bsp_cli else None
                except Exception:
                    bsp_race_data = None
                _bsp_race_cache[bsp_key] = bsp_race_data

            key = f"{course}|{time}"
            race_ctx[key] = {
                "course": course, "going": going, "time": time, "stage": stage,
                "runners": runners, "bsp_race_data": bsp_race_data,
            }

            race_rows = []
            for rn in runners:
                if rn.get("status") == "NON_RUNNER":
                    print(f"[NR Gate] Stripped {rn.get('horse','?')} — status: NON_RUNNER "
                          f"(race {time} {course})")
                    continue
                row = _score_runner(rn, course, going, time, stage, today_str,
                                    snapshot, new_snapshot, bsp_race_data,
                                    oddschecker_enabled=False)
                if row is not None:
                    race_rows.append(row)
            rows_by_race[key] = race_rows

    # ── Identify candidate races (any runner ≥ confidence gate) ───────────
    candidate_keys = [k for k, rows in rows_by_race.items()
                      if rows and max(r["Confidence"] for r in rows) > _OC_CONFIDENCE_GATE]

    # ── Pass 2: parallel OC fetch for candidate races + re-score ──────────
    if candidate_keys and OC_AVAILABLE and _get_oc_odds is not None:
        try:
            from concurrent.futures import ThreadPoolExecutor, wait as _cf_wait

            def _fetch_oc(key):
                ctx = race_ctx[key]
                try:
                    _oc_race_cache[key] = _get_oc_odds(ctx["course"], ctx["time"], timeout=3) or {}
                except Exception:
                    _oc_race_cache[key] = {}

            with ThreadPoolExecutor(max_workers=12) as _pool:
                _futs = [_pool.submit(_fetch_oc, k) for k in candidate_keys
                         if k not in _oc_race_cache]
                _cf_wait(_futs, timeout=20)
        except Exception:
            pass

        for key in candidate_keys:
            ctx = race_ctx[key]
            oc_data = _oc_race_cache.get(key, {})
            if not oc_data:
                continue
            oc_lower = {k.lower().strip(): v for k, v in oc_data.items()}
            for rn in ctx["runners"]:
                _entry = oc_lower.get(str(rn.get("horse", "")).lower().strip())
                if _entry and _oc_augment is not None:
                    _oc_augment(rn, _entry)

            # Re-score this race with OC-augmented runners
            new_rows = []
            for rn in ctx["runners"]:
                if rn.get("status") == "NON_RUNNER":
                    continue
                row = _score_runner(rn, ctx["course"], ctx["going"], ctx["time"],
                                    ctx["stage"], today_str, snapshot, new_snapshot,
                                    ctx["bsp_race_data"], oddschecker_enabled=True)
                if row is not None:
                    new_rows.append(row)
            rows_by_race[key] = new_rows

    all_rows = []
    for rows in rows_by_race.values():
        all_rows.extend(rows)

    # Persist updated snapshot (today's entries only)
    if new_snapshot:
        merged = {**snapshot, **new_snapshot}
        merged = {k: v for k, v in merged.items() if k.startswith(today_str)}
        _save_snapshot(merged)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df = df.sort_values("Confidence", ascending=False)

    # ── Strip races that have already started (past race times, London/BST) ──
    try:
        import zoneinfo
        _london = zoneinfo.ZoneInfo("Europe/London")
        now_str = datetime.now(_london).strftime("%H:%M")
    except Exception:
        now_str = datetime.utcnow().strftime("%H:%M")  # fallback: UTC
    def _race_is_future(t):
        try:
            return str(t).strip() >= now_str
        except Exception:
            return True  # keep if unparseable
    if "Time" in df.columns:
        df = df[df["Time"].apply(_race_is_future)].reset_index(drop=True)

    return df


def get_going_reports():
    """Returns going for all UK/Irish meetings today."""
    meetings = get_todays_meetings()
    rows = []
    for m in meetings:
        rows.append({
            "Course":  m["course"],
            "Going":   m["going"],
            "Races":   len(m["races"]),
            "Source":  "Sporting Life / BHA",
            "Updated": datetime.now().strftime("%H:%M"),
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def get_non_runners():
    """Returns any non-runners flagged in today's races."""
    meetings = get_todays_meetings()
    nrs = []
    for meeting in meetings:
        for race in meeting["races"]:
            slug = race.get("slug")
            if not slug:
                continue
            runners = get_race_runners(slug)
            for rn in runners:
                if rn.get("status") == "NON_RUNNER":  # normalised in get_race_runners
                    nrs.append({
                        "Race":    f"{_utc_to_bst(race['time'])} {meeting['course']}",
                        "Horse":   rn["horse"],
                        "Jockey":  rn["jockey"],
                        "Trainer": rn["trainer"],
                        "Reason":  "Declared non-runner",
                        "Source":  "Sporting Life",
                        "Time":    datetime.now().strftime("%H:%M"),
                    })
    return nrs


def get_todays_results():
    """Returns results for races already run today."""
    meetings = get_todays_meetings()
    results  = []
    for meeting in meetings:
        for race in meeting["races"]:
            if race.get("stage") not in ("WEIGHEDIN", "RESULT"):
                continue
            slug = race.get("slug")
            if not slug:
                continue
            runners   = get_race_runners(slug)
            finishers = sorted(
                [r for r in runners if r.get("finish_position")],
                key=lambda x: x["finish_position"]
            )
            if finishers:
                winner = finishers[0]
                results.append({
                    "Race":    f"{_utc_to_bst(race['time'])} {meeting['course']}",
                    "Winner":  winner["horse"],
                    "Jockey":  winner["jockey"],
                    "Trainer": winner["trainer"],
                    "Odds":    winner["odds"],
                    "Going":   meeting["going"],
                    "Source":  "Sporting Life",
                })
    return pd.DataFrame(results) if results else pd.DataFrame()


def _estimate_confidence(odds_str, tf_stars=None, rating=None):
    """Confidence proxy from market odds + Timeform stars + rating."""
    try:
        if odds_str and "/" in str(odds_str):
            n, d = str(odds_str).split("/")
            implied = float(d) / (float(n) + float(d))
        elif odds_str and str(odds_str).replace(".", "").isdigit():
            implied = 1 / float(odds_str)
        else:
            implied = 0.33
    except Exception:
        implied = 0.33

    tf_boost = 0.0
    try:
        tf_boost = min(int(tf_stars), 5) * 0.02
    except Exception:
        pass

    rating_boost = 0.0
    try:
        rating_boost = (3 - int(rating)) * 0.025
    except Exception:
        pass

    return round(min(implied + tf_boost + rating_boost, 0.97), 3)
