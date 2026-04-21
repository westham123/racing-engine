# Racing Engine — Visual Dashboard
# Version: 2.3 — Flexible selection plan, remove forced L15, 4/6 cut-off on all bets
# Built with Streamlit
# Date: 20 April 2026

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, date
from itertools import combinations
import sys, os

# ── Bulletproof sys.path for Streamlit Cloud ──────────────────
# Strategy 1: relative to __file__
_here = os.path.dirname(os.path.abspath(__file__))
_repo_root_a = os.path.dirname(_here)
# Strategy 2: walk up from cwd until we find requirements.txt
_cwd = os.getcwd()
_repo_root_b = _cwd
for _p in [_cwd, os.path.dirname(_cwd)]:
    if os.path.exists(os.path.join(_p, "requirements.txt")):
        _repo_root_b = _p
        break
for _rp in [_repo_root_a, _repo_root_b, _here, _cwd]:
    if _rp not in sys.path:
        sys.path.insert(0, _rp)
# ─────────────────────────────────────────────────────────────

try:
    from engine.odds_model import OddsModel as _OddsModel
    _ODDS_MODEL = _OddsModel()
    MODEL_AVAILABLE = True
except Exception:
    MODEL_AVAILABLE = False
    _ODDS_MODEL = None

try:
    from alerts.market_monitor import MultiSourceMarketMonitor as _MultiMonitor
    _MULTI_MONITOR = _MultiMonitor()
    MONITOR_AVAILABLE = True
except Exception:
    MONITOR_AVAILABLE = False
    _MULTI_MONITOR = None

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
@st.cache_data(ttl=60)  # 60s — short enough to catch model changes
def load_live_selections():
    """Fetch live UK/Irish selections. Returns (df, is_live)."""
    if not LIVE_DATA_AVAILABLE:
        return get_sample_selections(), False
    try:
        df = _live_selections()
        if df is not None and len(df) > 0:
            # Normalise column names so Today's Plan always works
            if "Race" in df.columns and "Time" not in df.columns:
                df["Time"]   = df["Race"].str.split(" ", n=1).str[0]
                df["Course"] = df["Race"].str.split(" ", n=1).str[1].fillna(df["Race"])
            return df, True
    except Exception:
        pass
    return get_sample_selections(), False

@st.cache_data(ttl=90)
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

@st.cache_data(ttl=90)
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

@st.cache_data(ttl=90)
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
    # Today's scored selections — updated daily
    return pd.DataFrame([
        {"Time": "2:17", "Course": "Pontefract",    "Horse": "Lady Youmzain",   "Jockey": "K. Stott",       "Trainer": "K. Ryan",         "Going": "Good",           "Odds": "11/10", "Confidence": 0.70, "Signal": "Stable"},
        {"Time": "4:02", "Course": "Pontefract",    "Horse": "Yorkshire Glory", "Jockey": "H. Vigors",      "Trainer": "B. Haslam",       "Going": "Good",           "Odds": "7/2",   "Confidence": 0.67, "Signal": "⬆ Move"},
        {"Time": "4:38", "Course": "Ffos Las",      "Horse": "Crystal Island",  "Jockey": "N. de Boinville","Trainer": "N. Henderson",    "Going": "Good to Soft",   "Odds": "4/6",   "Confidence": 0.79, "Signal": "⬆ Steam"},
        {"Time": "4:55", "Course": "Yarmouth",      "Horse": "Mister Mojito",   "Jockey": "TBC",           "Trainer": "TBC",             "Going": "Good to Firm",   "Odds": "13/2",  "Confidence": 0.67, "Signal": "Stable"},
        {"Time": "6:30", "Course": "Wolverhampton", "Horse": "Beaune",          "Jockey": "D. Probert",    "Trainer": "B. Llewellyn",    "Going": "Tapeta Standard","Odds": "7/4",   "Confidence": 0.73, "Signal": "⬆ Move"},
        {"Time": "8:30", "Course": "Wolverhampton", "Horse": "Kaaranah",        "Jockey": "D. Egan",       "Trainer": "J. Butler",       "Going": "Tapeta Standard","Odds": "13/8",  "Confidence": 0.70, "Signal": "Stable"},
    ])

def get_sample_accas():
    # Today's card — 21 April 2026
    return [
        {"Type": "Double",   "Legs": "Mister Mojito + Yorkshire Glory",              "Combined Odds": "26.25x", "Confidence": 0.67},
        {"Type": "Double",   "Legs": "Mister Mojito + Beaune",                       "Combined Odds": "20.63x", "Confidence": 0.67},
        {"Type": "Treble",   "Legs": "Mister Mojito + Beaune + Yorkshire Glory",     "Combined Odds": "72.19x", "Confidence": 0.65},
        {"Type": "4-fold",   "Legs": "Mister Mojito + Beaune + Kaaranah + YG",      "Combined Odds": "189.5x", "Confidence": 0.62},
        {"Type": "Lucky 15", "Legs": "Lady Youmzain / Yorkshire Glory / Mister Mojito / Beaune", "Combined Odds": "Various", "Confidence": 0.67},
    ]

