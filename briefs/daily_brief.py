# Racing Engine — Daily Brief Generator + Email System
# Version: 1.1
# Date: 20 April 2026
# Delivers: 8am morning brief + 2-hourly updates + instant alerts
# Recipient: richardking123@outlook.com

import smtplib
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, date

# ── Email Config ──────────────────────────────────────────────
RECIPIENT       = "richardking123@outlook.com"
SENDER_EMAIL    = os.getenv("SENDER_EMAIL", "")
SENDER_PASSWORD = os.getenv("SENDER_APP_PASSWORD", "")   # Gmail App Password


# ── Live Data Builder ─────────────────────────────────────────
def build_brief_data(update_number: int = 0) -> dict:
    """
    Pulls live data from all feeds and assembles the brief dict.
    Falls back to minimal sample data if live feeds are unavailable.
    update_number 0 = morning brief, 1+ = rolling updates
    """
    now = datetime.now()

    # ── Live selections ───────────────────────────────────────
    selections = []
    try:
        from dashboard.live_data import get_todays_selections
        df = get_todays_selections()
        if df is not None and len(df) > 0:
            top = df.head(8)
            for _, row in top.iterrows():
                selections.append({
                    "race":       str(row.get("Race", "-")),
                    "horse":      str(row.get("Horse", "-")),
                    "jockey":     str(row.get("Jockey", "-")),
                    "trainer":    str(row.get("Trainer", "-")),
                    "odds":       str(row.get("Odds", "N/A")),
                    "confidence": float(row.get("Confidence", 0.5)),
                    "signal":     str(row.get("Signal", "Stable")),
                    "going":      str(row.get("Going", "-")),
                })
    except Exception as e:
        print(f"[Brief] Live selections unavailable: {e}")
    if not selections:
        selections = _sample_selections()

    # ── Live accas (top 4 from live selections) ───────────────
    accas = []
    try:
        from itertools import combinations
        import numpy as np
        live_sels = [s for s in selections if s["confidence"] >= 0.65]
        for n in (2, 3):
            for combo in list(combinations(live_sels, n))[:3]:
                combined_prob   = float(np.prod([s["confidence"] for s in combo]))
                combined_dec    = float(np.prod([_to_decimal(s["odds"]) for s in combo]))
                accas.append({
                    "type":       {2: "Double", 3: "Treble"}.get(n, f"{n}-fold"),
                    "legs":       " + ".join(s["horse"] for s in combo),
                    "odds":       f"{combined_dec - 1:.1f}/1",
                    "confidence": round(combined_prob, 3),
                })
        accas = sorted(accas, key=lambda x: x["confidence"], reverse=True)[:4]
    except Exception as e:
        print(f"[Brief] Acca build failed: {e}")
    if not accas:
        accas = _sample_accas()

    # ── Live going ────────────────────────────────────────────
    going = []
    try:
        from dashboard.live_data import get_going_reports
        going_df = get_going_reports()
        if going_df is not None and len(going_df) > 0:
            for _, row in going_df.iterrows():
                going.append({
                    "course":  str(row.get("Course", "-")),
                    "going":   str(row.get("Going", "-")),
                    "trend":   "Live",
                    "updated": str(row.get("Updated", now.strftime("%H:%M"))),
                })
    except Exception as e:
        print(f"[Brief] Going reports unavailable: {e}")
    if not going:
        going = _sample_going()

    # ── Non-runners ───────────────────────────────────────────
    non_runners = []
    try:
        from dashboard.live_data import get_non_runners
        nrs = get_non_runners()
        for nr in nrs:
            non_runners.append({
                "horse":  nr.get("Horse", "-"),
                "race":   nr.get("Race", "-"),
                "reason": nr.get("Reason", "Declared NR"),
            })
    except Exception as e:
        print(f"[Brief] Non-runners unavailable: {e}")

    # ── Alerts from monitor ───────────────────────────────────
    alerts = []
    try:
        from alerts.monitor import AlertMonitor
        monitor = AlertMonitor()
        live_alerts = monitor.run_poll()
        for a in live_alerts[:5]:
            alerts.append({
                "level":   a.get("level", "MEDIUM"),
                "message": a.get("message", ""),
            })
    except Exception as e:
        print(f"[Brief] Alert monitor unavailable: {e}")

    # ── Learning stats ────────────────────────────────────────
    perf_note = ""
    try:
        from learning.loop import LearningLoop
        loop = LearningLoop()
        stats = loop.get_performance_stats()
        if stats["settled_races"] > 0:
            perf_note = (f"Hit Rate: {stats['hit_rate_pct']}% from "
                         f"{stats['settled_races']} settled races")
        else:
            perf_note = "Learning — data accumulating (stats appear after first settled races)"
    except Exception:
        pass

    return {
        "selections":    selections,
        "accas":         accas,
        "going":         going,
        "non_runners":   non_runners,
        "alerts":        alerts,
        "perf_note":     perf_note,
        "generated_at":  now.strftime("%H:%M"),
        "date":          now.strftime("%A %d %B %Y"),
        "update_number": update_number,
    }


