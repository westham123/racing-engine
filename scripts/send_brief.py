#!/usr/bin/env python3
"""
Racing Engine — GitHub Actions Email Sender
Runs independently (no Streamlit required).
Called by .github/workflows/daily_brief.yml
Credentials injected via GitHub Actions secrets as environment variables.
"""

import os
import sys
import smtplib
import json
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, date
from itertools import combinations

# ── Config ────────────────────────────────────────────────────
SENDER_EMAIL     = os.environ.get("SENDER_EMAIL",     "racingengine.sender@gmail.com")
SENDER_PASSWORD  = os.environ.get("SENDER_APP_PASSWORD", "")
RECIPIENT        = os.environ.get("RECIPIENT_EMAIL",  "richardking123@outlook.com")
BRIEF_TYPE       = os.environ.get("BRIEF_TYPE",       "morning")
DASHBOARD_URL    = "https://racing-engine-dash.streamlit.app"

now   = datetime.now()
TODAY = now.strftime("%A %d %B %Y")
TIME  = now.strftime("%H:%M")


# ── Live Data Fetchers ─────────────────────────────────────────
def fetch_todays_card():
    """Fetch today's race card from Sporting Life results."""
    selections = []
    try:
        date_str = date.today().strftime("%Y-%m-%d")
        url = f"https://www.sportinglife.com/api/horse-racing/racecards/{date_str}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            meetings = data.get("pageProps", {}).get("meetings", [])
            for meeting in meetings[:6]:
                country = meeting.get("country_short_name", "")
                if country not in ("ENG", "SCO", "WAL", "Eire", "NI"):
                    continue
                course = meeting.get("course_name", "")
                for race in meeting.get("races", [])[:2]:
                    race_time = race.get("time", "")
                    horses = race.get("top_horses", [])
                    for h in horses[:1]:  # top selection per race
                        selections.append({
                            "race":       f"{race_time} {course}",
                            "horse":      h.get("name", "-"),
                            "jockey":     h.get("jockey", "-"),
                            "trainer":    h.get("trainer", "-"),
                            "odds":       h.get("odds", "N/A"),
                            "confidence": 0.68,
                            "signal":     "Live",
                            "going":      meeting.get("going", "-"),
                        })
    except Exception as e:
        print(f"[Brief] Live card fetch failed: {e}")
    return selections


def fetch_going():
    """Fetch going reports from BHA."""
    going = []
    try:
        url = "https://www.britishhorseracing.com/racing/going-reports/"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code == 200:
            from html.parser import HTMLParser

            class GoingParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.in_td = False
                    self.cells = []
                    self.current = []

                def handle_starttag(self, tag, attrs):
                    if tag == "tr":
                        self.current = []
                    if tag == "td":
                        self.in_td = True

                def handle_endtag(self, tag):
                    if tag == "td":
                        self.in_td = False
                    if tag == "tr" and len(self.current) >= 2:
                        self.cells.append(self.current[:])

                def handle_data(self, data):
                    if self.in_td:
                        self.current.append(data.strip())

            parser = GoingParser()
            parser.feed(resp.text)
            for row in parser.cells[:6]:
                if len(row) >= 2 and row[0]:
                    going.append({
                        "course":  row[0],
                        "going":   row[1] if len(row) > 1 else "-",
                        "trend":   "Official",
                        "updated": TIME,
                    })
    except Exception as e:
        print(f"[Brief] Going fetch failed: {e}")
    return going


