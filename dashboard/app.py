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
    # Fallback only — shown when live feed is unavailable. No hardcoded horses.
    return pd.DataFrame(columns=["Time","Course","Horse","Jockey","Trainer","Going","Odds","Confidence","Signal"])

def get_sample_accas():
    # Returns empty — acca tab builds from live selections only
    return []

def get_sample_alerts():
    # Returns empty — alerts build from live feed signals only
    return []

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
    # Returns empty — results populate from live settlement engine only
    return pd.DataFrame(columns=["Date","Race","Selection","Result","Odds","Confidence"])

# ── Sidebar ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🏇 Racing Engine")
    st.markdown("**Phase 1 — Personal Research Tool**")
    st.markdown("---")
    _now_bst_sb = __import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo('Europe/London'))
    st.markdown(f"**Date:** {_now_bst_sb.strftime('%A %d %B %Y')}")
    st.markdown(f"**Time:** {_now_bst_sb.strftime('%H:%M')} BST")
    st.markdown("---")

    # ── Staking Settings ──────────────────────────────────────
    st.markdown("### ⚙️ Staking Settings")
    st.caption("Adjust anytime — saved for this session")

    _daily_budget = st.number_input(
        "Daily Budget (£)",
        min_value=5, max_value=500, value=st.session_state.get("daily_budget", 100), step=5,
        help="Total amount to allocate across all bets today"
    )
    st.session_state["daily_budget"] = _daily_budget

    _conf_threshold = st.slider(
        "Min Confidence Threshold",
        min_value=0.55, max_value=0.80, value=st.session_state.get("conf_threshold", 0.55),
        step=0.05,
        help="Slide left to 55% to bring in more selections, right to tighten the filter. Default is 55%."
    )
    st.caption(f"Currently: **{_conf_threshold:.0%}** — {'⚠️ Relaxed filter (more selections)' if _conf_threshold < 0.60 else '✅ Standard filter' if _conf_threshold == 0.60 else '🔒 Tight filter (fewer, higher-confidence only)'}")
    st.session_state["conf_threshold"] = _conf_threshold

    st.markdown("---")
    st.caption("BET A — Lucky 15 + singles (£50, top 4)")
    st.caption("BET B — Lucky 31 + singles (£50, top 5)")
    st.caption("Accumulator removed in v2.5.54.")

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
    st.markdown("**Engine v2.6.2** — Fix trainer/jockey results store, distance parsing fallback, OR gap validation (filter star ratings)")
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

# ── Global Qualifying Pool ────────────────────────────────────
# Built ONCE here at top level — shared by KPI metrics, Tab 1, Tab 2.
# Applies: model exclusions → confidence threshold → 4/6 price cut-off → NR gate → one-per-race.
# UI warnings (st.warning / st.info) for NR removals are deferred to Tab 1.

def _assign_tier(dec):
    if dec <= 2.50:  return "BANKER"
    if dec <= 5.00:  return "MID"
    if dec <= 10.00: return "VALUE"
    return "LONGSHOT"

_conf_threshold = st.session_state.get("conf_threshold", 0.55)
_pool_df, _pool_is_live = load_live_selections()  # fresh call respects cache TTL + Refresh button
_six_pool = []
_nr_removed_names = []   # populated by NR gate; Tab 1 shows the warning
_one_race_dropped = []   # populated by one-per-race; Tab 1 shows the info

try:
    from engine.odds_model import OddsModel as _PoolModel
    _pool_model = _PoolModel()
except Exception:
    _pool_model = None

try:
    import zoneinfo as _zi_pool
    _now_pool = __import__('datetime').datetime.now(
        _zi_pool.ZoneInfo('Europe/London')).strftime('%H:%M')
except Exception:
    _now_pool = __import__('datetime').datetime.utcnow().strftime('%H:%M')

# ── Favourite rank pre-processing ───────────────────────────────────────────
# For each race (Time + Course), rank all runners by decimal price.
# Rank 1 = market favourite (shortest price).
# We use this to exclude selections where a significantly shorter rival exists
# in the same race — avoids backing non-favourites into our banker pool.
#
# FAV_GAP_PCT = 0.35 (35%): if the favourite is more than 35% shorter than
# our selection, our horse is not market-backed and is excluded.
# Example: our horse at 2.0x (Evens), favourite at 1.35x — gap = 48% → exclude.
# Example: our horse at 2.0x (Evens), favourite at 1.60x — gap = 25% → allow.
#
# This is a SOFT exclusion — it only fires when both conditions are true:
#   1. Our horse is NOT the favourite (rank > 1)
#   2. The favourite is >35% shorter in decimal price
_FAV_GAP_PCT = 0.35

if _pool_is_live and len(_pool_df) > 0:
    # Build race-level favourite price lookup {"HH:MM::Course": shortest_decimal}
    _race_fav_price = {}
    _race_runners_pool = {}  # race_key -> list of {horse, trainer}
    for _, _fr in _pool_df.iterrows():
        _frkey = f"{str(_fr.get('Time',''))}::{str(_fr.get('Course',''))}"
        _frodds = str(_fr.get('Current Odds', '') or _fr.get('Odds', 'N/A')).strip()
        try:
            if '/' in _frodds:
                _fn, _fd = _frodds.split('/')
                _frdec = float(_fn) / float(_fd) + 1
            else:
                _frdec = float(_frodds)
        except Exception:
            _frdec = 99.0
        if _frdec > 1.0:
            if _frkey not in _race_fav_price or _frdec < _race_fav_price[_frkey]:
                _race_fav_price[_frkey] = _frdec
        _race_runners_pool.setdefault(_frkey, []).append({
            "horse":   str(_fr.get("Horse", "")),
            "trainer": str(_fr.get("Trainer", "")),
        })

# ── SELECTION PARITY WITH MORNING BRIEF ─────────────────────────────────────
# Use the SAME function the morning brief uses. This guarantees the app display
# and the emailed brief show the same horses under the same filters. The shared
# function already performs: OddsModel exclusions, confidence threshold, handicap
# uplift, 4/6 cut-off, favourite-gap exclusion, small/large-field exclusions,
# NR gate, and one-horse-per-race. We then map its output into the dict shape
# the app UI expects (adds odds_str, ev, tier, signal).
_brief_selections = []
try:
    from briefs.daily_brief import _get_official_selections as _brief_sels_fn
    _brief_selections = _brief_sels_fn(_conf_threshold)
except Exception as _bs_err:
    print(f"[App] Unable to load brief selections: {_bs_err}")
    _brief_selections = []

