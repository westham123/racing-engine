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
from datetime import datetime, date

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
    Returns horses that have moved significantly since yesterday's show prices.
    Pulls from early_market snapshot — steamers and drifters above 15%.
    Returns empty list if no baseline exists.
    """
    try:
        from dashboard.early_market import get_market_movers, _today_bst
        target = today or _today_bst()
        movers = get_market_movers(target, min_move_pct=0.15, vs="show")
        if not movers or (len(movers) == 1 and "error" in movers[0]):
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
                "is_handicap": bool(row.get("Is Handicap", False)),
                "tier":        ("BANKER" if dec <= 2.50 else
                                "MID"    if dec <= 5.00 else
                                "VALUE"  if dec <= 10.0 else "LONGSHOT"),
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
        # Even if the dataframe is cached, this strips NRs unconditionally.
        try:
            from dashboard.live_data import get_non_runners as _gnr
            _nr_names = {nr['Horse'].lower().strip() for nr in _gnr()}
            _before = len(out)
            out = [s for s in out if s['horse'].lower().strip() not in _nr_names]
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
    """Returns settled races from today's results feed."""
    try:
        from dashboard.live_data import get_todays_results
        df = get_todays_results()
        if df is None or len(df) == 0:
            return []
        out = []
        for _, row in df.iterrows():
            out.append({
                "race":    str(row.get("Race", "")),
                "winner":  str(row.get("Winner", "")),
                "sp":      str(row.get("Odds", "")),
            })
        return out
    except Exception:
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
        # Expose flat fields expected by legacy callers + staking block
        combined_dec = plan["main_dec"]
        return {
            "budget":         budget,
            "acc_stake":      plan["main_stake"],
            "acc_return":     plan["main_return"],
            "acc_legs":       len(plan["main_pool"]),
            "combined_dec":   combined_dec,
            "l15_available":  False,   # Lucky 15 permanently removed
            "l15_stake":      0,
            "l15_per_bet":    0,
            "l15_horses":     0,
            # Extended adaptive fields
            "plan_type":      plan["plan_type"],
            "plan_label":     plan["plan_label"],
            "plan_rationale": plan["plan_rationale"],
            "covers":         plan["covers"],
            "cover_total":    plan["cover_total"],
            "speculative":    plan["speculative"],
            "scenarios":      plan["scenarios"],
            "main_pool":      plan["main_pool"],
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

        rows += f"""<tr>
          <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;color:#888;">{s['time']}<br><span style="font-size:11px;">{s['course']}</span></td>
          <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;font-weight:bold;">{s['horse']}{hcap_tag}{mv_tag}</td>
          <td style="padding:7px 6px;border-bottom:1px solid #2a2a2a;font-size:13px;">{s['curr_odds']}</td>
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


def _staking_block(staking: dict) -> str:
    """Adaptive staking plan HTML block for morning brief email."""
    if not staking:
        return '<p style="color:#888;font-size:13px;margin:0;">No staking data.</p>'

    plan_type  = staking.get("plan_type", "FULL_ACC")
    plan_label = staking.get("plan_label", "Full Accumulator")
    rationale  = staking.get("plan_rationale", "")
    covers     = staking.get("covers", [])
    speculative = staking.get("speculative", [])

    # Plan banner colour
    if plan_type == "FULL_ACC":
        banner_col = "#437A22"   # green — be brave
        banner_txt = f"PLAN: {plan_label} — all selections are short-priced bankers. Full stake on the accumulator."
    elif plan_type == "COVER_70":
        banner_col = "#964219"   # amber — cover needed
        banner_txt = f"PLAN: {plan_label} — longer-priced selection detected. 70% accumulator + 30% cover."
    else:
        banner_col = "#A13544"   # red — high risk
        banner_txt = f"PLAN: {plan_label} — high-value selection detected. 50/50 split: accumulator + full cover."

    # Cover rows
    cover_rows = ""
    if covers:
        cover_rows = f"""
      <tr>
        <td colspan="2" style="padding:8px 0 2px;color:#888;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;">
          Cover Accumulators — £{staking['cover_total']:.2f} total
        </td>
      </tr>"""
        for c in covers:
            cover_rows += f"""
      <tr>
        <td style="padding:3px 0;color:#888;font-size:12px;">{c['fold']}-fold (omit {c['omit']} @ {c['omit_odds']:.2f}x)</td>
        <td style="padding:3px 0;font-size:12px;font-weight:bold;">£{c['stake']:.2f} → £{c['projected_return']:.2f} if wins</td>
      </tr>"""

    # Speculative flags
    spec_rows = ""
    if speculative:
        spec_rows = f"""
      <tr>
        <td colspan="2" style="padding:8px 0 2px;color:#888;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;">
          Flagged Side Bets (excluded from main acc)
        </td>
      </tr>"""
        for s in speculative:
            spec_rows += f"""
      <tr>
        <td style="padding:3px 0;color:#964219;font-size:12px;">{s['horse']} @ {s['decimal']:.2f}x</td>
        <td style="padding:3px 0;font-size:12px;color:#888;">Consider small separate stake</td>
      </tr>"""

    return f"""
    <div style="background:{banner_col};border-radius:4px;padding:8px 12px;margin-bottom:8px;">
      <span style="color:#fff;font-size:13px;font-weight:bold;">{banner_txt}</span>
    </div>
    <table style="width:100%;border-collapse:collapse;">
      <tr>
        <td style="padding:6px 0;color:#888;font-size:13px;">Budget</td>
        <td style="padding:6px 0;font-size:13px;font-weight:bold;">£{staking['budget']:.2f}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#888;font-size:13px;">{staking['acc_legs']}-fold Accumulator</td>
        <td style="padding:6px 0;font-size:13px;font-weight:bold;">
          £{staking['acc_stake']:.2f}
          <span style="color:#888;font-weight:normal;">→ returns £{staking['acc_return']:,.2f} if all win</span>
        </td>
      </tr>{cover_rows}{spec_rows}
      <tr>
        <td colspan="2" style="padding:6px 0 0;color:#888;font-size:11px;font-style:italic;">{rationale}</td>
      </tr>
    </table>"""


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

    # 3. Official selections with overnight move tags inline
    body += _section(
        f"Today's Official Selections ({len(selections)})",
        _sel_table(selections, movers),
        "#01696F"
    )

    # 4. Staking plan
    if selections:
        body += _section("Staking Plan", _staking_block(staking), "#437A22")

    # 5. Active filters — concise one-liner
    body += _section(
        "Active Filters",
        '<p style="font-size:12px;color:#888;margin:0;line-height:1.8;">'
        'Confidence: <strong style="color:#e0e0e0;">55%</strong> &nbsp;|&nbsp; '
        'Handicap: <strong style="color:#e0e0e0;">65%</strong> &nbsp;|&nbsp; '
        'Price cut-off: <strong style="color:#e0e0e0;">4/6</strong> &nbsp;|&nbsp; '
        'Large fields: <strong style="color:#e0e0e0;">&ge;12 excluded</strong> &nbsp;|&nbsp; '
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
        acc_note = '<p style="font-size:13px;color:#A13544;margin:8px 0 0;">Accumulator leg lost. Lucky 15 still paying on other winners.</p>'

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

    # Lucky 15 return (4 or more winners)
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
    """Called directly by the 08:00 BST cron."""
    html    = build_morning_brief(budget)
    subject = f"Racing Engine — Morning Brief | {_date_bst()}"
    return send_email(subject, html)


def send_evening_summary(budget: float = 100.0):
    """Called directly by the 19:00 BST cron. Fetches live results internally."""
    selections = _get_official_selections()
    results    = _get_todays_results()
    html       = build_evening_summary(results, selections, budget)
    subject    = f"Racing Engine — Evening Summary | {_date_bst()}"
    return send_email(subject, html)


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
