# Racing Engine — Daily Brief Generator + Email System
# Version: 2.0
# Date: 21 April 2026
#
# EMAIL TYPES:
#   1. Morning Brief      08:00 BST — today's official selections + staking plan
#   2. Result Alert       instant   — fired when a selection result is known
#   3. Evening Summary    19:00 BST — full day P&L, results vs selections, learning notes
#   4. Market Alert       instant   — significant steam/drift on a selection
#
# DESIGN RULES:
#   - Only OFFICIAL selections shown (cleared threshold + 4/6 cut-off on live engine)
#   - No sample/fallback data — if live feed is down, email says so clearly
#   - No Accumulator Permutations section — covered in dashboard
#   - No Going Reports section — only shown if a selection's going changes
#   - Concise — every email fits on one mobile screen without scrolling

import smtplib, os, sys, zoneinfo
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, date, timedelta

_LONDON = zoneinfo.ZoneInfo("Europe/London")

# ── Config ─────────────────────────────────────────────────────
RECIPIENT = "richardking123@outlook.com"

def _get_secret(key, default=""):
    try:
        import streamlit as st
        return st.secrets.get(key, os.getenv(key, default))
    except Exception:
        return os.getenv(key, default)

SENDER_EMAIL    = _get_secret("SENDER_EMAIL",        "racingengine.sender@gmail.com")
SENDER_PASSWORD = _get_secret("SENDER_APP_PASSWORD", "aase pwst fcbf smfs")

def _now_bst() -> str:
    return datetime.now(_LONDON).strftime("%H:%M")

def _date_bst() -> str:
    return datetime.now(_LONDON).strftime("%A %d %B %Y")

def _to_decimal(odds_str) -> float:
    try:
        s = str(odds_str).strip()
        if "/" in s:
            n, d = s.split("/")
            return round((float(n) + float(d)) / float(d), 3)
        return round(float(s), 3)
    except Exception:
        return 2.0


def _clean_str_or_none(v):
    """v2.5.62 — return None for nan/None/empty so emails skip the field cleanly."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "n/a"):
        return None
    return s


def _clean_price_or_none(v):
    """v2.5.62 — return float price or None. nan/None/<2.0 → None (no display)."""
    if v is None:
        return None
    s = str(v).strip().lower()
    if not s or s in ("nan", "none", "n/a"):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN check
        return None
    return f


def _coerce_list(v):
    """v2.6.1 — return a list. Handles None, pandas NA/NaN, scalar, list."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    # pandas NA / NaN: scalar nan != itself; pd.NA raises on bool
    try:
        if v != v:  # NaN check
            return []
    except (TypeError, ValueError):
        return []
    if isinstance(v, tuple):
        return list(v)
    return []


def _coerce_int_or_none(v):
    """v2.6.1 — return int or None. Handles pandas NA, NaN, '', '-'."""
    if v is None:
        return None
    try:
        if v != v:  # NaN
            return None
    except (TypeError, ValueError):
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "n/a", "-"):
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None

# ── Live Data Helpers ──────────────────────────────────────────
def _get_overnight_moves(today: str = None) -> list:
    """
    Returns ALL horses that have moved significantly since yesterday's show prices
    (across the entire card, not just official selections).
    Pulls from the show_price_snapshot baseline via get_previous_day_moves().
    Also refreshes the snapshot first if it was missed at 15:30 the day before.
    Returns empty list if no baseline exists.
    """
    try:
        from dashboard.early_market import (
            refresh_show_snapshot_if_empty,
            get_previous_day_moves,
        )
        refresh_show_snapshot_if_empty()
        movers = get_previous_day_moves(min_move_pct=0.30)
        if not movers or (isinstance(movers[0], dict) and "error" in movers[0]):
            return []
        return movers
    except Exception as e:
        print(f"[Brief] Overnight moves unavailable: {e}")
        return []


def _get_official_selections(conf_threshold: float = 0.50) -> list:
    # Calibration threshold — review after 2 weeks of live data
    """
    Returns only official selections: cleared threshold + evens cut-off
    on the live engine. No fallback — returns empty list if feed is down.

    Drift auto-drop (v2.5.42): horses whose current price has drifted
    >20% out from the morning baseline (show-price snapshot) are
    silently excluded — never reach staking. Excluded horses are
    written to learning/drift_excluded.json for the app to display
    as "DRIFTED — excluded".
    """
    try:
        from dashboard.live_data import get_todays_selections
        from engine.odds_model import OddsModel
        import zoneinfo as _zi

        df = get_todays_selections()
        if df is None or len(df) == 0:
            return []

        model    = OddsModel()
        now_bst  = datetime.now(_zi.ZoneInfo("Europe/London")).strftime("%H:%M")
        out      = []
        drift_excluded_log = []  # populated as we exclude drifters

        # Load morning-price baseline (show-price snapshot) for drift detection.
        # Snapshot is keyed by lower(horse)|lower(course)|time.
        try:
            _morning_prices = _load_show_price_snapshot()
        except Exception:
            _morning_prices = {}

        # ── Market position lookup (v2.5.50 — info, not filter) ──────────────
        # Oddschecker consensus is the source of truth for market position.
        # Fav = lowest consensus_decimal in the race. Falls back to SL feed
        # when OC is unavailable. Market position is NOT a selection gate —
        # it's information attached to every selection card.
        _race_fav_price_brief = {}     # race_key -> fav decimal
        _race_fav_name_brief  = {}     # race_key -> fav horse name
        _race_runners_brief   = {}     # race_key -> list of {horse, trainer}
        _race_all_prices_brief = {}    # race_key -> list of decimals
        _race_oc_data         = {}     # race_key -> {horse_lower: consensus_decimal}
        _race_oc_source       = {}     # race_key -> "oc" | "sl"

        # SL-based pre-pass — provides fallback when OC is unavailable
        _sl_fav_price = {}
        _sl_fav_name  = {}
        _sl_all_prices = {}
        for _, _fr in df.iterrows():
            _frkey = f"{str(_fr.get('Time',''))}::{str(_fr.get('Course',''))}"
            _frodds = str(_fr.get('Current Odds','') or _fr.get('Odds','N/A')).strip()
            try:
                _frdec = _to_decimal(_frodds)
            except Exception:
                _frdec = 99.0
            if _frdec > 1.0:
                if _frkey not in _sl_fav_price or _frdec < _sl_fav_price[_frkey]:
                    _sl_fav_price[_frkey] = _frdec
                    _sl_fav_name[_frkey]  = str(_fr.get('Horse', ''))
                _sl_all_prices.setdefault(_frkey, []).append(_frdec)
            _race_runners_brief.setdefault(_frkey, []).append({
                "horse":   str(_fr.get("Horse", "")),
                "trainer": str(_fr.get("Trainer", "")),
            })

        # Fetch Oddschecker per unique race and build OC fav lookup
        try:
            from engine.oddschecker import get_oddschecker_odds as _oc_fetch
        except Exception:
            _oc_fetch = None

        _race_keys = sorted(_sl_all_prices.keys())
        for _rk in _race_keys:
            _t, _c = _rk.split("::", 1)
            _oc = {}
            if _oc_fetch:
                try:
                    _oc = _oc_fetch(_c, _t) or {}
                except Exception as _oce:
                    print(f"[Brief] OC fetch failed {_c} {_t}: {_oce}")
                    _oc = {}
            if _oc:
                # Build {horse_lower: consensus_decimal} map for this race
                _consensus_map = {}
                for _hname, _entry in _oc.items():
                    try:
                        _cdec = float(_entry.get("consensus_decimal") or 0.0)
                    except Exception:
                        _cdec = 0.0
                    if _cdec > 1.0:
                        _consensus_map[_hname.strip().lower()] = _cdec
                if _consensus_map:
                    _race_oc_data[_rk] = _consensus_map
                    _prices = sorted(_consensus_map.values())
                    _fav_name = min(_consensus_map.items(), key=lambda kv: kv[1])[0]
                    # Recover original-cased name from OC keys
                    _orig_name = next(
                        (h for h in _oc.keys() if h.strip().lower() == _fav_name),
                        _fav_name,
                    )
                    _race_fav_price_brief[_rk] = _prices[0]
                    _race_fav_name_brief[_rk]  = _orig_name
                    _race_all_prices_brief[_rk] = _prices
                    _race_oc_source[_rk] = "oc"
                    continue
            # Fallback: SL feed
            _race_fav_price_brief[_rk] = _sl_fav_price.get(_rk, 99.0)
            _race_fav_name_brief[_rk]  = _sl_fav_name.get(_rk, "")
            _race_all_prices_brief[_rk] = sorted(_sl_all_prices.get(_rk, []))
            _race_oc_source[_rk] = "sl"

        # Compute 2nd-fav price per race for Yorkshire Glory / dominant-fav logic
        _race_second_fav_price = {}
        for _rk, _prices in _race_all_prices_brief.items():
            _sorted = sorted(_prices)
            if len(_sorted) >= 2:
                _race_second_fav_price[_rk] = _sorted[1]

        for _, row in df.iterrows():
            t = str(row.get("Time", ""))

            curr_str = str(row.get("Current Odds", "")).strip()
            _odds_raw = str(row.get("Odds", "")).strip()
            odds_str = curr_str if curr_str and curr_str.lower() not in ("", "n/a", "none", "nan") \
                       else _odds_raw

            # v2.5.62 — hard price-validation gate. If neither Current Odds nor
            # Odds gives a usable number, exclude the horse entirely. Previously
            # _to_decimal() silently defaulted to 2.0 on parse error, which let
            # L'Evangeliste through with "Best: nan" and into Bet A.
            _horse_log = str(row.get("Horse", ""))
            _odds_clean = (odds_str or "").strip().lower()
            if not _odds_clean or _odds_clean in ("n/a", "none", "nan"):
                print(f"[Gate] {_horse_log} excluded — invalid price: {odds_str!r}")
                continue
            # Parse manually so we never silently default to 2.0
            try:
                if "/" in odds_str:
                    _n, _d = odds_str.split("/")
                    dec = round((float(_n) + float(_d)) / float(_d), 3)
                else:
                    dec = round(float(odds_str), 3)
            except (TypeError, ValueError):
                print(f"[Gate] {_horse_log} excluded — unparsable price: {odds_str!r}")
                continue
            if not dec or dec < 2.0:
                # v2.5.53 evens cut-off (was 4/6 / 1.67)
                continue

            # v2.5.50 — market position is information, not a filter.
            # We compute the selection's OC consensus price for use as the
            # "effective" decimal in market-position comparisons below.
            _bracekey = f"{t}::{str(row.get('Course',''))}"
            _bfav_dec = _race_fav_price_brief.get(_bracekey, dec)
            _oc_map = _race_oc_data.get(_bracekey, {})
            _our_oc_dec = _oc_map.get(str(row.get('Horse','')).strip().lower(), 0.0) if _oc_map else 0.0
            _our_eff_dec = _our_oc_dec if (_our_oc_dec and _our_oc_dec > 1.0) else dec

            # Drift auto-drop — silent exclusion if current price has drifted
            # >20% out from the morning baseline (show-price snapshot).
            # Drift signals the market thinks worse of the horse since 15:30 BST
            # the day before — staking through it has historically lost money.
            _horse_str  = str(row.get("Horse", ""))
            _course_str = str(row.get("Course", ""))
            _morn_key   = (f"{_horse_str.lower().strip()}|"
                           f"{_course_str.lower().strip()}|"
                           f"{t.strip()}")
            _morn_dec   = _morning_prices.get(_morn_key, 0.0)
            if _morn_dec and _morn_dec > 1.0 and dec > _morn_dec * 1.20:
                _drift_pct = (dec - _morn_dec) / _morn_dec * 100.0
                print(f"[Brief] Drift-excluded {_horse_str} @ {dec:.2f}x "
                      f"(morning {_morn_dec:.2f}x, drift {_drift_pct:+.1f}%)")
                drift_excluded_log.append({
                    "horse":        _horse_str,
                    "course":       _course_str,
                    "time":         t,
                    "morning_odds": round(_morn_dec, 2),
                    "current_odds": round(dec, 2),
                    "drift_pct":    round(_drift_pct, 1),
                    "drift_excluded": True,
                })
                continue

            runner = {
                "horse":        str(row.get("Horse", "")),
                "course":       str(row.get("Course", "")),
                "odds":         str(row.get("Odds", "N/A")),
                "current_odds": odds_str,
                "form":         str(row.get("Form", "-")),
                "going":        str(row.get("Going", "")),
                "trainer":      str(row.get("Trainer", "")),
                "jockey":       str(row.get("Jockey", "")),
                "signal":       str(row.get("Signal", "Stable")),
                "tf_stars":     row.get("TF Stars"),
                "bet_movements": [],
                "field_size":   int(row.get("Field Size", 0) or 0),
                "is_handicap":  bool(row.get("Is Handicap", False)),
                "race_type":    str(row.get("Race Type", "") or "").strip(),
                "race_dist_f":  float(row.get("Race Dist F", 0) or 0),
                # v2.6.1 — pass v2.6.0 signal fields through to scoring
                "previous_results":     _coerce_list(row.get("Previous Results")),
                "race_history_stats":   _coerce_list(row.get("Race History Stats")),
                "rating123":            _coerce_int_or_none(row.get("Rating123")),
                "last_ran_days":        _coerce_int_or_none(row.get("Last Ran Days")),
                "all_ratings_in_race":  _coerce_list(row.get("All Ratings In Race")),
                "race_class":           str(row.get("Race Class", "") or ""),
            }

            # Hard filter layer
            exclude, _ = model.should_exclude(runner)
            if exclude:
                continue

            # Handicap uplift
            effective_threshold = model.get_handicap_threshold(runner, conf_threshold)
            conf = model.calculate_confidence(runner)
            if conf < effective_threshold:
                continue

            # v2.5.50 — market position computed as INFORMATION attached to
            # the selection. None of these flags exclude the horse — they are
            # surfaced in the email/app so the user sees market context.
            is_fav     = _our_eff_dec <= _bfav_dec + 1e-9
            fav_price  = round(float(_bfav_dec), 2)
            fav_name   = _race_fav_name_brief.get(_bracekey, "")
            _second_fav_dec = _race_second_fav_price.get(_bracekey)
            second_fav_price = round(float(_second_fav_dec), 2) if _second_fav_dec else 0.0

            # Market position rank by OC consensus (1 = fav). Falls back to SL.
            _race_prices = _race_all_prices_brief.get(_bracekey, []) or []
            _sorted_prices = sorted(_race_prices)
            market_position = 1
            for _i, _p in enumerate(_sorted_prices, start=1):
                if abs(_p - _our_eff_dec) < 1e-6:
                    market_position = _i
                    break
            else:
                # Our price not in the sorted list — count how many are shorter
                market_position = sum(1 for _p in _sorted_prices if _p < _our_eff_dec) + 1
            if market_position == 1:
                market_position_label = "FAV"
            elif market_position == 2:
                market_position_label = "2ND FAV"
            elif market_position == 3:
                market_position_label = "3RD FAV"
            else:
                market_position_label = f"{market_position}TH FAV"

            # Gap to 2nd-fav (only meaningful when we ARE the fav).
            if is_fav and second_fav_price > 0:
                gap_to_2nd = round((second_fav_price - _our_eff_dec) / _our_eff_dec, 4)
            else:
                gap_to_2nd = 0.0

            _runners = int(row.get("Field Size", row.get("Runners", 0)) or 0)

            # Dominant rival: any horse priced at < 60% of our consensus price
            # (i.e. they are 40%+ shorter than us — major market edge against us).
            dominant_rival = False
            dominant_rival_name = ""
            dominant_rival_price = 0.0
            if _our_eff_dec > 1.0:
                _threshold = _our_eff_dec * 0.60
                for _hname_lc, _hdec in (_oc_map or {}).items():
                    if _hdec < _threshold and _hname_lc != str(row.get('Horse','')).strip().lower():
                        if (not dominant_rival) or _hdec < dominant_rival_price:
                            dominant_rival = True
                            dominant_rival_price = round(float(_hdec), 2)
                            # Recover original-cased name from any source available
                            dominant_rival_name = _hname_lc.title()
                # If we're not the fav and fav < 0.60 * our price, fav is the rival
                if (not dominant_rival) and (not is_fav) and fav_price > 0 \
                        and fav_price < _threshold:
                    dominant_rival = True
                    dominant_rival_name = fav_name
                    dominant_rival_price = fav_price

            # Yorkshire Glory risk: open competitive field — field >= 10 AND
            # the actual market leader is bigger than 4.0 (no dominant fav).
            yg_risk = bool(_runners >= 10 and fav_price > 4.0)

            # Split market: we ARE the fav but 2nd fav is within 20% of our price.
            split_market = bool(is_fav and second_fav_price > 0 and gap_to_2nd < 0.20)

            # is_dominant_fav retained for backwards compat with downstream code
            is_dominant_fav = bool(is_fav and gap_to_2nd >= 0.50)

            # Low acca value: thin field OR odds-on price (≤1.85) — v2.5.35
            _low_thin_field = (_runners > 0 and _runners <= 4)
            _low_odds_on    = (dec <= 1.85)
            _low_value_acca = _low_thin_field or _low_odds_on
            _low_reason     = (
                "thin field" if _low_thin_field
                else ("odds-on price" if _low_odds_on else "")
            )

            # Top-trainer-in-race warning (warning flag only, never auto-excludes)
            _rival_flag = {"rival_top_trainer": False, "rival_trainer_name": ""}
            try:
                from engine.staking import detect_rival_top_trainer as _detect_rival
                _rival_flag = _detect_rival(
                    str(row.get("Horse", "")),
                    _race_runners_brief.get(_bracekey, []),
                )
            except Exception:
                pass

            out.append({
                "time":        t,
                "course":      str(row.get("Course", "")),
                "horse":       str(row.get("Horse", "")),
                "odds":        str(row.get("Odds", odds_str)),
                "curr_odds":   odds_str,
                "decimal":     round(dec, 2),
                "confidence":  round(conf, 3),
                "signal":      str(row.get("Signal", "Stable")),
                "going":       str(row.get("Going", "")),
                "race_name":   str(row.get("Race Name", row.get("Race", "")) or ""),
                "runners":     _runners,
                "low_value_acca":  _low_value_acca,
                "low_value_reason": _low_reason,
                "race_type":   str(row.get("Race Type", "") or "").strip(),
                "race_class":  str(row.get("Race Class", "") or "").strip(),
                "form":        str(row.get("Form", "-")),
                "trainer":     str(row.get("Trainer", "")),
                "jockey":      str(row.get("Jockey", "")),
                "tf_stars":    row.get("TF Stars"),
                "is_handicap": bool(row.get("Is Handicap", False)),
                "is_fav":      is_fav,
                "fav_price":   fav_price,
                "fav_name":    fav_name,
                "second_fav_price": second_fav_price,
                "gap_to_2nd":       gap_to_2nd,
                "is_dominant_fav":  is_dominant_fav,
                "yg_risk":          yg_risk,
                "split_market":     split_market,
                # v2.5.50 — market position information
                "market_position":       market_position,
                "market_position_label": market_position_label,
                "actual_fav_name":       fav_name,
                "actual_fav_price":      fav_price,
                "dominant_rival":        dominant_rival,
                "dominant_rival_name":   dominant_rival_name,
                "dominant_rival_price":  dominant_rival_price,
                "rival_top_trainer":  _rival_flag.get("rival_top_trainer", False),
                "rival_trainer_name": _rival_flag.get("rival_trainer_name", ""),
                "role":        ("BANKER" if (conf >= 0.63 and dec <= 4.00) else "VALUE"),
                "tier":        ("BANKER" if dec <= 2.50 else
                                "MID"    if dec <= 5.00 else
                                "VALUE"  if dec <= 10.0 else "LONGSHOT"),
                # Oddschecker multi-bookie fields (v2.5.40) — may be None.
                # v2.5.62 — scrub nan/None/empty so emails never render "Best: nan".
                "best_odds_decimal":    _clean_price_or_none(row.get("Best Odds Decimal")),
                "best_odds_fractional": _clean_str_or_none(row.get("Best Odds Fractional")),
                "best_bookmaker":       row.get("Best Bookmaker", "") or "",
                "odds_consensus":       _clean_price_or_none(row.get("Odds Consensus")),
                "bookmaker_count":      row.get("Bookmaker Count"),
                # v2.5.55 — course specialist + distance affinity
                "course_signal":   float(row.get("Course Signal", 0.50) or 0.50),
                "distance_signal": float(row.get("Distance Signal", 0.50) or 0.50),
                "course_wins":     int(row.get("Course Wins", 0) or 0),
                "course_runs":     int(row.get("Course Runs", 0) or 0),
                "distance_wins":   int(row.get("Distance Wins", 0) or 0),
                "distance_runs":   int(row.get("Distance Runs", 0) or 0),
                "race_dist_f":     float(row.get("Race Dist F", 0) or 0),
                # v2.6.0 — feed fields for "why selected" line in emails
                # v2.6.1 — coerce pandas NA/scalar safely so downstream gets list/int|None
                "previous_results":     _coerce_list(row.get("Previous Results")),
                "race_history_stats":   _coerce_list(row.get("Race History Stats")),
                "rating123":            _coerce_int_or_none(row.get("Rating123")),
                "last_ran_days":        _coerce_int_or_none(row.get("Last Ran Days")),
                "all_ratings_in_race":  _coerce_list(row.get("All Ratings In Race")),
            })

        out.sort(key=lambda x: x["confidence"], reverse=True)

        # ── ONE HORSE PER RACE RULE ───────────────────────────────────────────
        # An accumulator requires independent legs. Two horses in the same race
        # are correlated — only one can win. Take the highest-confidence selection
        # per race. Flag the second horse so the user knows it was considered.
        seen_races = {}
        filtered_out = []
        one_per_race = []
        for s in out:
            race_key = f"{s['time']}::{s['course']}"
            if race_key not in seen_races:
                seen_races[race_key] = s
                one_per_race.append(s)
            else:
                # Already have a horse for this race — keep only the higher confidence
                existing = seen_races[race_key]
                if s["confidence"] > existing["confidence"]:
                    # Replace existing with this one
                    one_per_race = [x for x in one_per_race if not (x["time"]==existing["time"] and x["course"]==existing["course"])]
                    one_per_race.append(s)
                    seen_races[race_key] = s
                    filtered_out.append(existing)
                else:
                    filtered_out.append(s)
                print(f"[Brief] One-per-race: dropped {filtered_out[-1]['horse']} from {race_key} (kept higher conf)")
        out = one_per_race
        out.sort(key=lambda x: x["time"])

        # ── HARD NR GATE — final check before any selection reaches the email ──
        # Runs fresh on EVERY call (no caching). Case-insensitive comparison:
        # normalise both sides to uppercase so feed variants ("NONRUNNER",
        # "NonRunner", "non_runner") can never leak through — e.g. the Milteye
        # incident (15:22 Beverley) where a case mismatch allowed a non-runner
        # into the selection list.
        try:
            from dashboard.live_data import get_non_runners as _gnr
            _nr_rows  = _gnr()  # fresh pull, no cache
            _nr_names = {str(nr.get('Horse', '')).strip().upper() for nr in _nr_rows}
            _before   = len(out)
            _kept = []
            for s in out:
                _hname = str(s.get('horse', '')).strip().upper()
                if _hname in _nr_names:
                    print(f"[NR Gate] Stripped {s.get('horse','?')} — status: NONRUNNER "
                          f"(race {s.get('time','?')} {s.get('course','?')})")
                    continue
                _kept.append(s)
            out = _kept
            _dropped = _before - len(out)
            if _dropped:
                print(f"[Brief] NR gate removed {_dropped} non-runner(s) from selections")
        except Exception as _nr_err:
            print(f"[Brief] NR gate warning: {_nr_err}")

        # Persist drift exclusions so the dashboard can show "DRIFTED — excluded"
        if drift_excluded_log:
            try:
                import json as _jdrf
                _drf_path = os.path.join(
                    os.path.dirname(__file__), "..", "learning",
                    "drift_excluded.json",
                )
                _today = datetime.now(_zi.ZoneInfo("Europe/London")).strftime("%Y-%m-%d")
                _drf_payload = {"date": _today, "horses": drift_excluded_log}
                os.makedirs(os.path.dirname(_drf_path), exist_ok=True)
                with open(_drf_path, "w") as _drf_f:
                    _jdrf.dump(_drf_payload, _drf_f, indent=2)
            except Exception as _drf_err:
                print(f"[Brief] Drift log write warning: {_drf_err}")

        return out
    except Exception as e:
        print(f"[Brief] Selections unavailable: {e}")
        return []