for _bs in _brief_selections:
    _ptime = str(_bs.get("time", ""))
    if _ptime and _ptime < _now_pool:
        continue  # skip past races in the app view
    _pdec = float(_bs.get("decimal", 0) or 0)
    _pdisp_odds = str(_bs.get("odds", _bs.get("curr_odds", "N/A")))
    _pconf = float(_bs.get("confidence", 0) or 0)
    _six_pool.append({
        'horse':              _bs.get("horse", ""),
        'course':             _bs.get("course", ""),
        'time':               _ptime,
        'odds_str':           _pdisp_odds,
        'decimal':            round(_pdec, 3),
        'confidence':         round(_pconf, 3),
        'ev':                 round(_pconf * _pdec - 1, 3) if _pdec else 0.0,
        'tier':               _bs.get("tier", _assign_tier(round(_pdec, 3))),
        'signal':             _bs.get("signal", "Stable"),
        'is_fav':             bool(_bs.get("is_fav", False)),
        'fav_price':          float(_bs.get("fav_price", 0) or 0),
        'runners':             int(_bs.get("runners", 0) or 0),
        'low_value_acca':      bool(_bs.get("low_value_acca", False)),
        'low_value_reason':    str(_bs.get("low_value_reason", "") or ""),
        'race_type':           str(_bs.get("race_type", "") or ""),
        'rival_top_trainer':   bool(_bs.get("rival_top_trainer", False)),
        'rival_trainer_name':  str(_bs.get("rival_trainer_name", "") or ""),
        # v2.5.47 — fold-bet gating fields (passed through from official sels)
        'gap_to_2nd':         float(_bs.get("gap_to_2nd", 0) or 0),
        'is_dominant_fav':    bool(_bs.get("is_dominant_fav", False)),
        'yg_risk':            bool(_bs.get("yg_risk", False)),
        'split_market':       bool(_bs.get("split_market", False)),
        'curr_odds':          str(_bs.get("curr_odds", _pdisp_odds) or _pdisp_odds),
        # Oddschecker multi-bookie (v2.5.40)
        'best_odds_decimal':    _bs.get("best_odds_decimal"),
        'best_odds_fractional': _bs.get("best_odds_fractional"),
        'best_bookmaker':       _bs.get("best_bookmaker", ""),
        'odds_consensus':       _bs.get("odds_consensus"),
        'bookmaker_count':      _bs.get("bookmaker_count"),
    })

_six_pool.sort(key=lambda x: x['confidence'], reverse=True)

# The legacy inline-pool block below is retained only to compute
# _race_runners_pool-style structures used elsewhere in the dashboard. It no
# longer populates _six_pool — that is done above via _get_official_selections.
if False and _pool_is_live and len(_pool_df) > 0:
    for _, _prow in _pool_df.iterrows():
        _ptime = str(_prow.get('Time', ''))
        if _ptime < _now_pool:
            continue  # skip past races

        _prunner = {
            'odds':         str(_prow.get('Odds', 'N/A')),
            'current_odds': str(_prow.get('Current Odds', '')) or str(_prow.get('Odds', 'N/A')),
            'form':         str(_prow.get('Form', '-')),
            'going':        str(_prow.get('Going', '')),
            'trainer':      str(_prow.get('Trainer', '')),
            'jockey':       str(_prow.get('Jockey', '')),
            'signal':       str(_prow.get('Signal', 'Stable')),
            'tf_stars':     _prow.get('TF Stars'),
            'bet_movements': [],
            'field_size':   int(_prow.get('Field Size', 0) or 0),
            'race_type':    str(_prow.get('Race Type', '')),
            'race_class':   str(_prow.get('Race Class', '')),
            'race_name':    str(_prow.get('Race Name', '')),
            'is_handicap':  bool(_prow.get('Is Handicap', False)),
            # v2.6.1 — pass v2.6.0 signal fields to scoring
            'previous_results':    (_prow.get('Previous Results') if isinstance(_prow.get('Previous Results'), list) else []),
            'race_history_stats':  (_prow.get('Race History Stats') if isinstance(_prow.get('Race History Stats'), list) else []),
            'rating123':           _prow.get('Rating123'),
            'last_ran_days':       _prow.get('Last Ran Days'),
            'all_ratings_in_race': (_prow.get('All Ratings In Race') if isinstance(_prow.get('All Ratings In Race'), list) else []),
            'race_dist_f':         float(_prow.get('Race Dist F', 0) or 0),
        }

        if _pool_model:
            try:
                _pexcl = _pool_model.should_exclude(_prunner, race_name=str(_prow.get('Race Name', '')))
                _pexclude = _pexcl[0] if isinstance(_pexcl, tuple) else bool(_pexcl)
            except Exception:
                _pexclude = False
            if _pexclude:
                continue
        else:
            # Model unavailable — apply hard exclusions from raw feed fields
            # Mirror OddsModel.should_exclude() logic without the class
            _pfs = int(_prow.get('Field Size', 0) or 0)
            if _pfs >= 16:
                continue  # large field
            _psig = str(_prow.get('Signal', 'Stable')).lower()
            _ptf  = _prow.get('TF Stars')
            try:
                _ptf_int = int(str(_ptf).strip())
            except Exception:
                _ptf_int = 0
            # Require at least 2 of: decent form score, TF>=4, steam signal, short price
            _pform_str = str(_prow.get('Form', '-'))
            _pform_digits = [c for c in _pform_str if c.isdigit()]
            _pform_score = (sum(1 for d in _pform_digits[-6:] if d in '123') / max(len(_pform_digits[-6:]),1)) if _pform_digits else 0
            _pcurr_chk = str(_prow.get('Current Odds','')).strip() or str(_prow.get('Odds',''))
            try:
                if '/' in _pcurr_chk:
                    _cn,_cd = _pcurr_chk.split('/')
                    _cdec = float(_cn)/float(_cd)+1
                else:
                    _cdec = float(_pcurr_chk)
            except Exception:
                _cdec = 99.0
            _psignals = 0
            if _pform_score >= 0.50: _psignals += 1
            if _ptf_int >= 4:        _psignals += 1
            if 'steam' in _psig or 'move' in _psig: _psignals += 1
            if _cdec <= 2.50:        _psignals += 1
            if _psignals < 2:
                continue

        _peff_thresh = _conf_threshold
        if _pool_model and _prunner.get('is_handicap'):
            _peff_thresh = _pool_model.get_handicap_threshold(_prunner, _conf_threshold)
        elif not _pool_model and bool(_prow.get('Is Handicap', False)):
            _peff_thresh = _conf_threshold + 0.10  # handicap uplift without model

        _pconf = _pool_model.calculate_confidence(_prunner) if _pool_model else float(_prow.get('Confidence', 0))
        if _pconf < _peff_thresh:
            continue

        _pcurr = str(_prow.get('Current Odds', '')).strip()
        _podds_filter = _pcurr if _pcurr and _pcurr not in ('', 'N/A', 'None', 'nan') \
                        else str(_prow.get('Odds', 'Evs'))
        _pdisp_odds = str(_prow.get('Odds', _podds_filter))
        try:
            if '/' in _podds_filter:
                _pn, _pd = _podds_filter.split('/')
                _pdec = float(_pn) / float(_pd) + 1
            else:
                _pdec = float(_podds_filter)
        except Exception:
            _pdec = 2.0
        if _pdec <= 1.67:
            continue

        # ── Favourite gap check ───────────────────────────────────────────
        # Exclude if a significantly shorter-priced horse exists in this race.
        # Protects against selecting against a dominant market leader.
        _pracekey = f"{_ptime}::{str(_prow.get('Course',''))}"
        _pfav_dec = _race_fav_price.get(_pracekey, _pdec)
        _is_fav_pool = _pdec <= _pfav_dec + 1e-9
        if _pfav_dec < _pdec:  # we are not the favourite
            _pgap = (_pdec - _pfav_dec) / _pfav_dec
            if _pgap > _FAV_GAP_PCT:
                continue  # favourite is >35% shorter — market disagrees with us

        _prunners = int(_prow.get('Field Size', _prow.get('Runners', 0)) or 0)
        _phorse_name = str(_prow.get('Horse', 'Unknown'))

        # Small-field non-fav exclusion: ≤6 runners and not the fav.
        if _prunners and _prunners <= 6 and not _is_fav_pool:
            print(f"[Brief] Small-field non-fav excluded: {_phorse_name} "
                  f"({_prunners} runners, not fav)")
            continue

        # Large-field weak non-fav exclusion: ≥11 runners, not fav, <60% conf.
        if _prunners >= 11 and not _is_fav_pool and _pconf < 0.60:
            print(f"[Brief] Large-field weak non-fav excluded: {_phorse_name} "
                  f"({_prunners} runners, {_pconf:.0%} conf, not fav)")
            continue

        # Low acca value: thin field OR odds-on price (≤1.85) — v2.5.35
        _plow_thin_field = (_prunners > 0 and _prunners <= 4)
        _plow_odds_on   = (_pdec <= 1.85)
        _plow_value_acca = _plow_thin_field or _plow_odds_on
        _plow_reason = (
            "thin field" if _plow_thin_field else ("odds-on price" if _plow_odds_on else "")
        )

        # Top-trainer-in-race warning (warning flag only, never auto-excludes)
        _p_rival = {"rival_top_trainer": False, "rival_trainer_name": ""}
        try:
            from engine.staking import detect_rival_top_trainer as _p_detect_rival
            _p_rival = _p_detect_rival(
                _phorse_name,
                _race_runners_pool.get(_pracekey, []),
            )
        except Exception:
            pass

        _six_pool.append({
            'horse':      _phorse_name,
            'course':     str(_prow.get('Course', '')),
            'time':       _ptime,
            'odds_str':   _pdisp_odds,
            'decimal':    round(_pdec, 3),
            'confidence': round(_pconf, 3),
            'ev':         round(_pconf * _pdec - 1, 3),
            'tier':       _assign_tier(round(_pdec, 3)),
            'is_fav':     _is_fav_pool,
            'fav_price':  round(float(_pfav_dec), 2),
            'runners':    _prunners,
            'low_value_acca': _plow_value_acca,
            'low_value_reason': _plow_reason,
            'race_type':  str(_prow.get('Race Type', '') or '').strip(),
            'rival_top_trainer':  _p_rival.get('rival_top_trainer', False),
            'rival_trainer_name': _p_rival.get('rival_trainer_name', ''),
            # Oddschecker multi-bookie (v2.5.40)
            'best_odds_decimal':    _prow.get("Best Odds Decimal"),
            'best_odds_fractional': _prow.get("Best Odds Fractional"),
            'best_bookmaker':       _prow.get("Best Bookmaker", ""),
            'odds_consensus':       _prow.get("Odds Consensus"),
            'bookmaker_count':      _prow.get("Bookmaker Count"),
            # v2.5.55 — course specialist + distance affinity
            'course_signal':   float(_prow.get("Course Signal", 0.50) or 0.50),
            'distance_signal': float(_prow.get("Distance Signal", 0.50) or 0.50),
            'course_wins':     int(_prow.get("Course Wins", 0) or 0),
            'course_runs':     int(_prow.get("Course Runs", 0) or 0),
            'distance_wins':   int(_prow.get("Distance Wins", 0) or 0),
            'distance_runs':   int(_prow.get("Distance Runs", 0) or 0),
            'race_dist_f':     float(_prow.get("Race Dist F", 0) or 0),
        })

    _six_pool.sort(key=lambda x: x['confidence'], reverse=True)

