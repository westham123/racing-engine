# Racing Engine — Live Data Fetcher
# Version: 1.1 — 21 April 2026
# Fetches real-time UK + Irish racing data from Sporting Life public pages
# Now enriched with Betfair BSP signal per runner

import requests
import json
import re
from bs4 import BeautifulSoup
from datetime import date, datetime
import pandas as pd
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from engine.odds_model import OddsModel as _OddsModel
    _odds_model = _OddsModel()
    MODEL_AVAILABLE = True
except Exception as _model_err:
    MODEL_AVAILABLE = False
    _odds_model = None

# ── Betfair BSP client (lazy init) ───────────────────────────────────────────
_bsp_client = None
_bsp_logged_in = False

def _get_bsp_client():
    """Return an authenticated BetfairBSP client, or None if unavailable."""
    global _bsp_client, _bsp_logged_in
    if _bsp_logged_in:
        return _bsp_client
    try:
        import streamlit as st
        app_key = st.secrets.get("BETFAIR_APP_KEY", "1Bj49mxBZBQ961WM")
        username = st.secrets.get("BETFAIR_USERNAME", "")
        password = st.secrets.get("BETFAIR_PASSWORD", "")
    except Exception:
        # Outside Streamlit context — try environment / settings directly
        try:
            from config.settings import BETFAIR_APP_KEY, BETFAIR_USERNAME, BETFAIR_PASSWORD
            app_key = BETFAIR_APP_KEY
            username = BETFAIR_USERNAME
            password = BETFAIR_PASSWORD
        except Exception:
            return None

    if not username or not password:
        return None

    try:
        from data.betfair_bsp import BetfairBSP
        _bsp_client = BetfairBSP(app_key, username, password)
        if _bsp_client.login():
            _bsp_logged_in = True
            print("[live_data] Betfair BSP login successful")
            return _bsp_client
        else:
            print("[live_data] Betfair BSP login failed — BSP signal will be neutral")
            return None
    except Exception as e:
        print(f"[live_data] Betfair BSP init error: {e}")
        return None

# Per-race BSP cache — avoids repeated API calls for same race
_bsp_race_cache: dict = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Courses that are UK or Irish
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

