# ==============================================================================
# TESS WORKS — Automated Unit Tests for Liquidity Guardrails (test_rules.py)
# Run in terminal: python3 -m unittest test_rules.py
# ==============================================================================

import unittest
from liquidity_engine import LiquidityEngine

class TestLiquidityGuardrails(unittest.TestCase):

    def setUp(self):
        self.engine = LiquidityEngine()

    def test_spread_bps_gate(self):
        """
        Verifies that the engine successfully blocks entries if the 
        exchange spread exceeds the maximum basis points limit.
        """
        # Mocking orderbook metrics with extremely wide spread (18 bps > 15 bps limit)
        mock_wide_ob = {
            "best_bid": 78.00,
            "best_ask": 78.15,
            "spread_bps": 19.2,
            "imbalance_ratio": 0.5
        }
        
        # Override local fetch method for isolation test
        self.engine.fetch_live_orderbook_metrics = lambda pair: mock_wide_ob
        self.engine.fetch_coinglass_heatmap_data = lambda symbol: []
        
        ok, reason = self.engine.evaluate_entry_safety("SOL/USDT", "long", 78.05)
        self.assertFalse(ok)
        self.assertIn("BANNED: Bid-ask spread too wide", reason)

    def test_liquidation_cascade_barrier(self):
        """
        Verifies that Tess is protected from entering a LONG trade when 
        positioned directly above a major LONG liquidation cascade pool.
        """
        # Mocking a massive Long Liquidation cluster sitting at $77.80 (0.3% below our entry price)
        mock_heatmap = [
            {"price": 77.80, "type": "long_liquidations", "intensity": 5.0}
        ]
        mock_tight_ob = {
            "best_bid": 78.00,
            "best_ask": 78.01,
            "spread_bps": 1.2,
            "imbalance_ratio": 0.5
        }
        
        self.engine.fetch_live_orderbook_metrics = lambda pair: mock_tight_ob
        self.engine.fetch_coinglass_heatmap_data = lambda symbol: mock_heatmap
        
        # Attempt to enter a LONG trade at $78.05, directly above the liquidation pool
        ok, reason = self.engine.evaluate_entry_safety("SOL/USDT", "long", 78.05)
        
        self.assertFalse(ok)
        self.assertIn("BANNED: Price is sitting directly above a major Long Liquidation Pool", reason)

if __name__ == '__main__':
    unittest.main()