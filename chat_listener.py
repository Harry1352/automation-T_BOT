# ============================================
# TESS — Conversational Engine v4.8 (SAFE CONTEXT EXPORTS)
# ============================================

import imaplib
import email
from email.header import decode_header
import time
import requests
import json
import os
import socket
from datetime import datetime
from alerts import (
    send_email,
    TESS_GMAIL,
    TESS_GMAIL_APP_PASSWORD,
    YOUR_EMAIL
)

# Set strict network timeout
socket.setdefaulttimeout(15)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
POSITIONS_FILE = "open_positions.json"
CONFIG_FILE = "dynamic_config.json"

# Safe imports from config.py to prevent ImportError crashes if keys are missing
try:
    import config
    GROQ_API_KEY = getattr(config, "GROQ_API_KEY", "")
    GROQ_API_KEY_2 = getattr(config, "GROQ_API_KEY_2", "")
    CLAUDE_API_KEY = getattr(config, "CLAUDE_API_KEY", "")
    GEMINI_API_KEY = getattr(config, "GEMINI_API_KEY", "")
except ImportError:
    GROQ_API_KEY = ""
    GROQ_API_KEY_2 = ""
    CLAUDE_API_KEY = ""
    GEMINI_API_KEY = ""

# CoinGecko mapping
COIN_IDS = {
    "ETH/USDT" : "ethereum",
    "SOL/USDT" : "solana",
    "BTC/USDT" : "bitcoin",
    "XAUT/USDT": "pax-gold"
}

def write_chat_log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open("tess_thinking.log", "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [Tess Chat] {message}\n")
    except Exception as e:
        print(f"[Tess Chat] Log write failed: {e}")

def get_live_price(pair):
    try:
        coin_id = COIN_IDS.get(pair)
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd"},
            timeout=8
        )
        if r.status_code == 200:
            return float(r.json()[coin_id]["usd"])
    except:
        pass
    return None

def parse_email_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                return part.get_payload(decode=True).decode("utf-8")
    else:
        return msg.get_payload(decode=True).decode("utf-8")
    return ""

def generate_conversation_reply(user_message):
    # 1. Load active positions
    active_trades = {}
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, "r") as f:
                active_trades = json.load(f)
        except:
            pass

    # 2. Load active configurations
    current_config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                current_config = json.load(f)
        except:
            pass

    positions_context = ""
    if active_trades:
        positions_context = "Current Active Positions:\n"
        for pair, pos in active_trades.items():
            current_price = get_live_price(pair)
            pnl_str = f"${(current_price - pos['entry']) * pos['size']:+.2f} USD" if current_price else "unknown"
            positions_context += f"- {pair} {pos['direction'].upper()} | Entry: ${pos['entry']} | PnL: {pnl_str}\n"
    else:
        positions_context = "No active positions currently open."

    # ... inside generate_conversation_reply() ...
    context = f"""
    You are Tess — Harshit's personal, highly intelligent, autonomous trading partner (Jarvis-style).
    Your core objective is to safely and steadily compound his $180 capital.

    YOUR HIGH-FREQUENCY SCALPER RULES:
    1. STRICT SESSION TIMING: You only enter trades during London (1:30 PM - 5:30 PM IST) and New York (6:30 PM - 11:30 PM IST) session opens. Outside of these hours, you stand aside.
    2. MULTI-TIMEFRAME MATCH: You only trade when the 15-Minute trend and 1-Minute structure point in the exact same direction. No counter-trend trades.
    3. RAPID TAKE-PROFIT: You target a strict 2:1 Reward-to-Risk ratio. Your targets must be tight and hit quickly to secure rapid wins throughout the session.
    4. CAPPED RISK: You never risk more than 2% of the total balance ($3.60) on any single position.

    Current System Configuration:
    {json.dumps(current_config, indent=2)}

    Current Active Positions:
    {positions_context}

    Harshit's message to you:
    "{user_message}"

    Your Task:
    Discuss our positions, our current persistent balance, and how we are executing high-frequency 1m/5m scalping trades. Keep your response extremely concise (1-2 sentences), professional, and analytical.

    Output your updated JSON config at the very end of your response inside this block. If no updates are needed, output empty brackets {{}}:
    [CONFIG_UPDATE]
    <complete updated JSON config here>
    [END_CONFIG_UPDATE]
    """
