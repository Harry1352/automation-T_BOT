# ============================================
# TESS — Configuration File
# Your settings live here. Nothing else touches this file.
# ============================================

# --- Your Delta Exchange API Keys ---
# Go to Delta Exchange → Settings → API Keys → Create
# Give it Trade permission only. NO withdrawal permission.
DELTA_API_KEY = "paste_your_api_key_here"
DELTA_API_SECRET = "paste_your_secret_here"

#GROQ_API_KEY
GROQ_API_KEY = "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"

# --- Are we in paper mode or real mode? ---
# ALWAYS start with True. Only change to False when you trust her.
PAPER_MODE = True
PAPER_BALANCE = 10000  # fake $10,000 to test with

# --- What Tess trades ---
TRADING_PAIRS = ["ETH/USDT", "SOL/USDT"]

# --- How careful Tess is ---
MIN_CONFIDENCE = 75        # she only trades if she's 75% sure or more
MAX_RISK_PER_TRADE = 2     # she risks max 2% of your balance per trade
DAILY_LOSS_LIMIT = 200     # she stops completely if she loses $200 in one day
REWARD_TO_RISK = 2.0       # she aims to make 2x whatever she risks

# --- Timeframes she watches ---
TIMEFRAMES = ["15m", "1h", "4h"]  # 15 min, 1 hour, 4 hour candles

# --- Email alerts ---
TESS_GMAIL = "tessbot1352@gmail.com"
TESS_GMAIL_APP_PASSWORD = "zdohidapzpgkupgt"
YOUR_EMAIL = "h.tess.receiver@gmail.com"

# --- WhatsApp (we set this up together later) ---
WHATSAPP_ENABLED = False
TWILIO_SID = ""
TWILIO_TOKEN = ""
YOUR_WHATSAPP = ""

# --- How often Tess checks the market ---
SCAN_INTERVAL_MINUTES = 5  # every 5 minutes she looks at ETH and SOL