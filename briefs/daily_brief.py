# Racing Engine — Daily Brief Generator + Email System
# Version: 0.6
# Date: 20 April 2026
# Delivers: 8am morning brief + 2-hourly updates + instant alerts
# Recipient: richardking123@outlook.com

import smtplib
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, date
import pandas as pd

# ── Email Config ──────────────────────────────────────────────
RECIPIENT = "richardking123@outlook.com"

# Email is sent via Gmail SMTP (free)
# Requires a Gmail address set up as the sender
# Set these in config/settings.py or as environment variables
import os
SENDER_EMAIL    = os.getenv("SENDER_EMAIL", "")
SENDER_PASSWORD = os.getenv("SENDER_APP_PASSWORD", "")  # Gmail App Password


# ── Sample Data Builder (replaced by live feeds in v0.7) ──────
def build_brief_data(update_number: int = 0) -> dict:
    """
    Assembles all data signals into a structured brief dict.
    update_number 0 = morning brief, 1+ = rolling updates
    """
    now = datetime.now()

    selections = [
        {"race": "14:00 Cheltenham",     "horse": "Constitution Hill",  "jockey": "N. de Boinville", "trainer": "N. Henderson", "odds": "5/4",  "confidence": 0.91, "signal": "⬆ Steaming", "going": "Good-Soft"},
        {"race": "14:35 Cheltenham",     "horse": "Energumene",         "jockey": "P. Townend",      "trainer": "W. Mullins",   "odds": "2/1",  "confidence": 0.84, "signal": "⬆ Move",     "going": "Good-Soft"},
        {"race": "15:10 Cheltenham",     "horse": "Galopin Des Champs", "jockey": "P. Townend",      "trainer": "W. Mullins",   "odds": "4/6",  "confidence": 0.88, "signal": "Stable",     "going": "Good-Soft"},
        {"race": "15:45 Cheltenham",     "horse": "Fact To File",       "jockey": "M. Walsh",        "trainer": "W. Mullins",   "odds": "7/2",  "confidence": 0.72, "signal": "⬆ Move",     "going": "Good-Soft"},
        {"race": "14:20 Leopardstown",   "horse": "Brighterdaysahead",  "jockey": "R. Blackmore",    "trainer": "G. Elliott",   "odds": "9/4",  "confidence": 0.79, "signal": "Stable",     "going": "Soft"},
        {"race": "15:00 Leopardstown",   "horse": "Marine Nationale",   "jockey": "S. Flanagan",     "trainer": "P. Nolan",     "odds": "11/4", "confidence": 0.67, "signal": "⬇ Drifting", "going": "Soft"},
    ]

    accas = [
        {"type": "Double",   "legs": "Constitution Hill + Galopin Des Champs",                               "odds": "11/8",  "confidence": 0.89},
        {"type": "Treble",   "legs": "Energumene + Constitution Hill + Galopin Des Champs",                   "odds": "11/2",  "confidence": 0.81},
        {"type": "Lucky 15", "legs": "Energumene, Constitution Hill, Galopin Des Champs, Fact To File",       "odds": "Various","confidence": 0.78},
        {"type": "Double",   "legs": "Constitution Hill + Brighterdaysahead",                                 "odds": "9/4",   "confidence": 0.74},
    ]

    going = [
        {"course": "Cheltenham",    "going": "Good to Soft", "trend": "Drying",  "updated": "07:30"},
        {"course": "Leopardstown",  "going": "Soft",         "trend": "Stable",  "updated": "07:15"},
        {"course": "Sandown",       "going": "Good",         "trend": "Drying",  "updated": "07:45"},
    ]

    non_runners = [
        {"horse": "Honeysuckle", "race": "15:40 Leopardstown", "reason": "Going too soft"},
    ]

    alerts = [
        {"level": "HIGH",   "message": "Constitution Hill steamed 6/4 → 5/4 overnight — confidence upgraded to 91%"},
        {"level": "MEDIUM", "message": "Cheltenham going easing slightly — monitor before 14:00"},
        {"level": "LOW",    "message": "Marine Nationale drifting — confidence reduced to 67%"},
    ] if update_number == 0 else [
        {"level": "HIGH",   "message": "Non-runner declared: Honeysuckle (15:40 Leopardstown) — permutations updated"},
        {"level": "MEDIUM", "message": "Fact To File market move: 9/2 → 7/2 — confidence upgraded"},
    ]

    return {
        "selections":   selections,
        "accas":        accas,
        "going":        going,
        "non_runners":  non_runners,
        "alerts":       alerts,
        "generated_at": now.strftime("%H:%M"),
        "date":         now.strftime("%A %d %B %Y"),
        "update_number": update_number
    }


