# Racing Engine — Adaptive Staking Engine
# Version: 1.0 — 22 April 2026
#
# PHILOSOPHY:
#   Staking should adapt to what the card actually looks like.
#   Short-priced cards = full accumulator, be brave.
#   Longer-priced horses = cover accumulators protect the stake.
#   Very long prices = flag separately, never damage the main bet.
#
# ADAPTIVE RULES:
#   All selections < 2.50x  -> 100% full accumulator. No cover needed.
#   Any selection >= 2.50x  -> 70% main acc + 30% cover (omit value horse(s))
#   Any selection >= 4.00x  -> 50% main acc + 50% cover (value horse is real risk)
#   Any selection >= 8.00x  -> Flag as optional side bet. Exclude from main acc.
#
# Lucky 15:
#   Removed from core plan — at short prices (< 2.50x) L15 singles return
#   almost nothing. Cover accumulators deploy stake more efficiently.
#   L15 only reinstated if user explicitly requests it or majority > 3.0x.

from itertools import combinations as _combs


# ── Price tier thresholds ─────────────────────────────────────────────────────
BANKER_THRESHOLD   = 2.50   # below this = short-priced banker, full acc
VALUE_THRESHOLD    = 4.00   # above this = value horse, needs cover
SPECULATIVE_THRESHOLD = 8.00  # above this = side bet only, excluded from main acc


def classify_selections(selections: list) -> dict:
    """
    Classify selections into tiers based on decimal odds.
    Returns dict with bankers, value, speculative lists and the recommended plan.

    selections: list of dicts with at least {'horse', 'decimal', 'confidence', 'time', 'course'}
    """
    bankers      = [s for s in selections if s["decimal"] < BANKER_THRESHOLD]
    value        = [s for s in selections if BANKER_THRESHOLD <= s["decimal"] < SPECULATIVE_THRESHOLD]
    speculative  = [s for s in selections if s["decimal"] >= SPECULATIVE_THRESHOLD]

    # Horses that go into the main accumulator (exclude speculative)
    main_pool = [s for s in selections if s["decimal"] < SPECULATIVE_THRESHOLD]

    # Determine plan
    has_value       = len(value) > 0
    has_speculative = len(speculative) > 0
    all_short       = len(value) == 0 and len(speculative) == 0

    if all_short:
        plan = "FULL_ACC"
    elif any(s["decimal"] >= VALUE_THRESHOLD for s in value):
        plan = "COVER_50"   # 50/50 split
    else:
        plan = "COVER_70"   # 70/30 split

    return {
        "bankers":       bankers,
        "value":         value,
        "speculative":   speculative,
        "main_pool":     main_pool,
        "plan":          plan,
        "all_short":     all_short,
        "has_value":     has_value,
        "has_speculative": has_speculative,
    }


def build_staking_plan(selections: list, budget: float = 100.0) -> dict:
    """
    Build the full adaptive staking plan for the day.

    Returns a dict with:
      plan_type       : FULL_ACC | COVER_70 | COVER_50
      plan_label      : human-readable description
      main_stake      : £ on main accumulator
      main_pool       : list of horses in main acc
      main_dec        : combined decimal odds of main acc
      main_return     : projected return if main acc wins
      covers          : list of cover accumulators [{omit, stake, dec, projected_return}]
      cover_total     : total £ on cover accumulators
      speculative     : list of flagged side-bet horses
      budget          : total budget
      plan_rationale  : one-line explanation
    """
    classified = classify_selections(selections)
    plan       = classified["plan"]
    main_pool  = classified["main_pool"]
    speculative = classified["speculative"]

    # ── Calculate main accumulator ────────────────────────────────────────────
    if plan == "FULL_ACC":
        main_pct   = 1.00
        cover_pct  = 0.00
        plan_label = "Full Accumulator"
        rationale  = (f"All {len(main_pool)} selections are short-priced bankers (below 2.5x). "
                      f"Cover accumulators return minimal value at these odds. "
                      f"Full stake on the accumulator maximises profit.")
    elif plan == "COVER_70":
        main_pct   = 0.70
        cover_pct  = 0.30
        plan_label = "Accumulator + Cover"
        rationale  = (f"{len(classified['value'])} selection(s) above 2.5x detected. "
                      f"70% on main accumulator, 30% spread across cover bets "
                      f"that protect if the longer-priced horse fails.")
    else:  # COVER_50
        main_pct   = 0.50
        cover_pct  = 0.50
        plan_label = "Accumulator + Full Cover"
        rationale  = (f"{len(classified['value'])} selection(s) above 4.0x detected — real risk to accumulator. "
                      f"50/50 split: main accumulator and full cover protection.")

    main_stake = round(budget * main_pct, 2)
    cover_budget = round(budget * cover_pct, 2)

    # Combined odds of main pool
    main_dec = 1.0
    for s in main_pool:
        main_dec *= s["decimal"]
    main_dec    = round(main_dec, 2)
    main_return = round(main_stake * main_dec, 2)

    # ── Build cover accumulators ──────────────────────────────────────────────
    # One cover per horse in main pool (omit one each time)
    # Only build covers if cover_pct > 0 and 3+ horses in pool
    covers = []
    if cover_pct > 0 and len(main_pool) >= 3:
        # Prioritise covers that omit higher-priced horses (most risk)
        sorted_pool = sorted(main_pool, key=lambda x: x["decimal"], reverse=True)
        # Number of covers = number of value horses (at least 1, max all)
        n_covers = len(classified["value"]) if classified["value"] else len(main_pool)
        n_covers = min(n_covers, len(main_pool))
        stake_per_cover = round(cover_budget / n_covers, 2)

        for omit_horse in sorted_pool[:n_covers]:
            pool = [s for s in main_pool if s["horse"] != omit_horse["horse"]]
            dec  = 1.0
            for s in pool:
                dec *= s["decimal"]
            dec = round(dec, 2)
            covers.append({
                "omit":             omit_horse["horse"],
                "omit_odds":        omit_horse["decimal"],
                "omit_conf":        omit_horse["confidence"],
                "pool":             [s["horse"] for s in pool],
                "stake":            stake_per_cover,
                "dec":              dec,
                "projected_return": round(stake_per_cover * dec, 2),
                "fold":             len(pool),
            })

    # ── Scenario projections ──────────────────────────────────────────────────
    scenarios = _build_scenarios(main_pool, main_stake, main_dec, covers, budget)

    return {
        "plan_type":      plan,
        "plan_label":     plan_label,
        "plan_rationale": rationale,
        "budget":         budget,
        "main_stake":     main_stake,
        "main_pool":      main_pool,
        "main_dec":       main_dec,
        "main_return":    main_return,
        "covers":         covers,
        "cover_total":    cover_budget,
        "speculative":    speculative,
        "classified":     classified,
        "scenarios":      scenarios,
    }