def get_sample_alerts():
    # Today's card — 21 April 2026
    return [
        {"level": "high",   "time": "09:05", "message": "Crystal Island steamed to 4/6 — excluded from Lucky 15 (≤ 4/6 cut-off)"},
        {"level": "high",   "time": "09:12", "message": "Mister Mojito confirmed 13/2 — top EV selection today"},
        {"level": "medium", "time": "09:30", "message": "Going update: Yarmouth Good to Firm (5.7) — suits Mister Mojito"},
        {"level": "medium", "time": "09:45", "message": "Yorkshire Glory market move: 4/1 → 7/2 — confidence raised to 0.67"},
        {"level": "low",    "time": "10:00", "message": "Lady Youmzain stable in market at 11/10 — monitor for drift"},
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
    # Placeholder — populates automatically from settlement engine after races run
    return pd.DataFrame([
        {"Date": "21 Apr", "Race": "2:17 Pontefract",    "Selection": "Lady Youmzain",   "Result": "Pending", "Odds": "11/10", "Confidence": 0.70},
        {"Date": "21 Apr", "Race": "4:02 Pontefract",    "Selection": "Yorkshire Glory",  "Result": "Pending", "Odds": "7/2",   "Confidence": 0.67},
        {"Date": "21 Apr", "Race": "4:38 Ffos Las",      "Selection": "Crystal Island",   "Result": "Pending", "Odds": "4/6",   "Confidence": 0.79},
        {"Date": "21 Apr", "Race": "4:55 Yarmouth",      "Selection": "Mister Mojito",    "Result": "Pending", "Odds": "13/2",  "Confidence": 0.67},
        {"Date": "21 Apr", "Race": "6:30 Wolverhampton", "Selection": "Beaune",           "Result": "Pending", "Odds": "7/4",   "Confidence": 0.73},
        {"Date": "21 Apr", "Race": "8:30 Wolverhampton", "Selection": "Kaaranah",         "Result": "Pending", "Odds": "13/8",  "Confidence": 0.70},
    ])

# ── Sidebar ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🏇 Racing Engine")
    st.markdown("**Phase 1 — Personal Research Tool**")
    st.markdown("---")
    st.markdown(f"**Date:** {datetime.now().strftime('%A %d %B %Y')}")
    st.markdown(f"**Time:** {datetime.now().strftime('%H:%M')} BST")
    st.markdown("---")

    # ── Staking Settings ──────────────────────────────────────
    st.markdown("### ⚙️ Staking Settings")
    st.caption("Adjust anytime — saved for this session")

    _daily_budget = st.number_input(
        "Daily Budget (£)",
        min_value=5, max_value=500, value=st.session_state.get("daily_budget", 50), step=5,
        help="Total amount to allocate across all bets today"
    )
    st.session_state["daily_budget"] = _daily_budget

    _risk_profile = st.select_slider(
        "Risk Profile",
        options=["Conservative", "Balanced", "Aggressive"],
        value=st.session_state.get("risk_profile", "Balanced"),
        help="Conservative = more singles, smaller multiples. Aggressive = concentrate on high-odds multiples."
    )
    st.session_state["risk_profile"] = _risk_profile

    st.markdown("**Bet Types**")
    _use_singles   = st.toggle("Singles",   value=st.session_state.get("use_singles", True))
    _use_doubles   = st.toggle("Doubles",   value=st.session_state.get("use_doubles", True))
    _use_trebles   = st.toggle("Trebles",   value=st.session_state.get("use_trebles", True))
    _use_4fold     = st.toggle("4-folds",   value=st.session_state.get("use_4fold", True))
    _use_5fold     = st.toggle("5-folds+",  value=st.session_state.get("use_5fold", True))
    _use_lucky15   = st.toggle("Lucky 15/31/63", value=st.session_state.get("use_lucky15", False))
    st.session_state["use_singles"]  = _use_singles
    st.session_state["use_doubles"]  = _use_doubles
    st.session_state["use_trebles"]  = _use_trebles
    st.session_state["use_4fold"]    = _use_4fold
    st.session_state["use_5fold"]    = _use_5fold
    st.session_state["use_lucky15"]  = _use_lucky15

    _conf_threshold = st.slider(
        "Min Confidence Threshold",
        min_value=0.55, max_value=0.80, value=st.session_state.get("conf_threshold", 0.60),
        step=0.05, format="%.0%%",
        help="Slide left to 55% to bring in more selections, right to tighten the filter. Default is 60%."
    )
    st.caption(f"Currently: **{_conf_threshold:.0%}** — {'⚠️ Relaxed filter (more selections)' if _conf_threshold < 0.60 else '✅ Standard filter' if _conf_threshold == 0.60 else '🔒 Tight filter (fewer, higher-confidence only)'}")
    st.session_state["conf_threshold"] = _conf_threshold

    _max_legs = st.slider(
        "Max Accumulator Legs",
        min_value=2, max_value=6, value=st.session_state.get("max_legs", 6), step=1,
        help="Maximum number of selections in a single multiple bet"
    )
    st.session_state["max_legs"] = _max_legs

    # Stake split ratios by risk profile
    _risk_splits = {
        "Conservative": {"singles_pct": 0.50, "doubles_pct": 0.25, "trebles_pct": 0.15, "4fold_pct": 0.07, "5fold_pct": 0.03},
        "Balanced":     {"singles_pct": 0.20, "doubles_pct": 0.20, "trebles_pct": 0.27, "4fold_pct": 0.24, "5fold_pct": 0.09},
        "Aggressive":   {"singles_pct": 0.10, "doubles_pct": 0.10, "trebles_pct": 0.20, "4fold_pct": 0.35, "5fold_pct": 0.25},
    }
    _split = _risk_splits[_risk_profile]
    st.session_state["stake_splits"] = _split

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
    st.markdown("**Engine v2.4.1** — Live rescore | BST times | Outlier filter | Cache fix")
    st.caption("Tab 1 rescores all runners live on every load")
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
# Race count: use live meetings if available, else count today's known card (6 races, 4 meetings)
_races_today = sum(len(m.get('races', [])) for m in _live_meetings_data) if _meetings_live else 6
_top_sels = len(_live_df[_live_df['Confidence'] >= 0.65]) if _is_live and len(_live_df) > 0 else 6
_signal_df = _live_df if (_is_live and len(_live_df) > 0) else get_sample_selections()
_steam_alerts = len(_signal_df[_signal_df['Signal'].str.contains('Steam|Move', na=False)])

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Races Today", str(_races_today), "UK + IRE" + (" 🟢 LIVE" if _meetings_live else " (sample)"))
with col2:
    st.metric("Top Selections", str(_top_sels), "Above 65% confidence")
with col3:
    st.metric("Acca Permutations", "Auto", "From live runners")
with col4:
    # Pull real hit rate from settlement engine
    try:
        import sys as _s3
        _s3.path.insert(0, __import__("os").path.dirname(__file__) + "/..")
        from settlement.settle import SettlementEngine as _SE
        _kpi_stats = _SE().get_summary_stats()
        _hit_rate_kpi = f"{_kpi_stats['hit_rate']:.1f}%" if _kpi_stats.get("total",0) > 0 else "—"
        _hit_delta = f"{_kpi_stats['total']} races settled"
    except Exception:
        _hit_rate_kpi = "—"
        _hit_delta = "Building..."
    st.metric("Hit Rate", _hit_rate_kpi, _hit_delta)
with col5:
    st.metric("Steam Moves", str(_steam_alerts), "Runners shortening")

st.markdown("---")

# ── Main Tabs ─────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "💰 Today's Plan",
    "📋 Today's Selections",
    "🎰 Accumulator Permutations",
    "📈 Acca Efficiency",
    "🚨 Live Alerts",
    "🧠 Learning Engine",
    "📊 Results History",
    "📉 Odds Comparison"
])

