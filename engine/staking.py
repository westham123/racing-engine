# Racing Engine — Staking Engine v2.0
# Updated: 22 April 2026
#
# PHILOSOPHY:
#   Three-bet structure designed to maximise profit on £100 budget
#   with a target of £2,000+ profit. No singles. No Lucky 15.
#
# STRUCTURE:
#   BET 1 — Main Accumulator (60% of budget)
#     BANKERS ONLY (conf >= 63%, price <= 4.0x). No value horses.
#     Lesson: outlier prices (4x+) destroy acca probability.
#     This is the profit engine. Targets £2,000+ return.
#
#   BET 2 — Cover Accumulator (25% of budget)
#     All bankers MINUS the highest-priced one (the riskiest leg).
#     If BET 1's riskiest banker fails, BET 2 still lands.
#     Genuinely different from BET 1 — not a duplicate.
#
#   BET 3 — Value Double (15% of budget)
#     The two highest-EV selections (price >= 4.0x).
#     ~1 in 3 chance of landing. Returns independently of accas.
#     If fewer than 2 value horses exist, stake rolls into main acc.
#
# CLASSIFICATION:
#   BANKER  : conf >= 63% AND price <= 4.0x  — core accumulator legs
#   VALUE   : price >= 4.0x AND conf >= 55%  — high EV, goes in main + double
#   WEAK    : conf < 63% AND price < 4.0x    — excluded from all bets
#
# ONE-HORSE-PER-RACE: enforced upstream. Engine assumes pool is pre-filtered.
# NR GATE: enforced upstream. Engine assumes pool contains no non-runners.

from itertools import combinations as _combs


# ── Top trainers whose presence in a race is a WARNING flag (not auto-exclude)
# If one of these trainers has a runner in the same race as our selection
# (and it's NOT our selection), flag it — serious intent from a top yard.
TOP_RIVAL_TRAINERS = [
    "henderson", "mullins", "o'brien", "elliott", "nicholls",
    "o'neill", "stoute", "gosden", "appleby",
]


def detect_rival_top_trainer(our_horse: str, race_runners: list) -> dict:
    """
    Scan the other runners in a race for a top-tier trainer.
    Returns dict: {"rival_top_trainer": bool, "rival_trainer_name": str}.

    our_horse      : the horse name we selected (case-insensitive compare)
    race_runners   : list of runner dicts containing "horse" and "trainer" keys

    If trainer data is missing per runner, silently returns False/"" —
    this is a best-effort warning, never an automatic exclusion.
    """
    result = {"rival_top_trainer": False, "rival_trainer_name": ""}
    if not race_runners or not our_horse:
        return result
    our_name = str(our_horse).lower().strip()
    for rn in race_runners:
        try:
            rn_horse   = str(rn.get("horse", "")).lower().strip()
            rn_trainer = str(rn.get("trainer", "")).lower().strip()
        except Exception:
            continue
        if not rn_trainer or rn_horse == our_name:
            continue
        for top in TOP_RIVAL_TRAINERS:
            if top in rn_trainer:
                # Preserve original casing from the feed for display
                original = rn.get("trainer", "") or top.title()
                return {"rival_top_trainer": True, "rival_trainer_name": original}
    return result


# ── Thresholds ────────────────────────────────────────────────────────────────
BANKER_CONF      = 0.63    # minimum confidence to be a banker leg
BANKER_MAX_PRICE = 4.00    # maximum price to be a banker leg
VALUE_MIN_PRICE  = 4.00    # minimum price to be a value leg
VALUE_MIN_CONF   = 0.55    # minimum confidence to be a value leg

MAIN_PCT   = 0.60          # 60% of budget on main accumulator
COVER_PCT  = 0.25          # 25% of budget on cover accumulator
DOUBLE_PCT = 0.15          # 15% of budget on value double