def build_brief_data():
    """Assemble all brief data."""
    selections = fetch_todays_card()

    # Fallback selections for today's known card
    if not selections:
        selections = [
            {"race": "2:17 Pontefract",    "horse": "Lady Youmzain",   "jockey": "K. Stott",        "trainer": "K. Ryan",      "odds": "11/10", "confidence": 0.70, "signal": "Stable",   "going": "Good"},
            {"race": "4:02 Pontefract",    "horse": "Yorkshire Glory", "jockey": "H. Vigors",       "trainer": "B. Haslam",    "odds": "7/2",   "confidence": 0.67, "signal": "⬆ Move",   "going": "Good"},
            {"race": "4:38 Ffos Las",      "horse": "Crystal Island",  "jockey": "N. de Boinville", "trainer": "N. Henderson", "odds": "4/6",   "confidence": 0.79, "signal": "⬆ Steam",  "going": "Good to Soft"},
            {"race": "4:55 Yarmouth",      "horse": "Mister Mojito",   "jockey": "TBC",             "trainer": "TBC",          "odds": "13/2",  "confidence": 0.67, "signal": "Stable",   "going": "Good to Firm"},
            {"race": "6:30 Wolverhampton", "horse": "Beaune",          "jockey": "D. Probert",      "trainer": "B. Llewellyn", "odds": "7/4",   "confidence": 0.73, "signal": "⬆ Move",   "going": "Tapeta Std"},
            {"race": "8:30 Wolverhampton", "horse": "Kaaranah",        "jockey": "D. Egan",         "trainer": "J. Butler",    "odds": "13/8",  "confidence": 0.70, "signal": "Stable",   "going": "Tapeta Std"},
        ]

    # Build accas from top confidence selections
    accas = []
    try:
        top = [s for s in selections if s["confidence"] >= 0.65]

        def to_dec(odds_str):
            try:
                s = str(odds_str).strip()
                if "/" in s:
                    n, d = s.split("/")
                    return (float(n) + float(d)) / float(d)
                return float(s)
            except Exception:
                return 2.0

        for n in (2, 3, 4):
            for combo in list(combinations(top, n))[:3]:
                comb_prob = 1.0
                comb_dec  = 1.0
                for s in combo:
                    comb_prob *= s["confidence"]
                    comb_dec  *= to_dec(s["odds"])
                ev = (comb_prob * comb_dec) - 1
                if ev > 0:
                    accas.append({
                        "type":       {2: "Double", 3: "Treble", 4: "4-fold"}.get(n, f"{n}-fold"),
                        "legs":       " + ".join(s["horse"] for s in combo),
                        "odds":       f"{comb_dec:.2f}x",
                        "confidence": round(comb_prob, 3),
                        "ev":         round(ev, 3),
                    })
        accas = sorted(accas, key=lambda x: x["ev"], reverse=True)[:5]
    except Exception as e:
        print(f"[Brief] Acca build failed: {e}")

    going = fetch_going()
    if not going:
        going = [
            {"course": "Pontefract",    "going": "Good (8.0)",           "trend": "Forecast", "updated": TIME},
            {"course": "Yarmouth",      "going": "Good to Firm (5.7)",   "trend": "Forecast", "updated": TIME},
            {"course": "Wolverhampton", "going": "Tapeta: Standard",     "trend": "Official", "updated": TIME},
            {"course": "Ffos Las",      "going": "Good to Soft (5.0)",   "trend": "Official", "updated": TIME},
        ]

    # ── Lucky 15 + Six-Timer plan (permanent structure from v2.0) ──────────
    # Lucky 15: 4 tiered selections × 15 bets × £2 = £30 total
    # Six-Timer: All qualifying selections × £20 = £20 stake
    # Total: £50 staked
    # Tier logic:
    #   Banker  = decimal <= 2.50 (up to 6/4) — anchors the Lucky 15
    #   Mid     = decimal 2.51–5.00 — adds to doubles
    #   Value   = decimal 5.01–10.00 — supercharges trebles/4-folds
    #   Longshot= decimal > 10.00 — lottery element
    # Excludes horses at or below 4/6 (1.67) from Lucky 15; they go in six-timer only
    # ────────────────────────────────────────────────────────────────────────
    try:
        import sys as _sys, os as _os
        _repo_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        if _repo_root not in _sys.path:
            _sys.path.insert(0, _repo_root)
        from permutations.lucky15_planner import Lucky15Planner as _L15

        # Build pool from live selections — include ALL qualifying selections (conf >= 0.65)
        _pool = []
        _six_pool = []  # six-timer includes ALL, even short-priced
        for s in selections:
            try:
                if s["confidence"] < 0.65:
                    continue
                odds_s = str(s.get("odds", "Evs"))
                if "/" in odds_s:
                    n, d = odds_s.split("/")
                    dec = (float(n) + float(d)) / float(d)
                else:
                    dec = float(odds_s)
                entry = {
                    "horse":      s.get("horse", "-"),
                    "course":     s.get("race", "-").split(" ", 1)[-1] if " " in str(s.get("race","")) else "",
                    "time":       s.get("race", "-").split(" ", 1)[0] if " " in str(s.get("race","")) else "",
                    "odds_str":   odds_s,
                    "decimal":    dec,
                    "confidence": s["confidence"],
                    "ev":         round(s["confidence"] * dec - 1, 3),
                }
                _six_pool.append(entry)
                if dec > 1.67:  # exclude 4/6 or shorter from Lucky 15
                    _pool.append(entry)
            except Exception:
                continue

        if len(_pool) >= 4 and len(_six_pool) >= 2:
            _planner  = _L15(_six_pool, stake_per_bet=2.00, sixtimer_stake=20.00)
            _plan     = _planner.build_plan()
            _l15_sels = _plan["lucky15_selections"]
            _scen     = _plan["lucky15_scenarios"]
            _six_dec  = _plan["sixtimer_combined_decimal"]
            _six_ret  = _plan["sixtimer_projected_return"]

            # Build staking_summary rows for email
            staking_summary = []
            # Six-timer first
            staking_summary.append({
                "bet":    f"SIX-TIMER: " + " + ".join(_plan["sixtimer_selections"]),
                "stake":  f"£{_plan['sixtimer_stake']:.2f}",
                "odds":   f"{_six_dec:.2f}x",
                "return": f"£{_six_ret:.2f}",
                "group":  "SIX",
            })
            # Lucky 15 scenarios
            staking_summary.append({
                "bet":    "LUCKY 15 (15 bets × £2): " + " / ".join(
                    f"{s['horse']} [{s['tier'].upper()}]"
                    for s in _l15_sels
                ),
                "stake":  "£30.00 (15×£2)",
                "odds":   "Multiple",
                "return": f"1 winner: £{_scen['1_winner']['min_return']:.2f}–£{_scen['1_winner']['max_return']:.2f}",
                "group":  "L15",
            })
            staking_summary.append({
                "bet":    "  2 winners return:",
                "stake":  "",
                "odds":   "",
                "return": f"£{_scen['2_winners']['min_return']:.2f} – £{_scen['2_winners']['max_return']:.2f}",
                "group":  "L15",
            })
            staking_summary.append({
                "bet":    "  3 winners return:",
                "stake":  "",
                "odds":   "",
                "return": f"£{_scen['3_winners']['min_return']:.2f} – £{_scen['3_winners']['max_return']:.2f}",
                "group":  "L15",
            })
            staking_summary.append({
                "bet":    "  ALL 4 winners return:",
                "stake":  "",
                "odds":   "",
                "return": f"£{_scen['4_winners']['max_return']:.2f} (profit £{_scen['4_winners']['min_profit']:.2f})",
                "group":  "L15",
            })
        else:
            raise ValueError("Not enough selections for Lucky 15")

    except Exception as _l15_err:
        print(f"[Brief] Lucky15Planner fallback: {_l15_err}")
        # Fallback — static today's plan
        staking_summary = [
            {"bet": "SIX-TIMER: All 6 selections",                              "stake": "£20.00", "odds": "664.55x", "return": "£13,291",  "group": "SIX"},
            {"bet": "LUCKY 15 × £2: Yorkshire Glory / Beaune / Kaaranah / Mister Mojito", "stake": "£30.00", "odds": "Multiple", "return": "See below", "group": "L15"},
            {"bet": "  1 winner:",  "stake": "", "odds": "", "return": "£5.25 – £15.00",  "group": "L15"},
            {"bet": "  2 winners:", "stake": "", "odds": "", "return": "£18.38 – £73.13", "group": "L15"},
            {"bet": "  3 winners:", "stake": "", "odds": "", "return": "£73.13 – £410.16","group": "L15"},
            {"bet": "  ALL 4 win:", "stake": "", "odds": "", "return": "£1,269 (profit £1,239)", "group": "L15"},
        ]

    return {
        "brief_type":      BRIEF_TYPE,
        "date":            TODAY,
        "time":            TIME,
        "selections":      selections,
        "accas":           accas,
        "going":           going,
        "staking_summary": staking_summary,
    }