def _get_going() -> list:
    """Returns going description for every UK/Irish meeting today."""
    try:
        from dashboard.live_data import get_todays_meetings
        meetings = get_todays_meetings()
        return [{"course": m["course"], "going": m["going"], "races": len(m["races"])}
                for m in meetings]
    except Exception:
        return []


def _get_todays_results() -> list:
    """Returns settled races from today's results feed — strictly today only.

    Defence-in-depth: even though get_todays_meetings() already filters to today,
    we stamp each row with today's ISO date on ingest and strip rows that
    cannot be matched to today. Prevents any upstream cache bleed from putting
    yesterday's results into the evening summary.
    """
    today_str = datetime.now(_LONDON).date().isoformat()
    print(f"[Evening] Fetching results for {today_str}")
    try:
        from dashboard.live_data import get_todays_results
        df = get_todays_results()
        if df is None or len(df) == 0:
            print(f"[Evening] No results returned for {today_str}")
            return []
        out = []
        for _, row in df.iterrows():
            out.append({
                "race":    str(row.get("Race", "")),
                "winner":  str(row.get("Winner", "")),
                "sp":      str(row.get("Odds", "")),
                "date":    today_str,
            })
        print(f"[Evening] Got {len(out)} results for {today_str}")
        return out
    except Exception as _err:
        print(f"[Evening] Results fetch failed for {today_str}: {_err}")
        return []


def _get_todays_results_from_feed(date_str: str) -> dict:
    """v2.6.4 — Direct-from-feed results lookup for evening summary fallback.

    Fetches Sporting Life's results page for date_str and parses __NEXT_DATA__.
    Returns dict keyed by race_id ({date}::{time}::{course}) with:
      { "winner": name, "odds": sp_fractional, "position": {horse_lower: pos} }

    Used when auto_settle() finds 0 open recommendations to settle — lets the
    evening summary cross-reference today's selections against the feed
    directly and compute P&L even when the learning loop is empty.
    """
    import requests, json as _json
    from bs4 import BeautifulSoup
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    }
    url = f"https://www.sportinglife.com/racing/results/{date_str}"
    out = {}
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"[Evening] Feed results fetch {date_str} status {r.status_code}")
            return out
        soup = BeautifulSoup(r.text, "html.parser")
        nd = soup.find("script", id="__NEXT_DATA__")
        if not nd:
            return out
        data = _json.loads(nd.get_text())
    except Exception as _e:
        print(f"[Evening] Feed results fetch failed {date_str}: {_e}")
        return out

    meetings = data.get("props", {}).get("pageProps", {}).get("meetings", []) or []
    for meeting in meetings:
        for race in meeting.get("races", []) or []:
            course = race.get("course_name", "")
            time_  = race.get("time", "")
            if not course or not time_:
                continue
            winner_name = ""
            winner_odds = ""
            positions = {}
            for th in race.get("top_horses", []) or []:
                try:
                    pos = int(th.get("position", 0))
                except Exception:
                    pos = 0
                hname = str(th.get("name", ""))
                if hname:
                    positions[hname.strip().lower()] = pos
                if pos == 1 and not winner_name:
                    winner_name = hname
                    winner_odds = str(th.get("starting_price", "") or "")
            race_id = f"{date_str}::{time_}::{course}"
            out[race_id] = {
                "winner":   winner_name,
                "odds":     winner_odds,
                "position": positions,
                "course":   course,
                "time":     time_,
            }
    return out


def _build_learning_update_block(today_str: str) -> str:
    """v2.6.4 — Render the LEARNING UPDATE section for the evening summary.

    Reports: today's recorded selections, how many were settled, running win
    rate, and a 14-day signal performance snapshot. Falls back to a "building
    data" notice when fewer than 10 results are logged.
    """
    import json as _json
    recs_path    = os.path.join(os.path.dirname(__file__), "..", "learning", "recommendations.json")
    results_path = os.path.join(os.path.dirname(__file__), "..", "learning", "results_store.json")

    try:
        recs_raw = _json.load(open(recs_path))
        recs = recs_raw.get("records", []) if isinstance(recs_raw, dict) else recs_raw
    except Exception:
        recs = []
    try:
        res_raw = _json.load(open(results_path))
        results = res_raw.get("results", []) if isinstance(res_raw, dict) else res_raw
    except Exception:
        results = []

    # Strip noise: 21 Apr "all_runners" backfill is not a real selection set.
    real_recs = [r for r in recs if r.get("source") != "all_runners"]

    today_recs = [r for r in real_recs if r.get("date") == today_str]
    today_settled = [r for r in today_recs if r.get("won") is not None]

    settled_all = [r for r in real_recs if r.get("won") is not None]
    wins_all    = [r for r in settled_all if r.get("won") is True]
    win_pct = (100.0 * len(wins_all) / len(settled_all)) if settled_all else 0.0

    # 14-day signal performance: per-signal SR when value > 0.65
    from datetime import date as _date, timedelta as _td
    cutoff = (_date.today() - _td(days=14)).isoformat()
    recent = [r for r in settled_all if (r.get("date") or "") >= cutoff]

    signal_keys = [
        ("going",           "Going preference"),
        ("course_signal",   "Course form"),
        ("distance_signal", "Distance form"),
    ]
    signal_lines = []
    for key, label in signal_keys:
        fired = [r for r in recent
                 if float((r.get("signals") or {}).get(key, 0) or 0) > 0.65]
        if fired:
            sr = 100.0 * sum(1 for r in fired if r.get("won") is True) / len(fired)
            signal_lines.append(
                f"<tr><td style='padding:4px 0;color:#888;font-size:12px;'>"
                f"{label}</td>"
                f"<td style='padding:4px 0;font-size:12px;color:#e0e0e0;'>"
                f"fired on {len(fired)} horses, {sr:.0f}% SR when high (&gt;0.65)</td></tr>"
            )
        else:
            signal_lines.append(
                f"<tr><td style='padding:4px 0;color:#888;font-size:12px;'>"
                f"{label}</td>"
                f"<td style='padding:4px 0;font-size:12px;color:#888;'>"
                f"no high signal firings in last 14 days</td></tr>"
            )

    if len(settled_all) < 10:
        body_html = (
            f"<div style='font-size:13px;color:#e0e0e0;margin-bottom:8px;'>"
            f"Selections recorded today: <b>{len(today_recs)}</b><br>"
            f"Results settled today: <b>{len(today_settled)}</b><br>"
            f"</div>"
            f"<div style='font-size:12px;color:#A1873B;'>"
            f"Building data — {len(settled_all)} settled results so far, "
            f"need ~50 for meaningful patterns.</div>"
        )
    else:
        body_html = (
            f"<table style='width:100%;border-collapse:collapse;margin-bottom:8px;'>"
            f"<tr><td style='padding:4px 0;color:#888;font-size:12px;'>Selections recorded today</td>"
            f"<td style='padding:4px 0;font-size:12px;color:#e0e0e0;'>{len(today_recs)}</td></tr>"
            f"<tr><td style='padding:4px 0;color:#888;font-size:12px;'>Results settled today</td>"
            f"<td style='padding:4px 0;font-size:12px;color:#e0e0e0;'>{len(today_settled)}</td></tr>"
            f"<tr><td style='padding:4px 0;color:#888;font-size:12px;'>Running win rate</td>"
            f"<td style='padding:4px 0;font-size:12px;color:#e0e0e0;'>"
            f"{win_pct:.0f}% ({len(wins_all)}/{len(settled_all)} selections logged)</td></tr>"
            f"</table>"
            f"<div style='font-size:12px;color:#888;margin-bottom:6px;'>"
            f"Signal performance (last 14 days):</div>"
            f"<table style='width:100%;border-collapse:collapse;'>"
            f"{''.join(signal_lines)}"
            f"</table>"
        )
    return _section("Learning Update", body_html, "#A1873B")


def _calc_staking(selections: list, budget: float = 100.0) -> dict:
    """
    Adaptive staking plan wrapper.
    Delegates to engine.staking.build_staking_plan() — returns a compatible dict
    for _staking_block() HTML renderer.
    Lucky 15 permanently removed — cover accumulators used instead at short prices.
    """
    if not selections:
        return {}
    try:
        from engine.staking import build_staking_plan
        plan = build_staking_plan(selections, budget=budget)
        # Expose all fields for 3-bet display
        combined_dec = plan["main_dec"]
        return {
            "budget":         budget,
            "acc_stake":      plan["main_stake"],
            "acc_return":     plan["main_return"],
            "acc_legs":       len(plan["main_pool"]),
            "combined_dec":   combined_dec,
            "l15_available":  False,
            "l15_stake":      0,
            "l15_per_bet":    0,
            "l15_horses":     0,
            # Core plan
            "plan_type":      plan["plan_type"],
            "plan_label":     plan["plan_label"],
            "plan_rationale": plan["plan_rationale"],
            "main_pool":      plan["main_pool"],
            "main_stake":     plan["main_stake"],
            "main_dec":       plan["main_dec"],
            "main_return":    plan["main_return"],
            # Cover accumulator (BET 2)
            "cover_pool":     plan["cover_pool"],
            "cover_stake":    plan["cover_stake"],
            "cover_dec":      plan["cover_dec"],
            "cover_return":   plan["cover_return"],
            # Value double (BET 3)
            "double_pool":    plan["double_pool"],
            "double_stake":   plan["double_stake"],
            "double_dec":     plan["double_dec"],
            "double_return":  plan["double_return"],
            # Legacy fields
            "covers":         plan["covers"],
            "cover_total":    plan["cover_total"],
            "speculative":    plan["speculative"],
            "scenarios":      plan["scenarios"],
        }
    except Exception as e:
        # Fallback: simple full accumulator
        combined_dec = 1.0
        for s in selections:
            combined_dec *= s["decimal"]
        return {
            "budget":        budget,
            "acc_stake":     budget,
            "acc_return":    round(budget * combined_dec, 2),
            "acc_legs":      len(selections),
            "combined_dec":  round(combined_dec, 1),
            "l15_available": False,
            "l15_stake":     0,
            "l15_per_bet":   0,
            "l15_horses":    0,
        }


# ── Shared Email Shell ─────────────────────────────────────────
def _get_version() -> str:
    """Read live version from app.py VERSION line."""
    try:
        app_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "app.py")
        with open(app_path) as f:
            for line in f:
                if line.strip().startswith("VERSION"):
                    return line.split('"')[1] if '"' in line else line.split("'")[1]
    except Exception:
        pass
    return "2.5"


def _email_shell(title: str, label_color: str, label_text: str, body_html: str) -> str:
    version = _get_version()
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#0f1117;font-family:Arial,sans-serif;color:#e0e0e0;">
<div style="max-width:640px;margin:0 auto;padding:16px;">

  <!-- Header -->
  <div style="background:#1c1f2e;border-radius:10px;padding:18px 20px;margin-bottom:14px;
              border-left:5px solid {label_color};">
    <div style="font-size:11px;font-weight:bold;color:{label_color};letter-spacing:1px;
                text-transform:uppercase;margin-bottom:4px;">{label_text}</div>
    <div style="font-size:18px;font-weight:bold;color:#ffffff;">{title}</div>
    <div style="font-size:12px;color:#888;margin-top:4px;">{_date_bst()} &nbsp;|&nbsp; {_now_bst()} BST</div>
  </div>

  {body_html}

  <!-- Footer -->
  <div style="text-align:center;color:#444;font-size:11px;padding:14px;">
    Racing Engine {version} &nbsp;|&nbsp; Phase 1 — Personal Research Tool &nbsp;|&nbsp;
    <a href="https://racing-engine-dash.streamlit.app" style="color:#01696F;">Dashboard (PIN: 1012)</a>
  </div>

