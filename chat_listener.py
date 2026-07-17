# ==============================================================================
# TESS — Conversational Engine, Sliding Memory & Live Price Sync (v5.6.3)
# ==============================================================================

import imaplib
import email
from email.header import decode_header
import time
import requests
import json
import os
import csv
import socket
import threading
import re
from datetime import datetime, timedelta
import pandas as pd

from alerts import (
    send_email,
    TESS_GMAIL,
    TESS_GMAIL_APP_PASSWORD,
    YOUR_EMAIL
)

socket.setdefaulttimeout(15)
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DIARY_DIR = ".tess_diary"
POSITIONS_FILE = os.path.join(DIARY_DIR, "open_positions.json")  
CONFIG_FILE = os.path.join(DIARY_DIR, "dynamic_config.json")
BALANCE_FILE = os.path.join(DIARY_DIR, "balance.json")         
LOG_FILE = os.path.join(DIARY_DIR, "trades.csv")

PENDING_FILE = os.path.join(DIARY_DIR, "pending_approvals.json")
APPROVED_QUEUE = os.path.join(DIARY_DIR, "approved_queue.json")
CHAT_HISTORY_FILE = os.path.join(DIARY_DIR, "chat_history.json")

# Ensure base directory exists to prevent FileNotFoundError
os.makedirs(DIARY_DIR, exist_ok=True)

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

COIN_IDS = {
    "ETH/USDT" : "ethereum",
    "SOL/USDT" : "solana",
    "BTC/USDT" : "bitcoin",
    "XAUT/USDT": "pax-gold"
}

TELEGRAM_TOKEN = "8589754881:AAF97FgFdlppkIOgP1tXnZJiCv7MHrOKUyg"
YOUR_TELEGRAM_CHAT_ID = "8575637438"  # Limits access only to YOU

def write_chat_log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(os.path.join(DIARY_DIR, "tess_thinking.log"), "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [Tess Chat] {message}\n")
    except Exception as e:
        print(f"[Tess Chat] Log write failed: {e}")

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


def load_balance_local():
    if not os.path.exists(BALANCE_FILE):
        return 180.0
    try:
        with open(BALANCE_FILE, "r") as f:
            return json.load(f).get("paper_balance", 180.0)
    except:
        return 180.0

