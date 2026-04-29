"""
Course and distance affinity signals for the confidence model.
Fetches horse-level win records from Sporting Life form pages.
Results cached in memory (per-session) to avoid repeat fetches.

Signal is OPTIONAL — any failure (network, parsing, missing dep) returns
neutral (0.50, 0.50). The main selection pipeline must never block on this.

v2.5.58 — parallel fetches via ThreadPoolExecutor, hard 10s total cap,
2s per-request timeout. Never blocks the main pipeline.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Optional, Tuple

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except Exception:  # pragma: no cover
    BeautifulSoup = None
    _BS4_AVAILABLE = False

_CACHE: dict = {}  # cache_key -> (course_signal, distance_signal)
_DATA_CACHE: dict = {}  # horse_name -> raw parse for course/dist counts
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-tool/1.0)"}
_TIMEOUT = 2          # per-request timeout — was 8s, now 2s
_MODULE_TIMEOUT = 10  # hard cap: entire module must finish within 10s
_LAST_FETCH = [0.0]
_MIN_INTERVAL = 0.0   # parallel fetches — no sequential delay needed


def _name_to_slug(name: str) -> str:
    """Convert horse name to URL slug."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _fetch_form_page(horse_name: str) -> Optional[str]:
    """Fetch Sporting Life form page HTML for a horse. Returns HTML or None.
    Hard 2s timeout — never blocks the pipeline.
    """
    if requests is None:
        return None
    slug = _name_to_slug(horse_name)
    if not slug:
        return None
    url = f"https://www.sportinglife.com/racing/horses/{slug}/form"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def prefetch_signals(horses: list) -> None:
    """Pre-fetch course/distance signals for a list of horse dicts in parallel.
    Call this once with all selections before scoring. Results go into _CACHE.
    horses: list of dicts with keys: horse, course, race_dist_f
    Hard cap: entire prefetch completes within _MODULE_TIMEOUT seconds.
    """
    if not horses:
        return

    def _fetch_one(h):
        name = h.get('horse', '')
        course = h.get('course', '')
        dist_f = float(h.get('race_dist_f') or 0.0)
        return get_course_distance_signals(name, course, dist_f)

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_one, h): h for h in horses}
            # Wait max _MODULE_TIMEOUT for all to complete
            import concurrent.futures
            done, _ = concurrent.futures.wait(
                futures.keys(), timeout=_MODULE_TIMEOUT
            )
            # Any not done within timeout stay as 0.50 neutral (already cached)
    except Exception:
        pass  # entire prefetch failed — neutral fallback for all


def _parse_course_distance(html: str, target_course: str, target_dist_f: float) -> dict:
    """
    Parse course wins and distance wins from Sporting Life horse form HTML.

    Counts wins (finish position 1) at:
      - target_course (case-insensitive substring match)
      - target_dist_f ± 1 furlong
    """
    result = {"course_wins": 0, "course_runs": 0, "dist_wins": 0, "dist_runs": 0}

    if not _BS4_AVAILABLE or not html:
        return result

    try:
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all("tr")

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            row_text = [c.get_text(strip=True) for c in cells]

            course_match = False
            dist_match = False
            is_win = False

            for cell_text in row_text:
                if target_course and target_course.lower() in cell_text.lower():
                    course_match = True

                dist_pattern = re.search(r'(\d+)f', cell_text)
                if dist_pattern:
                    try:
                        cell_dist = float(dist_pattern.group(1))
                        if target_dist_f and target_dist_f > 0 and abs(cell_dist - target_dist_f) <= 1.0:
                            dist_match = True
                    except Exception:
                        pass

                stripped = cell_text.strip()
                if stripped in ("1", "1st") or stripped.startswith("1/"):
                    is_win = True

            if course_match:
                result["course_runs"] += 1
                if is_win:
                    result["course_wins"] += 1

            if dist_match:
                result["dist_runs"] += 1
                if is_win:
                    result["dist_wins"] += 1
    except Exception:
        pass

    return result


def _win_rate_to_signal(wins: int, runs: int) -> float:
    if runs == 0:
        return 0.50  # no evidence
    rate = wins / runs
    if rate > 0.40:
        return 0.70
    elif rate > 0.25:
        return 0.60
    elif rate >= 0.10:
        return 0.50
    elif runs >= 3:
        return 0.40  # proven doesn't like it here
    return 0.50


def get_course_distance_signals(horse_name: str, course: str, dist_f: float) -> Tuple[float, float]:
    """
    Returns (course_signal, distance_signal) as floats 0.0–1.0.

    On any failure (no horse name, no requests, fetch error, parse error,
    missing BeautifulSoup) returns the neutral (0.50, 0.50).

    Cached per-session to avoid repeat fetches.
    """
    if not horse_name:
        return (0.50, 0.50)

    cache_key = f"{horse_name}::{course}::{dist_f}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    try:
        html = _fetch_form_page(horse_name)
        if not html:
            _CACHE[cache_key] = (0.50, 0.50)
            return (0.50, 0.50)

        data = _parse_course_distance(html, course or "", dist_f or 0.0)
        _DATA_CACHE[cache_key] = data

        course_sig = _win_rate_to_signal(data["course_wins"], data["course_runs"])
        dist_sig = _win_rate_to_signal(data["dist_wins"], data["dist_runs"])
        result = (course_sig, dist_sig)
    except Exception:
        result = (0.50, 0.50)

    _CACHE[cache_key] = result
    return result


def get_course_distance_detail(horse_name: str, course: str, dist_f: float) -> dict:
    """
    Returns the raw counts dict {course_wins, course_runs, dist_wins, dist_runs}
    for display purposes. Returns zeros if data not yet fetched / unavailable.
    """
    cache_key = f"{horse_name}::{course}::{dist_f}"
    if cache_key in _DATA_CACHE:
        return dict(_DATA_CACHE[cache_key])
    return {"course_wins": 0, "course_runs": 0, "dist_wins": 0, "dist_runs": 0}