def recommend_bet_type(selections: list) -> dict:
    """
    Evaluate today's card and recommend the optimal bet structure.

    The 3-bet plan remains the DEFAULT — this function layers an additional
    recommendation on top so the user can choose between the structured plan
    and alternative shapes (Lucky 15 / Lucky 31 / Lucky 63 / straight accas
    with cover) when the card composition favours them.

    Returns dict:
        recommendation : short label (e.g. "Lucky 15")
        rationale      : one-line explanation of why
        structure      : list of sub-bet dicts for the recommended alternative
        bankers        : count of banker-tier selections
        value          : count of value-tier selections
        default_ok     : True if the default 3-bet plan is optimal as-is
    """
    if not selections:
        return {
            "recommendation": "No recommendation",
            "rationale":      "No qualifying selections.",
            "structure":      [],
            "bankers":        0,
            "value":          0,
            "default_ok":     False,
        }

    # Exclude low_value_acca selections (thin fields ≤4 runners) from
    # accumulator leg count — they rarely add meaningful value to a perm.
    _acca_eligible = [s for s in selections if not s.get("low_value_acca", False)]
    classified  = classify_selections(_acca_eligible)
    bankers     = classified["bankers"]
    value       = classified["value"]
    n_bankers   = len(bankers)
    n_value     = len(value)

    if n_bankers >= 6:
        pool = bankers[:6]
        return {
            "recommendation": "Lucky 63 (6 bankers)",
            "rationale": (
                f"{n_bankers} bankers available — Lucky 63 covers 63 combinations "
                f"(6 singles, 15 doubles, 20 trebles, 15 4-folds, 6 5-folds, 1 6-fold). "
                f"Any 2+ winners returns something."
            ),
            "structure": [
                {"bet": "Lucky 63", "legs": 6, "combinations": 63,
                 "horses": [s["horse"] for s in pool],
                 "stake_per_line": "£0.63 (£40 / 63)",
                 "total_stake": 40.0},
                {"bet": "Straight 6-fold acca", "legs": 6, "combinations": 1,
                 "horses": [s["horse"] for s in pool],
                 "stake_per_line": "£60 retained",
                 "total_stake": 60.0},
            ],
            "bankers": n_bankers,
            "value":   n_value,
            "default_ok": False,
        }

    if n_bankers == 5:
        pool = bankers[:5]
        return {
            "recommendation": "5-fold Accumulator + Cover Treble",
            "rationale": (
                f"{n_bankers} bankers available — 5-fold acca for the main stake "
                f"with a cover treble on the top 3 bankers for insurance."
            ),
            "structure": [
                {"bet": "5-fold Accumulator", "legs": 5, "combinations": 1,
                 "horses": [s["horse"] for s in pool],
                 "stake_per_line": "£70 on 5-fold",
                 "total_stake": 70.0},
                {"bet": "Cover Treble (top 3)", "legs": 3, "combinations": 1,
                 "horses": [s["horse"] for s in pool[:3]],
                 "stake_per_line": "£30 on treble",
                 "total_stake": 30.0},
            ],
            "bankers": n_bankers,
            "value":   n_value,
            "default_ok": False,
        }

    if n_bankers >= 4:
        pool = bankers[:4]
        return {
            "recommendation": "Lucky 15 (4 bankers)",
            "rationale": (
                f"{n_bankers} bankers available today — Lucky 15 gives insurance "
                f"on 15 combinations (4 singles, 6 doubles, 4 trebles, 1 4-fold). "
                f"Any single winner returns stake; 2+ winners returns profit."
            ),
            "structure": [
                {"bet": "Lucky 15", "legs": 4, "combinations": 15,
                 "horses": [s["horse"] for s in pool],
                 "stake_per_line": "£2.67 per line (£40 / 15)",
                 "total_stake": 40.0},
                {"bet": "Straight 4-fold acca", "legs": 4, "combinations": 1,
                 "horses": [s["horse"] for s in pool],
                 "stake_per_line": "£60 retained",
                 "total_stake": 60.0},
            ],
            "bankers": n_bankers,
            "value":   n_value,
            "default_ok": False,
        }

    if n_bankers == 3:
        return {
            "recommendation": "3-Bet Plan (default optimal)",
            "rationale": (
                f"3 bankers is the sweet spot for the default 3-bet structure "
                f"(Main Acc 60% + Cover Acc 25% + Value Double 15%). "
                f"Lucky permutations would dilute the profit engine."
            ),
            "structure": [],
            "bankers": n_bankers,
            "value":   n_value,
            "default_ok": True,
        }

    if n_bankers == 2:
        return {
            "recommendation": "Straight Double + Value Double",
            "rationale": (
                f"Only 2 bankers — insufficient for a cover accumulator. "
                f"Back a straight banker double plus a value double on the "
                f"top 2 value horses (if available)."
            ),
            "structure": [
                {"bet": "Banker Double", "legs": 2, "combinations": 1,
                 "horses": [s["horse"] for s in bankers[:2]],
                 "stake_per_line": "£70 on double",
                 "total_stake": 70.0},
                {"bet": "Value Double", "legs": 2, "combinations": 1,
                 "horses": [s["horse"] for s in value[:2]] if n_value >= 2 else [],
                 "stake_per_line": "£30 on value double" if n_value >= 2 else "n/a",
                 "total_stake": 30.0 if n_value >= 2 else 0.0},
            ],
            "bankers": n_bankers,
            "value":   n_value,
            "default_ok": False,
        }

    return {
        "recommendation": "Hold or Reduce Stakes",
        "rationale": (
            f"Only {n_bankers} banker(s) available — weak card. "
            f"Consider holding the budget or backing single-race value only."
        ),
        "structure": [],
        "bankers": n_bankers,
        "value":   n_value,
        "default_ok": False,
    }


