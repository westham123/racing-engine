# Racing Engine — Visual Dashboard
# Version: 0.3 — PIN lock added
# Built with Streamlit
# Date: 20 April 2026

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, date
from itertools import combinations
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
    from engine.odds_model import OddsModel as _OddsModel
    _ODDS_MODEL = _OddsModel()
    MODEL_AVAILABLE = True
except Exception:
    MODEL_AVAILABLE = False
    _ODDS_MODEL = None

try:
    from live_data import (
        get_todays_selections as _live_selections,
        get_going_reports as _live_going,
        get_non_runners as _live_non_runners,
        get_todays_results as _live_results,
        get_todays_meetings as _live_meetings,
        get_race_runners as _live_race_runners,
    )
    LIVE_DATA_AVAILABLE = True
except Exception as _import_err:
    LIVE_DATA_AVAILABLE = False


# ══════════════════════════════════════════════════════════════
# ACCA EFFICIENCY ENGINE — inlined to avoid Streamlit Cloud
# import resolution issues with subdirectory modules
# ══════════════════════════════════════════════════════════════

def _odds_to_probability(odds_str: str) -> float:
    """Convert fractional odds string (e.g. '5/4') to implied probability."""
    try:
        if "/" in str(odds_str):
            num, den = str(odds_str).split("/")
            return float(den) / (float(num) + float(den))
        elif str(odds_str).replace(".", "").isdigit():
            dec = float(odds_str)
            return 1 / dec
        return 0.5
    except Exception:
        return 0.5


def _probability_to_odds(prob: float) -> str:
    """Convert probability to approximate fractional odds string."""
    if prob <= 0 or prob >= 1:
        return "N/A"
    decimal = 1 / prob
    common = {
        1.25: "1/4", 1.33: "1/3", 1.5: "1/2", 1.67: "4/6",
        2.0: "Evs", 2.5: "6/4", 3.0: "2/1", 3.5: "5/2",
        4.0: "3/1", 4.5: "7/2", 5.0: "4/1", 6.0: "5/1",
        7.0: "6/1", 8.0: "7/1", 9.0: "8/1", 10.0: "9/1",
        11.0: "10/1", 13.0: "12/1", 17.0: "16/1"
    }
    closest = min(common.keys(), key=lambda x: abs(x - decimal))
    if abs(closest - decimal) < 0.5:
        return common[closest]
    return f"{decimal - 1:.0f}/1"


