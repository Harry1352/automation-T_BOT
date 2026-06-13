# ============================================
# TESS — Alerts v3
# Gmail SMTP restored
# ntfy push notifications added as bonus
# Both work simultaneously
# ============================================

import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ── Your settings ─────────────────────────────
TESS_GMAIL            = "tessbot1352@gmail.com"
TESS_GMAIL_APP_PASSWORD = "uswkamwndvfjcfhe"
YOUR_EMAIL            = "h.tess.receiver@gmail.com"
PAPER_MODE            = True

# ── ntfy push notifications (optional bonus) ──
NTFY_TOPIC   = "tess_harshit_1352"
NTFY_ENABLED = True

MODE_TAG = "[PAPER]" if PAPER_MODE else "[LIVE]"

# ── Send push notification ─────────────────────
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

# ── Send email ────────────────────────────────
def send_email(subject, body):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Tess {MODE_TAG} — {subject}"
        msg["From"]    = TESS_GMAIL
        msg["To"]      = YOUR_EMAIL

        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;
        margin:auto;padding:24px;border:1px solid #eee;
        border-radius:12px">
          <div style="margin-bottom:20px">
            <div style="font-weight:600;font-size:18px;
            color:#111">Tess</div>
            <div style="font-size:12px;color:#888">
            {'Paper Mode' if PAPER_MODE else 'LIVE MODE'}</div>
          </div>
          <div style="background:#f9f9f9;border-radius:8px;
          padding:16px;font-size:14px;line-height:1.8;color:#333">
            {body.replace(chr(10), '<br>')}
          </div>
          <div style="margin-top:16px;font-size:11px;
          color:#aaa;text-align:center">
            {datetime.now().strftime('%A, %d %B %Y at %H:%M')}
            · Tess Trading Bot
          </div>
        </div>
        """

        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(TESS_GMAIL, TESS_GMAIL_APP_PASSWORD)
            server.sendmail(TESS_GMAIL, YOUR_EMAIL, msg.as_string())

        print(f"[Tess] Email sent — {subject}")
        return True

    except Exception as e:
        print(f"[Tess] Email failed — {e}")
        return False


def alert_trade_entered(pair, direction, entry, sl, tp,
                        confidence, reason):
    direction_word = "LONG" if direction == "long" else "SHORT"
    subject = f"{pair} {direction_word} entered — {confidence}%"
    body = (
        f"Tess just entered a trade.\n\n"
        f"Pair:       {pair}\n"
        f"Direction:  {direction_word}\n"
        f"Entry:      ${entry:,.4f}\n"
        f"Stop loss:  ${sl:,.4f}\n"
        f"Target:     ${tp:,.4f}\n"
        f"Confidence: {confidence}%\n\n"
        f"Why:\n{reason}"
    )
    send_email(subject, body)
    send_push(
        f"Tess {direction_word} {pair}",
        f"Entry ${entry:,.2f} | SL ${sl:,.2f} | TP ${tp:,.2f} | {confidence}%"
    )


def alert_trade_closed(pair, direction, entry,
                       close_price, pnl, reason):
    result = "WIN" if pnl > 0 else "LOSS"
    sign   = "+" if pnl > 0 else ""
    subject = f"{pair} closed — {result} {sign}${abs(pnl):.2f}"
    body = (
        f"Trade closed.\n\n"
        f"Pair:      {pair}\n"
        f"Direction: {'LONG' if direction == 'long' else 'SHORT'}\n"
        f"Entry:     ${entry:,.4f}\n"
        f"Exit:      ${close_price:,.4f}\n"
        f"Result:    {result}\n"
        f"P&L:       {sign}${abs(pnl):.2f}\n\n"
        f"Reason: {reason}"
    )
    send_email(subject, body)
    send_push(
        f"Tess {result} {sign}${abs(pnl):.2f}",
        f"{pair} closed | Entry ${entry:,.2f} | Exit ${close_price:,.2f}",
        priority="high" if abs(pnl) > 3 else "default"
    )


def alert_signal_skipped(pair, confidence, reason):
    send_email(
        f"{pair} skipped — {confidence}%",
        f"Setup skipped.\n\nPair: {pair}\n"
        f"Confidence: {confidence}%\n\nReason:\n{reason}"
    )


def alert_kill_switch(daily_loss, limit):
    subject = "KILL SWITCH — Tess stopped for today"
    body = (
        f"Daily loss limit hit.\n\n"
        f"Loss today: ${daily_loss:.2f}\n"
        f"Limit:      ${limit:.2f}\n\n"
        f"Tess has stopped trading for today.\n"
        f"She resumes tomorrow. Your account is safe."
    )
    send_email(subject, body)
    send_push(
        "TESS KILL SWITCH",
        f"Daily loss ${daily_loss:.2f} hit limit ${limit:.2f}. Stopped.",
        priority="urgent"
    )


def alert_morning_report(total_pnl, trades_today,
                         wins, losses, watching):
    win_rate = round(wins/trades_today*100) if trades_today > 0 else 0
    sign     = "+" if total_pnl >= 0 else ""
    subject  = f"Morning report — {sign}${abs(total_pnl):.2f} overnight"
    body = (
        f"Good morning. Here is what Tess did.\n\n"
        f"Overnight P&L: {sign}${abs(total_pnl):.2f}\n"
        f"Trades:        {trades_today}\n"
        f"Wins:          {wins}\n"
        f"Losses:        {losses}\n"
        f"Win rate:      {win_rate}%\n\n"
        f"Currently watching:\n{watching}\n\n"
        f"Tess is scanning ETH and SOL now."
    )
    send_email(subject, body)
    send_push(
        f"Tess Morning Report {sign}${abs(total_pnl):.2f}",
        f"{trades_today} trades | {wins}W {losses}L | {win_rate}% win rate"
    )


def test_alerts():
    print("[Tess] Sending test email and push...")
    email_ok = send_email(
        "Tess is alive and ready",
        "This is a test.\n\n"
        "Email alerts are working.\n"
        "Tess is watching ETH and SOL for you."
    )
    send_push(
        "Tess is alive",
        "Push notifications working. Watching ETH and SOL."
    )
    if email_ok:
        print("[Tess] Check your inbox and ntfy app.")
    else:
        print("[Tess] Email failed — check Gmail password.")


if __name__ == "__main__":
    test_alerts()