# NR gate — strip non-runners silently here; warning shown in Tab 1.
# Runs fresh on every Tab 1 load (no caching). Case-insensitive comparison so
# feed variants ("NONRUNNER", "NonRunner", "non_runner") can never slip through.
try:
    from dashboard.live_data import get_non_runners as _get_nrs_pool
    _nr_pool_list  = _get_nrs_pool()  # fresh pull every call
    _nr_pool_names = {str(nr.get('Horse', '')).strip().upper() for nr in _nr_pool_list}
    _nr_removed_names = []
    _pool_kept = []
    for _s_pool in _six_pool:
        _hn = str(_s_pool.get('horse', '')).strip().upper()
        if _hn in _nr_pool_names:
            print(f"[NR Gate] Stripped {_s_pool.get('horse','?')} — status: NONRUNNER "
                  f"(race {_s_pool.get('time','?')} {_s_pool.get('course','?')})")
            _nr_removed_names.append(_s_pool.get('horse', ''))
            continue
        _pool_kept.append(_s_pool)
    _six_pool = _pool_kept
except Exception:
    pass

# One-per-race rule — keep highest confidence per race
if _six_pool:
    _pool_seen = {}
    _pool_clean = []
    for _ps2 in _six_pool:
        _prk = f"{_ps2['time']}::{_ps2['course']}"
        if _prk not in _pool_seen:
            _pool_seen[_prk] = _ps2
            _pool_clean.append(_ps2)
        else:
            if _ps2['confidence'] > _pool_seen[_prk]['confidence']:
                _pool_clean = [x for x in _pool_clean
                               if not (x['time'] == _ps2['time'] and x['course'] == _ps2['course'])]
                _pool_clean.append(_ps2)
                _pool_seen[_prk] = _ps2
    _one_race_dropped = [s['horse'] for s in _six_pool if s not in _pool_clean]
    _six_pool = sorted(_pool_clean, key=lambda x: x['time'])

# Update top KPI from the real pool
_top_sels = len(_six_pool)


# ── Top KPI Metrics ───────────────────────────────────────────
# Race count: use live meetings if available, else count today's known card (6 races, 4 meetings)
_races_today = sum(len(m.get('races', [])) for m in _live_meetings_data) if _meetings_live else 6
_top_sels = len(_six_pool)  # pool already built above

_signal_df = _live_df if (_is_live and len(_live_df) > 0) else get_sample_selections()
_steam_alerts = len(_signal_df[_signal_df['Signal'].str.contains('Steam|Move', na=False)])

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Races Today", str(_races_today), "UK + IRE" + (" 🟢 LIVE" if _meetings_live else " (sample)"))
with col2:
    st.metric("Top Selections", str(_top_sels), f"Min conf: {int(_conf_threshold*100)}% | 4/6 cut-off")