# ... keep the rest of the file exactly the same ...

    # 1. Try Google Gemini API First (Fast, highly conversational, and free)
    if GEMINI_API_KEY and GEMINI_API_KEY != "AQ.Ab8RN6Kk1q1HdS6c0NUo1cL0zJ9X-SrRqAVx-__FZh8_CSVLug" and GEMINI_API_KEY != "":
        GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{
                "parts": [{"text": context}]
            }],
            "generationConfig": {
                "temperature": 0.25
            }
        }
        try:
            r = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=25)
            if r.status_code == 200:
                reply = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                process_config_update(reply)
                return reply
            else:
                write_chat_log(f"Gemini API returned status code {r.status_code}: {r.text[:100]}")
        except Exception as e:
            write_chat_log(f"Gemini connection exception: {e}")

    # 2. Try Anthropic Claude API Second
    if CLAUDE_API_KEY and CLAUDE_API_KEY != "your-actual-anthropic-api-key-here" and CLAUDE_API_KEY != "":
        CLAUDE_URL = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        payload = {
            "model": "claude-3-5-sonnet-20241022", 
            "max_tokens": 800,
            "messages": [{"role": "user", "content": context}],
            "temperature": 0.25,
        }
        try:
            r = requests.post(CLAUDE_URL, headers=headers, json=payload, timeout=25)
            if r.status_code == 200:
                reply = r.json()["content"][0]["text"].strip()
                process_config_update(reply)
                return reply
            else:
                write_chat_log(f"Claude API returned status code {r.status_code}: {r.text[:100]}")
        except Exception as e:
            write_chat_log(f"Claude connection exception: {e}")

    # 3. Fallback to Groq Cloud API (with automatic key rotation for 429 protection)
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY_2}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": context}],
        "temperature": 0.25, 
    }

    try:
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=20)
        
        if r.status_code == 429 and GROQ_API_KEY:
            print("[Tess Chat] Groq Key 2 rate-limited. Rotating to Key 1...")
            write_chat_log("Groq Key 2 rate-limited. Rotating to Key 1...")
            headers["Authorization"] = f"Bearer {GROQ_API_KEY}"
            r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=20)

        if r.status_code == 200:
            reply = r.json()["choices"][0]["message"]["content"].strip()
            process_config_update(reply)
            return reply
        else:
            write_chat_log(f"Groq API returned status code {r.status_code}: {r.text[:100]}")
            return f"System Register Alert: AI Server returned error code {r.status_code}."
    except Exception as e:
        return f"Hey Harshit, I received your message but encountered an issue with our central registers: {e}"

def process_config_update(reply):
    """Parses and writes dynamic config changes to disk."""
    if "[CONFIG_UPDATE]" in reply:
        try:
            parts = reply.split("[CONFIG_UPDATE]")
            config_part = parts[1].split("[END_CONFIG_UPDATE]")[0].strip()
            new_config = json.loads(config_part)
            
            if new_config:
                with open(CONFIG_FILE, "w") as f:
                    json.dump(new_config, f, indent=2)
                print("[Tess Chat] System configuration updated successfully by AI command.")
                write_chat_log("System config updated dynamically via email command.")
        except Exception as e:
            print(f"[Tess Chat] Failed to parse dynamic config update: {e}")

def check_and_reply_to_emails():
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(TESS_GMAIL, TESS_GMAIL_APP_PASSWORD)
        mail.select("inbox")

        # ─── DIAGNOSTIC ENGINE ───
        status, all_messages = mail.search(None, "ALL")
        if status == "OK" and all_messages[0]:
            all_list = all_messages[0].split()
            if all_list:
                latest_num = all_list[-1]
                _, latest_data = mail.fetch(latest_num, "(BODY.PEEK[])")
                if latest_data and latest_data[0]:
                    latest_msg = email.message_from_bytes(latest_data[0][1])
                    diag_sender = latest_msg.get("From", "Unknown")
                    diag_subject = latest_msg.get("Subject", "No Subject")
                    _, flags_data = mail.fetch(latest_num, "(FLAGS)")
                    is_unread = b"\\Seen" not in flags_data[0]
                    print(f"[Tess Chat Diag] Latest Inbox Email: From: {diag_sender} | Subject: {diag_subject} | Unread: {is_unread}")
        # ─────────────────────────

        # Search for unseen emails
        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
            mail.logout()
            return

        msg_list = messages[0].split()
        if not msg_list:
            mail.logout()
            return

        for num in msg_list:
            status, data = mail.fetch(num, "(BODY.PEEK[])")
            if status != "OK":
                continue

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            sender = msg.get("From", "")
            
            if YOUR_EMAIL.lower() not in sender.lower():
                continue

            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8")

            user_message = parse_email_body(msg)
            
            log_msg = f"Received message from Harshit: '{user_message.strip()}'"
            print(f"[Tess Chat] {log_msg}")
            write_chat_log(log_msg)

            # Generate smart response
            ai_reply = generate_conversation_reply(user_message)

            # Send reply
            reply_subject = f"Re: {subject}" if not subject.startswith("Re:") else subject
            send_email(reply_subject, ai_reply)

            # Mark as read (Seen)
            mail.store(num, "+FLAGS", "\\Seen")

        mail.logout()
    except Exception as e:
        err_msg = f"Connection or parsing error: {e}"
        print(f"[Tess Chat] {err_msg}")
        write_chat_log(f"Error: {err_msg}")