# ── HTML Email Builder ────────────────────────────────────────
def build_html_email(data: dict, is_alert: bool = False, alert_message: str = "") -> str:
    update_type = "🚨 INSTANT ALERT" if is_alert else (
        "☀️ MORNING BRIEF" if data["update_number"] == 0 else
        f"🔄 UPDATE #{data['update_number']}"
    )

    def selection_rows():
        rows = ""
        for s in data["selections"]:
            conf_pct    = int(s["confidence"] * 100)
            conf_colour = "#00c853" if s["confidence"] >= 0.80 else "#ff9100" if s["confidence"] >= 0.70 else "#ff1744"
            sig_colour  = "#00c853" if "⬆" in s["signal"] or "Steam" in s["signal"] or "Move" in s["signal"] else "#ff1744" if "⬇" in s["signal"] or "Drift" in s["signal"] else "#888888"
            rows += f"""<tr>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{s['race']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;font-weight:bold;">{s['horse']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{s['jockey']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{s['trainer']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{s['odds']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;color:{conf_colour};font-weight:bold;">{conf_pct}%</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;color:{sig_colour};">{s['signal']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{s['going']}</td>
            </tr>"""
        return rows

    def acca_rows():
        rows = ""
        for a in data["accas"]:
            conf_pct    = int(a["confidence"] * 100)
            conf_colour = "#00c853" if a["confidence"] >= 0.80 else "#ff9100"
            rows += f"""<tr>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;font-weight:bold;">{a['type']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{a['legs']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{a['odds']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;color:{conf_colour};font-weight:bold;">{conf_pct}%</td>
            </tr>"""
        return rows

    def alert_rows():
        rows = ""
        for a in data["alerts"]:
            bg     = "#2a0000" if a["level"] == "HIGH" else "#2a1a00" if a["level"] == "MEDIUM" else "#002a0a"
            border = "#ff1744" if a["level"] == "HIGH" else "#ff9100" if a["level"] == "MEDIUM" else "#00c853"
            icon   = "🔴" if a["level"] == "HIGH" else "🟠" if a["level"] == "MEDIUM" else "🟢"
            rows += f"""<tr><td style="padding:10px;background:{bg};border-left:4px solid {border};border-radius:4px;margin-bottom:4px;display:block;">
                {icon} <strong>{a['level']}</strong> — {a['message']}
            </td></tr>"""
        if not rows:
            rows = '<tr><td style="padding:10px;color:#888;">No alerts at this time.</td></tr>'
        return rows

    def going_rows():
        rows = ""
        for g in data["going"]:
            rows += f"""<tr>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{g['course']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{g['going']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{g['trend']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{g['updated']}</td>
            </tr>"""
        return rows

    def nr_rows():
        rows = ""
        for nr in data["non_runners"]:
            rows += f"""<tr>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;color:#ff1744;font-weight:bold;">{nr['horse']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{nr['race']}</td>
                <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{nr['reason']}</td>
            </tr>"""
        if not rows:
            rows = '<tr><td colspan="3" style="padding:10px;color:#888;">No non-runners declared.</td></tr>'
        return rows

    perf_block = ""
    if data.get("perf_note"):
        perf_block = f"""
        <div style="background:#1c1f2e;border-radius:12px;padding:16px;margin-bottom:20px;">
            <p style="margin:0;color:#888;font-size:13px;">🧠 Learning Engine — {data['perf_note']}</p>
        </div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="background:#0f1117;color:#e0e0e0;font-family:Arial,sans-serif;margin:0;padding:20px;">
<div style="max-width:900px;margin:0 auto;">

  <div style="background:#1c1f2e;border-radius:12px;padding:24px;margin-bottom:20px;border-left:5px solid #00c853;">
    <h1 style="margin:0;color:#ffffff;font-size:22px;">🏇 Racing Engine — {update_type}</h1>
    <p style="margin:6px 0 0;color:#888;">{data['date']} &nbsp;|&nbsp; {data['generated_at']} BST &nbsp;|&nbsp; UK + Irish Racing</p>
  </div>

  <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:20px;">
    <h2 style="color:#ffffff;margin-top:0;">🚨 Alerts</h2>
    <table style="width:100%;border-collapse:collapse;">{alert_rows()}</table>
  </div>

  <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:20px;">
    <h2 style="color:#ffffff;margin-top:0;">📋 Top Selections</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="color:#888;text-align:left;">
        <th style="padding:8px;">Race</th><th style="padding:8px;">Horse</th>
        <th style="padding:8px;">Jockey</th><th style="padding:8px;">Trainer</th>
        <th style="padding:8px;">Odds</th><th style="padding:8px;">Confidence</th>
        <th style="padding:8px;">Signal</th><th style="padding:8px;">Going</th>
      </tr></thead>
      <tbody>{selection_rows()}</tbody>
    </table>
  </div>

  <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:20px;">
    <h2 style="color:#ffffff;margin-top:0;">🎰 Accumulator Permutations</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="color:#888;text-align:left;">
        <th style="padding:8px;">Type</th><th style="padding:8px;">Selections</th>
        <th style="padding:8px;">Odds</th><th style="padding:8px;">Confidence</th>
      </tr></thead>
      <tbody>{acca_rows()}</tbody>
    </table>
  </div>

  <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:20px;">
    <h2 style="color:#ffffff;margin-top:0;">🌿 Going Reports</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="color:#888;text-align:left;">
        <th style="padding:8px;">Course</th><th style="padding:8px;">Going</th>
        <th style="padding:8px;">Trend</th><th style="padding:8px;">Updated</th>
      </tr></thead>
      <tbody>{going_rows()}</tbody>
    </table>
  </div>

  <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:20px;">
    <h2 style="color:#ffffff;margin-top:0;">❌ Non-Runners</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="color:#888;text-align:left;">
        <th style="padding:8px;">Horse</th><th style="padding:8px;">Race</th><th style="padding:8px;">Reason</th>
      </tr></thead>
      <tbody>{nr_rows()}</tbody>
    </table>
  </div>

  {perf_block}

  <div style="text-align:center;color:#444;font-size:12px;padding:16px;">
    Racing Engine v1.1 &nbsp;|&nbsp; Phase 1: Personal Research Tool &nbsp;|&nbsp;
    <a href="https://racing-engine-dash.streamlit.app" style="color:#00c853;">Open Dashboard (PIN: 1012)</a>
  </div>

</div></body></html>"""