def classify_selections(selections: list) -> dict:
    """
    Classify selections into BANKER, VALUE, and WEAK tiers.

    BANKER : conf >= 63% AND price <= 4.0x
    VALUE  : price >= 4.0x AND conf >= 55%
    WEAK   : everything else (excluded from all bets)

    A horse can appear in both BANKER and VALUE lists if it straddles the
    boundary — in practice this won't happen given the thresholds, but the
    logic handles it cleanly.
    """
    bankers = [s for s in selections
               if s["confidence"] >= BANKER_CONF and s["decimal"] <= BANKER_MAX_PRICE]
    value   = [s for s in selections
               if s["decimal"] >= VALUE_MIN_PRICE and s["confidence"] >= VALUE_MIN_CONF]
    weak    = [s for s in selections
               if s not in bankers and s not in value]

    # Sort bankers by confidence desc, value by EV desc
    bankers.sort(key=lambda x: -x["confidence"])
    value.sort(key=lambda x: -(x["confidence"] * x["decimal"] - 1))

    return {
        "bankers": bankers,
        "value":   value,
        "weak":    weak,
    }


def build_staking_plan(selections: list, budget: float = 100.0) -> dict:
    """
    Build the 3-bet staking plan.

    Returns a dict with all fields needed by app.py Tab 1 and daily_brief.py.

    Keys (backwards-compatible with old engine where possible):
      plan_type         : THREE_BET | MAIN_ONLY | FULL_ACC (fallback)
      plan_label        : human-readable title
      plan_rationale    : one-line explanation
      budget            : total budget
      main_stake        : £ on main accumulator
      main_pool         : list of horse dicts in main acc
      main_dec          : combined decimal odds of main acc
      main_return       : projected return if main acc wins
      cover_pool        : list of horse dicts in cover acc (may be empty)
      cover_stake       : £ on cover accumulator
      cover_dec         : combined decimal odds of cover acc
      cover_return      : projected return if cover wins
      double_pool       : list of 2 horse dicts in value double (may be empty)
      double_stake      : £ on value double
      double_dec        : combined decimal odds of double
      double_return     : projected return if double wins
      speculative       : horses flagged but not placed (weak tier)
      covers            : legacy list format expected by old scenario builder
      cover_total       : legacy total cover stake
      scenarios         : list of scenario dicts for display table
    """
    if not selections:
        return _empty_plan(budget)

    classified = classify_selections(selections)
    bankers    = classified["bankers"]
    value      = classified["value"]
    weak       = classified["weak"]

    # ── Decide structure based on what's available ────────────────────────────
    has_bankers = len(bankers) >= 2
    has_value   = len(value)   >= 2

    if not has_bankers and not has_value:
        # Nothing qualifies — full acc on whatever we have
        return _full_acc_fallback(selections, budget)

    # ── BET 1: Main Accumulator ───────────────────────────────────────────────
    # BANKERS ONLY — value horses are isolated to BET 3 to protect the acca.
    # Lesson: outlier prices (4x+) destroy accumulator probability.
    # VALUE horses never enter BET 1 regardless of EV.
    main_pool = bankers

    # Sort by race time
    main_pool = sorted(main_pool, key=lambda x: x["time"])

    main_stake = round(budget * MAIN_PCT, 2)
    main_dec   = 1.0
    for s in main_pool:
        main_dec *= s["decimal"]
    main_dec    = round(main_dec, 2)
    main_return = round(main_stake * main_dec, 2)

    # ── BET 2: Cover Accumulator ──────────────────────────────────────────────
    # Bankers minus the highest-priced one — genuine safety net.
    # If BET 1's riskiest banker fails, BET 2 still lands.
    # Requires at least 2 bankers after removing the longest price.
    if has_bankers and len(bankers) >= 2:
        # Remove the highest-priced (riskiest) banker from BET 2
        _riskiest   = max(bankers, key=lambda x: x["decimal"])
        cover_pool  = sorted(
            [b for b in bankers if b is not _riskiest],
            key=lambda x: x["time"]
        )
        if len(cover_pool) >= 2:
            cover_stake = round(budget * COVER_PCT, 2)
            cover_dec   = 1.0
            for s in cover_pool:
                cover_dec *= s["decimal"]
            cover_dec    = round(cover_dec, 2)
            cover_return = round(cover_stake * cover_dec, 2)
        else:
            # Only 1 banker after removing riskiest — no meaningful cover
            cover_pool   = []
            cover_stake  = 0.0
            cover_dec    = 1.0
            cover_return = 0.0
            main_stake   = round(main_stake + budget * COVER_PCT, 2)
            main_return  = round(main_stake * main_dec, 2)
    else:
        cover_pool   = []
        cover_stake  = 0.0
        cover_dec    = 1.0
        cover_return = 0.0

    # ── BET 3: Value Double ───────────────────────────────────────────────────
    # Top 2 value horses by EV — isolated here, never in BET 1.
    # If only 1 value horse, or none, stake rolls into main acc.
    if has_value and len(value) >= 2:
        double_pool  = value[:2]
        double_stake = round(budget * DOUBLE_PCT, 2)
    elif has_value and len(value) == 1:
        # Only 1 value horse — no double, roll stake into main
        double_pool  = []
        double_stake = 0.0
        main_stake   = round(main_stake + budget * DOUBLE_PCT, 2)
        main_return  = round(main_stake * main_dec, 2)  # recalc with extra stake
    else:
        double_pool  = []
        double_stake = 0.0
        main_stake   = round(main_stake + budget * DOUBLE_PCT, 2)
        main_return  = round(main_stake * main_dec, 2)  # recalc with extra stake

    if double_pool:
        double_pool  = sorted(double_pool, key=lambda x: x["time"])
        double_dec   = 1.0
        for s in double_pool:
            double_dec *= s["decimal"]
        double_dec    = round(double_dec, 2)
        double_return = round(double_stake * double_dec, 2)
    else:
        double_dec    = 1.0
        double_return = 0.0

    # ── Plan label + rationale ────────────────────────────────────────────────
    plan_type = "THREE_BET" if double_pool else ("MAIN_COVER" if cover_pool else "MAIN_ONLY")

    if plan_type == "THREE_BET":
        plan_label = "3-Bet Plan: Main Acc + Cover + Value Double"
        rationale  = (
            f"{len(bankers)} banker leg(s) (≥63% conf, ≤4x price) form the core. "
            f"{len(value)} value horse(s) (≥4x price) targeted in main acc and value double. "
            f"Main acc targets £{main_return:,.0f} return. "
            f"Cover protects if value leg fails. "
            f"Double fires ~1 in {round(1/(value[0]['confidence']*value[1]['confidence'])):.0f} days."
        )
    elif plan_type == "MAIN_COVER":
        plan_label = "2-Bet Plan: Main Acc + Cover"
        rationale  = (
            f"No value horses above 4x today. "
            f"{len(bankers)} banker legs form both the main and cover accumulator. "
            f"Full 15% double stake rolled into main accumulator."
        )
    else:
        plan_label = "Main Accumulator"
        rationale  = "Single accumulator — insufficient bankers for a cover bet."

    # ── Build legacy 'covers' list for scenario engine compatibility ──────────
    legacy_covers = []
    if cover_pool and cover_stake > 0:
        legacy_covers.append({
            "omit":             "value legs",
            "omit_odds":        0.0,
            "omit_conf":        0.0,
            "pool":             [s["horse"] for s in cover_pool],
            "stake":            cover_stake,
            "dec":              cover_dec,
            "projected_return": cover_return,
            "fold":             len(cover_pool),
        })
    if double_pool and double_stake > 0:
        legacy_covers.append({
            "omit":             "non-value legs",
            "omit_odds":        0.0,
            "omit_conf":        0.0,
            "pool":             [s["horse"] for s in double_pool],
            "stake":            double_stake,
            "dec":              double_dec,
            "projected_return": double_return,
            "fold":             len(double_pool),
        })

    # ── Scenarios ─────────────────────────────────────────────────────────────
    scenarios = _build_scenarios(
        main_pool, main_stake, main_dec,
        cover_pool, cover_stake, cover_dec,
        double_pool, double_stake, double_dec,
        budget
    )

    return {
        # Primary fields
        "plan_type":      plan_type,
        "plan_label":     plan_label,
        "plan_rationale": rationale,
        "budget":         budget,
        # Main acc
        "main_stake":     main_stake,
        "main_pool":      main_pool,
        "main_dec":       main_dec,
        "main_return":    main_return,
        # Cover acc
        "cover_pool":     cover_pool,
        "cover_stake":    cover_stake,
        "cover_dec":      cover_dec,
        "cover_return":   cover_return,
        # Value double
        "double_pool":    double_pool,
        "double_stake":   double_stake,
        "double_dec":     double_dec,
        "double_return":  double_return,
        # Legacy compatibility
        "covers":         legacy_covers,
        "cover_total":    round(cover_stake + double_stake, 2),
        "speculative":    weak,
        "classified":     classified,
        "scenarios":      scenarios,
    }


