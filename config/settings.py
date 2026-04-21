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

# ── Signal Weightings (initial — learning loop will adjust) ──
WEIGHTS = {
    "market_odds":    0.25,
    "horse_form":     0.20,
    "track_form":     0.15,
    "going":          0.10,
    "trainer_form":   0.10,
    "jockey_form":    0.10,
    "market_moves":   0.07,
    "jump_index":     0.03,
}

# ── Accumulator Settings ─────────────────────────────────────
MAX_RACES_PER_DAY = 8
MIN_CONFIDENCE_FOR_ACCA = 0.55  # Only include horses above 65% confidence

# ── Alert Thresholds ─────────────────────────────────────────
MARKET_MOVE_THRESHOLD = 0.20  # Flag if odds move more than 20%
TIME_BEFORE_OFF_ALERT = 30    # Alert window in minutes before race off