</div></body></html>"""


def _section(title: str, content_html: str, border_color: str = "#01696F") -> str:
    return f"""
  <div style="background:#1c1f2e;border-radius:10px;padding:16px 18px;margin-bottom:12px;
              border-top:2px solid {border_color};">
    <div style="font-size:13px;font-weight:bold;color:#ffffff;margin-bottom:10px;
                text-transform:uppercase;letter-spacing:0.5px;">{title}</div>
    {content_html}
  </div>"""


def _moves_lookup(movers: list) -> dict:
    """Build a horse-name keyed dict of overnight move data for quick lookup."""
    out = {}
    for m in movers:
        key = m.get("horse", "").lower().strip()
        out[key] = m
    return out


def _sel_table(selections: list, movers: list = None) -> str:
    if not selections:
        return '<p style="color:#888;font-size:13px;margin:0;">No qualifying selections at this time.</p>'

    moves_map = _moves_lookup(movers or [])
    rows = ""
    for s in selections:
        conf_pct = int(s["confidence"] * 100)
        conf_col = "#437A22" if s["confidence"] >= 0.70 else "#01696F" if s["confidence"] >= 0.60 else "#964219"
        sig_col  = "#437A22" if any(x in s["signal"] for x in ["Steam","Move","⬆"]) \
                   else "#A13544" if "Drift" in s["signal"] else "#888"
        hcap_tag = ' <span style="font-size:10px;color:#964219;">[H]</span>' if s.get("is_handicap") else ""

        # Overnight move tag
        mv = moves_map.get(s["horse"].lower().strip())
        if mv:
            mv_dir   = mv["direction"]
            mv_pct   = mv["move_pct"]
            mv_from  = mv["baseline_odds"]
            mv_col   = "#437A22" if mv_dir == "STEAM" else "#A13544"
            mv_arrow = "⬆" if mv_dir == "STEAM" else "⬇"
            mv_tag   = (f' <span style="font-size:10px;font-weight:bold;color:{mv_col};"'
                        f'title="Show: {mv_from} → Now: {mv["current_odds"]}">'  
                        f'{mv_arrow}{mv_pct:.0f}% overnight</span>')
        else:
            mv_tag = ""

        # Favourite warning
        is_fav  = s.get("is_fav", True)
        fav_prc = s.get("fav_price", None)
        if not is_fav and fav_prc:
            fav_tag = (
                f'<br><span style="color:#e65c00;font-weight:bold;font-size:11px;">'
                f'⚠ NOT FAV — market fav @ {fav_prc:.2f}x</span>'
            )
        else:
            fav_tag = ""

        # Best-odds tag — multi-bookmaker price from Oddschecker (v2.5.40)
        _best_frac = s.get("best_odds_fractional")
        _best_bk   = s.get("best_bookmaker")
        _bk_count  = s.get("bookmaker_count")
        if _best_frac and _best_bk:
            oc_tag = (
                f'<br><span style="color:#4caf50;font-size:11px;">'
                f'Best: {_best_frac} @ {_best_bk}'
                f'{f" | {_bk_count} bookmakers" if _bk_count else ""}</span>'
            )
        else:
            oc_tag = ""

        # v2.5.55 — Course specialist + Distance affinity tags
        _cs   = float(s.get("course_signal",   0.50) or 0.50)
        _ds   = float(s.get("distance_signal", 0.50) or 0.50)
        _cw   = int(s.get("course_wins",   0) or 0)
        _crn  = int(s.get("course_runs",   0) or 0)
        _dw   = int(s.get("distance_wins", 0) or 0)
        _drn  = int(s.get("distance_runs", 0) or 0)
        cd_bits = []
        if _cs > 0.55:
            cd_bits.append(f'<span style="color:#4caf50;font-size:11px;">✓ Course ({_cw}/{_crn})</span>')
        elif _cs < 0.45 and _crn >= 3:
            cd_bits.append(f'<span style="color:#e65c00;font-size:11px;">⚠ No course form ({_cw}/{_crn})</span>')
        if _ds > 0.55:
            cd_bits.append(f'<span style="color:#4caf50;font-size:11px;">✓ Dist ({_dw}/{_drn})</span>')
        cd_tag = ("<br>" + " &nbsp; ".join(cd_bits)) if cd_bits else ""

        # Odds cell — show best decimal when present, falling back to SL current odds
        _best_dec = s.get("best_odds_decimal")
        if _best_dec:
            odds_cell = f'{s["curr_odds"]}<br><span style="color:#4caf50;font-size:11px;">best {_best_dec:.2f}x</span>'
        else:
            odds_cell = s["curr_odds"]

        rows += f"""<tr>
          <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;color:#888;">{s['time']}<br><span style="font-size:11px;">{s['course']}</span></td>
          <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;font-weight:bold;">{s['horse']}{hcap_tag}{mv_tag}{fav_tag}{oc_tag}{cd_tag}</td>
          <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;">{odds_cell}</td>
          <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;font-weight:bold;color:{conf_col};">{conf_pct}%</td>
          <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:12px;color:{sig_col};">{s['signal']}</td>
          <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:11px;color:#888;">{s['tier']}</td>
        </tr>"""

    return f"""<table style="width:100%;border-collapse:collapse;">
      <thead><tr style="color:#555;font-size:11px;text-transform:uppercase;">
        <th style="padding:5px 6px;text-align:left;">Time / Course</th>
        <th style="padding:5px 6px;text-align:left;">Horse</th>
        <th style="padding:5px 6px;text-align:left;">Odds</th>
        <th style="padding:5px 6px;text-align:left;">Conf</th>
        <th style="padding:5px 6px;text-align:left;">Signal</th>
        <th style="padding:5px 6px;text-align:left;">Tier</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>"""


def _fold_bets_block(fold_bets: dict) -> str:
    """v2.5.39 — 2-bet fold structure (Bet A core / Bet B extended) HTML block."""
    if not fold_bets or (not fold_bets.get("bet_a") and not fold_bets.get("bet_b")):
        return (
            '<p style="color:#888;font-size:13px;margin:0;">'
            'No qualifying fold bets today — fewer than 4 dominant-fav selections '
            '(gap to 2nd ≥50%, field &lt;10). Engine abstains.'
            '</p>'
        )

    def _bet_card(bet: dict, label_col: str, bet_key: str) -> str:
        horses = bet["horses"]
        dec    = bet["combined_decimal"]
        warns  = bet.get("warnings", [])

        leg_rows = ""
        for h in horses:
            yg_flag = ""
            if bool(h.get("yg_risk", False)):
                yg_flag = (
                    ' <span style="color:#e8a33d;font-weight:bold;">'
                    f'&#9888; Yorkshire Glory risk ({h.get("runners", 0)} runners, '
                    f'gap {h.get("gap_to_2nd", 0):.0%})</span>'
                )
            sm_flag = ""
            if bool(h.get("split_market", False)):
                sm_flag = (
                    ' <span style="color:#d9534f;font-weight:bold;">'
                    f'&#9888; SPLIT MARKET (2nd fav within '
                    f'{h.get("gap_to_2nd", 0):.0%})</span>'
                )
            leg_rows += f"""
          <tr>
            <td style="padding:4px 8px;font-size:12px;color:#aaa;white-space:nowrap;">{h.get('time','')} {h.get('course','')}</td>
            <td style="padding:4px 8px;font-size:13px;font-weight:bold;">{h.get('horse','')}{sm_flag}{yg_flag}</td>
            <td style="padding:4px 8px;font-size:12px;color:#aaa;">{h.get('curr_odds', h.get('odds','N/A'))} ({h.get('decimal',0):.2f}x)</td>
            <td style="padding:4px 8px;font-size:12px;color:#aaa;">{int(h.get('runners', 0) or 0)} run</td>
          </tr>"""

        ret_10  = dec * 10.0
        ret_20  = dec * 20.0
        ret_50  = dec * 50.0

        warn_html = ""
        if warns:
            warn_html = (
                '<p style="margin:6px 0 0;font-size:11px;color:#e8a33d;">'
                + " &middot; ".join(warns) + "</p>"
            )

        return f"""
        <div style="background:#16191d;border:1px solid {label_col};border-radius:4px;padding:10px 12px;margin-bottom:10px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <span style="color:{label_col};font-size:14px;font-weight:bold;">{bet_key} — {bet['label']}</span>
            <span style="color:#fff;font-size:14px;font-weight:bold;">{dec:.2f}x combined</span>
          </div>
          <table style="width:100%;border-collapse:collapse;">
            <tbody>{leg_rows}</tbody>
          </table>
          <div style="margin-top:8px;padding-top:6px;border-top:1px solid #2a2a2a;font-size:12px;color:#aaa;">
            Example returns: £10 &rarr; <span style="color:#fff;font-weight:bold;">£{ret_10:,.2f}</span>
            &nbsp;|&nbsp; £20 &rarr; <span style="color:#fff;font-weight:bold;">£{ret_20:,.2f}</span>
            &nbsp;|&nbsp; £50 &rarr; <span style="color:#fff;font-weight:bold;">£{ret_50:,.2f}</span>
          </div>
          {warn_html}
        </div>"""

    html_parts = [
        '<div style="background:#437A22;border-radius:4px;padding:8px 12px;margin-bottom:10px;">'
        '<span style="color:#fff;font-size:13px;font-weight:bold;">'
        '2-BET FOLD STRUCTURE (v2.5.39) — Bet A core only / Bet B extended with optional 5th leg'
        '</span></div>'
    ]

    if fold_bets.get("bet_a"):
        html_parts.append(_bet_card(fold_bets["bet_a"], "#437A22", "Bet A"))
    else:
        html_parts.append(
            '<p style="color:#888;font-size:12px;margin:0 0 10px;">'
            'Bet A unavailable — fewer than 4 dominant-fav selections today.'
            '</p>'
        )

    if fold_bets.get("bet_b"):
        html_parts.append(_bet_card(fold_bets["bet_b"], "#01696F", "Bet B"))
    else:
        html_parts.append(
            '<p style="color:#888;font-size:12px;margin:0;">'
            'Bet B unavailable — no qualifying 5th leg.'
            '</p>'
        )

    return "".join(html_parts)


def _staking_block(staking: dict) -> str:
    """3-Bet staking plan HTML block — BET 1 / BET 2 / BET 3 layout."""
    if not staking:
        return '<p style="color:#888;font-size:13px;margin:0;">No staking data.</p>'

    plan_type  = staking.get("plan_type", "FULL_ACC")
    plan_label = staking.get("plan_label", "Full Accumulator")
    rationale  = staking.get("plan_rationale", "")

    main_pool    = staking.get("main_pool",    [])
    main_stake   = staking.get("main_stake",   staking.get("acc_stake", 0))
    main_dec     = staking.get("main_dec",     staking.get("combined_dec", 1.0))
    main_return  = staking.get("main_return",  staking.get("acc_return", 0))

    cover_pool   = staking.get("cover_pool",   [])
    cover_stake  = staking.get("cover_stake",  0)
    cover_dec    = staking.get("cover_dec",    1.0)
    cover_return = staking.get("cover_return", 0)

    double_pool   = staking.get("double_pool",   [])
    double_stake  = staking.get("double_stake",  0)
    double_dec    = staking.get("double_dec",    1.0)
    double_return = staking.get("double_return", 0)

    budget       = staking.get("budget", 100)
    scenarios    = staking.get("scenarios", [])

    # v2.5.35 — 4+ banker mode changes structure labels
    four_banker_mode = len(main_pool) >= 4
    cover_type_lbl = "4-fold Cover" if four_banker_mode else "Cover Accumulator"
    value_type_lbl = (
        ("Value Single" if len(double_pool) == 1 else "Value Double")
        if four_banker_mode else "Value Double"
    )

    # ── Plan banner ──────────────────────────────────────────────────
    if plan_type == "THREE_BET" and four_banker_mode:
        banner_col = "#437A22"
        banner_txt = (
            f"3-BET (4+ bankers) — Main 50% + 4-fold Cover 30% + {value_type_lbl} 20% "
            f"| Doubles dropped, 4-folds promoted (backtest-driven)"
        )
    elif plan_type == "THREE_BET":
        banner_col = "#437A22"
        banner_txt = f"3-BET PLAN — Main Acc 60% + Cover Acc 25% + Value Double 15%"
    elif plan_type == "MAIN_COVER" and four_banker_mode:
        banner_col = "#964219"
        banner_txt = f"2-BET (4+ bankers) — Main Acc 50% + 4-fold Cover 30% (no value today)"
    elif plan_type == "MAIN_COVER":
        banner_col = "#964219"
        banner_txt = f"2-BET PLAN — Main Acc + Cover Acc (no value double today)"
    elif plan_type == "MAIN_ONLY":
        banner_col = "#01696F"
        banner_txt = f"MAIN ACCUMULATOR — bankers only, no cover or value leg"
    else:
        banner_col = "#888"
        banner_txt = f"FULL ACCUMULATOR — fallback plan"

    # ── BET 1: Main accumulator ──────────────────────────────────────
    main_horses = ", ".join(s["horse"] for s in main_pool) if main_pool else "—"
    bet1_html = f"""
      <tr style="background:#1a2a1a;">
        <td style="padding:8px 10px;font-size:13px;font-weight:bold;color:#437A22;white-space:nowrap;">BET 1</td>
        <td style="padding:8px 10px;font-size:12px;color:#aaa;">Main Accumulator</td>
        <td style="padding:8px 10px;font-size:13px;font-weight:bold;">£{main_stake:.2f}</td>
        <td style="padding:8px 10px;font-size:12px;color:#aaa;">{len(main_pool)}-fold @ {main_dec:.1f}x</td>
        <td style="padding:8px 10px;font-size:13px;font-weight:bold;color:#437A22;">£{main_return:,.2f}</td>
      </tr>
      <tr>
        <td colspan="5" style="padding:2px 10px 8px;font-size:11px;color:#666;">{main_horses}</td>
      </tr>"""

    # ── BET 2: Cover accumulator ─────────────────────────────────────
    if cover_pool:
        cover_horses = ", ".join(s["horse"] for s in cover_pool)
        if four_banker_mode:
            _omit_note = " — top 4 bankers by confidence"
        else:
            _main_names  = {s["horse"] for s in main_pool}
            _cover_names = {s["horse"] for s in cover_pool}
            _omitted_h   = _main_names - _cover_names
            _omit_note   = f" — omits {', '.join(_omitted_h)} (riskiest leg)" if _omitted_h else ""
        bet2_html = f"""
      <tr style="background:#1a221a;">
        <td style="padding:8px 10px;font-size:13px;font-weight:bold;color:#01696F;white-space:nowrap;">BET 2</td>
        <td style="padding:8px 10px;font-size:12px;color:#aaa;">{cover_type_lbl}{_omit_note}</td>
        <td style="padding:8px 10px;font-size:13px;font-weight:bold;">£{cover_stake:.2f}</td>
        <td style="padding:8px 10px;font-size:12px;color:#aaa;">{len(cover_pool)}-fold @ {cover_dec:.1f}x</td>
        <td style="padding:8px 10px;font-size:13px;font-weight:bold;color:#01696F;">£{cover_return:,.2f}</td>
      </tr>
      <tr>
        <td colspan="5" style="padding:2px 10px 8px;font-size:11px;color:#666;">{cover_horses}</td>
      </tr>"""
    else:
        bet2_html = f"""
      <tr>
        <td style="padding:8px 10px;font-size:13px;font-weight:bold;color:#555;white-space:nowrap;">BET 2</td>
        <td colspan="4" style="padding:8px 10px;font-size:12px;color:#555;">Cover Acc — not applicable today (bankers only)</td>
      </tr>"""

    # ── BET 3: Value leg (double or single in 4+ banker mode) ───────
    if double_pool:
        double_horses = " + ".join(s["horse"] for s in double_pool)
        double_odds   = " / ".join(s.get("odds_str", f"{s['decimal']:.2f}x") for s in double_pool)
        _shape_lbl    = ("Single" if len(double_pool) == 1
                         else f"{len(double_pool)}-fold" if len(double_pool) != 2 else "Double")
        bet3_html = f"""
      <tr style="background:#1a1a2a;">
        <td style="padding:8px 10px;font-size:13px;font-weight:bold;color:#964219;white-space:nowrap;">BET 3</td>
        <td style="padding:8px 10px;font-size:12px;color:#aaa;">{value_type_lbl}</td>
        <td style="padding:8px 10px;font-size:13px;font-weight:bold;">£{double_stake:.2f}</td>
        <td style="padding:8px 10px;font-size:12px;color:#aaa;">{_shape_lbl} @ {double_dec:.1f}x</td>
        <td style="padding:8px 10px;font-size:13px;font-weight:bold;color:#964219;">£{double_return:,.2f}</td>
      </tr>
      <tr>
        <td colspan="5" style="padding:2px 10px 8px;font-size:11px;color:#666;">{double_horses} ({double_odds})</td>
      </tr>"""
    else:
        bet3_html = f"""
      <tr>
        <td style="padding:8px 10px;font-size:13px;font-weight:bold;color:#555;white-space:nowrap;">BET 3</td>
        <td colspan="4" style="padding:8px 10px;font-size:12px;color:#555;">Value Double — no qualifying value horses today (need ≥4x price)</td>
      </tr>"""

    # ── Scenario table ───────────────────────────────────────────────
    scen_rows = ""
    for sc in scenarios:
        net_val = sc.get("Net P&L", "£0.00")
        try:
            net_num = float(str(net_val).replace("£","").replace("+","").replace(",",""))
            net_col = "#437A22" if net_num > 0 else "#A13544" if net_num < 0 else "#888"
        except Exception:
            net_col = "#888"
        scen_rows += f"""
      <tr style="border-bottom:1px solid #2a2a2a;">
        <td style="padding:5px 8px;font-size:12px;color:#aaa;">{sc.get("Scenario","")}</td>
        <td style="padding:5px 8px;font-size:12px;">{sc.get("Acc Return","—")}</td>
        <td style="padding:5px 8px;font-size:12px;">{sc.get("Cover Return","n/a")}</td>
        <td style="padding:5px 8px;font-size:12px;">{sc.get("Double Return","n/a")}</td>
        <td style="padding:5px 8px;font-size:12px;">{sc.get("Total Back","£0.00")}</td>
        <td style="padding:5px 8px;font-size:12px;font-weight:bold;color:{net_col};">{net_val}</td>
      </tr>"""

    scen_table = f"""
    <table style="width:100%;border-collapse:collapse;margin-top:10px;">
      <thead>
        <tr style="color:#555;font-size:11px;text-transform:uppercase;">
          <th style="padding:5px 8px;text-align:left;">Scenario</th>
          <th style="padding:5px 8px;text-align:left;">BET 1</th>
          <th style="padding:5px 8px;text-align:left;">BET 2</th>
          <th style="padding:5px 8px;text-align:left;">BET 3</th>
          <th style="padding:5px 8px;text-align:left;">Total Back</th>
          <th style="padding:5px 8px;text-align:left;">Net P&L</th>
        </tr>
      </thead>
      <tbody>{scen_rows}</tbody>
    </table>""" if scen_rows else ""

    return f"""
    <div style="background:{banner_col};border-radius:4px;padding:8px 12px;margin-bottom:10px;">
      <span style="color:#fff;font-size:13px;font-weight:bold;">{banner_txt}</span>
    </div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:4px;">
      <thead>
        <tr style="color:#555;font-size:11px;text-transform:uppercase;border-bottom:1px solid #333;">
          <th style="padding:5px 10px;text-align:left;">Bet</th>
          <th style="padding:5px 10px;text-align:left;">Type</th>
          <th style="padding:5px 10px;text-align:left;">Stake</th>
          <th style="padding:5px 10px;text-align:left;">Odds</th>
          <th style="padding:5px 10px;text-align:left;">Potential Return</th>
        </tr>
      </thead>
      <tbody>{bet1_html}{bet2_html}{bet3_html}
        <tr style="border-top:2px solid #444;">
          <td colspan="2" style="padding:8px 10px;font-size:13px;font-weight:bold;color:#aaa;">TOTAL STAKE</td>
          <td style="padding:8px 10px;font-size:13px;font-weight:bold;">£{budget:.2f}</td>
          <td colspan="2" style="padding:8px 10px;font-size:11px;color:#666;font-style:italic;">{rationale}</td>
        </tr>
      </tbody>
    </table>
    {scen_table}"""


def _best_acca_block(combos: list) -> str:
    """Render the top-ranked accumulator combinations as an HTML table."""
    if not combos:
        return ('<p style="color:#888;font-size:13px;margin:0;">'
                'No qualifying combinations today — pool too thin to rank.</p>')

    rows = ""
    for c in combos:
        rank = c.get("rank", 0)
        horses = " + ".join(c.get("horses", []))
        warnings = c.get("warnings", []) or []
        warn_txt = "<br>".join(warnings) if warnings else '<span style="color:#437A22;">Clean</span>'

        # Highlight rank 1 in green.
        if rank == 1:
            row_bg = "#1a2a1a"
            rank_col = "#437A22"
            rank_txt = f"#{rank} ★"
        else:
            row_bg = "#111"
            rank_col = "#aaa"
            rank_txt = f"#{rank}"

        rows += f"""
          <tr style="background:{row_bg};">
            <td style="padding:7px 8px;border-bottom:1px solid #2a2a2a;font-size:13px;
                       font-weight:bold;color:{rank_col};white-space:nowrap;">{rank_txt}</td>
            <td style="padding:7px 8px;border-bottom:1px solid #2a2a2a;font-size:12px;
                       color:#e0e0e0;">{horses}</td>
            <td style="padding:7px 8px;border-bottom:1px solid #2a2a2a;font-size:12px;
                       color:#aaa;text-align:center;">{c.get('legs',0)}</td>
            <td style="padding:7px 8px;border-bottom:1px solid #2a2a2a;font-size:12px;
                       color:#fff;white-space:nowrap;">{c.get('combined_dec',0):.1f}x<br>
                       <span style="font-size:10px;color:#666;">{c.get('combined_frac','')}</span></td>
            <td style="padding:7px 8px;border-bottom:1px solid #2a2a2a;font-size:12px;
                       color:#fff;">{c.get('win_prob',0)*100:.1f}%</td>
            <td style="padding:7px 8px;border-bottom:1px solid #2a2a2a;font-size:12px;
                       color:#437A22;font-weight:bold;">£{c.get('proj_return',0):,.2f}</td>
            <td style="padding:7px 8px;border-bottom:1px solid #2a2a2a;font-size:11px;
                       color:#964219;">{warn_txt}</td>
          </tr>"""

    note = (
        '<p style="font-size:11px;color:#888;margin:10px 0 0;line-height:1.4;">'
        'Ranked by probability × odds value (Expected Value). '
        'Not the same as the staking plan — these are the mathematically '
        'strongest combinations from today\'s card. Projected return assumes '
        '£10 stake per combination.</p>'
    )

    return f"""
    <table style="width:100%;border-collapse:collapse;">
      <thead>
        <tr style="color:#555;font-size:10px;text-transform:uppercase;">
          <th style="padding:5px 8px;text-align:left;">Rank</th>
          <th style="padding:5px 8px;text-align:left;">Combination</th>
          <th style="padding:5px 8px;text-align:center;">Legs</th>
          <th style="padding:5px 8px;text-align:left;">Combined Odds</th>
          <th style="padding:5px 8px;text-align:left;">Win Prob</th>
          <th style="padding:5px 8px;text-align:left;">Proj. Return (£10)</th>
          <th style="padding:5px 8px;text-align:left;">Warnings</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    {note}"""


# ── Email Type 1: Morning Brief ────────────────────────────────
def _going_section_html(going: list) -> str:
    """Compact going table — one row per course."""
    if not going:
        return '<p style="color:#888;font-size:13px;margin:0;">Going not yet available.</p>'

    def _going_colour(g: str) -> str:
        g = g.lower()
        if "heavy" in g:                          return "#6B2737"  # deep red
        if "soft" in g and "good" not in g:       return "#964219"  # amber
        if "good to soft" in g:                   return "#7A6A1A"  # yellow-brown
        if "good" in g and "firm" not in g:       return "#437A22"  # green
        if "good to firm" in g or "firm" in g:    return "#1A6A6A"  # teal
        if "standard" in g or "fast" in g:        return "#1A6A6A"
        return "#888"

    rows = ""
    for m in going:
        col = _going_colour(m["going"])
        rows += f"""<tr>
          <td style="padding:6px 8px;border-bottom:1px solid #2a2a2a;font-size:13px;
                     font-weight:bold;color:#fff;">{m['course']}</td>
          <td style="padding:6px 8px;border-bottom:1px solid #2a2a2a;font-size:13px;
                     font-weight:bold;color:{col};">{m['going']}</td>
          <td style="padding:6px 8px;border-bottom:1px solid #2a2a2a;font-size:12px;
                     color:#888;">{m['races']} races</td>
        </tr>"""

    return f"""<table style="width:100%;border-collapse:collapse;">
      <thead><tr style="color:#555;font-size:10px;text-transform:uppercase;">
        <th style="padding:4px 8px;text-align:left;">Course</th>
        <th style="padding:4px 8px;text-align:left;">Going</th>
        <th style="padding:4px 8px;text-align:left;">Races</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>"""


def _moves_section_html(movers: list) -> str:
    """HTML block listing all significant overnight market moves."""
    if not movers:
        return '<p style="color:#888;font-size:13px;margin:0;">No significant moves recorded vs yesterday\'s show prices.</p>'

    steamers = [m for m in movers if m["direction"] == "STEAM"]
    drifters = [m for m in movers if m["direction"] == "DRIFT"]
    html = ""

    for group, label, col, arrow in [
        (steamers, "Shorteners — money coming in", "#437A22", "⬆"),
        (drifters, "Drifters — market cooling",    "#A13544", "⬇"),
    ]:
        if not group:
            continue
        rows = ""
        for m in group:
            rows += f"""<tr>
              <td style="padding:6px 5px;border-bottom:1px solid #2a2a2a;font-size:12px;color:#888;">{m['time']}<br><span style="font-size:11px;">{m['course']}</span></td>
              <td style="padding:6px 5px;border-bottom:1px solid #2a2a2a;font-size:13px;font-weight:bold;color:#fff;">{m['horse']}</td>
              <td style="padding:6px 5px;border-bottom:1px solid #2a2a2a;font-size:12px;color:#888;">{m['baseline_odds']}</td>
              <td style="padding:6px 5px;border-bottom:1px solid #2a2a2a;font-size:13px;font-weight:bold;">{m['current_odds']}</td>
              <td style="padding:6px 5px;border-bottom:1px solid #2a2a2a;font-size:12px;font-weight:bold;color:{col};">{arrow}{m['move_pct']:.0f}%</td>
              <td style="padding:6px 5px;border-bottom:1px solid #2a2a2a;font-size:11px;color:#888;">{m.get('tf_stars','-')} ★</td>
            </tr>"""
        html += f"""
        <div style="font-size:11px;font-weight:bold;color:{col};text-transform:uppercase;
                    letter-spacing:0.5px;margin:10px 0 4px;">{arrow} {label}</div>
        <table style="width:100%;border-collapse:collapse;margin-bottom:8px;">
          <thead><tr style="color:#555;font-size:10px;text-transform:uppercase;">
            <th style="padding:4px 5px;text-align:left;">Time</th>
            <th style="padding:4px 5px;text-align:left;">Horse</th>
            <th style="padding:4px 5px;text-align:left;">Show</th>
            <th style="padding:4px 5px;text-align:left;">Now</th>
            <th style="padding:4px 5px;text-align:left;">Move</th>
            <th style="padding:4px 5px;text-align:left;">TF</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>"""
    return html


def _load_show_price_snapshot() -> dict:
    """Load show-price snapshot for drift/steam detection. Returns {} if missing."""
    import json as _json
    _paths = [
        os.path.join(os.path.dirname(__file__), "..", "learning", "show_price_snapshot.json"),
        "/home/user/workspace/racing-engine/learning/show_price_snapshot.json",
    ]
    for _p in _paths:
        try:
            if os.path.exists(_p):
                with open(_p) as _f:
                    data = _json.load(_f)
                out = {}
                for h in data.get("horses", []):
                    k = (f"{str(h.get('horse','')).lower().strip()}|"
                         f"{str(h.get('course','')).lower().strip()}|"
                         f"{str(h.get('time','')).strip()}")
                    try:
                        out[k] = float(h.get("odds", 0) or 0)
                    except Exception:
                        continue
                return out
        except Exception:
            continue
    return {}


def _signal_breakdown_for(sel: dict) -> dict:
    """Call OddsModel.get_signal_breakdown for a selection. Returns {} on failure."""
    try:
        from engine.odds_model import OddsModel
        model = OddsModel()
        runner = {
            "form":          sel.get("form", "-"),
            "tf_stars":      sel.get("tf_stars"),
            "odds":          sel.get("odds", "N/A"),
            "signal":        sel.get("signal", "Stable"),
            "bet_movements": [],
            "trainer":       sel.get("trainer", ""),
            "jockey":        sel.get("jockey", ""),
            "going":         sel.get("going", ""),
            "field_size":    int(sel.get("runners", 0) or 0),
            "is_handicap":   bool(sel.get("is_handicap", False)),
            # v2.6.0 — feed fields needed for the new signals
            "previous_results":     sel.get("previous_results", []) or [],
            "race_history_stats":   sel.get("race_history_stats", []) or [],
            "horse_lifetime_stats": sel.get("horse_lifetime_stats", []) or [],
            "rating123":            sel.get("rating123"),
            "all_ratings_in_race":  sel.get("all_ratings_in_race", []) or [],
            "last_ran_days":        sel.get("last_ran_days"),
            "race_class":           sel.get("race_class", ""),
            "race_dist_f":          float(sel.get("race_dist_f", 0) or 0),
            "course":               sel.get("course", ""),
        }
        return model.get_signal_breakdown(runner)
    except Exception as e:
        print(f"[Brief] signal breakdown failed for {sel.get('horse','')}: {e}")
        return {}


def _morning_html(selections: list) -> str:
    """
    Race-by-race intelligence layout grouped by racecourse.
    Each race card: engine pick, market fav comparison, drift/steam flag, why block.
    """
    if not selections:
        return '<p style="color:#888;font-size:13px;margin:0;">No qualifying selections at this time.</p>'

    snapshot  = _load_show_price_snapshot()
    # v2.6.0 — added going / course_form / distance_form / or_gap /
    # class_consistency / freshness now that they're computed from feed data.
    label_map = {
        "horse_form":        "Recent form",
        "trainer_form":      "Trainer form",
        "jockey_form":       "Jockey form",
        "track_form":        "Track record",
        "going":             "Going preference",
        "course_form":       "Course form",
        "distance_form":     "Distance form",
        "or_gap":            "Official rating",
        "class_consistency": "Class move",
        "freshness":         "Freshness",
        "race_pace":         "Pace angle",
        "market_moves":      "Market move",
        "tf_stars":          "Timeform rating",
        "bsp_signal":        "Exchange signal",
        "market_odds":       "Market price",
        "adjustment":        "Adjustments",
    }

    by_course = {}
    for s in selections:
        by_course.setdefault(s["course"], []).append(s)

    html = ""
    for course in sorted(by_course.keys()):
        races = sorted(by_course[course], key=lambda r: r["time"])

        html += (
            f'<div style="background:#1a472a;color:#ffffff;font-weight:bold;'
            f'padding:10px 14px;border-radius:6px;margin:14px 0 8px;'
            f'letter-spacing:0.5px;text-transform:uppercase;font-size:13px;">'
            f'{course} — {len(races)} selection{"s" if len(races)!=1 else ""}'
            f'</div>'
        )

        for s in races:
            horse       = s.get("horse", "")
            t_time      = s.get("time", "")
            race_name   = s.get("race_name", "") or ""
            runners     = int(s.get("runners", 0) or 0)
            going       = s.get("going", "") or ""
            dec         = float(s.get("decimal", 0) or 0)
            conf_pct    = int(float(s.get("confidence", 0) or 0) * 100)
            role        = s.get("role", "VALUE")
            is_fav      = bool(s.get("is_fav", False))
            fav_price   = float(s.get("fav_price", 0) or 0)
            fav_name    = (s.get("fav_name", "") or "").strip()
            race_class  = (s.get("race_class", "") or "").strip()
            race_type_raw = str(s.get("race_type", "") or "").strip().lower()
            race_type_lbl = {
                "nhf": "NHF", "bumper": "NHF",
                "hurdle": "Hurdle", "flat": "Flat", "chase": "Chase",
            }.get(race_type_raw, race_type_raw.title() if race_type_raw else "")

            role_col = "#0b5394" if role == "BANKER" else "#6a1b9a"

            header_bits = [t_time]
            if race_name:     header_bits.append(race_name)
            if race_class:    header_bits.append(race_class)
            if race_type_lbl: header_bits.append(race_type_lbl)
            if runners:       header_bits.append(f"{runners} runners")
            if going:         header_bits.append(going)
            header_txt  = " &nbsp;|&nbsp; ".join(header_bits)

            race_card = (
                '<div style="background:#ffffff;color:#1a1a1a;border:1px solid #e0e0e0;'
                'border-radius:8px;padding:12px 14px;margin-bottom:12px;max-width:600px;'
                'font-family:Arial,sans-serif;">'
                f'<div style="font-size:12px;color:#555;margin-bottom:8px;">{header_txt}</div>'
            )

            race_card += (
                '<div style="padding:6px 0;border-bottom:1px solid #f0f0f0;">'
                '<span style="font-size:10px;color:#888;letter-spacing:0.5px;'
                'text-transform:uppercase;">Engine pick</span><br>'
                f'<span style="font-size:15px;font-weight:bold;color:#1a1a1a;">{horse}</span>'
                f' &nbsp;<span style="font-size:13px;color:#1a1a1a;">@ {dec:.2f}x</span>'
                f' &nbsp;<span style="font-size:12px;color:#444;">Conf {conf_pct}%</span>'
                f' &nbsp;<span style="display:inline-block;background:{role_col};color:#ffffff;'
                f'font-size:10px;font-weight:bold;padding:2px 8px;border-radius:10px;'
                f'letter-spacing:0.5px;">{role}</span>'
                '</div>'
            )

            if is_fav:
                race_card += (
                    '<div style="padding:6px 0;border-bottom:1px solid #f0f0f0;">'
                    '<span style="font-size:10px;color:#888;letter-spacing:0.5px;'
                    'text-transform:uppercase;">Market fav</span><br>'
                    '<span style="font-size:13px;font-weight:bold;color:#2e7d32;">'
                    '&#9989; IS MARKET FAVOURITE</span>'
                    '</div>'
                )
            else:
                fav_display = fav_name if fav_name else "another horse"
                race_card += (
                    '<div style="padding:6px 0;border-bottom:1px solid #f0f0f0;">'
                    '<span style="font-size:10px;color:#888;letter-spacing:0.5px;'
                    'text-transform:uppercase;">Market fav</span><br>'
                    f'<span style="font-size:13px;color:#e65100;">{fav_display}'
                    f' @ {fav_price:.2f}x</span>'
                    '</div>'
                    '<div style="background:#fff3cd;border:1px solid #ffe08a;color:#7a5c00;'
                    'padding:8px 10px;border-radius:6px;margin:8px 0;font-size:12px;">'
                    f'&#9888; NOT FAV — market prefers {fav_display} @ {fav_price:.2f}x'
                    '</div>'
                )

            if bool(s.get("low_value_acca", False)):
                _lva_reason = (s.get("low_value_reason", "") or "").strip() or (
                    f"thin field ({runners} runners)" if runners and runners <= 4
                    else "odds-on price"
                )
                race_card += (
                    '<div style="background:#fff3cd;border:1px solid #ffe08a;color:#7a5c00;'
                    'padding:8px 10px;border-radius:6px;margin:8px 0;font-size:12px;">'
                    f'&#9888; Low acca value ({_lva_reason}) &mdash; '
                    f'consider omitting from accumulator'
                    '</div>'
                )

            if race_type_raw == "chase":
                race_card += (
                    '<div style="background:#f8d7da;border:1px solid #e4868d;color:#842029;'
                    'padding:8px 10px;border-radius:6px;margin:8px 0;font-size:12px;'
                    'font-weight:bold;">'
                    '&#9888; CHASE RACE &mdash; 37.5% historical strike rate '
                    '(vs 55.8% for hurdles). Market leaders beaten more often over fences.'
                    '</div>'
                )

            if bool(s.get("rival_top_trainer", False)):
                _rival_nm = (s.get("rival_trainer_name", "") or "top trainer").strip()
                race_card += (
                    '<div style="background:#f8d7da;border:1px solid #e4868d;color:#842029;'
                    'padding:8px 10px;border-radius:6px;margin:8px 0;font-size:12px;'
                    'font-weight:bold;">'
                    f'&#9888; TOP TRAINER DANGER: {_rival_nm} has a runner in this race'
                    '</div>'
                )

            snap_key  = (f"{horse.lower().strip()}|"
                         f"{s.get('course','').lower().strip()}|"
                         f"{t_time.strip()}")
            snap_odds = snapshot.get(snap_key, 0.0)
            if snap_odds and dec and snap_odds > 0:
                delta_pct = ((dec - snap_odds) / snap_odds) * 100.0
                if delta_pct > 15.0:
                    race_card += (
                        '<div style="background:#fff3cd;border:1px solid #ffe08a;color:#7a5c00;'
                        'padding:8px 10px;border-radius:6px;margin:8px 0;font-size:12px;">'
                        f'&#9888; DRIFTING — opened {snap_odds:.2f}x &rarr; now {dec:.2f}x '
                        f'(+{delta_pct:.0f}%) — MONITOR'
                        '</div>'
                    )
                elif delta_pct < -15.0:
                    race_card += (
                        '<div style="background:#d4edda;border:1px solid #b8e0c2;color:#1e5631;'
                        'padding:8px 10px;border-radius:6px;margin:8px 0;font-size:12px;">'
                        f'&#9989; STEAMING — opened {snap_odds:.2f}x &rarr; now {dec:.2f}x '
                        f'({delta_pct:.0f}%) — confidence building'
                        '</div>'
                    )

            # v2.6.0 — "Why selected" line: surface the new feed-driven signals
            # using specific wording, and only show meaningful ones (>0.65 or <0.40).
            breakdown = _signal_breakdown_for(s)
            prev_res  = s.get("previous_results", []) or []

            def _count_at_going(prev, today_going):
                if not prev or not today_going:
                    return (0, 0)
                from engine.odds_model import OddsModel as _OM
                grp = _OM._classify_going(today_going)
                runs = [r for r in prev
                        if _OM._classify_going(r.get("going_shortcode") or r.get("going") or "") == grp]
                wins = sum(1 for r in runs if str(r.get("position","")).strip() == "1")
                return (wins, len(runs))

            def _count_at_course(prev, course):
                if not prev or not course:
                    return (0, 0)
                target = course.lower().strip()
                runs = [r for r in prev
                        if str(r.get("course_name","")).lower().strip() == target]
                wins = sum(1 for r in runs if str(r.get("position","")).strip() == "1")
                return (wins, len(runs))

            def _count_at_dist(prev, dist_f):
                if not prev or not dist_f or dist_f <= 0:
                    return (0, 0)
                from engine.odds_model import OddsModel as _OM
                runs = []
                for r in prev:
                    d = _OM._parse_furlongs(r.get("distance",""))
                    if d > 0 and abs(d - dist_f) <= 0.5:
                        runs.append(r)
                wins = sum(1 for r in runs if str(r.get("position","")).strip() == "1")
                return (wins, len(runs))

            why_lines = []

            g_score = float(breakdown.get("going", 0.5) or 0.5)
            if g_score >= 0.65:
                gw, gn = _count_at_going(prev_res, going)
                if gn > 0:
                    why_lines.append(f"&#10003; Won on {going} ({gw}/{gn})" if gw else f"&#10003; Suits {going}")
            elif g_score <= 0.40:
                why_lines.append(f"&#9888; Untested / poor on {going}")

            c_score = float(breakdown.get("course_form", 0.5) or 0.5)
            if c_score >= 0.65:
                cw, cn = _count_at_course(prev_res, s.get("course",""))
                if cw:
                    why_lines.append(f"&#10003; Course winner ({cw}/{cn})")
                elif cn:
                    why_lines.append(f"&#10003; Course form ({cn} runs)")

            d_score = float(breakdown.get("distance_form", 0.5) or 0.5)
            if d_score >= 0.65:
                dw, dn = _count_at_dist(prev_res, float(s.get("race_dist_f", 0) or 0))
                if dw:
                    why_lines.append(f"&#10003; Won at this trip ({dw}/{dn})")
                elif dn:
                    why_lines.append(f"&#10003; Tried this trip")

            cls_score = float(breakdown.get("class_consistency", 0.5) or 0.5)
            if cls_score >= 0.65:
                why_lines.append("&#8595; Dropping in class")
            elif cls_score <= 0.40:
                why_lines.append("&#8593; Stepping up in class")

            or_score = float(breakdown.get("or_gap", 0.5) or 0.5)
            if or_score >= 0.75:
                why_lines.append("&#10003; Top rated in field")
            elif or_score >= 0.60:
                why_lines.append("&#10003; Highly rated")

            f_score = float(breakdown.get("freshness", 0.5) or 0.5)
            if f_score >= 0.60:
                why_lines.append("&#10003; Race-fit")
            elif f_score <= 0.40:
                why_lines.append("&#9888; Long absence")

            why_lines = why_lines[:3]
            if why_lines:
                race_card += (
                    '<div style="font-size:11px;color:#555;margin-top:8px;">'
                    f'<span style="color:#888;text-transform:uppercase;letter-spacing:0.5px;">Why selected:</span> '
                    + " &nbsp;|&nbsp; ".join(why_lines)
                    + '</div>'
                )

            # Generic top-3 score breakdown (unchanged) — kept as secondary line
            numeric = []
            for k, v in breakdown.items():
                if k not in label_map:
                    continue
                try:
                    fv = float(v)
                    numeric.append((k, fv))
                except (TypeError, ValueError):
                    continue
            numeric.sort(key=lambda kv: abs(kv[1] - 0.5), reverse=True)
            top3 = [(k, v) for k, v in numeric if abs(v - 0.5) > 0.05][:3]
            if top3:
                parts = []
                for k, v in top3:
                    if v > 0.55:
                        icon = "&#9989;"
                    elif v < 0.45:
                        icon = "&#10060;"
                    else:
                        icon = "&#9888;"
                    parts.append(f'{icon} {label_map.get(k, k)}')
                why_txt = " &nbsp;|&nbsp; ".join(parts)
                race_card += (
                    '<div style="font-size:11px;color:#555;margin-top:4px;">'
                    f'<span style="color:#888;text-transform:uppercase;letter-spacing:0.5px;">Signals:</span> '
                    f'{why_txt}'
                    '</div>'
                )

            race_card += '</div>'
            html += race_card

    return html


_MORNING_PRICES_FILE = os.path.join(
    os.path.dirname(__file__), "..", "learning", "morning_prices.json"
)

_DAILY_SELECTIONS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "learning", "daily_selections.json"
)


def _save_daily_selections_snapshot(selections: list) -> None:
    """Save today's confirmed selections at 13:00 for use by evening summary.

    The 21:00 evening summary cannot re-derive selections from the live
    racecard (races have finished). This snapshot is the canonical source.
    """
    import json as _json
    today = datetime.now(_LONDON).date().isoformat()
    snap_sels = []
    for s in (selections or []):
        snap_sels.append({
            "horse":        s.get("horse", ""),
            "course":       s.get("course", ""),
            "time":         s.get("time", ""),
            "confidence":   float(s.get("confidence", 0) or 0),
            "odds":         s.get("curr_odds", s.get("odds", "")),
            "odds_decimal": float(s.get("best_odds_decimal") or s.get("decimal") or s.get("odds_dec") or 0),
            "decimal":      float(s.get("decimal") or s.get("best_odds_decimal") or 0),
            "decimal_odds": float(s.get("decimal") or s.get("best_odds_decimal") or 0),
            "why":          s.get("why_selected", ""),
            "dominant_rival": bool(s.get("dominant_rival", False)),
            "yg_risk":      bool(s.get("yg_risk", False)),
            "runners":      int(s.get("runners") or s.get("field_size") or 0),
            "bet_a":        not bool(s.get("dominant_rival", False)),
            "is_handicap":  bool(s.get("is_handicap", False)),
            "race_type":    s.get("race_type", ""),
            "signals": {
                "going":           float((s.get("signals") or {}).get("going", 0) or 0),
                "course_signal":   float(s.get("course_signal", 0) or 0),
                "distance_signal": float(s.get("distance_signal", 0) or 0),
            },
        })
    payload = {
        "date":       today,
        "saved_at":   datetime.now(_LONDON).isoformat(),
        "selections": snap_sels,
    }
    try:
        os.makedirs(os.path.dirname(_DAILY_SELECTIONS_PATH), exist_ok=True)
        with open(_DAILY_SELECTIONS_PATH, "w") as f:
            _json.dump(payload, f, indent=2)
        print(f"[Brief] Saved {len(snap_sels)} selections to daily snapshot")
    except Exception as e:
        print(f"[Brief] Could not save daily selections snapshot: {e}")


def _load_daily_selections_snapshot() -> list:
    """Load today's confirmed selections from the 13:00 snapshot file."""
    import json as _json
    today = datetime.now(_LONDON).date().isoformat()
    try:
        with open(_DAILY_SELECTIONS_PATH) as f:
            snap = _json.load(f)
        if snap.get("date") != today:
            print(f"[Evening] daily_selections.json is from {snap.get('date')} not today — no selections")
            return []
        sels = snap.get("selections", []) or []
        print(f"[Evening] Loaded {len(sels)} selections from 13:00 snapshot")
        return sels
    except FileNotFoundError:
        print("[Evening] No daily selections snapshot found")
        return []
    except Exception as e:
        print(f"[Evening] Could not load daily selections snapshot: {e}")
        return []