with col3:
    st.metric("Acca Permutations", "Auto", "From live runners")
with col4:
    # Pull real hit rate from learning loop
    try:
        import sys as _s3, os as _os3
        _s3.path.insert(0, _os3.path.join(_os3.path.dirname(__file__), ".."))
        from learning.loop import LearningLoop as _LL
        _ll     = _LL()
        _recs   = _ll.recommendations.get("records", [])
        _settled = [r for r in _recs if r.get("won") is not None]
        _wins    = [r for r in _settled if r.get("won")]
        _hit_rate_kpi = f"{len(_wins)/len(_settled)*100:.1f}%" if _settled else "—"
        _hit_delta    = f"{len(_settled)} races settled"
    except Exception:
        _hit_rate_kpi = "—"
        _hit_delta    = "0 races settled"
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
    st.caption(f"Budget: **£{st.session_state.get('daily_budget', 100)}** | 3-Bet plan: BET 1 (60%) + BET 2 (25%) + BET 3 (15%) | Min conf: **{st.session_state.get('conf_threshold', 0.55):.0%}** (handicaps: +10%) | Short price cut-off: 4/6 | Fields 16+ excluded")

    # Show NR and one-per-race warnings here in Tab 1
    if _nr_removed_names:
        st.warning(f"⚠️ Non-runner(s) removed: {', '.join(_nr_removed_names)}")
    if _one_race_dropped:
        st.info(f"ℹ️ One selection per race rule applied — removed: {', '.join(_one_race_dropped)}. Only highest-confidence horse from each race included.")

    # Pool is already built above — _six_pool is ready
    _t1_df, _t1_is_live = _pool_df, _pool_is_live  # alias for any legacy references below

    # _six_pool already built at top level (filter → NR gate → one-per-race)

    # ── Main display ────────────────────────────────────────────────────────
    if len(_six_pool) == 0:
        st.info("No qualifying selections yet — check back once today's markets are live, or lower the confidence threshold in the sidebar.")
    else:
        st.markdown("---")

        # ── Overnight market moves ───────────────────────────────────
        try:
            import sys as _sys
            _sys.path.insert(0, ".")
            from dashboard.early_market import get_market_movers, _today_bst as _em_today
            _overnight_movers = get_market_movers(_em_today(), min_move_pct=0.15, vs="show")
            if _overnight_movers and "error" in _overnight_movers[0]:
                _overnight_movers = []
        except Exception:
            _overnight_movers = []

        _mv_lookup = {m["horse"].lower().strip(): m for m in _overnight_movers}

        # ── All qualifying selections table ──
        st.markdown("#### 📋 All Qualifying Selections")
        st.caption(f"All {len(_six_pool)} horses above {_conf_threshold:.0%} confidence and above 4/6 price. Overnight move = % change vs yesterday's show price.")
        _sel_rows = []
        for _s in _six_pool:
            _mv  = _mv_lookup.get(_s["horse"].lower().strip())
            if _mv:
                _mv_str = f"⬆{_mv['move_pct']:.0f}% ({_mv['baseline_odds']}→{_mv['current_odds']})" \
                          if _mv["direction"] == "STEAM" \
                          else f"⬇{_mv['move_pct']:.0f}% ({_mv['baseline_odds']}→{_mv['current_odds']})"
            else:
                _mv_str = "—"
            _warn_bits = []
            if _s.get("rival_top_trainer"):
                _rv_nm = (_s.get("rival_trainer_name", "") or "TOP TRAINER").strip()
                _warn_bits.append(f"⚠ TOP TRAINER IN RACE ({_rv_nm})")
            # v2.5.55 — course specialist + distance affinity tags
            _crn  = int(_s.get("course_runs", 0) or 0)
            _cw   = int(_s.get("course_wins", 0) or 0)
            _drn  = int(_s.get("distance_runs", 0) or 0)
            _dw   = int(_s.get("distance_wins", 0) or 0)
            _dist_f = float(_s.get("race_dist_f", 0) or 0)
            _course_str = f"{_cw}/{_crn} here" if _crn > 0 else "no runs here"
            _dist_str   = (
                f"{_dw}/{_drn} @ {_dist_f:g}f" if _drn > 0
                else (f"no runs @ {_dist_f:g}f" if _dist_f > 0 else "no dist data")
            )
            _sel_rows.append({
                "Time":           _s["time"],
                "Horse":          _s["horse"],
                "Course":         _s["course"],
                "Odds":           _s["odds_str"],
                "Confidence":     f"{_s['confidence']:.1%}",
                "Course Form":    _course_str,
                "Distance Form":  _dist_str,
                "Overnight Move": _mv_str,
                "Signal":         _s.get("signal", "Stable"),
                "Tier":           _s["tier"],
                "Warning":        " | ".join(_warn_bits) if _warn_bits else "—",
            })
        st.dataframe(pd.DataFrame(_sel_rows), use_container_width=True, hide_index=True)

        st.markdown("---")

        # ══════════════════════════════════════════════════════════════
        # STAKING ENGINE v2.5.54 — Bet A (Lucky 15 + singles) / Bet B (Lucky 31 + singles)
        # No accumulator. Each bet £50 (£100 total when both active).
        # ══════════════════════════════════════════════════════════════
        from engine.staking import get_daily_bets as _get_daily_bets

        _bets  = _get_daily_bets(_six_pool)
        _bet_a = _bets.get("bet_a") or {}
        _bet_b = _bets.get("bet_b") or {}
        _bet_a_active = not _bet_a.get("skipped")
        _bet_b_active = not _bet_b.get("skipped")

        st.markdown("#### 💳 Today's Staking Plan — BET A / BET B")

        if not _bet_a_active and not _bet_b_active:
            st.warning(
                "**No qualifying bets today.** BET A requires 4 selections; "
                "BET B requires 5+. Engine abstains."
            )
        else:
            st.success(
                f"**v2.5.54 unified BET A / BET B** — "
                f"BET A: {'✅' if _bet_a_active else '—'} | "
                f"BET B: {'✅' if _bet_b_active else '— (5+ selections required)'}"
            )

        def _render_bet_card(bet, colour, bet_key):
            if not bet or bet.get("skipped"):
                if bet_key == "BET B":
                    st.caption("BET B not available today — requires 5+ selections.")
                else:
                    st.caption(f"{bet_key} unavailable today.")
                return
            sels   = bet.get("selections") or []
            lucky  = bet.get("lucky_bet")  or {}
            sgls   = bet.get("singles")    or {}
            total  = bet.get("total_stake", 0.0)

            st.markdown(
                f"##### <span style='color:{colour}'>{bet_key} — "
                f"{lucky.get('label', '')} + Singles</span> "
                f"&nbsp;&nbsp; **£{total:.2f} total**",
                unsafe_allow_html=True,
            )
            _rows = []
            stake_each = (sgls.get("stake", 0.0) / max(len(sels), 1))
            for s in sels:
                dec = float(s.get("decimal_odds", 0) or 0)
                _rows.append({
                    "Time":           s.get("time", ""),
                    "Horse":          s.get("name", ""),
                    "Course":         s.get("course", ""),
                    "Decimal":        f"{dec:.2f}x",
                    "Confidence":     f"{float(s.get('confidence', 0)):.1%}",
                    "Single Stake":   f"£{stake_each:.2f}",
                    "Single Return":  f"£{stake_each * dec:.2f}",
                })
            st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)
            st.info(
                f"**{lucky.get('label','Lucky')}:** £{lucky.get('stake', 0):.2f} "
                f"across {lucky.get('lines', 0)} lines "
                f"(£{lucky.get('stake_per_line', 0):.4f}/line) | "
                f"max return if all win: £{lucky.get('potential_return', 0):,.2f}"
                f"\n\n**Singles:** £{sgls.get('stake', 0):.2f} "
                f"(£{stake_each:.2f} per horse)"
            )

        _render_bet_card(_bet_a, "#00ff88", "BET A")
        st.markdown("")
        _render_bet_card(_bet_b, "#ffaa00", "BET B")

        with st.expander("Why this structure? (click to expand)"):
            st.markdown("""
**v2.5.54 — Unified BET A / BET B (no accumulator)**

- **BET A — CORE:** top 4 selections by confidence.
  Lucky 15 (£20 across 15 lines) + Singles (£30, £7.50/horse). Total £50.
- **BET B — MID:** top 5 selections by confidence.
  Lucky 31 (£20 across 31 lines) + Singles (£30, £6/horse). Total £50.

BET B is only active when 5+ selections qualify. The straight n-fold
accumulator has been removed entirely — Lucky perms already cover the
all-win line, and singles smooth the variance.

**Hard exclusions (upstream):** evens (2.0) price floor, Group/Listed/Grade
races, and the standard confidence threshold.
            """)

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

    # Tab 2 reads directly from _six_pool — already filtered, NR-gated, one-per-race.
    # This is the single source of truth. No re-filtering of _live_df here.
    if _six_pool:
        def _fmt_race_type(rt: str) -> str:
            _rt = str(rt or "").strip().lower()
            if not _rt:
                return ""
            return {
                "nhf": "NHF", "bumper": "NHF",
                "hurdle": "Hurdle", "flat": "Flat", "chase": "Chase",
            }.get(_rt, _rt.title())

        def _best_cell(sel):
            _bf = sel.get('best_odds_fractional')
            _bk = sel.get('best_bookmaker')
            if _bf and _bk:
                return f"{_bf} @ {_bk}"
            return "—"

        def _book_cell(sel):
            _n = sel.get('bookmaker_count')
            try:
                return int(_n) if _n else 0
            except Exception:
                return 0

        def _flags_cell(sel):
            flags = []
            if sel.get('split_market'):
                flags.append("⚠ SPLIT_MARKET")
            if sel.get('yg_risk'):
                flags.append("⚠ YG_RISK")
            return " ".join(flags)

        df = pd.DataFrame([{
            'Time':        s['time'],
            'Course':      s['course'],
            'Horse':       s['horse'],
            'Type':        _fmt_race_type(s.get('race_type', '')),
            'Odds':        s['odds_str'],
            'Decimal':     f"{s['decimal']:.2f}x",
            'Best':        _best_cell(s),
            'Books':       _book_cell(s),
            'Confidence':  s['confidence'],
            'Signal':      s.get('signal', 'Stable'),
            'Tier':        s['tier'],
            'EV':          round(s['ev'], 3),
            'Flags':       _flags_cell(s),
        } for s in _six_pool])
    else:
        df = pd.DataFrame()

    if df.empty:
        st.info("No qualifying selections — check back once markets are live.")
    else:
        # Ensure Confidence column is numeric
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

        # Only style columns that actually exist in the dataframe
        _style = df.style.map(colour_confidence, subset=["Confidence"]).format({"Confidence": "{:.0%}"})
        if "Signal" in df.columns:
            _style = _style.map(colour_signal, subset=["Signal"])

        st.dataframe(_style, use_container_width=True, hide_index=True)

    # ── Best Accumulator Options (EV-ranked) ──────────────────────
    if _six_pool:
        with st.expander("🎯 Best Accumulator Options (Ranked by EV)"):
            try:
                from engine.staking import rank_accumulator_combinations as _rank_accas_app
                _acca_combos = _rank_accas_app(_six_pool, top_n=5)
            except Exception as _ba_err:
                _acca_combos = []
                st.caption(f"Ranking unavailable: {_ba_err}")

            if not _acca_combos:
                st.info("No qualifying accumulator combinations — pool too thin after excluding low-value legs.")
            else:
                _rows_app = []
                for c in _acca_combos:
                    _warns = " | ".join(c.get("warnings", [])) or "Clean"
                    _rows_app.append({
                        "Rank":         c.get("rank", 0),
                        "Horses":       " + ".join(c.get("horses", [])),
                        "Legs":         c.get("legs", 0),
                        "Odds":         f"{c.get('combined_dec', 0):.1f}x ({c.get('combined_frac','')})",
                        "Win Prob %":   round(c.get("win_prob", 0) * 100, 1),
                        "Proj Return (£10)": f"£{c.get('proj_return', 0):,.2f}",
                        "Warnings":     _warns,
                    })
                _acca_df = pd.DataFrame(_rows_app)

                def _colour_rank(val):
                    try:
                        return "color: #00ff88; font-weight: bold" if int(val) == 1 else "color: #aaaaaa"
                    except Exception:
                        return ""

                def _colour_warn(val):
                    return "color: #ffaa00" if str(val) != "Clean" else "color: #00ff88"

                st.dataframe(
                    _acca_df.style
                        .map(_colour_rank, subset=["Rank"])
                        .map(_colour_warn, subset=["Warnings"]),
                    use_container_width=True, hide_index=True
                )
                st.caption(
                    "Ranked by Expected Value (probability × return). "
                    "Lower-ranked accas have worse mathematical expected value. "
                    "These are additive to the staking plan above — shown so you can spot "
                    "stronger combinations than the default main acc."
                )

    st.markdown("---")
    st.markdown("### Signal Breakdown")
    # Show live signal breakdown for top selection when model is active
    if MODEL_AVAILABLE and _ODDS_MODEL is not None and _is_live and _six_pool:
        top_sel    = _six_pool[0]
        top_runner_input = {
            "odds":    top_sel.get("odds_str", "N/A"),
            "form":    "-",
            "going":   top_sel.get("going", ""),
            "trainer": "",
            "jockey":  "",
            "signal":  top_sel.get("signal", "Stable"),
            "tf_stars": None,
            "bet_movements": [],
        }
        try:
            breakdown = _ODDS_MODEL.get_signal_breakdown(top_runner_input)
            label     = top_sel.get("horse", "Top Selection")

            # Map breakdown keys safely — use .get() with N/A fallback
            _sig_rows = [
                ("Market Odds",  0.20, breakdown.get("market_odds",  "—")),
                ("Horse Form",   0.20, breakdown.get("horse_form",   "—")),
                ("Track Form",   0.15, breakdown.get("track_form",   "—")),
                ("Going",        0.10, breakdown.get("going",        "—")),
                ("Trainer Form", 0.15, breakdown.get("trainer_form", "—")),
                ("Jockey Form",  0.08, breakdown.get("jockey_form",  "—")),
                ("Market Moves", 0.12, breakdown.get("market_moves", "—")),
            ]
            signals = pd.DataFrame(_sig_rows, columns=["Signal", "Weight", f"Score ({label})"])
            st.caption(f"Live signal breakdown for: **{label}** | Confidence: {top_sel['confidence']:.1%}")
        except Exception as _bd_err:
            st.caption(f"Signal breakdown unavailable: {_bd_err}")
            signals = pd.DataFrame()
    else:
        signals = pd.DataFrame({
            "Signal": ["Market Odds", "Horse Form", "Track Form", "Going",
                       "Trainer Form", "Jockey Form", "Market Moves"],
            "Weight": [0.25, 0.20, 0.15, 0.10, 0.10, 0.10, 0.10],
            "Score":  ["—"] * 7
        })

    if not signals.empty:
        col_name = [c for c in signals.columns if c not in ("Signal", "Weight")][0]
        # Only format numeric rows — skip N/A strings
        _num_mask = pd.to_numeric(signals[col_name], errors="coerce").notna()
        try:
            st.dataframe(signals, use_container_width=True, hide_index=True)
        except Exception:
            st.dataframe(signals, hide_index=True)

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

    if not acca_df.empty and "Confidence" in acca_df.columns:
        st.dataframe(
            acca_df.style.map(colour_acca_conf, subset=["Confidence"]).format({"Confidence": "{:.0%}"}),
            width="stretch", hide_index=True
        )
    elif not acca_df.empty:
        st.dataframe(acca_df, width="stretch", hide_index=True)
    else:
        st.info("Accumulator permutations will populate once today's qualifying selections are confirmed. Check Tab 1 for the current staking plan.")

    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("""
| Bet Type | Legs | Number of Bets |
|---|---|---|
| Double | 2 | 1 |
| Treble | 3 | 1 |
| 4-fold Acca | 4 | 1 |
| 5-fold Acca | 5 | 1 |
| 6-fold Acca | 6 | 1 |
        """)
    with col2:
        st.info("Accumulator permutations are built from today's qualifying selections. The 3-bet plan on Tab 1 is the recommended staking structure. The learning engine will adjust confidence thresholds automatically as results are recorded.")


