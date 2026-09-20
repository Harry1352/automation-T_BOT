#!/usr/bin/env python3
"""
api_healthcheck.py — Tess API Health Check
Run standalone, from your T_BOT project root: python3 api_healthcheck.py

Tests every external API Tess depends on WITHOUT running the scanner loop.
For each API this reports:
  1. Is it reachable and authenticating correctly right now?
  2. Any rate-limit / quota info the provider actually exposes in its
     response headers (see honesty note below).
  3. A live proof-of-life answer to a random trivial test question
     (for LLM APIs), so you can eyeball that it's really responding.
  4. Latency, so you can spot a slow/degraded provider before it costs you
     a missed trade.

HONESTY NOTE on "tokens left": most LLM providers (NVIDIA NIM, Google AI
Studio) do NOT expose remaining account balance/credits through the
completions API itself -- that info only lives on their billing dashboard.
Where a provider's HTTP response DOES include rate-limit headers (Groq is
the one that reliably does this), this script reads and prints them
automatically. Where a provider doesn't expose it, we say so plainly
instead of guessing or inventing a number.

Nothing here places a trade, sends a real alert email, or spams your
Telegram/ntfy unless you flip the SEND_LIVE_TEST_MESSAGES flag below.
"""

import os
import sys
import time
import random
import smtplib
import requests
from datetime import datetime

# ── Make sure we run from the T_BOT root so `import keys` works ──────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import keys
except ImportError:
    print("FATAL: keys.py not found. Run this script from your T_BOT project root.")
    sys.exit(1)

# Set to True if you also want a real Telegram + ntfy test message sent
# (harmless, but they DO land on your phone). Defaults to False so running
# this repeatedly doesn't spam you.
SEND_LIVE_TEST_MESSAGES = False

TEST_QUESTIONS = [
    "What is 7 times 6? Answer with just the number.",
    "Name one primary color. One word only.",
    "What is the capital of France? One word only.",
    "What year comes right after 1999? Just the number.",
    "Reply with exactly one word: pong.",
]

# ── Small helpers ─────────────────────────────────────────────────────────
GREEN, RED, YELLOW, DIM, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"

def _ok(msg):   return f"{GREEN}✔ {msg}{RESET}"
def _fail(msg): return f"{RED}✘ {msg}{RESET}"
def _warn(msg): return f"{YELLOW}! {msg}{RESET}"

results = []  # (name, status: "ok"/"fail"/"warn", one_line_summary)

def record(name, status, summary):
    results.append((name, status, summary))

def section(title):
    print(f"\n{'─'*70}\n{title}\n{'─'*70}")

def print_rate_limit_headers(resp):
    """Print any rate-limit headers a provider actually returned -- never fabricated."""
    rl_headers = {k: v for k, v in resp.headers.items() if "ratelimit" in k.lower() or "rate-limit" in k.lower()}
    if rl_headers:
        print(f"  {DIM}Rate-limit info from response headers:{RESET}")
        for k, v in rl_headers.items():
            print(f"    {DIM}{k}: {v}{RESET}")
    else:
        print(f"  {DIM}No rate-limit/quota headers returned by this provider on this call.{RESET}")


# ══════════════════════════════════════════════════════════════════════════
# LLM APIs
# ══════════════════════════════════════════════════════════════════════════