def _going_badge_colour(g: str) -> tuple:
    """Return (background, text) hex colours for a going string."""
    g = (g or "").lower()
    if "heavy" in g:                     return ("#4a90d9", "#ffffff")
    if "good to soft" in g:              return ("#7cb9e8", "#333333")
    if "soft" in g and "good" not in g:  return ("#4a90d9", "#ffffff")
    if "good to firm" in g:              return ("#e8a33d", "#333333")
    if ("firm" in g and "good" not in g) or "hard" in g:
        return ("#d9534f", "#ffffff")
    if "good" in g:                      return ("#2d7a3a", "#ffffff")
    if "standard" in g or "fast" in g or "all weather" in g:
        return ("#888888", "#ffffff")
    return ("#888888", "#ffffff")


def _race_going(sel: dict) -> str:
    """Pull going from a selection/race dict — try multiple field names."""
    for k in ("going", "goingDescription", "ground"):
        v = sel.get(k)
        if v and str(v).strip() and str(v).strip().lower() != "tbc":
            return str(v).strip()
    return "Going TBC"


def _store_morning_prices(selections: list) -> None:
    """Write today's selection prices to learning/morning_prices.json. Silent on failure."""
    try:
        import json as _json
        today_str = datetime.now(_LONDON).strftime("%Y-%m-%d")
        payload = {
            "date":       today_str,
            "timestamp":  f"{_now_bst()} BST",
            "selections": {},
        }
        for s in selections:
            horse = s.get("horse", "")
            if not horse:
                continue
            payload["selections"][horse] = {
                "decimal":        float(s.get("best_odds_decimal") or s.get("decimal") or 0),
                "fractional":     s.get("best_odds_fractional") or s.get("odds") or "N/A",
                "best_bookmaker": s.get("best_bookmaker") or "",
                "course":         s.get("course", ""),
                "time":           s.get("time", ""),
            }
        os.makedirs(os.path.dirname(_MORNING_PRICES_FILE), exist_ok=True)
        with open(_MORNING_PRICES_FILE, "w") as f:
            _json.dump(payload, f, indent=2)
    except Exception as e:
        print(f"[Brief] Morning prices store failed (silent): {e}")


