# Racing Engine — BHA Official Ratings Loader
# Version: 2.6.8 — Real BHA OR lookup replaces 1-5 star Timeform proxy
#
# WEEKLY REFRESH:
# Call refresh_bha_ratings() every Tuesday — BHA updates every Tuesday morning.
# Can be wired to a cron or called manually from the operator brief. We do NOT
# install a new cron here; the function is just made available.
#
# DATA SHAPE:
# learning/bha_ratings_lookup.json keys are lowercased horse names *with*
# the country suffix included (e.g. "mahler moon (ire)"). Values:
#     {"flat": 85, "awt": 72, "chase": 110, "hurdle": 95, "trainer": "..."}
# Irish/French horses rated by IHRB/AAHO won't appear here — get_bha_or
# returns None for those and callers must fail gracefully to neutral 0.50.

import csv
import json
import os
import re
import urllib.request

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
LOOKUP_PATH = os.path.join(_THIS_DIR, "bha_ratings_lookup.json")
CSV_PATH = os.path.join(_THIS_DIR, "bha_ratings.csv")

_BHA_PAGE_URL = "https://www.britishhorseracing.com/racing/horses/ratings/"
_USER_AGENT = "Mozilla/5.0 (compatible; racing-engine/2.6.8)"

# In-process cache so we don't reload on every runner.
_LOOKUP_CACHE: dict | None = None

# Country suffixes that may follow a horse name.
_COUNTRY_SUFFIXES = (
    "(gb)", "(ire)", "(fr)", "(usa)", "(ger)", "(ity)", "(spa)", "(arg)",
    "(aus)", "(nz)", "(saf)", "(jpn)", "(can)", "(uae)", "(ind)", "(tur)",
    "(swi)", "(bel)", "(den)", "(swe)", "(nor)", "(pol)", "(hun)", "(cze)",
    "(brz)", "(chi)", "(per)", "(ury)",
)


def load_bha_ratings(path: str = LOOKUP_PATH) -> dict:
    """Load BHA ratings lookup. Returns {horse_name_lower: {flat, awt, chase, hurdle}}."""
    global _LOOKUP_CACHE
    if _LOOKUP_CACHE is not None and path == LOOKUP_PATH:
        return _LOOKUP_CACHE
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if path == LOOKUP_PATH:
        _LOOKUP_CACHE = data
    return data


def _normalise_name(name: str) -> str:
    if not name:
        return ""
    return str(name).strip().lower()


def _strip_country_suffix(name: str) -> str:
    n = name
    for s in _COUNTRY_SUFFIXES:
        if n.endswith(s):
            return n[: -len(s)].strip()
    return n


def _race_type_key(race_type: str) -> str:
    """Map a free-form race_type to one of: flat, awt, chase, hurdle."""
    rt = (race_type or "").strip().lower()
    if "chase" in rt:
        return "chase"
    if "hurdle" in rt:
        return "hurdle"
    if "awt" in rt or "all weather" in rt or "all-weather" in rt or "polytrack" in rt or "tapeta" in rt:
        return "awt"
    return "flat"


def get_bha_or(horse_name: str, race_type: str = "flat") -> int | None:
    """
    Look up a horse's BHA Official Rating.

    race_type: 'flat', 'awt', 'chase', 'hurdle' (free-form strings tolerated).
    Returns int OR or None if not found.

    Lookup order:
      1. exact lowercase match
      2. with each country suffix appended
      3. base name (suffix stripped) → exact
      4. fuzzy: any key whose stripped base equals our stripped base
    """
    if not horse_name:
        return None
    lookup = load_bha_ratings()
    if not lookup:
        return None
    rt_key = _race_type_key(race_type)

    raw = _normalise_name(horse_name)
    base = _strip_country_suffix(raw)

    candidates = []
    if raw in lookup:
        candidates.append(lookup[raw])
    else:
        for s in _COUNTRY_SUFFIXES:
            k = f"{base} {s}" if base else s
            if k in lookup:
                candidates.append(lookup[k])
                break
        if not candidates and base in lookup:
            candidates.append(lookup[base])

    if not candidates:
        # Last-resort fuzzy: same stripped base.
        for k, v in lookup.items():
            if _strip_country_suffix(k) == base:
                candidates.append(v)
                break

    for c in candidates:
        try:
            v = c.get(rt_key)
            if v in (None, "", 0):
                continue
            return int(v)
        except Exception:
            continue
    # Fall back to flat if specific surface missing — better than None for UI.
    if rt_key != "flat":
        for c in candidates:
            try:
                v = c.get("flat")
                if v not in (None, "", 0):
                    return int(v)
            except Exception:
                continue
    return None


def _build_lookup_from_csv(csv_path: str = CSV_PATH) -> dict:
    """Rebuild the lookup dict from the BHA CSV."""
    lookup: dict = {}
    if not os.path.exists(csv_path):
        return lookup
    with open(csv_path, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("Name") or "").strip()
            if not name:
                continue
            key = name.lower()
            entry: dict = {"trainer": (row.get("Trainer") or "").strip()}
            for surface, col in (("flat", "Flat rating"),
                                 ("awt", "AWT rating"),
                                 ("chase", "Chase rating"),
                                 ("hurdle", "Hurdle rating")):
                v = (row.get(col) or "").strip()
                try:
                    iv = int(v)
                    if iv > 0:
                        entry[surface] = iv
                except Exception:
                    continue
            if any(k in entry for k in ("flat", "awt", "chase", "hurdle")):
                lookup[key] = entry
    return lookup


def _save_lookup(lookup: dict, path: str = LOOKUP_PATH) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lookup, f)
    os.replace(tmp, path)


def refresh_bha_ratings() -> dict:
    """
    Re-download BHA ratings from britishhorseracing.com and rebuild the lookup.

    Steps:
      1. GET the ratings page HTML.
      2. Extract the signed CSV download URL via regex.
      3. Download the CSV to learning/bha_ratings.csv.
      4. Rebuild the lookup JSON in place.

    Failures fall back silently — existing lookup remains usable. Returns a
    short status dict so a caller (operator brief) can surface the result.
    """
    global _LOOKUP_CACHE
    status: dict = {"ok": False, "rows": 0, "error": None}
    try:
        req = urllib.request.Request(_BHA_PAGE_URL, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # The CSV link is a signed S3 URL; pick the first href ending .csv.
        m = re.search(r'href=["\']([^"\']+\.csv[^"\']*)["\']', html)
        if not m:
            status["error"] = "no_csv_link"
            return status
        csv_url = m.group(1)
        if csv_url.startswith("//"):
            csv_url = "https:" + csv_url
        elif csv_url.startswith("/"):
            csv_url = "https://www.britishhorseracing.com" + csv_url
        req2 = urllib.request.Request(csv_url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req2, timeout=60) as resp2:
            csv_bytes = resp2.read()
        with open(CSV_PATH, "wb") as f:
            f.write(csv_bytes)
        lookup = _build_lookup_from_csv(CSV_PATH)
        if lookup:
            _save_lookup(lookup, LOOKUP_PATH)
            _LOOKUP_CACHE = lookup
            status["ok"] = True
            status["rows"] = len(lookup)
        else:
            status["error"] = "empty_lookup"
    except Exception as e:
        status["error"] = str(e)
    return status


if __name__ == "__main__":
    # Smoke test.
    for q, rt, expected in (
        ("mahler moon", "chase", 106),
        ("the gay blade", "flat", 57),
        ("faiyum", "flat", None),
    ):
        got = get_bha_or(q, rt)
        print(f"{q!r} ({rt}) -> {got} (expected {expected})")
