# Racing Engine — Betfair BSP Fetcher
# Version: 1.0
# Date: 21 April 2026
#
# What this does:
#   - Logs into Betfair's free Exchange API using the delay key (1Bj49mxBZBQ961WM)
#   - Looks up the WIN market for any UK/IRE horse race by course + time
#   - Fetches: BSP projection price (pre-race implied SP), last traded price,
#              total volume matched, runner status (active/removed/winner)
#
# Why it matters:
#   - Betfair BSP is generally a better-calibrated price than bookmaker SP
#     because it reflects the sum of thousands of punters' money, not a
#     bookmaker's margin-inflated price
#   - Large gaps between Betfair price and bookmaker price = potential value
#   - Total volume matched = liquidity signal (more money = more informed market)
#   - BSP projection (near/far price) = where the market THINKS the SP will land
#     BEFORE the race — a real-time "smart money" signal
#
# Free API limitations with delay key:
#   - Data is delayed ~1 second (not an issue for pre-race use)
#   - No live in-play data
#   - listMarketBook, listMarketCatalogue, listEvents all available
#   - BSP projection (SP_PROJECTED) is available pre-race
#   - Actual BSP (SP_TRADED) available post-race / at reconciliation

import requests
import json
import re
from datetime import datetime, timedelta, timezone

# Betfair REST API endpoints
LOGIN_URL   = "https://identitysso.betfair.com/api/login"
API_URL     = "https://api.betfair.com/exchange/betting/rest/v1.0"

UK_IRE_COUNTRIES = ["GB", "IE"]


