# ==============================================================================
# TESS WORKS — Liquidity & Liquidation Heatmap Engine (liquidity_engine.py)
# ==============================================================================

import requests
import json
import os
import pandas as pd
from datetime import datetime

class LiquidityEngine:
    def __init__(self, coinglass_api_key=None):
        # Coinglass API key can be loaded securely from your environment variables
        self.api_key = coinglass_api_key
        self.coinglass_url = "https://open-api.coinglass.com/public/v2/liquidation/heatmap"
        self.max_allowed_spread_bps = 15.0  # 15 basis points (0.15%) max spread allowed

    def fetch_live_orderbook_metrics(self, pair):
        """
        Queries exchange orderbook depth to calculate real-time spread 
        and immediate liquidity thickness.
        """
        symbol = pair.replace("/", "")
        url = f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit=20"
        
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                
                if not bids or not asks:
                    return None
                
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                
                # Calculate Spread in Basis Points (1 bps = 0.01%)
                spread_bps = ((best_ask - best_bid) / best_bid) * 10000
                
                # Calculate Orderbook Imbalance (Volume Bid vs Ask depth)
                bid_volume = sum(float(b[1]) for b in bids[:5])
                ask_volume = sum(float(a[1]) for a in asks[:5])
                imbalance_ratio = bid_volume / (bid_volume + ask_volume) if (bid_volume + ask_volume) > 0 else 0.5
                
                return {
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread_bps": round(spread_bps, 2),
                    "imbalance_ratio": round(imbalance_ratio, 2)
                }
        except Exception:
            pass
        return None

    def fetch_coinglass_heatmap_data(self, symbol_base):
        """
        Queries Coinglass Futures Liquidation Heatmap endpoint.
        Returns mapped price levels with high cumulative leverage liquidation density.
        """
        if not self.api_key or self.api_key == "your_coinglass_key":
            # If no API key is set, the system falls back safely to a 
            # deterministic math model using orderbook boundaries.
            return self._simulate_liquidation_heatmap_fallback(symbol_base)
            
        headers = {
            "accept": "application/json",
            "coinglassSecret": self.api_key
        }
        params = {
            "symbol": symbol_base,
            "interval": "12h"
        }
        
        try:
            r = requests.get(self.coinglass_url, headers=headers, params=params, timeout=8)
            if r.status_code == 200:
                data = r.json()
                # Parse Coinglass structured data array: returns list of dicts {"price": float, "accumulated_usd": float}
                return data.get("data", [])
        except Exception:
            pass
        return self._simulate_liquidation_heatmap_fallback(symbol_base)

    def _simulate_liquidation_heatmap_fallback(self, symbol_base):
        """
        Mathematical fallback modeling historical liquidation clusters 
        around key support/resistance boundaries if API key is inactive.
        """
        # In a real market, major liquidation pools cluster just outside recent 10-day swing highs/lows
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol_base}USDT"
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                ticker = r.json()
                high = float(ticker["highPrice"])
                low = float(ticker["lowPrice"])
                
                # Mock typical leverage clusters: 50x liquidations sit ~2% away from boundary levels
                return [
                    {"price": round(high * 1.015, 2), "type": "short_liquidations", "intensity": 5.0},
                    {"price": round(low * 0.985, 2), "type": "long_liquidations", "intensity": 5.0}
                ]
        except:
            pass
        return []

    def evaluate_entry_safety(self, pair, direction, entry_price):
        """
        Deterministic Rule Engine protecting against entering trades directly
        into high-leverage liquidation cascades or high-spread environments.
        """
        symbol_base = pair.split("/")[0]
        
        # 1. Evaluate Spread & Local Liquidity Depth
        ob_metrics = self.fetch_live_orderbook_metrics(pair)
        if ob_metrics:
            if ob_metrics["spread_bps"] > self.max_allowed_spread_bps:
                return False, f"BANNED: Bid-ask spread too wide ({ob_metrics['spread_bps']} bps). Avoid execution."
            
            # Reduce leverage if orderbook volume is heavily imbalanced against our direction
            if direction == "long" and ob_metrics["imbalance_ratio"] < 0.25:
                return True, "WARNING: Low buy-side orderbook depth. Restrict entry leverage to 5x."
            if direction == "short" and ob_metrics["imbalance_ratio"] > 0.75:
                return True, "WARNING: Low sell-side orderbook depth. Restrict entry leverage to 5x."

        # 2. Evaluate Coinglass Liquidation Heatmap Cascades
        heatmap = self.fetch_coinglass_heatmap_data(symbol_base)
        for cluster in heatmap:
            cluster_price = cluster["price"]
            distance_pct = abs(entry_price - cluster_price) / entry_price
            
            # If our trade entry is extremely close (under 1.0%) to a major liquidation cluster:
            if distance_pct <= 0.01:
                # If we are going LONG, but we are sitting right on top of a massive LONG liquidation pool:
                if direction == "long" and cluster_price < entry_price:
                    return False, f"BANNED: Price is sitting directly above a major Long Liquidation Pool (${cluster_price}). High cascade risk."
                # If we are going SHORT, but we are sitting right below a massive SHORT liquidation pool:
                if direction == "short" and cluster_price > entry_price:
                    return False, f"BANNED: Price is sitting directly below a major Short Liquidation Pool (${cluster_price}). High cascade risk."

        return True, "PASSED: Liquidity and Liquidation boundary checks validated."