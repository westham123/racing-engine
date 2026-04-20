# Racing Engine — Accumulator Efficiency Engine
# Version: 0.6
# Date: 20 April 2026
# Calculates true probability, expected value, and coverage options for accas

from itertools import combinations
import pandas as pd
import numpy as np


def odds_to_probability(odds_str: str) -> float:
    """Convert fractional odds string (e.g. '5/4') to implied probability."""
    try:
        if "/" in odds_str:
            num, den = odds_str.split("/")
            return float(den) / (float(num) + float(den))
        elif odds_str.endswith("/1") or odds_str.replace(".", "").isdigit():
            dec = float(odds_str)
            return 1 / dec
        return 0.5
    except:
        return 0.5


def probability_to_odds(prob: float) -> str:
    """Convert probability to approximate fractional odds string."""
    if prob <= 0 or prob >= 1:
        return "N/A"
    decimal = 1 / prob
    # Round to nearest common fraction
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
    """
    Analyses accumulator selections for efficiency, expected value,
    and coverage options across multiple races.
    """

    def analyse_selections(self, selections: list) -> dict:
        """
        Takes list of selections (each with confidence score and odds).
        Returns full efficiency analysis.
        """
        results = []

        for sel in selections:
            bookie_prob   = odds_to_probability(sel["odds"])
            engine_prob   = sel["confidence"]
            edge          = engine_prob - bookie_prob
            ev            = (engine_prob * (1 / bookie_prob - 1)) - (1 - engine_prob)

            results.append({
                **sel,
                "bookie_prob":  round(bookie_prob * 100, 1),
                "engine_prob":  round(engine_prob * 100, 1),
                "edge":         round(edge * 100, 1),
                "expected_value": round(ev, 3),
                "ev_rating":    "✅ Value" if ev > 0.05 else "⚠️ Marginal" if ev > 0 else "❌ No Value",
            })

        return results

    def build_permutations(self, selections: list, min_legs: int = 2, max_legs: int = 6) -> list:
        """
        Build all accumulator permutations from selections.
        Returns ranked by combined confidence and expected value.
        """
        perms = []

        for n_legs in range(min_legs, min(max_legs + 1, len(selections) + 1)):
            for combo in combinations(selections, n_legs):
                # Combined engine probability (product of individual probs)
                combined_engine_prob = np.prod([s["confidence"] for s in combo])

                # Combined bookie probability
                combined_bookie_prob = np.prod([odds_to_probability(s["odds"]) for s in combo])

                # Combined decimal odds
                combined_decimal = np.prod([(1 / odds_to_probability(s["odds"])) for s in combo])

                # Expected value of the acca
                ev = (combined_engine_prob * combined_decimal) - 1

                # Bet type name
                type_names = {2: "Double", 3: "Treble", 4: "Lucky 15 leg", 5: "Lucky 31 leg", 6: "Lucky 63 leg"}
                bet_type = type_names.get(n_legs, f"{n_legs}-fold")

                perms.append({
                    "type":                bet_type,
                    "legs":                n_legs,
                    "selections":          " + ".join([s["horse"] for s in combo]),
                    "races":               " | ".join([s["race"] for s in combo]),
                    "combined_engine_prob": round(combined_engine_prob * 100, 1),
                    "combined_bookie_prob": round(combined_bookie_prob * 100, 1),
                    "combined_odds":       f"{combined_decimal - 1:.1f}/1",
                    "expected_value":      round(ev, 3),
                    "ev_rating":           "✅ Value" if ev > 0.1 else "⚠️ Marginal" if ev > 0 else "❌ Avoid",
                    "confidence_gap":      round((combined_engine_prob - combined_bookie_prob * 100), 1),
                })

        # Sort by expected value descending
        perms.sort(key=lambda x: x["expected_value"], reverse=True)
        return perms

    def coverage_options(self, race: dict, top_n: int = 3) -> list:
        """
        For a single race, shows how covering top N runners
        changes the probability of landing that leg.
        Returns coverage options 1 through top_n.
        """
        runners = sorted(race["runners"], key=lambda x: x["confidence"], reverse=True)
        options = []

        for n in range(1, min(top_n + 1, len(runners) + 1)):
            covered = runners[:n]
            coverage_prob = sum([r["confidence"] for r in covered])
            coverage_prob = min(coverage_prob, 0.99)  # Cap at 99%

            options.append({
                "cover_n":      n,
                "horses":       ", ".join([r["horse"] for r in covered]),
                "odds":         ", ".join([r["odds"] for r in covered]),
                "coverage_prob": round(coverage_prob * 100, 1),
                "stake_multiplier": n,
                "label":        "Single selection" if n == 1 else f"Cover top {n}",
                "recommendation": "✅ Recommended" if n == 1 and covered[0]["confidence"] >= 0.80
                                   else "⚠️ Consider covering" if coverage_prob < 0.70
                                   else "ℹ️ Optional cover"
            })

        return options

    def full_day_analysis(self, daily_races: list) -> dict:
        """
        Full day analysis — takes all races, all selections,
        returns: selection analysis, top permutations, coverage options per race.
        """
        all_selections = []
        for race in daily_races:
            top_runner = max(race["runners"], key=lambda x: x["confidence"])
            top_runner["race"] = race["race"]
            all_selections.append(top_runner)

        selection_analysis = self.analyse_selections(all_selections)
        permutations       = self.build_permutations(all_selections)
        coverage           = {race["race"]: self.coverage_options(race) for race in daily_races}

        # Summary stats
        value_perms    = [p for p in permutations if p["ev_rating"] == "✅ Value"]
        best_perm      = permutations[0] if permutations else None
        avg_edge       = np.mean([s["edge"] for s in selection_analysis]) if selection_analysis else 0

        return {
            "selections":       selection_analysis,
            "permutations":     permutations[:20],  # Top 20
            "coverage_options": coverage,
            "summary": {
                "total_selections":  len(all_selections),
                "value_perms":       len(value_perms),
                "best_perm":         best_perm,
                "avg_edge":          round(avg_edge, 1),
                "overall_rating":    "🟢 Strong day" if avg_edge > 5 else "🟡 Mixed day" if avg_edge > 0 else "🔴 Weak day"
            }
        }
