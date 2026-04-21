# Racing Engine — Lucky 15 Planner + Tier Logic
# Version: 1.0
# Date: 21 April 2026
#
# Selects the best Lucky 15 quartet (4 horses) from a scored pool,
# assigns each selection to a price tier, builds all 15 bets,
# projects returns per scenario, and assembles a six-timer accumulator
# from the full pool.

import itertools
from functools import reduce
from typing import List, Dict, Any, Optional


# ── Price tier thresholds ──────────────────────────────────────

TIER_BANKER   = "banker"    # decimal <= 2.50
TIER_MID      = "mid"       # decimal 2.51 – 5.00
TIER_VALUE    = "value"     # decimal 5.01 – 10.00
TIER_LONGSHOT = "longshot"  # decimal > 10.00

MIN_DECIMAL   = 1.67        # At or below 4/6 — excluded from Lucky 15


def _assign_tier(decimal: float) -> str:
    """Return price tier string for a given decimal odds."""
    if decimal <= 2.50:
        return TIER_BANKER
    elif decimal <= 5.00:
        return TIER_MID
    elif decimal <= 10.00:
        return TIER_VALUE
    else:
        return TIER_LONGSHOT


def _to_decimal(odds_str: Any) -> float:
    """Convert fractional or decimal odds string to float. Returns 2.0 on failure."""
    try:
        s = str(odds_str).strip()
        if "/" in s:
            n, d = s.split("/")
            return (float(n) + float(d)) / float(d)
        return float(s)
    except Exception:
        return 2.0


def _safe_ev(sel: dict) -> float:
    """Extract EV from a selection dict, falling back to 0.0."""
    try:
        return float(sel.get("ev", 0.0) or 0.0)
    except Exception:
        return 0.0


