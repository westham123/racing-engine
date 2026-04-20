# Racing Engine — Visual Dashboard
# Version: 0.3 — PIN lock added
# Built with Streamlit
# Date: 20 April 2026

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, date

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
        unlock = st.button("Unlock", use_container_width=True, type="primary")
        if unlock or (len(pin_input) == 4):
            if pin_input == CORRECT_PIN:
                st.session_state.unlocked = True
                st.rerun()
            elif len(pin_input) == 4:
                st.error("Incorrect PIN. Please try again.")
    st.stop()

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
    st.markdown("🟢 Betfair Exchange — *connected*")
    st.markdown("🟢 BHA Going Reports — *live*")
    st.markdown("🟢 HRI Data — *live*")
    st.markdown("---")
    st.markdown("**Engine v0.3**")
    st.markdown("GitHub: `westham123/racing-engine`")
    st.markdown("---")
    if st.button("🔒 Lock Dashboard", use_container_width=True):
        st.session_state.unlocked = False
        st.rerun()

# ── Header ────────────────────────────────────────────────────
st.markdown("# 🏇 Racing Engine Dashboard")
st.markdown("**Phase 1 — Personal Research Tool** | UK + Irish Racing")
st.markdown("---")

# ── Top KPI Metrics ───────────────────────────────────────────
col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Races Today", "12", "UK + IRE")
with col2:
    st.metric("Top Selections", "6", "Above 65% confidence")
with col3:
    st.metric("Acca Permutations", "5", "Generated")
with col4:
    st.metric("30-Day Hit Rate", "68.4%", "+2.1%")
with col5:
    st.metric("Active Alerts", "5", "2 high priority")

st.markdown("---")

# ── Main Tabs ─────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📋 Today's Selections",
    "🎰 Accumulator Permutations",
    "🚨 Live Alerts",
    "🧠 Learning Engine",
    "📊 Results History"
])

# ── Tab 1: Today's Selections ─────────────────────────────────
with tab1:
    st.markdown("### Today's Top Selections")
    st.markdown("Horses ranked by confidence score across all UK and Irish races today.")

    df = get_sample_selections()

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

    st.dataframe(styled, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("### Signal Breakdown")
    signals = pd.DataFrame({
        "Signal": ["Market Odds", "Horse Form", "Track Form", "Going", "Trainer Form", "Jockey Form", "Market Moves", "Jump Index"],
        "Weight": [0.25, 0.20, 0.15, 0.10, 0.10, 0.10, 0.07, 0.03],
        "Score (Constitution Hill)": [0.92, 0.95, 0.88, 0.85, 0.97, 0.90, 0.96, 0.88]
    })
    st.dataframe(signals.style.format({"Weight": "{:.0%}", "Score (Constitution Hill)": "{:.0%}"}),
                 use_container_width=True, hide_index=True)

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
        use_container_width=True, hide_index=True
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

# ── Tab 3: Live Alerts ────────────────────────────────────────
with tab3:
    st.markdown("### Live Alerts")
    for alert in get_sample_alerts():
        icon = "🔴" if alert["level"] == "high" else "🟠" if alert["level"] == "medium" else "🟢"
        st.markdown(
            f'<div class="alert-{alert["level"]}">{icon} <strong>{alert["time"]}</strong> — {alert["message"]}</div>',
            unsafe_allow_html=True
        )

    st.markdown("---")
    st.markdown("### Going Reports")
    st.dataframe(pd.DataFrame([
        {"Course": "Cheltenham", "Country": "🇬🇧 UK", "Going": "Good to Soft", "Updated": "13:00", "Trend": "Drying"},
        {"Course": "Leopardstown", "Country": "🇮🇪 IRE", "Going": "Soft", "Updated": "12:30", "Trend": "Stable"},
        {"Course": "Sandown", "Country": "🇬🇧 UK", "Going": "Good", "Updated": "11:45", "Trend": "Drying"},
        {"Course": "Naas", "Country": "🇮🇪 IRE", "Going": "Heavy", "Updated": "12:00", "Trend": "Easing"},
    ]), use_container_width=True, hide_index=True)

# ── Tab 4: Learning Engine ────────────────────────────────────
with tab4:
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
    ]), use_container_width=True, hide_index=True)

# ── Tab 5: Results History ────────────────────────────────────
with tab5:
    st.markdown("### Results History")
    results_df = get_sample_results()

    def colour_result(val):
        if val == "WON":
            return "background-color: #003300; color: #00ff88; font-weight: bold"
        return "background-color: #330000; color: #ff6666"

    st.dataframe(
        results_df.style.map(colour_result, subset=["Result"]).format({"Confidence": "{:.0%}"}),
        use_container_width=True, hide_index=True
    )

    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Winners", "4 / 6", "Last 2 days")
    with col2:
        st.metric("Strike Rate", "66.7%", "Last 2 days")
    with col3:
        st.metric("Best Call", "Constitution Hill 90%", "Won at 5/4")
