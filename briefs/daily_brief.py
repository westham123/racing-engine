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
        movers = get_previous_day_moves(min_move_pct=0.15)
        if not movers or (isinstance(movers[0], dict) and "error" in movers[0]):
            return []
        return movers
    except Exception as e:
        print(f"[Brief] Overnight moves unavailable: {e}")
        return []


def _get_official_selections(conf_threshold: float = 0.55) -> list:
    """
    Returns only official selections: cleared threshold + 4/6 cut-off
    on the live engine. No fallback — returns empty list if feed is down.
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

        # ── Favourite gap lookup ─────────────────────────────────────────────
        # Build race-level shortest price lookup before iterating.
        # Excludes selections running against a dominant market leader (>35% gap).
        _FAV_GAP_PCT = 0.35
        _race_fav_price_brief = {}
        _race_fav_name_brief  = {}
        _race_runners_brief   = {}  # race_key -> list of {horse, trainer} dicts
        _race_all_prices_brief = {}  # race_key -> sorted list of decimal prices
        for _, _fr in df.iterrows():
            _frkey = f"{str(_fr.get('Time',''))}::{str(_fr.get('Course',''))}"
            _frodds = str(_fr.get('Current Odds','') or _fr.get('Odds','N/A')).strip()
            try:
                _frdec = _to_decimal(_frodds)
            except Exception:
                _frdec = 99.0
            if _frdec > 1.0:
                if _frkey not in _race_fav_price_brief or _frdec < _race_fav_price_brief[_frkey]:
                    _race_fav_price_brief[_frkey] = _frdec
                    _race_fav_name_brief[_frkey]  = str(_fr.get('Horse', ''))
                _race_all_prices_brief.setdefault(_frkey, []).append(_frdec)
            _race_runners_brief.setdefault(_frkey, []).append({
                "horse":   str(_fr.get("Horse", "")),
                "trainer": str(_fr.get("Trainer", "")),
            })

        # Compute 2nd-fav price per race for Yorkshire Glory / dominant-fav logic
        _race_second_fav_price = {}
        for _rk, _prices in _race_all_prices_brief.items():
            _sorted = sorted(_prices)
            if len(_sorted) >= 2:
                _race_second_fav_price[_rk] = _sorted[1]

        for _, row in df.iterrows():
            t = str(row.get("Time", ""))

            curr_str = str(row.get("Current Odds", "")).strip()
            odds_str = curr_str if curr_str and curr_str not in ("", "N/A", "None", "nan") \
                       else str(row.get("Odds", "Evs"))
            try:
                dec = _to_decimal(odds_str)
            except Exception:
                dec = 2.0

            if dec <= 1.67:
                continue  # 4/6 cut-off

            # Favourite gap check — skip if dominant market leader exists in this race
            _bracekey = f"{t}::{str(row.get('Course',''))}"
            _bfav_dec = _race_fav_price_brief.get(_bracekey, dec)
            if _bfav_dec < dec:
                _bgap = (dec - _bfav_dec) / _bfav_dec
                if _bgap > _FAV_GAP_PCT:
                    print(f"[Brief] Fav-gap excluded {row.get('Horse','')} @ {dec:.2f}x "
                          f"(fav @ {_bfav_dec:.2f}x, gap {_bgap:.0%})")
                    continue

            runner = {
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

            is_fav     = dec <= _bfav_dec + 1e-9
            fav_price  = round(float(_bfav_dec), 2)
            fav_name   = _race_fav_name_brief.get(_bracekey, "")
            _second_fav_dec = _race_second_fav_price.get(_bracekey)
            second_fav_price = round(float(_second_fav_dec), 2) if _second_fav_dec else 0.0

            # Gap to 2nd-fav (only meaningful when we ARE the fav).
            # gap_to_2nd = (2nd_price - our_price) / our_price
            if is_fav and second_fav_price > 0:
                gap_to_2nd = round((second_fav_price - dec) / dec, 4)
            else:
                gap_to_2nd = 0.0

            _runners = int(row.get("Field Size", row.get("Runners", 0)) or 0)

            # Dominant fav: we're the fav AND gap to 2nd ≥50%
            is_dominant_fav = bool(is_fav and gap_to_2nd >= 0.50)
            # Yorkshire Glory risk: field ≥10 AND gap to 2nd <50% (even if fav)
            yg_risk = bool(is_fav and _runners >= 10 and gap_to_2nd < 0.50)

            # Small-field non-fav exclusion: ≤6 runners and we're not the fav.
            # Market is better informed in small fields; edge is too thin.
            if _runners and _runners <= 6 and not is_fav:
                print(f"[Brief] Small-field non-fav excluded: {row.get('Horse','')} "
                      f"({_runners} runners, not fav)")
                continue

            # Large-field weak non-fav exclusion: ≥11 runners, not fav, <60% conf.
            # Competitive field without market support or strong conviction.
            if _runners >= 11 and not is_fav and conf < 0.60:
                print(f"[Brief] Large-field weak non-fav excluded: {row.get('Horse','')} "
                      f"({_runners} runners, {conf:.0%} conf, not fav)")
                continue

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
                "rival_top_trainer":  _rival_flag.get("rival_top_trainer", False),
                "rival_trainer_name": _rival_flag.get("rival_trainer_name", ""),
                "role":        ("BANKER" if (conf >= 0.63 and dec <= 4.00) else "VALUE"),
                "tier":        ("BANKER" if dec <= 2.50 else
                                "MID"    if dec <= 5.00 else
                                "VALUE"  if dec <= 10.0 else "LONGSHOT"),
                # Oddschecker multi-bookie fields (v2.5.40) — may be None
                "best_odds_decimal":    row.get("Best Odds Decimal"),
                "best_odds_fractional": row.get("Best Odds Fractional"),
                "best_bookmaker":       row.get("Best Bookmaker", ""),
                "odds_consensus":       row.get("Odds Consensus"),
                "bookmaker_count":      row.get("Bookmaker Count"),
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

        # Odds cell — show best decimal when present, falling back to SL current odds
        _best_dec = s.get("best_odds_decimal")
        if _best_dec:
            odds_cell = f'{s["curr_odds"]}<br><span style="color:#4caf50;font-size:11px;">best {_best_dec:.2f}x</span>'
        else:
            odds_cell = s["curr_odds"]

        rows += f"""<tr>
          <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;color:#888;">{s['time']}<br><span style="font-size:11px;">{s['course']}</span></td>
          <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;font-weight:bold;">{s['horse']}{hcap_tag}{mv_tag}{fav_tag}{oc_tag}</td>
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
            leg_rows += f"""
          <tr>
            <td style="padding:4px 8px;font-size:12px;color:#aaa;white-space:nowrap;">{h.get('time','')} {h.get('course','')}</td>
            <td style="padding:4px 8px;font-size:13px;font-weight:bold;">{h.get('horse','')}{yg_flag}</td>
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
    label_map = {
        "horse_form":   "Recent form",
        "trainer_form": "Trainer form",
        "jockey_form":  "Jockey form",
        "track_form":   "Track record",
        "going":        "Going",
        "race_pace":    "Pace angle",
        "market_moves": "Market move",
        "tf_stars":     "Timeform rating",
        "bsp_signal":   "Exchange signal",
        "market_odds":  "Market price",
        "adjustment":   "Adjustments",
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

            breakdown = _signal_breakdown_for(s)
            numeric = []
            for k, v in breakdown.items():
                if k not in label_map:
                    continue
                try:
                    fv = float(v)
                    numeric.append((k, fv))
                except (TypeError, ValueError):
                    continue
            numeric.sort(key=lambda kv: abs(kv[1]), reverse=True)
            top3 = numeric[:3]
            if top3:
                parts = []
                for k, v in top3:
                    if v > 0:
                        icon = "&#9989;"
                    elif v < 0:
                        icon = "&#10060;"
                    else:
                        icon = "&#9888;"
                    parts.append(f'{icon} {label_map.get(k, k)}')
                why_txt = " &nbsp;|&nbsp; ".join(parts)
                race_card += (
                    '<div style="font-size:11px;color:#555;margin-top:8px;">'
                    f'<span style="color:#888;text-transform:uppercase;letter-spacing:0.5px;">Why:</span> '
                    f'{why_txt}'
                    '</div>'
                )

            race_card += '</div>'
            html += race_card

    return html


def build_morning_brief(budget: float = 100.0) -> str:
    selections = _get_official_selections()
    movers     = _get_overnight_moves()
    going      = _get_going()
    staking    = _calc_staking(selections, budget)

    body = ""

    # 1. Going — first, sets the context for every selection
    body += _section(
        f"Today's Going — {len(going)} Meetings",
        _going_section_html(going),
        "#2a2a2a"
    )

    # 2. Overnight market moves — key intelligence before selections
    body += _section(
        f"Overnight Market Moves ({len([m for m in movers if m['direction']=='STEAM'])} shorteners, "
        f"{len([m for m in movers if m['direction']=='DRIFT'])} drifters)",
        _moves_section_html(movers),
        "#437A22" if any(m["direction"] == "STEAM" for m in movers) else "#2a2a2a"
    )

    # 3. Race-by-race intelligence layout
    body += _section(
        f"Today's Official Selections ({len(selections)})",
        _morning_html(selections),
        "#01696F"
    )

    # 4. Staking plan — v2.5.39 2-bet fold structure (Bet A / Bet B)
    if selections:
        try:
            from engine.staking import get_fold_bets as _get_fold_bets
            _folds = _get_fold_bets(selections)
        except Exception as _fb_err:
            print(f"[Brief] Fold-bets failed: {_fb_err}")
            _folds = {"bet_a": None, "bet_b": None}
        body += _section("Staking Plan — Bet A / Bet B", _fold_bets_block(_folds), "#437A22")

    # 4b. Best Accumulator Options — EV-ranked combinations (additive)
    if selections:
        try:
            from engine.staking import rank_accumulator_combinations as _rank_accas
            _best_combos = _rank_accas(selections, top_n=5)
        except Exception as _ba_err:
            print(f"[Brief] Best-acca ranking failed: {_ba_err}")
            _best_combos = []
        body += _section(
            "🎯 Best Accumulator Options",
            _best_acca_block(_best_combos),
            "#437A22"
        )

    # 5. Active filters — concise one-liner
    body += _section(
        "Active Filters",
        '<p style="font-size:12px;color:#888;margin:0;line-height:1.8;">'
        'Confidence: <strong style="color:#e0e0e0;">55%</strong> &nbsp;|&nbsp; '
        'Handicap: <strong style="color:#e0e0e0;">65%</strong> &nbsp;|&nbsp; '
        'Price cut-off: <strong style="color:#e0e0e0;">4/6</strong> &nbsp;|&nbsp; '
        'Large fields: <strong style="color:#e0e0e0;">&ge;16 excluded</strong> &nbsp;|&nbsp; '
        'Dual signal required'
        '</p>',
        "#2a2a2a"
    )

    if not selections:
        body += _section(
            "Status",
            '<p style="color:#964219;font-size:13px;margin:0;">No qualifying selections at this time — '
            'markets are live but no horses have cleared all filters yet. '
            'Check dashboard from 10:30 BST for developing selections.</p>',
            "#964219"
        )

    return _email_shell(
        title       = "Morning Brief — Today's Selections",
        label_color = "#01696F",
        label_text  = "Morning Brief",
        body_html   = body
    )


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
def build_evening_summary(results: list, selections: list, budget: float = 100.0) -> str:
    """
    Full day P&L once all races have run.
    results: list of dicts with horse/result/sp keys (matched against selections).
    selections: today's official selections list.
    """
    from itertools import combinations as _combs

    # Match results to selections
    sel_names = {s["horse"].lower(): s for s in selections}
    matched   = []
    for r in results:
        sel = sel_names.get(r.get("winner","").lower())
        # Also check by horse name in selections
        for s in selections:
            if s["horse"].lower() == r.get("winner","").lower() or \
               r.get("race","") in (s["time"] + " " + s["course"]):
                matched.append({**s, "result": "WON",  "sp": r.get("sp","")})
                break

    winners = [s for s in selections if any(
        r.get("winner","").lower() == s["horse"].lower() for r in results
    )]
    losers  = [s for s in selections if s not in winners]

    # P&L
    acc_stake = budget * 0.60 if len(selections) >= 4 else budget
    l15_stake = budget * 0.40 if len(selections) >= 4 else 0
    stake_per = l15_stake / 15 if l15_stake else 0

    all_won = len(losers) == 0
    acc_return = 0.0
    if all_won and winners:
        dec = 1.0
        for w in winners:
            dec *= w["decimal"]
        acc_return = round(acc_stake * dec, 2)

    # Cover accumulator and value double returns
    l15_return = 0.0
    if len(winners) >= 1 and l15_stake > 0:
        w_decs = [w["decimal"] for w in winners]
        for n in range(1, len(w_decs)+1):
            for combo in _combs(w_decs, n):
                prod = 1.0
                for d in combo: prod *= d
                l15_return += stake_per * prod
        l15_return = round(l15_return, 2)

    net = round((acc_return + l15_return) - budget, 2)

    # Results table
    def results_rows():
        rows = ""
        for s in selections:
            won = any(r.get("winner","").lower() == s["horse"].lower() for r in results)
            result_str = "WON" if won else "LOST"
            col = "#437A22" if won else "#A13544"
            sp_str = next((r.get("sp","") for r in results
                          if r.get("winner","").lower() == s["horse"].lower()), "—")
            rows += f"""<tr>
              <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;color:#888;">{s['time']} {s['course']}</td>
              <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;font-weight:bold;">{s['horse']}</td>
              <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;">{s['curr_odds']}</td>
              <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;">{sp_str}</td>
              <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;font-weight:bold;color:{col};">{result_str}</td>
            </tr>"""
        return rows

    results_table = f"""<table style="width:100%;border-collapse:collapse;">
      <thead><tr style="color:#555;font-size:11px;text-transform:uppercase;">
        <th style="padding:5px 6px;text-align:left;">Race</th>
        <th style="padding:5px 6px;text-align:left;">Horse</th>
        <th style="padding:5px 6px;text-align:left;">SP (eng)</th>
        <th style="padding:5px 6px;text-align:left;">SP (actual)</th>
        <th style="padding:5px 6px;text-align:left;">Result</th>
      </tr></thead>
      <tbody>{results_rows()}</tbody>
    </table>"""

    net_col = "#437A22" if net >= 0 else "#A13544"
    net_str = f"+£{net:.2f}" if net >= 0 else f"-£{abs(net):.2f}"

    pl_block = f"""
    <table style="width:100%;border-collapse:collapse;">
      <tr>
        <td style="padding:6px 0;color:#888;font-size:13px;">Budget</td>
        <td style="padding:6px 0;font-size:13px;">£{budget:.2f}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#888;font-size:13px;">Accumulator</td>
        <td style="padding:6px 0;font-size:13px;">
          {'WON — £'+str(acc_return) if all_won and acc_return else 'LOST — £'+str(acc_stake)+' stake'}
        </td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#888;font-size:13px;">Lucky 15</td>
        <td style="padding:6px 0;font-size:13px;">
          {'£'+str(l15_return)+' returned ('+str(len(winners))+' winners)' if l15_return else 'N/A'}
        </td>
      </tr>
      <tr style="border-top:1px solid #333;">
        <td style="padding:8px 0;font-size:14px;font-weight:bold;">Net P&amp;L</td>
        <td style="padding:8px 0;font-size:16px;font-weight:bold;color:{net_col};">{net_str}</td>
      </tr>
    </table>"""

    body  = _section(f"Results — {len(winners)}/{len(selections)} Winners", results_table, "#01696F")
    body += _section("P&L Summary", pl_block, net_col)

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
def send_email(subject: str, html_content: str, recipient: str = RECIPIENT) -> bool:
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print(f"[Email] No credentials — skipping: {subject}")
        return False
    try:
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

    html    = build_morning_brief(budget)
    subject = f"Racing Engine — Morning Brief | {_date_bst()}"
    ok      = send_email(subject, html)

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
    """Send pre-race alerts for every selection whose race time falls in
    [start_hhmm, end_hhmm] BST. Returns count of alerts sent."""
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

    sent = 0
    for s in selections:
        t_min = _parse_hhmm(str(s.get("time", "")))
        if t_min < 0:
            continue
        if lo <= t_min <= hi:
            try:
                if send_prerace_alert(s):
                    sent += 1
            except Exception as _e:
                print(f"[PreRace] alert failed for {s.get('horse','?')}: {_e}")
    print(f"[PreRace] {label}: sent {sent} alert(s) for window {start_hhmm}-{end_hhmm} BST")
    return sent


def send_afternoon_prerace_alerts():
    """Called by a 12:30 BST cron. Pre-race alerts for races 13:00-15:00 BST."""
    return _send_prerace_window("13:00", "15:00", label="afternoon")


def send_late_prerace_alerts():
    """Called by a 14:30 BST cron. Pre-race alerts for races 15:00-18:30 BST."""
    return _send_prerace_window("15:00", "18:30", label="late")


def send_evening_summary(budget: float = 100.0):
    """Called directly by the 21:00 BST cron. Fetches live results + runs ML settlement.

    Guards against stale data: if 0 results for today, send a fallback
    notice rather than any cached/yesterday content.
    """
    today_str  = datetime.now(_LONDON).date().isoformat()
    print(f"[Evening] Fetching results for {today_str}")
    selections = _get_official_selections()
    results    = _get_todays_results()
    subject    = f"Racing Engine — Evening Summary | {_date_bst()}"

    if not results:
        # Fallback: feed empty for today — don't send yesterday's/Tuesday's data.
        import smtplib
        from email.mime.text import MIMEText
        SENDER_EMAIL = "racingengine.sender@gmail.com"
        SENDER_PASS  = "aase pwst fcbf smfs"
        RECIPIENT    = "richardking123@outlook.com"
        msg = MIMEText(
            f"Racing Engine — Evening Summary\n"
            f"{_date_bst()} | {_now_bst()} BST\n\n"
            f"No results available for {today_str}.\n"
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

    html       = build_evening_summary(results, selections, budget)
    ok         = send_email(subject, html)

    # ── ML Learning loop ──────────────────────────────────────
    # 1. Settle today's races (match winners to recommendations)
    # 2. Adjust signal weights based on today's outcomes
    # 3. Log outcomes for loss analysis
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from learning.loop import LearningLoop
        from learning.loss_analyser import LossAnalyser

        loop = LearningLoop()

        # Step 1: Settle — pull results from feed, match to recs
        settled_count = loop.auto_settle()
        print(f"[ML] Settled {settled_count} races")

        # Step 2: Adjust weights — requires >=20 settled races
        new_weights = loop.adjust_weightings()
        print(f"[ML] Weights updated: {new_weights}")

        # Step 3: Loss analysis — diagnose losing selections
        try:
            from learning.loss_analyser import diagnose_loss
            recs = loop.recommendations.get("records", [])
            today_str = datetime.now(_LONDON).strftime("%Y-%m-%d")
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
        print(f"[ML] Learning loop error (non-fatal): {_ml_err}")

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
    try:
        recs_path     = os.path.join(os.path.dirname(__file__), "..", "learning", "recommendations.json")
        results_path  = os.path.join(os.path.dirname(__file__), "..", "learning", "results_store.json")
        recs_count    = len(json.load(open(recs_path))) if os.path.exists(recs_path) else 0
        results_count = len(json.load(open(results_path))) if os.path.exists(results_path) else 0
    except Exception:
        recs_count, results_count = 0, 0

    # ── Git status ───────────────────────────────────────────────
    try:
        import subprocess
        git_log = subprocess.check_output(
            ["git", "-C", os.path.join(os.path.dirname(__file__), ".."),
             "log", "--oneline", "-3"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        git_log = "unavailable"

    # ── Loose ends from learning/results store ──────────────────
    try:
        settled = json.load(open(os.path.join(os.path.dirname(__file__), "..", "learning", "settled_races.json")))
        pending_results = [r for r in settled if not r.get("outcome_recorded")]
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
  the confidence threshold AND the 4/6 price cut-off. No exceptions.
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
Budget: £100 | Singles: PERMANENTLY REMOVED | Lucky 15: PERMANENTLY REMOVED
Short price cut-off : 4/6 (1.67 decimal) — hard exclusion from ALL bets
Confidence threshold: 55% minimum (handicaps: 65%)
One horse per race  : highest confidence only

3-BET STRUCTURE (approved {date_str}):
  BET 1 — Main Accumulator (£60) — BANKERS ONLY
           Bankers = conf ≥ 63% AND price ≤ 4.0x
           VALUE horses (4x+) are NEVER in BET 1
  BET 2 — Cover Accumulator (£25) — all bankers MINUS riskiest leg
           Riskiest = highest-priced banker in BET 1
           Safety net: lands if BET 1's longest-priced horse fails
           Must be genuinely different from BET 1 — never a duplicate
  BET 3 — Value Double (£15) — top 2 VALUE horses only
           Value = price ≥ 4.0x AND conf ≥ 55%
           Independent of both accumulators

Target: £2,000+ profit, uncapped.

Additional exclusion rules:
  - Favourite gap: exclude if market fav is >35% shorter in price
  - Large fields: 16+ runners excluded entirely
  - Drifters: flagged in Tab 2, auto-drop rule under development

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCHEDULED CRONS (all times BST = UTC+1)
────────────────────────────────────────
[4eac6ab1] 10:00 daily — Morning racing brief → richardking123@outlook.com
[operator] 08:00 daily — THIS EMAIL → richardking123@outlook.com
[c58b4236] 15:30 daily — Show price snapshot (tomorrow's card baseline)
[de70bd36] Hourly 15:09–06:09 — Market movers (silent if nothing ≥15%)
[385f97ff] 21:00 daily — Evening results summary → richardking123@outlook.com

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KEY FILES (workspace: /home/user/workspace/racing-engine)
──────────────────────────────────────────────────────────
engine/staking.py          — 3-bet staking engine (v2.0)
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
Results fed back       : {results_count}
Pending outcome records: {pending_str}
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
1. DRIFT AUTO-DROP
   Horses drifting >20% from morning price should be auto-excluded.
   Currently flagged in Tab 2 only — not acted on.
   Build time: ~30 mins.

2. FAVOURITE CHECK THRESHOLD REVIEW
   35% gap exclusion is live. Review after 1 week of data.
   Scheduled review: Friday 24 April 2026.

3. SCENARIO TABLE — Double Return column
   Verify "Double Return" column displays correctly after 3-bet rebuild.

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
