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

    # Staking plan — two groups to ensure coverage if top EV horse (Mister Mojito) loses
    # Group A (60% budget — ★): MM-anchored, high upside
    # Group B (40% budget): Non-MM cover multiples, pay out independently
    staking_summary = [
        {"bet": "★ MM + Yorkshire Glory (double)",           "stake": "£4.00", "odds": "26.25x",  "return": "£105.00",  "group": "A"},
        {"bet": "★ MM + Beaune (double)",                   "stake": "£4.00", "odds": "20.63x",  "return": "£82.50",   "group": "A"},
        {"bet": "★ MM + Beaune + Yorkshire Glory (treble)", "stake": "£6.00", "odds": "72.19x",  "return": "£433.13",  "group": "A"},
        {"bet": "★ MM + Kaaranah + Yorkshire Glory",        "stake": "£6.00", "odds": "68.91x",  "return": "£413.44",  "group": "A"},
        {"bet": "★ MM + Beaune + Kaaranah + YG (4-fold)",  "stake": "£8.00", "odds": "189.49x", "return": "£1515.94", "group": "A"},
        {"bet": "COVER: Beaune + Yorkshire Glory (double)",  "stake": "£4.00", "odds": "12.38x",  "return": "£49.50",   "group": "B"},
        {"bet": "COVER: Kaaranah + Yorkshire Glory (double)","stake": "£4.00", "odds": "11.81x",  "return": "£47.25",   "group": "B"},
        {"bet": "COVER: Beaune + Kaaranah + YG (treble)",   "stake": "£14.00","odds": "32.39x",  "return": "£453.47",  "group": "B"},
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

    def staking_rows():
        rows = ""
        last_group = None
        for b in data.get("staking_summary", []):
            grp = b.get("group", "A")
            # Insert group header row when group changes
            if grp != last_group:
                if grp == "A":
                    rows += '<tr><td colspan="4" style="padding:8px 6px;color:#ffcc00;font-weight:bold;background:#1a1600;">&#9733; Group A — Anchor multiples (60% budget). Need Mister Mojito to win.</td></tr>'
                else:
                    rows += '<tr><td colspan="4" style="padding:8px 6px;color:#00c8ff;font-weight:bold;background:#001a22;">&#9632; Group B — Cover multiples (40% budget). Pay out even if Mister Mojito loses.</td></tr>'
                last_group = grp
            ret_val = b['return'].replace("£","")
            try:
                rc = "#00ff88" if float(ret_val) > 200 else "#66ff66" if float(ret_val) > 50 else "#aaffaa"
            except Exception:
                rc = "#aaffaa"
            bg = "#1a1600" if grp == "A" else "#001a22"
            rows += f"""<tr style="background:{bg}">
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;">{b['bet']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;font-weight:bold;color:#fff;">{b['stake']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;">{b['odds']}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #2a2a2a;color:{rc};font-weight:bold;">{b['return']}</td>
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
    <h2 style="color:#fff;margin-top:0;font-size:16px;">💰 Today's £50 Staking Plan — Multiples Only</h2>
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
      ★ Group A (yellow): MM-anchored multiples — high upside, need Mister Mojito to win.<br>
      &#9632; Group B (blue): Cover multiples — Beaune/Kaaranah/Yorkshire Glory only. Pay out even if MM loses.<br>
      Total staked: £50. If MM wins + 2 others: ~£500+. If MM loses: Group B treble still live (£453 return on £14 stake).<br>
      Crystal Island excluded — 4/6 price too short to add multiple value.
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

  <!-- Footer -->
  <div style="text-align:center;color:#444;font-size:12px;padding:16px 0;">
    Racing Engine v1.9 &nbsp;|&nbsp; Phase 1: Personal Research Tool<br>
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
