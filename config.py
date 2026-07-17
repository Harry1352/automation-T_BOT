# ==============================================================================
# TESS — Unified Configuration File (config.py)
# ==============================================================================
from keys import (
    DELTA_API_KEY,
    DELTA_API_SECRET,
    NVIDIA_NEMOTRON_KEY,
    GROQ_API_KEY_2,
    TESS_GMAIL,
    TESS_GMAIL_APP_PASSWORD,
    YOUR_EMAIL,
    GOOGLE_STUDIO_API_KEY  
)

# Rename the key standard for backward compatibility with your existing scanner
GEMINI_API_KEY = NVIDIA_NEMOTRON_KEY

# --- Are we in paper mode or real mode? ---
PAPER_MODE = True
PAPER_BALANCE = 180.0  # Simulated $10,000 to test with

# --- What Tess trades ---
TRADING_PAIRS = ["ETH/USDT", "SOL/USDT", "BTC/USDT", "XAUT/USDT"]

# --- Sizing & Safety Parameters ---
MIN_CONFIDENCE = 75        
MAX_RISK_PER_TRADE = 2.0     
DAILY_LOSS_LIMIT = 200.0     
REWARD_TO_RISK = 3.5       

# --- Technical Scan Interval ---
SCAN_INTERVAL_MINUTES = 5  

# --- Session Control ---
SESSION_GATE_ACTIVE = False

# --- Adaptive Indicator Tuning ---
# Lower to 10.0-15.0 for active trading; raise to 20.0 for strict trend-following
MIN_ADX_THRESHOLD = 14.0