def _load_morning_prices() -> dict:
    """Return today's morning_prices payload or {} if missing/stale."""
    try:
        import json as _json
        if not os.path.exists(_MORNING_PRICES_FILE):
            return {}
        with open(_MORNING_PRICES_FILE) as f:
            data = _json.load(f)
        today_str = datetime.now(_LONDON).strftime("%Y-%m-%d")
        if data.get("date") != today_str:
            return {}
        return data
    except Exception:
        return {}


def _movers_section_30(movers: list) -> str:
    """Mobile-first 30%+ movers section. Caps at 5 steamers + 5 drifters."""
    big = [m for m in movers
           if m.get("direction") in ("STEAM", "DRIFT")
           and float(m.get("move_pct", 0)) >= 30.0
           and float(m.get("baseline_dec", 0) or 0) <= 20.0]
    if not big:
        return '<p style="color:#888;font-size:14px;margin:0;">No 30%+ moves on today\'s racing.</p>'

    steamers = sorted([m for m in big if m["direction"] == "STEAM"],
                      key=lambda x: x["move_pct"], reverse=True)[:5]
    drifters = sorted([m for m in big if m["direction"] == "DRIFT"],
                      key=lambda x: x["move_pct"], reverse=True)[:5]
    rows = []
    for grp, label, col, arrow in [
        (steamers, "STEAMING", "#2d7a3a", "↓"),
        (drifters, "DRIFTING", "#d9534f", "↑"),
    ]:
        for m in grp:
            sign = "-" if grp is steamers else "+"
            rows.append(
                f'<div style="background:#1c1f2e;border-radius:6px;padding:10px 12px;'
                f'margin-bottom:8px;border-left:3px solid {col};">'
                f'<div style="font-size:12px;color:#aaa;text-transform:uppercase;'
                f'letter-spacing:0.5px;">{m.get("course","")} {m.get("time","")}</div>'
                f'<div style="font-size:15px;font-weight:bold;color:#fff;margin:2px 0;">'
                f'{m.get("horse","")}</div>'
                f'<div style="font-size:13px;color:#e0e0e0;">'
                f'{m.get("baseline_odds","?")} → {m.get("current_odds","?")} '
                f'<span style="color:{col};font-weight:bold;">{arrow} {label} '
                f'({sign}{int(m["move_pct"])}%)</span></div>'
                f'</div>'
            )
    return "".join(rows)


def _going_strip_html(going: list) -> str:
    """Compact horizontal strip of going badges, one per course."""
    if not going:
        return ('<p style="color:#888;font-size:14px;margin:0;">'
                'Going data not yet available.</p>')
    pills = []
    for m in going:
        bg, fg = _going_badge_colour(m.get("going", ""))
        pills.append(
            f'<span style="display:inline-block;background:{bg};color:{fg};'
            f'font-size:13px;font-weight:bold;padding:6px 12px;border-radius:14px;'
            f'margin:3px 4px 3px 0;letter-spacing:0.3px;">'
            f'{m.get("course","").upper()} · {m.get("going","TBC")}</span>'
        )
    return f'<div style="line-height:2.0;">{"".join(pills)}</div>'


def _bet_card_mobile(bet: dict, label: str, accent: str, deadline: str) -> str:
    """v2.5.54 — card-per-bet for the mobile email.

    Renders a BET A or BET B block with Lucky 15/31 stake, singles stake, and
    a list of horses (decimal odds + course/time)."""
    if not bet or bet.get("skipped"):
        return ""
    selections = bet.get("selections") or []
    if not selections:
        return ""
    lucky      = bet.get("lucky_bet") or {}
    singles    = bet.get("singles")   or {}
    total      = bet.get("total_stake", 0.0)
    lucky_lbl  = lucky.get("label", "Lucky")
    lucky_st   = lucky.get("stake", 0.0)
    lucky_lines = lucky.get("lines", 0)
    lucky_ret  = lucky.get("potential_return", 0.0)
    singles_st = singles.get("stake", 0.0)
    singles_each = singles_st / max(len(selections), 1)

    legs = []
    for s in selections:
        legs.append(
            f'<div style="padding:8px 0;border-bottom:1px solid #2a2a2a;">'
            f'<div style="font-size:12px;color:#aaa;">'
            f'{s.get("time","")} {s.get("course","")}</div>'
            f'<div style="font-size:16px;font-weight:bold;color:#fff;'
            f'text-transform:uppercase;margin:2px 0;">{s.get("name","")}</div>'
            f'<div style="font-size:13px;color:#e0e0e0;">'
            f'{float(s.get("decimal_odds", 0) or 0):.2f}x &middot; '
            f'conf {float(s.get("confidence", 0) or 0):.0%}</div>'
            f'</div>'
        )
    legs_html = "".join(legs)

    return (
        f'<div style="background:#1c1f2e;border-radius:10px;padding:14px 16px;'
        f'margin-bottom:14px;border-left:5px solid {accent};">'
        f'<div style="font-size:11px;color:{accent};font-weight:bold;letter-spacing:1px;'
        f'text-transform:uppercase;margin-bottom:4px;">{label}</div>'
        f'<div style="font-size:18px;font-weight:bold;color:#fff;margin-bottom:6px;">'
        f'{bet.get("tier","").replace("_"," ")} — {len(selections)} selections '
        f'&middot; £{total:.2f} total</div>'
        f'<div style="font-size:13px;color:#e0e0e0;margin-bottom:10px;">'
        f'<b>{lucky_lbl}:</b> £{lucky_st:.2f} across {lucky_lines} lines '
        f'(potential return £{lucky_ret:,.2f})<br>'
        f'<b>Singles:</b> £{singles_st:.2f} (£{singles_each:.2f} per horse)'
        f'</div>'
        f'{legs_html}'
        f'<div style="font-size:12px;color:#e8a33d;margin-top:10px;font-weight:bold;">'
        f'Place before {deadline}</div>'
        f'</div>'
    )


# v2.5.54 — back-compat alias used elsewhere in the file.
_fold_card_mobile = _bet_card_mobile


def _selection_card_mobile(s: dict, snapshot: dict, morning_prices: dict = None) -> str:
    """One card per official selection — mobile-first 600px max-width layout."""
    horse   = (s.get("horse", "") or "").strip()
    course  = s.get("course", "")
    t_time  = s.get("time", "")
    going   = _race_going(s)
    bg, fg  = _going_badge_colour(going)

    dec     = float(s.get("decimal", 0) or 0)
    conf    = int(float(s.get("confidence", 0) or 0) * 100)
    role    = s.get("role", "VALUE")
    role_col = "#0b5394" if role == "BANKER" else "#6a1b9a"

    # v2.5.62 — never render "nan" in the Best: line.
    _bf_raw = s.get("best_odds_fractional") or s.get("odds")
    _bf_str = ("" if _bf_raw is None else str(_bf_raw).strip())
    if not _bf_str or _bf_str.lower() in ("nan", "none", "n/a"):
        best_frac = "No price"
        no_price_flag = True
    else:
        best_frac = _bf_str
        no_price_flag = False
    best_bk   = s.get("best_bookmaker") or ""

    is_fav    = bool(s.get("is_fav", False))
    fav_price = float(s.get("fav_price", 0) or 0)
    fav_name  = (s.get("fav_name", "") or "").strip()

    if is_fav:
        fav_line = '<div style="font-size:13px;color:#2d7a3a;margin:4px 0;"><b>Fav:</b> ✓ YES (market favourite)</div>'
    elif fav_price and fav_name:
        gap_pct = (fav_price / dec - 1.0) * 100 if dec > 0 else 0
        fav_line = (
            f'<div style="font-size:13px;color:#e65100;margin:4px 0;">'
            f'<b>Fav:</b> ✗ {fav_name} @ {fav_price:.2f}x ({gap_pct:+.0f}% gap)</div>'
        )
    else:
        fav_line = ''

    snap_key = f"{horse.lower()}|{course.lower().strip()}|{t_time.strip()}"
    snap_dec = float(snapshot.get(snap_key, 0) or 0)
    move_line = '<div style="font-size:13px;color:#666;margin:4px 0;"><b>Move:</b> — (stable)</div>'
    if snap_dec > 0 and dec > 0:
        delta = ((dec - snap_dec) / snap_dec) * 100.0
        if delta <= -15.0:
            move_line = (f'<div style="font-size:13px;color:#2d7a3a;margin:4px 0;">'
                         f'<b>Move:</b> ↓ {snap_dec:.2f}x → {dec:.2f}x STEAMING ({delta:.0f}%)</div>')
        elif delta >= 15.0:
            move_line = (f'<div style="font-size:13px;color:#d9534f;margin:4px 0;">'
                         f'<b>Move:</b> ↑ {snap_dec:.2f}x → {dec:.2f}x DRIFTING (+{delta:.0f}%)</div>')

    yg_line = ''
    if bool(s.get("yg_risk", False)):
        yg_line = ('<div style="background:#fff3cd;color:#7a5c00;font-size:12px;'
                   'padding:6px 8px;border-radius:4px;margin:6px 0;font-weight:bold;">'
                   '⚠ Yorkshire Glory risk — large field</div>')

    if bool(s.get("split_market", False)):
        yg_line += ('<div style="background:#f8d7da;color:#721c24;font-size:12px;'
                    'padding:6px 8px;border-radius:4px;margin:6px 0;font-weight:bold;">'
                    '⚠ SPLIT MARKET — 2nd favourite within 20% of our price</div>')

    # v2.5.62 — flag horses we kept in the reference list but excluded from Bet A/B
    if no_price_flag:
        yg_line += ('<div style="background:#f8d7da;color:#721c24;font-size:12px;'
                    'padding:6px 8px;border-radius:4px;margin:6px 0;font-weight:bold;">'
                    '⚠ No price data — excluded from Bet A/B</div>')

    if bool(s.get("dominant_rival", False)):
        _dr_name  = s.get("dominant_rival_name", "?") or "?"
        _dr_price = float(s.get("dominant_rival_price", 0) or 0)
        _our_dec  = float(s.get("decimal", 0) or 0)
        _dr_gap   = (1.0 - _dr_price / _our_dec) * 100.0 if _our_dec > 0 else 0.0
        yg_line += (
            f'<div style="background:#f8d7da;color:#721c24;font-size:12px;'
            f'padding:6px 8px;border-radius:4px;margin:6px 0;font-weight:bold;">'
            f'⚠ DOM — {_dr_name} @ {_dr_price:.2f}x ({_dr_gap:+.0f}% shorter)</div>'
        )

    morning_line = ''
    if morning_prices:
        mp = (morning_prices.get("selections") or {}).get(horse, {})
        m_frac = mp.get("fractional")
        m_dec  = float(mp.get("decimal") or 0)
        if m_frac and m_dec > 0:
            change = ""
            if dec > 0:
                d = (dec - m_dec) / m_dec * 100
                if d <= -8.0:
                    change = f' <span style="color:#2d7a3a;font-weight:bold;">SHORTENING ↑ ({d:+.0f}%)</span>'
                elif d >= 8.0:
                    change = f' <span style="color:#d9534f;font-weight:bold;">DRIFTED ↓ (+{d:.0f}%)</span>'
            morning_line = (
                f'<div style="font-size:12px;color:#888;margin:4px 0;">'
                f'Morning: {m_frac} → Now: {best_frac}'
                f'{f" @ {best_bk}" if best_bk else ""}{change}</div>'
            )

    return (
        f'<div style="background:#ffffff;color:#1a1a1a;border:1px solid #d8d8d8;'
        f'border-radius:8px;padding:14px 16px;margin-bottom:12px;'
        f'font-family:Arial,Helvetica,sans-serif;line-height:1.5;max-width:600px;">'
        f'<div style="font-size:13px;color:#555;margin-bottom:6px;">'
        f'{t_time} {course.upper()} '
        f'<span style="display:inline-block;background:{bg};color:{fg};'
        f'font-size:11px;font-weight:bold;padding:2px 8px;border-radius:10px;'
        f'margin-left:4px;">{going}</span></div>'
        f'<div style="font-size:20px;font-weight:bold;text-transform:uppercase;'
        f'color:#1a1a1a;margin:4px 0 8px;letter-spacing:0.5px;">{horse}</div>'
        f'<div style="font-size:14px;color:#1a1a1a;margin:4px 0;">'
        f'<b>Best:</b> {best_frac}'
        f'{f" @ {best_bk}" if best_bk else ""}</div>'
        f'<div style="font-size:13px;margin:4px 0;color:#1a1a1a;">'
        f'<b>Conf:</b> {conf}% '
        f'<span style="display:inline-block;background:{role_col};color:#fff;'
        f'font-size:11px;font-weight:bold;padding:2px 8px;border-radius:10px;'
        f'margin-left:4px;letter-spacing:0.5px;">{role}</span></div>'
        f'{fav_line}'
        f'{move_line}'
        f'{morning_line}'
        f'{yg_line}'
        f'</div>'
    )


def _mobile_email_shell(title: str, header_color: str, label_text: str,
                        body_html: str, footer_note: str) -> str:
    """Mobile-first email shell — 600px max width, light card layout on dark backdrop."""
    version = _get_version()
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#0f1117;
             font-family:Arial,Helvetica,sans-serif;color:#e0e0e0;
             font-size:16px;line-height:1.5;">
<div style="max-width:600px;margin:0 auto;padding:12px;">

  <!-- Header -->
  <div style="background:{header_color};color:#ffffff;border-radius:10px;
              padding:16px 18px;margin-bottom:12px;">
    <div style="font-size:11px;font-weight:bold;letter-spacing:1px;
                text-transform:uppercase;opacity:0.85;">{label_text}</div>
    <div style="font-size:18px;font-weight:bold;margin-top:2px;">{title}</div>
    <div style="font-size:13px;opacity:0.85;margin-top:4px;">
      {_date_bst()} &nbsp;|&nbsp; {_now_bst()} BST
    </div>
  </div>

  {body_html}

  <!-- Footer -->
  <div style="text-align:center;color:#666;font-size:12px;padding:14px;line-height:1.6;">
    {footer_note}<br>
    Racing Engine {version} &nbsp;|&nbsp;
    <a href="https://racing-engine-dash.streamlit.app" style="color:#01696F;">Dashboard (PIN: 1012)</a>
  </div>