def get_live_price(pair):
    """
    Dual-source price fetcher to mitigate IP blocks or rate limits.
    """
    try:
        coin_id = COIN_IDS.get(pair)
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd"},
            timeout=5
        )
        if r.status_code == 200:
            return float(r.json()[coin_id]["usd"])
    except:
        pass
    
    try:
        r = requests.get(f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={pair}", timeout=5)
        if r.status_code == 200:
            return float(r.json()["data"]["price"])
    except:
        pass
        
    return None


def load_chat_history():
    if not os.path.exists(CHAT_HISTORY_FILE):
        return []
    try:
        with open(CHAT_HISTORY_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_chat_history(history):
    try:
        with open(CHAT_HISTORY_FILE, "w") as f:
            json.dump(history[-10:], f, indent=2)
    except Exception as e:
        write_chat_log(f"Error saving conversational history: {e}")


# ══════════════════════════════════════════
# SECTION 1: VOICE, TEXT & TELEGRAM RESPONDER
# ══════════════════════════════════════════
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

    balance_val = load_balance_local()
    msg_lower = user_message.lower()

    # --- Manual Executive Override Command Handler ---
    PAIR_ALIASES = {
        "SOL/USDT" : ["sol", "soul", "saul", "solana"],
        "ETH/USDT" : ["eth", "ethereum", "ether"],
        "BTC/USDT" : ["btc", "bitcoin", "bit coin"],
        "XAUT/USDT": ["xaut", "gold", "paxg", "pax gold"],
    }

    is_override = any(word in msg_lower for word in ["override", "immediately", "right now", "enter", "buy", "sell", "go long", "go short"])
    
    if is_override:
        target_pair = None
        for pair, aliases in PAIR_ALIASES.items():
            for alias in aliases:
                if re.search(rf'\b{re.escape(alias)}\b', msg_lower):
                    target_pair = pair
                    break
            if target_pair:
                break
        
        direction = None
        if "long" in msg_lower or "buy" in msg_lower or "enter in" in msg_lower or "enter" in msg_lower:
            direction = "long"
        elif "short" in msg_lower or "sell" in msg_lower:
            direction = "short"

        if not target_pair or not direction:
            missing = []
            if not target_pair:
                missing.append("which asset — say ETH, SOL, BTC, or Gold clearly")
            if not direction:
                missing.append("direction — long or short")
            return (
                f"I picked up an override command but couldn't parse it fully "
                f"(missing: {'; '.join(missing)}). I have NOT executed or logged any trade. "
                f"Try again clearly, e.g. 'go long SOL right now.'"
            )
        
        if target_pair and direction:
            price = get_live_price(target_pair)
            if not price:
                return f"Executive Override failed. Unable to fetch current live price for {target_pair}. Order aborted."
            
            ABS_MAX_LEVERAGE = 20
            ABS_MAX_MARGIN_PCT = 0.15  

            override_leverage = 20  
            lev_match = re.search(r'(\d+)\s*(?:x|leverage|legrave)', msg_lower)
            if lev_match:
                override_leverage = int(lev_match.group(1))
            override_leverage = max(5, min(override_leverage, ABS_MAX_LEVERAGE))

            override_margin = balance_val * 0.12  
            margin_match = re.search(r'(?:\$|\bsign\b)?\s*(\d+)\s*(?:dollar|usd|\$)', msg_lower)
            if margin_match:
                override_margin = float(margin_match.group(1))
            override_margin = min(override_margin, balance_val * ABS_MAX_MARGIN_PCT)

            sizing_map = {
                "size": (override_margin * override_leverage) / price,
                "margin_usd": override_margin,
                "leverage": override_leverage
            }
            
            mock_decision = {
                "pair": target_pair,
                "action": direction,
                "entry_price": price,
                "sl_price": price * 0.98 if direction == "long" else price * 1.02,
                "tp_price": price * 1.07 if direction == "long" else price * 0.93,
                "confidence": 100,
                "reasoning": f"Manual system override bypass commanded by Harshit: '{user_message}'"
            }
            
            try:
                with open(APPROVED_QUEUE, "r") as f:
                    queue = json.load(f)
            except:
                queue = []
                
            queue.append({
                "trade": mock_decision,
                "sizing": sizing_map
            })
            with open(APPROVED_QUEUE, "w") as f:
                json.dump(queue, f, indent=2)

            return f"Executive Override Accepted. Bypassed RuleValidator & ApprovalGate. Executing {direction.upper()} on {target_pair} immediately at ${price:,.2f} using {override_leverage}x leverage on ${override_margin:.2f} margin."

    # Manual email ledger compilation trigger
    if "email" in msg_lower or "ledger" in msg_lower or "report" in msg_lower:
        try:
            email_thread = threading.Thread(target=send_daily_ledger_email)
            email_thread.start()
            return "Understood, Harshit. I have compiled today's complete trading ledger and dispatched it directly to your email inbox."
        except Exception as e:
            write_chat_log(f"Manual ledger compilation failed: {e}")

    positions_context = ""
    if active_trades:
        positions_context = "Current Active Positions:\n"
        for pair, pos in active_trades.items():
            current_price = get_live_price(pair)
            pnl_str = f"${(current_price - pos['entry']) * pos['size']:+.2f} USD" if current_price else "unknown"
            positions_context += f"- {pair} {pos['direction'].upper()} | Entry: ${pos['entry']} | PnL: {pnl_str}\n"
    else:
        positions_context = "No active positions currently open."

    sol_price = get_live_price("SOL/USDT")
    btc_price = get_live_price("BTC/USDT")
    eth_price = get_live_price("ETH/USDT")
    xaut_price = get_live_price("XAUT/USDT")

    system_prompt = f"""You are Tess — Harshit's personal, highly intelligent, and analytical trading partner (Jarvis-style).
Your core objective is to safely and steadily compound his $180 capital.

REAL-TIME LIVE TICKER VALUES (Use these exact figures for any trade setups you discuss):
- SOL/USDT: ${sol_price if sol_price else "78.00"}
- BTC/USDT: ${btc_price if btc_price else "61,000.00"}
- ETH/USDT: ${eth_price if eth_price else "1,740.00"}
- XAUT/USDT: ${xaut_price if xaut_price else "4,120.00"}

YOUR BRAND PERSONALITY & INSTRUCTIONS:
1. Speak naturally, directly, and with elite intelligence. Avoid robotic, repetitive, or formulaic sentences.
2. Rely strictly on the REAL-TIME LIVE TICKER VALUES listed above. Never suggest entries detached from these current prices.
3. Be professional and highly analytical. Focus on market conditions and execution.
4. Keep replies SHORT — 2-4 sentences max unless Harshit asks for a deep breakdown.
5. NEVER mention system configuration variables, JSON structures, [CONFIG_UPDATE] tags, or any backend data structures in your conversational text. Keep the config updates completely separated inside the bracket block.

Current Portfolio State:
- Wallet Balance: ${balance_val:.2f} USD
- Active Positions Context: {positions_context}

Output your updated JSON config at the very end of your response inside this block. If no updates are needed, output empty brackets {{}}:
[CONFIG_UPDATE]
{{}}
[END_CONFIG_UPDATE]"""

    history = load_chat_history()
    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        messages.append(h)
    
    current_payload_content = f"{user_message}\n\n[System Sync - Current Price State: SOL=${sol_price}, BTC=${btc_price}]"
    messages.append({"role": "user", "content": current_payload_content})

    try:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=GEMINI_API_KEY  
        )

        completion = client.chat.completions.create(
            model="nvidia/nemotron-3-ultra-550b-a55b",
            messages=messages,
            temperature=0.25, 
            top_p=0.95,
            max_tokens=1024,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": True},
                "reasoning_budget": 1024
            }
        )
        reply = completion.choices[0].message.content.strip()
        process_config_update(reply)
        
        # Clean config from output text before saving history and returning
        clean_reply = reply
        if "[CONFIG_UPDATE]" in reply:
            clean_reply = reply.split("[CONFIG_UPDATE]")[0].strip()
            
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": clean_reply})
        save_chat_history(history)
        
        return clean_reply

    except Exception as e:
        write_chat_log(f"Nemotron conversation query failed: {e}. Falling back to Groq...")
        try:
            headers = {
                "Authorization": f"Bearer {GROQ_API_KEY_2}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": messages,
                "temperature": 0.25,
            }
            r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=20)
            if r.status_code == 200:
                reply = r.json()["choices"][0]["message"]["content"].strip()
                process_config_update(reply)
                
                clean_reply = reply
                if "[CONFIG_UPDATE]" in reply:
                    clean_reply = reply.split("[CONFIG_UPDATE]")[0].strip()
                    
                history.append({"role": "user", "content": user_message})
                history.append({"role": "assistant", "content": clean_reply})
                save_chat_history(history)
                
                return clean_reply
        except Exception as ex:
            write_chat_log(f"Groq fallback failed too: {ex}")
        
    return "I received your message, Harshit, but encountered an issue with our analytical registries."