# ── Tab 4: Accumulator Efficiency ────────────────────────────
with tab4:
    st.markdown("### 📈 Acca Efficiency")
    st.caption("Expected-value analysis per qualifying selection. EV = (confidence × decimal odds) − (1 − confidence).")

    if not _six_pool:
        st.info("No qualifying selections — check back once today's markets are live.")
    else:
        _ev_rows = []
        for _s in _six_pool:
            _c    = float(_s["confidence"])
            _dec  = float(_s["decimal"])
            _evv  = (_c * _dec) - (1 - _c)
            _role = "BANKER" if _s["tier"] == "BANKER" else "VALUE" if _s["tier"] == "VALUE" else _s["tier"]
            _ev_rows.append({
                "Horse":      _s["horse"],
                "Course":     _s["course"],
                "Time":       _s["time"],
                "Odds":       _s["odds_str"],
                "Confidence": f"{_c:.1%}",
                "EV":         round(_evv, 3),
                "Role":       _role,
            })
        _ev_rows.sort(key=lambda r: r["EV"], reverse=True)
        _ev_df = pd.DataFrame(_ev_rows)

        def _colour_role(val):
            if val == "BANKER":
                return "color: #00ff88; font-weight: bold"
            if val == "VALUE":
                return "color: #ffaa00; font-weight: bold"
            return "color: #aaaaaa"

        def _colour_ev_val(val):
            try:
                v = float(val)
                if v > 0.3: return "color: #00ff88; font-weight: bold"
                if v > 0:   return "color: #ffaa00"
                return "color: #ff6666"
            except Exception:
                return ""

        st.dataframe(
            _ev_df.style.map(_colour_role, subset=["Role"]).map(_colour_ev_val, subset=["EV"]),
            use_container_width=True, hide_index=True
        )

        st.markdown("---")
        st.markdown("#### EV by Horse")
        _chart_df = _ev_df.set_index("Horse")[["EV"]]
        st.bar_chart(_chart_df)

        st.markdown("---")
        st.markdown("#### Coverage Analysis")
        try:
            from engine.staking import classify_selections as _classify
            _cls = _classify(_six_pool)
            _b = _cls["bankers"]
        except Exception:
            _b = [s for s in _six_pool if s["tier"] == "BANKER"]
        if len(_b) >= 2:
            _riskiest = max(_b, key=lambda x: x["decimal"])
            st.info(
                f"**If BET 1 fails at the riskiest leg, BET 2 covers.** "
                f"BET 1 includes all {len(_b)} bankers; BET 2 drops "
                f"**{_riskiest['horse']}** ({_riskiest['odds_str']}, "
                f"{_riskiest['confidence']:.1%}) — the highest-priced leg — so it "
                f"lands when that horse loses but the other {len(_b)-1} bankers win."
            )
        else:
            st.caption("Coverage analysis requires at least 2 bankers in today's pool.")


