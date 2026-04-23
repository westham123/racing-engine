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
    st.caption("3-Bet plan: BET 1 (60%) + BET 2 (25%) + BET 3 (15%)")
    st.caption("Singles, Doubles, Lucky 15 permanently removed.")

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
    st.markdown("**Engine v2.5.27** — Filter layer: field size, dual signal, handicap uplift")
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

if _pool_is_live and len(_pool_df) > 0:
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
            'is_handicap':  bool(_prow.get('Is Handicap', False)),
        }

        if _pool_model:
            try:
                _pexcl = _pool_model.should_exclude(_prunner)
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
        if _pfav_dec < _pdec:  # we are not the favourite
            _pgap = (_pdec - _pfav_dec) / _pfav_dec
            if _pgap > _FAV_GAP_PCT:
                continue  # favourite is >35% shorter — market disagrees with us

        _six_pool.append({
            'horse':      str(_prow.get('Horse', 'Unknown')),
            'course':     str(_prow.get('Course', '')),
            'time':       _ptime,
            'odds_str':   _pdisp_odds,
            'decimal':    round(_pdec, 3),
            'confidence': round(_pconf, 3),
            'ev':         round(_pconf * _pdec - 1, 3),
            'tier':       _assign_tier(round(_pdec, 3)),
        })

    _six_pool.sort(key=lambda x: x['confidence'], reverse=True)