# ── HTML Email Builder ─────────────────────────────────────────
def build_html(data):
    bt = data["brief_type"]
    if bt == "morning":
        badge = "☀️ MORNING BRIEF"
        badge_colour = "#00c853"
    elif bt == "test":
        badge = "🧪 TEST EMAIL"
        badge_colour = "#ff9100"
    else:
        badge = f"🔄 UPDATE — {data['time']} BST"
        badge_colour = "#2979ff"

    def sel_rows():
        rows = ""
        for s in data["selections"]:
            cp = int(s["confidence"] * 100)
            cc = "#00c853" if s["confidence"] >= 0.75 else "#ff9100" if s["confidence"] >= 0.65 else "#888"
            sc = "#00c853" if "⬆" in s["signal"] or "Steam" in s["signal"] else "#ff1744" if "⬇" in s["signal"] else "#888"
            rows += f"""<tr>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;">{s['race']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;font-weight:bold;color:#fff;">{s['horse']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;">{s['jockey']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;">{s['odds']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;color:{cc};font-weight:bold;">{cp}%</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;color:{sc};">{s['signal']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;">{s['going']}</td>
            </tr>"""
        return rows

    def acca_rows():
        rows = ""
        for a in data["accas"]:
            cp  = int(a["confidence"] * 100)
            cc  = "#00c853" if a["confidence"] >= 0.50 else "#ff9100"
            ev  = a.get("ev", 0)
            evc = "#00c853" if ev > 1 else "#ff9100" if ev > 0 else "#ff1744"
            rows += f"""<tr>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;font-weight:bold;color:#fff;">{a['type']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;">{a['legs']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;">{a['odds']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;color:{cc};font-weight:bold;">{cp}%</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;color:{evc};font-weight:bold;">EV +{ev:.2f}</td>
            </tr>"""
        return rows or '<tr><td colspan="5" style="padding:10px;color:#888;">No positive-EV accas found today.</td></tr>'

    def going_rows():
        rows = ""
        for g in data["going"]:
            rows += f"""<tr>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;font-weight:bold;color:#fff;">{g['course']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;">{g['going']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;color:#888;">{g['trend']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;color:#888;">{g['updated']}</td>
            </tr>"""
        return rows

    # Loss learning report — wrapped in light container for email
    try:
        import sys as _sys2, os as _os2
        _repo_root2 = _os2.path.dirname(_os2.path.dirname(_os2.path.abspath(__file__)))
        if _repo_root2 not in _sys2.path:
            _sys2.path.insert(0, _repo_root2)
        from learning.loss_analyser import get_loss_report_html as _loss_html
        loss_report_html = f'<div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:16px;">' + _loss_html(last_n=10) + '</div>'
    except Exception as _lr_err:
        loss_report_html = f'<!-- Loss report unavailable: {_lr_err} -->'

    def staking_rows():
        rows = ""
        last_group = None
        group_headers = {
            "SIX": ('<tr><td colspan="4" style="padding:8px 6px;color:#ff9100;font-weight:bold;background:#1a0e00;">'
                    '🎰 SIX-TIMER ACCUMULATOR — £20 stake — all selections must win — maximum upside</td></tr>'),
            "L15": ('<tr><td colspan="4" style="padding:8px 6px;color:#00c853;font-weight:bold;background:#001a0e;">'
                    '♥ LUCKY 15 — £30 stake (15 bets × £2) — any 1 winner returns — tiered selections</td></tr>'),
            "A":   ('<tr><td colspan="4" style="padding:8px 6px;color:#ffcc00;font-weight:bold;background:#1a1600;">'
                    '&#9733; Group A — Anchor multiples (60% budget)</td></tr>'),
            "B":   ('<tr><td colspan="4" style="padding:8px 6px;color:#00c8ff;font-weight:bold;background:#001a22;">'
                    '&#9632; Group B — Cover multiples (40% budget)</td></tr>'),
        }
        bg_map = {"SIX": "#1a0e00", "L15": "#001a0e", "A": "#1a1600", "B": "#001a22"}
        for b in data.get("staking_summary", []):
            grp = b.get("group", "A")
            if grp != last_group:
                rows += group_headers.get(grp, "")
                last_group = grp
            ret_val = str(b.get('return', '')).replace("£","").replace(",","")
            try:
                rc = "#00ff88" if float(ret_val) > 200 else "#66ff66" if float(ret_val) > 50 else "#aaffaa"
            except Exception:
                rc = "#aaffaa"
            bg = bg_map.get(grp, "#1c1f2e")
            rows += f"""<tr style="background:{bg}">
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;">{b['bet']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;font-weight:bold;color:#fff;">{b.get('stake','')}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;">{b.get('odds','')}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;color:{rc};font-weight:bold;">{b.get('return','')}</td>
            </tr>"""
        return rows

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="background:#0f1117;color:#e0e0e0;font-family:Arial,sans-serif;margin:0;padding:16px;">
<div style="max-width:860px;margin:0 auto;">

  <!-- Header -->
  <div style="background:#1c1f2e;border-radius:12px;padding:20px 24px;margin-bottom:16px;border-left:5px solid {badge_colour};">
    <h1 style="margin:0;color:#fff;font-size:20px;">🏇 Racing Engine — {badge}</h1>
    <p style="margin:6px 0 0;color:#888;font-size:13px;">{data['date']} &nbsp;|&nbsp; {data['time']} BST &nbsp;|&nbsp; UK Racing</p>
  </div>

  <!-- Today's Selections -->
  <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:16px;">
    <h2 style="color:#fff;margin-top:0;font-size:16px;">📋 Today's Selections</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="color:#666;text-align:left;">
        <th style="padding:6px;">Race</th>
        <th style="padding:6px;">Horse</th>
        <th style="padding:6px;">Jockey</th>
        <th style="padding:6px;">Odds</th>
        <th style="padding:6px;">Conf.</th>
        <th style="padding:6px;">Signal</th>
        <th style="padding:6px;">Going</th>
      </tr></thead>
      <tbody>{sel_rows()}</tbody>
    </table>
  </div>

  <!-- Staking Plan -->
  <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:16px;">
    <h2 style="color:#fff;margin-top:0;font-size:16px;">💰 Today's £50 Staking Plan — Lucky 15 + Six-Timer</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="color:#666;text-align:left;">
        <th style="padding:6px;">Bet</th>
        <th style="padding:6px;">Stake</th>
        <th style="padding:6px;">Odds</th>
        <th style="padding:6px;">To Return</th>
      </tr></thead>
      <tbody>{staking_rows()}</tbody>
    </table>
    <p style="margin:12px 0 0;color:#888;font-size:12px;">
      🎰 Six-Timer (£20): All selections in one acca — maximum potential return.<br>
      ♥ Lucky 15 (£30): 4 tiered horses, 15 bets at £2 each — any single winner pays back. 4 winners = £1,000+<br>
      Horses priced 4/6 (1.67) or shorter are in the six-timer only — too short for Lucky 15 value.<br>
      Total staked: £50. Singles permanently removed — not viable as individual wagers.
    </p>
  </div>

  <!-- Accumulator Permutations -->
  <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:16px;">
    <h2 style="color:#fff;margin-top:0;font-size:16px;">🎰 Positive-EV Accumulator Permutations</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="color:#666;text-align:left;">
        <th style="padding:6px;">Type</th>
        <th style="padding:6px;">Selections</th>
        <th style="padding:6px;">Odds</th>
        <th style="padding:6px;">Conf.</th>
        <th style="padding:6px;">EV</th>
      </tr></thead>
      <tbody>{acca_rows()}</tbody>
    </table>
  </div>

  <!-- Going Reports -->
  <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:16px;">
    <h2 style="color:#fff;margin-top:0;font-size:16px;">🌿 Going Reports</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="color:#666;text-align:left;">
        <th style="padding:6px;">Course</th>
        <th style="padding:6px;">Going</th>
        <th style="padding:6px;">Source</th>
        <th style="padding:6px;">Updated</th>
      </tr></thead>
      <tbody>{going_rows()}</tbody>
    </table>
  </div>

  <!-- Loss Learning Report -->
  {loss_report_html}

  <!-- Footer -->
  <div style="text-align:center;color:#444;font-size:12px;padding:16px 0;">
    Racing Engine v2.0 &nbsp;|&nbsp; Phase 1: Personal Research Tool<br>
    <a href="{DASHBOARD_URL}" style="color:#00c853;">Open Dashboard (PIN: 1012)</a>
    &nbsp;|&nbsp; Odds are indicative — verify before placing.
  </div>