# ── Tab 5: Live Alerts ────────────────────────────────────────
with tab5:
    st.markdown("### 🚨 Live Alerts")
    st.caption("Data refreshes every 5 minutes. Steam/drift thresholds set at >10% price move vs the 15:30 BST show-price snapshot.")

    # ── Load show price snapshot ──
    import json as _alert_json
    _snap_path = os.path.join(os.path.dirname(__file__), "..", "learning", "show_price_snapshot.json")
    _snapshot = None
    try:
        if os.path.exists(_snap_path):
            with open(_snap_path) as _sf:
                _snapshot = _alert_json.load(_sf)
    except Exception:
        _snapshot = None

    _snap_horses = {}
    if _snapshot and isinstance(_snapshot.get("horses"), dict):
        _snap_horses = {str(k).lower().strip(): v for k, v in _snapshot["horses"].items()}

    def _to_dec_alert(odds_str):
        try:
            s = str(odds_str).strip()
            if "/" in s:
                n, d = s.split("/")
                return float(n) / float(d) + 1
            return float(s)
        except Exception:
            return None

    # ── Non-runners ──
    _nr_rows = []
    try:
        from dashboard.live_data import get_non_runners as _get_nrs_tab5
        _nrs = _get_nrs_tab5() or []
        _nr_names = {str(nr.get("Horse","")).lower().strip() for nr in _nrs}
        for _s in _six_pool:
            if _s["horse"].lower().strip() in _nr_names:
                _nr_rows.append({
                    "Horse":  _s["horse"],
                    "Course": _s["course"],
                    "Time":   _s["time"],
                    "Status": "🔴 NON-RUNNER",
                })
    except Exception:
        pass

    # ── Steam + drift from snapshot ──
    _steam_rows = []
    _drift_rows = []
    if _snap_horses and _six_pool:
        for _s in _six_pool:
            _key = _s["horse"].lower().strip()
            _snap = _snap_horses.get(_key)
            if not _snap:
                continue
            _was = _to_dec_alert(_snap.get("odds") if isinstance(_snap, dict) else _snap)
            _now = _s["decimal"]
            if not _was or not _now or _was <= 1.0:
                continue
            _pct = (_was - _now) / _was * 100  # + shortening, - drifting
            if _pct > 10:
                _steam_rows.append({
                    "Horse":  _s["horse"],
                    "Course": _s["course"],
                    "Time":   _s["time"],
                    "Was":    f"{_was:.2f}x",
                    "Now":    f"{_now:.2f}x",
                    "% Move": f"⬆ {_pct:.1f}%",
                    "Direction": "STEAM",
                })
            elif _pct < -10:
                _drift_rows.append({
                    "Horse":  _s["horse"],
                    "Course": _s["course"],
                    "Time":   _s["time"],
                    "Was":    f"{_was:.2f}x",
                    "Now":    f"{_now:.2f}x",
                    "% Move": f"⬇ {abs(_pct):.1f}%",
                    "Badge":  "🔴 MONITOR",
                })

    # ── Display ──
    _has_snapshot = bool(_snap_horses)
    _anything = _steam_rows or _drift_rows or _nr_rows

    if _nr_rows:
        st.error(f"🔴 Non-runner alert — {len(_nr_rows)} previously selected horse(s) now NR")
        st.dataframe(pd.DataFrame(_nr_rows), use_container_width=True, hide_index=True)
        st.markdown("---")

    if _steam_rows:
        st.success(f"⬆ Steam moves — {len(_steam_rows)} horse(s) shortened >10% since 15:30 snapshot")
        st.dataframe(pd.DataFrame(_steam_rows), use_container_width=True, hide_index=True)
        st.markdown("---")

    if _drift_rows:
        st.warning(f"⬇ Drift alerts — {len(_drift_rows)} horse(s) drifted >10% since 15:30 snapshot")
        st.dataframe(pd.DataFrame(_drift_rows), use_container_width=True, hide_index=True)
        st.markdown("---")

    if not _anything:
        if not _has_snapshot:
            st.info("Show price snapshot captured at 15:30 daily — alerts will populate from 15:30 onwards.")
        else:
            st.success("No significant moves detected — all selections stable.")

    st.markdown("---")
    st.caption("Data refreshes every 5 minutes.")