def check_nemotron():
    section("NVIDIA Nemotron (nvidia/nemotron-3-ultra-550b-a55b)")
    key = getattr(keys, "NVIDIA_NEMOTRON_KEY", "")
    if not key:
        print(_fail("NVIDIA_NEMOTRON_KEY is empty in keys.py"))
        record("Nemotron", "fail", "No key configured")
        return

    question = random.choice(TEST_QUESTIONS)
    payload = {
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
        "messages": [
            {"role": "system", "content": "You are a terse test assistant. Skip any reasoning "
                                            "narration and reply with ONLY the final short answer."},
            {"role": "user", "content": question},
        ],
        "temperature": 0.2,
        "max_tokens": 300,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    try:
        t0 = time.time()
        r = requests.post("https://integrate.api.nvidia.com/v1/chat/completions",
                           json=payload, headers=headers, timeout=30)
        elapsed = time.time() - t0

        if r.status_code == 401:
            print(_fail(f"Auth failed (401) — key is invalid or revoked. ({elapsed:.1f}s)"))
            record("Nemotron", "fail", "401 — bad/revoked key")
        elif r.status_code == 429:
            print(_fail(f"Rate limited / quota exceeded (429). ({elapsed:.1f}s)"))
            print_rate_limit_headers(r)
            record("Nemotron", "fail", "429 — rate limited or out of quota")
        elif r.status_code == 402:
            print(_fail(f"Payment required (402) — out of credits. ({elapsed:.1f}s)"))
            record("Nemotron", "fail", "402 — out of credits")
        elif r.status_code != 200:
            print(_fail(f"Unexpected HTTP {r.status_code}: {r.text[:200]} ({elapsed:.1f}s)"))
            record("Nemotron", "fail", f"HTTP {r.status_code}")
        else:
            data = r.json()
            answer = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            print(_ok(f"Responding normally. Latency: {elapsed:.1f}s"))
            print(f"  Q: {question}")
            print(f"  A: {answer}")
            print(f"  {DIM}Tokens used this call — prompt: {usage.get('prompt_tokens','?')}, "
                  f"completion: {usage.get('completion_tokens','?')}, "
                  f"total: {usage.get('total_tokens','?')}{RESET}")
            print_rate_limit_headers(r)
            print(f"  {DIM}Note: NVIDIA's API does not expose remaining account credits — "
                  f"check https://build.nvidia.com for your balance.{RESET}")
            record("Nemotron", "ok", f"{elapsed:.1f}s, {usage.get('total_tokens','?')} tokens used")
    except requests.exceptions.Timeout:
        print(_fail("Timed out after 30s."))
        record("Nemotron", "fail", "Timeout")
    except Exception as e:
        print(_fail(f"Request failed: {e}"))
        record("Nemotron", "fail", str(e))


def check_groq():
    section("Groq (GROQ_API_KEY_2)")
    key = getattr(keys, "GROQ_API_KEY_2", "")
    if not key:
        print(_warn("GROQ_API_KEY_2 is empty in keys.py — skipping (not currently wired into scanner.py anyway)."))
        record("Groq", "warn", "No key configured / not currently used")
        return

    question = random.choice(TEST_QUESTIONS)
    payload = {
        "model": "openai/gpt-oss-120b",
        "messages": [{"role": "user", "content": question}],
        "temperature": 0.2,
        "max_tokens": 50,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    try:
        t0 = time.time()
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                           json=payload, headers=headers, timeout=30)
        elapsed = time.time() - t0

        if r.status_code == 401:
            print(_fail(f"Auth failed (401) — key is invalid. ({elapsed:.1f}s)"))
            record("Groq", "fail", "401 — bad key")
        elif r.status_code == 429:
            print(_fail(f"Rate limited / quota exceeded (429). ({elapsed:.1f}s)"))
            print_rate_limit_headers(r)
            record("Groq", "fail", "429 — rate limited")
        elif r.status_code != 200:
            print(_fail(f"Unexpected HTTP {r.status_code}: {r.text[:200]} ({elapsed:.1f}s)"))
            record("Groq", "fail", f"HTTP {r.status_code}")
        else:
            data = r.json()
            answer = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            print(_ok(f"Responding normally. Latency: {elapsed:.1f}s"))
            print(f"  Q: {question}")
            print(f"  A: {answer}")
            print(f"  {DIM}Tokens used this call — total: {usage.get('total_tokens','?')}{RESET}")
            # Groq is one of the few providers that reliably returns real
            # remaining-quota headers -- this is the actual "how many more
            # times can it run" answer, straight from the provider.
            print_rate_limit_headers(r)
            record("Groq", "ok", f"{elapsed:.1f}s, {usage.get('total_tokens','?')} tokens used")
    except requests.exceptions.Timeout:
        print(_fail("Timed out after 30s."))
        record("Groq", "fail", "Timeout")
    except Exception as e:
        print(_fail(f"Request failed: {e}"))
        record("Groq", "fail", str(e))


def check_gemini():
    section("Google Gemini / AI Studio (Dev Console backend)")
    key = getattr(keys, "GOOGLE_STUDIO_API_KEY", "")
    if not key:
        print(_warn("GOOGLE_STUDIO_API_KEY is empty in keys.py — Dev Console AI chat won't work."))
        record("Gemini", "warn", "No key configured")
        return

    question = random.choice(TEST_QUESTIONS)
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": question}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 50},
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}

    try:
        t0 = time.time()
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        elapsed = time.time() - t0

        if r.status_code == 401 or r.status_code == 403:
            print(_fail(f"Auth failed ({r.status_code}) — key invalid or lacks permission. ({elapsed:.1f}s)"))
            record("Gemini", "fail", f"{r.status_code} — bad/unauthorized key")
        elif r.status_code == 429:
            print(_fail(f"Rate limited / quota exceeded (429). ({elapsed:.1f}s)"))
            print_rate_limit_headers(r)
            record("Gemini", "fail", "429 — rate limited or out of quota")
        elif r.status_code != 200:
            print(_fail(f"Unexpected HTTP {r.status_code}: {r.text[:200]} ({elapsed:.1f}s)"))
            record("Gemini", "fail", f"HTTP {r.status_code}")
        else:
            data = r.json()
            answer = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            usage = data.get("usageMetadata", {})
            print(_ok(f"Responding normally. Latency: {elapsed:.1f}s"))
            print(f"  Q: {question}")
            print(f"  A: {answer}")
            print(f"  {DIM}Tokens used this call — prompt: {usage.get('promptTokenCount','?')}, "
                  f"total: {usage.get('totalTokenCount','?')}{RESET}")
            print_rate_limit_headers(r)
            print(f"  {DIM}Note: Google AI Studio doesn't expose remaining quota via this API — "
                  f"check https://aistudio.google.com for your usage/limits.{RESET}")
            record("Gemini", "ok", f"{elapsed:.1f}s, {usage.get('totalTokenCount','?')} tokens used")
    except requests.exceptions.Timeout:
        print(_fail("Timed out after 30s."))
        record("Gemini", "fail", "Timeout")
    except Exception as e:
        print(_fail(f"Request failed: {e}"))
        record("Gemini", "fail", str(e))


