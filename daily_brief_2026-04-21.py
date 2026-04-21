"""
Racing Engine — Daily Brief PDF
Date: Tuesday 21 April 2026
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, HRFlowable, KeepTogether)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
import urllib.request, os

OUTPUT = "/home/user/workspace/racing_engine_daily_brief_20260421.pdf"

# ── Fonts ──────────────────────────────────────────────────────────────────────
FONT_DIR = "/home/user/workspace/fonts_brief"
os.makedirs(FONT_DIR, exist_ok=True)

def dl(url, dest):
    if not os.path.exists(dest):
        urllib.request.urlretrieve(url, dest)

dl("https://github.com/rsms/inter/releases/download/v3.19/Inter-3.19.zip",
   f"{FONT_DIR}/inter.zip")

import zipfile
with zipfile.ZipFile(f"{FONT_DIR}/inter.zip") as z:
    for name in z.namelist():
        if name.endswith(".ttf") and "Inter-Regular" in name:
            z.extract(name, FONT_DIR)
        if name.endswith(".ttf") and "Inter-Bold" in name:
            z.extract(name, FONT_DIR)
        if name.endswith(".ttf") and "Inter-Medium" in name:
            z.extract(name, FONT_DIR)

# Find extracted files
import glob
reg = glob.glob(f"{FONT_DIR}/**/*Inter-Regular*", recursive=True)
bold = glob.glob(f"{FONT_DIR}/**/*Inter-Bold*", recursive=True)
med  = glob.glob(f"{FONT_DIR}/**/*Inter-Medium*", recursive=True)

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

if reg:  pdfmetrics.registerFont(TTFont("Inter",        reg[0]))
if bold: pdfmetrics.registerFont(TTFont("Inter-Bold",   bold[0]))
if med:  pdfmetrics.registerFont(TTFont("Inter-Medium", med[0]))

FONT      = "Inter"       if reg  else "Helvetica"
FONT_BOLD = "Inter-Bold"  if bold else "Helvetica-Bold"
FONT_MED  = "Inter-Medium"if med  else "Helvetica"

# ── Palette ────────────────────────────────────────────────────────────────────
TEAL     = colors.HexColor("#01696F")
TEAL_LT  = colors.HexColor("#E6F4F4")
DARK     = colors.HexColor("#28251D")
MUTED    = colors.HexColor("#7A7974")
BORDER   = colors.HexColor("#D4D1CA")
BG       = colors.HexColor("#F7F6F2")
WHITE    = colors.white
WIN_GRN  = colors.HexColor("#437A22")
LOSS_RED = colors.HexColor("#A13544")
WARN_AMB = colors.HexColor("#964219")

# ── Page setup ─────────────────────────────────────────────────────────────────
W, H = A4
MARGIN = 18*mm
doc = SimpleDocTemplate(
    OUTPUT, pagesize=A4,
    leftMargin=MARGIN, rightMargin=MARGIN,
    topMargin=22*mm, bottomMargin=18*mm,
    title="Racing Engine Daily Brief — 21 April 2026",
    author="Perplexity Computer"
)

# ── Styles ─────────────────────────────────────────────────────────────────────
def S(name, **kw):
    base = getSampleStyleSheet()["Normal"]
    defaults = dict(fontName=FONT, fontSize=9, textColor=DARK, leading=14)
    defaults.update(kw)
    return ParagraphStyle(name, parent=base, **defaults)

H1   = S("H1",  fontName=FONT_BOLD, fontSize=20, textColor=TEAL, leading=26, spaceAfter=2)
H2   = S("H2",  fontName=FONT_BOLD, fontSize=12, textColor=DARK, leading=18, spaceBefore=10, spaceAfter=4)
H3   = S("H3",  fontName=FONT_MED,  fontSize=10, textColor=TEAL, leading=15, spaceBefore=6, spaceAfter=2)
BODY = S("BODY",fontSize=9, leading=14)
BODY_MUTED = S("BM", fontSize=8, textColor=MUTED, leading=13)
CAPTION = S("CAP", fontSize=7.5, textColor=MUTED, leading=12, alignment=TA_CENTER)
SMALL = S("SM", fontSize=8, leading=13)
TH   = S("TH",  fontName=FONT_BOLD, fontSize=8, textColor=WHITE, alignment=TA_CENTER, leading=12)
TD   = S("TD",  fontSize=8, leading=12, alignment=TA_CENTER)
TD_L = S("TDL", fontSize=8, leading=12, alignment=TA_LEFT)
RULE_S = S("RS", fontName=FONT_BOLD, fontSize=8, textColor=TEAL, alignment=TA_CENTER, leading=12)

W_BODY = W - 2*MARGIN

# ── Helpers ────────────────────────────────────────────────────────────────────
def hr(color=BORDER, thickness=0.5, spB=4, spA=4):
    return HRFlowable(width="100%", thickness=thickness, color=color,
                      spaceAfter=spA, spaceBefore=spB)

def kpi_table(items):
    """items = list of (label, value, color)"""
    col_w = W_BODY / len(items)
    data = [[Paragraph(v, ParagraphStyle("kv", fontName=FONT_BOLD, fontSize=16,
                        textColor=c, leading=20, alignment=TA_CENTER))
             for _, v, c in items],
            [Paragraph(l, ParagraphStyle("kl", fontName=FONT, fontSize=7.5,
                        textColor=MUTED, leading=11, alignment=TA_CENTER))
             for l, _, _ in items]]
    t = Table([data[0], data[1]], colWidths=[col_w]*len(items))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), BG),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING",   (0,0), (-1,-1), 4),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4),
        ("LINEBELOW", (0,0), (-1,0), 0.3, BORDER),
        ("ROUNDEDCORNERS", [3]),
    ]))
    return t

# ── Data ───────────────────────────────────────────────────────────────────────
# Today's qualifying selections at 55% threshold (final pre-race)
SELECTIONS = [
    # horse, course, time, best_odds, curr_odds, conf, tier, result
    ("Lady Youmzain",   "Pontefract",    "14:17", "5/6",  "5/6",  "71.9%", "BANKER",  "PENDING"),
    ("Brilliant Star",  "Yarmouth",      "14:35", "2/9",  "8/11", "57.2%", "BANKER",  "PENDING"),
    ("Yorkshire Glory", "Pontefract",    "16:02", "9/4",  "7/4",  "68.4%", "MID",     "PENDING"),
    ("Crystal Island",  "Ffos Las",      "16:38", "4/6",  "6/4",  "65.0%", "BANKER",  "PENDING"),
    ("Beaune",          "Wolverhampton", "18:30", "9/4",  "6/4",  "59.8%", "MID",     "PENDING"),
]

RESULTS = [
    # race_time, course, winner, sp, note
    ("13:42", "Pontefract", "Margaret's Pearl", "11/2", "Not a selection"),
    ("14:00", "Yarmouth",   "Siouxperb",        "4/6",  "Not a selection"),
]

# ── Build content ──────────────────────────────────────────────────────────────
story = []

# ── HEADER ────────────────────────────────────────────────────────────────────
story.append(Paragraph("Racing Engine", H1))
story.append(Paragraph(
    "Daily Brief &amp; Session Review — Tuesday 21 April 2026",
    S("sub", fontName=FONT_MED, fontSize=11, textColor=MUTED, leading=16)
))
story.append(Spacer(1, 3*mm))
story.append(hr(TEAL, thickness=1.5, spB=0, spA=6))

# ── KPIs ──────────────────────────────────────────────────────────────────────
story.append(kpi_table([
    ("Qualifying Selections", "5", TEAL),
    ("Confidence Range", "57–72%", DARK),
    ("Threshold Used", "55%", WARN_AMB),
    ("Accumulator Legs", "5", DARK),
    ("Lucky 15", "Unlocked", WIN_GRN),
]))
story.append(Spacer(1, 4*mm))

# ── TODAY'S SELECTIONS ────────────────────────────────────────────────────────
story.append(Paragraph("Today's Qualifying Selections", H2))
story.append(Paragraph(
    "All 5 horses cleared the 55% confidence threshold and the 4/6 (1.67 decimal) short-price cut-off. "
    "Results marked PENDING — update tomorrow once all races have run.",
    BODY_MUTED
))
story.append(Spacer(1, 2*mm))

sel_header = [Paragraph(t, TH) for t in
              ["Time", "Horse", "Course", "Best Odds", "Curr Odds", "Conf", "Tier", "Result"]]
sel_rows = [sel_header]
for horse, course, time, bo, co, conf, tier, result in SELECTIONS:
    rc = WIN_GRN if result == "WON" else LOSS_RED if result == "LOST" else MUTED
    sel_rows.append([
        Paragraph(time, TD),
        Paragraph(f"<b>{horse}</b>", TD_L),
        Paragraph(course, TD),
        Paragraph(bo, TD),
        Paragraph(co, TD),
        Paragraph(conf, ParagraphStyle("cp", fontName=FONT_BOLD, fontSize=8,
                   textColor=TEAL, alignment=TA_CENTER, leading=12)),
        Paragraph(tier, RULE_S),
        Paragraph(result, ParagraphStyle("rp", fontName=FONT_BOLD, fontSize=8,
                   textColor=rc, alignment=TA_CENTER, leading=12)),
    ])

sel_cw = [14*mm, 38*mm, 28*mm, 20*mm, 20*mm, 16*mm, 18*mm, 20*mm]
sel_t = Table(sel_rows, colWidths=sel_cw, repeatRows=1)
sel_t.setStyle(TableStyle([
    ("BACKGROUND",    (0,0), (-1,0), TEAL),
    ("BACKGROUND",    (0,1), (-1,1), TEAL_LT),
    ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, TEAL_LT]),
    ("GRID",          (0,0), (-1,-1), 0.3, BORDER),
    ("TOPPADDING",    (0,0), (-1,-1), 5),
    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ("LEFTPADDING",   (0,0), (-1,-1), 5),
    ("RIGHTPADDING",  (0,0), (-1,-1), 5),
    ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ("LINEBELOW",     (0,0), (-1,0), 1, TEAL),
]))
story.append(sel_t)
story.append(Spacer(1, 2*mm))
story.append(Paragraph(
    "Note: Results column shows PENDING — fill in after racing. Update the brief tomorrow with actuals to build the learning log.",
    CAPTION
))
story.append(Spacer(1, 4*mm))

# ── STAKING PLAN ─────────────────────────────────────────────────────────────
story.append(Paragraph("Staking Plan (£50 Budget)", H2))
stk_data = [
    [Paragraph(h, TH) for h in ["Bet Type", "Stake", "Horses", "Projected Return"]],
    [Paragraph("5-fold Accumulator", TD_L),
     Paragraph("£30.00", TD),
     Paragraph("All 5", TD),
     Paragraph("~£207 (approx — depends on SPs)", TD)],
    [Paragraph("Lucky 15", TD_L),
     Paragraph("£20.00 (£1.33/bet × 15)", TD),
     Paragraph("All 5", TD),
     Paragraph("Covers singles + doubles + trebles + 4-fold + 5-fold", TD)],
]
stk_cw = [45*mm, 35*mm, 25*mm, W_BODY - 105*mm]
stk_t = Table(stk_data, colWidths=stk_cw)
stk_t.setStyle(TableStyle([
    ("BACKGROUND",    (0,0), (-1,0), TEAL),
    ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, TEAL_LT]),
    ("GRID",          (0,0), (-1,-1), 0.3, BORDER),
    ("TOPPADDING",    (0,0), (-1,-1), 5),
    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ("LEFTPADDING",   (0,0), (-1,-1), 5),
    ("RIGHTPADDING",  (0,0), (-1,-1), 5),
    ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
]))
story.append(stk_t)
story.append(Spacer(1, 4*mm))

# ── EARLY RESULTS ─────────────────────────────────────────────────────────────
story.append(Paragraph("Early Race Results (pre-selection races)", H2))
story.append(Paragraph(
    "These races ran before our selections were finalised. Not selections — included for context.",
    BODY_MUTED
))
story.append(Spacer(1, 2*mm))
res_data = [
    [Paragraph(h, TH) for h in ["Time", "Course", "Winner", "SP", "Note"]],
]
for rt, rc_, rw, rs, rn in RESULTS:
    res_data.append([
        Paragraph(rt, TD), Paragraph(rc_, TD), Paragraph(f"<b>{rw}</b>", TD_L),
        Paragraph(rs, TD), Paragraph(rn, BODY_MUTED)
    ])
res_cw = [14*mm, 28*mm, 40*mm, 18*mm, W_BODY - 100*mm]
res_t = Table(res_data, colWidths=res_cw)
res_t.setStyle(TableStyle([
    ("BACKGROUND",    (0,0), (-1,0), TEAL),
    ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, TEAL_LT]),
    ("GRID",          (0,0), (-1,-1), 0.3, BORDER),
    ("TOPPADDING",    (0,0), (-1,-1), 5),
    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ("LEFTPADDING",   (0,0), (-1,-1), 5),
    ("RIGHTPADDING",  (0,0), (-1,-1), 5),
    ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
]))
story.append(res_t)
story.append(Spacer(1, 4*mm))

# ── SESSION REVIEW ────────────────────────────────────────────────────────────
story.append(hr(spB=2, spA=6))
story.append(Paragraph("Session Review — What We Built Today", H2))

review_items = [
    ("v2.4 — Staking Plan",
     "Added a full budget-derived staking plan to Tab 1. Budget set in sidebar (default £50). "
     "If 4+ horses qualify, splits 60% accumulator / 40% Lucky 15. If fewer than 4, 100% on accumulator."),
    ("v2.4.1 — Streamlit Cache Fix",
     "Tab 1 was iterating over _live_df — a variable bound once at app startup that never refreshed. "
     "Changed to iterate over _t1_df from a direct load_live_selections() call inside Tab 1. "
     "The Refresh button now correctly clears the cache and reloads data."),
    ("v2.4.1 — Version Stamp Fix",
     "Sidebar was stuck showing v2.3.8 because the version string was never updated across releases. "
     "Now shows correct version and forces Streamlit to acknowledge the redeploy."),
    ("v2.4.2 — Confidence Slider Improved",
     "Slider now snaps to clean 5% increments (55/60/65/70/75/80%). "
     "Status label shows Relaxed / Standard / Tight dynamically as you drag."),
    ("v2.4.3 — Default Threshold Set to 55%",
     "At your instruction the default confidence threshold was lowered from 60% to 55%. "
     "This unlocks 5 selections today (vs 3 at 60%) and triggers the Lucky 15."),
]

for title, detail in review_items:
    story.append(KeepTogether([
        Paragraph(f"<b>{title}</b>", SMALL),
        Paragraph(detail, BODY_MUTED),
        Spacer(1, 3*mm),
    ]))

# ── WHY ONLY 3 SHOWED (diagnosis) ─────────────────────────────────────────────
story.append(hr(spB=2, spA=6))
story.append(Paragraph("Diagnostic: Why 3 Selections Appeared Initially", H2))
story.append(Paragraph(
    "The engine was showing 3 selections (Crystal Island, Yorkshire Glory, Lady Youmzain) "
    "instead of the expected 7. Two separate issues were responsible:",
    BODY
))
story.append(Spacer(1, 2*mm))

diag_data = [
    [Paragraph(h, TH) for h in ["Issue", "Root Cause", "Fix Applied"]],
    [Paragraph("Stale cache", TD_L),
     Paragraph("Tab 1 looped over top-level _live_df (bound at app startup, never refreshed). "
               "Refresh button had no effect.", TD_L),
     Paragraph("Tab 1 now calls load_live_selections() directly as _t1_df.", TD_L)],
    [Paragraph("Odds movement", TD_L),
     Paragraph("Final Appeal, Trust House, Beaune, Brilliant Star all scored below 60% "
               "at 14:04 BST as market odds had moved since the 13:49 snap.", TD_L),
     Paragraph("Threshold lowered to 55% (user decision). "
               "Beaune (59.8%) and Brilliant Star (57.2%) re-entered the pool.", TD_L)],
]
diag_cw = [30*mm, 70*mm, W_BODY - 100*mm]
diag_t = Table(diag_data, colWidths=diag_cw, repeatRows=1)
diag_t.setStyle(TableStyle([
    ("BACKGROUND",    (0,0), (-1,0), TEAL),
    ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, TEAL_LT]),
    ("GRID",          (0,0), (-1,-1), 0.3, BORDER),
    ("TOPPADDING",    (0,0), (-1,-1), 5),
    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ("LEFTPADDING",   (0,0), (-1,-1), 5),
    ("RIGHTPADDING",  (0,0), (-1,-1), 5),
    ("VALIGN",        (0,0), (-1,-1), "TOP"),
]))
story.append(diag_t)
story.append(Spacer(1, 4*mm))

# ── LEARNING LOG ──────────────────────────────────────────────────────────────
story.append(hr(spB=2, spA=6))
story.append(Paragraph("Learning Log — Results vs Selections", H2))
story.append(Paragraph(
    "This section is the core of tomorrow's brief. After racing, fill in the Result column above "
    "then review the following questions to improve the model over time.",
    BODY_MUTED
))
story.append(Spacer(1, 2*mm))

learn_data = [
    [Paragraph(h, TH) for h in ["Question", "What to Check", "Action if Needed"]],
    [Paragraph("Did high-confidence horses win?", TD_L),
     Paragraph("Yorkshire Glory (68.4%) and Lady Youmzain (71.9%) are the bankers. "
               "If they lost, check if odds drifted (signal = market doubt).", TD_L),
     Paragraph("If bankers fail repeatedly, review form weight (currently 35%).", TD_L)],
    [Paragraph("Did low-confidence horses win?", TD_L),
     Paragraph("Brilliant Star (57.2%) and Beaune (59.8%) are the borderline picks. "
               "If these win and bankers lose, 55% threshold may be too conservative.", TD_L),
     Paragraph("Consider lowering to 52% or reviewing the odds-movement penalty.", TD_L)],
    [Paragraph("Did non-selections win their race?", TD_L),
     Paragraph("Cross-check race winners vs horses that scored 45-55%. "
               "If 2+ winners sat just below our cut-off, the model is filtering too aggressively.", TD_L),
     Paragraph("Log the runner, their score, and what signals they had.", TD_L)],
    [Paragraph("tf_stars accuracy check", TD_L),
     Paragraph("Crystal Island and Lady Youmzain are both tf_stars=5. "
               "Note whether Timeform's top pick for each race won.", TD_L),
     Paragraph("Track over 30+ races. If tf_stars=5 wins <40% of races, reduce its weight from 20%.", TD_L)],
    [Paragraph("Odds drift accuracy", TD_L),
     Paragraph("Brilliant Star shortened from 3/10 → 8/11 (confidence dropped). "
               "Crystal Island drifted from 4/6 → 6/4 (confidence held at 65%). "
               "Check if drifters won or lost.", TD_L),
     Paragraph("If drifters consistently lose, increase the current_odds penalty weight.", TD_L)],
]
learn_cw = [38*mm, 65*mm, W_BODY - 103*mm]
learn_t = Table(learn_data, colWidths=learn_cw, repeatRows=1)
learn_t.setStyle(TableStyle([
    ("BACKGROUND",    (0,0), (-1,0), TEAL),
    ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, TEAL_LT]),
    ("GRID",          (0,0), (-1,-1), 0.3, BORDER),
    ("TOPPADDING",    (0,0), (-1,-1), 5),
    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ("LEFTPADDING",   (0,0), (-1,-1), 5),
    ("RIGHTPADDING",  (0,0), (-1,-1), 5),
    ("VALIGN",        (0,0), (-1,-1), "TOP"),
]))
story.append(learn_t)
story.append(Spacer(1, 4*mm))

# ── LOOSE ENDS / TOMORROW ─────────────────────────────────────────────────────
story.append(hr(spB=2, spA=6))
story.append(Paragraph("Loose Ends &amp; Tomorrow's Tasks", H2))

tomorrow = [
    ("[PRIORITY 1] Fill results", "Update today's 5 selections with Win/Place/Lost once all races run. "
      "Note SPs, positions, and any notable market movements."),
    ("[PRIORITY 2] Form string review", "Final Appeal (form: '2') and Trust House (form: '8123-51') scored below 60% "
      "despite tf_stars=5. The form parser may be under-weighting single-run horses. Review form scoring logic."),
    ("[PRIORITY 2] Confidence calibration check", "Once results are in: did scores above 65% win more often than 55-60%? "
      "Build a simple hit-rate table — we need 30+ data points before adjusting weights."),
    ("[PRIORITY 3] Daily brief automation", "Build a cron job to auto-generate this PDF each evening at 19:00 BST "
      "with results filled in from the results feed, ready to review."),
    ("[PRIORITY 3] Version stamp automation", "Add a script that auto-increments the version string on every push "
      "so it never gets stuck at an old number again."),
    ("[FUTURE] Phase 2", "Commercial platform planning — not until Phase 1 model is stable over 4+ weeks."),
]

for icon_title, detail in tomorrow:
    story.append(KeepTogether([
        Paragraph(f"<b>{icon_title}</b>", SMALL),
        Paragraph(detail, BODY_MUTED),
        Spacer(1, 3*mm),
    ]))

# ── FOOTER ────────────────────────────────────────────────────────────────────
story.append(hr(spB=4, spA=4))
story.append(Paragraph(
    "Racing Engine v2.4.3 — Personal Research Tool — Phase 1 Only — Not for commercial use",
    CAPTION
))
story.append(Paragraph(
    "Dashboard: https://racing-engine-dash.streamlit.app  |  Repo: github.com/westham123/racing-engine",
    CAPTION
))

# ── Build ──────────────────────────────────────────────────────────────────────
doc.build(story)
print(f"PDF written to {OUTPUT}")