def _build_scenarios(
    main_pool, main_stake, main_dec,
    cover_pool, cover_stake, cover_dec,
    double_pool, double_stake, double_dec,
    budget
) -> list:
    """Build scenario table for all meaningful win/loss combinations."""
    rows = []
    total_staked = main_stake + cover_stake + double_stake

    def _calc(main_wins, cover_wins, double_wins):
        ret  = (main_stake * main_dec   if main_wins   else 0.0)
        ret += (cover_stake * cover_dec if cover_wins  else 0.0)
        ret += (double_stake * double_dec if double_wins else 0.0)
        ret  = round(ret, 2)
        net  = round(ret - total_staked, 2)
        return {
            "Acc Return":    f"£{main_stake * main_dec:,.2f}"   if main_wins   else "—",
            "Cover Return":  f"£{cover_stake * cover_dec:,.2f}" if cover_wins  else ("—" if cover_pool else "n/a"),
            "Double Return": f"£{double_stake * double_dec:,.2f}" if double_wins else ("—" if double_pool else "n/a"),
            "Total Back":    f"£{ret:,.2f}",
            "Net P&L":       f"£{net:+,.2f}",
        }

    has_cover  = bool(cover_pool)
    has_double = bool(double_pool)

    rows.append({"Scenario": "All bets win",        **_calc(True,  True,  True)})
    rows.append({"Scenario": "Main acc wins only",   **_calc(True,  False, False)})
    if has_cover:
        rows.append({"Scenario": "Cover wins only",  **_calc(False, True,  False)})
    if has_double:
        rows.append({"Scenario": "Double wins only", **_calc(False, False, True)})
    if has_cover and has_double:
        rows.append({"Scenario": "Cover + Double win", **_calc(False, True, True)})
    rows.append({"Scenario": "Nothing wins",         **_calc(False, False, False)})

    return rows