# ══════════════════════════════════════════════════════════════════════════
# Market data APIs (no auth, but Tess is dead in the water without these)
# ══════════════════════════════════════════════════════════════════════════

def check_binance():
    section("Binance (primary candle/price feed)")
    try:
        t0 = time.time()
        r = requests.get("https://api.binance.com/api/v3/ping", timeout=8)
        elapsed = time.time() - t0
        if r.status_code == 200:
            # Follow up with a real candle fetch, since scanner.py actually
            # needs this to work, not just the ping endpoint.
            r2 = requests.get("https://api.binance.com/api/v3/klines",
                               params={"symbol": "ETHUSDT", "interval": "1h", "limit": 1}, timeout=8)
            if r2.status_code == 200 and r2.json():
                price = r2.json()[0][4]
                print(_ok(f"Reachable. Latency: {elapsed:.2f}s. Sample ETH/USDT close: ${price}"))
                record("Binance", "ok", f"{elapsed:.2f}s")
            else:
                print(_warn(f"Ping OK but klines fetch failed (HTTP {r2.status_code}) — "
                             f"possible IP ban/region block."))
                record("Binance", "warn", "Ping OK, klines failed")
        else:
            print(_fail(f"HTTP {r.status_code} — possible region block."))
            record("Binance", "fail", f"HTTP {r.status_code}")
    except Exception as e:
        print(_fail(f"Unreachable: {e}"))
        record("Binance", "fail", str(e))


def check_kucoin():
    section("KuCoin (fallback candle feed)")
    try:
        t0 = time.time()
        r = requests.get("https://api.kucoin.com/api/v1/market/orderbook/level1",
                          params={"symbol": "ETH-USDT"}, timeout=8)
        elapsed = time.time() - t0
        if r.status_code == 200 and r.json().get("data"):
            price = r.json()["data"]["price"]
            print(_ok(f"Reachable. Latency: {elapsed:.2f}s. Sample ETH-USDT price: ${price}"))
            record("KuCoin", "ok", f"{elapsed:.2f}s")
        else:
            print(_fail(f"HTTP {r.status_code}: {r.text[:150]}"))
            record("KuCoin", "fail", f"HTTP {r.status_code}")
    except Exception as e:
        print(_fail(f"Unreachable: {e}"))
        record("KuCoin", "fail", str(e))


def check_coingecko():
    section("CoinGecko (primary price feed)")
    try:
        t0 = time.time()
        r = requests.get("https://api.coingecko.com/api/v3/simple/price",
                          params={"ids": "ethereum", "vs_currencies": "usd"}, timeout=8)
        elapsed = time.time() - t0
        if r.status_code == 200 and "ethereum" in r.json():
            price = r.json()["ethereum"]["usd"]
            print(_ok(f"Reachable. Latency: {elapsed:.2f}s. Sample ETH price: ${price}"))
            record("CoinGecko", "ok", f"{elapsed:.2f}s")
        elif r.status_code == 429:
            print(_warn(f"Rate limited (429) — CoinGecko's free tier is easy to exhaust "
                         f"with frequent scans. Kraken fallback matters more than usual."))
            record("CoinGecko", "warn", "429 — rate limited")
        else:
            print(_fail(f"HTTP {r.status_code}"))
            record("CoinGecko", "fail", f"HTTP {r.status_code}")
    except Exception as e:
        print(_fail(f"Unreachable: {e}"))
        record("CoinGecko", "fail", str(e))