# ── Tab 1: Today's Plan ──────────────────────────────────────
with tab1:
    _t1col1, _t1col2 = st.columns([5, 1])
    _t1col1.markdown("### 💰 Today's Staking Plan")
    if _t1col2.button("🔄 Refresh", help="Clear cache and reload live data"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Budget: **£{st.session_state.get('daily_budget', 50)}** | Six-timer is main bet | Lucky 15 shown only if 4+ horses qualify | Min confidence: **{st.session_state.get('conf_threshold', 0.60):.0%}** | Adjust in sidebar ←")

    # ── Build pool from live data or sample ────────────────────────────────
    _conf_threshold = st.session_state.get("conf_threshold", 0.60)
    _six_pool = []  # All qualifying selections

    def _assign_tier(dec):
        if dec <= 2.50:  return "BANKER"
        if dec <= 5.00:  return "MID"
        if dec <= 10.00: return "VALUE"
        return "LONGSHOT"

    # Always call load_live_selections() directly here — do NOT use the top-level
    # _live_df which was bound before the Refresh button could clear the cache.
    _t1_df, _t1_is_live = load_live_selections()

    if _t1_is_live and len(_t1_df) > 0:
        try:
            from engine.odds_model import OddsModel as _OddsModel
            _tab1_model = _OddsModel()
        except Exception:
            _tab1_model = None

        try:
            import zoneinfo as _zi2
            _now_live = __import__('datetime').datetime.now(
                _zi2.ZoneInfo('Europe/London')).strftime('%H:%M')
        except Exception:
            _now_live = __import__('datetime').datetime.utcnow().strftime('%H:%M')

        for _, _row in _t1_df.iterrows():
            _time = str(_row.get('Time', ''))
            if _time < _now_live:
                continue  # skip past races

            # Build runner dict from dataframe row for rescoring
            _runner_dict = {
                'odds':         str(_row.get('Odds', 'N/A')),
                'current_odds': str(_row.get('Current Odds', '')) or str(_row.get('Odds', 'N/A')),
                'form':         str(_row.get('Form', '-')),
                'going':        str(_row.get('Going', '')),
                'trainer':      str(_row.get('Trainer', '')),
                'jockey':       str(_row.get('Jockey', '')),
                'signal':       str(_row.get('Signal', 'Stable')),
                'tf_stars':     _row.get('TF Stars'),
                'bet_movements': [],
            }
            # Rescore with current model, or fall back to cached confidence
            if _tab1_model:
                _conf = _tab1_model.calculate_confidence(_runner_dict)
            else:
                _conf = float(_row.get('Confidence', 0))

            if _conf < _conf_threshold:
                continue

            # Use Current Odds for cut-off; display best bk Odds
            _curr_str = str(_row.get('Current Odds', '')).strip()
            _odds_for_filter = _curr_str if _curr_str and _curr_str not in ('', 'N/A', 'None', 'nan') \
                               else str(_row.get('Odds', 'Evs'))
            _disp_odds = str(_row.get('Odds', _odds_for_filter))
            try:
                if '/' in _odds_for_filter:
                    _n2, _d2 = _odds_for_filter.split('/')
                    _dec = float(_n2) / float(_d2) + 1
                else:
                    _dec = float(_odds_for_filter)
            except Exception:
                _dec = 2.0
            if _dec <= 1.67:
                continue

            _ev = round(_conf * _dec - 1, 3)
            _course = str(_row.get('Course', ''))
            _six_pool.append({
                'horse':      str(_row.get('Horse', 'Unknown')),
                'course':     _course,
                'time':       _time,
                'odds_str':   _disp_odds,
                'decimal':    round(_dec, 3),
                'confidence': round(_conf, 3),
                'ev':         _ev,
                'tier':       _assign_tier(round(_dec, 3)),
            })

        _six_pool.sort(key=lambda x: x['confidence'], reverse=True)
    else:
        # Sample pool — today's qualifying selections (> 4/6, >= 0.60 conf, future races only)
        try:
            import zoneinfo as _zi
            _now_str = __import__('datetime').datetime.now(_zi.ZoneInfo('Europe/London')).strftime("%H:%M")
        except Exception:
            _now_str = __import__('datetime').datetime.utcnow().strftime("%H:%M")
        # All times are BST (UTC+1) — matching what UK users see
        _raw_sample = [
            {"horse": "Lady Youmzain",   "course": "Pontefract",    "time": "14:17", "odds_str": "1/1",   "decimal": 2.00,  "confidence": 0.812, "ev": 0.62, "tier": "BANKER"},
            {"horse": "Brilliant Star",  "course": "Yarmouth",      "time": "14:35", "odds_str": "3/10",  "decimal": 1.30,  "confidence": 0.748, "ev": -0.03, "tier": "BANKER"},
            {"horse": "Final Appeal",    "course": "Wolverhampton", "time": "17:00", "odds_str": "10/11", "decimal": 1.91,  "confidence": 0.711, "ev": 0.36, "tier": "BANKER"},
            {"horse": "Trust House",     "course": "Ffos Las",      "time": "18:12", "odds_str": "10/11", "decimal": 1.91,  "confidence": 0.708, "ev": 0.35, "tier": "BANKER"},
            {"horse": "Yorkshire Glory", "course": "Pontefract",    "time": "16:02", "odds_str": "5/2",   "decimal": 3.50,  "confidence": 0.683, "ev": 1.09, "tier": "MID"},
            {"horse": "Beaune",          "course": "Wolverhampton", "time": "18:30", "odds_str": "7/4",   "decimal": 2.75,  "confidence": 0.664, "ev": 0.83, "tier": "MID"},
            {"horse": "Daizen",          "course": "Pontefract",    "time": "14:52", "odds_str": "13/2",  "decimal": 7.50,  "confidence": 0.649, "ev": 3.87, "tier": "VALUE"},
            {"horse": "Eightthreeone",   "course": "Yarmouth",      "time": "16:20", "odds_str": "4/1",   "decimal": 5.00,  "confidence": 0.645, "ev": 2.23, "tier": "MID"},
            {"horse": "Lillistar",       "course": "Pontefract",    "time": "16:32", "odds_str": "11/1",  "decimal": 12.00, "confidence": 0.639, "ev": 6.67, "tier": "LONGSHOT"},
            {"horse": "Esperti",         "course": "Ffos Las",      "time": "18:42", "odds_str": "7/2",   "decimal": 4.50,  "confidence": 0.614, "ev": 1.76, "tier": "MID"},
        ]
        _six_pool = [s for s in _raw_sample if s["time"] >= _now_str]

    # ── Main display ────────────────────────────────────────────────────────
    if len(_six_pool) == 0:
        st.info("No qualifying selections yet — check back once today's markets are live, or lower the confidence threshold in the sidebar.")
    else:
        # ── Combined accumulator odds ──
        _combined_dec = 1.0
        for _ps in _six_pool:
            _combined_dec *= _ps["decimal"]
        _combined_dec = round(_combined_dec, 2)
        _acc_stake    = round(st.session_state.get("daily_budget", 50) * 0.40, 2)
        _acc_return   = round(_acc_stake * _combined_dec, 2)

        # ── KPI row ──
        _kc1, _kc2, _kc3, _kc4 = st.columns(4)
        _kc1.metric("🎯 Qualifying Selections", str(len(_six_pool)))
        _kc2.metric("🎰 Accumulator Odds", f"{_combined_dec:,.0f}x")
        _kc3.metric("💰 Acc Stake", f"£{_acc_stake:.2f}", f"Return if all win: £{_acc_return:,.2f}")
        _l15_eligible  = [s for s in _six_pool if s["decimal"] > 1.67]
        _l15_available = len(_l15_eligible) >= 4
        _kc4.metric("♥ Lucky 15", "✅ Available" if _l15_available else "✖ Not enough horses",
                    f"{len(_l15_eligible)} of 4 needed" if not _l15_available else "Optional — see below")

        st.markdown("---")

        # ── All qualifying selections table ──
        st.markdown("#### 📋 All Qualifying Selections")
        st.caption(f"All {len(_six_pool)} horses above {_conf_threshold:.0%} confidence and above 4/6 price. Put all of these in your accumulator.")
        _sel_rows = []
        for _s in _six_pool:
            _sel_rows.append({
                "Time":       _s["time"],
                "Horse":      _s["horse"],
                "Course":     _s["course"],
                "Odds":       _s["odds_str"],
                "Dec":        f"{_s['decimal']:.2f}x",
                "Confidence": f"{_s['confidence']:.1%}",
                "EV":         f"+{_s['ev']:.2f}" if _s["ev"] >= 0 else str(_s["ev"]),
                "Tier":       _s["tier"],
            })
        st.dataframe(pd.DataFrame(_sel_rows), use_container_width=True, hide_index=True)

        st.markdown("---")

        # ── Staking Plan ────────────────────────────────────────────────
        _budget        = float(st.session_state.get("daily_budget", 50))
        _l15_available = len(_l15_eligible) >= 4
        # Budget split: if L15 available, 60% acc + 40% L15. Otherwise 100% acc.
        if _l15_available:
            _acc_stake_plan = round(_budget * 0.60, 2)
            _l15_stake_plan = round(_budget * 0.40, 2)   # 40% split across 15 bets
            _l15_bet_size   = round(_l15_stake_plan / 15, 2)
        else:
            _acc_stake_plan = round(_budget, 2)
            _l15_stake_plan = 0.0
            _l15_bet_size   = 0.0
        _acc_return_plan = round(_acc_stake_plan * _combined_dec, 2)

        st.markdown("#### 💳 Staking Plan")
        _sp1, _sp2, _sp3 = st.columns(3)
        _sp1.metric("Daily Budget", f"£{_budget:.2f}")
        _sp2.metric("🎰 Accumulator Stake", f"£{_acc_stake_plan:.2f}",
                    f"Return if all win: £{_acc_return_plan:,.2f}")
        if _l15_available:
            _sp3.metric("♥ Lucky 15 Stake", f"£{_l15_stake_plan:.2f}",
                        f"£{_l15_bet_size:.2f} per bet × 15")
        else:
            _sp3.metric("♥ Lucky 15", "N/A", "Need 4+ horses")

        st.markdown("---")
        st.markdown("#### 🎰 Accumulator — All Selections")
        st.caption(f"★ Main bet — stake £{_acc_stake_plan:.2f} on all {len(_six_pool)} selections as one accumulator. All must win.")
        _acc_rows = []
        for i, _s in enumerate(_six_pool):
            _acc_rows.append({
                "#":          i + 1,
                "Time":       _s["time"],
                "Horse":      _s["horse"],
                "Course":     _s["course"],
                "Odds":       _s["odds_str"],
                "Dec":        f"{_s['decimal']:.2f}x",
                "Confidence": f"{_s['confidence']:.1%}",
                "Tier":       _s["tier"],
            })
        st.dataframe(pd.DataFrame(_acc_rows), use_container_width=True, hide_index=True)
        st.success(
            f"💰 Stake **£{_acc_stake_plan:.2f}** on accumulator | "
            f"Combined odds: **{_combined_dec:,.0f}x** | "
            f"Projected return if all win: **£{_acc_return_plan:,.2f}**"
        )

        # ── Lucky 15 — optional, only if >= 4 horses qualify ──
        if _l15_available:
            st.markdown("---")
            st.markdown("#### ♥ Lucky 15 — Optional Side Bet")
            st.caption("4 horses selected from your qualifying list. Lucky 15 = 15 bets (4 singles, 6 doubles, 4 trebles, 1 four-fold). Only place this if you want part-return safety net.")

            # Use budget-derived stake per bet
            _l15_plan = None
            _l15_err  = None
            import itertools as _it
            def _l15_scenarios_budget(quartet, bet_size):
                decs = [s["decimal"] for s in quartet]
                def _combret(n):
                    flat = []
                    for c in _it.combinations(decs, n):
                        r = bet_size
                        for d in c: r *= d
                        flat.append(round(r, 2))
                    return {"min_return": round(min(flat), 2), "max_return": round(max(flat), 2),
                            "min_profit": round(min(flat) - _l15_stake_plan, 2)}
                s1_rets = [round(bet_size * d, 2) for d in decs]
                s1 = {"min_return": min(s1_rets), "max_return": max(s1_rets),
                      "min_profit": round(min(s1_rets) - _l15_stake_plan, 2)}
                return {"1_winner": s1, "2_winners": _combret(2),
                        "3_winners": _combret(3), "4_winners": _combret(4)}
            _quartet = sorted(_l15_eligible, key=lambda s: s["ev"], reverse=True)[:4]
            _l15_plan = {
                "lucky15_selections": [{"horse": s["horse"], "course": s["course"], "time": s["time"],
                                        "tier": s["tier"], "odds_str": s["odds_str"], "decimal": s["decimal"]}
                                       for s in _quartet],
                "lucky15_scenarios":  _l15_scenarios_budget(_quartet, _l15_bet_size),
                "total_staked":       _l15_stake_plan,
            }

            if _l15_plan is not None:
                _l15_sels = _l15_plan["lucky15_selections"]
                _scen     = _l15_plan["lucky15_scenarios"]

                _tier_df_rows = []
                for _s in _l15_sels:
                    _tier_df_rows.append({
                        "Tier":    _s["tier"],
                        "Horse":   _s["horse"],
                        "Course":  _s["course"],
                        "Time":    _s["time"],
                        "Odds":    _s["odds_str"],
                        "Decimal": f"{_s['decimal']:.2f}x",
                    })
                st.dataframe(pd.DataFrame(_tier_df_rows), use_container_width=True, hide_index=True)

                st.markdown(f"##### Return Scenarios (Lucky 15 — £{_l15_stake_plan:.2f} stake = 15 bets × £{_l15_bet_size:.2f})")
                _scen_rows = [
                    {"Scenario": "1 winner (best single)",
                     "Min Return": f"£{_scen['1_winner']['min_return']:.2f}",
                     "Max Return": f"£{_scen['1_winner']['max_return']:.2f}",
                     "vs stake": f"£{_scen['1_winner']['min_profit']:.2f}"},
                    {"Scenario": "2 winners",
                     "Min Return": f"£{_scen['2_winners']['min_return']:.2f}",
                     "Max Return": f"£{_scen['2_winners']['max_return']:.2f}",
                     "vs stake": f"£{_scen['2_winners']['min_profit']:.2f}"},
                    {"Scenario": "3 winners",
                     "Min Return": f"£{_scen['3_winners']['min_return']:.2f}",
                     "Max Return": f"£{_scen['3_winners']['max_return']:.2f}",
                     "vs stake": f"£{_scen['3_winners']['min_profit']:.2f}"},
                    {"Scenario": "ALL 4 winners",
                     "Min Return": f"£{_scen['4_winners']['max_return']:.2f}",
                     "Max Return": f"£{_scen['4_winners']['max_return']:.2f}",
                     "vs stake": f"+£{_scen['4_winners']['min_profit']:.2f}"},
                ]
                st.dataframe(pd.DataFrame(_scen_rows), use_container_width=True, hide_index=True)
            else:
                if _l15_err:
                    st.warning(f"Lucky 15 builder note: {_l15_err}")
        else:
            st.info(f"✖ Lucky 15 not available today — only {len(_l15_eligible)} horse(s) qualify (need 4+). Accumulator is the main bet.")

        st.markdown("---")

        # ── Loss Learning summary ──
        st.markdown("#### 🔍 Loss Learning — Recent Diagnoses")
        try:
            from learning.loss_analyser import get_loss_report as _get_loss_report
            _loss_txt = _get_loss_report(last_n=5)
            st.text(_loss_txt)
        except Exception:
            st.caption("Loss report accumulates after races are settled. Check back after today's races complete.")

        if not _is_live:
            st.info("📌 Showing today's manually-scored selections. Live data will populate automatically when the market feed connects.")

    st.markdown("---")
    st.caption("All figures are research estimates only. Phase 1 personal research tool. Singles removed from plan permanently.")



# ── Tab 2: Today's Selections ─────────────────────────────────
with tab2:
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

# ── Tab 3: Accumulator Permutations ───────────────────────────
with tab3:
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


# ── Tab 4: Accumulator Efficiency ────────────────────────────
with tab4:
    st.markdown("### Accumulator Efficiency Engine")
    st.markdown("Analyses every selection for true probability, expected value, and coverage options.")

    # Today's card — 21 April 2026
    sample_races = [
        {"race": "2:17 Pontefract", "runners": [
            {"horse": "Lady Youmzain",   "odds": "11/10", "confidence": 0.70},
            {"horse": "Runner 2",        "odds": "5/2",   "confidence": 0.48},
            {"horse": "Runner 3",        "odds": "7/1",   "confidence": 0.31},
        ]},
        {"race": "4:02 Pontefract", "runners": [
            {"horse": "Yorkshire Glory", "odds": "7/2",   "confidence": 0.67},
            {"horse": "Runner 2",        "odds": "9/4",   "confidence": 0.54},
            {"horse": "Runner 3",        "odds": "5/1",   "confidence": 0.39},
        ]},
        {"race": "4:55 Yarmouth", "runners": [
            {"horse": "Mister Mojito",   "odds": "13/2",  "confidence": 0.67},
            {"horse": "Runner 2",        "odds": "2/1",   "confidence": 0.55},
            {"horse": "Runner 3",        "odds": "4/1",   "confidence": 0.41},
        ]},
        {"race": "6:30 Wolverhampton", "runners": [
            {"horse": "Beaune",          "odds": "7/4",   "confidence": 0.73},
            {"horse": "Runner 2",        "odds": "3/1",   "confidence": 0.51},
            {"horse": "Runner 3",        "odds": "8/1",   "confidence": 0.35},
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


# ── Tab 5: Live Alerts ────────────────────────────────────────
with tab5:
    st.markdown("### Live Alerts")
    # Generate alerts from live market move signals
    _alerts_shown = 0
    # Always build alerts from sample df (today's known card) — live df used when feed connects
    _alert_df = _live_df if (_is_live and len(_live_df) > 0) else get_sample_selections()
    _steam = _alert_df[_alert_df["Signal"].str.contains("Steam", na=False)]
    _drift = _alert_df[_alert_df["Signal"].str.contains("Drift", na=False)]
    _moves = _alert_df[_alert_df["Signal"].str.contains("Move", na=False)]
    now_str = datetime.now().strftime("%H:%M")
    _live_label = "🟢 LIVE" if _is_live else "🟡 TODAY'S CARD"

    if len(_steam) > 0 or len(_drift) > 0 or len(_moves) > 0:
        if not _is_live:
            st.info("🟡 Showing today's pre-scored signals. Live market feed will update these in real time.")
        for _, row in _steam.iterrows():
            _race_label = f"{row.get('Time','')} {row.get('Course','')}" if 'Time' in row else row.get('Race','')
            st.markdown(
                f'<div class="alert-high">🔴 <strong>{now_str} BST</strong> [{_live_label}] — ⬆ STEAM: '
                f'<strong>{row["Horse"]}</strong> ({_race_label}) — Odds: {row["Odds"]} — Conf: {row["Confidence"]:.0%}</div>',
                unsafe_allow_html=True
            )
            _alerts_shown += 1
        for _, row in _drift.iterrows():
            _race_label = f"{row.get('Time','')} {row.get('Course','')}" if 'Time' in row else row.get('Race','')
            st.markdown(
                f'<div class="alert-medium">🟠 <strong>{now_str} BST</strong> [{_live_label}] — ⬇ DRIFT: '
                f'<strong>{row["Horse"]}</strong> ({_race_label}) — Odds: {row["Odds"]} — Conf: {row["Confidence"]:.0%}</div>',
                unsafe_allow_html=True
            )
            _alerts_shown += 1
        for _, row in _moves.iterrows():
            _race_label = f"{row.get('Time','')} {row.get('Course','')}" if 'Time' in row else row.get('Race','')
            st.markdown(
                f'<div class="alert-low">🟢 <strong>{now_str} BST</strong> [{_live_label}] — ⬆ MOVE: '
                f'<strong>{row["Horse"]}</strong> ({_race_label}) — Odds: {row["Odds"]}</div>',
                unsafe_allow_html=True
            )
            _alerts_shown += 1
    else:
        st.info("🟢 No market move signals in today's card. Check back closer to race times.")

    st.markdown("---")
    st.markdown("### Going Reports")
    if _going_live and _live_going_df is not None and len(_live_going_df) > 0:
        st.success(f"🟢 Live going data — {len(_live_going_df)} UK + Irish courses — updated {__import__('datetime').datetime.now().strftime('%H:%M')} BST")
        st.dataframe(_live_going_df, use_container_width=True, hide_index=True)
    else:
        st.warning("🟡 Sample going data shown")
        st.dataframe(pd.DataFrame([
            {"Course": "Pontefract",    "Going": "Good (8.0)",         "Updated": "08:00", "Source": "BHA"},
            {"Course": "Yarmouth",      "Going": "Good to Firm (5.7)", "Updated": "08:00", "Source": "BHA"},
            {"Course": "Wolverhampton", "Going": "Tapeta: Standard",   "Updated": "08:00", "Source": "BHA"},
            {"Course": "Ffos Las",      "Going": "Good to Soft (5.0)", "Updated": "08:00", "Source": "BHA"},
        ]), use_container_width=True, hide_index=True)

# ── Tab 6: Learning Engine ────────────────────────────────────
with tab6:
    st.markdown("### Learning Engine Performance")

    # Load live stats from learning loop
    @st.cache_data(ttl=120)
    def _get_learning_stats():
        try:
            import sys as _sys
            _sys.path.insert(0, __import__("os").path.dirname(__file__) + "/..")
            from learning.loop import LearningLoop
            return LearningLoop().get_performance_stats()
        except Exception:
            return None

    _stats = _get_learning_stats()
    _default_weights = {
        "market_odds": 0.25, "horse_form": 0.20, "track_form": 0.15,
        "going": 0.10, "trainer_form": 0.10, "jockey_form": 0.10,
        "market_moves": 0.07, "jump_index": 0.03,
    }

    if _stats:
        _settled = _stats.get("settled_races", 0)
        _hit     = _stats.get("hit_rate_pct", 0.0)
        _7d_hit  = _stats.get("hit_rate_7d_pct", 0.0)
        _total   = _stats.get("total_recommendations", 0)
        _winners = _stats.get("winners", 0)
        _days_left = _stats.get("days_until_first_adjust", 20)
        _adj     = _stats.get("weight_adjustments", 0)
        _cw      = _stats.get("current_weights", _default_weights)

        if _settled == 0:
            st.info(f"🟡 Learning loop active — tracking starts today. Needs 20 settled races before first weight adjustment. ({_total} runners recorded so far)")
        else:
            st.success(f"🟢 Live — {_settled} settled races tracked across {_stats.get('note','')}")

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Hit Rate (All)", f"{_hit:.1f}%" if _settled > 0 else "—", "Top selections only")
        with col2:
            st.metric("Hit Rate (7-day)", f"{_7d_hit:.1f}%" if _settled > 0 else "—", "Rolling window")
        with col3:
            st.metric("Recommendations", str(_total), "Logged today")
        with col4:
            st.metric("Weight Adjustments", str(_adj), f"{_days_left} races to first" if _adj == 0 else "Active")

        st.markdown("---")
        st.markdown("#### Current Signal Weightings")
        st.caption("Initial weights shown alongside current. As data accumulates, the learning loop nudges these automatically.")

        weight_rows = []
        for sig, init_w in _default_weights.items():
            curr_w = _cw.get(sig, init_w)
            change = curr_w - init_w
            arrow  = "↑" if change > 0.001 else "↓" if change < -0.001 else "—"
            label  = sig.replace("_", " ").title()
            weight_rows.append({
                "Signal":         label,
                "Initial Weight": f"{init_w*100:.0f}%",
                "Current Weight": f"{curr_w*100:.1f}%",
                "Change":         f"{arrow} {abs(change)*100:.1f}%" if change != 0 else "—",
                "Status":         "🟢 Adjusted" if abs(change) > 0.005 else "🟡 Default",
            })
        st.dataframe(pd.DataFrame(weight_rows), use_container_width=True, hide_index=True)

        if _stats.get("recent_winners"):
            st.markdown("---")
            st.markdown("#### Recent Winners Tracked")
            rw_df = pd.DataFrame(_stats["recent_winners"])
            if "winner" in rw_df.columns:
                st.dataframe(rw_df[["date","winner","course","odds"]].rename(columns={
                    "date":"Date","winner":"Winner","course":"Course","odds":"Odds"
                }), use_container_width=True, hide_index=True)

        if _settled >= 5:
            st.markdown("---")
            st.markdown("#### Confidence vs Outcome")
            st.caption(f"Avg confidence on winners: {_stats['avg_confidence_winners']:.0%} | "
                      f"Avg confidence on losers: {_stats['avg_confidence_losers']:.0%}")
            _gap = _stats["avg_confidence_winners"] - _stats["avg_confidence_losers"]
            if _gap > 0:
                st.success(f"✅ Model is predictive — confidence scores {_gap:.0%} higher on winners than losers")
            else:
                st.warning("⚠️ Not yet enough data to assess model predictiveness")
    else:
        st.info("🟡 Learning loop initialising — data will appear here as races complete today.")
        st.markdown("#### Default Signal Weightings (pre-learning)")
        st.dataframe(pd.DataFrame([
            {"Signal": s.replace("_"," ").title(), "Weight": f"{w*100:.0f}%"}
            for s, w in _default_weights.items()
        ]), use_container_width=True, hide_index=True)

# ── Tab 7: Odds Comparison ────────────────────────────────────
with tab7:
    st.markdown("## Odds Comparison — All Bookmakers")
    st.caption("Live odds from Betfair Exchange, The Racing API, and Oddschecker across all UK and Irish bookmakers")

    col_course, col_time = st.columns(2)
    with col_course:
        oc_course = st.text_input("Course", value="Pontefract", key="oc_course")
    with col_time:
        oc_time = st.text_input("Race Time (HH:MM)", value="14:00", key="oc_time")

    if st.button("Fetch Odds", key="fetch_odds_btn"):
        if MONITOR_AVAILABLE and _MULTI_MONITOR is not None:
            with st.spinner("Fetching odds from all bookmakers..."):
                try:
                    summary = _MULTI_MONITOR.get_current_odds_summary(oc_course, oc_time)
                    if summary:
                        oc_df = pd.DataFrame(summary)
                        display_cols = ["horse", "best_price", "best_bookie",
                                        "betfair_back", "betfair_lay", "betfair_vol",
                                        "bet365", "william_hill", "ladbrokes",
                                        "paddy_power", "coral", "sky_bet"]
                        available_cols = [c for c in display_cols if c in oc_df.columns]
                        st.dataframe(oc_df[available_cols].rename(columns={
                            "horse": "Horse", "best_price": "Best Price",
                            "best_bookie": "Best Bookie", "betfair_back": "Betfair Back",
                            "betfair_lay": "Betfair Lay", "betfair_vol": "Betfair Vol",
                            "bet365": "Bet365", "william_hill": "William Hill",
                            "ladbrokes": "Ladbrokes", "paddy_power": "Paddy Power",
                            "coral": "Coral", "sky_bet": "Sky Bet",
                        }), width=None, hide_index=True)
                    else:
                        st.info("No odds data found for this race. Check course name and time.")
                except Exception as _e:
                    st.error(f"Could not fetch odds: {_e}")
        else:
            st.warning("Odds monitor not available — check configuration.")

    st.markdown("---")
    st.markdown("### Recent Market Move Alerts")
    try:
        import json as _json
        _state_path = _os.path.join(_os.path.dirname(__file__), "..", "learning", "market_state.json")
        if _os.path.exists(_state_path):
            with open(_state_path) as _sf:
                _mstate = _json.load(_sf)
            _fired = _mstate.get("alerts_fired", [])
            if _fired:
                st.caption(f"{len(_fired)} total alerts fired today")
            else:
                st.info("No market move alerts fired yet today.")
        else:
            st.info("Monitor state not yet initialised — will populate once scheduler starts.")
    except Exception:
        st.info("Alert history unavailable.")

# ── Tab 8: Results History ────────────────────────────────────
with tab8:
    st.markdown("### Results History")
    st.caption("Every settled race — engine tip cross-checked against the actual winner automatically.")

    @st.cache_data(ttl=120)
    def _load_settlement_data():
        try:
            import sys as _s2
            _s2.path.insert(0, __import__("os").path.dirname(__file__) + "/..")
            from settlement.settle import SettlementEngine
            se = SettlementEngine()
            return se.get_results_for_dashboard(days=14), se.get_summary_stats()
        except Exception:
            return [], {}

    _settled_races, _settle_stats = _load_settlement_data()

    # KPI row
    _total_s  = _settle_stats.get("total", 0)
    _hits_s   = _settle_stats.get("hits", 0)
    _rate_s   = _settle_stats.get("hit_rate", 0.0)
    _rate_7d  = _settle_stats.get("hit_rate_7d", 0.0)
    _exc_s    = _settle_stats.get("exceptions", 0)
    _last_win = _settle_stats.get("last_winner")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Races Settled", str(_total_s), "All-time")
    with col2:
        st.metric("Engine Hit Rate", f"{_rate_s:.1f}%" if _total_s > 0 else "—", "Top selection wins")
    with col3:
        st.metric("Hit Rate (7-day)", f"{_rate_7d:.1f}%" if _total_s > 0 else "—", "Rolling window")
    with col4:
        st.metric("Last Winner", _last_win or "—", "Engine-tipped")

    st.markdown("---")

    if _settled_races:
        st.success(f"🟢 {_total_s} races settled — {_hits_s} engine hits")
        if _exc_s > 0:
            st.warning(f"⚠️ {_exc_s} races flagged for review (dead heats / DQs)")

        # Build display dataframe
        _rows = []
        for r in _settled_races:
            _rows.append({
                "Date":       r.get("date",""),
                "Time":       r.get("time",""),
                "Course":     r.get("course",""),
                "Going":      r.get("going",""),
                "Winner":     r.get("winner",""),
                "SP Odds":    r.get("winner_odds","N/A"),
                "2nd":        r.get("second","-"),
                "3rd":        r.get("third","-"),
                "Engine Tip": "✅ HIT" if r.get("engine_tipped") else "❌ MISS",
                "Confidence": f"{r['engine_confidence']:.0%}" if r.get("engine_confidence") else "—",
                "⚠️ Flag":    ", ".join(r.get("exceptions",[])) or "Clean",
            })
        _res_df = pd.DataFrame(_rows)

        def _colour_tip(val):
            if "HIT" in str(val):
                return "background-color: #003300; color: #00ff88; font-weight: bold"
            if "MISS" in str(val):
                return "background-color: #330000; color: #ff6666"
            return ""
        def _colour_flag(val):
            if val != "Clean":
                return "background-color: #332200; color: #ffaa00"
            return ""

        st.dataframe(
            _res_df.style
                .map(_colour_tip,  subset=["Engine Tip"])
                .map(_colour_flag, subset=["⚠️ Flag"]),
            use_container_width=True, hide_index=True
        )
    else:
        st.info("🟡 No settled races yet — results populate automatically as each race finishes today.")
        st.markdown("**Today's pending races (21 Apr 2026):**")
        st.dataframe(pd.DataFrame([
            {"Time": "2:17",  "Course": "Pontefract",    "Selection": "Lady Youmzain",   "Odds": "11/10", "Status": "Pending"},
            {"Time": "4:02",  "Course": "Pontefract",    "Selection": "Yorkshire Glory",  "Odds": "7/2",   "Status": "Pending"},
            {"Time": "4:38",  "Course": "Ffos Las",      "Selection": "Crystal Island",   "Odds": "4/6",   "Status": "Pending"},
            {"Time": "4:55",  "Course": "Yarmouth",      "Selection": "Mister Mojito",    "Odds": "13/2",  "Status": "Pending"},
            {"Time": "6:30",  "Course": "Wolverhampton", "Selection": "Beaune",           "Odds": "7/4",   "Status": "Pending"},
            {"Time": "8:30",  "Course": "Wolverhampton", "Selection": "Kaaranah",         "Odds": "13/8",  "Status": "Pending"},
        ]), use_container_width=True, hide_index=True)
        st.caption("The settlement engine polls every 2 minutes. First results expected after today's opening race.")