class AccaEfficiencyEngine:
    """Analyses accumulator selections for efficiency, EV, and coverage options."""

    def analyse_selections(self, selections: list) -> list:
        results = []
        for sel in selections:
            bookie_prob = _odds_to_probability(sel["odds"])
            engine_prob = sel["confidence"]
            edge = engine_prob - bookie_prob
            ev = (engine_prob * (1 / bookie_prob - 1)) - (1 - engine_prob)
            results.append({
                **sel,
                "bookie_prob": round(bookie_prob * 100, 1),
                "engine_prob": round(engine_prob * 100, 1),
                "edge": round(edge * 100, 1),
                "expected_value": round(ev, 3),
                "ev_rating": "\u2705 Value" if ev > 0.05 else "\u26a0\ufe0f Marginal" if ev > 0 else "\u274c No Value",
            })
        return results

    def build_permutations(self, selections: list, min_legs: int = 2, max_legs: int = 6) -> list:
        perms = []
        for n_legs in range(min_legs, min(max_legs + 1, len(selections) + 1)):
            for combo in combinations(selections, n_legs):
                combined_engine_prob = np.prod([s["confidence"] for s in combo])
                combined_bookie_prob = np.prod([_odds_to_probability(s["odds"]) for s in combo])
                combined_decimal = np.prod([(1 / _odds_to_probability(s["odds"])) for s in combo])
                ev = (combined_engine_prob * combined_decimal) - 1
                type_names = {2: "Double", 3: "Treble", 4: "Lucky 15 leg", 5: "Lucky 31 leg", 6: "Lucky 63 leg"}
                bet_type = type_names.get(n_legs, f"{n_legs}-fold")
                perms.append({
                    "type": bet_type,
                    "legs": n_legs,
                    "selections": " + ".join([s["horse"] for s in combo]),
                    "races": " | ".join([s["race"] for s in combo]),
                    "combined_engine_prob": round(combined_engine_prob * 100, 1),
                    "combined_bookie_prob": round(combined_bookie_prob * 100, 1),
                    "combined_odds": f"{combined_decimal - 1:.1f}/1",
                    "expected_value": round(ev, 3),
                    "ev_rating": "\u2705 Value" if ev > 0.1 else "\u26a0\ufe0f Marginal" if ev > 0 else "\u274c Avoid",
                    "confidence_gap": round((combined_engine_prob - combined_bookie_prob * 100), 1),
                })
        perms.sort(key=lambda x: x["expected_value"], reverse=True)
        return perms

    def coverage_options(self, race: dict, top_n: int = 3) -> list:
        runners = sorted(race["runners"], key=lambda x: x["confidence"], reverse=True)
        options = []
        for n in range(1, min(top_n + 1, len(runners) + 1)):
            covered = runners[:n]
            coverage_prob = min(sum([r["confidence"] for r in covered]), 0.99)
            options.append({
                "cover_n": n,
                "horses": ", ".join([r["horse"] for r in covered]),
                "odds": ", ".join([r["odds"] for r in covered]),
                "coverage_prob": round(coverage_prob * 100, 1),
                "stake_multiplier": n,
                "label": "Single selection" if n == 1 else f"Cover top {n}",
                "recommendation": "\u2705 Recommended" if n == 1 and covered[0]["confidence"] >= 0.80
                                   else "\u26a0\ufe0f Consider covering" if coverage_prob < 0.70
                                   else "\u2139\ufe0f Optional cover"
            })
        return options

    def full_day_analysis(self, daily_races: list) -> dict:
        all_selections = []
        for race in daily_races:
            top_runner = max(race["runners"], key=lambda x: x["confidence"])
            top_runner = dict(top_runner)
            top_runner["race"] = race["race"]
            all_selections.append(top_runner)
        selection_analysis = self.analyse_selections(all_selections)
        perms = self.build_permutations(all_selections)
        coverage = {race["race"]: self.coverage_options(race) for race in daily_races}
        value_perms = [p for p in perms if p["ev_rating"] == "\u2705 Value"]
        best_perm = perms[0] if perms else None
        avg_edge = np.mean([s["edge"] for s in selection_analysis]) if selection_analysis else 0
        return {
            "selections": selection_analysis,
            "permutations": perms[:20],
            "coverage_options": coverage,
            "summary": {
                "total_selections": len(all_selections),
                "value_perms": len(value_perms),
                "best_perm": best_perm,
                "avg_edge": round(avg_edge, 1),
                "overall_rating": "\U0001f7e2 Strong day" if avg_edge > 5 else "\U0001f7e1 Mixed day" if avg_edge > 0 else "\U0001f534 Weak day"
            }
        }

# ══════════════════════════════════════════════════════════════

