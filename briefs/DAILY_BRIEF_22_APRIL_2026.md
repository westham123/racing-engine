# Racing Engine — Daily Brief
## Wednesday 22 April 2026

---

## Yesterday's Results (21 April 2026)

| Time (BST) | Horse | Confidence | SP | Result |
|---|---|---|---|---|
| 14:17 | Lady Youmzain | 71.9% | 4/5 | **WON** |
| 14:35 | Brilliant Star | 57.2% | 2/9 | **WON** |
| 16:02 | Yorkshire Glory | 68.5% | — | **LOST** (Diamont Katie 10/3 won) |
| 16:38 | Crystal Island | 65.0% | Evs | **WON** |
| 18:30 | Beaune | 67.6% | 5/4 | **WON** |

**P&L on £50 budget: +£29.47**
- Accumulator (£30): Lost — Yorkshire Glory failed
- Lucky 15 (£20 across 15 bets): Returned £79.47

**Note — Selection Integrity**:
Final Appeal and Trust House were mentioned by the engine during the day but NEVER officially qualified — they were below 55% confidence at the time the engine was live and functioning. Only the 5 horses above are official selections.

**Yorkshire Glory loss diagnosis**: Diamont Katie (10/3) won the race. Diamont Katie was not present in the Sporting Life feed at scoring time — this is a data coverage gap, not a model error. The filter layer built tonight (large field exclusion, dual signal requirement, handicap uplift) should reduce this class of loss going forward.

---

## What Was Built Tonight (v2.5.0 → v2.5.3)

### v2.5.0 — Full cleanup
- Removed ALL hardcoded stale horses (Mister Mojito, Kaaranah, Daizen, Lillistar, Eightthreeone)
- Fixed BST timezone across sidebar, alerts, going report timestamp
- Results/acca efficiency/pending table now use live data only

### v2.5.1 — Filter layer
Three mechanisms applied BEFORE scoring:
1. **Large field exclusion**: 12+ runners → excluded entirely
2. **Dual signal requirement**: horse must clear 2+ of: decent form (≥0.50), TF stars ≥4, market shortening, implied prob ≥40%
3. **Handicap uplift**: handicap races require confidence + 10% (55% → 65%)

### v2.5.2 — Email + automation
- `daily_brief.py` rebuilt from scratch — 4 email types, live data only, no sample horses
- `early_market.py` — tomorrow's market monitor (opening snapshot + hourly mover detection)
- `bump_version.py` — auto-increments patch version on every commit
- Pre-commit git hook — runs bump_version.py automatically

### v2.5.3 — Staking plan v2.0
- Clear colour-coded banner: which plan applies today and why
- 4-column KPI summary: Budget / Accumulator stake / Lucky 15 stake / Combined odds
- "What do these bets mean?" explainer (collapsible)
- Accumulator table with all selections
- Lucky 15 return scenarios table: 1 / 2 / 3 / 4 winners with net vs stake
- **Full day P&L matrix**: every combination of acc win/loss vs L15 outcome, colour coded green/red

---

## Staking Plan (as built — applies from today)

| Situation | Plan |
|---|---|
| 4+ selections qualify above 4/6 | 60% accumulator + 40% Lucky 15 (auto-selected 4 highest EV horses) |
| <4 selections qualify above 4/6 | 100% accumulator only |
| Any single selection below 4/6 | Hard excluded from ALL bets |
| Confidence below 55% | Excluded from all bets |
| Handicap race | Confidence threshold raised to 65% |

**On £50 budget with 4+ qualifying horses:**
- Accumulator stake: £30
- Lucky 15 stake: £20 (= £1.33 per bet × 15 bets)

---

## Scheduled Automations Active

| Task | Time | What it does |
|---|---|---|
| Morning Brief email | 08:00 BST daily | Sends today's declared runners, selections, staking plan to richardking123@outlook.com |
| Evening Summary email | 19:00 BST daily | Sends results vs selections, P&L to richardking123@outlook.com |

---

## Tomorrow Morning Checklist

1. **~10:00 BST** — Declarations publish on Sporting Life. Run `take_opening_snapshot()` in `early_market.py` to capture opening prices.
2. **Check morning brief email** arrived at richardking123@outlook.com (sent at 08:00 BST automatically).
3. **Open dashboard** at https://racing-engine-dash.streamlit.app (PIN: 1012) — confirm version shows v2.5.3.
4. **Review early market movers** — run `get_market_movers()` in `early_market.py` to see any horses shortening overnight.
5. **Review staking plan** in Tab 1 — the new Full Day P&L matrix shows every scenario in green/red.

---

## Loose Ends / Known Issues

| Item | Status | Notes |
|---|---|---|
| Early market snapshot | Ready but not yet tested live | Declarations publish ~10:00 BST — run `take_opening_snapshot()` then |
| Tomorrow's card | Not yet published | Sporting Life only showing 21 April at time of writing |
| Email delivery test | Not yet run live | First real test will be 08:00 BST morning brief |
| Selection integrity display | Improved in staking plan | Clear banner distinguishes official selections from engine mentions |

---

## Key Reference

- **Dashboard**: https://racing-engine-dash.streamlit.app — PIN: 1012
- **GitHub**: https://github.com/westham123/racing-engine — current version v2.5.3
- **Confidence threshold**: 55% default (65% for handicaps)
- **Price cut-off**: 4/6 (1.67 decimal) — hard exclusion from all bets
- **Budget default**: £50/day

---

*Brief generated: Tuesday 21 April 2026, 19:14 BST*