# ── Email Sender ──────────────────────────────────────────────
def send_email(subject: str, html_content: str, recipient: str = RECIPIENT) -> bool:
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print(f"[Email] No sender credentials — skipping: {subject}")
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


# ── Brief Dispatcher ──────────────────────────────────────────
class DailyBrief:

    def send_morning_brief(self):
        data = build_brief_data(update_number=0)
        html = build_html_email(data)
        subject = f"🏇 Racing Engine — Morning Brief | {data['date']}"
        send_email(subject, html)
        print(f"[Brief] Morning brief sent at {data['generated_at']}")

    def send_update(self, update_number: int):
        data = build_brief_data(update_number=update_number)
        html = build_html_email(data)
        now = datetime.now().strftime("%H:%M")
        subject = f"🔄 Racing Engine — Update #{update_number} | {now} BST"
        send_email(subject, html)
        print(f"[Brief] Update #{update_number} sent at {now}")

    def send_instant_alert(self, alert_type: str, message: str):
        data = build_brief_data(update_number=0)
        data["alerts"] = [{"level": "HIGH", "message": message}]
        html = build_html_email(data, is_alert=True, alert_message=message)
        subject = f"🚨 Racing Engine ALERT — {alert_type}"
        send_email(subject, html)
        print(f"[Alert] Instant alert sent: {alert_type}")


# ── Utilities ─────────────────────────────────────────────────
def _to_decimal(odds_str) -> float:
    try:
        s = str(odds_str).strip()
        if "/" in s:
            n, d = s.split("/")
            return (float(n) + float(d)) / float(d)
        return float(s)
    except Exception:
        return 2.0


def _sample_selections():
    return [
        {"race": "Sample Race",  "horse": "Sample Horse", "jockey": "-", "trainer": "-",
         "odds": "2/1", "confidence": 0.70, "signal": "Stable", "going": "Good"},
    ]

def _sample_accas():
    return [{"type": "Double", "legs": "Live data loading…", "odds": "N/A", "confidence": 0.0}]

def _sample_going():
    return [{"course": "Loading…", "going": "Live", "trend": "—", "updated": "—"}]
