# Racing Engine — Data Ingestion Layer
# Version: 0.5
# Date: 20 April 2026
# Free data feeds wired in — no paid APIs required for Phase 1

import requests
import pandas as pd
from datetime import datetime, date
from bs4 import BeautifulSoup
import io

# ── Betfair SP (Starting Price) Free CSV Feed ─────────────────
# Betfair publishes free daily SP data for all UK/IRE markets
# URL pattern: https://promo.betfair.com/betfairsp/prices/dwbfprices{country}win{DD}{MM}{YYYY}.csv

class BetfairSPFeed:
    """
    Free Betfair Starting Price data.
    Published daily for UK and Irish win + place markets.
    No API key required.
    """
    BASE_URL = "https://promo.betfair.com/betfairsp/prices"

    def get_daily_sp(self, target_date: date = None, country: str = "uk") -> pd.DataFrame:
        """
        Fetch SP data for a given date and country (uk or ire).
        Returns DataFrame with horse names, BSP, and win/place market data.
        """
        if target_date is None:
            target_date = date.today()

        dd = target_date.strftime("%d")
        mm = target_date.strftime("%m")
        yyyy = target_date.strftime("%Y")
        country_code = "gbr" if country == "uk" else "ire"

        url = f"{self.BASE_URL}/dwbfprices{country_code}win{dd}{mm}{yyyy}.csv"

        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                df = pd.read_csv(io.StringIO(response.text))
                df["country"] = country.upper()
                df["date"] = target_date
                return df
            else:
                print(f"[BetfairSP] No data for {target_date} ({country}): HTTP {response.status_code}")
                return pd.DataFrame()
        except Exception as e:
            print(f"[BetfairSP] Error: {e}")
            return pd.DataFrame()

    def get_todays_sp(self) -> pd.DataFrame:
        """Fetch today's SP for both UK and Irish racing."""
        uk = self.get_daily_sp(country="uk")
        ire = self.get_daily_sp(country="ire")
        return pd.concat([uk, ire], ignore_index=True)


# ── Sporting Life — Non-Runners Feed ─────────────────────────
# Free public page updated in real time throughout the day

class NonRunnersFeed:
    """
    Fetches today's non-runners from Sporting Life.
    Free, no authentication required.
    Updates throughout the day as declarations are made.
    """
    URL = "https://www.sportinglife.com/racing/non-runners"

    def get_todays_non_runners(self) -> list:
        """
        Returns list of today's non-runners with race details.
        """
        headers = {"User-Agent": "Mozilla/5.0 (compatible; RacingEngine/1.0)"}
        try:
            response = requests.get(self.URL, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, "html.parser")

            non_runners = []
            # Parse non-runner entries from the page
            entries = soup.find_all("tr", class_=lambda x: x and "non-runner" in x.lower())
            for entry in entries:
                cells = entry.find_all("td")
                if len(cells) >= 2:
                    non_runners.append({
                        "horse": cells[0].get_text(strip=True),
                        "race": cells[1].get_text(strip=True) if len(cells) > 1 else "",
                        "reason": cells[2].get_text(strip=True) if len(cells) > 2 else "",
                        "source": "Sporting Life",
                        "timestamp": datetime.now().isoformat()
                    })

            print(f"[NonRunners] Found {len(non_runners)} non-runners today")
            return non_runners

        except Exception as e:
            print(f"[NonRunners] Error: {e}")
            return []


# ── BHA — Going Reports & Official Non-Runners ───────────────
# British Horseracing Authority — free official data

class BHAFeed:
    """
    Fetches official going reports and non-runners from the BHA.
    Free public data — the official source for UK racing conditions.
    """
    GOING_URL = "https://www.britishhorseracing.com/racing/going-reports/"
    NR_URL = "https://www.britishhorseracing.com/racing/non-runners/"

    def get_going_reports(self) -> list:
        """Fetch latest going reports for all UK courses."""
        headers = {"User-Agent": "Mozilla/5.0 (compatible; RacingEngine/1.0)"}
        try:
            response = requests.get(self.GOING_URL, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, "html.parser")

            reports = []
            rows = soup.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 2:
                    reports.append({
                        "course": cells[0].get_text(strip=True),
                        "going": cells[1].get_text(strip=True),
                        "updated": cells[2].get_text(strip=True) if len(cells) > 2 else "",
                        "source": "BHA Official",
                        "timestamp": datetime.now().isoformat()
                    })

            print(f"[BHA] Found {len(reports)} going reports")
            return reports

        except Exception as e:
            print(f"[BHA Going] Error: {e}")
            return []