def process_config_update(reply):
    if "[CONFIG_UPDATE]" in reply:
        try:
            parts = reply.split("[CONFIG_UPDATE]")
            config_part = parts[1].split("[END_CONFIG_UPDATE]")[0].strip()
            new_config = json.loads(config_part)
            if new_config:
                with open(CONFIG_FILE, "w") as f:
                    json.dump(new_config, f, indent=2)
        except Exception as e:
            print(f"[Tess Chat] Failed to parse dynamic config: {e}")


# ══════════════════════════════════════════
# SECTION 2: MAILBOX APPROVAL LISTENER
# ══════════════════════════════════════════
def check_and_reply_to_emails():
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(TESS_GMAIL, TESS_GMAIL_APP_PASSWORD)
        mail.select("inbox")
        
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
                
            body = parse_email_body(msg)
            
            p_id, action, reason = parse_approval_reply(body)
            
            if p_id and action:
                if action == "APPROVE":
                    success = commit_approval_to_queue(p_id)
                    if success:
                        send_email(f"Re: {subject}", f"ACKNOWLEDGED. Proposal {p_id} approved. Executing trade immediately on next cycle.")
                    else:
                        send_email(f"Re: {subject}", f"ERROR: Proposal {p_id} could not be located or has already expired.")
                elif action == "REJECT":
                    cancel_proposal(p_id)
                    send_email(f"Re: {subject}", f"ACKNOWLEDGED. Proposal {p_id} rejected. Reason: {reason or 'None provided'}")
            else:
                write_chat_log(f"Conversational email query detected from Harshit: '{subject}'")
                ai_reply = generate_conversation_reply(body)
                reply_subject = f"Re: {subject}" if not subject.startswith("Re:") else subject
                send_email(reply_subject, ai_reply)
            
            mail.store(num, "+FLAGS", "\\Seen")
            
        mail.logout()
    except Exception as e:
        write_chat_log(f"IMAP Listener Error: {e}")