</div></body></html>"""


def build_morning_brief(budget: float = 100.0) -> str:
    """v2.5.43 mobile-first card-per-horse design. Sent at 08:00 BST."""
    selections = _get_official_selections()
    movers     = _get_overnight_moves()
    going      = _get_going()
    snapshot   = _load_show_price_snapshot()

    body = ""

    # 1. Going strip — one pill per course
    body += (
        '<div style="background:#1c1f2e;border-radius:10px;padding:12px 14px;'
        'margin-bottom:12px;border-top:2px solid #1a472a;">'
        '<div style="font-size:11px;font-weight:bold;color:#aaa;letter-spacing:1px;'
        'text-transform:uppercase;margin-bottom:8px;">Today\'s Going</div>'
        f'{_going_strip_html(going)}'
        '</div>'
    )

    # 2. BET A & BET B (top of email — most actionable)
    if selections:
        try:
            from engine.staking import get_daily_bets as _get_daily_bets
            _bets = _get_daily_bets(selections)
        except Exception as _fb_err:
            print(f"[Brief] Daily bets failed: {_fb_err}")
            _bets = {"bet_a": {"skipped": True}, "bet_b": {"skipped": True}}

        _ba = _bets.get("bet_a") or {}
        _bb = _bets.get("bet_b") or {}
        if not _ba.get("skipped"):
            body += _bet_card_mobile(_ba, "BET A — CORE (Lucky 15 + Singles)",
                                     "#2d7a3a", "13:00 BST")
        if not _bb.get("skipped"):
            body += _bet_card_mobile(_bb, "BET B — MID (Lucky 31 + Singles)",
                                     "#01696F", "13:00 BST")
        else:
            body += (
                '<div style="background:#1c1f2e;border-radius:10px;padding:10px 14px;'
                'margin-bottom:14px;color:#aaa;font-size:13px;">'
                'BET B requires 5+ selections — not enough qualifiers today.'
                '</div>'
            )

    # 3. Selection cards
    if selections:
        body += (
            '<div style="font-size:13px;font-weight:bold;color:#aaa;letter-spacing:1px;'
            'text-transform:uppercase;padding:8px 4px;margin-top:6px;">'
            f'Selections ({len(selections)})</div>'
        )
        for s in selections:
            body += _selection_card_mobile(s, snapshot)

    # 4. Significant 30%+ market movers
    body += (
        '<div style="background:#1c1f2e;border-radius:10px;padding:14px 16px;'
        'margin:14px 0 12px;border-top:2px solid #e8a33d;">'
        '<div style="font-size:13px;font-weight:bold;color:#fff;letter-spacing:0.5px;'
        'text-transform:uppercase;margin-bottom:10px;">'
        'Significant Moves Today (30%+)</div>'
        f'{_movers_section_30(movers)}'
        '</div>'
    )

    if not selections:
        body += (
            '<div style="background:#1c1f2e;border-radius:10px;padding:14px 16px;'
            'margin-bottom:12px;border-left:4px solid #964219;">'
            '<div style="font-size:14px;color:#e0e0e0;">No qualifying selections at this time. '
            'Markets are live but no horses have cleared all filters yet. '
            'Check dashboard from 10:30 BST for developing selections.</div>'
            '</div>'
        )

    # ── Persist morning prices for the 13:30 confirmed-selections compare ──
    try:
        _store_morning_prices(selections)
    except Exception as e:
        print(f"[Brief] Morning prices not stored: {e}")

    return _mobile_email_shell(
        title       = "Today's Selections",
        header_color = "#1a472a",
        label_text  = "Racing Engine | Morning Brief",
        body_html   = body,
        footer_note = "Updated: 08:00 BST &middot; Next update: 13:30 BST",
    )


# ── Email Type 1b: Confirmed Selections (13:30 BST) ────────────
def build_confirmed_selections() -> str:
    """v2.5.43 13:30 BST 'final word' email — confirms selections 30 mins
    before afternoon racing. Drops anything that has drifted 30%+ since 08:00.
    """
    selections     = _get_official_selections()
    morning_prices = _load_morning_prices()
    snapshot       = _load_show_price_snapshot()
    going          = _get_going()

    morning_sel_map = (morning_prices.get("selections") or {})
    morning_courses = {
        v.get("course", ""): None for v in morning_sel_map.values()
    }

    # Drop anything that drifted 30%+ since morning baseline
    confirmed = []
    removed   = []
    for s in selections:
        horse  = s.get("horse", "")
        m      = morning_sel_map.get(horse, {})
        m_dec  = float(m.get("decimal") or 0)
        c_dec  = float(s.get("decimal") or 0)
        going_now = _race_going(s)
        if going_now == "Going TBC":
            removed.append({"horse": horse, "reason": "going still TBC"})
            continue
        if m_dec > 0 and c_dec > 0:
            drift_pct = (c_dec - m_dec) / m_dec * 100.0
            if drift_pct >= 30.0:
                removed.append({"horse": horse, "reason": f"drifted {drift_pct:.0f}% since 08:00"})
                continue
        confirmed.append(s)

    # Going changes since morning
    going_changes = []
    try:
        for m in going:
            course_name = m.get("course", "")
            curr_going  = m.get("going", "")
            for hname, hd in morning_sel_map.items():
                if hd.get("course", "") == course_name:
                    # we don't store morning going on the selection — best effort
                    break
    except Exception:
        going_changes = []

    # v2.5.65 — hard-exclude dominant rivals (gap > 25%) from the Bet A/B pool.
    # These horses can still appear in the reference list with a DOM flag, but
    # they should never feed staking. Threshold lowered from 35% to 25% to catch
    # cases like Tales of Wisdom (2.91x) vs Ray Mon Dough (1.91x) ≈ 34% gap that
    # were just clearing the old gate. Engine.staking._build_bet now also
    # rejects ANY DOM-flagged horse from Bet A regardless of gap, so the
    # numerical threshold here is a belt-and-braces upstream filter.
    def _dom_gap_pct(s):
        try:
            our = float(s.get("decimal", 0) or 0)
            riv = float(s.get("dominant_rival_price", 0) or 0)
        except (TypeError, ValueError):
            return 0.0
        if our <= 0 or riv <= 0:
            return 0.0
        return (1.0 - riv / our) * 100.0

    bet_pool = []
    dom_excluded = []
    for s in confirmed:
        if bool(s.get("dominant_rival", False)) and _dom_gap_pct(s) > 25.0:
            dom_excluded.append(s)
            continue
        bet_pool.append(s)

    # v2.6.6 — persist confirmed selections so the 21:00 evening summary
    # can compute P&L (the live racecard returns 0 horses by then).
    try:
        _save_daily_selections_snapshot(bet_pool)
    except Exception as _snap_err:
        print(f"[Confirmed] Daily selections snapshot failed: {_snap_err}")

    body = ""

    # 1. Urgency banner
    body += (
        '<div style="background:#e8a33d;color:#1a1a1a;border-radius:10px;'
        'padding:14px 16px;margin-bottom:12px;text-align:center;">'
        '<div style="font-size:18px;font-weight:bold;letter-spacing:0.5px;">'
        'PLACE BETS NOW</div>'
        '<div style="font-size:13px;margin-top:4px;">'
        'First afternoon race 14:00 BST &middot; BOG available at most bookmakers'
        '</div></div>'
    )

    # ── SECTION 1 — ADVISED BETS (prominent, at top) ───────────────────────
    # Only Bet A (top 4 by confidence) and Bet B (top 5 by confidence) — these
    # are the only horses to actually back today. Sorted-by-confidence is
    # enforced inside engine.staking.get_bet_a / get_bet_b (v2.5.62).
    body += (
        '<div style="font-size:14px;font-weight:bold;color:#fff;letter-spacing:1px;'
        'text-transform:uppercase;padding:6px 4px;margin:4px 0 8px;'
        'border-bottom:2px solid #e8a33d;">'
        'Advised bets — back these only</div>'
    )

    if bet_pool:
        try:
            from engine.staking import get_daily_bets as _get_daily_bets
            _bets = _get_daily_bets(bet_pool)
        except Exception as _fb_err:
            print(f"[Confirmed] Daily bets failed: {_fb_err}")
            _bets = {"bet_a": {"skipped": True}, "bet_b": {"skipped": True}}

        _ba = _bets.get("bet_a") or {}
        _bb = _bets.get("bet_b") or {}
        if not _ba.get("skipped"):
            body += _bet_card_mobile(_ba, "BET A (Lucky 15 + Singles)",
                                     "#2d7a3a", "first race off")
        else:
            body += (
                '<div style="background:#1c1f2e;border-radius:10px;padding:10px 14px;'
                'margin-bottom:14px;color:#aaa;font-size:13px;">'
                'BET A requires 4+ qualifying selections — not enough today.'
                '</div>'
            )
        if not _bb.get("skipped"):
            body += _bet_card_mobile(_bb, "BET B (Lucky 31 + Singles)",
                                     "#01696F", "first race off")
        else:
            body += (
                '<div style="background:#1c1f2e;border-radius:10px;padding:10px 14px;'
                'margin-bottom:14px;color:#aaa;font-size:13px;">'
                'BET B requires 5+ qualifying selections — not enough today.'
                '</div>'
            )
    else:
        body += (
            '<div style="background:#1c1f2e;border-radius:10px;padding:14px 16px;'
            'margin-bottom:12px;border-left:4px solid #964219;">'
            '<div style="font-size:14px;color:#e0e0e0;">No qualifying horses for '
            'Bet A/B after applying price and dominant-rival filters.</div>'
            '</div>'
        )

    # ── SECTION 2 — Changes since 08:00 ────────────────────────────────────
    changes_html = ""
    if removed:
        for r in removed:
            changes_html += (
                f'<div style="font-size:14px;color:#d9534f;margin:4px 0;">'
                f'❌ REMOVED: {r["horse"]} — {r["reason"]}</div>'
            )
    if dom_excluded:
        for s in dom_excluded:
            _dr_name  = s.get("dominant_rival_name", "?") or "?"
            _dr_price = float(s.get("dominant_rival_price", 0) or 0)
            _gap      = _dom_gap_pct(s)
            changes_html += (
                f'<div style="font-size:14px;color:#d9534f;margin:4px 0;">'
                f'❌ EXCLUDED FROM BETS: {s.get("horse","?")} — DOM rival '
                f'{_dr_name} @ {_dr_price:.2f}x ({_gap:.0f}% gap)</div>'
            )
    # Significant price moves vs morning (>20%)
    move_lines = []
    for s in confirmed:
        m_dec = float((morning_sel_map.get(s.get("horse",""), {}) or {}).get("decimal") or 0)
        c_dec = float(s.get("decimal", 0) or 0)
        if m_dec > 0 and c_dec > 0:
            d = (c_dec - m_dec) / m_dec * 100.0
            if abs(d) >= 20.0:
                arrow = "↑ drifted" if d > 0 else "↓ steamed"
                col   = "#d9534f" if d > 0 else "#2d7a3a"
                move_lines.append(
                    f'<div style="font-size:14px;color:{col};margin:4px 0;">'
                    f'{arrow} {s.get("horse","?")} — {m_dec:.2f}x → {c_dec:.2f}x '
                    f'({d:+.0f}%)</div>'
                )
    changes_html += "".join(move_lines)
    changes_html += (
        f'<div style="font-size:14px;color:#2d7a3a;margin:4px 0;">'
        f'✅ {len(bet_pool)} qualifying selection{"s" if len(bet_pool)!=1 else ""} '
        f'(Bet A/B drawn from these by confidence)</div>'
    )
    body += (
        '<div style="background:#1c1f2e;border-radius:10px;padding:14px 16px;'
        'margin-bottom:12px;border-top:2px solid #01696F;">'
        '<div style="font-size:13px;font-weight:bold;color:#fff;letter-spacing:0.5px;'
        'text-transform:uppercase;margin-bottom:8px;">'
        'Changes since 08:00</div>'
        f'{changes_html}'
        '</div>'
    )

    # ── SECTION 3 — Full qualifying list (reference only) ─────────────────
    if confirmed:
        body += (
            '<div style="font-size:12px;color:#888;letter-spacing:0.5px;'
            'padding:8px 4px;margin-top:14px;font-style:italic;">'
            f'Full qualifying list ({len(confirmed)}) — for reference only. '
            'Advised bets are above.</div>'
        )
        # Sort reference list by race time so user can scan the card
        for s in sorted(confirmed, key=lambda x: x.get("time", "")):
            body += _selection_card_mobile(s, snapshot, morning_prices)
    else:
        body += (
            '<div style="background:#1c1f2e;border-radius:10px;padding:14px 16px;'
            'margin-bottom:12px;border-left:4px solid #964219;">'
            '<div style="font-size:14px;color:#e0e0e0;">No confirmed selections remaining. '
            'All morning picks have either drifted 30%+ or still have unconfirmed going.</div>'
            '</div>'
        )

    return _mobile_email_shell(
        title       = "Confirmed Selections",
        header_color = "#e8a33d",
        label_text  = "Racing Engine | 13:30 BST",
        body_html   = body,
        footer_note = "This is your final advised bet list. "
                      "Next email: Evening Summary ~21:00 BST",
    )


def send_confirmed_selections() -> bool:
    """Called by the 13:30 BST cron. Sends the 'final word' confirmed list.
    v2.5.59 — hard 90s timeout via concurrent.futures; sends plain fallback
    if build hangs so the cron never times out silently.
    """
    # v2.6.3 — defensive cache clear (cron is fresh subprocess but be safe)
    try:
        from engine.odds_model import OddsModel
        OddsModel._SHOW_SNAPSHOT_CACHE["loaded"] = False
    except Exception:
        pass
    import concurrent.futures as _cf
    subject = f"Racing Engine — Confirmed Selections | {_date_bst()}"
    fallback_body = (
        f"Confirmed selections failed to generate. "
        f"Check dashboard for live selections before placing bets.\n"
        f"Dashboard: https://racing-engine-dash.streamlit.app (PIN: 1012)"
    )
    # Run build in a thread with hard 90s cap
    with _cf.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(build_confirmed_selections)
        try:
            html = future.result(timeout=180)
        except _cf.TimeoutError:
            print("[Confirmed] Build timed out after 90s — sending fallback")
            return send_email(subject, fallback_body, plain=True)
        except Exception as e:
            print(f"[Confirmed] Build failed: {e} — sending fallback")
            return send_email(subject, fallback_body, plain=True)
    try:
        return send_email(subject, html)
    except Exception as e:
        print(f"[Confirmed] Send failed: {e}")
        return False


# ── Email Type 2: Result Alert ─────────────────────────────────
def build_result_alert(horse: str, race: str, result: str,
                       sp: str, confidence: float, acc_still_live: bool,
                       remaining: list = None) -> str:
    """
    Fired instantly when a selection's result is known.
    result: 'WON' | 'LOST' | 'PLACED'
    """
    won  = result.upper() == "WON"
    col  = "#437A22" if won else "#A13544"
    icon = "WIN" if won else "LOSS"

    acc_note = ""
    if won and remaining:
        rem_names = " → ".join(r["horse"] for r in remaining)
        acc_note = f'<p style="font-size:13px;color:#888;margin:8px 0 0;">Accumulator still live: <strong style="color:#e0e0e0;">{rem_names}</strong></p>'
    elif won and not remaining:
        acc_note = '<p style="font-size:13px;color:#437A22;margin:8px 0 0;font-weight:bold;">All legs complete — accumulator WON.</p>'
    elif not won:
        acc_note = '<p style="font-size:13px;color:#A13544;margin:8px 0 0;">Accumulator leg lost. Check BET 2 cover accumulator — may still pay out.</p>'

    result_block = f"""
    <div style="text-align:center;padding:16px 0;">
      <div style="font-size:28px;font-weight:bold;color:{col};">{icon}</div>
      <div style="font-size:20px;font-weight:bold;color:#ffffff;margin-top:4px;">{horse}</div>
      <div style="font-size:13px;color:#888;margin-top:2px;">{race} &nbsp;|&nbsp; SP: {sp} &nbsp;|&nbsp; Conf: {int(confidence*100)}%</div>
      {acc_note}
    </div>"""

    body = _section("Result", result_block, col)

    return _email_shell(
        title       = f"{horse} — {result}",
        label_color = col,
        label_text  = icon,
        body_html   = body
    )


# ── Email Type 3: Evening Summary ──────────────────────────────
def build_evening_summary(results: list, selections: list, budget: float = 100.0,
                          extra_html: str = "", note: str = "") -> str:
    """
    Full day P&L once all races have run.
    results: list of dicts with horse/result/sp keys (matched against selections).
    selections: today's official selections list.

    v2.5.54 — Bet A (Lucky 15 + singles) and Bet B (Lucky 31 + singles).
    Each bet: £50 total. No accumulator. P&L computed from per-line winners.
    """
    try:
        from engine.staking import get_daily_bets
        daily = get_daily_bets(selections) or {}
    except Exception:
        daily = {}
    bet_a = daily.get("bet_a") if not (daily.get("bet_a") or {}).get("skipped") else None
    bet_b = daily.get("bet_b") if not (daily.get("bet_b") or {}).get("skipped") else None

    def _won_horse(name: str) -> bool:
        n = (name or "").lower().strip()
        return any((r.get("winner", "") or "").lower().strip() == n for r in results)

    winners = [s for s in selections if _won_horse(s.get("horse", ""))]
    losers  = [s for s in selections if s not in winners]

    def _eval_bet(bet):
        """Return (return_amount, total_stake, won_lines, total_lines, failed_horses)."""
        if not bet:
            return 0.0, 0.0, 0, 0, []
        sels  = bet.get("selections") or []
        lucky = bet.get("lucky_bet") or {}
        sgls  = bet.get("singles")   or {}
        n     = len(sels)

        win_flags = [_won_horse(s.get("name", "")) for s in sels]
        decs      = [float(s.get("decimal_odds", 0) or 0) for s in sels]

        # Lucky perm: enumerate every k-combination 1..n; winning lines pay.
        from itertools import combinations as _c
        line_stake = float(lucky.get("stake_per_line", 0.0) or 0.0)
        lucky_ret  = 0.0
        won_lines  = 0
        total_lines = 0
        for k in range(1, n + 1):
            for combo in _c(range(n), k):
                total_lines += 1
                if all(win_flags[i] for i in combo):
                    won_lines += 1
                    combined = 1.0
                    for i in combo:
                        combined *= decs[i]
                    lucky_ret += line_stake * combined

        # Singles
        single_stake_each = (sgls.get("stake", 0.0) / n) if n else 0.0
        singles_ret = sum(
            single_stake_each * decs[i]
            for i in range(n) if win_flags[i]
        )

        total_ret    = round(lucky_ret + singles_ret, 2)
        total_stake  = round(float(lucky.get("stake", 0.0)) + float(sgls.get("stake", 0.0)), 2)
        failed       = [s for s, w in zip(sels, win_flags) if not w]
        return total_ret, total_stake, won_lines, total_lines, failed

    bet_a_return, bet_a_stake, bet_a_won_lines, bet_a_total_lines, bet_a_failed = _eval_bet(bet_a)
    bet_b_return, bet_b_stake, bet_b_won_lines, bet_b_total_lines, bet_b_failed = _eval_bet(bet_b)

    bet_a_net = round(bet_a_return - bet_a_stake, 2) if bet_a_stake > 0 else 0.0
    bet_b_net = round(bet_b_return - bet_b_stake, 2) if bet_b_stake > 0 else 0.0

    total_staked = round(bet_a_stake + bet_b_stake, 2)
    net          = round((bet_a_return + bet_b_return) - total_staked, 2)

    # ── Per-bet membership lookup so the results table can tag each
    # selection with [Bet A] / [Bet B] / [—] for the punter.
    def _names_in(bet):
        if not bet:
            return set()
        return {(s.get("name") or "").lower().strip() for s in (bet.get("selections") or [])}
    bet_a_names = _names_in(bet_a)
    bet_b_names = _names_in(bet_b)

    bet_a_winners = sum(1 for n in bet_a_names if any(
        (r.get("winner", "") or "").lower().strip() == n for r in results))
    bet_b_winners = sum(1 for n in bet_b_names if any(
        (r.get("winner", "") or "").lower().strip() == n for r in results))

    def _bet_tag(horse: str) -> str:
        n = (horse or "").lower().strip()
        in_a = n in bet_a_names
        in_b = n in bet_b_names
        if in_a and in_b: return "Bet A+B"
        if in_a:          return "Bet A"
        if in_b:          return "Bet B"
        return "—"

    # ── Results card — every selection shown with WIN/LOSS, mobile-friendly.
    def _result_rows():
        if not selections:
            return ('<div style="padding:12px 0;color:#888;font-size:13px;">'
                    'No selections recorded for today.</div>')
        rows = []
        for s in sorted(selections, key=lambda x: (x.get("time", ""), x.get("course", ""))):
            won = _won_horse(s.get("horse", ""))
            tag = _bet_tag(s.get("horse", ""))
            sp_str = next(
                (r.get("sp", "") for r in results
                 if (r.get("winner", "") or "").lower().strip()
                    == (s.get("horse", "") or "").lower().strip()),
                "",
            )
            odds_disp = s.get("curr_odds") or s.get("odds") or ""
            if won and sp_str:
                odds_show = f"SP {sp_str}"
            else:
                odds_show = str(odds_disp) if odds_disp else "—"
            mark      = "✓" if won else "✗"
            mark_col  = "#437A22" if won else "#A13544"
            label     = "WON" if won else "LOST"
            rows.append(
                f'<div style="display:block;padding:9px 10px;'
                f'border-bottom:1px solid #2a2a2a;">'
                f'<table style="width:100%;border-collapse:collapse;"><tr>'
                f'<td style="width:22px;font-size:18px;font-weight:bold;color:{mark_col};'
                f'vertical-align:top;">{mark}</td>'
                f'<td style="vertical-align:top;">'
                f'<div style="font-size:14px;font-weight:bold;color:#fff;">{s.get("horse","")}</div>'
                f'<div style="font-size:12px;color:#888;margin-top:2px;">'
                f'{s.get("time","")} {s.get("course","")}'
                f'</div>'
                f'</td>'
                f'<td style="text-align:right;vertical-align:top;white-space:nowrap;">'
                f'<div style="font-size:13px;color:{mark_col};font-weight:bold;">{label}</div>'
                f'<div style="font-size:11px;color:#888;margin-top:2px;">{odds_show} · [{tag}]</div>'
                f'</td>'
                f'</tr></table>'
                f'</div>'
            )
        # Summary line
        a_n = len(bet_a_names)
        b_n = len(bet_b_names)
        a_pct = (100.0 * bet_a_winners / a_n) if a_n else 0.0
        b_pct = (100.0 * bet_b_winners / b_n) if b_n else 0.0
        a_summary = (f"{bet_a_winners} winner{'s' if bet_a_winners != 1 else ''} "
                     f"from {a_n} Bet A selections ({a_pct:.0f}%)") if a_n else "Bet A: no qualifying selections"
        b_summary = (f"{bet_b_winners} winner{'s' if bet_b_winners != 1 else ''} "
                     f"from {b_n} Bet B selections ({b_pct:.0f}%)") if b_n else "Bet B: no qualifying selections"
        rows.append(
            f'<div style="padding:10px 10px 4px;font-size:12px;color:#aaa;">'
            f'{a_summary}<br>{b_summary}'
            f'</div>'
        )
        return "".join(rows)

    results_block = (
        f'<div style="background:#1c1f2e;border-radius:8px;overflow:hidden;">'
        f'{_result_rows()}'
        f'</div>'
    )

    # ── Per-bet P&L breakdown (actual money, not percentages).
    def _bet_breakdown(bet, stake, ret, won_lines, total_lines, label, net_b):
        if not bet or stake <= 0:
            why = "needs 5+ qualifying selections" if label == "BET B" \
                  else "needs 4+ qualifying selections"
            return (
                f'<div style="background:#1c1f2e;border-radius:8px;'
                f'padding:12px 14px;margin:8px 0;border-left:3px solid #555;">'
                f'<div style="font-size:13px;font-weight:bold;color:#aaa;'
                f'letter-spacing:0.4px;text-transform:uppercase;">{label}</div>'
                f'<div style="font-size:13px;color:#888;margin-top:4px;">'
                f'Not run today — {why}.</div>'
                f'</div>'
            )
        n         = len(bet.get("selections") or [])
        lucky     = bet.get("lucky_bet") or {}
        sgls      = bet.get("singles")   or {}
        lucky_lbl = lucky.get("label", "Lucky")
        lucky_stk = float(lucky.get("stake", 0.0) or 0.0)
        sgls_stk  = float(sgls.get("stake", 0.0) or 0.0)

        # Singles breakdown
        win_flags = []
        decs      = []
        winning_singles_lines = []
        losing_count = 0
        single_each  = round(sgls_stk / n, 2) if n else 0.0
        singles_ret  = 0.0
        for sel in (bet.get("selections") or []):
            won = _won_horse(sel.get("name", ""))
            dec = float(sel.get("decimal_odds", 0) or 0)
            win_flags.append(won)
            decs.append(dec)
            if won:
                ret_one = round(single_each * dec, 2)
                profit  = round(ret_one - single_each, 2)
                singles_ret += ret_one
                winning_singles_lines.append(
                    f'<div style="font-size:12px;color:#e0e0e0;margin-left:14px;">'
                    f'{sel.get("name","?")} @ {dec:.2f} → '
                    f'£{ret_one:.2f} return (+£{profit:.2f})</div>'
                )
            else:
                losing_count += 1
        singles_loss = round(losing_count * single_each, 2)
        singles_net  = round(singles_ret - sgls_stk, 2)

        # Lucky perm net
        lucky_ret = round(ret - singles_ret, 2)
        lucky_net = round(lucky_ret - lucky_stk, 2)
        lucky_status = (f"all {total_lines} lines lose"
                        if won_lines == 0 else
                        f"{won_lines}/{total_lines} lines won")

        net_col = "#437A22" if net_b >= 0 else "#A13544"
        net_str = (f"+£{net_b:.2f}" if net_b >= 0 else f"-£{abs(net_b):.2f}")

        winning_html = "".join(winning_singles_lines) or (
            '<div style="font-size:12px;color:#888;margin-left:14px;">No singles won.</div>'
        )

        return (
            f'<div style="background:#1c1f2e;border-radius:8px;'
            f'padding:12px 14px;margin:8px 0;border-left:3px solid {net_col};">'
            f'<div style="font-size:13px;font-weight:bold;color:#fff;'
            f'letter-spacing:0.4px;text-transform:uppercase;margin-bottom:6px;">'
            f'{label} — {lucky_lbl} (£{lucky_stk:.2f}) + Singles (£{sgls_stk:.2f})</div>'
            f'<div style="font-size:12px;color:#aaa;margin-top:6px;">'
            f'<b style="color:#e0e0e0;">Singles</b> '
            f'(£{single_each:.2f} each × {n}):</div>'
            f'{winning_html}'
            f'<div style="font-size:12px;color:#aaa;margin-left:14px;">'
            f'{losing_count} lost = -£{singles_loss:.2f} '
            f'&middot; singles net: <b style="color:{("#437A22" if singles_net>=0 else "#A13544")};">'
            f'{("+" if singles_net>=0 else "-")}£{abs(singles_net):.2f}</b></div>'
            f'<div style="font-size:12px;color:#aaa;margin-top:8px;">'
            f'<b style="color:#e0e0e0;">{lucky_lbl}</b>: £{lucky_ret:.2f} return on '
            f'£{lucky_stk:.2f} stake &middot; {lucky_status} &middot; net '
            f'<b style="color:{("#437A22" if lucky_net>=0 else "#A13544")};">'
            f'{("+" if lucky_net>=0 else "-")}£{abs(lucky_net):.2f}</b>'
            f'</div>'
            f'<div style="font-size:14px;font-weight:bold;color:{net_col};'
            f'margin-top:10px;letter-spacing:0.4px;">'
            f'{label} NET: {net_str}</div>'
            f'</div>'
        )

    pl_block = (
        _bet_breakdown(bet_a, bet_a_stake, bet_a_return, bet_a_won_lines,
                       bet_a_total_lines, "BET A", bet_a_net) +
        _bet_breakdown(bet_b, bet_b_stake, bet_b_return, bet_b_won_lines,
                       bet_b_total_lines, "BET B", bet_b_net)
    )

    # ── Net day footer
    net_col = "#437A22" if net >= 0 else "#A13544"
    net_str = f"+£{net:.2f}" if net >= 0 else f"-£{abs(net):.2f}"
    a_net_str = ("+" if bet_a_net >= 0 else "-") + f"£{abs(bet_a_net):.2f}"
    b_net_str = ("+" if bet_b_net >= 0 else "-") + f"£{abs(bet_b_net):.2f}"

    if total_staked <= 0:
        net_footer = (
            f'<div style="background:#1c1f2e;border-radius:8px;padding:14px 16px;'
            f'margin-top:8px;border-top:2px solid #888;text-align:center;">'
            f'<div style="font-size:13px;color:#aaa;">No bets placed today — '
            f'fewer than 4 qualifying selections.</div>'
            f'</div>'
        )
    else:
        net_footer = (
            f'<div style="background:#1c1f2e;border-radius:8px;padding:14px 16px;'
            f'margin-top:8px;border-top:2px solid {net_col};text-align:center;">'
            f'<div style="font-size:12px;color:#888;letter-spacing:0.5px;'
            f'text-transform:uppercase;">Net day</div>'
            f'<div style="font-size:24px;font-weight:bold;color:{net_col};'
            f'margin:4px 0;">{net_str}</div>'
            f'<div style="font-size:12px;color:#aaa;">'
            f'Bet A: {a_net_str} &middot; Bet B: {b_net_str} &middot; '
            f'Total staked £{total_staked:.2f}</div>'
            f'</div>'
        )

    body  = _section(f"Today's Results — {len(winners)}/{len(selections)} Winners",
                     results_block, "#01696F")
    body += _section("P&L Summary", pl_block + net_footer, net_col)
    if note:
        body += _section(
            "Note",
            f"<div style='font-size:12px;color:#A1873B;'>{note}</div>",
            "#A1873B",
        )
    if extra_html:
        body += extra_html

    return _email_shell(
        title       = f"Evening Summary — {len(winners)}/{len(selections)} Winners",
        label_color = "#01696F",
        label_text  = "Evening Summary",
        body_html   = body
    )


# ── Email Type 4: Market Alert ─────────────────────────────────
def build_market_alert(horse: str, race: str, move_type: str,
                       from_odds: str, to_odds: str, move_pct: float) -> str:
    """
    Fired when a selection shortens or drifts significantly.
    move_type: 'STEAM' | 'DRIFT'
    """
    col  = "#437A22" if move_type == "STEAM" else "#A13544"
    desc = "shortening" if move_type == "STEAM" else "drifting out"

    alert_block = f"""
    <div style="text-align:center;padding:16px 0;">
      <div style="font-size:20px;font-weight:bold;color:#ffffff;">{horse}</div>
      <div style="font-size:13px;color:#888;margin:4px 0;">{race}</div>
      <div style="font-size:24px;font-weight:bold;color:{col};margin:8px 0;">
        {from_odds} → {to_odds} &nbsp; ({move_pct:.0f}% {desc})
      </div>
      <div style="font-size:12px;color:#888;">
        {'Smart money arriving — price shortening.' if move_type == 'STEAM'
         else 'Market cooling — price drifting out.'}
      </div>
    </div>"""

    body = _section(f"{move_type} — {horse}", alert_block, col)

    return _email_shell(
        title       = f"{move_type}: {horse} {from_odds} → {to_odds}",
        label_color = col,
        label_text  = f"Market {move_type}",
        body_html   = body
    )


# ── Pre-Race Alert (30 minutes before off) ─────────────────────
def _fetch_live_price(horse: str, course: str, race_time: str) -> float:
    """Best-effort fresh price lookup. Returns 0.0 if not found."""
    try:
        from dashboard.live_data import get_todays_selections
        df = get_todays_selections()
        if df is None or len(df) == 0:
            return 0.0
        hn, cn, tn = horse.lower().strip(), course.lower().strip(), race_time.strip()
        for _, r in df.iterrows():
            if (str(r.get("Horse", "")).lower().strip() == hn
                    and str(r.get("Course", "")).lower().strip() == cn
                    and str(r.get("Time", "")).strip() == tn):
                odds_s = str(r.get("Current Odds", "") or r.get("Odds", "")).strip()
                try:
                    return _to_decimal(odds_s)
                except Exception:
                    return 0.0
    except Exception:
        pass
    return 0.0


def send_prerace_alert(selection: dict) -> bool:
    """
    Send a single-race pre-race alert ~30 minutes before the off.

    selection keys used: horse, course, time, decimal (morning price),
    is_fav, rival_top_trainer, rival_trainer_name, odds (fractional morning).
    Fetches a fresh live price, computes move, and emits GO/MONITOR/AVOID.
    """
    horse     = str(selection.get("horse", "")) or "Unknown"
    course    = str(selection.get("course", "")) or ""
    race_time = str(selection.get("time", "")) or ""

    morning_dec   = float(selection.get("decimal", 0) or 0)
    morning_odds  = str(selection.get("odds", "N/A") or selection.get("curr_odds", "N/A"))
    live_dec      = _fetch_live_price(horse, course, race_time)
    live_odds     = f"{live_dec:.2f}" if live_dec > 0 else "N/A"

    # Price move (positive = shortened = steamer)
    move_pct = 0.0
    if morning_dec > 0 and live_dec > 0:
        move_pct = (morning_dec - live_dec) / morning_dec * 100.0

    if move_pct >= 5.0:
        move_line = f"Shortened from {morning_odds} to {live_odds} — ✅ STEAMING"
    elif move_pct <= -5.0:
        move_line = f"Drifted from {morning_odds} to {live_odds} — ⚠ MONITOR"
    else:
        move_line = f"Price stable {morning_odds} → {live_odds}"

    still_fav         = bool(selection.get("is_fav", False))
    rival_flag        = bool(selection.get("rival_top_trainer", False))
    rival_name        = str(selection.get("rival_trainer_name", "") or "")

    # Signal
    drifted_hard = move_pct <= -20.0
    if drifted_hard:
        signal = "AVOID"
    elif move_pct >= 5.0 and still_fav and not rival_flag:
        signal = "GO"
    elif not still_fav or move_pct < 0 or rival_flag:
        signal = "MONITOR"
    else:
        signal = "MONITOR"

    subject = f"⚡ PRE-RACE: {horse} @ {race_time} {course} — {signal}"

    rows = [
        ("Horse",         horse),
        ("Race",          f"{race_time} {course}"),
        ("Current price", live_odds),
        ("Morning price", morning_odds),
        ("Price move",    move_line),
        ("Still fav?",    "Yes" if still_fav else "No"),
        ("Top trainer danger?",
         (f"Yes — {rival_name}" if rival_flag else "No")),
        ("Signal",        signal),
    ]
    table = "".join(
        f'<tr><td style="padding:6px 10px;color:#888;font-size:12px;">{k}</td>'
        f'<td style="padding:6px 10px;font-size:13px;font-weight:bold;">{v}</td></tr>'
        for k, v in rows
    )
    body = _section(f"Pre-Race Check — {horse}",
                    f'<table style="width:100%;border-collapse:collapse;">{table}</table>',
                    "#01696F")
    html = _email_shell(
        title       = f"PRE-RACE {signal}: {horse}",
        label_color = "#01696F",
        label_text  = "Pre-Race Alert",
        body_html   = body,
    )
    return send_email(subject, html)


def schedule_prerace_alerts() -> list:
    """
    Build a schedule of (send_time_utc, selection) tuples for today's selections
    that haven't started yet. Each alert fires 30 minutes before the race (BST),
    returned in UTC so an external cron can consume it directly.
    """
    out = []
    try:
        selections = _get_official_selections()
    except Exception as _e:
        print(f"[PreRace] Unable to load selections: {_e}")
        return out

    now_bst = datetime.now(_LONDON)
    today_d = now_bst.date()

    for s in selections:
        t_str = str(s.get("time", "")).strip()
        if not t_str or ":" not in t_str:
            continue
        try:
            hh, mm = t_str.split(":")
            race_dt_bst = datetime(
                today_d.year, today_d.month, today_d.day,
                int(hh), int(mm), tzinfo=_LONDON,
            )
        except Exception:
            continue
        send_bst = race_dt_bst - timedelta(minutes=30)
        if send_bst <= now_bst:
            continue  # already past
        send_utc = send_bst.astimezone(zoneinfo.ZoneInfo("UTC"))
        out.append((send_utc, s))

    out.sort(key=lambda x: x[0])
    return out


# ── Email Sender ───────────────────────────────────────────────
def send_email(subject: str, html_content: str, recipient: str = RECIPIENT, plain: bool = False) -> bool:
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print(f"[Email] No credentials — skipping: {subject}")
        return False
    try:
        if plain:
            msg = MIMEText(html_content, "plain")
            msg["Subject"] = subject
            msg["From"]    = SENDER_EMAIL
            msg["To"]      = recipient
        else:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = SENDER_EMAIL
            msg["To"]      = recipient
            msg.attach(MIMEText(html_content, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, recipient, msg.as_string())
        print(f"[Email] Sent: {subject}")
        return True
    except Exception as e:
        print(f"[Email] Failed: {e}")
        return False


# ── Top-level convenience functions (used by crons) ───────────
def send_morning_brief(budget: float = 100.0):
    """Called directly by the 10:00 BST cron. Checks feed is live before sending."""
    # v2.6.3 — defensive cache clear (cron is fresh subprocess but be safe)
    try:
        from engine.odds_model import OddsModel
        OddsModel._SHOW_SNAPSHOT_CACHE["loaded"] = False
    except Exception:
        pass
    # Guard: check feed has today's selections before building brief
    try:
        _test_sels = _get_official_selections()
    except Exception:
        _test_sels = []

    if not _test_sels:
        # Feed not ready — send a minimal holding email rather than stale data
        import smtplib
        from email.mime.text import MIMEText
        SENDER_EMAIL = "racingengine.sender@gmail.com"
        SENDER_PASS  = "aase pwst fcbf smfs"
        RECIPIENT    = "richardking123@outlook.com"
        msg = MIMEText(
            f"Racing Engine — Morning Brief\n"
            f"{_date_bst()} | {_now_bst()} BST\n\n"
            "Feed not yet available — no qualifying selections at this time.\n"
            "Markets typically go live 10:00–10:30 BST.\n"
            f"Check dashboard: https://racing-engine-dash.streamlit.app (PIN: 1012)",
            "plain"
        )
        msg["Subject"] = f"Racing Engine — Brief Pending | {_date_bst()}"
        msg["From"]    = SENDER_EMAIL
        msg["To"]      = RECIPIENT
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
                srv.login(SENDER_EMAIL, SENDER_PASS)
                srv.sendmail(SENDER_EMAIL, RECIPIENT, msg.as_string())
            print("[Brief] Feed not ready — holding email sent")
        except Exception as e:
            print(f"[Brief] Holding email failed: {e}")
        return False

    # ── ML: log today's selections as recommendations ────────────
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from learning.loop import LearningLoop
        loop = LearningLoop()
        rec_count = loop.auto_record_day()
        print(f"[ML] Logged {rec_count} recommendations for today")
    except Exception as _ml_err:
        print(f"[ML] auto_record_day skipped: {_ml_err}")

    # Hard 90s timeout on build — sends fallback if hangs
    import concurrent.futures as _cf
    subject = f"Racing Engine — Morning Brief | {_date_bst()}"
    with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
        _fut = _pool.submit(build_morning_brief, budget)
        try:
            html = _fut.result(timeout=180)
        except _cf.TimeoutError:
            print("[Brief] build_morning_brief timed out — sending fallback")
            send_email(subject,
                "Morning brief timed out. Check dashboard: "
                "https://racing-engine-dash.streamlit.app (PIN: 1012)",
                plain=True)
            return False
        except Exception as _e:
            print(f"[Brief] build_morning_brief failed: {_e}")
            return False
    ok = send_email(subject, html)

    # ── Early-race pre-race alerts ───────────────────────────────
    # Cron granularity is 1h, so we can't fire every 5 minutes. Instead, when
    # the morning brief lands (~10:00 BST), immediately send pre-race alerts
    # for any race starting between 10:00 and 11:30 BST — the punter has
    # little time to act on these.
    try:
        _send_prerace_window(start_hhmm="10:00", end_hhmm="11:30", label="early")
    except Exception as _e:
        print(f"[PreRace] Early window failed: {_e}")

    return ok


def _send_prerace_window(start_hhmm: str, end_hhmm: str, label: str = "") -> int:
    """Send ONE consolidated pre-race alert email covering all selections
    whose race time falls in [start_hhmm, end_hhmm] BST.
    v2.5.61 — was sending one email per horse (19 emails). Now single digest.
    Returns 1 if email sent, 0 otherwise.
    """
    try:
        selections = _get_official_selections()
    except Exception as _e:
        print(f"[PreRace] {label}: unable to load selections: {_e}")
        return 0

    def _parse_hhmm(s: str) -> int:
        try:
            hh, mm = str(s).strip().split(":")
            return int(hh) * 60 + int(mm)
        except Exception:
            return -1

    lo = _parse_hhmm(start_hhmm)
    hi = _parse_hhmm(end_hhmm)
    if lo < 0 or hi < 0:
        return 0

    # Filter selections in this window
    in_window = []
    for s in selections:
        t_min = _parse_hhmm(str(s.get("time", "")))
        if t_min >= 0 and lo <= t_min <= hi:
            in_window.append(s)

    if not in_window:
        print(f"[PreRace] {label}: no selections in window {start_hhmm}-{end_hhmm} BST")
        return 0

    # Build ONE consolidated email
    date_str = _date_bst()
    subject  = f"Racing Engine — Pre-Race Alerts | {start_hhmm}–{end_hhmm} BST | {date_str}"

    rows = ""
    for s in sorted(in_window, key=lambda x: x.get("time", "")):
        horse   = s.get("horse", "?")
        course  = s.get("course", "?")
        time_   = s.get("time", "?")
        odds    = s.get("best_odds") or s.get("decimal_odds") or s.get("odds", "?") 
        conf    = s.get("confidence", 0)
        mkt_pos = s.get("market_position_label", "")
        move    = s.get("market_move", "")
        dom     = " ⚠️ DOM" if s.get("dominant_rival") else ""
        yg      = " ⚠️ YG" if s.get("yg_risk") else ""
        conf_pct = f"{conf*100:.0f}%" if isinstance(conf, float) else str(conf)
        move_str = f" | {move}" if move else ""
        rows += f"""
        <tr>
          <td style="padding:8px;font-weight:bold">{time_} {course}</td>
          <td style="padding:8px">{horse}</td>
          <td style="padding:8px">{odds}</td>
          <td style="padding:8px">{conf_pct}</td>
          <td style="padding:8px">{mkt_pos}{move_str}{dom}{yg}</td>
        </tr>"""

    html = f"""
    <html><body style="font-family:sans-serif;background:#0d1117;color:#e6edf3;padding:20px">
    <h2 style="color:#f0a500">Racing Engine — Pre-Race Alerts</h2>
    <p style="color:#8b949e">{date_str} | {start_hhmm}–{end_hhmm} BST | {len(in_window)} selection(s)</p>
    <table style="width:100%;border-collapse:collapse;margin-top:16px">
      <thead>
        <tr style="background:#161b22;color:#8b949e;font-size:12px">
          <th style="padding:8px;text-align:left">Race</th>
          <th style="padding:8px;text-align:left">Horse</th>
          <th style="padding:8px;text-align:left">Odds</th>
          <th style="padding:8px;text-align:left">Conf</th>
          <th style="padding:8px;text-align:left">Market</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <p style="color:#8b949e;margin-top:24px;font-size:12px">
      Dashboard: <a href="https://racing-engine-dash.streamlit.app" style="color:#58a6ff">racing-engine-dash.streamlit.app</a> (PIN: 1012)
    </p>
    </body></html>"""

    ok = send_email(subject, html)
    print(f"[PreRace] {label}: sent consolidated alert ({len(in_window)} horses) — {'OK' if ok else 'FAILED'}")
    return 1 if ok else 0


def send_afternoon_prerace_alerts():
    """DEPRECATED — replaced by send_late_prerace_alerts() via cron 909fe390 at 13:30 BST.
    Cron 48f28f46 (12:30 BST duplicate) has been decommissioned.
    This function exits silently to prevent duplicate alert emails."""
    import logging
    logging.getLogger(__name__).info(
        "send_afternoon_prerace_alerts() called by decommissioned cron 48f28f46 — exiting silently."
    )
    return 0  # silent exit — do not send emails


def send_late_prerace_alerts():
    """Called by a 14:30 BST cron. Pre-race alerts for races 15:00-18:30 BST."""
    return _send_prerace_window("15:00", "18:30", label="late")


def send_evening_summary(budget: float = 100.0):
    """Called directly by the 21:00 BST cron. Fetches live results + runs ML settlement.

    v2.6.4 — Settle FIRST, then build email. If auto_settle() finds no open
    recs (because the morning recording job didn't run or selections weren't
    logged), fall back to direct feed comparison so P&L still renders.
    """
    # v2.6.3 — defensive cache clear (cron is fresh subprocess but be safe)
    try:
        from engine.odds_model import OddsModel
        OddsModel._SHOW_SNAPSHOT_CACHE["loaded"] = False
    except Exception:
        pass
    today_str  = datetime.now(_LONDON).date().isoformat()
    print(f"[Evening] Fetching results for {today_str}")
    subject    = f"Racing Engine — Evening Summary | {_date_bst()}"

    # ── Step 1: Settle BEFORE building the email so the learning block
    #           reflects the most recent state.
    settled_count = 0
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from learning.loop import LearningLoop
        loop = LearningLoop()
        settled_count = loop.auto_settle()
        print(f"[ML] Settled {settled_count} races")
    except Exception as _ml_err:
        print(f"[ML] auto_settle error (non-fatal): {_ml_err}")

    # ── Step 2: Gather selections + results
    # v2.6.6 — Prefer the 13:00 confirmed-selections snapshot. By 21:00 the
    # live pipeline returns 0 horses (races finished, racecard no longer live)
    # so the snapshot is the only reliable source for today's actual bets.
    selections = _load_daily_selections_snapshot()
    if not selections:
        selections = _get_official_selections()
        if selections:
            print(f"[Evening] Used live pipeline fallback: {len(selections)} selections")
        else:
            print("[Evening] No selections available from snapshot or live pipeline")
    results    = _get_todays_results()

    # ── Step 3: Direct-from-feed fallback when settlement was empty.
    # Triggered when nothing was settled AND we have selections to score.
    note = ""
    if settled_count == 0 and selections:
        feed_results = _get_todays_results_from_feed(today_str)
        if feed_results:
            print(f"[Evening] Direct-feed fallback: {len(feed_results)} races")
            # Augment results list with feed entries so build_evening_summary's
            # winner-matching logic finds today's winners even if get_todays_results()
            # returned a sparse set.
            existing_winners = {
                (r.get("winner", "") or "").lower().strip()
                for r in (results or [])
            }
            for race_id, info in feed_results.items():
                w = (info.get("winner", "") or "").lower().strip()
                if w and w not in existing_winners:
                    results = (results or []) + [{
                        "race":   f"{info.get('time','')} {info.get('course','')}",
                        "winner": info.get("winner", ""),
                        "sp":     info.get("odds", "") or "—",
                        "date":   today_str,
                    }]
                    existing_winners.add(w)
            note = (
                "Selection records were not in the learning loop for this date — "
                "P&L computed by direct cross-reference against the live results feed."
            )

    if not results and not selections:
        # Nothing to report — feed and selections both empty.
        import smtplib
        from email.mime.text import MIMEText
        SENDER_EMAIL = "racingengine.sender@gmail.com"
        SENDER_PASS  = "aase pwst fcbf smfs"
        RECIPIENT    = "richardking123@outlook.com"
        msg = MIMEText(
            f"Racing Engine — Evening Summary\n"
            f"{_date_bst()} | {_now_bst()} BST\n\n"
            f"No results or selections available for {today_str}.\n"
            "Results feed returned 0 races — sending holding notice rather than stale data.\n"
            "Check again later or review the dashboard directly.",
            "plain"
        )
        msg["Subject"] = subject
        msg["From"]    = SENDER_EMAIL
        msg["To"]      = RECIPIENT
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
                srv.login(SENDER_EMAIL, SENDER_PASS)
                srv.sendmail(SENDER_EMAIL, RECIPIENT, msg.as_string())
            print(f"[Evening] No results for {today_str} — fallback email sent")
        except Exception as e:
            print(f"[Evening] Fallback email failed: {e}")
        return False

    # ── Step 3b: Cross-reference results against selections (v2.6.6)
    results_by_winner = {}
    for r in (results or []):
        w = (r.get("winner", "") or "").lower().strip()
        if w:
            results_by_winner[w] = r
    for sel in (selections or []):
        horse_lower = (sel.get("horse", "") or "").lower().strip()
        if horse_lower in results_by_winner:
            sel["won"] = True
            sel["result_sp"] = results_by_winner[horse_lower].get("sp", "")
        else:
            sel["won"] = False

    # ── Step 4: Build learning block + email
    learning_block = ""
    try:
        learning_block = _build_learning_update_block(today_str)
    except Exception as _lb_err:
        print(f"[Evening] Learning block render skipped: {_lb_err}")

    html = build_evening_summary(
        results, selections, budget,
        extra_html=learning_block,
        note=note,
    )
    ok = send_email(subject, html)

    # ── Step 5: Adjust weights + loss analysis (post-email so a slow
    # ML pass never delays the summary).
    try:
        from learning.loop import LearningLoop as _LL
        loop = _LL()
        new_weights = loop.adjust_weightings()
        print(f"[ML] Weights updated: {new_weights}")

        try:
            from learning.loss_analyser import diagnose_loss
            recs = loop.recommendations.get("records", [])
            today_losses = [
                r for r in recs
                if r.get("date") == today_str and r.get("won") is False
            ]
            for loss_rec in today_losses:
                diagnose_loss({
                    "horse":       loss_rec.get("runner", ""),
                    "course":      loss_rec.get("course", ""),
                    "race_type":   loss_rec.get("race_type", ""),
                    "confidence":  loss_rec.get("confidence", 0),
                    "odds":        loss_rec.get("odds", "N/A"),
                    "signals":     loss_rec.get("signals", {}),
                    "field_size":  loss_rec.get("field_size", 0),
                    "is_handicap": loss_rec.get("is_handicap", False),
                })
            print(f"[ML] Diagnosed {len(today_losses)} losses")
        except Exception as _la_err:
            print(f"[ML] Loss analyser skipped: {_la_err}")

    except Exception as _ml_err:
        print(f"[ML] Post-email ML error (non-fatal): {_ml_err}")

    return ok


# ── Dispatcher ─────────────────────────────────────────────────
class DailyBrief:

    def send_morning_brief(self, budget: float = 100.0):
        html    = build_morning_brief(budget)
        subject = f"Racing Engine — Morning Brief | {_date_bst()}"
        send_email(subject, html)

    def send_result_alert(self, horse: str, race: str, result: str,
                          sp: str, confidence: float,
                          acc_still_live: bool, remaining: list = None):
        html    = build_result_alert(horse, race, result, sp,
                                     confidence, acc_still_live, remaining)
        subject = f"Racing Engine — {horse} {result} | {_now_bst()} BST"
        send_email(subject, html)

    def send_evening_summary(self, results: list, selections: list, budget: float = 100.0):
        html    = build_evening_summary(results, selections, budget)
        subject = f"Racing Engine — Evening Summary | {_date_bst()}"
        send_email(subject, html)

    def send_market_alert(self, horse: str, race: str, move_type: str,
                          from_odds: str, to_odds: str, move_pct: float):
        html    = build_market_alert(horse, race, move_type, from_odds, to_odds, move_pct)
        subject = f"Racing Engine — {move_type}: {horse} | {_now_bst()} BST"
        send_email(subject, html)


# ── Operator Daily Brief ────────────────────────────────────────
def send_operator_brief():
    """
    Sends a plain-text operator briefing to richardking123@outlook.com at 08:00 BST.
    Designed to be pasted directly into a new Computer/AI session to instantly
    resume work on the racing engine without needing any prior context.
    """
    import os, json, datetime, zoneinfo

    _LONDON  = zoneinfo.ZoneInfo("Europe/London")
    now      = datetime.datetime.now(_LONDON)
    date_str = now.strftime("%A, %d %B %Y")
    time_str = now.strftime("%H:%M BST")

    # ── Version ──────────────────────────────────────────────────
    try:
        import subprocess
        version = subprocess.check_output(
            ["git", "describe", "--tags", "--always"],
            cwd="/home/user/workspace/racing-engine",
            stderr=subprocess.DEVNULL
        ).decode().strip()
        if not version:
            raise RuntimeError("empty git describe")
    except Exception:
        try:
            app_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "app.py")
            version  = "v2.5.29"
            with open(app_path) as f:
                for line in f:
                    if line.strip().startswith("VERSION"):
                        version = line.split('"')[1] if '"' in line else line.split("'")[1]
                        break
        except Exception:
            version = "v2.5.29"

    # ── ML learning status ───────────────────────────────────────
    recs_count = 0
    settled_count = 0
    wins_count = 0
    results_count = 0
    try:
        recs_path     = os.path.join(os.path.dirname(__file__), "..", "learning", "recommendations.json")
        results_path  = os.path.join(os.path.dirname(__file__), "..", "learning", "results_store.json")
        if os.path.exists(recs_path):
            _recs_raw = json.load(open(recs_path))
            _recs = _recs_raw.get("records", []) if isinstance(_recs_raw, dict) else _recs_raw
            # v2.6.5 — exclude legacy "all_runners" backfill so the brief
            # doesn't claim "65 selections, 7 wins" off one noisy day.
            _recs = [r for r in _recs if r.get("source") != "all_runners"]
            recs_count = len(_recs)
            settled_count = len([r for r in _recs if r.get("won") is not None])
            wins_count = len([r for r in _recs if r.get("won") is True])
        if os.path.exists(results_path):
            _res_raw = json.load(open(results_path))
            if isinstance(_res_raw, dict):
                _res = _res_raw.get("results", _res_raw.get("records", []))
            else:
                _res = _res_raw
            results_count = len(_res)
    except Exception:
        pass

    # ── Git status ───────────────────────────────────────────────
    try:
        import subprocess
        git_log = subprocess.check_output(
            ["git", "-C", os.path.join(os.path.dirname(__file__), ".."),
             "log", "--oneline", "-5"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        git_log = "unavailable"

    # ── Loose ends from learning/results store ──────────────────
    try:
        settled = json.load(open(os.path.join(os.path.dirname(__file__), "..", "learning", "settled_races.json")))
        _races = settled.get("races", []) if isinstance(settled, dict) else settled
        if not _races:
            pending_str = "settled_races.json is EMPTY — auto_settle() may not be writing back (see KNOWN BUGS)"
        else:
            pending_results = [r for r in _races if not r.get("outcome_recorded")]
            pending_str = str(len(pending_results)) + " races awaiting outcome recording"
    except Exception:
        pending_str = "unavailable"

    body = f"""RACING ENGINE — OPERATOR BRIEF
{date_str} | {time_str}
Dashboard : https://racing-engine-dash.streamlit.app  PIN: 1012
Version   : {version}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