# ── At The Races — Results Feed ───────────────────────────────
# Free results published rapidly after each race

class ATRResultsFeed:
    """
    Fetches race results from At The Races.
    Free, covers UK and Irish racing.
    Results published within seconds of the race finishing.
    """
    BASE_URL = "https://www.attheraces.com/results"

    def get_todays_results(self) -> list:
        """Fetch all results so far today."""
        headers = {"User-Agent": "Mozilla/5.0 (compatible; RacingEngine/1.0)"}
        try:
            response = requests.get(self.BASE_URL, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, "html.parser")

            results = []
            race_blocks = soup.find_all("div", class_=lambda x: x and "result" in x.lower())
            for block in race_blocks:
                race_info = block.find("h2") or block.find("h3")
                winner = block.find("li")
                if race_info and winner:
                    results.append({
                        "race": race_info.get_text(strip=True),
                        "winner": winner.get_text(strip=True),
                        "source": "At The Races",
                        "timestamp": datetime.now().isoformat()
                    })

            print(f"[ATR Results] Found {len(results)} results today")
            return results

        except Exception as e:
            print(f"[ATR Results] Error: {e}")
            return []


# ── GG.co.uk — Fast Results Feed ─────────────────────────────
# Free rapid results feed, one of the fastest in the industry

class GGResultsFeed:
    """
    Fetches fast race results from GG.co.uk.
    Free, covers UK and Irish racing.
    Updates within seconds of each race finishing.
    """
    URL = "https://gg.co.uk/results/today/"

    def get_todays_results(self) -> list:
        """Fetch today's results — first 4 finishers per race."""
        headers = {"User-Agent": "Mozilla/5.0 (compatible; RacingEngine/1.0)"}
        try:
            response = requests.get(self.URL, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, "html.parser")

            results = []
            races = soup.find_all("div", class_=lambda x: x and "race" in x.lower())
            for race in races:
                race_title = race.find("h2") or race.find("h3")
                finishers = race.find_all("li")
                if race_title:
                    results.append({
                        "race": race_title.get_text(strip=True),
                        "finishers": [f.get_text(strip=True) for f in finishers[:4]],
                        "source": "GG.co.uk",
                        "timestamp": datetime.now().isoformat()
                    })

            print(f"[GG Results] Found {len(results)} race results today")
            return results

        except Exception as e:
            print(f"[GG Results] Error: {e}")
            return []


# ── Betfair Exchange API — Delayed Market Data ────────────────
# Free developer key — delayed odds and market movements

class BetfairFeed:
    """
    Uses the free Betfair delay key to pull market data.
    Covers live odds, market volumes, and price movements.
    Delay key: 1Bj49mxBZBQ961WM (dev/delayed — free)
    """
    APP_KEY = "1Bj49mxBZBQ961WM"
    BASE_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

    def get_racing_markets(self, country_codes: list = ["GB", "IE"]) -> list:
        """
        Fetch today's horse racing markets from Betfair Exchange.
        Returns market IDs, names, start times and current odds.
        """
        # To be connected with session token in v0.6
        # Requires Betfair username/password login first
        pass


# ── Master Data Manager ───────────────────────────────────────
# Orchestrates all feeds into a single unified data object

class DataManager:
    """
    Central controller for all data feeds.
    Pulls from every free source and combines into
    a unified dataset for the odds model and dashboard.
    """

    def __init__(self):
        self.betfair_sp   = BetfairSPFeed()
        self.non_runners  = NonRunnersFeed()
        self.bha          = BHAFeed()
        self.atr_results  = ATRResultsFeed()
        self.gg_results   = GGResultsFeed()

    def get_full_daily_feed(self) -> dict:
        """
        Pulls all available free data for today.
        Returns unified dict ready for the odds model.
        """
        print(f"\n[DataManager] Pulling all feeds — {datetime.now().strftime('%H:%M:%S')}")

        return {
            "betfair_sp":   self.betfair_sp.get_todays_sp(),
            "non_runners":  self.non_runners.get_todays_non_runners(),
            "going_reports": self.bha.get_going_reports(),
            "atr_results":  self.atr_results.get_todays_results(),
            "gg_results":   self.gg_results.get_todays_results(),
            "pulled_at":    datetime.now().isoformat()
        }