class Lucky15Planner:
    """
    Builds a Lucky 15 bet plan from a pool of scored horse selections.

    Parameters
    ----------
    pool : list of dict
        Each dict must have: horse, course, time, odds_str, decimal,
        confidence, ev.
    stake_per_bet : float
        Stake for each of the 15 Lucky 15 bets (default £2.00).
    sixtimer_stake : float
        Stake for the six-timer accumulator from the full pool (default £20.00).
    """

    def __init__(
        self,
        pool: List[Dict[str, Any]],
        stake_per_bet: float = 2.00,
        sixtimer_stake: float = 20.00,
    ):
        self.pool          = list(pool)   # full pool — all horses
        self.stake_per_bet = stake_per_bet
        self.sixtimer_stake = sixtimer_stake

    # ── Public entry point ─────────────────────────────────────

    def build_plan(self) -> Dict[str, Any]:
        """
        Build the full plan dict.

        Returns
        -------
        dict with keys:
            lucky15_selections, lucky15_bets, lucky15_scenarios,
            sixtimer_selections, sixtimer_stake, sixtimer_combined_decimal,
            sixtimer_projected_return, total_staked, tier_breakdown
        """
        quartet = self._select_quartet()
        bets    = self._build_lucky15_bets(quartet)
        plan = {
            "lucky15_selections":        [self._selection_summary(s) for s in quartet],
            "lucky15_bets":              bets,
            "lucky15_scenarios":         self._build_scenarios(quartet),
            "sixtimer_selections":       [s.get("horse") for s in self.pool],
            "sixtimer_stake":            round(self.sixtimer_stake, 2),
            "sixtimer_combined_decimal": self._sixtimer_combined_decimal(),
            "sixtimer_projected_return": self._sixtimer_projected_return(),
            "total_staked":              round(15 * self.stake_per_bet + self.sixtimer_stake, 2),
            "tier_breakdown":            self._tier_breakdown(quartet),
        }
        return plan

    # ── Summary text ──────────────────────────────────────────

    def plan_summary_text(self) -> str:
        """Return a plain-English summary of the current plan."""
        plan    = self.build_plan()
        sels    = plan["lucky15_selections"]
        scen    = plan["lucky15_scenarios"]
        six_ret = plan["sixtimer_projected_return"]
        total   = plan["total_staked"]

        lines = [
            "=== Lucky 15 + Six-Timer Plan ===",
            "",
            "Lucky 15 Selections:",
        ]
        for s in sels:
            lines.append(
                f"  [{s['tier'].upper():8s}]  {s['horse']:<30s}  {s['odds_str']:>8s}"
                f"  (decimal {s['decimal']:.2f})"
            )

        lines += [
            "",
            "Scenarios (£{:.2f}/bet x 15 = £{:.2f} staked):".format(
                self.stake_per_bet, 15 * self.stake_per_bet
            ),
            "  1 winner : min return £{:.2f}  (profit £{:.2f})".format(
                scen["1_winner"]["min_return"], scen["1_winner"]["min_profit"]
            ),
            "  2 winners: min £{:.2f} — max £{:.2f}".format(
                scen["2_winners"]["min_return"], scen["2_winners"]["max_return"]
            ),
            "  3 winners: min £{:.2f} — max £{:.2f}".format(
                scen["3_winners"]["min_return"], scen["3_winners"]["max_return"]
            ),
            "  4 winners: max return £{:.2f}  (profit £{:.2f})".format(
                scen["4_winners"]["max_return"], scen["4_winners"]["min_profit"]
            ),
            "",
            "Six-Timer Accumulator ({} horses):".format(len(self.pool)),
            "  Combined decimal : {:.2f}".format(plan["sixtimer_combined_decimal"]),
            "  Stake            : £{:.2f}".format(self.sixtimer_stake),
            "  Projected return : £{:.2f}".format(six_ret),
            "  Projected profit : £{:.2f}".format(six_ret - self.sixtimer_stake),
            "",
            "Total staked (Lucky 15 + six-timer): £{:.2f}".format(total),
        ]
        return "\n".join(lines)

    # ── Quartet selection ──────────────────────────────────────

    def _select_quartet(self) -> List[Dict[str, Any]]:
        """
        Choose the best 4 horses from the pool for the Lucky 15,
        applying the tier-priority logic.
        """
        # Eligible horses: exclude those at or below 4/6 (decimal <= 1.67)
        eligible = [
            s for s in self.pool
            if self._get_decimal(s) > MIN_DECIMAL
        ]

        # Build per-tier lists, sorted by EV descending
        tiers: Dict[str, List[Dict]] = {
            TIER_BANKER:   [],
            TIER_MID:      [],
            TIER_VALUE:    [],
            TIER_LONGSHOT: [],
        }
        for s in eligible:
            t = _assign_tier(self._get_decimal(s))
            tiers[t].append(s)

        for t in tiers:
            tiers[t].sort(key=_safe_ev, reverse=True)

        # Priority 1: 1 banker + 1 mid + 1 value + 1 longshot
        if (tiers[TIER_BANKER] and tiers[TIER_MID]
                and tiers[TIER_VALUE] and tiers[TIER_LONGSHOT]):
            return [
                tiers[TIER_BANKER][0],
                tiers[TIER_MID][0],
                tiers[TIER_VALUE][0],
                tiers[TIER_LONGSHOT][0],
            ]

        # Priority 2: 1 banker + 1 mid + 2 value (no longshot)
        if tiers[TIER_BANKER] and tiers[TIER_MID] and len(tiers[TIER_VALUE]) >= 2:
            return [
                tiers[TIER_BANKER][0],
                tiers[TIER_MID][0],
                tiers[TIER_VALUE][0],
                tiers[TIER_VALUE][1],
            ]

        # Priority 3: 1 banker + 1 mid + 1 value (best fallback from any tier for 4th)
        if tiers[TIER_BANKER] and tiers[TIER_MID] and tiers[TIER_VALUE]:
            fourth = self._best_remaining(
                eligible,
                exclude=[tiers[TIER_BANKER][0], tiers[TIER_MID][0], tiers[TIER_VALUE][0]],
            )
            if fourth:
                return [
                    tiers[TIER_BANKER][0],
                    tiers[TIER_MID][0],
                    tiers[TIER_VALUE][0],
                    fourth,
                ]

        # Priority 4: No banker — 2 mid + 1 value + 1 longshot
        if (len(tiers[TIER_MID]) >= 2
                and tiers[TIER_VALUE] and tiers[TIER_LONGSHOT]):
            return [
                tiers[TIER_MID][0],
                tiers[TIER_MID][1],
                tiers[TIER_VALUE][0],
                tiers[TIER_LONGSHOT][0],
            ]

        # Priority 5: No banker — 2 mid + 2 value
        if len(tiers[TIER_MID]) >= 2 and len(tiers[TIER_VALUE]) >= 2:
            return [
                tiers[TIER_MID][0],
                tiers[TIER_MID][1],
                tiers[TIER_VALUE][0],
                tiers[TIER_VALUE][1],
            ]

        # Final fallback: just take the top 4 by EV from eligible pool
        by_ev = sorted(eligible, key=_safe_ev, reverse=True)
        return by_ev[:4]

    def _best_remaining(
        self,
        pool: List[Dict],
        exclude: List[Dict],
    ) -> Optional[Dict]:
        """Return highest-EV horse from pool not in exclude list."""
        exclude_ids = {id(s) for s in exclude}
        candidates = [s for s in pool if id(s) not in exclude_ids]
        if not candidates:
            return None
        return max(candidates, key=_safe_ev)

    # ── Lucky 15 bet construction ──────────────────────────────

    def _build_lucky15_bets(self, quartet: List[Dict]) -> List[Dict]:
        """
        Build all 15 bets from a quartet:
        4 singles, 6 doubles, 4 trebles, 1 four-fold.
        """
        bets = []
        stake = self.stake_per_bet

        # Singles (4)
        for s in quartet:
            dec   = self._get_decimal(s)
            ret   = round(stake * dec, 2)
            bets.append({
                "type":             "Single",
                "selections":       [s.get("horse")],
                "stake":            stake,
                "projected_return": ret,
                "projected_profit": round(ret - stake, 2),
            })

        # Doubles (6)
        for a, b in itertools.combinations(quartet, 2):
            dec   = self._get_decimal(a) * self._get_decimal(b)
            ret   = round(stake * dec, 2)
            bets.append({
                "type":             "Double",
                "selections":       [a.get("horse"), b.get("horse")],
                "stake":            stake,
                "projected_return": ret,
                "projected_profit": round(ret - stake, 2),
            })

        # Trebles (4)
        for triple in itertools.combinations(quartet, 3):
            dec   = reduce(lambda x, y: x * y, [self._get_decimal(s) for s in triple])
            ret   = round(stake * dec, 2)
            bets.append({
                "type":             "Treble",
                "selections":       [s.get("horse") for s in triple],
                "stake":            stake,
                "projected_return": ret,
                "projected_profit": round(ret - stake, 2),
            })

        # Four-fold (1)
        dec   = reduce(lambda x, y: x * y, [self._get_decimal(s) for s in quartet])
        ret   = round(stake * dec, 2)
        bets.append({
            "type":             "Four-fold",
            "selections":       [s.get("horse") for s in quartet],
            "stake":            stake,
            "projected_return": ret,
            "projected_profit": round(ret - stake, 2),
        })

        return bets

    # ── Scenario calculator ────────────────────────────────────

    def _build_scenarios(self, quartet: List[Dict]) -> Dict[str, Dict]:
        """
        Calculate min/max returns for 1, 2, 3 and 4 winners.
        'min' uses the lowest-price winners; 'max' uses the highest-price.
        """
        stake    = self.stake_per_bet
        total_l15_stake = stake * 15
        decimals = sorted([self._get_decimal(s) for s in quartet])
        # decimals[0] = shortest price, decimals[-1] = longest

        def singles_return(winners_dec: List[float]) -> float:
            return sum(stake * d for d in winners_dec)

        def doubles_return(winners_dec: List[float]) -> float:
            return sum(
                stake * a * b
                for a, b in itertools.combinations(winners_dec, 2)
            ) if len(winners_dec) >= 2 else 0.0

        def trebles_return(winners_dec: List[float]) -> float:
            return sum(
                stake * a * b * c
                for a, b, c in itertools.combinations(winners_dec, 3)
            ) if len(winners_dec) >= 3 else 0.0

        def fourfold_return(winners_dec: List[float]) -> float:
            if len(winners_dec) < 4:
                return 0.0
            return stake * reduce(lambda x, y: x * y, winners_dec)

        def calc_total_return(winners_dec: List[float]) -> float:
            return (
                singles_return(winners_dec)
                + doubles_return(winners_dec)
                + trebles_return(winners_dec)
                + fourfold_return(winners_dec)
            )

        scenarios = {}

        # 1 winner — only singles pay
        min_1 = round(calc_total_return([decimals[0]]), 2)
        max_1 = round(calc_total_return([decimals[-1]]), 2)
        scenarios["1_winner"] = {
            "min_return": min_1,
            "max_return": max_1,
            "min_profit": round(min_1 - total_l15_stake, 2),
        }

        # 2 winners
        min_2 = round(calc_total_return(decimals[:2]), 2)
        max_2 = round(calc_total_return(decimals[-2:]), 2)
        scenarios["2_winners"] = {
            "min_return": min_2,
            "max_return": max_2,
            "min_profit": round(min_2 - total_l15_stake, 2),
        }

        # 3 winners
        min_3 = round(calc_total_return(decimals[:3]), 2)
        max_3 = round(calc_total_return(decimals[-3:]), 2)
        scenarios["3_winners"] = {
            "min_return": min_3,
            "max_return": max_3,
            "min_profit": round(min_3 - total_l15_stake, 2),
        }

        # 4 winners
        all_4 = round(calc_total_return(decimals), 2)
        scenarios["4_winners"] = {
            "min_return": all_4,
            "max_return": all_4,
            "min_profit": round(all_4 - total_l15_stake, 2),
        }

        return scenarios

    # ── Six-timer accumulator ──────────────────────────────────

    def _sixtimer_combined_decimal(self) -> float:
        """Product of all decimals in the full pool (no price filter)."""
        if not self.pool:
            return 1.0
        result = 1.0
        for s in self.pool:
            result *= self._get_decimal(s)
        return round(result, 2)

    def _sixtimer_projected_return(self) -> float:
        return round(self.sixtimer_stake * self._sixtimer_combined_decimal(), 2)

    # ── Helpers ───────────────────────────────────────────────

    def _get_decimal(self, sel: dict) -> float:
        """Extract decimal odds from a selection, falling back to odds_str parse."""
        try:
            dec = float(sel.get("decimal") or 0)
            if dec > 0:
                return dec
        except Exception:
            pass
        return _to_decimal(sel.get("odds_str", "2.0"))

    def _selection_summary(self, sel: dict) -> Dict[str, Any]:
        dec = self._get_decimal(sel)
        return {
            "horse":    sel.get("horse", "Unknown"),
            "course":   sel.get("course", ""),
            "time":     sel.get("time", ""),
            "tier":     _assign_tier(dec),
            "odds_str": sel.get("odds_str", str(dec)),
            "decimal":  dec,
        }

    def _tier_breakdown(self, quartet: List[Dict]) -> Dict[str, str]:
        """Return {horse_name: tier} for the Lucky 15 quartet."""
        breakdown = {}
        for s in quartet:
            dec = self._get_decimal(s)
            breakdown[s.get("horse", "Unknown")] = _assign_tier(dec)
        return breakdown