def _empty_plan(budget: float) -> dict:
    return {
        "plan_type": "EMPTY", "plan_label": "No Qualifying Selections",
        "plan_rationale": "No selections cleared the confidence and price thresholds.",
        "budget": budget,
        "main_stake": 0, "main_pool": [], "main_dec": 1.0, "main_return": 0,
        "cover_pool": [], "cover_stake": 0, "cover_dec": 1.0, "cover_return": 0,
        "double_pool": [], "double_stake": 0, "double_dec": 1.0, "double_return": 0,
        "covers": [], "cover_total": 0, "speculative": [], "classified": {},
        "scenarios": [],
    }


def _full_acc_fallback(selections: list, budget: float) -> dict:
    """Fallback when no horses hit banker/value thresholds — full acc on all."""
    pool = sorted(selections, key=lambda x: x["time"])
    dec  = 1.0
    for s in pool:
        dec *= s["decimal"]
    dec    = round(dec, 2)
    ret    = round(budget * dec, 2)
    return {
        "plan_type": "FULL_ACC", "plan_label": "Full Accumulator (fallback)",
        "plan_rationale": "No horses cleared banker/value thresholds. Full budget on accumulator.",
        "budget": budget,
        "main_stake": budget, "main_pool": pool, "main_dec": dec, "main_return": ret,
        "cover_pool": [], "cover_stake": 0, "cover_dec": 1.0, "cover_return": 0,
        "double_pool": [], "double_stake": 0, "double_dec": 1.0, "double_return": 0,
        "covers": [], "cover_total": 0, "speculative": [],
        "classified": classify_selections(selections),
        "scenarios": [{"Scenario": "All win", "Acc Return": f"£{ret:,.2f}",
                        "Cover Return": "n/a", "Double Return": "n/a",
                        "Total Back": f"£{ret:,.2f}", "Net P&L": f"£{ret-budget:+,.2f}"},
                       {"Scenario": "Nothing wins", "Acc Return": "—",
                        "Cover Return": "n/a", "Double Return": "n/a",
                        "Total Back": "£0.00", "Net P&L": f"£{-budget:+,.2f}"}],
    }


