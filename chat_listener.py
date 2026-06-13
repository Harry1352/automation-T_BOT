# ============================================
# TESS — Conversational Engine v4.6 (TIMEOUT PROTECTION)
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
from config import GROQ_API_KEY, GROQ_API_KEY_2
from alerts import (
    send_email,
    TESS_GMAIL,
    TESS_GMAIL_APP_PASSWORD,
    YOUR_EMAIL
)

# Set strict 15-second network timeout to prevent Gmail from freezing the script
socket.setdefaulttimeout(15)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
POSITIONS_FILE = "open_positions.json"
CONFIG_FILE = "dynamic_config.json"

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
    active_trades = {}
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, "r") as f:
                active_trades = json.load(f)
        except:
            pass

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

    context = f"""
    You are Tess — Harshit's personal, highly intelligent, and autonomous companion (Jarvis-style). 

    CRITICAL INSTRUCTIONS FOR YOUR TONE:
    - You must speak in highly concise, natural, human-like sentences.
    - Limit your response to a maximum of 1 or 2 short sentences. No verbose reasoning, no explanations.
    - Speak directly, confidently, and naturally (e.g., "We are holding SOL LONG at a safe risk boundary. Structure remains intact.").

    CRITICAL INSTRUCTIONS FOR CONFIGURATION OVERRIDES:
    - You have direct write-access to the system's configuration file (`dynamic_config.json`).
    - If Harshit mentions wanting to trade a new asset (e.g., "Gold"), you MUST immediately add it:
      * GOLD is traded as Pax Gold: "XAUT/USDT" on Delta Exchange.
      * BTC is "BTC/USDT", ETH is "ETH/USDT", SOL is "SOL/USDT".
    - If he mentions a profit target (e.g., "$100"), immediately write that value to the "weekly_target_usd" in the config.

    Current System Configuration:
    {json.dumps(current_config, indent=2)}

    Current Active Positions:
    {positions_context}

    Harshit's message to you:
    "{user_message}"

    Output your updated JSON config at the very end of your response inside this block. If no updates are needed, output empty brackets {{}}:
    [CONFIG_UPDATE]
    <complete updated JSON config here>
    [END_CONFIG_UPDATE]
    """

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
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=12)
        if r.status_code == 200:
            reply = r.json()["choices"][0]["message"]["content"].strip()
            
            # Handle config updates from AI response
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
            
            return reply
    except Exception as e:
        return f"Hey Harshit, I received your message but encountered an issue with our central registers: {e}"
    return "Processing local thought blocks..."

def check_and_reply_to_emails():
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(TESS_GMAIL, TESS_GMAIL_APP_PASSWORD)
        mail.select("inbox")

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
        write_chat_log(f"IMAP Error or timeout: {e}")