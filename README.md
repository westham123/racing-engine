# Racing Engine — Phase 1: Personal Research Tool

A hybrid horse racing logic engine covering UK and Irish racing.

## Project Rules
- Phase 1: Personal research tool only — no commercial activity
- Code versioned and pushed to GitHub regularly
- All technical decisions documented

## Engine Features
- Hybrid odds model (market base + 8 data signals)
- Real-time market move monitoring
- Intelligent accumulator permutation engine
- Self-learning loop (recommendations vs outcomes)
- Real-time alert system
- Automated settlement with exception flagging
- Daily brief generator

## Data Sources (Phase 1 — Free Tier)
- The Racing API (free tier)
- Betfair Exchange API (delayed dev key)
- BHA going reports (public)
- Horse Racing Ireland (public)

## Project Structure
```
racing-engine/
├── config/         # API keys and settings
├── data/           # Data ingestion layer
├── engine/         # Hybrid odds model and confidence scoring
├── alerts/         # Real-time alert system
├── permutations/   # Accumulator permutation engine
├── learning/       # Learning loop — records and adjusts weightings
├── settlement/     # Settlement engine and exception flagging
├── briefs/         # Daily brief generator
└── tests/          # Testing and validation
```

## Version History
- v0.1 — 20 April 2026 — Project structure and rules established