def _build_scenarios(main_pool: list, main_stake: float, main_dec: float,
                     covers: list, budget: float) -> list:
    """
    Build a scenario table showing P&L for key win/loss combinations.
    """
    n = len(main_pool)
    rows = []

    def _check(win_set: set) -> dict:
        # Main acc
        main_won = all(s["horse"] in win_set for s in main_pool)
        main_ret = round(main_stake * main_dec, 2) if main_won else 0.0
        # Covers
        cover_ret = 0.0
        for c in covers:
            pool_won = all(h in win_set for h in c["pool"])
            if pool_won:
                cover_ret += c["projected_return"]
        cover_ret = round(cover_ret, 2)
        total = round(main_ret + cover_ret, 2)
        net   = round(total - budget, 2)
        return {"main": main_ret, "cover": cover_ret, "total": total, "net": net}

    # All win
    win_all = {s["horse"] for s in main_pool}
    r = _check(win_all)
    rows.append({"scenario": f"All {n} win", **r})

    # Each horse loses (one at a time)
    for omit in main_pool:
        win_set = {s["horse"] for s in main_pool if s["horse"] != omit["horse"]}
        r = _check(win_set)
        rows.append({"scenario": f"{n-1} win (excl. {omit['horse']})", **r})

    # Two losers (brief summary)
    if n >= 4:
        worst_two = sorted(main_pool, key=lambda x: x["decimal"], reverse=True)[:2]
        win_set = {s["horse"] for s in main_pool
                   if s["horse"] not in {w["horse"] for w in worst_two}}
        r = _check(win_set)
        rows.append({"scenario": f"{n-2} win (2 fail)", **r})

    # None win
    r = _check(set())
    rows.append({"scenario": "None win", **r})

    return rows


def format_plan_summary(plan: dict) -> str:
    """Plain-text summary of the staking plan for emails/logging."""
    lines = [
        f"STAKING PLAN: {plan['plan_label']}",
        f"Budget: £{plan['budget']:.2f}",
        f"",
        f"Main {len(plan['main_pool'])}-fold accumulator: £{plan['main_stake']:.2f}",
        f"  Odds: {plan['main_dec']:.1f}x | Return if all win: £{plan['main_return']:,.2f}",
    ]
    if plan["covers"]:
        lines.append(f"")
        lines.append(f"Cover accumulators: £{plan['cover_total']:.2f} total")
        for c in plan["covers"]:
            lines.append(f"  {c['fold']}-fold (omit {c['omit']}): "
                         f"£{c['stake']:.2f} @ {c['dec']:.1f}x = £{c['projected_return']:.2f} if wins")
    if plan["speculative"]:
        lines.append(f"")
        lines.append(f"Flagged side bets (excluded from main acc):")
        for s in plan["speculative"]:
            lines.append(f"  {s['horse']} @ {s['decimal']:.2f}x — consider separate small stake")
    lines.append(f"")
    lines.append(f"Rationale: {plan['plan_rationale']}")
    return "\n".join(lines)