def check_kraken():
    section("Kraken (fallback price feed)")
    try:
        t0 = time.time()
        r = requests.get("https://api.kraken.com/0/public/Ticker", params={"pair": "XETHZUSD"}, timeout=8)
        elapsed = time.time() - t0
        data = r.json()
        if r.status_code == 200 and not data.get("error"):
            price = list(data["result"].values())[0]["c"][0]
            print(_ok(f"Reachable. Latency: {elapsed:.2f}s. Sample ETH price: ${price}"))
            record("Kraken", "ok", f"{elapsed:.2f}s")
        else:
            print(_fail(f"HTTP {r.status_code}, error: {data.get('error')}"))
            record("Kraken", "fail", str(data.get("error")))
    except Exception as e:
        print(_fail(f"Unreachable: {e}"))
        record("Kraken", "fail", str(e))


def check_delta_exchange():
    section("Delta Exchange India")
    key = getattr(keys, "DELTA_API_KEY", "")
    secret = getattr(keys, "DELTA_API_SECRET", "")

    try:
        t0 = time.time()
        r = requests.get("https://api.india.delta.exchange/v2/products", timeout=8)
        elapsed = time.time() - t0
        if r.status_code == 200:
            print(_ok(f"Public API reachable. Latency: {elapsed:.2f}s."))
        else:
            print(_warn(f"Public API returned HTTP {r.status_code}."))
    except Exception as e:
        print(_fail(f"Unreachable: {e}"))
        record("Delta Exchange", "fail", str(e))
        return

    if not key or not secret:
        print(_warn("DELTA_API_KEY / DELTA_API_SECRET not set — no authenticated check possible."))
        record("Delta Exchange", "warn", "Public OK, no keys configured")
    else:
        print(_warn("Keys are present, but scanner.py doesn't currently call Delta's authenticated "
                     "endpoints anywhere (it's still using Binance/Kraken/CoinGecko for prices, paper "
                     "mode only) — so an authenticated check isn't wired up yet. Flag this for when "
                     "you actually move to live Delta execution."))
        record("Delta Exchange", "warn", "Keys present, not yet integrated — public check only")


# ══════════════════════════════════════════════════════════════════════════
# Alerts: Email, ntfy, Telegram
# ══════════════════════════════════════════════════════════════════════════

def check_gmail_smtp():
    section("Gmail SMTP (alerts.py)")
    gmail = getattr(keys, "TESS_GMAIL", "")
    app_pw = getattr(keys, "TESS_GMAIL_APP_PASSWORD", "")
    if not gmail or not app_pw:
        print(_fail("TESS_GMAIL / TESS_GMAIL_APP_PASSWORD not set in keys.py"))
        record("Gmail SMTP", "fail", "No credentials")
        return
    try:
        t0 = time.time()
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
        server.starttls()
        server.login(gmail, app_pw)
        server.quit()
        elapsed = time.time() - t0
        print(_ok(f"Login succeeded (no email sent — this only verifies credentials). {elapsed:.1f}s"))
        record("Gmail SMTP", "ok", f"{elapsed:.1f}s login OK")
    except smtplib.SMTPAuthenticationError:
        print(_fail("Auth failed — app password is wrong/revoked, or 2FA app-password needs regenerating."))
        record("Gmail SMTP", "fail", "Auth failed")
    except Exception as e:
        print(_fail(f"Connection failed: {e}"))
        record("Gmail SMTP", "fail", str(e))