# NR gate — strip non-runners silently here; warning shown in Tab 1
try:
    from dashboard.live_data import get_non_runners as _get_nrs_pool
    _nr_pool_list = _get_nrs_pool()
    _nr_pool_names = {nr['Horse'].lower().strip() for nr in _nr_pool_list}
    _nr_removed_names = [s['horse'] for s in _six_pool if s['horse'].lower().strip() in _nr_pool_names]
    _six_pool = [s for s in _six_pool if s['horse'].lower().strip() not in _nr_pool_names]
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
            _sel_rows.append({
                "Time":           _s["time"],
                "Horse":          _s["horse"],
                "Course":         _s["course"],
                "Odds":           _s["odds_str"],
                "Confidence":     f"{_s['confidence']:.1%}",
                "Overnight Move": _mv_str,
                "Signal":         _s.get("signal", "Stable"),
                "Tier":           _s["tier"],
            })
        st.dataframe(pd.DataFrame(_sel_rows), use_container_width=True, hide_index=True)

        st.markdown("---")

        # ══════════════════════════════════════════════════════════════
        # STAKING ENGINE v3.0 — 3-Bet Structure
        # BET 1: Main accumulator (60%) — BANKERS ONLY (value horses isolated to BET 3)
        # BET 2: Cover accumulator (25%) — bankers only, safety net
        # BET 3: Value double (15%) — top 2 highest-EV horses (≥4x)
        # Target: £2,000+ profit, uncapped
        # ══════════════════════════════════════════════════════════════
        from engine.staking import build_staking_plan as _build_staking_plan, recommend_bet_type as _recommend_bet_type

        _budget = float(st.session_state.get("daily_budget", 100))
        _stk    = _build_staking_plan(_six_pool, budget=_budget)

        # ── Flexible Bet-Type Recommendation ─────────────────────────
        _rec = _recommend_bet_type(_six_pool)
        st.markdown("#### 🎯 Recommended Bet Structure")
        if _rec.get("default_ok"):
            st.success(
                f"**{_rec['recommendation']}** — {_rec['rationale']}"
            )
        elif _rec["recommendation"] == "Hold or Reduce Stakes":
            st.warning(
                f"**{_rec['recommendation']}** — {_rec['rationale']}"
            )
        else:
            st.info(
                f"**{_rec['recommendation']}** — {_rec['rationale']}"
            )
        if _rec.get("structure"):
            _rec_rows = []
            for _rbt in _rec["structure"]:
                _rec_rows.append({
                    "Bet":          _rbt["bet"],
                    "Legs":         _rbt["legs"],
                    "Combinations": _rbt["combinations"],
                    "Horses":       ", ".join(_rbt["horses"]) if _rbt["horses"] else "—",
                    "Stake":        f"£{_rbt['total_stake']:.2f}",
                    "Per line":     _rbt["stake_per_line"],
                })
            st.dataframe(pd.DataFrame(_rec_rows), use_container_width=True, hide_index=True)
        st.caption(
            f"Bankers: **{_rec['bankers']}** | Value horses: **{_rec['value']}** | "
            f"The structured 3-bet plan below remains shown — final call is yours."
        )

        st.markdown("---")
        st.markdown("#### 💳 Today's Staking Plan")

        # ── Plan banner ───────────────────────────────────────────────
        _pt = _stk["plan_type"]
        if _pt == "THREE_BET":
            st.success(
                f"**3-BET PLAN** — Main Acc (£{_stk['main_stake']:.2f}) + "
                f"Cover Acc (£{_stk['cover_stake']:.2f}) + "
                f"Value Double (£{_stk['double_stake']:.2f}) | "
                f"Total: **£{_budget:.2f}** | Target: **£2,000+ uncapped**"
            )
        elif _pt == "MAIN_COVER":
            st.warning(
                f"**2-BET PLAN** — Main Acc (£{_stk['main_stake']:.2f}) + "
                f"Cover Acc (£{_stk['cover_stake']:.2f}) | "
                f"No value horses today for double | Total: **£{_budget:.2f}**"
            )
        elif _pt == "MAIN_ONLY":
            st.info(
                f"**MAIN ACC ONLY** — £{_stk['main_stake']:.2f} on {len(_stk['main_pool'])}-fold accumulator | "
                f"Bankers only today, no cover or double needed"
            )
        else:
            st.info(f"**FULL ACCUMULATOR** — £{_stk['main_stake']:.2f} | {_stk['plan_label']}")

        # ── KPI row ───────────────────────────────────────────────────
        _k1, _k2, _k3, _k4 = st.columns(4)
        _k1.metric("Budget",          f"£{_stk['budget']:.2f}")
        _k2.metric("BET 1 — Main Acc", f"£{_stk['main_stake']:.2f}",
                   delta=f"Returns £{_stk['main_return']:,.0f}" if _stk['main_return'] else None)
        _k3.metric("BET 2 — Cover Acc",
                   f"£{_stk['cover_stake']:.2f}" if _stk["cover_pool"] else "—",
                   delta=f"Returns £{_stk['cover_return']:,.0f}" if _stk['cover_return'] else None)
        _k4.metric("BET 3 — Value Double",
                   f"£{_stk['double_stake']:.2f}" if _stk["double_pool"] else "—",
                   delta=f"Returns £{_stk['double_return']:,.0f}" if _stk['double_return'] else None)

        st.markdown("---")

        # ── BET 1: Main accumulator ───────────────────────────────────
        st.markdown("#### 🎰 BET 1 — Main Accumulator — Bankers Only (60% budget)")
        _b1_rows = []
        for _i, _s in enumerate(_stk["main_pool"]):
            _b1_rows.append({
                "#":          _i + 1,
                "Time":       _s["time"],
                "Horse":      _s["horse"],
                "Course":     _s["course"],
                "Odds":       _s.get("odds_str", f"{_s['decimal']:.2f}x"),
                "Decimal":    f"{_s['decimal']:.2f}x",
                "Confidence": f"{_s['confidence']:.1%}",
                "Tier":       _s.get("tier", "BANKER"),
            })
        if _b1_rows:
            st.dataframe(pd.DataFrame(_b1_rows), use_container_width=True, hide_index=True)
            st.success(
                f"Stake **£{_stk['main_stake']:.2f}** | "
                f"{len(_stk['main_pool'])}-fold @ **{_stk['main_dec']:,.1f}x** | "
                f"Return if wins: **£{_stk['main_return']:,.2f}**"
            )
        else:
            st.warning("No horses qualified for the main accumulator today.")

        # ── BET 2: Cover accumulator ──────────────────────────────────
        st.markdown("#### 🛡️ BET 2 — Cover Accumulator (25% budget)")
        if _stk["cover_pool"]:
            _b2_rows = []
            for _i, _s in enumerate(_stk["cover_pool"]):
                _b2_rows.append({
                    "#":          _i + 1,
                    "Time":       _s["time"],
                    "Horse":      _s["horse"],
                    "Course":     _s["course"],
                    "Odds":       _s.get("odds_str", f"{_s['decimal']:.2f}x"),
                    "Decimal":    f"{_s['decimal']:.2f}x",
                    "Confidence": f"{_s['confidence']:.1%}",
                    "Tier":       "BANKER",
                })
            st.dataframe(pd.DataFrame(_b2_rows), use_container_width=True, hide_index=True)
            # Identify which banker was omitted (highest-priced in BET 1 not in BET 2)
            _b1_horses = {s['horse'] for s in _stk['main_pool']}
            _b2_horses = {s['horse'] for s in _stk['cover_pool']}
            _omitted   = _b1_horses - _b2_horses
            _omit_str  = f" | Omits: **{', '.join(_omitted)}** (riskiest leg)" if _omitted else ""
            st.info(
                f"Stake **£{_stk['cover_stake']:.2f}** | "
                f"{len(_stk['cover_pool'])}-fold @ **{_stk['cover_dec']:,.1f}x** | "
                f"Return if wins: **£{_stk['cover_return']:,.2f}**"
                f"{_omit_str} | "
                f"Lands if BET 1's riskiest horse fails"
            )
        else:
            st.caption("No cover accumulator today — not enough bankers for a separate safety net.")

        # ── BET 3: Value double ───────────────────────────────────────
        st.markdown("#### 💎 BET 3 — Value Double (15% budget)")
        if _stk["double_pool"]:
            _b3_rows = []
            for _i, _s in enumerate(_stk["double_pool"]):
                _b3_rows.append({
                    "#":          _i + 1,
                    "Time":       _s["time"],
                    "Horse":      _s["horse"],
                    "Course":     _s["course"],
                    "Odds":       _s.get("odds_str", f"{_s['decimal']:.2f}x"),
                    "Decimal":    f"{_s['decimal']:.2f}x",
                    "Confidence": f"{_s['confidence']:.1%}",
                    "Tier":       "VALUE",
                })
            st.dataframe(pd.DataFrame(_b3_rows), use_container_width=True, hide_index=True)
            st.info(
                f"Stake **£{_stk['double_stake']:.2f}** | "
                f"Double @ **{_stk['double_dec']:,.1f}x** | "
                f"Return if wins: **£{_stk['double_return']:,.2f}** | "
                f"High-value horses — price ≥4x, conf ≥55%"
            )
        else:
            st.caption("No value double today — need 2 horses at ≥4x odds and ≥55% confidence.")

        st.markdown("---")

        # ── Scenario table ────────────────────────────────────────────
        st.markdown("#### 📊 P&L Scenarios")
        st.caption(f"What happens to your £{_stk['budget']:.2f} budget in key win/loss combinations.")
        _scen_rows = []
        for _sc in _stk["scenarios"]:
            _scen_rows.append({
                "Scenario":      _sc.get("Scenario", ""),
                "BET 1 Return":  _sc.get("Acc Return", "—"),
                "BET 2 Return":  _sc.get("Cover Return", "n/a"),
                "BET 3 Return":  _sc.get("Double Return", "n/a"),
                "Total Back":    _sc.get("Total Back", "£0.00"),
                "Net P&L":       _sc.get("Net P&L", "£0.00"),
            })
        _scen_df = pd.DataFrame(_scen_rows)

        def _colour_pnl(val):
            try:
                v = float(str(val).replace("£","").replace("+","").replace(",",""))
                if v > 0:  return "color: green; font-weight: bold"
                if v < 0:  return "color: red"
                return ""
            except Exception:
                return ""

        if not _scen_df.empty:
            st.dataframe(
                _scen_df.style.map(_colour_pnl, subset=["Net P&L"]),
                use_container_width=True, hide_index=True
            )

        # ── Rationale ─────────────────────────────────────────────────
        with st.expander("Why this plan? (click to expand)"):
            st.markdown(f"""
**{_stk['plan_label']}** — {_stk['plan_rationale']}

**3-Bet structure:**
- **BET 1 — Main Accumulator (60%):** Bankers only (conf ≡61%, price ≤4x). Profit engine.
- **BET 2 — Cover Accumulator (25%):** All bankers minus riskiest leg. Safety net.
- **BET 3 — Value Double (15%):** Top 2 value horses (≥4x, ≥55% conf). Independent high-reward bet.

**Exclusion rules:** 4/6 cut-off | Favourite gap >35% | Fields 16+ | Singles/Lucky 15 permanently removed.
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
        df = pd.DataFrame([{
            'Time':        s['time'],
            'Course':      s['course'],
            'Horse':       s['horse'],
            'Odds':        s['odds_str'],
            'Decimal':     f"{s['decimal']:.2f}x",
            'Confidence':  s['confidence'],
            'Signal':      s.get('signal', 'Stable'),
            'Tier':        s['tier'],
            'EV':          round(s['ev'], 3),
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
                ("Market Odds",  0.25, breakdown.get("market_odds",  "—")),
                ("Horse Form",   0.20, breakdown.get("horse_form",   "—")),
                ("Track Form",   0.15, breakdown.get("track_form",   "—")),
                ("Going",        0.10, breakdown.get("going",        "—")),
                ("Trainer Form", 0.10, breakdown.get("trainer_form", "—")),
                ("Jockey Form",  0.10, breakdown.get("jockey_form",  "—")),
                ("Market Moves", 0.10, breakdown.get("market_moves", "—")),
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
    st.caption("Weights last manually calibrated — 21 April 2026. Self-adjustment begins at 20 settled races.")

    import json as _learn_json
    _weights_path = os.path.join(os.path.dirname(__file__), "..", "learning", "learned_weights.json")
    _recs_path    = os.path.join(os.path.dirname(__file__), "..", "learning", "recommendations.json")
    _settled_path = os.path.join(os.path.dirname(__file__), "..", "learning", "settled_races.json")

    _current_weights = {
        "market_odds":  0.25, "horse_form":   0.20, "track_form":   0.15,
        "going":        0.10, "trainer_form": 0.10, "jockey_form":  0.10,
        "market_moves": 0.10,
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
    st.caption("Weights last manually calibrated — 21 April 2026. Self-adjustment begins at 20 settled races.")

# ── Tab 7: Results History ────────────────────────────────────
with tab7:
    st.markdown("### 📊 Results History")
    st.caption("Every logged recommendation. Results feed back automatically each evening after 18:30 BST.")

    import json as _res_json
    _r7_path = os.path.join(os.path.dirname(__file__), "..", "learning", "recommendations.json")
    _records = []
    try:
        if os.path.exists(_r7_path):
            with open(_r7_path) as _rf:
                _rd = _res_json.load(_rf)
            _records = _rd.get("records", []) if isinstance(_rd, dict) else []
    except Exception:
        _records = []

    _total_recs    = len(_records)
    _settled_recs  = [r for r in _records if r.get("won") is not None]
    _pending_recs  = [r for r in _records if r.get("won") is None]
    _wins_recs     = [r for r in _settled_recs if r.get("won")]

    _k1, _k2, _k3 = st.columns(3)
    _k1.metric("Recommendations Logged", str(_total_recs))
    _k2.metric("Settled",                str(len(_settled_recs)))
    _k3.metric("Pending",                str(len(_pending_recs)))

    st.markdown("---")

    if not _records:
        st.info("No results recorded yet — results feed back automatically each evening after 18:30 BST.")
    else:
        _row_sorted = sorted(_records, key=lambda r: (r.get("date",""), r.get("time","")), reverse=True)
        _res_rows = []
        for _r in _row_sorted:
            _won = _r.get("won")
            if _won is True:
                _result = "✅ WON"
            elif _won is False:
                _result = "❌ LOST"
            else:
                _result = "⏳ Pending"
            _conf_v = _r.get("confidence")
            try:
                _conf_disp = f"{float(_conf_v):.1%}" if _conf_v is not None else "—"
            except Exception:
                _conf_disp = "—"
            _res_rows.append({
                "Date":   _r.get("date", ""),
                "Horse":  _r.get("runner", ""),
                "Course": _r.get("course", ""),
                "Time":   _r.get("time", ""),
                "Odds":   str(_r.get("odds", "")),
                "Conf":   _conf_disp,
                "Result": _result,
            })
        _res_df7 = pd.DataFrame(_res_rows)

        def _colour_result(val):
            s = str(val)
            if "WON" in s:      return "background-color: #003300; color: #00ff88; font-weight: bold"
            if "LOST" in s:     return "background-color: #330000; color: #ff6666"
            if "Pending" in s:  return "color: #ffaa00"
            return ""

        st.dataframe(
            _res_df7.style.map(_colour_result, subset=["Result"]),
            use_container_width=True, hide_index=True
        )

        if _wins_recs:
            _hit_rate = len(_wins_recs) / len(_settled_recs) * 100
            st.caption(f"Hit rate: **{_hit_rate:.1f}%** across {len(_settled_recs)} settled recommendations.")

    st.markdown("---")
    st.caption(
        f"{_total_recs} recommendations logged, {len(_settled_recs)} settled, {len(_pending_recs)} pending."
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
