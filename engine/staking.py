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

MAIN_PCT   = 0.60          # 60% of budget on main accumulator (<4 bankers)
COVER_PCT  = 0.25          # 25% of budget on cover accumulator (<4 bankers)
DOUBLE_PCT = 0.15          # 15% of budget on value double (<4 bankers)

# ── v2.5.35 — 4+ bankers restructure ─────────────────────────────────────
# Backtest 7-day P&L: doubles -£192 (losing), 4-folds +£183 (profitable).
# When we have 4+ bankers: drop the losing double, promote a 4-fold cover.
MAIN_PCT_4B   = 0.50       # 50% main acc (all bankers)
COVER_PCT_4B  = 0.30       # 30% cover 4-fold (best 4 bankers)
VALUE_PCT_4B  = 0.20       # 20% value selection(s)


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
        # v2.5.35 — 4+ bankers: Lucky 15 (30%) + Main Acc (50%) + Value (20%).
        # Swap from the old Lucky 15 (40%) + 4-fold acca (60%) split — backtest
        # P&L showed 4-folds profitable and doubles losing, so the Lucky 15
        # covers the perm insurance and the main acc chases the big-return leg.
        pool = bankers[:4]
        return {
            "recommendation": "Lucky 15 + Main Acc + Value (4+ bankers)",
            "rationale": (
                f"{n_bankers} bankers available — Lucky 15 covers 15 combinations "
                f"(4 singles, 6 doubles, 4 trebles, 1 4-fold). Split: Main Acc 50% "
                f"(big-return leg), Lucky 15 30% (£2/bet insurance), Value 20%."
            ),
            "structure": [
                {"bet": "Main Accumulator", "legs": len(bankers), "combinations": 1,
                 "horses": [s["horse"] for s in bankers],
                 "stake_per_line": "£50 on full acca",
                 "total_stake": 50.0},
                {"bet": "Lucky 15", "legs": 4, "combinations": 15,
                 "horses": [s["horse"] for s in pool],
                 "stake_per_line": "£2.00 per line (£30 / 15)",
                 "total_stake": 30.0},
                {"bet": "Value selection(s)", "legs": min(n_value, 2), "combinations": 1,
                 "horses": [s["horse"] for s in value[:2]] if n_value >= 1 else [],
                 "stake_per_line": "£20 on value" if n_value >= 1 else "rolled into main",
                 "total_stake": 20.0 if n_value >= 1 else 0.0},
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
    main_pool = sorted(bankers, key=lambda x: x["time"])

    # ── v2.5.35 — structure branches on banker count ─────────────────────────
    # 4+ bankers: main (50%) + 4-fold cover (30%) + value (20%) — profitable shape
    # <4 bankers: legacy 3-bet structure — main (60%) + cover minus riskiest (25%) + double (15%)
    four_bankers_mode = len(bankers) >= 4

    if four_bankers_mode:
        main_pct_use   = MAIN_PCT_4B
        cover_pct_use  = COVER_PCT_4B
        value_pct_use  = VALUE_PCT_4B
    else:
        main_pct_use   = MAIN_PCT
        cover_pct_use  = COVER_PCT
        value_pct_use  = DOUBLE_PCT

    main_stake = round(budget * main_pct_use, 2)
    main_dec   = 1.0
    for s in main_pool:
        main_dec *= s["decimal"]
    main_dec    = round(main_dec, 2)
    main_return = round(main_stake * main_dec, 2)

    # ── BET 2: Cover Accumulator ──────────────────────────────────────────────
    if four_bankers_mode:
        # 4-fold cover: TOP 4 bankers by confidence (profitable shape per backtest)
        _top4 = sorted(bankers, key=lambda x: -x["confidence"])[:4]
        cover_pool  = sorted(_top4, key=lambda x: x["time"])
        cover_stake = round(budget * cover_pct_use, 2)
        cover_dec   = 1.0
        for s in cover_pool:
            cover_dec *= s["decimal"]
        cover_dec    = round(cover_dec, 2)
        cover_return = round(cover_stake * cover_dec, 2)
    elif has_bankers and len(bankers) >= 2:
        # Legacy: bankers minus highest-priced (riskiest) leg
        _riskiest   = max(bankers, key=lambda x: x["decimal"])
        cover_pool  = sorted(
            [b for b in bankers if b is not _riskiest],
            key=lambda x: x["time"]
        )
        if len(cover_pool) >= 2:
            cover_stake = round(budget * cover_pct_use, 2)
            cover_dec   = 1.0
            for s in cover_pool:
                cover_dec *= s["decimal"]
            cover_dec    = round(cover_dec, 2)
            cover_return = round(cover_stake * cover_dec, 2)
        else:
            cover_pool   = []
            cover_stake  = 0.0
            cover_dec    = 1.0
            cover_return = 0.0
            main_stake   = round(main_stake + budget * cover_pct_use, 2)
            main_return  = round(main_stake * main_dec, 2)
    else:
        cover_pool   = []
        cover_stake  = 0.0
        cover_dec    = 1.0
        cover_return = 0.0

    # ── BET 3: Value selection(s) ─────────────────────────────────────────────
    # 4+ bankers mode: value is a single or double on top value horse(s).
    # <4 bankers mode: legacy 2-leg value double; single value rolls into main.
    if four_bankers_mode:
        # In 4-banker mode, don't require pairs — a single value horse gets the stake
        if has_value and len(value) >= 2:
            double_pool  = value[:2]
            double_stake = round(budget * value_pct_use, 2)
        elif has_value and len(value) == 1:
            double_pool  = value[:1]
            double_stake = round(budget * value_pct_use, 2)
        else:
            double_pool  = []
            double_stake = 0.0
            # No value available — roll value stake into main
            main_stake   = round(main_stake + budget * value_pct_use, 2)
            main_return  = round(main_stake * main_dec, 2)
    else:
        if has_value and len(value) >= 2:
            double_pool  = value[:2]
            double_stake = round(budget * value_pct_use, 2)
        else:
            double_pool  = []
            double_stake = 0.0
            main_stake   = round(main_stake + budget * value_pct_use, 2)
            main_return  = round(main_stake * main_dec, 2)

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

    if four_bankers_mode and plan_type == "THREE_BET":
        _value_leg_txt = (
            "Value Double" if len(double_pool) == 2 else "Value Single"
        )
        plan_label = f"3-Bet (4+ Bankers): Main Acc + 4-fold Cover + {_value_leg_txt}"
        rationale  = (
            f"{len(bankers)} bankers available — backtest-validated 4+ banker structure: "
            f"50% Main Acc ({len(main_pool)}-fold) + 30% 4-fold Cover (top 4 by conf) + "
            f"20% value. Doubles dropped (-£192 P&L over 7d); 4-folds promoted (+£183)."
        )
    elif four_bankers_mode and plan_type == "MAIN_COVER":
        plan_label = f"2-Bet (4+ Bankers): Main Acc + 4-fold Cover"
        rationale  = (
            f"{len(bankers)} bankers — no value horses today. "
            f"50% Main Acc ({len(main_pool)}-fold) + 30% 4-fold Cover. "
            f"Value stake rolled into main."
        )
    elif plan_type == "THREE_BET":
        plan_label = "3-Bet Plan: Main Acc + Cover + Value Double"
        rationale  = (
            f"{len(bankers)} banker leg(s) (≥63% conf, ≤4x price) form the core. "
            f"{len(value)} value horse(s) (≥4x price) targeted in value double. "
            f"Main acc targets £{main_return:,.0f} return. "
            f"Cover protects if riskiest banker fails."
        )
    elif plan_type == "MAIN_COVER":
        plan_label = "2-Bet Plan: Main Acc + Cover"
        rationale  = (
            f"No value horses above 4x today. "
            f"{len(bankers)} banker legs form both the main and cover accumulator. "
            f"Full value stake rolled into main accumulator."
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


def _approx_fractional(decimal_odds: float) -> str:
    """Convert combined decimal odds into an 'approximately X/Y' fraction label."""
    try:
        d = float(decimal_odds)
    except Exception:
        return "approximately 1/1"
    net = max(d - 1.0, 0.0)
    if net <= 0.0:
        return "approximately 1/1"
    common = [
        (0.5, "1/2"), (0.8, "4/5"), (1.0, "1/1"), (1.2, "6/5"), (1.5, "6/4"),
        (1.75, "7/4"), (2.0, "2/1"), (2.5, "5/2"), (3.0, "3/1"), (3.5, "7/2"),
        (4.0, "4/1"), (4.5, "9/2"), (5.0, "5/1"), (6.0, "6/1"), (7.0, "7/1"),
        (8.0, "8/1"), (9.0, "9/1"), (10.0, "10/1"), (12.0, "12/1"), (14.0, "14/1"),
        (16.0, "16/1"), (20.0, "20/1"), (25.0, "25/1"), (33.0, "33/1"), (40.0, "40/1"),
        (50.0, "50/1"), (66.0, "66/1"), (80.0, "80/1"), (100.0, "100/1"),
    ]
    best_lbl = common[-1][1]
    best_diff = abs(net - common[-1][0])
    for num, lbl in common:
        diff = abs(net - num)
        if diff < best_diff:
            best_diff = diff
            best_lbl = lbl
    if net > 100.0:
        return f"approximately {int(round(net))}/1"
    return f"approximately {best_lbl}"


def rank_accumulator_combinations(
    selections: list,
    min_legs: int = 2,
    max_legs: int = 5,
    top_n: int = 5,
    budget: float = 100.0,
) -> list:
    """
    Score and rank every meaningful accumulator combination from the
    qualifying selection pool. Returns top_n dicts sorted by EV score.

    Score = win_prob × combined_decimal_return (EV multiplier).
    Per-leg probability: (1 / decimal) × (confidence / 0.55), capped at 0.95.

    Same-race combinations (same time + course) are excluded.
    Combinations with low_value_acca / non-favourite / rival-top-trainer
    legs are flagged via warnings but not excluded.

    Each result dict:
      legs, horses, times, courses, combined_dec, combined_frac,
      win_prob, ev_score, proj_return, warnings, rank.
    """
    if not selections:
        return []

    # Build eligible pool: must have a valid decimal price, not low_value_acca.
    pool = []
    for s in selections:
        if s.get("low_value_acca", False):
            continue
        try:
            dec = float(s.get("decimal") or 0.0)
        except Exception:
            continue
        if dec <= 1.0:
            continue
        try:
            conf = float(s.get("confidence") or 0.0)
        except Exception:
            conf = 0.0
        pool.append({
            "horse":             str(s.get("horse", "")),
            "time":              str(s.get("time", "")),
            "course":            str(s.get("course", "")),
            "decimal":           dec,
            "confidence":        conf,
            "is_fav":            bool(s.get("is_fav", False)),
            "rival_top_trainer": bool(s.get("rival_top_trainer", False)),
            "rival_trainer_name": str(s.get("rival_trainer_name", "") or ""),
            "low_value_acca":    bool(s.get("low_value_acca", False)),
        })

    if len(pool) < min_legs:
        return []

    # £ stake per combination used for projected return (matches test rubric).
    stake_per_combo = 10.0

    upper = min(max_legs, len(pool))
    scored = []
    for k in range(min_legs, upper + 1):
        for combo in _combs(pool, k):
            # Same-race exclusion: no two legs with identical time + course.
            race_keys = [(leg["time"], leg["course"]) for leg in combo]
            if len(set(race_keys)) != len(race_keys):
                continue

            combined_dec = 1.0
            win_prob = 1.0
            warnings = []
            for leg in combo:
                combined_dec *= leg["decimal"]
                # Per-leg implied probability × confidence quality multiplier.
                base_p = 1.0 / leg["decimal"]
                mult = (leg["confidence"] / 0.55) if leg["confidence"] > 0 else 1.0
                leg_p = min(base_p * mult, 0.95)
                win_prob *= leg_p

                if leg.get("low_value_acca"):
                    warnings.append(f"LOW VALUE: {leg['horse']}")
                if not leg.get("is_fav"):
                    warnings.append(f"NOT FAV: {leg['horse']}")
                if leg.get("rival_top_trainer"):
                    rival = leg.get("rival_trainer_name", "top yard")
                    warnings.append(f"RIVAL TRAINER ({rival}) vs {leg['horse']}")

            ev_score = win_prob * combined_dec
            proj_return = round(stake_per_combo * combined_dec, 2)

            scored.append({
                "legs":         len(combo),
                "horses":       [leg["horse"]  for leg in combo],
                "times":        [leg["time"]   for leg in combo],
                "courses":      [leg["course"] for leg in combo],
                "combined_dec": round(combined_dec, 3),
                "combined_frac": _approx_fractional(combined_dec),
                "win_prob":     round(win_prob, 5),
                "ev_score":     round(ev_score, 4),
                "proj_return":  proj_return,
                "warnings":     warnings,
            })

    scored.sort(key=lambda x: x["ev_score"], reverse=True)
    top = scored[:max(top_n, 0)]
    for i, row in enumerate(top, start=1):
        row["rank"] = i
    return top


def get_fold_bets(selections: list) -> dict:
    """
    v2.5.47 — 2-bet fold structure (relaxed gating).

    Bet A = Core fold — dominant-fav anchors:
        Includes any selection where is_dominant_fav=True
        OR (is_fav=True AND gap_to_2nd >= 0.50).
        Confidence threshold: >= 0.50 (matches active engine threshold).
        Field-size: only excluded when explicitly >= 16 runners. Missing/None
        field-size does NOT exclude the leg.
        Minimum legs: 2. If only 1 (or 0) qualifies, Bet A is None.
        Cap at 4 legs (top-4 by confidence).

    Bet B = Bet A + next-highest-confidence remaining selection, even if YG_RISK.

    Selections must clear the 4/6 price cut-off (decimal > 1.67) — this is the
    only hard exclusion besides low_value_acca.

    Returns:
        {
          "bet_a": {horses, combined_decimal, label, legs, warnings} | None,
          "bet_b": {horses, combined_decimal, label, legs, warnings} | None,
        }
    """
    result = {"bet_a": None, "bet_b": None}
    if not selections:
        return result

    BET_A_CONF = 0.50

    def _runners(s):
        # Returns int runner count or None when truly absent (don't coerce to 0).
        for k in ("runners", "field_size"):
            if k in s and s.get(k) not in (None, "", 0):
                try:
                    return int(s.get(k))
                except (TypeError, ValueError):
                    pass
        return None

    # Hard exclude: 4/6 cut-off, low_value_acca, and field_size explicitly ≥16
    eligible = []
    for s in selections:
        try:
            dec = float(s.get("decimal") or 0.0)
        except Exception:
            continue
        if dec <= 1.67:
            continue
        if s.get("low_value_acca", False):
            continue
        rn = _runners(s)
        if rn is not None and rn >= 16:
            continue
        eligible.append(s)

    def _qualifies_a(s) -> bool:
        try:
            conf = float(s.get("confidence", 0.0) or 0.0)
        except Exception:
            conf = 0.0
        if conf < BET_A_CONF:
            return False
        is_fav   = bool(s.get("is_fav", False))
        try:
            gap = float(s.get("gap_to_2nd", 0.0) or 0.0)
        except Exception:
            gap = 0.0
        is_dom = bool(s.get("is_dominant_fav", False)) or (is_fav and gap >= 0.50)
        return is_dom

    core = [s for s in eligible if _qualifies_a(s)]
    core.sort(key=lambda x: -float(x.get("confidence", 0.0) or 0.0))

    # Bet A requires at least 2 legs to form a fold
    if len(core) < 2:
        return result

    bet_a_horses = core[:4]
    core_names = {s.get("horse") for s in bet_a_horses}
    legs_a = len(bet_a_horses)

    def _combined(pool):
        d = 1.0
        for s in pool:
            d *= float(s["decimal"])
        return round(d, 2)

    def _warnings(pool):
        w = []
        for s in pool:
            if s.get("yg_risk"):
                rn = _runners(s) or s.get("runners", 0)
                w.append(
                    f"YG_RISK: {s.get('horse','?')} — {rn} runners, "
                    f"gap {float(s.get('gap_to_2nd', 0) or 0):.0%}"
                )
        return w

    fold_label = {2: "2-fold double", 3: "3-fold treble",
                  4: "4-fold acca"}.get(legs_a, f"{legs_a}-fold acca")

    bet_a_pool = sorted(bet_a_horses, key=lambda x: x.get("time", ""))
    result["bet_a"] = {
        "horses":            bet_a_pool,
        "combined_decimal":  _combined(bet_a_pool),
        "label":             f"Core {fold_label}",
        "legs":              legs_a,
        "warnings":          _warnings(bet_a_pool),
    }

    # Bet B: Bet A horses + next-highest-confidence remaining selection
    # (even YG_RISK). Falls back to nothing if no extra leg available.
    remaining = [s for s in eligible if s.get("horse") not in core_names]
    remaining.sort(key=lambda x: -float(x.get("confidence", 0.0) or 0.0))
    optional = remaining[0] if remaining else None

    if optional is not None:
        bet_b_pool = sorted(bet_a_horses + [optional], key=lambda x: x.get("time", ""))
        legs_b = len(bet_b_pool)
        b_label = {2: "2-fold double", 3: "3-fold treble", 4: "4-fold acca",
                   5: "5-fold acca"}.get(legs_b, f"{legs_b}-fold acca")
        result["bet_b"] = {
            "horses":            bet_b_pool,
            "combined_decimal":  _combined(bet_b_pool),
            "label":             f"Extended {b_label}",
            "legs":              legs_b,
            "warnings":          _warnings(bet_b_pool),
        }

    return result


def get_best_acca_label(combo: dict) -> str:
    """Short human-readable label, e.g. 'Double: Organise + Misterdoc (EV: 1.42x)'."""
    if not combo:
        return ""
    legs = combo.get("legs", 0)
    name_map = {2: "Double", 3: "Treble", 4: "4-fold", 5: "5-fold"}
    lbl = name_map.get(legs, f"{legs}-fold")
    horses = " + ".join(combo.get("horses", []))
    ev = combo.get("ev_score", 0.0)
    return f"{lbl}: {horses} (EV: {ev:.2f}x)"


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
