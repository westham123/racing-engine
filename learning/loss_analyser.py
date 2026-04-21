# Racing Engine — Loss Analyser
# Version: 1.0
# Date: 21 April 2026
#
# After each settled race where the engine's selection LOST, automatically
# analyses WHY using available signals. Maintains a fault ledger in
# loss_analysis.json, applies weight nudges, and generates HTML for the
# daily email brief.

import os
import json
from datetime import datetime, date
from typing import Any, Dict, List, Optional

LOSS_ANALYSIS_PATH = os.path.join(os.path.dirname(__file__), "loss_analysis.json")
WEIGHTS_PATH       = os.path.join(os.path.dirname(__file__), "learned_weights.json")

# Fault → signal mapping
FAULT_TO_SIGNAL: Dict[str, Optional[str]] = {
    "GOING_MISMATCH":       "going",
    "MARKET_DRIFT":         "market_moves",
    "MARKET_STEAM_RIVAL":   "bsp_signal",
    "FORM_REGRESSION":      "horse_form",
    "TRAINER_COLD":         "trainer_form",
    "RACE_TYPE_MISMATCH":   None,
}

ALL_SIGNALS = ["going", "market_moves", "horse_form", "trainer_form", "bsp_signal", "jockey_form"]

FAULT_ORDER = [
    "GOING_MISMATCH",
    "MARKET_DRIFT",
    "MARKET_STEAM_RIVAL",
    "FORM_REGRESSION",
    "TRAINER_COLD",
    "RACE_TYPE_MISMATCH",
]

_EMPTY_STORE: Dict[str, Any] = {
    "losses":                     [],
    "signal_fault_counts":        {s: 0 for s in ALL_SIGNALS},
    "race_type_hit_rates":        {},
    "weight_adjustments_applied": [],
}


# ── JSON helpers ───────────────────────────────────────────────

def _load_store() -> Dict[str, Any]:
    try:
        if os.path.exists(LOSS_ANALYSIS_PATH):
            with open(LOSS_ANALYSIS_PATH) as f:
                data = json.load(f)
            for k, v in _EMPTY_STORE.items():
                data.setdefault(k, v)
            for sig in ALL_SIGNALS:
                data["signal_fault_counts"].setdefault(sig, 0)
            return data
    except Exception:
        pass
    return {
        "losses": [],
        "signal_fault_counts": {s: 0 for s in ALL_SIGNALS},
        "race_type_hit_rates": {},
        "weight_adjustments_applied": [],
    }


def _save_store(data: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(LOSS_ANALYSIS_PATH), exist_ok=True)
        with open(LOSS_ANALYSIS_PATH, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"[LossAnalyser] Save failed: {e}")


