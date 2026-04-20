# Racing Engine — Main Entry Point
# Version: 0.1
# Date: 20 April 2026
# Phase 1: Personal Research Tool

"""
Horse Racing Logic Engine
UK + Irish racing coverage
Hybrid odds model + 8 data signals + learning loop

Run this file to start the engine.
"""

from data.ingest import DataIngestion
from engine.odds_model import OddsModel
from permutations.acca_builder import AccaBuilder
from learning.loop import LearningLoop
from alerts.monitor import AlertMonitor
from settlement.settle import SettlementEngine
from briefs.daily_brief import DailyBrief

def main():
    print("Racing Engine v0.1 — Starting...")
    print("Phase 1: Personal Research Tool")
    print("Coverage: UK + Irish Racing")
    print("Status: Initialising modules...")

    # Initialise all engine components
    data      = DataIngestion()
    model     = OddsModel()
    accas     = AccaBuilder()
    learning  = LearningLoop()
    alerts    = AlertMonitor()
    settlement = SettlementEngine()
    brief     = DailyBrief()

    print("All modules loaded. Engine ready.")
    print("v0.1 — Structure complete. Data connections to be built in v0.2.")

if __name__ == "__main__":
    main()