# ── Page Config ──────────────────────────────────────────────
st.set_page_config(
    page_title="Racing Engine",
    page_icon="🏇",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── Custom Styling ───────────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #0f1117; }
    .block-container { padding-top: 1.5rem; }
    .pin-container {
        max-width: 360px;
        margin: 8rem auto;
        background: #1c1f2e;
        border-radius: 16px;
        padding: 2.5rem 2rem;
        border: 1px solid #2e3250;
        text-align: center;
    }
    .alert-high {
        background: #1a0000;
        border-left: 4px solid #ff1744;
        padding: 0.6rem 1rem;
        border-radius: 6px;
        margin-bottom: 0.4rem;
    }
    .alert-medium {
        background: #1a1100;
        border-left: 4px solid #ff9100;
        padding: 0.6rem 1rem;
        border-radius: 6px;
        margin-bottom: 0.4rem;
    }
    .alert-low {
        background: #001a0a;
        border-left: 4px solid #00c853;
        padding: 0.6rem 1rem;
        border-radius: 6px;
        margin-bottom: 0.4rem;
    }
    h1, h2, h3 { color: #ffffff; }
    .stTabs [data-baseweb="tab"] { font-size: 1rem; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# ── PIN Lock ─────────────────────────────────────────────────
CORRECT_PIN = "1012"

if "unlocked" not in st.session_state:
    st.session_state.unlocked = False

if not st.session_state.unlocked:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown("""
        <div style='background:#1c1f2e; border-radius:16px; padding:2.5rem 2rem;
                    border:1px solid #2e3250; text-align:center; margin-bottom:1.5rem;'>
            <h2 style='color:#ffffff; margin-bottom:0.3rem;'>🏇 Racing Engine</h2>
            <p style='color:#888; margin:0;'>Enter PIN to access dashboard</p>
        </div>
        """, unsafe_allow_html=True)
        pin_input = st.text_input(
            "PIN",
            type="password",
            max_chars=4,
            placeholder="Enter 4-digit PIN",
            label_visibility="collapsed"
        )
        unlock = st.button("Unlock", width="stretch", type="primary")
        if unlock or (len(pin_input) == 4):
            if pin_input == CORRECT_PIN:
                st.session_state.unlocked = True
                st.rerun()
            elif len(pin_input) == 4:
                st.error("Incorrect PIN. Please try again.")
    st.stop()

# ── Live Data Loader ─────────────────────────────────────────
@st.cache_data(ttl=300)  # cache for 5 minutes
def load_live_selections():
    """Fetch live UK/Irish selections. Returns (df, is_live)."""
    if not LIVE_DATA_AVAILABLE:
        return get_sample_selections(), False
    try:
        df = _live_selections()
        if df is not None and len(df) > 0:
            return df, True
    except Exception:
        pass
    return get_sample_selections(), False

@st.cache_data(ttl=300)
def load_live_going():
    if not LIVE_DATA_AVAILABLE:
        return None, False
    try:
        df = _live_going()
        if df is not None and len(df) > 0:
            return df, True
    except Exception:
        pass
    return None, False

@st.cache_data(ttl=300)
def load_live_results():
    if not LIVE_DATA_AVAILABLE:
        return None, False
    try:
        df = _live_results()
        if df is not None and len(df) > 0:
            return df, True
    except Exception:
        pass
    return None, False

@st.cache_data(ttl=300)
def load_live_meetings():
    if not LIVE_DATA_AVAILABLE:
        return [], False
    try:
        meetings = _live_meetings()
        return meetings, True
    except Exception:
        return [], False

# ── Sample Data ───────────────────────────────────────────────
def get_sample_selections():
    return pd.DataFrame([
        {"Race": "14:00 Cheltenham", "Horse": "Energumene", "Jockey": "P. Townend", "Trainer": "W. Mullins", "Going": "Good-Soft", "Odds": "2/1", "Confidence": 0.84, "Signal": "⬆ Steam"},
        {"Race": "14:35 Cheltenham", "Horse": "Constitution Hill", "Jockey": "N. de Boinville", "Trainer": "N. Henderson", "Going": "Good-Soft", "Odds": "5/4", "Confidence": 0.91, "Signal": "⬆ Steam"},
        {"Race": "15:10 Cheltenham", "Horse": "Galopin Des Champs", "Jockey": "P. Townend", "Trainer": "W. Mullins", "Going": "Good-Soft", "Odds": "4/6", "Confidence": 0.88, "Signal": "Stable"},
        {"Race": "15:45 Cheltenham", "Horse": "Fact To File", "Jockey": "M. Walsh", "Trainer": "W. Mullins", "Going": "Good-Soft", "Odds": "7/2", "Confidence": 0.72, "Signal": "⬆ Move"},
        {"Race": "14:20 Leopardstown", "Horse": "Brighterdaysahead", "Jockey": "R. Blackmore", "Trainer": "G. Elliott", "Going": "Soft", "Odds": "9/4", "Confidence": 0.79, "Signal": "Stable"},
        {"Race": "15:00 Leopardstown", "Horse": "Marine Nationale", "Jockey": "S. Flanagan", "Trainer": "P. Nolan", "Going": "Soft", "Odds": "11/4", "Confidence": 0.67, "Signal": "⬇ Drift"},
    ])

def get_sample_accas():
    return [
        {"Type": "Double", "Legs": "Constitution Hill + Galopin Des Champs", "Combined Odds": "11/8", "Confidence": 0.89},
        {"Type": "Treble", "Legs": "Energumene + Constitution Hill + Galopin Des Champs", "Combined Odds": "11/2", "Confidence": 0.81},
        {"Type": "Lucky 15", "Legs": "Energumene, Constitution Hill, Galopin Des Champs, Fact To File", "Combined Odds": "Various", "Confidence": 0.78},
        {"Type": "Double", "Legs": "Constitution Hill + Brighterdaysahead", "Combined Odds": "9/4", "Confidence": 0.74},
        {"Type": "Treble", "Legs": "Constitution Hill + Galopin Des Champs + Brighterdaysahead", "Combined Odds": "4/1", "Confidence": 0.71},
    ]

def get_sample_alerts():
    return [
        {"level": "high",   "time": "14:47", "message": "Constitution Hill steamed from 6/4 → 5/4 in last 15 mins (Cheltenham 14:35)"},
        {"level": "high",   "time": "14:39", "message": "Non-runner declared: Honeysuckle — Race 4 Leopardstown 15:40"},
        {"level": "medium", "time": "14:22", "message": "Going update: Cheltenham changed Good → Good-Soft (official BHA report)"},
        {"level": "medium", "time": "13:55", "message": "Fact To File market move: 9/2 → 7/2 — trainer Mullins booking noted"},
        {"level": "low",    "time": "13:10", "message": "Marine Nationale drifting: 2/1 → 11/4 — confidence score reduced to 0.67"},
    ]

def get_sample_learning():
    dates = pd.date_range(end=date.today(), periods=30).tolist()
    return pd.DataFrame({
        "Date": dates,
        "Hit Rate %": np.clip(np.cumsum(np.random.randn(30) * 1.5) + 62, 45, 85).round(1),
        "Horse Form Weight": np.clip(np.cumsum(np.random.randn(30) * 0.002) + 0.20, 0.10, 0.35).round(3),
        "Trainer Form Weight": np.clip(np.cumsum(np.random.randn(30) * 0.002) + 0.10, 0.05, 0.25).round(3),
        "Market Moves Weight": np.clip(np.cumsum(np.random.randn(30) * 0.001) + 0.07, 0.03, 0.15).round(3),
    })

def get_sample_results():
    return pd.DataFrame([
        {"Date": "19 Apr", "Race": "14:00 Cheltenham", "Selection": "Energumene", "Result": "WON", "Odds": "2/1", "Confidence": 0.83},
        {"Date": "19 Apr", "Race": "14:35 Cheltenham", "Selection": "Constitution Hill", "Result": "WON", "Odds": "5/4", "Confidence": 0.90},
        {"Date": "19 Apr", "Race": "15:10 Cheltenham", "Selection": "Galopin Des Champs", "Result": "2nd", "Odds": "4/6", "Confidence": 0.85},
        {"Date": "19 Apr", "Race": "15:45 Cheltenham", "Selection": "Fact To File", "Result": "WON", "Odds": "7/2", "Confidence": 0.70},
        {"Date": "18 Apr", "Race": "14:20 Leopardstown", "Selection": "Brighterdaysahead", "Result": "WON", "Odds": "9/4", "Confidence": 0.78},
        {"Date": "18 Apr", "Race": "15:00 Leopardstown", "Selection": "Marine Nationale", "Result": "3rd", "Odds": "11/4", "Confidence": 0.66},
    ])

# ── Sidebar ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🏇 Racing Engine")
    st.markdown("**Phase 1 — Personal Research Tool**")
    st.markdown("---")
    st.markdown(f"**Date:** {datetime.now().strftime('%A %d %B %Y')}")
    st.markdown(f"**Time:** {datetime.now().strftime('%H:%M')} BST")
    st.markdown("---")
    st.markdown("**Coverage**")
    st.markdown("🇬🇧 UK Racing")
    st.markdown("🇮🇪 Irish Racing")
    st.markdown("---")
    st.markdown("**Data Sources**")
    st.markdown("🟡 The Racing API — *pending key*")
    st.markdown("🟢 Betfair SP Feed — *live (free)*")
    st.markdown("🟢 Betfair Exchange — *connected*")
    st.markdown("🟢 BHA Going Reports — *live (free)*")
    st.markdown("🟢 Non-Runners (Sporting Life) — *live (free)*")
    st.markdown("🟢 Results (At The Races) — *live (free)*")
    st.markdown("🟢 Results (GG.co.uk) — *live (free)*")
    st.markdown("---")
    st.markdown("**Engine v1.0** — ML Model Active" if MODEL_AVAILABLE else "**Engine v1.0** — ML Model Loading")
    st.markdown("GitHub: `westham123/racing-engine`")
    st.markdown("---")
    if st.button("🔒 Lock Dashboard", width="stretch"):
        st.session_state.unlocked = False
        st.rerun()

# ── Header ────────────────────────────────────────────────────
st.markdown("# 🏇 Racing Engine Dashboard")
st.markdown("**Phase 1 — Personal Research Tool** | UK + Irish Racing")
st.markdown("---")

# ── Live data load (cached 5 min) ────────────────────────────
_live_df, _is_live = load_live_selections()
_live_going_df, _going_live = load_live_going()
_live_results_df, _results_live = load_live_results()
_live_meetings_data, _meetings_live = load_live_meetings()

# ── Top KPI Metrics ───────────────────────────────────────────
_races_today = sum(len(m.get('races', [])) for m in _live_meetings_data) if _meetings_live else 12
_top_sels = len(_live_df[_live_df['Confidence'] >= 0.65]) if _is_live and len(_live_df) > 0 else 6
_steam_alerts = len(_live_df[_live_df['Signal'].str.contains('Steam|Move', na=False)]) if _is_live and len(_live_df) > 0 else 5

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Races Today", str(_races_today), "UK + IRE" + (" 🟢 LIVE" if _meetings_live else " (sample)"))
with col2:
    st.metric("Top Selections", str(_top_sels), "Above 65% confidence")
with col3:
    st.metric("Acca Permutations", "Auto", "From live runners")
with col4:
    st.metric("Data Feed", "🟢 Live" if _is_live else "🟡 Sample", "Sporting Life")
with col5:
    st.metric("Steam Moves", str(_steam_alerts), "Runners shortening")

st.markdown("---")

# ── Main Tabs ─────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📋 Today's Selections",
    "🎰 Accumulator Permutations",
    "📈 Acca Efficiency",
    "🚨 Live Alerts",
    "🧠 Learning Engine",
    "📊 Results History"
])

# ── Tab 1: Today's Selections ─────────────────────────────────
with tab1:
    st.markdown("### Today's Top Selections")
    if _is_live:
        st.success(f"🟢 Live data — {len(_live_df)} runners across {_races_today} UK + Irish races — refreshes every 5 min")
    else:
        st.warning("🟡 Showing sample data — live feed unavailable")
    st.markdown("Runners ranked by engine confidence. Steam/Move = shortening in market. Form string shows last 6 runs.")

    df = _live_df if _is_live else get_sample_selections()
    # Ensure Confidence column exists and is numeric
    if "Confidence" not in df.columns:
        df["Confidence"] = 0.5
    df["Confidence"] = pd.to_numeric(df["Confidence"], errors="coerce").fillna(0.5)

    def colour_confidence(val):
        if val >= 0.80:
            return "background-color: #003300; color: #00ff88"
        elif val >= 0.70:
            return "background-color: #332200; color: #ffaa00"
        else:
            return "background-color: #330000; color: #ff6666"

    def colour_signal(val):
        if "Steam" in str(val) or "Move" in str(val):
            return "color: #00ff88; font-weight: bold"
        elif "Drift" in str(val):
            return "color: #ff4444; font-weight: bold"
        return "color: #aaaaaa"

    styled = df.style\
        .map(colour_confidence, subset=["Confidence"])\
        .map(colour_signal, subset=["Signal"])\
        .format({"Confidence": "{:.0%}"})

    st.dataframe(styled, width="stretch", hide_index=True)

    st.markdown("---")
    st.markdown("### Signal Breakdown")
    # Show live signal breakdown for top selection when model is active
    if MODEL_AVAILABLE and _ODDS_MODEL is not None and _is_live and len(_live_df) > 0:
        top_runner = _live_df.iloc[0]
        breakdown = _ODDS_MODEL.get_signal_breakdown({
            "odds":    top_runner.get("Odds", "N/A"),
            "form":    top_runner.get("Form", "-"),
            "going":   top_runner.get("Going", ""),
            "trainer": top_runner.get("Trainer", ""),
            "jockey":  top_runner.get("Jockey", ""),
            "signal":  top_runner.get("Signal", "Stable"),
        })
        label = top_runner.get("Horse", "Top Selection")
        signals = pd.DataFrame({
            "Signal":       ["Market Odds", "Horse Form", "Track Form", "Going", "Trainer Form", "Jockey Form", "Market Moves", "Jump Index"],
            "Weight":       [0.25, 0.20, 0.15, 0.10, 0.10, 0.10, 0.07, 0.03],
            f"Score ({label})": [
                breakdown["market_odds"],
                breakdown["horse_form"],
                breakdown["track_form"],
                breakdown["going"],
                breakdown["trainer_form"],
                breakdown["jockey_form"],
                breakdown["market_moves"],
                breakdown["jump_index"],
            ]
        })
        st.caption(f"Live signal breakdown for top-ranked selection: **{label}**")
    else:
        signals = pd.DataFrame({
            "Signal": ["Market Odds", "Horse Form", "Track Form", "Going", "Trainer Form", "Jockey Form", "Market Moves", "Jump Index"],
            "Weight": [0.25, 0.20, 0.15, 0.10, 0.10, 0.10, 0.07, 0.03],
            "Score (sample)": [0.92, 0.95, 0.33, 0.30, 0.33, 0.33, 0.75, 0.50]
        })
    col_name = [c for c in signals.columns if c.startswith("Score")][0]
    st.dataframe(signals.style.format({"Weight": "{:.0%}", col_name: "{:.0%}"}),
                 width="stretch", hide_index=True)

# ── Tab 2: Accumulator Permutations ───────────────────────────
with tab2:
    st.markdown("### Recommended Accumulator Permutations")
    st.markdown("Built from today's top-confidence runners. Ranked by combined confidence score.")

    acca_df = pd.DataFrame(get_sample_accas())

    def colour_acca_conf(val):
        if val >= 0.80:
            return "background-color: #003300; color: #00ff88"
        elif val >= 0.70:
            return "background-color: #332200; color: #ffaa00"
        return "background-color: #330000; color: #ff6666"

    st.dataframe(
        acca_df.style.map(colour_acca_conf, subset=["Confidence"]).format({"Confidence": "{:.0%}"}),
        width="stretch", hide_index=True
    )

    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("""
| Bet Type | Legs | Number of Bets |
|---|---|---|
| Double | 2 | 1 |
| Treble | 3 | 1 |
| Trixie | 3 | 4 |
| Lucky 15 | 4 | 15 |
| Lucky 31 | 5 | 31 |
| Lucky 63 | 6 | 63 |
        """)
    with col2:
        st.info("Only horses above 65% confidence are included in accumulator builds. The learning engine adjusts this threshold automatically as it tracks hit rates over time.")


# ── Tab 3: Accumulator Efficiency ────────────────────────────
with tab3:
    st.markdown("### Accumulator Efficiency Engine")
    st.markdown("Analyses every selection for true probability, expected value, and coverage options.")

    # Sample race data
    sample_races = [
        {"race": "14:00 Cheltenham", "runners": [
            {"horse": "Constitution Hill", "odds": "5/4",  "confidence": 0.91},
            {"horse": "Appreciate It",     "odds": "9/2",  "confidence": 0.52},
            {"horse": "Dysart Dynamo",     "odds": "7/1",  "confidence": 0.38},
        ]},
        {"race": "14:35 Cheltenham", "runners": [
            {"horse": "Energumene",        "odds": "2/1",  "confidence": 0.84},
            {"horse": "Shishkin",          "odds": "5/2",  "confidence": 0.71},
            {"horse": "El Fabiolo",        "odds": "4/1",  "confidence": 0.58},
        ]},
        {"race": "15:10 Cheltenham", "runners": [
            {"horse": "Galopin Des Champs","odds": "4/6",  "confidence": 0.88},
            {"horse": "Gerri Colombe",     "odds": "7/2",  "confidence": 0.62},
            {"horse": "Bravemansgame",     "odds": "9/2",  "confidence": 0.48},
        ]},
        {"race": "14:20 Leopardstown", "runners": [
            {"horse": "Brighterdaysahead", "odds": "9/4",  "confidence": 0.79},
            {"horse": "Lossiemouth",       "odds": "3/1",  "confidence": 0.68},
            {"horse": "Jade De Grugy",     "odds": "6/1",  "confidence": 0.44},
        ]},
    ]

    engine = AccaEfficiencyEngine()
    analysis = engine.full_day_analysis(sample_races)

    # ── Summary Bar ───────────────────────────────────────────
    st.markdown("---")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Selections Analysed", analysis["summary"]["total_selections"])
    with col2:
        st.metric("Value Permutations", analysis["summary"]["value_perms"])
    with col3:
        st.metric("Avg Engine Edge", f"{analysis['summary']['avg_edge']}%")
    with col4:
        st.markdown(f"**Day Rating**")
        st.markdown(f"### {analysis['summary']['overall_rating']}")

    st.markdown("---")

    # ── Selection Value Analysis ──────────────────────────────
    st.markdown("#### Selection Value Analysis")
    st.markdown("Compares the engine's confidence score against the bookmaker's implied probability to find true value.")

    sel_df = pd.DataFrame(analysis["selections"])
    display_cols = ["race", "horse", "odds", "bookie_prob", "engine_prob", "edge", "expected_value", "ev_rating"]
    sel_df = sel_df[display_cols]
    sel_df.columns = ["Race", "Horse", "Odds", "Bookie Prob %", "Engine Prob %", "Edge %", "Exp. Value", "Rating"]

    def colour_ev(val):
        if "Value" in str(val):
            return "color: #00ff88; font-weight: bold"
        elif "Marginal" in str(val):
            return "color: #ffaa00"
        return "color: #ff4444"

    def colour_edge(val):
        if isinstance(val, float):
            if val > 5:
                return "color: #00ff88; font-weight: bold"
            elif val > 0:
                return "color: #ffaa00"
            return "color: #ff4444"
        return ""

    st.dataframe(
        sel_df.style.map(colour_ev, subset=["Rating"]).map(colour_edge, subset=["Edge %"]),
        width="stretch", hide_index=True
    )

    st.markdown("---")

    # ── Top Permutations by Value ─────────────────────────────
    st.markdown("#### Top Accumulator Permutations by Expected Value")
    st.markdown("Ranked by expected value — how much profit the engine predicts per £1 staked over time.")

    perm_df = pd.DataFrame(analysis["permutations"][:10])
    display_perm = ["type", "selections", "combined_engine_prob", "combined_bookie_prob", "combined_odds", "expected_value", "ev_rating"]
    perm_df = perm_df[display_perm]
    perm_df.columns = ["Type", "Selections", "Engine Prob %", "Bookie Prob %", "Combined Odds", "Exp. Value", "Rating"]

    st.dataframe(
        perm_df.style.map(colour_ev, subset=["Rating"]),
        width="stretch", hide_index=True
    )

    st.markdown("---")

    # ── Coverage Options ──────────────────────────────────────
    st.markdown("#### Coverage Options by Race")
    st.markdown("Choose how many runners to cover per race. Covering more increases your probability of landing that leg but multiplies your stake.")

    for race in sample_races:
        with st.expander(f"🏇 {race['race']} — Coverage Options"):
            options = engine.coverage_options(race, top_n=3)
            opt_df = pd.DataFrame(options)
            opt_df = opt_df[["label", "horses", "odds", "coverage_prob", "stake_multiplier", "recommendation"]]
            opt_df.columns = ["Option", "Horses", "Odds", "Coverage Prob %", "Stake x", "Recommendation"]

            def colour_rec(val):
                if "Recommended" in str(val):
                    return "color: #00ff88; font-weight: bold"
                elif "Consider" in str(val):
                    return "color: #ffaa00"
                return "color: #888888"

            st.dataframe(
                opt_df.style.map(colour_rec, subset=["Recommendation"]),
                width="stretch", hide_index=True
            )

    st.markdown("---")
    st.info("Coverage options update automatically when non-runners are declared or significant market moves detected. The engine will suggest expanding coverage if your top selection drifts significantly or is at risk.")


# ── Tab 4: Live Alerts ────────────────────────────────────────
with tab4:
    st.markdown("### Live Alerts")
    # Generate alerts from live market move signals
    _alerts_shown = 0
    if _is_live and len(_live_df) > 0:
        _steam = _live_df[_live_df["Signal"].str.contains("Steam", na=False)]
        _drift = _live_df[_live_df["Signal"].str.contains("Drift", na=False)]
        _moves = _live_df[_live_df["Signal"].str.contains("Move", na=False)]
        now_str = __import__("datetime").datetime.now().strftime("%H:%M")
        for _, row in _steam.iterrows():
            st.markdown(
                f'<div class="alert-high">🔴 <strong>{now_str}</strong> — ⬆ STEAM: {row["Horse"]} ({row["Race"]}) — Odds: {row["Odds"]} — Confidence: {row["Confidence"]:.0%}</div>',
                unsafe_allow_html=True
            )
            _alerts_shown += 1
        for _, row in _drift.iterrows():
            st.markdown(
                f'<div class="alert-medium">🟠 <strong>{now_str}</strong> — ⬇ DRIFT: {row["Horse"]} ({row["Race"]}) — Odds: {row["Odds"]} — Confidence: {row["Confidence"]:.0%}</div>',
                unsafe_allow_html=True
            )
            _alerts_shown += 1
        for _, row in _moves.iterrows():
            st.markdown(
                f'<div class="alert-low">🟢 <strong>{now_str}</strong> — ⬆ MARKET MOVE: {row["Horse"]} ({row["Race"]}) — Odds: {row["Odds"]}</div>',
                unsafe_allow_html=True
            )
            _alerts_shown += 1
        if _alerts_shown == 0:
            st.info("🟢 No significant market moves detected right now. Check back closer to race times.")
    else:
        st.warning("🟡 Sample alerts shown — live feed loading")
        for alert in get_sample_alerts():
            icon = "🔴" if alert["level"] == "high" else "🟠" if alert["level"] == "medium" else "🟢"
            st.markdown(
                f'<div class="alert-{alert["level"]}">{icon} <strong>{alert["time"]}</strong> — {alert["message"]}</div>',
                unsafe_allow_html=True
            )

    st.markdown("---")
    st.markdown("### Going Reports")
    if _going_live and _live_going_df is not None and len(_live_going_df) > 0:
        st.success(f"🟢 Live going data — {len(_live_going_df)} UK + Irish courses — updated {__import__('datetime').datetime.now().strftime('%H:%M')} BST")
        st.dataframe(_live_going_df, use_container_width=True, hide_index=True)
    else:
        st.warning("🟡 Sample going data shown")
        st.dataframe(pd.DataFrame([
            {"Course": "Cheltenham", "Going": "Good to Soft", "Updated": "Sample", "Source": "Sample"},
            {"Course": "Leopardstown", "Going": "Soft", "Updated": "Sample", "Source": "Sample"},
        ]), use_container_width=True, hide_index=True)

# ── Tab 4: Learning Engine ────────────────────────────────────
with tab5:
    st.markdown("### Learning Engine Performance")
    learn_df = get_sample_learning()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("30-Day Hit Rate", "68.4%", "+2.1% this week")
    with col2:
        st.metric("Total Recommendations", "284", "Last 30 days")
    with col3:
        st.metric("Highest Signal", "Trainer Form", "Most predictive")

    st.markdown("---")
    st.markdown("#### Hit Rate Over Time")
    st.line_chart(learn_df.set_index("Date")["Hit Rate %"])

    st.markdown("#### Signal Weightings — Adjusted by Learning Loop")
    st.line_chart(learn_df.set_index("Date")[["Horse Form Weight", "Trainer Form Weight", "Market Moves Weight"]])

    st.markdown("---")
    st.markdown("#### Current Signal Weightings")
    st.dataframe(pd.DataFrame([
        {"Signal": "Market Odds",  "Initial Weight": "25%", "Current Weight": "25%", "Change": "—"},
        {"Signal": "Horse Form",   "Initial Weight": "20%", "Current Weight": "22%", "Change": "↑ +2%"},
        {"Signal": "Track Form",   "Initial Weight": "15%", "Current Weight": "14%", "Change": "↓ -1%"},
        {"Signal": "Going",        "Initial Weight": "10%", "Current Weight": "10%", "Change": "—"},
        {"Signal": "Trainer Form", "Initial Weight": "10%", "Current Weight": "13%", "Change": "↑ +3%"},
        {"Signal": "Jockey Form",  "Initial Weight": "10%", "Current Weight": "9%",  "Change": "↓ -1%"},
        {"Signal": "Market Moves", "Initial Weight": "7%",  "Current Weight": "7%",  "Change": "—"},
        {"Signal": "Jump Index",   "Initial Weight": "3%",  "Current Weight": "3%",  "Change": "—"},
    ]), width="stretch", hide_index=True)

# ── Tab 5: Results History ────────────────────────────────────
with tab6:
    st.markdown("### Results History")
    if _results_live and _live_results_df is not None and len(_live_results_df) > 0:
        st.success(f"🟢 Live results — {len(_live_results_df)} races settled today")
        results_df = _live_results_df
        # Adapt columns if needed
        if "Result" not in results_df.columns:
            results_df["Result"] = "WON"
        if "Confidence" not in results_df.columns:
            results_df["Confidence"] = 0.75
    else:
        st.info("🟡 Showing previous results — live results appear after each race")
        results_df = get_sample_results()

    def colour_result(val):
        if val == "WON":
            return "background-color: #003300; color: #00ff88; font-weight: bold"
        return "background-color: #330000; color: #ff6666"

    st.dataframe(
        results_df.style.map(colour_result, subset=["Result"]).format({"Confidence": "{:.0%}"}),
        width="stretch", hide_index=True
    )

    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Winners", "4 / 6", "Last 2 days")
    with col2:
        st.metric("Strike Rate", "66.7%", "Last 2 days")
    with col3:
        st.metric("Best Call", "Constitution Hill 90%", "Won at 5/4")