def format_plan_summary(plan: dict) -> str:
    """Plain-text summary for emails and logging."""
    lines = [
        f"STAKING PLAN: {plan['plan_label']}",
        f"Budget: £{plan['budget']:.2f}",
        f"",
        f"BET 1 — Main {len(plan['main_pool'])}-fold Accumulator: £{plan['main_stake']:.2f}",
        f"  Legs: {', '.join(s['horse'] for s in plan['main_pool'])}",
        f"  Combined odds: {plan['main_dec']:.1f}x | Return if wins: £{plan['main_return']:,.2f}",
    ]
    if plan.get("cover_pool"):
        lines += [
            f"",
            f"BET 2 — Cover {len(plan['cover_pool'])}-fold Accumulator: £{plan['cover_stake']:.2f}",
            f"  Legs: {', '.join(s['horse'] for s in plan['cover_pool'])}",
            f"  Combined odds: {plan['cover_dec']:.1f}x | Return if wins: £{plan['cover_return']:,.2f}",
        ]
    if plan.get("double_pool"):
        lines += [
            f"",
            f"BET 3 — Value Double: £{plan['double_stake']:.2f}",
            f"  Legs: {', '.join(s['horse'] for s in plan['double_pool'])}",
            f"  Combined odds: {plan['double_dec']:.1f}x | Return if wins: £{plan['double_return']:,.2f}",
        ]
    if plan.get("speculative"):
        lines += [
            f"",
            f"Excluded (below thresholds): {', '.join(s['horse'] for s in plan['speculative'])}",
        ]
    lines += [f"", f"Rationale: {plan['plan_rationale']}"]
    return "\n".join(lines)