▶ HOW TO RESUME IN A NEW SESSION
──────────────────────────────────
Paste this ENTIRE email as your first message in any new session.
The AI will have full context and be ready to work immediately.
No other setup needed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠ CRITICAL OPERATOR ACTIONS (check these first every day)
───────────────────────────────────────────────────────────
1. After EVERY code push → MANUALLY REBOOT the Streamlit app
   Go to: https://racing-engine-dash.streamlit.app
   Click: hamburger menu (top right) → Settings → Reboot app
   Auto-redeploy is UNRELIABLE — always reboot manually.

2. Check morning brief arrived at 10:00 BST
   If missing → data feed may be down, check dashboard

3. Check evening summary arrived at 21:00 BST
   If missing → results feed may be down or selections empty

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SYSTEM RULES (CRITICAL — AI must follow at all times)
──────────────────────────────────────────────────────
1. Anonymity & security paramount. Push ALL code to GitHub
   with versioning. Repo: westham123/racing-engine (private).
2. Begin every session by tidying loose ends from previous session.
3. Explain all technical terms clearly — user is not a coder.
4. When decisions required: always give 3 options + 1 recommendation.
5. Ask for confirmation before each build step.
6. End every session with a daily brief listing loose ends & next tasks.
7. Carry out ALL tasks possible — minimise actions required from user.
8. Phase 1 = personal research tool ONLY. No payments. No commercial.
9. Phase 2 (commercial) is future only — NEVER conflate the two phases.
10. VPN/anonymity measures NOT required for Phase 1.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DATA INTEGRITY RULES (CRITICAL — these must never be broken)
──────────────────────────────────────────────────────────────
• OFFICIAL SELECTION: a horse is official ONLY if it cleared BOTH
  the confidence threshold AND the evens (2.0) price cut-off. No exceptions.