class BetfairBSP:
    """
    Fetches BSP projection and exchange data for UK/IRE horse races.
    Uses the free delay API key — no Betfair account balance required.

    Usage:
        bsp = BetfairBSP(app_key, username, password)
        if bsp.login():
            data = bsp.get_race_bsp("Pontefract", "13:17")
    """

    def __init__(self, app_key: str, username: str = "", password: str = ""):
        self.app_key  = app_key
        self.username = username
        self.password = password
        self.session_token = None
        self._headers = {}

    # ── Authentication ────────────────────────────────────────────────────────

    def login(self) -> bool:
        """
        Log in to Betfair and retrieve a session token.
        Returns True if successful.
        Betfair credentials must be provided (username/password).
        """
        if not self.username or not self.password:
            print("[BetfairBSP] No credentials provided — BSP signals unavailable.")
            return False
        try:
            resp = requests.post(LOGIN_URL, data={
                "username": self.username,
                "password": self.password,
            }, headers={
                "X-Application": self.app_key,
                "Accept": "application/json",
            }, timeout=10)
            data = resp.json()
            if data.get("status") == "SUCCESS":
                self.session_token = data["token"]
                self._headers = {
                    "X-Application": self.app_key,
                    "X-Authentication": self.session_token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                return True
            else:
                print(f"[BetfairBSP] Login failed: {data.get('error', 'Unknown error')}")
                return False
        except Exception as e:
            print(f"[BetfairBSP] Login error: {e}")
            return False

    def _post(self, endpoint: str, payload: dict) -> dict:
        """Low-level API call."""
        try:
            r = requests.post(
                f"{API_URL}/{endpoint}/",
                headers=self._headers,
                data=json.dumps(payload),
                timeout=10,
            )
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    # ── Market Discovery ──────────────────────────────────────────────────────

    def find_race_market(self, course: str, race_time: str, date_str: str = None) -> str | None:
        """
        Find the Betfair WIN market ID for a specific race.

        Args:
            course: e.g. "Pontefract"
            race_time: e.g. "13:17"
            date_str: "YYYY-MM-DD", defaults to today

        Returns:
            marketId string (e.g. "1.234567890") or None
        """
        if not self.session_token:
            return None

        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        # Build a time window: ±10 minutes around the race time
        try:
            race_dt = datetime.strptime(f"{date_str} {race_time}", "%Y-%m-%d %H:%M")
            race_dt = race_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

        from_dt = (race_dt - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        to_dt   = (race_dt + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

        payload = {
            "filter": {
                "eventTypeIds": ["7"],  # 7 = Horse Racing
                "marketCountries": UK_IRE_COUNTRIES,
                "marketTypeCodes": ["WIN"],
                "venues": [course],
                "marketStartTime": {
                    "from": from_dt,
                    "to": to_dt,
                }
            },
            "marketProjection": ["RUNNER_METADATA", "MARKET_START_TIME", "EVENT"],
            "maxResults": 5,
        }

        result = self._post("listMarketCatalogue", payload)
        markets = result if isinstance(result, list) else []

        if markets:
            return markets[0].get("marketId")
        return None

    def get_runners_for_market(self, market_id: str) -> dict:
        """
        Given a market ID, fetch all runner names and their selectionIds.
        Returns {runner_name: selection_id} mapping.
        """
        if not self.session_token or not market_id:
            return {}

        payload = {
            "filter": {"marketIds": [market_id]},
            "marketProjection": ["RUNNER_METADATA"],
            "maxResults": 1,
        }
        result = self._post("listMarketCatalogue", payload)
        markets = result if isinstance(result, list) else []
        if not markets:
            return {}

        runner_map = {}
        for runner in markets[0].get("runners", []):
            name = runner.get("runnerName", "")
            sel_id = runner.get("selectionId")
            meta = runner.get("metadata", {})
            # Betfair sometimes stores cloth number in metadata
            runner_map[name.upper()] = {
                "selection_id": sel_id,
                "cloth_number": meta.get("CLOTH_NUMBER", ""),
                "colour": meta.get("COLOUR_TYPE", ""),
                "sex": meta.get("SEX_TYPE", ""),
                "sire": meta.get("SIRE_NAME", ""),
                "dam": meta.get("DAM_NAME", ""),
                "official_rating": meta.get("OFFICIAL_RATING", ""),
                "form": meta.get("FORM", ""),
                "owner": meta.get("OWNER_NAME", ""),
            }
        return runner_map

    # ── Price Data ────────────────────────────────────────────────────────────

    def get_market_book(self, market_id: str) -> list:
        """
        Fetch the current market book for a given market ID.
        Returns list of runner data with BSP projection, exchange prices,
        total volume matched, and status.
        """
        if not self.session_token or not market_id:
            return []

        payload = {
            "marketIds": [market_id],
            "priceProjection": {
                "priceData": ["EX_BEST_OFFERS", "SP_TRADED", "EX_TRADED"],
                "exBestOfferOverRides": {
                    "bestPricesDepth": 3,
                    "rollupModel": "STAKE",
                    "rollupLimit": 10,
                },
                "sp": True,
            },
            "orderProjection": "NONE",
            "matchProjection": "NO_ROLLUP",
        }

        result = self._post("listMarketBook", payload)
        markets = result if isinstance(result, list) else []
        if not markets:
            return []

        return markets[0].get("runners", [])

    # ── Main Public Method ────────────────────────────────────────────────────

    def get_race_bsp(self, course: str, race_time: str, date_str: str = None) -> dict:
        """
        Full BSP data fetch for a race.

        Returns dict with:
          market_id: str
          runners: list of {
              selection_id, horse_name (if catalogue loaded),
              bsp_near: float  (projected near SP)
              bsp_far: float   (projected far SP)
              bsp_actual: float or None (only after race settles)
              last_price_traded: float
              total_matched: float
              best_back: float  (best available back price)
              best_lay: float   (best available lay price)
              status: str       (ACTIVE / WINNER / LOSER / REMOVED)
              volume_signal: str  (High / Medium / Low based on total matched)
              bsp_vs_sp: float  (% difference vs bookmaker SP — filled later)
          }
        """
        market_id = self.find_race_market(course, race_time, date_str)
        if not market_id:
            return {"market_id": None, "runners": [], "note": "Market not found"}

        runner_catalogue = self.get_runners_for_market(market_id)
        runners_book = self.get_market_book(market_id)

        # Invert catalogue for lookup by selection_id
        id_to_name = {v["selection_id"]: k for k, v in runner_catalogue.items()}

        runners_out = []
        for r in runners_book:
            sel_id = r.get("selectionId")
            sp = r.get("sp", {})
            ex = r.get("ex", {})

            bsp_near   = sp.get("nearPrice")
            bsp_far    = sp.get("farPrice")
            bsp_actual = sp.get("actualSP")
            ltp        = r.get("lastPriceTraded")
            matched    = r.get("totalMatched", 0)
            status     = r.get("status", "ACTIVE")

            # Best back/lay from exchange ladder
            backs = ex.get("availableToBack", [])
            lays  = ex.get("availableToLay", [])
            best_back = backs[0]["price"] if backs else None
            best_lay  = lays[0]["price"]  if lays  else None

            # Volume signal thresholds (£ matched)
            if matched > 50000:
                vol_sig = "High"
            elif matched > 10000:
                vol_sig = "Medium"
            else:
                vol_sig = "Low"

            runners_out.append({
                "selection_id":      sel_id,
                "horse_name":        id_to_name.get(sel_id, f"Runner {sel_id}"),
                "bsp_near":          round(bsp_near, 2)   if bsp_near   else None,
                "bsp_far":           round(bsp_far, 2)    if bsp_far    else None,
                "bsp_actual":        round(bsp_actual, 2) if bsp_actual else None,
                "last_price_traded": round(ltp, 2)        if ltp        else None,
                "total_matched":     round(matched, 0),
                "best_back":         round(best_back, 2)  if best_back  else None,
                "best_lay":          round(best_lay, 2)   if best_lay   else None,
                "status":            status,
                "volume_signal":     vol_sig,
            })

        return {
            "market_id":  market_id,
            "course":     course,
            "race_time":  race_time,
            "runners":    runners_out,
        }

    def score_bsp_signal(self, horse_name: str, bookmaker_odds_str: str,
                         bsp_data: dict) -> dict:
        """
        Compare Betfair BSP projection vs bookmaker odds to generate a value signal.

        A horse is VALUE if:
          - Betfair BSP near-price < bookmaker decimal odds  (Betfair is shorter = smarter market backs it)
          - OR total matched volume is HIGH (confidence in price)

        Returns:
          bsp_score: float 0-1 (higher = better Betfair signal)
          value_flag: "Value" / "Fair" / "Overpriced"
          note: explanation string
        """
        # Convert bookmaker odds to decimal
        def to_dec(s):
            try:
                s = str(s).strip().lower()
                if s in ("evs", "evens"):
                    return 2.0
                if "/" in s:
                    n, d = s.split("/")
                    return round(float(n) / float(d) + 1, 4)
                return float(s)
            except Exception:
                return None

        bk_dec = to_dec(bookmaker_odds_str)

        # Find this horse in BSP data
        horse_upper = horse_name.upper().strip()
        runner_bsp = None
        for r in bsp_data.get("runners", []):
            if r.get("horse_name", "").upper() == horse_upper:
                runner_bsp = r
                break

        if not runner_bsp or not bk_dec:
            return {"bsp_score": 0.50, "value_flag": "Unknown", "note": "No BSP data"}

        bsp_price = runner_bsp.get("bsp_near") or runner_bsp.get("last_price_traded")
        if not bsp_price:
            return {"bsp_score": 0.50, "value_flag": "Unknown", "note": "No BSP price"}

        # Value calculation:
        # If BSP < bookmaker odds → market rates it shorter than bookie → value
        # If BSP > bookmaker odds → bookie is shorter → overpriced by market
        ratio = bsp_price / bk_dec  # >1 = bookie shorter (value to us); <1 = exchange shorter

        if ratio >= 1.15:
            score = 0.72
            flag  = "Value"
            note  = f"BSP {bsp_price:.2f} vs bookie {bk_dec:.2f} — exchange backs it"
        elif ratio >= 0.95:
            score = 0.55
            flag  = "Fair"
            note  = f"BSP {bsp_price:.2f} approx matches bookie — fairly priced"
        else:
            score = 0.30
            flag  = "Overpriced"
            note  = f"BSP {bsp_price:.2f} shorter than bookie {bk_dec:.2f} — market more cautious"

        # Volume boost: high volume = more confidence in the BSP signal
        if runner_bsp.get("volume_signal") == "High":
            score = min(score + 0.08, 0.95)

        return {
            "bsp_score":   round(score, 3),
            "value_flag":  flag,
            "note":        note,
            "bsp_price":   bsp_price,
            "bk_dec":      bk_dec,
            "vol_signal":  runner_bsp.get("volume_signal", "Unknown"),
            "total_matched": runner_bsp.get("total_matched", 0),
        }