# ── HTML Email Builder ────────────────────────────────────────
def build_html_email(data: dict, is_alert: bool = False, alert_message: str = "") -> str:
    """Builds a clean, formatted HTML email from brief data."""

    update_type = "🚨 INSTANT ALERT" if is_alert else (
        "☀️ MORNING BRIEF" if data["update_number"] == 0 else
        f"🔄 UPDATE #{data['update_number']}"
    )

    # Selections table rows
    selection_rows = ""
    for s in data["selections"]:
        conf_pct = int(s["confidence"] * 100)
        conf_colour = "#00c853" if s["confidence"] >= 0.80 else "#ff9100" if s["confidence"] >= 0.70 else "#ff1744"
        signal_colour = "#00c853" if "⬆" in s["signal"] else "#ff1744" if "⬇" in s["signal"] else "#888888"
        selection_rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{s['race']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;font-weight:bold;">{s['horse']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{s['jockey']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{s['trainer']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{s['odds']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;color:{conf_colour};font-weight:bold;">{conf_pct}%</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;color:{signal_colour};">{s['signal']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{s['going']}</td>
        </tr>"""

    # Acca table rows
    acca_rows = ""
    for a in data["accas"]:
        conf_pct = int(a["confidence"] * 100)
        conf_colour = "#00c853" if a["confidence"] >= 0.80 else "#ff9100"
        acca_rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;font-weight:bold;">{a['type']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{a['legs']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{a['odds']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;color:{conf_colour};font-weight:bold;">{conf_pct}%</td>
        </tr>"""

    # Alerts
    alert_rows = ""
    for a in data["alerts"]:
        bg = "#2a0000" if a["level"] == "HIGH" else "#2a1a00" if a["level"] == "MEDIUM" else "#002a0a"
        border = "#ff1744" if a["level"] == "HIGH" else "#ff9100" if a["level"] == "MEDIUM" else "#00c853"
        icon = "🔴" if a["level"] == "HIGH" else "🟠" if a["level"] == "MEDIUM" else "🟢"
        alert_rows += f"""
        <tr>
            <td style="padding:10px;background:{bg};border-left:4px solid {border};margin-bottom:6px;display:block;">
                {icon} <strong>{a['level']}</strong> — {a['message']}
            </td>
        </tr>"""

    # Going rows
    going_rows = ""
    for g in data["going"]:
        going_rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{g['course']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{g['going']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{g['trend']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{g['updated']}</td>
        </tr>"""

    # Non-runners
    nr_rows = ""
    for nr in data["non_runners"]:
        nr_rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;color:#ff1744;font-weight:bold;">{nr['horse']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{nr['race']}</td>
            <td style="padding:8px;border-bottom:1px solid #2a2a2a;">{nr['reason']}</td>
        </tr>"""

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="background:#0f1117;color:#e0e0e0;font-family:Arial,sans-serif;margin:0;padding:20px;">

    <div style="max-width:900px;margin:0 auto;">

        <!-- Header -->
        <div style="background:#1c1f2e;border-radius:12px;padding:24px;margin-bottom:20px;border-left:5px solid #00c853;">
            <h1 style="margin:0;color:#ffffff;font-size:22px;">🏇 Racing Engine — {update_type}</h1>
            <p style="margin:6px 0 0;color:#888;">{data['date']} &nbsp;|&nbsp; Generated: {data['generated_at']} BST &nbsp;|&nbsp; UK + Irish Racing</p>
        </div>

        <!-- Alerts -->
        <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:20px;">
            <h2 style="color:#ffffff;margin-top:0;">🚨 Alerts</h2>
            <table style="width:100%;border-collapse:collapse;">{alert_rows}</table>
        </div>

        <!-- Top Selections -->
        <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:20px;">
            <h2 style="color:#ffffff;margin-top:0;">📋 Top Selections</h2>
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
                <thead>
                    <tr style="color:#888;text-align:left;">
                        <th style="padding:8px;">Race</th>
                        <th style="padding:8px;">Horse</th>
                        <th style="padding:8px;">Jockey</th>
                        <th style="padding:8px;">Trainer</th>
                        <th style="padding:8px;">Odds</th>
                        <th style="padding:8px;">Confidence</th>
                        <th style="padding:8px;">Signal</th>
                        <th style="padding:8px;">Going</th>
                    </tr>
                </thead>
                <tbody>{selection_rows}</tbody>
            </table>
        </div>

        <!-- Accumulator Permutations -->
        <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:20px;">
            <h2 style="color:#ffffff;margin-top:0;">🎰 Accumulator Permutations</h2>
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
                <thead>
                    <tr style="color:#888;text-align:left;">
                        <th style="padding:8px;">Type</th>
                        <th style="padding:8px;">Selections</th>
                        <th style="padding:8px;">Odds</th>
                        <th style="padding:8px;">Confidence</th>
                    </tr>
                </thead>
                <tbody>{acca_rows}</tbody>
            </table>
        </div>

        <!-- Going Reports -->
        <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:20px;">
            <h2 style="color:#ffffff;margin-top:0;">🌿 Going Reports</h2>
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
                <thead>
                    <tr style="color:#888;text-align:left;">
                        <th style="padding:8px;">Course</th>
                        <th style="padding:8px;">Going</th>
                        <th style="padding:8px;">Trend</th>
                        <th style="padding:8px;">Updated</th>
                    </tr>
                </thead>
                <tbody>{going_rows}</tbody>
            </table>
        </div>

        <!-- Non-Runners -->
        <div style="background:#1c1f2e;border-radius:12px;padding:20px;margin-bottom:20px;">
            <h2 style="color:#ffffff;margin-top:0;">❌ Non-Runners</h2>
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
                <thead>
                    <tr style="color:#888;text-align:left;">
                        <th style="padding:8px;">Horse</th>
                        <th style="padding:8px;">Race</th>
                        <th style="padding:8px;">Reason</th>
                    </tr>
                </thead>
                <tbody>{nr_rows}</tbody>
            </table>
        </div>

        <!-- Footer -->
        <div style="text-align:center;color:#444;font-size:12px;padding:16px;">
            Racing Engine v0.6 &nbsp;|&nbsp; Phase 1: Personal Research Tool &nbsp;|&nbsp;
            <a href="https://racing-engine-dash.streamlit.app" style="color:#00c853;">Open Dashboard</a>
        </div>

    </div>
    </body>
    </html>
    """
    return html


# ── Email Sender ──────────────────────────────────────────────
def send_email(subject: str, html_content: str, recipient: str = RECIPIENT) -> bool:
    """Sends an HTML email via Gmail SMTP."""
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("[Email] No sender credentials configured — skipping send")
        print(f"[Email] Would have sent: {subject}")
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
    """
    Orchestrates all brief and alert emails.
    - 8:00am: Morning brief
    - Every 2 hours: Rolling update
    - Instantly: Significant change alerts
    """

    def send_morning_brief(self):
        """Send the 8am morning brief."""
        data = build_brief_data(update_number=0)
        html = build_html_email(data)
        subject = f"🏇 Racing Engine — Morning Brief | {data['date']}"
        send_email(subject, html)
        print(f"[Brief] Morning brief sent at {data['generated_at']}")

    def send_update(self, update_number: int):
        """Send a 2-hourly rolling update."""
        data = build_brief_data(update_number=update_number)
        html = build_html_email(data)
        now = datetime.now().strftime("%H:%M")
        subject = f"🔄 Racing Engine — Update #{update_number} | {now} BST"
        send_email(subject, html)
        print(f"[Brief] Update #{update_number} sent at {now}")

    def send_instant_alert(self, alert_type: str, message: str):
        """Fire an instant alert email for a significant change."""
        data = build_brief_data(update_number=0)
        data["alerts"] = [{"level": "HIGH", "message": message}]
        html = build_html_email(data, is_alert=True, alert_message=message)
        subject = f"🚨 Racing Engine ALERT — {alert_type}"
        send_email(subject, html)
        print(f"[Alert] Instant alert sent: {alert_type}")