UK_IRE_MEETING_NAMES = {
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
    """Fetch a Sporting Life page and return the embedded page JSON."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200:
            return None

        # Method 1: <script id="__NEXT_DATA__"> (old format)
        soup = BeautifulSoup(r.text, "html.parser")
        nd = soup.find("script", id="__NEXT_DATA__")
        if nd:
            return json.loads(nd.get_text())

        # Method 2: plain <script> tag containing {"props":{"pageProps": (new format)
        for script in soup.find_all("script"):
            txt = script.get_text(strip=True)
            if txt.startswith('{"props"') and '"pageProps"' in txt:
                try:
                    return json.loads(txt)
                except Exception:
                    pass

        # Method 3: any script block containing meetings/races JSON
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
    Source: Sporting Life racecards page (free, public).
    """
    today = date.today().strftime("%Y-%m-%d")
    url = "https://www.sportinglife.com/racing/racecards"
    data = _get_page_json(url)
    if not data:
        return []

    meetings_raw = data.get("props", {}).get("pageProps", {}).get("meetings", [])

    # UK + Irish country codes as used by Sporting Life
    UK_IRE_COUNTRIES_LIVE = {"ENG", "SCO", "IRE", "IE", "WAL", "Wale", "Wales",
                              "GB", "UK", "Northern Ireland", "NI"}

    # Build a slug lookup from HTML links: race_id -> full path
    slug_map = {}
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if f"/racecards/{today}/" in href and "/racecard/" in href:
                # Extract race id from path: .../racecard/{id}/...
                parts = href.split("/racecard/")
                if len(parts) == 2:
                    race_id = parts[1].split("/")[0]
                    slug_map[race_id] = href
    except Exception:
        pass

    meetings = []
    for m in meetings_raw:
        ms = m.get("meeting_summary", {})
        course_data = ms.get("course", {})
        course  = course_data.get("name", "")
        country = course_data.get("country", {}).get("short_name", "")

        # Filter to UK + Irish only — use country code, not course name
        if country not in UK_IRE_COUNTRIES_LIVE:
            continue

        going = ms.get("going", "Unknown")
        races_raw = m.get("races", [])

        races = []
        for rc in races_raw:
            rc_id = str(rc.get("race_summary_reference", {}).get("id", ""))
            stage = rc.get("race_stage", "")
            time  = rc.get("time", "")
            name  = rc.get("name", "")

            # Build slug from map (HTML links) — fall back to constructing it
            slug = slug_map.get(rc_id)
            if not slug and rc_id:
                course_slug = course.lower().replace(" ", "-").replace("'", "")
                slug = f"/racing/racecards/{today}/{course_slug}/racecard/{rc_id}"

            races.append({
                "id":    rc_id,
                "time":  time,
                "name": name,
                "stage": stage,
                "slug": slug,
                "course": course,
                "going": going,
            })

        meetings.append({
            "course": course,
            "going": going,
            "races": races,
        })

    return meetings


def get_race_runners(slug):
    """
    Fetches full runner list for a single race from Sporting Life.
    Returns list of runner dicts with horse, jockey, trainer, odds, form, bet movements.
    """
    if not slug:
        return []

    url = f"https://www.sportinglife.com{slug}"
    data = _get_page_json(url)
    if not data:
        return []

    race = data.get("props", {}).get("pageProps", {}).get("race", {})
    rides = race.get("rides", [])
    race_summary = race.get("race_summary", {})
    going = race_summary.get("going", "")

    runners = []
    for ride in rides:
        if ride.get("ride_status") not in ("RUNNER", "NON_RUNNER", None, ""):
            pass  # include all

        horse = ride.get("horse", {})
        jockey = ride.get("jockey", {})
        trainer = ride.get("trainer", {})
        betting = ride.get("betting", {})
        bm = ride.get("bet_movements", [])
        bk_odds = ride.get("bookmakerOdds", [])

        # Current best odds (take Betfair Sportsbook if available, else first)
        current_odds = betting.get("current_odds", "N/A")
        best_bk_odds = None
        for bk in bk_odds:
            if "betfair" in bk.get("bookmakerName", "").lower():
                best_bk_odds = bk.get("fractionalOdds")
                break
        if not best_bk_odds and bk_odds:
            best_bk_odds = bk_odds[0].get("fractionalOdds")

        # Market move signal
        move_signal = "Stable"
        if bm:
            first_price = bm[0].get("odds") if isinstance(bm[0], dict) else None
            last_price = betting.get("current_odds")
            if first_price and last_price and first_price != last_price:
                # Simple heuristic: compare as decimal
                def to_dec(o):
                    try:
                        if "/" in str(o):
                            n, d = str(o).split("/")
                            return (float(n) + float(d)) / float(d)
                        return float(o)
                    except Exception:
                        return 0
                if to_dec(last_price) < to_dec(first_price):
                    move_signal = "⬆ Steam"
                elif to_dec(last_price) > to_dec(first_price):
                    move_signal = "⬇ Drift"

        # Form string
        form = horse.get("formsummary", {}).get("display_text", "-")
        if not form:
            form = "-"

        # Finish position (if race already run)
        finish_pos = ride.get("finish_position")

        runners.append({
            "horse": horse.get("name", "Unknown"),
            "jockey": jockey.get("name", "-"),
            "trainer": trainer.get("name", "-"),
            "form": form,
            "odds": best_bk_odds or current_odds or "N/A",
            "current_odds": current_odds,
            "signal": move_signal,
            "going": going,
            "age": horse.get("age", "-"),
            "cloth": ride.get("cloth_number", "-"),
            "draw": ride.get("draw_number", "-"),
            "tf_stars": ride.get("timeform_stars", "-"),
            "rating": ride.get("rating123", "-"),
            "status": ride.get("ride_status", "RUNNER"),
            "finish_position": finish_pos,
            "bet_movements": bm,
        })

    return runners


def get_todays_selections():
    """
    Master function — pulls all UK/Irish races today, fetches runners for upcoming races,
    returns a flat DataFrame of top selections with confidence proxies.
    """
    meetings = get_todays_meetings()
    all_rows = []
    going_map = {}

    for meeting in meetings:
        course = meeting["course"]
        going = meeting["going"]
        going_map[course] = going

        for race in meeting["races"]:
            stage = race.get("stage", "")
            time = race.get("time", "")
            name = race.get("name", "")
            slug = race.get("slug")

            if not slug:
                continue  # No racecard link available

            runners = get_race_runners(slug)

            # ── Fetch Betfair BSP data for this race (cached per race) ──────────
            bsp_race_key = f"{course}|{time}"
            bsp_race_data = None
            if bsp_race_key not in _bsp_race_cache:
                try:
                    bsp_cli = _get_bsp_client()
                    if bsp_cli:
                        bsp_race_data = bsp_cli.get_race_bsp(course, time)
                        _bsp_race_cache[bsp_race_key] = bsp_race_data
                except Exception:
                    _bsp_race_cache[bsp_race_key] = None
            else:
                bsp_race_data = _bsp_race_cache[bsp_race_key]

            for rn in runners:
                if rn.get("status") == "NON_RUNNER":
                    continue

                odds_str = rn.get("odds", "N/A")

                # ── Enrich runner with BSP signal ─────────────────────────────
                bsp_result = None
                if bsp_race_data:
                    try:
                        bsp_cli = _get_bsp_client()
                        if bsp_cli:
                            bsp_result = bsp_cli.score_bsp_signal(
                                rn["horse"], odds_str, bsp_race_data
                            )
                    except Exception:
                        pass

                # ── Derive confidence using full ML model (or proxy if unavailable) ──
                if MODEL_AVAILABLE and _odds_model is not None:
                    runner_input = {
                        "odds":          odds_str,
                        "form":          rn.get("form", "-"),
                        "going":         going,
                        "trainer":       rn.get("trainer", ""),
                        "jockey":        rn.get("jockey", ""),
                        "signal":        rn.get("signal", "Stable"),
                        "bet_movements": rn.get("bet_movements", []),
                        "tf_stars":      rn.get("tf_stars"),
                        "course":        course,
                        "bsp_result":    bsp_result,    # NEW: Betfair BSP signal
                    }
                    confidence = _odds_model.calculate_confidence(runner_input)
                else:
                    confidence = _estimate_confidence(odds_str, rn.get("tf_stars"), rn.get("rating"))

                # BSP display fields
                bsp_price  = bsp_result.get("bsp_price")    if bsp_result else None
                bsp_flag   = bsp_result.get("value_flag")   if bsp_result else ""
                bsp_vol    = bsp_result.get("vol_signal")   if bsp_result else ""
                bsp_matched= bsp_result.get("total_matched")if bsp_result else None

                all_rows.append({
                    "Race":          f"{time} {course}",
                    "Horse":         rn["horse"],
                    "Jockey":        rn["jockey"],
                    "Trainer":       rn["trainer"],
                    "Form":          rn["form"],
                    "Going":         going,
                    "Odds":          odds_str,
                    "Confidence":    confidence,
                    "Signal":        rn["signal"],
                    "TF Stars":      rn.get("tf_stars", "-"),
                    "Stage":         stage,
                    "Cloth":         rn.get("cloth", "-"),
                    "Draw":          rn.get("draw", "-"),
                    "Finish":        rn.get("finish_position"),
                    "BSP Price":     bsp_price,
                    "BSP Flag":      bsp_flag,
                    "BSP Volume":    bsp_vol,
                    "BSP Matched":   bsp_matched,
                })

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df = df.sort_values("Confidence", ascending=False)
    return df


def get_going_reports():
    """Returns going for all UK/Irish meetings today."""
    meetings = get_todays_meetings()
    rows = []
    for m in meetings:
        rows.append({
            "Course": m["course"],
            "Going": m["going"],
            "Races": len(m["races"]),
            "Source": "Sporting Life / BHA",
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
                if rn.get("status") == "NON_RUNNER":
                    nrs.append({
                        "Race": f"{race['time']} {meeting['course']}",
                        "Horse": rn["horse"],
                        "Jockey": rn["jockey"],
                        "Trainer": rn["trainer"],
                        "Reason": "Declared non-runner",
                        "Source": "Sporting Life",
                        "Time": datetime.now().strftime("%H:%M"),
                    })
    return nrs


def get_todays_results():
    """Returns results for races already run today."""
    meetings = get_todays_meetings()
    results = []
    for meeting in meetings:
        for race in meeting["races"]:
            if race.get("stage") not in ("WEIGHEDIN", "RESULT"):
                continue
            slug = race.get("slug")
            if not slug:
                continue
            runners = get_race_runners(slug)
            finishers = sorted(
                [r for r in runners if r.get("finish_position")],
                key=lambda x: x["finish_position"]
            )
            if finishers:
                winner = finishers[0]
                results.append({
                    "Race": f"{race['time']} {meeting['course']}",
                    "Winner": winner["horse"],
                    "Jockey": winner["jockey"],
                    "Trainer": winner["trainer"],
                    "Odds": winner["odds"],
                    "Going": meeting["going"],
                    "Source": "Sporting Life",
                })
    return pd.DataFrame(results) if results else pd.DataFrame()


def _estimate_confidence(odds_str, tf_stars=None, rating=None):
    """
    Derives a confidence score (0-1) from market odds + Timeform stars + rating.
    This is the pre-model proxy used until the full ML model is wired in.
    """
    # Convert odds to implied probability
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

    # Boost from Timeform stars (0-5 scale → 0-0.1 bonus)
    tf_boost = 0.0
    try:
        tf_boost = min(int(tf_stars), 5) * 0.02
    except Exception:
        pass

    # Boost from rating123 (1-3 scale → 0.0-0.05 bonus)
    rating_boost = 0.0
    try:
        rating_boost = (3 - int(rating)) * 0.025
    except Exception:
        pass

    confidence = min(implied + tf_boost + rating_boost, 0.97)
    return round(confidence, 3)
