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

# ── Signal Weightings (v2.5.43 — dead signals zero-weighted) ──
# Total must sum to 1.0. Five signals are confirmed-dead (no live data feed)
# and have been zero-weighted. Their 0.36 share is redistributed to the live
# signals, with the largest share moved to market_moves (smart-money signal).
WEIGHTS = {
    "horse_form":     0.32,   # Best single predictor we have
    "market_odds":    0.28,   # Strong sanity-check signal
    "market_moves":   0.20,   # Steam/drift — most actionable smart-money signal
    "trainer_form":   0.12,   # Trainer 14/30-day win rate
    "jockey_form":    0.08,   # Jockey 14/30-day win rate
    # Dead signals — zero-weighted until real data feeds exist
    "track_form":     0.00,   # Needs Racing API
    "going":          0.00,   # Needs going history per horse
    "bsp_signal":     0.00,   # Betfair 403 on free key
    "race_pace":      0.00,   # Not implemented
    "jump_index":     0.00,   # Not implemented
}

# ── Staking Rules (permanent) ──────────────────────────────
# Short price cut-off: anything AT or BELOW 4/6 (1.67 decimal) excluded
# from ALL bets — six-timer and Lucky 15.
# Previously the cut-off only applied to the Lucky 15; now it applies to both.
SHORT_PRICE_CUTOFF_DECIMAL = 1.67   # 4/6
SHORT_PRICE_CUTOFF_DISPLAY = "4/6"

# Confidence threshold — only runners above this qualify for any selection
# v2.5.43: aligned with live gate in briefs/_get_official_selections (was 0.60)
MIN_CONFIDENCE = 0.55

# ── Accumulator Settings ─────────────────────────────────────
MAX_RACES_PER_DAY = 8
MIN_CONFIDENCE_FOR_ACCA = 0.60  # Updated from 0.55

# ── Alert Thresholds ─────────────────────────────────────────
MARKET_MOVE_THRESHOLD = 0.20  # Flag if odds move more than 20%
TIME_BEFORE_OFF_ALERT = 30    # Alert window in minutes before race off
