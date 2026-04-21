# Racing Engine — Configuration
# Version: 0.1
# Date: 20 April 2026

# ── API Credentials ──────────────────────────────────────────
# The Racing API
RACING_API_USERNAME = ""  # Add after email verification
RACING_API_PASSWORD = ""  # Add after email verification

# Betfair
BETFAIR_APP_KEY = "1Bj49mxBZBQ961WM"  # Delay key (free dev)
BETFAIR_USERNAME = "richardking123@outlook.com"
BETFAIR_PASSWORD = "Pa55word2018!"

# ── Scope ────────────────────────────────────────────────────
COUNTRIES = ["GB", "IE"]  # UK and Irish racing only

# ── Signal Weightings (v1.1 — 10 signals, learning loop will adjust) ──
# Total must sum to 1.0
# BSP and race_pace start at low weight until live data confirms their value
WEIGHTS = {
    "market_odds":    0.22,   # Implied prob from bookmaker odds
    "horse_form":     0.18,   # Recent form string (weighted recency)
    "track_form":     0.14,   # Course-specific form
    "going":          0.10,   # Going preference match
    "trainer_form":   0.09,   # Trainer 14/30-day win rate
    "jockey_form":    0.09,   # Jockey 14/30-day win rate
    "market_moves":   0.07,   # Steam/drift signal
    "bsp_signal":     0.05,   # Betfair BSP vs bookmaker price
    "jump_index":     0.03,   # Jump ability proxy
    "race_pace":      0.03,   # Speed rating vs course par
}

# ── Staking Rules (permanent) ──────────────────────────────
# Short price cut-off: anything AT or BELOW 4/6 (1.67 decimal) excluded
# from ALL bets — six-timer and Lucky 15.
# Previously the cut-off only applied to the Lucky 15; now it applies to both.
SHORT_PRICE_CUTOFF_DECIMAL = 1.67   # 4/6
SHORT_PRICE_CUTOFF_DISPLAY = "4/6"

# Confidence threshold — only runners above this qualify for any selection
MIN_CONFIDENCE = 0.60

# ── Accumulator Settings ─────────────────────────────────────
MAX_RACES_PER_DAY = 8
MIN_CONFIDENCE_FOR_ACCA = 0.60  # Updated from 0.55

# ── Alert Thresholds ─────────────────────────────────────────
MARKET_MOVE_THRESHOLD = 0.20  # Flag if odds move more than 20%
TIME_BEFORE_OFF_ALERT = 30    # Alert window in minutes before race off