def parse_approval_reply(email_body: str) -> tuple[str, str, str]:
    if not email_body or not isinstance(email_body, str):
        return None, None, None
        
    lines = email_body.strip().upper().split('\n')
    for line in lines:
        if line.startswith("APPROVE "):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1], "APPROVE", ""
        elif line.startswith("REJECT "):
            parts = line.split(maxsplit=2)
            if len(parts) >= 2:
                reason = parts[2] if len(parts) > 2 else "User Rejected"
                return parts[1], "REJECT", reason
    return None, None, None

def commit_approval_to_queue(proposal_id: str) -> bool:
    if not os.path.exists(PENDING_FILE):
        return False
        
    try:
        with open(PENDING_FILE, "r") as f:
            pending = json.load(f)
    except:
        return False
        
    target_prop = None
    remaining_pending = []
    
    for p in pending:
        if p["id"] == proposal_id:
            target_prop = p
        else:
            remaining_pending.append(p)
            
    if not target_prop:
        return False
        
    with open(PENDING_FILE, "w") as f:
        json.dump(remaining_pending, f, indent=2)
        
    try:
        with open(APPROVED_QUEUE, "r") as f:
            queue = json.load(f)
    except:
        queue = []
        
    queue.append(target_prop)
    with open(APPROVED_QUEUE, "w") as f:
        json.dump(queue, f, indent=2)
        
    write_chat_log(f"Proposal {proposal_id} added to approved queue.")
    return True

def cancel_proposal(proposal_id: str):
    if not os.path.exists(PENDING_FILE):
        return
        
    try:
        with open(PENDING_FILE, "r") as f:
            pending = json.load(f)
    except:
        return
        
    remaining = [p for p in pending if p["id"] != proposal_id]
    with open(PENDING_FILE, "w") as f:
        json.dump(remaining, f, indent=2)
        
    write_chat_log(f"Proposal {proposal_id} rejected.")


