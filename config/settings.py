# Racing Engine — Configuration
# Version: 0.1
# Date: 20 April 2026

# ── API Credentials ──────────────────────────────────────────
# The Racing API
RACING_API_USERNAME = ""  # Add after email verification
RACING_API_PASSWORD = ""  # Add after email verification

# Betfair
BETFAIR_APP_KEY = "1Bj49mxBZBQ961WM"  # Delay key (free dev)
BETFAIR_USERNAME = ""  # Your Betfair username
BETFAIR_PASSWORD = ""  # Your Betfair password

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

# ── Accumulator Settings ─────────────────────────────────────
MAX_RACES_PER_DAY = 8
MIN_CONFIDENCE_FOR_ACCA = 0.55  # Only include horses above 65% confidence

# ── Alert Thresholds ─────────────────────────────────────────
MARKET_MOVE_THRESHOLD = 0.20  # Flag if odds move more than 20%
TIME_BEFORE_OFF_ALERT = 30    # Alert window in minutes before race off