def _load_weights() -> Dict[str, float]:
    _defaults = {
        "market_odds": 0.25, "horse_form": 0.20, "track_form": 0.15,
        "going": 0.10, "trainer_form": 0.10, "jockey_form": 0.10,
        "market_moves": 0.07, "jump_index": 0.03,
    }
    try:
        if os.path.exists(WEIGHTS_PATH):
            with open(WEIGHTS_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return _defaults


def _save_weights(weights: Dict[str, float]) -> None:
    try:
        os.makedirs(os.path.dirname(WEIGHTS_PATH), exist_ok=True)
        with open(WEIGHTS_PATH, "w") as f:
            json.dump(weights, f, indent=2)
    except Exception as e:
        print(f"[LossAnalyser] Weight save failed: {e}")


# ── LossAnalyser class ─────────────────────────────────────────

class LossAnalyser:
    """
    Analyses settled losses and maintains a fault ledger in
    loss_analysis.json.

    Fault categories checked (in order):
        GOING_MISMATCH       — forecast going vs actual going
        MARKET_DRIFT         — horse drifted 20%+ before off
        MARKET_STEAM_RIVAL   — rival steamed in vs stored prices
        FORM_REGRESSION      — 3+ recent placings but finished 4th or worse
        TRAINER_COLD         — trainer rolling 14-day win rate < 8%
        RACE_TYPE_MISMATCH   — engine win rate on race type < 25%
    """

    def __init__(self):
        self.store = _load_store()

    # ── Core analysis ──────────────────────────────────────────

    def analyse_loss(self, loss_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyse a single settled loss and record findings.

        Required keys in loss_data:
            horse, course, race_type, date

        Optional (supply for richer diagnostics):
            selection_going, result_going,
            selection_decimal, sp_decimal,
            winner_sp_decimal, rival_stored_odds,
            recent_form_places, finish_position,
            trainer_win_rate_14d,
            race_type_hits, race_type_settled
        """
        try:
            faults         = self._detect_faults(loss_data)
            primary_fault  = faults[0] if faults else "UNKNOWN"
            signal_to_adj  = FAULT_TO_SIGNAL.get(primary_fault)
            adj_direction  = "down" if faults else "neutral"
            adj_magnitude  = 0.01 if faults else 0.0
            notes          = self._build_notes(faults, loss_data)

            record = {
                "date":                  loss_data.get("date", date.today().isoformat()),
                "horse":                 loss_data.get("horse", "Unknown"),
                "course":                loss_data.get("course", ""),
                "race_type":             loss_data.get("race_type", ""),
                "fault_categories":      faults,
                "primary_fault":         primary_fault,
                "signal_to_adjust":      signal_to_adj,
                "adjustment_direction":  adj_direction,
                "adjustment_magnitude":  adj_magnitude,
                "notes":                 notes,
            }

            self.store["losses"].append(record)

            # Update signal fault counts
            for fault in faults:
                sig = FAULT_TO_SIGNAL.get(fault)
                if sig and sig in self.store["signal_fault_counts"]:
                    self.store["signal_fault_counts"][sig] += 1

            # Update race type hit rate (loss)
            self._update_race_type_hit_rate(loss_data.get("race_type", ""), won=False)

            _save_store(self.store)
            return record

        except Exception as e:
            print(f"[LossAnalyser] analyse_loss error: {e}")
            return {}

    def record_win(self, race_type: str) -> None:
        """Record a win for race_type hit-rate tracking."""
        try:
            self._update_race_type_hit_rate(race_type, won=True)
            _save_store(self.store)
        except Exception as e:
            print(f"[LossAnalyser] record_win error: {e}")

    # ── Fault detection ────────────────────────────────────────

    def _detect_faults(self, d: Dict[str, Any]) -> List[str]:
        faults = []
        for fault in FAULT_ORDER:
            try:
                if self._check_fault(fault, d):
                    faults.append(fault)
            except Exception:
                pass
        return faults

    def _check_fault(self, fault: str, d: Dict[str, Any]) -> bool:
        if fault == "GOING_MISMATCH":
            sel_going = (d.get("selection_going") or "").strip().lower()
            res_going = (d.get("result_going") or "").strip().lower()
            return bool(sel_going and res_going and sel_going != res_going)

        elif fault == "MARKET_DRIFT":
            try:
                sel_dec = float(d.get("selection_decimal") or 0)
                sp_dec  = float(d.get("sp_decimal") or 0)
                return sel_dec > 0 and sp_dec > 0 and sp_dec > sel_dec * 1.20
            except Exception:
                return False

        elif fault == "MARKET_STEAM_RIVAL":
            try:
                winner_sp   = float(d.get("winner_sp_decimal") or 0)
                rival_odds  = d.get("rival_stored_odds") or {}
                if winner_sp > 0 and rival_odds:
                    for _, rival_dec in rival_odds.items():
                        try:
                            if float(rival_dec) > winner_sp * 1.25:
                                return True
                        except Exception:
                            pass
                return False
            except Exception:
                return False

        elif fault == "FORM_REGRESSION":
            try:
                placings = int(d.get("recent_form_places") or 0)
                finish   = int(d.get("finish_position") or 99)
                return placings >= 3 and finish >= 4
            except Exception:
                return False

        elif fault == "TRAINER_COLD":
            try:
                win_rate = float(d.get("trainer_win_rate_14d") or -1)
                return 0 <= win_rate < 0.08
            except Exception:
                return False

        elif fault == "RACE_TYPE_MISMATCH":
            race_type = d.get("race_type", "")
            if not race_type:
                return False
            rt_data = self.store["race_type_hit_rates"].get(race_type)
            if rt_data and rt_data.get("settled", 0) >= 5:
                return rt_data.get("hit_rate_pct", 100.0) < 25.0
            try:
                hits    = int(d.get("race_type_hits") or 0)
                settled = int(d.get("race_type_settled") or 0)
                if settled >= 5:
                    return (hits / settled) * 100 < 25.0
            except Exception:
                pass
            return False

        return False

    # ── Notes ─────────────────────────────────────────────────

    def _build_notes(self, faults: List[str], d: Dict[str, Any]) -> str:
        if not faults:
            return "No dominant fault category identified from available signals."
        parts = []
        for fault in faults:
            if fault == "GOING_MISMATCH":
                parts.append(
                    f"Forecast {d.get('selection_going', '?')}, "
                    f"actual {d.get('result_going', '?')}. "
                    "Engine overweighted going signal."
                )
            elif fault == "MARKET_DRIFT":
                parts.append(
                    f"Horse drifted from {d.get('selection_decimal','?')} "
                    f"to {d.get('sp_decimal','?')} SP (20%+ move)."
                )
            elif fault == "MARKET_STEAM_RIVAL":
                parts.append("A rival steamed in significantly vs stored prices.")
            elif fault == "FORM_REGRESSION":
                parts.append(
                    f"Horse had {d.get('recent_form_places','?')} recent placings "
                    f"but finished {d.get('finish_position','?')}."
                )
            elif fault == "TRAINER_COLD":
                wr = d.get("trainer_win_rate_14d")
                parts.append(
                    "Trainer win rate over 14 days: "
                    f"{f'{wr:.1%}' if wr is not None else 'unknown'} (<8%)."
                )
            elif fault == "RACE_TYPE_MISMATCH":
                rt = d.get("race_type", "this race type")
                parts.append(f"Engine consistently underperforms in {rt} (<25% hit rate).")
        return " | ".join(parts)

    # ── Race-type hit rate ─────────────────────────────────────

    def _update_race_type_hit_rate(self, race_type: str, won: bool) -> None:
        if not race_type:
            return
        rt = self.store["race_type_hit_rates"]
        if race_type not in rt:
            rt[race_type] = {"settled": 0, "hits": 0, "hit_rate_pct": 0.0}
        rt[race_type]["settled"] += 1
        if won:
            rt[race_type]["hits"] += 1
        settled = rt[race_type]["settled"]
        hits    = rt[race_type]["hits"]
        rt[race_type]["hit_rate_pct"] = round(hits / settled * 100, 1) if settled else 0.0

    # ── Weight adjustment ──────────────────────────────────────

    def apply_weight_adjustments(self, current_weights: Dict[str, float]) -> Dict[str, float]:
        """
        For any signal with 3+ faults: nudge weight DOWN by 0.01 (min 0.02).
        Redistributes removed weight to signals with 0–1 faults.
        Logs the adjustment and returns updated weights dict.
        """
        try:
            fault_counts  = self.store["signal_fault_counts"]
            weights       = dict(current_weights)
            total_removed = 0.0
            MIN_WEIGHT    = 0.02
            NUDGE         = 0.01
            adjustments_made: List[Dict] = []

            to_nudge_down = [
                sig for sig, cnt in fault_counts.items()
                if cnt >= 3 and sig in weights and weights[sig] - NUDGE >= MIN_WEIGHT
            ]

            if not to_nudge_down:
                return weights

            for sig in to_nudge_down:
                old_w      = weights[sig]
                new_w      = max(old_w - NUDGE, MIN_WEIGHT)
                removed    = old_w - new_w
                total_removed += removed
                weights[sig]  = new_w
                adjustments_made.append({
                    "date":       date.today().isoformat(),
                    "signal":     sig,
                    "old_weight": round(old_w, 4),
                    "new_weight": round(new_w, 4),
                    "reason":     f"{fault_counts[sig]} {sig} faults recorded",
                })

            # Redistribute to low-fault signals
            recipients = [
                sig for sig in weights
                if fault_counts.get(sig, 0) <= 1 and sig not in to_nudge_down
            ]
            if recipients and total_removed > 0:
                share = total_removed / len(recipients)
                for sig in recipients:
                    weights[sig] = round(weights[sig] + share, 4)

            # Renormalise
            total = sum(weights.values())
            if total > 0:
                weights = {k: round(v / total, 4) for k, v in weights.items()}

            self.store["weight_adjustments_applied"].extend(adjustments_made)
            _save_store(self.store)
            _save_weights(weights)

            for adj in adjustments_made:
                print(
                    f"[LossAnalyser] Weight adjusted: {adj['signal']} "
                    f"{adj['old_weight']} -> {adj['new_weight']}  ({adj['reason']})"
                )

            return weights

        except Exception as e:
            print(f"[LossAnalyser] apply_weight_adjustments error: {e}")
            return current_weights

    # ── Summary ───────────────────────────────────────────────

    def get_loss_summary(self) -> Dict[str, Any]:
        """
        Returns:
            total_losses, most_common_fault, worst_signal,
            best_signal, race_type_weaknesses
        """
        try:
            from collections import Counter
            losses       = self.store["losses"]
            fault_counts = self.store["signal_fault_counts"]
            rt_rates     = self.store["race_type_hit_rates"]

            all_faults: List[str] = []
            for loss in losses:
                all_faults.extend(loss.get("fault_categories", []))
            fault_counter  = Counter(all_faults)
            most_common    = fault_counter.most_common(1)[0][0] if fault_counter else None

            worst_signal  = max(fault_counts, key=lambda s: fault_counts[s]) if fault_counts else None
            best_signal   = min(fault_counts, key=lambda s: fault_counts[s]) if fault_counts else None

            weaknesses = [
                rt for rt, d in rt_rates.items()
                if d.get("settled", 0) >= 5 and d.get("hit_rate_pct", 100.0) < 25.0
            ]

            return {
                "total_losses":         len(losses),
                "most_common_fault":    most_common,
                "worst_signal":         worst_signal,
                "best_signal":          best_signal,
                "race_type_weaknesses": weaknesses,
            }
        except Exception as e:
            print(f"[LossAnalyser] get_loss_summary error: {e}")
            return {
                "total_losses": 0,
                "most_common_fault": None,
                "worst_signal": None,
                "best_signal": None,
                "race_type_weaknesses": [],
            }

    # ── HTML email snippet ────────────────────────────────────

    def generate_loss_report_html(self) -> str:
        """
        Returns an HTML snippet for the daily email brief.
        Dark theme: #0f1117 background, #e0e0e0 text.
        Includes: recent losses table, signal fault bar chart,
        weight adjustments log.
        """
        try:
            summary       = self.get_loss_summary()
            losses        = self.store["losses"]
            fault_counts  = self.store["signal_fault_counts"]
            adjustments   = self.store["weight_adjustments_applied"]
            recent_losses = losses[-10:]

            # Recent losses rows
            loss_rows = ""
            for loss in reversed(recent_losses):
                faults_str = ", ".join(loss.get("fault_categories", [])) or "—"
                note_text  = (loss.get("notes") or "")[:80]
                ellipsis   = "…" if len(loss.get("notes") or "") > 80 else ""
                loss_rows += (
                    "<tr>"
                    f"<td style='padding:7px 10px;border-bottom:1px solid #2a2a2a;'>{loss.get('date','')}</td>"
                    f"<td style='padding:7px 10px;border-bottom:1px solid #2a2a2a;font-weight:bold;'>{loss.get('horse','')}</td>"
                    f"<td style='padding:7px 10px;border-bottom:1px solid #2a2a2a;'>{loss.get('course','')}</td>"
                    f"<td style='padding:7px 10px;border-bottom:1px solid #2a2a2a;'>{loss.get('race_type','')}</td>"
                    f"<td style='padding:7px 10px;border-bottom:1px solid #2a2a2a;color:#ff9100;'>{faults_str}</td>"
                    f"<td style='padding:7px 10px;border-bottom:1px solid #2a2a2a;color:#888;font-size:11px;'>{note_text}{ellipsis}</td>"
                    "</tr>"
                )
            if not loss_rows:
                loss_rows = "<tr><td colspan='6' style='padding:10px;color:#888;'>No losses recorded yet.</td></tr>"

            # Signal fault bar chart
            max_count = max(fault_counts.values()) if fault_counts else 1
            max_count = max(max_count, 1)
            bar_cells = ""
            for sig, cnt in sorted(fault_counts.items(), key=lambda x: -x[1]):
                bar_pct    = int((cnt / max_count) * 100)
                bar_colour = "#ff1744" if cnt >= 3 else "#ff9100" if cnt >= 1 else "#2a2a2a"
                bar_cells += (
                    "<tr>"
                    f"<td style='padding:4px 10px;color:#888;font-size:12px;min-width:110px;'>{sig}</td>"
                    "<td style='padding:4px 6px;'>"
                    f"<div style='background:{bar_colour};height:14px;width:{bar_pct}%;min-width:4px;"
                    "border-radius:3px;display:inline-block;'></div>"
                    "</td>"
                    f"<td style='padding:4px 6px;color:#e0e0e0;font-size:12px;'>{cnt}</td>"
                    "</tr>"
                )

            # Weight adjustment log rows
            adj_rows = ""
            for adj in adjustments[-8:]:
                adj_rows += (
                    "<tr>"
                    f"<td style='padding:5px 10px;border-bottom:1px solid #2a2a2a;color:#888;font-size:12px;'>{adj.get('date','')}</td>"
                    f"<td style='padding:5px 10px;border-bottom:1px solid #2a2a2a;'>{adj.get('signal','')}</td>"
                    f"<td style='padding:5px 10px;border-bottom:1px solid #2a2a2a;color:#ff9100;'>{adj.get('old_weight','')}</td>"
                    f"<td style='padding:5px 10px;border-bottom:1px solid #2a2a2a;color:#00c853;'>{adj.get('new_weight','')}</td>"
                    f"<td style='padding:5px 10px;border-bottom:1px solid #2a2a2a;color:#888;font-size:11px;'>{adj.get('reason','')}</td>"
                    "</tr>"
                )
            if not adj_rows:
                adj_rows = "<tr><td colspan='5' style='padding:10px;color:#888;'>No weight adjustments applied yet.</td></tr>"

            mcf            = summary.get("most_common_fault") or "—"
            worst          = summary.get("worst_signal") or "—"
            best           = summary.get("best_signal") or "—"
            total          = summary.get("total_losses", 0)
            weaknesses_str = ", ".join(summary.get("race_type_weaknesses", [])) or "None identified"

            html = (
                "<div style='background:#0f1117;border-radius:12px;padding:20px;margin-bottom:20px;"
                "border:1px solid #2a2a2a;font-family:Arial,sans-serif;color:#e0e0e0;'>"
                "<h2 style='color:#ffffff;margin-top:0;font-size:16px;'>&#x1F50D; Loss Analysis &amp; Signal Diagnostics</h2>"
                "<div style='display:flex;gap:20px;flex-wrap:wrap;margin-bottom:16px;'>"
                f"<div style='background:#1c1f2e;border-radius:8px;padding:10px 16px;'>"
                f"<div style='color:#888;font-size:11px;text-transform:uppercase;'>Total Losses</div>"
                f"<div style='color:#ff1744;font-size:22px;font-weight:bold;'>{total}</div></div>"
                f"<div style='background:#1c1f2e;border-radius:8px;padding:10px 16px;'>"
                f"<div style='color:#888;font-size:11px;text-transform:uppercase;'>Primary Fault</div>"
                f"<div style='color:#ff9100;font-size:16px;font-weight:bold;'>{mcf}</div></div>"
                f"<div style='background:#1c1f2e;border-radius:8px;padding:10px 16px;'>"
                f"<div style='color:#888;font-size:11px;text-transform:uppercase;'>Worst Signal</div>"
                f"<div style='color:#ff9100;font-size:16px;'>{worst}</div></div>"
                f"<div style='background:#1c1f2e;border-radius:8px;padding:10px 16px;'>"
                f"<div style='color:#888;font-size:11px;text-transform:uppercase;'>Best Signal</div>"
                f"<div style='color:#00c853;font-size:16px;'>{best}</div></div>"
                f"<div style='background:#1c1f2e;border-radius:8px;padding:10px 16px;'>"
                f"<div style='color:#888;font-size:11px;text-transform:uppercase;'>Structural Weaknesses</div>"
                f"<div style='color:#888;font-size:13px;'>{weaknesses_str}</div></div>"
                "</div>"
                "<h3 style='color:#e0e0e0;font-size:14px;margin-bottom:8px;'>Recent Losses</h3>"
                "<table style='width:100%;border-collapse:collapse;font-size:12px;margin-bottom:20px;'>"
                "<thead><tr style='color:#888;text-align:left;'>"
                "<th style='padding:7px 10px;'>Date</th>"
                "<th style='padding:7px 10px;'>Horse</th>"
                "<th style='padding:7px 10px;'>Course</th>"
                "<th style='padding:7px 10px;'>Race Type</th>"
                "<th style='padding:7px 10px;'>Fault Categories</th>"
                "<th style='padding:7px 10px;'>Notes</th>"
                f"</tr></thead><tbody>{loss_rows}</tbody></table>"
                "<h3 style='color:#e0e0e0;font-size:14px;margin-bottom:8px;'>Signal Fault Counts</h3>"
                f"<table style='border-collapse:collapse;margin-bottom:20px;'>{bar_cells}</table>"
                "<h3 style='color:#e0e0e0;font-size:14px;margin-bottom:8px;'>Weight Adjustments Applied</h3>"
                "<table style='width:100%;border-collapse:collapse;font-size:12px;'>"
                "<thead><tr style='color:#888;text-align:left;'>"
                "<th style='padding:5px 10px;'>Date</th>"
                "<th style='padding:5px 10px;'>Signal</th>"
                "<th style='padding:5px 10px;'>Old Weight</th>"
                "<th style='padding:5px 10px;'>New Weight</th>"
                "<th style='padding:5px 10px;'>Reason</th>"
                f"</tr></thead><tbody>{adj_rows}</tbody></table>"
                "</div>"
            )
            return html

        except Exception as e:
            print(f"[LossAnalyser] generate_loss_report_html error: {e}")
            return (
                "<div style='background:#0f1117;color:#888;padding:16px;'>"
                f"Loss report unavailable: {e}</div>"
            )


# ── Module-level convenience wrappers ────────────────────────────────────────
# These allow settle.py and send_brief.py to call functions directly
# without needing to instantiate the class.

def diagnose_loss(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Module-level wrapper — diagnose why a tipped horse lost.
    Called by settle.py after each missed recommendation.

    Parameters
    ----------
    result : dict  (same schema as LossAnalyser.analyse_loss)

    Returns
    -------
    dict with keys: horse, faults (list), weight_adjustments (dict), summary (str)
    """
    analyser  = LossAnalyser()
    outcome   = analyser.analyse_loss(result)
    new_weights = analyser.apply_weight_adjustments(_load_weights())
    _save_weights(new_weights)
    return {
        "horse":              result.get("horse", "Unknown"),
        "faults":             outcome.get("faults_found", []),
        "weight_adjustments": {},   # handled inside apply_weight_adjustments
        "summary":            outcome.get("notes", "No diagnosis available"),
    }


def get_loss_report(last_n: int = 10) -> str:
    """
    Module-level wrapper — plain-text loss report for dashboard.
    """
    try:
        store   = _load_store()
        summary = LossAnalyser().get_loss_summary()
        log     = store.get("losses", [])[-last_n:]
        weights = _load_weights()

        lines = ["=== Loss Learning Report ===", ""]
        if not log:
            lines.append("No losses recorded yet.")
        else:
            lines.append(f"Last {len(log)} settled losses:")
            for entry in reversed(log):
                faults_str = ", ".join(entry.get("faults_found", [])) or "No fault identified"
                lines.append(
                    f"  {entry.get('race_date', '')} | {entry.get('horse', '')}"
                    f" @ {entry.get('course', '')} {entry.get('time', '')}"
                    f" → {faults_str}"
                )

        lines += ["", "Fault totals (all time):"]
        fault_counts = store.get("fault_counts", {})
        for fault, count in sorted(fault_counts.items(), key=lambda x: -x[1]):
            flag = " ← WEIGHT ADJUSTED" if count >= 3 else ""
            lines.append(f"  {fault:<30s}: {count}{flag}")

        lines += ["", "Current signal weights:"]
        for sig, w in sorted(weights.items(), key=lambda x: -x[1]):
            lines.append(f"  {sig:<20s}: {w:.4f}")

        return "\n".join(lines)
    except Exception as e:
        return f"Loss report unavailable: {e}"


def get_loss_report_html(last_n: int = 10) -> str:
    """
    Module-level wrapper — HTML loss report for daily email.
    """
    try:
        return LossAnalyser().generate_loss_report_html()
    except Exception as e:
        return f"<!-- Loss report HTML unavailable: {e} -->"
