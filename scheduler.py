# Racing Engine — Scheduler
# Version: 1.1
# Date: 20 April 2026
# Runs the engine on a timed schedule:
# - 07:30 BST: Reset alert state for new racing day
# - 08:00 BST: Morning brief email to richardking123@outlook.com
# - Every 2 hours 10:00–20:00 BST: Rolling update email
# - Every 60 seconds: Alert monitor poll (steam, drift, non-runners, going)
# - Every 2 minutes: Settlement engine poll (settle finished races)
# - 21:00 BST: Daily learning loop adjustment

import schedule
import time
from datetime import datetime

from briefs.daily_brief import DailyBrief, send_confirmed_selections
from alerts.monitor import AlertMonitor
from alerts.market_monitor import MultiSourceMarketMonitor
from settlement.settle import SettlementEngine
from learning.loop import LearningLoop

brief          = DailyBrief()
monitor        = AlertMonitor()
multi_monitor  = MultiSourceMarketMonitor()
settler        = SettlementEngine()
learner        = LearningLoop()

update_counter = [0]


# ── Morning Reset ─────────────────────────────────────────────
def morning_reset():
    """Clear alert state at the start of each racing day."""
    print(f"\n[Scheduler] ── New Racing Day Reset ({datetime.now().strftime('%H:%M')}) ──")
    monitor.reset_state()
    multi_monitor.reset_state()


# ── Morning Brief ─────────────────────────────────────────────
def morning_brief():
    print(f"\n[Scheduler] ── Morning Brief ({datetime.now().strftime('%H:%M')}) ──")
    brief.send_morning_brief()
    # Auto-record all today's runners as recommendations
    try:
        learner.auto_record_day()
    except Exception as e:
        print(f"[Scheduler] Auto-record failed: {e}")


# ── Rolling Update ────────────────────────────────────────────
def rolling_update():
    update_counter[0] += 1
    print(f"\n[Scheduler] ── Rolling Update #{update_counter[0]} ({datetime.now().strftime('%H:%M')}) ──")
    brief.send_update(update_counter[0])


# ── Alert Poll ────────────────────────────────────────────────
def alert_poll():
    """Run every 60 seconds — multi-source bookmaker + exchange monitor."""
    # Run multi-source monitor (Betfair + Racing API + Oddschecker)
    try:
        multi_alerts = multi_monitor.run_poll()
        for alert in multi_alerts:
            if alert.get("level") == "HIGH":
                brief.send_instant_alert(alert.get("type", "Alert"), alert.get("message", ""))
    except Exception as e:
        print(f"[Scheduler] Multi-source monitor error: {e}")

    # Also run single-source monitor for non-runners + going changes
    try:
        alerts = monitor.run_poll()
        for alert in alerts:
            if alert.get("level") == "HIGH":
                brief.send_instant_alert(alert.get("type", "Alert"), alert.get("message", ""))
    except Exception as e:
        print(f"[Scheduler] Alert poll error: {e}")


# ── Settlement Poll ───────────────────────────────────────────
def settlement_poll():
    """Run every 2 minutes — picks up finished races and settles them."""
    try:
        settler.run_settlement_poll()
    except Exception as e:
        print(f"[Scheduler] Settlement poll error: {e}")
    # Auto-settle recommendations against live results
    try:
        learner.auto_settle()
    except Exception as e:
        print(f"[Scheduler] Auto-settle failed: {e}")


# ── Daily Learning Adjustment ─────────────────────────────────
def daily_learning():
    """Run at end of racing day — adjusts signal weightings from today's results."""
    print(f"\n[Scheduler] ── Daily Learning Adjustment ({datetime.now().strftime('%H:%M')}) ──")
    try:
        learner.adjust_weightings()
        stats = learner.get_performance_stats()
        print(f"[Scheduler] Hit rate: {stats['hit_rate_pct']}% from {stats['settled_races']} races")
    except Exception as e:
        print(f"[Scheduler] Learning adjustment error: {e}")


# ── Schedule ──────────────────────────────────────────────────

# Daily reset at 07:30 BST (06:30 UTC)
schedule.every().day.at("06:30").do(morning_reset)

# Morning brief at 08:00 BST (07:00 UTC)
schedule.every().day.at("07:00").do(morning_brief)

# v2.5.43: 13:30 BST (12:30 UTC) — Confirmed selections "final word" email
def _confirmed_selections_job():
    print(f"\n[Scheduler] ── Confirmed Selections 13:30 BST ({datetime.now().strftime('%H:%M')}) ──")
    try:
        send_confirmed_selections()
    except Exception as _e:
        print(f"[Scheduler] Confirmed selections error: {_e}")
schedule.every().day.at("12:30").do(_confirmed_selections_job)

# Rolling updates every 2 hours 10:00–20:00 BST (09:00–19:00 UTC)
schedule.every().day.at("09:00").do(rolling_update)   # 10:00 BST
schedule.every().day.at("11:00").do(rolling_update)   # 12:00 BST
schedule.every().day.at("13:00").do(rolling_update)   # 14:00 BST
schedule.every().day.at("15:00").do(rolling_update)   # 16:00 BST
schedule.every().day.at("17:00").do(rolling_update)   # 18:00 BST
schedule.every().day.at("19:00").do(rolling_update)   # 20:00 BST

# Alert poll every 60 seconds (during racing hours 10:00–20:00 BST)
schedule.every(60).seconds.do(alert_poll)

# Settlement poll every 2 minutes
schedule.every(2).minutes.do(settlement_poll)

# Learning loop adjustment at 21:00 BST (20:00 UTC)
schedule.every().day.at("20:00").do(daily_learning)


# ── Startup ───────────────────────────────────────────────────
print("\n🏇 Racing Engine Scheduler v1.2 — Started")
print(f"  Time: {datetime.now().strftime('%A %d %B %Y %H:%M')} BST")
print("  Schedule:")
print("    07:30 BST — Daily reset")
print("    08:00 BST — Morning brief → richardking123@outlook.com")
print("    13:30 BST — Confirmed selections (final word, 30%-drift drop)")
print("    10:00, 12:00, 14:00, 16:00, 18:00, 20:00 BST — Rolling updates")
print("    Every 60s — Alert monitor (steam / drift / non-runners / going)")
print("    Every 2m  — Settlement engine poll")
print("    21:00 BST — Daily learning loop adjustment")
print()

while True:
    schedule.run_pending()
    time.sleep(10)