# ── Tab 6: Learning Engine ────────────────────────────────────
with tab6:
    st.markdown("### 🧠 Learning Engine")
    st.caption("Last updated: Manually recalibrated 24 Apr 2026 — trainer_form increased after Henderson/Perth analysis.")

    import json as _learn_json
    _weights_path = os.path.join(os.path.dirname(__file__), "..", "learning", "learned_weights.json")
    _recs_path    = os.path.join(os.path.dirname(__file__), "..", "learning", "recommendations.json")
    _settled_path = os.path.join(os.path.dirname(__file__), "..", "learning", "settled_races.json")

    _current_weights = {
        "market_odds":  0.20, "horse_form":   0.20, "track_form":   0.15,
        "going":        0.10, "trainer_form": 0.15, "jockey_form":  0.08,
        "market_moves": 0.12,
    }
    try:
        if os.path.exists(_weights_path):
            with open(_weights_path) as _wf:
                _current_weights = _learn_json.load(_wf)
    except Exception:
        pass

    _n_recs    = 0
    _n_settled = 0
    try:
        if os.path.exists(_recs_path):
            with open(_recs_path) as _rf:
                _r = _learn_json.load(_rf)
            _n_recs = len(_r.get("records", [])) if isinstance(_r, dict) else 0
    except Exception:
        pass
    try:
        if os.path.exists(_settled_path):
            with open(_settled_path) as _sf:
                _sd = _learn_json.load(_sf)
            _n_settled = len(_sd.get("races", [])) if isinstance(_sd, dict) else 0
    except Exception:
        pass

    _signal_descriptions = {
        "market_odds":  "Bookmaker implied probability (sanity-check signal)",
        "horse_form":   "Form string parsed with recency weighting",
        "track_form":   "Course-specific record (needs Racing API)",
        "going":        "Going preference from historical runs",
        "trainer_form": "Trainer strike rate and recent-runs score",
        "jockey_form":  "Jockey strike rate and recent-runs score",
        "market_moves": "Steam / drift vs show-price snapshot",
    }

    col1, col2, col3 = st.columns(3)
    col1.metric("Recommendations Logged", str(_n_recs))
    col2.metric("Results Settled",        str(_n_settled))
    col3.metric("Weight Adjustments",     "0", f"{max(0, 20 - _n_settled)} races to first" if _n_settled < 20 else "Active")

    st.markdown("---")

    if _n_settled < 20:
        st.info(
            f"Collecting data — weights will self-adjust after 20 settled races "
            f"({_n_settled} of 20 recorded)."
        )
    else:
        st.success(f"🟢 Learning loop active — {_n_settled} settled races analysed.")

    st.markdown("#### Current Signal Weights")
    _weight_rows = []
    for _sig, _desc in _signal_descriptions.items():
        _w = _current_weights.get(_sig, 0.0)
        _weight_rows.append({
            "Signal":      _sig.replace("_", " ").title(),
            "Weight":      f"{_w*100:.1f}%",
            "Description": _desc,
        })
    st.dataframe(pd.DataFrame(_weight_rows), use_container_width=True, hide_index=True)

    _total_w = sum(_current_weights.get(k, 0) for k in _signal_descriptions.keys())
    st.caption(f"Total weight: **{_total_w*100:.0f}%** (must sum to 100%).")

    st.markdown("---")
    st.caption("Last updated: Manually recalibrated 24 Apr 2026 — trainer_form increased after Henderson/Perth analysis.")