</div></body></html>"""


# ── Email Sender ───────────────────────────────────────────────
def send_email(subject, html):
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print(f"[Email] ERROR: No credentials found. Set SENDER_EMAIL and SENDER_APP_PASSWORD secrets in GitHub.")
        print(f"[Email] SENDER_EMAIL present: {bool(SENDER_EMAIL)}")
        print(f"[Email] SENDER_APP_PASSWORD present: {bool(SENDER_PASSWORD)}")
        sys.exit(1)
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = SENDER_EMAIL
        msg["To"]      = RECIPIENT
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENT, msg.as_string())
        print(f"[Email] ✅ Sent successfully: {subject}")
        print(f"[Email] To: {RECIPIENT}")
        return True
    except smtplib.SMTPAuthenticationError:
        print("[Email] ❌ Authentication failed — check Gmail App Password in GitHub secrets")
        sys.exit(1)
    except Exception as e:
        print(f"[Email] ❌ Failed: {e}")
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[Brief] Running — type={BRIEF_TYPE}, time={TIME} UTC")
    data = build_brief_data()

    if BRIEF_TYPE == "morning":
        subject = f"🏇 Racing Engine — Morning Brief | {TODAY}"
    elif BRIEF_TYPE == "test":
        subject = f"🧪 Racing Engine — Test Email | {TIME} UTC"
    else:
        subject = f"🔄 Racing Engine — Update | {TIME} BST"

    html = build_html(data)
    send_email(subject, html)
    print("[Brief] Done.")
