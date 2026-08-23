# ==============================================================================
# TESS — Alerts & SMTP Email Dispatcher Module (v3.1 - Resilient Edition)
# ==============================================================================

import smtplib
import socket
import requests
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# --- Load settings dynamically from central keys config ---
try:
    import keys
    TESS_GMAIL = getattr(keys, "TESS_GMAIL", "")
    TESS_GMAIL_APP_PASSWORD = getattr(keys, "TESS_GMAIL_APP_PASSWORD", "")
    YOUR_EMAIL = getattr(keys, "YOUR_EMAIL", "")
except ImportError:
    TESS_GMAIL = ""
    TESS_GMAIL_APP_PASSWORD = ""
    YOUR_EMAIL = ""

NTFY_TOPIC = "tess_harshit_1352"
NTFY_ENABLED = True
PAPER_MODE = True
MODE_TAG = "[PAPER]" if PAPER_MODE else "[LIVE]"

# ── Dynamic Helper to write to local diary thinking log ───────────────────────
def write_thinking_log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(".tess_diary", exist_ok=True)
        with open(".tess_diary/tess_thinking.log", "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [Alerts] {message}\n")
    except:
        pass

# ── Send Push Notification ─────────────────────
def send_push(title, message, priority="default"):
    if not NTFY_ENABLED:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority},
            timeout=5
        )
    except:
        pass

# ── Send Email with Automated Retry & Socket Timeout Guards ────────────────────
def send_email(subject, body):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Tess {MODE_TAG} — {subject}"
        msg["From"] = TESS_GMAIL
        msg["To"] = YOUR_EMAIL

        # Set up a clean HTML wrapper for the email body
        if "<html>" in body:
            html_content = body
        else:
            # Fallback wrapper for plain text notifications
            html_content = f"""
            <html>
            <body style="background-color:#0f172a; font-family:sans-serif; color:#f8fafc; padding:20px;">
                <div style="max-width:600px; margin:0 auto; background-color:#1e293b; padding:30px; border-radius:12px;">
                    <h2 style="color:#38bdf8; margin-top:0;">TESSWORKS Alerts</h2>
                    <hr style="border:0; border-top:1px solid #334155; margin:20px 0;"/>
                    <p style="white-space: pre-wrap; line-height: 1.6;">{body}</p>
                </div>
            </body>
            </html>
            """
        msg.attach(MIMEText(html_content, "html"))

        # Connection parameters with high-conformance retries
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Force a strict 10-second timeout constraint to prevent system hangs
                server = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
                server.starttls()
                server.login(TESS_GMAIL, TESS_GMAIL_APP_PASSWORD)
                server.sendmail(TESS_GMAIL, YOUR_EMAIL, msg.as_string())
                server.quit()
                print(f"[Tess] Email sent — {subject}")
                return True
            except (socket.timeout, smtplib.SMTPException, Exception) as e:
                if attempt == max_retries - 1:
                    print(f"[Tess] Email failed — Connection unexpectedly closed: {e}")
                    write_thinking_log(f"All SMTP retries failed for '{subject}'. Error: {e}")
                else:
                    time.sleep(2)  # Wait 2 seconds before retrying
                    
    except Exception as e:
        print(f"[Tess] Alerts Error: {e}")
    return False

# ── Strategy Specific Alert Adapters ───────────
def alert_trade_entered(pair, direction, entry, sl, tp, confidence, reason):
    subject = f"{pair} {direction.upper()} entered — {confidence}%"
    body = (
        f"Tess has established an active position.\n\n"
        f"Asset: {pair}\n"
        f"Direction: {direction.upper()}\n"
        f"Entry Price: ${entry:,.4f}\n"
        f"Stop Loss: ${sl:,.4f}\n"
        f"Take Profit: ${tp:,.4f}\n"
        f"Confidence: {confidence}%\n\n"
        f"Strategic Reasoning:\n{reason}"
    )
    send_email(subject, body)
    send_push(f"Tess {direction.upper()} {pair}", f"Entry ${entry:,.2f} | SL ${sl:,.2f} | TP ${tp:,.2f} | {confidence}%")

def alert_trade_closed(pair, direction, entry, close, pnl, reason):
    result = "WIN" if pnl > 0 else "LOSS"
    sign = "+" if pnl > 0 else ""
    subject = f"{pair} closed — {result} {sign}${abs(pnl):.2f}"
    body = (
        f"Position closed.\n\n"
        f"Asset: {pair}\n"
        f"Direction: {direction.upper()}\n"
        f"Entry Price: ${entry:,.4f}\n"
        f"Exit Price: ${close:,.4f}\n"
        f"Result: {result}\n"
        f"Net P&L: {sign}${pnl:,.2f} USD\n\n"
        f"Exit Reason: {reason}"
    )
    send_email(subject, body)
    send_push(f"Tess {result} {sign}${abs(pnl):.2f}", f"{pair} closed | Entry ${entry:,.2f} | Exit ${close:,.2f}")

def alert_kill_switch(daily_loss, limit):
    subject = "KILL SWITCH — Tess stopped for today"
    body = (
        f"Daily loss limit has been breached.\n\n"
        f"Current Realized Daily Loss: ${daily_loss:.2f}\n"
        f"Daily System Limit: ${limit:.2f}\n\n"
        f"All automated scanning and trading operations have been frozen for the remainder of this calendar day. "
        f"Counters will automatically reset at midnight. Your account is protected."
    )
    send_email(subject, body)
    send_push("TESS KILL SWITCH", f"Daily loss ${daily_loss:.2f} hit limit ${limit:.2f}. Stopped.", "urgent")

def alert_morning_report(total_pnl, trades_today, wins, losses, watching):
    win_rate = round(wins / trades_today * 100) if trades_today > 0 else 0
    sign = "+" if total_pnl >= 0 else ""
    subject = f"Morning report — {sign}${abs(total_pnl):.2f} overnight"
    body = (
        f"Good morning. Here is your overnight operational digest:\n\n"
        f"Overnight Net P&L: {sign}${total_pnl:,.2f} USD\n"
        f"Completed Trades: {trades_today}\n"
        f"Wins: {wins}\n"
        f"Losses: {losses}\n"
        f"Win Rate: {win_rate}%\n\n"
        f"Active Watchlist: {watching}\n"
    )
    send_email(subject, body)
    send_push(f"Tess Morning Report {sign}${abs(total_pnl):.2f}", f"{trades_today} trades | {wins}W {losses}L | {win_rate}% win rate")