# ── Tab 7: Results History ────────────────────────────────────
with tab7:
    st.markdown("### 📊 Results History")
    st.caption("Every logged recommendation. Past dates auto-settle from Sporting Life results on tab load.")

    # Auto-heal: settle any outstanding past-date recommendations silently
    try:
        from learning.loop import settle_outstanding_recommendations as _settle_outstanding
        _healed = _settle_outstanding()
    except Exception as _e_heal:
        _healed = 0

    import json as _res_json
    _r7_path   = os.path.join(os.path.dirname(__file__), "..", "learning", "recommendations.json")
    _res_path7 = os.path.join(os.path.dirname(__file__), "..", "learning", "results_store.json")
    _records = []
    try:
        if os.path.exists(_r7_path):
            with open(_r7_path) as _rf:
                _rd = _res_json.load(_rf)
            _records = _rd.get("records", []) if isinstance(_rd, dict) else []
    except Exception:
        _records = []

    _results_idx = {}
    try:
        if os.path.exists(_res_path7):
            with open(_res_path7) as _rsf:
                _rs = _res_json.load(_rsf)
            for _e in (_rs.get("results", []) if isinstance(_rs, dict) else []):
                rid = _e.get("race_id")
                if rid:
                    _results_idx[rid] = _e
    except Exception:
        _results_idx = {}

    _total_recs    = len(_records)
    _settled_recs  = [r for r in _records if r.get("won") is not None]
    _pending_recs  = [r for r in _records if r.get("won") is None]
    _wins_recs     = [r for r in _settled_recs if r.get("won")]

    def _frac_to_decimal(odds_str):
        s = str(odds_str).strip()
        if not s or s.lower() == "n/a":
            return None
        try:
            if "/" in s:
                num, den = s.split("/")
                return 1.0 + float(num) / float(den)
            if s.lower() in ("evs", "evens"):
                return 2.0
            return float(s)
        except Exception:
            return None

    STAKE = 60.0
    _total_pnl = 0.0
    for _r in _settled_recs:
        _dec = _frac_to_decimal(_r.get("odds"))
        if _r.get("won") and _dec:
            _total_pnl += STAKE * (_dec - 1.0)
        else:
            _total_pnl -= STAKE

    _hit_rate = (len(_wins_recs) / len(_settled_recs) * 100) if _settled_recs else 0.0

    _k1, _k2, _k3, _k4 = st.columns(4)
    _k1.metric("Selections",  str(_total_recs))
    _k2.metric("Won",         str(len(_wins_recs)))
    _k3.metric("Strike Rate", f"{_hit_rate:.1f}%")
    _k4.metric("P&L (£60 ew-level flat)", f"£{_total_pnl:+,.2f}")

    if _healed:
        st.success(f"Auto-settled {_healed} past race(s) from Sporting Life results.")

    st.markdown(
        f"**{_total_recs} selections, {len(_wins_recs)} won "
        f"({_hit_rate:.1f}% strike rate)** — P&L at £{STAKE:.0f} flat stake: **£{_total_pnl:+,.2f}**"
    )

    st.markdown("---")

    if not _records:
        st.info("No results recorded yet — selections will appear here once the morning brief runs.")
    else:
        def _colour_result(val):
            s = str(val)
            if "WON" in s:      return "background-color: #003300; color: #00ff88; font-weight: bold"
            if "LOST" in s:     return "background-color: #330000; color: #ff6666"
            if "Pending" in s:  return "color: #ffaa00"
            return ""

        # Settled table
        if _settled_recs:
            st.markdown("#### Settled")
            _sorted_settled = sorted(
                _settled_recs,
                key=lambda r: (r.get("date",""), r.get("time","")),
                reverse=True,
            )
            _rows = []
            for _r in _sorted_settled:
                _won = _r.get("won")
                _result = "✅ WON" if _won else "❌ LOST"
                _conf_v = _r.get("confidence")
                try:
                    _conf_disp = f"{float(_conf_v):.1%}" if _conf_v is not None else "—"
                except Exception:
                    _conf_disp = "—"
                _dec = _frac_to_decimal(_r.get("odds"))
                if _won and _dec:
                    _pnl = STAKE * (_dec - 1.0)
                else:
                    _pnl = -STAKE
                _settled_by = _r.get("settled_at", "")
                if _settled_by:
                    _settled_by = str(_settled_by)[:10]
                _rid = _r.get("race_id", "")
                _winner = _results_idx.get(_rid, {}).get("winner") or _r.get("outcome") or ""
                _rows.append({
                    "Date":      _r.get("date", ""),
                    "Time":      _r.get("time", ""),
                    "Course":    _r.get("course", ""),
                    "Horse":     _r.get("runner", ""),
                    "Confidence": _conf_disp,
                    "Odds":      str(_r.get("odds", "")),
                    "Result":    _result,
                    "Winner":    _winner,
                    "P&L (£)":   round(_pnl, 2),
                    "Settled":   _settled_by,
                })
            _df_settled = pd.DataFrame(_rows)
            st.dataframe(
                _df_settled.style.map(_colour_result, subset=["Result"]),
                use_container_width=True, hide_index=True,
            )

        # Pending table
        if _pending_recs:
            st.markdown("#### Pending")
            st.caption("Today's races or dates without published results yet.")
            _sorted_pending = sorted(
                _pending_recs,
                key=lambda r: (r.get("date",""), r.get("time","")),
                reverse=True,
            )
            _prows = []
            for _r in _sorted_pending:
                _conf_v = _r.get("confidence")
                try:
                    _conf_disp = f"{float(_conf_v):.1%}" if _conf_v is not None else "—"
                except Exception:
                    _conf_disp = "—"
                _prows.append({
                    "Date":       _r.get("date", ""),
                    "Time":       _r.get("time", ""),
                    "Course":     _r.get("course", ""),
                    "Horse":      _r.get("runner", ""),
                    "Confidence": _conf_disp,
                    "Odds":       str(_r.get("odds", "")),
                    "Status":     "⏳ Pending",
                })
            st.dataframe(pd.DataFrame(_prows), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.caption(
        f"{_total_recs} recommendations logged, {len(_settled_recs)} settled, {len(_pending_recs)} pending. "
        f"P&L uses £{STAKE:.0f} flat win stake per selection."
    )


# ── Tab 8: Odds Comparison ────────────────────────────────────
with tab8:
    st.markdown("### 📉 Odds Comparison")
    st.caption("Current prices for today's qualifying selections from the Sporting Life live feed.")

    if not _six_pool:
        st.info("No qualifying selections — check back once today's markets are live.")
    else:
        _oc_rows = []
        for _s in _six_pool:
            _c    = float(_s["confidence"])
            _dec  = float(_s["decimal"])
            _evv  = (_c * _dec) - (1 - _c)
            _oc_rows.append({
                "Horse":         _s["horse"],
                "Course":        _s["course"],
                "Time":          _s["time"],
                "Current Price": _s["odds_str"],
                "Decimal":       f"{_dec:.2f}x",
                "Role":          _s["tier"],
                "EV":            round(_evv, 3),
            })
        _oc_rows.sort(key=lambda r: (r["Time"], r["Course"]))
        st.dataframe(pd.DataFrame(_oc_rows), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.info(
        "Odds sourced from Sporting Life live feed. Best odds comparison requires "
        "additional bookmaker feeds — planned for Phase 2."
    )