• NO HARDCODED / SAMPLE DATA: all selections must come from the
  live Sporting Life feed. Never display example or fallback horses.
• NON-RUNNERS: must be stripped at EVERY output point — app Tab 1,
  morning brief, evening summary. NR gate runs fresh (uncached).
• ONE HORSE PER RACE: only the highest-confidence selection per race.
  Two horses in the same race is always a bug.
• NEVER conflate unofficial mentions (e.g. "Final Appeal",
  "Trust House") with official engine selections.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STAKING RULES (PERMANENT — do not change without user approval)
────────────────────────────────────────────────────────────────
Budget: £50 per bet (£100 total if both BET A and BET B active)
Short price cut-off: evens (2.0 decimal) — hard exclusion
Confidence threshold: 50% minimum (handicaps: 60%)
One horse per race: highest confidence only
Group/Listed/Grade races: excluded entirely

BET A (CORE) — top 4 selections by confidence
  Lucky 15: £20 stake across 15 combination bets (4 horses)
  Singles: £30 stake (£7.50 per horse)
  Total: £50

BET B (MID) — top 5 selections by confidence
  Lucky 31: £20 stake across 31 combination bets (5 horses)
  Singles: £30 stake (£6.00 per horse)
  Total: £50
  Only active when 5+ selections qualify

Oddschecker multi-bookmaker odds (v2.5.40):
  Best available price shown across 24 bookmakers
  Betfair Exchange price included via BF bookmaker code
  Fallback to Sporting Life if Oddschecker unavailable

Target: £2,000+ profit, uncapped.

Additional exclusion rules:
  - Dominant rival: excluded from Bet A if rival >25% shorter in price (any DOM-flagged horse hard-excluded from Bet A regardless)
  - Large fields: 16+ runners excluded entirely
  - Drifters: flagged in Tab 2, auto-drop rule under development

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCHEDULED CRONS (all times BST = UTC+1)
────────────────────────────────────────
[3fb7f776] 09:00 daily  — Operator Brief → richardking123@outlook.com
[4eac6ab1] 10:00 daily  — Morning Brief → richardking123@outlook.com
[a54556fb] 13:00 daily  — Confirmed Selections → richardking123@outlook.com
[909fe390] 14:30 daily  — Late Pre-Race Alerts (15:00–18:30 BST)
[c58b4236] 15:30 daily  — Show Price Baseline snapshot
[de70bd36] Hourly 16:09–07:09 — Market Movers (silent if nothing ≥30%)
[385f97ff] 21:00 daily  — Evening Summary → richardking123@outlook.com

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KEY FILES (workspace: /home/user/workspace/racing-engine)
──────────────────────────────────────────────────────────
engine/staking.py          — 2-bet fold staking engine (Bet A / Bet B, v2.5.39+)
engine/oddschecker.py      — 24-bookmaker best odds fetcher (Oddschecker, v2.5.40+)
engine/odds_model.py       — confidence scoring + hard exclusion filters
dashboard/app.py           — Streamlit dashboard (Tab 1 staking, Tab 2 runners)
dashboard/live_data.py     — Sporting Life __NEXT_DATA__ feed parser
dashboard/early_market.py  — market movers, show price snapshot
briefs/daily_brief.py      — all email builders + cron entry points
learning/                  — ML loop, loss analyser, learned weights

ARCHITECTURE NOTES:
  - Live data: Sporting Life __NEXT_DATA__ JSON
  - UTC→BST: zoneinfo.ZoneInfo("Europe/London") — always use this
  - OddsModel fails silently on Streamlit Cloud — raw-field fallback exists
  - Pool must be built BEFORE st.metric() renders (Streamlit top-to-bottom)
  - bookmakerOdds array = live price (current_odds field is stale)
  - NONRUNNER (no underscore) = what feed returns — normalised in live_data.py
  - Version auto-bumps on every commit via pre-commit hook

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MACHINE LEARNING STATUS
────────────────────────
Recommendations logged : {recs_count}
Settled with outcome   : {settled_count} ({wins_count} wins)
Results in store       : {results_count}
Trainer/jockey data    : from {results_count} races (builds over ~2 weeks)
Learned weights        : self-adjusting (evening loop recalibrates)
Learning loop status   : CLOSED — wired in v2.5.26
What's wired           : morning brief calls auto_record_day();
                         evening summary calls auto_settle(),
                         adjust_weightings() and diagnose_loss()

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RECENT GIT COMMITS
───────────────────
{git_log}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EXTERNAL SERVICES & CREDENTIALS
─────────────────────────────────
GitHub     : westham123/racing-engine (private repo)
             git push needs api_credentials=["github"] in bash tool
Streamlit  : https://racing-engine-dash.streamlit.app (PIN: 1012)
             *** MANUAL REBOOT REQUIRED AFTER EVERY PUSH ***
Gmail send : racingengine.sender@gmail.com / aase pwst fcbf smfs
Recipient  : richardking123@outlook.com
Betfair    : richardking123@outlook.com / Pa55word2018!
             App Key: 1Bj49mxBZBQ961WM — BSP data only (403 fail-fast)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KNOWN BUGS & NEXT BUILDS (in priority order)
──────────────────────────────────────────────
1. LEARNING DATA THIN
   {recs_count} selections logged, {results_count} results in store (from 2 May).
   Trainer/jockey form, market moves will activate as data accumulates (~2 weeks).
   Action: none needed — accumulates automatically.

2. IRISH TRACK PRICE COVERAGE
   Oddschecker does not cover Punchestown/Leopardstown.
   Horses from these venues qualify on Sporting Life SP only.

3. BETFAIR BSP — HTTP 403
   BSP data unavailable (app key 403). BSP treated as neutral in all scoring.
   Expected until Betfair resolves access.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LESSONS LEARNED (do not repeat these mistakes)
───────────────────────────────────────────────
• BET 1 + BET 2 were identical — fixed 22 Apr. Always verify pools differ.
• VALUE horses (4x+) were entering BET 1 — fixed 22 Apr. Never again.
• Non-runners (Yorkshire Glory, Wolfburg) were appearing as selections.
  Always run NR gate before ANY output.
• App was showing 24 selections — root cause: pool built inside tab block,
  rendered after KPI metric. Pool must always be built at TOP of script.
• applymap() removed in pandas 3.0 — use .style.map() instead.
• Streamlit Cloud does not reload automatically after push — always reboot.
• bookmakerOdds array has live price; current_odds field is stale.
• Snapshot date bug: snapshots were saving for tomorrow — fixed.
• 200/1 outsiders were appearing in market movers — now capped at 20x baseline.
• Fountain House 28 Apr — flagged dominant fav but Willitgoahead (G.Elliott) equally backed at 13/5.
  Split market rule added v2.5.48: if 2nd fav within 20% of our price, exclude from Bet A.

Racing Engine {version} | Phase 1 Personal Research Tool
"""

    subject = f"Racing Engine — Operator Brief | {date_str}"

    # Send as plain text
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    SENDER_EMAIL = "racingengine.sender@gmail.com"
    SENDER_PASS  = "aase pwst fcbf smfs"
    RECIPIENT    = "richardking123@outlook.com"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = SENDER_EMAIL
        msg["To"]      = RECIPIENT
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(SENDER_EMAIL, SENDER_PASS)
            srv.sendmail(SENDER_EMAIL, RECIPIENT, msg.as_string())
        print(f"[OperatorBrief] Sent to {RECIPIENT}")
        return True
    except Exception as e:
        print(f"[OperatorBrief] Send failed: {e}")
        return False