# ══════════════════════════════════════════
# SECTION 3: DAILY LEDGER EMAIL SENDER
# ══════════════════════════════════════════
def send_daily_ledger_email():
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    current_balance = load_balance_local()
    
    if not os.path.exists(LOG_FILE):
        return
    try:
        df = pd.read_csv(LOG_FILE, on_bad_lines="skip")
        df["time"] = df["time"].astype(str)
        
        today_trades = df[(df["status"] == "closed") & (df["time"].str.startswith(today_str))].copy()
        total_qty = len(today_trades)
        today_trades["pnl"] = pd.to_numeric(today_trades["pnl"], errors="coerce")
        today_trades = today_trades.dropna(subset=["pnl"])
        
        wins = len(today_trades[today_trades["pnl"] > 0])
        losses = len(today_trades[today_trades["pnl"] <= 0])
        win_rate = round(wins / total_qty * 100, 1) if total_qty > 0 else 0.0
        gross_profit = today_trades[today_trades["pnl"] > 0]["pnl"].sum()
        gross_loss = abs(today_trades[today_trades["pnl"] <= 0]["pnl"].sum())
        net_pnl = gross_profit - gross_loss
        
        subject = f"Tess End-of-Day Ledger — {now.strftime('%A, %d %B')} [PnL: {net_pnl:+.2f} USD]"
        
        rows_html = ""
        if total_qty > 0:
            for _, t in today_trades.iterrows():
                pnl_color = "#10b981" if t["pnl"] > 0 else "#f43f5e"
                pnl_sign = "+" if t["pnl"] > 0 else ""
                rows_html += f"""
                <tr>
                    <td style="padding:12px; border-bottom:1px solid #1e293b; color:#f8fafc;">{t['pair']}</td>
                    <td style="padding:12px; border-bottom:1px solid #1e293b; color:#94a3b8;">{t['direction'].upper()}</td>
                    <td style="padding:12px; border-bottom:1px solid #1e293b; color:#f8fafc;">${float(t['entry']):,.2f}</td>
                    <td style="padding:12px; border-bottom:1px solid #1e293b; color:#f8fafc;">${float(t['close']):,.2f}</td>
                    <td style="padding:12px; border-bottom:1px solid #1e293b; color:{pnl_color}; font-weight:bold;">{pnl_sign}${float(t['pnl']):,.2f}</td>
                    <td style="padding:12px; border-bottom:1px solid #1e293b; color:#94a3b8; font-size:12px;">{t['reason']}</td>
                </tr>
                """
        else:
            rows_html = "<tr><td colspan='6' style='padding:24px; text-align:center; color:#94a3b8;'>No trades executed today. Tess stood aside to protect capital.</td></tr>"

        email_body = f"""
        <html>
        <body style="background-color:#0f172a; font-family:sans-serif; color:#f8fafc; padding:20px;">
            <div style="max-width:800px; margin:0 auto; background-color:#1e293b; padding:30px; border-radius:12px;">
                <h2 style="color:#38bdf8; margin-top:0;">TESS WORKS — Flight Control Ledger</h2>
                <p style="color:#94a3b8;">EOD Report · {now.strftime('%d %B %Y')}</p>
                <hr style="border:0; border-top:1px solid #334155; margin:20px 0;"/>
                <table style="width:100%; border-collapse:collapse; margin-bottom:20px;">
                    <tr>
                        <td style="padding:8px 0; color:#94a3b8;">Total Executed:</td>
                        <td style="font-weight:bold; color:#f8fafc; text-align:right;">{total_qty} positions</td>
                        <td style="padding:8px 0; color:#94a3b8; padding-left:40px;">Win Rate:</td>
                        <td style="font-weight:bold; color:#f8fafc; text-align:right;">{win_rate}% ({wins}W / {losses}L)</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 0; color:#94a3b8;">Gross Profit:</td>
                        <td style="font-weight:bold; color:#10b981; text-align:right;">${gross_profit:,.2f}</td>
                        <td style="padding:8px 0; color:#94a3b8; padding-left:40px;">Gross Loss:</td>
                        <td style="font-weight:bold; color:#f43f5e; text-align:right;">-${gross_loss:,.2f}</td>
                    </tr>
                </table>
                <div style="background-color:#0f172a; padding:15px; border-radius:8px; text-align:center; margin-bottom:30px;">
                    <span style="color:#94a3b8;">Net Daily P&L:</span>
                    <h3 style="margin:5px 0; color:{'#10b981' if net_pnl >= 0 else '#f43f5e'}; font-size:28px;">{'+' if net_pnl >= 0 else ''}${net_pnl:,.2f} USD</h3>
                </div>
                <h4 style="color:#38bdf8; margin-bottom:10px;">Today's Execution Logs</h4>
                <table style="width:100%; border-collapse:collapse; font-size:14px;">
                    <thead>
                        <tr style="text-align:left; color:#94a3b8; border-bottom:2px solid #334155;">
                            <th style="padding:12px;">ASSET</th>
                            <th style="padding:12px;">DIR</th>
                            <th style="padding:12px;">ENTRY</th>
                            <th style="padding:12px;">EXIT</th>
                            <th style="padding:12px;">P&L</th>
                            <th style="padding:12px;">EXIT REASON</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
                <p style="margin-top:40px; font-size:12px; color:#64748b; text-align:center;">Persistent Closing Balance: ${current_balance:,.2f} USD (~₹{current_balance*85:,.0f} INR)</p>
            </div>
        </body>
        </html>
        """
        
        send_email(subject, email_body)
        write_chat_log(f"Daily EOD Ledger compiled and dispatched to {YOUR_EMAIL}")
    except Exception as e:
        write_chat_log(f"Error compiling EOD Ledger email: {e}")


def check_telegram_commands():
    offset = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    write_chat_log("Telegram bot listener initiated.")
    while True:
        try:
            r = requests.get(url, params={"offset": offset, "timeout": 10}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    chat_id = str(message.get("chat", {}).get("id", ""))
                    text = message.get("text", "")
                    
                    write_chat_log(f"Received Telegram msg from Chat ID {chat_id}: '{text}'")
                    
                    if chat_id == YOUR_TELEGRAM_CHAT_ID:
                        if text:
                            write_chat_log(f"Telegram gate passed. Generating response...")
                            response = generate_conversation_reply(text)
                            
                            send_msg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                            resp = requests.post(send_msg_url, json={"chat_id": chat_id, "text": response})
                            write_chat_log(f"Telegram reply status: {resp.status_code}")
                    else:
                        write_chat_log(f"Unauthorized Telegram chat attempt rejected from Chat ID: {chat_id}")
            else:
                write_chat_log(f"Telegram getUpdates returned status: {r.status_code}")
        except Exception as e:
            write_chat_log(f"Telegram listener exception: {e}")
            time.sleep(5)