def check_ntfy():
    section("ntfy push (topic: tess_harshit_1352)")
    try:
        t0 = time.time()
        if SEND_LIVE_TEST_MESSAGES:
            r = requests.post(
                "https://ntfy.sh/tess_harshit_1352",
                data=f"Health check ping at {datetime.now().strftime('%H:%M:%S')}".encode(),
                headers={"Title": "Tess Health Check"},
                timeout=8,
            )
            elapsed = time.time() - t0
            if r.status_code == 200:
                print(_ok(f"Test push sent — check your phone. {elapsed:.1f}s"))
                record("ntfy", "ok", f"{elapsed:.1f}s, live push sent")
            else:
                print(_fail(f"HTTP {r.status_code}: {r.text[:150]}"))
                record("ntfy", "fail", f"HTTP {r.status_code}")
        else:
            r = requests.head("https://ntfy.sh", timeout=8)
            elapsed = time.time() - t0
            if r.status_code < 400:
                print(_ok(f"ntfy.sh reachable. {elapsed:.2f}s. "
                           f"{DIM}(Set SEND_LIVE_TEST_MESSAGES=True at top of file for a real push test.){RESET}"))
                record("ntfy", "ok", f"{elapsed:.2f}s, reachability only")
            else:
                print(_fail(f"HTTP {r.status_code}"))
                record("ntfy", "fail", f"HTTP {r.status_code}")
    except Exception as e:
        print(_fail(f"Unreachable: {e}"))
        record("ntfy", "fail", str(e))


def check_telegram():
    section("Telegram Bot")
    try:
        import chat_listener
        token = getattr(chat_listener, "TELEGRAM_TOKEN", "")
        chat_id = getattr(chat_listener, "YOUR_TELEGRAM_CHAT_ID", "")
    except Exception as e:
        print(_fail(f"Couldn't import chat_listener.py to get the bot token: {e}"))
        record("Telegram", "fail", "Import failed")
        return

    if not token:
        print(_fail("TELEGRAM_TOKEN not found."))
        record("Telegram", "fail", "No token")
        return

    # SECURITY FLAG: this token is hardcoded in plaintext inside chat_listener.py
    # rather than loaded from keys.py/.env like everything else. Worth moving it
    # into keys.py the same way DELTA_API_KEY etc. are handled.
    print(_warn("TELEGRAM_TOKEN is hardcoded directly in chat_listener.py, not in keys.py/.env "
                 "like your other secrets. Worth moving it into keys.py for consistency."))

    try:
        t0 = time.time()
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=8)
        elapsed = time.time() - t0
        if r.status_code == 200 and r.json().get("ok"):
            bot_info = r.json()["result"]
            print(_ok(f"Bot token valid. Bot: @{bot_info.get('username')}. {elapsed:.1f}s"))
            if SEND_LIVE_TEST_MESSAGES and chat_id:
                send_r = requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "Tess health check ping ✅"},
                    timeout=8,
                )
                if send_r.status_code == 200:
                    print(_ok("Test message sent — check your Telegram."))
                else:
                    print(_warn(f"getMe worked but sendMessage failed: HTTP {send_r.status_code}"))
            record("Telegram", "ok", f"{elapsed:.1f}s, bot verified")
        else:
            print(_fail(f"Token invalid or bot unreachable: {r.text[:150]}"))
            record("Telegram", "fail", "Invalid token")
    except Exception as e:
        print(_fail(f"Request failed: {e}"))
        record("Telegram", "fail", str(e))


# ══════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════

def main():
    print(f"{'='*70}\nTESS API HEALTH CHECK — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*70}")
    print(f"{DIM}No trades placed. No live alerts sent unless SEND_LIVE_TEST_MESSAGES=True.{RESET}")

    checks = [
        check_nemotron,
        check_groq,
        check_gemini,
        check_binance,
        check_kucoin,
        check_coingecko,
        check_kraken,
        check_delta_exchange,
        check_gmail_smtp,
        check_ntfy,
        check_telegram,
    ]
    for c in checks:
        try:
            c()
        except Exception as e:
            print(_fail(f"Check crashed unexpectedly: {e}"))

    section("SUMMARY")
    n_ok = sum(1 for _, s, _ in results if s == "ok")
    n_warn = sum(1 for _, s, _ in results if s == "warn")
    n_fail = sum(1 for _, s, _ in results if s == "fail")
    for name, status, summary in results:
        icon = {"ok": _ok, "fail": _fail, "warn": _warn}[status]
        print(f"  {icon(name):<45} {DIM}{summary}{RESET}")
    print(f"\n{n_ok} OK  |  {n_warn} warnings  |  {n_fail} failed  (out of {len(results)} checked)")

    if n_fail > 0:
        print(_fail("\nDo not restart the scanner loop until every FAIL above is resolved."))
    elif n_warn > 0:
        print(_warn("\nNo hard failures, but review the warnings above before going live."))
    else:
        print(_ok("\nAll clear. Safe to restart Tess."))


if __name__ == "__main__":
    main()
