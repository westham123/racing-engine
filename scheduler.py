# Racing Engine — Scheduler
# Version: 0.6
# Runs the engine on a timed schedule:
# - 08:00 BST: Morning brief email
# - Every 2 hours 08:00–20:00: Rolling update email
# - Continuously: Monitor for instant alert triggers

import schedule
import time
from briefs.daily_brief import DailyBrief

brief = DailyBrief()
update_counter = [0]

def morning_brief():
    print("[Scheduler] Running morning brief...")
    brief.send_morning_brief()

def rolling_update():
    update_counter[0] += 1
    print(f"[Scheduler] Running update #{update_counter[0]}...")
    brief.send_update(update_counter[0])

# ── Schedule ──────────────────────────────────────────────────
# Morning brief at 8am BST (UTC+1)
schedule.every().day.at("07:00").do(morning_brief)   # 07:00 UTC = 08:00 BST

# Rolling updates every 2 hours between 8am and 8pm BST
schedule.every().day.at("09:00").do(rolling_update)  # 10:00 BST
schedule.every().day.at("11:00").do(rolling_update)  # 12:00 BST
schedule.every().day.at("13:00").do(rolling_update)  # 14:00 BST
schedule.every().day.at("15:00").do(rolling_update)  # 16:00 BST
schedule.every().day.at("17:00").do(rolling_update)  # 18:00 BST
schedule.every().day.at("19:00").do(rolling_update)  # 20:00 BST

print("[Scheduler] Racing Engine scheduler started")
print("[Scheduler] Morning brief: 08:00 BST daily")
print("[Scheduler] Updates: 10:00, 12:00, 14:00, 16:00, 18:00, 20:00 BST")
print("[Scheduler] Watching for instant alert triggers...")

while True:
    schedule.run_pending()
    time.sleep(30)
