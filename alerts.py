import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

PAPER_MODE = True
TESS_GMAIL = "tessbot1352@gmail.com"
TESS_GMAIL_APP_PASSWORD = "zdohidapzpgkupgt"
YOUR_EMAIL = "h.tess.receiver@gmail.com"

MODE_TAG = "[PAPER]" if PAPER_MODE else "[LIVE]"

def send_email(subject, body):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Tess {MODE_TAG} — {subject}"
        msg["From"] = TESS_GMAIL
        msg["To"] = YOUR_EMAIL

        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:24px;border:1px solid #eee;border-radius:12px">
          <div style="margin-bottom:20px">
            <div style="font-weight:600;font-size:18px;color:#111">Tess</div>
            <div style="font-size:12px;color:#888">{'Paper Mode' if PAPER_MODE else 'LIVE MODE'}</div>
          </div>
          <div style="background:#f9f9f9;border-radius:8px;padding:16px;font-size:14px;line-height:1.8;color:#333">
            {body.replace(chr(10), '<br>')}
          </div>
          <div style="margin-top:16px;font-size:11px;color:#aaa;text-align:center">
            {datetime.now().strftime('%A, %d %B %Y at %H:%M')} · Tess Trading Bot
          </div>
        </div>
        """

        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(TESS_GMAIL, TESS_GMAIL_APP_PASSWORD)
            server.sendmail(TESS_GMAIL, YOUR_EMAIL, msg.as_string())

        print(f"[Tess] Email sent successfully")
        return True

    except Exception as e:
        print(f"[Tess] Email failed — {e}")
        return False


def alert_trade_entered(pair, direction, entry, sl, tp, confidence, reason):
    direction_word = "LONG (buying)" if direction == "long" else "SHORT (selling)"
    subject = f"{pair} trade entered — {confidence}% confidence"
    body = f"""
Tess just entered a trade.

Pair:         {pair}
Direction:    {direction_word}
Entry price:  ${entry:,.2f}
Stop loss:    ${sl:,.2f}
Take profit:  ${tp:,.2f}
Confidence:   {confidence}%

Why she entered:
{reason}
    """
    send_email(subject, body)


def alert_trade_closed(pair, direction, entry, close_price, pnl, reason):
    result = "WIN" if pnl > 0 else "LOSS"
    subject = f"{pair} closed — {result} ${abs(pnl):,.2f}"
    body = f"""
Tess closed a trade.

Pair:         {pair}
Direction:    {'LONG' if direction == 'long' else 'SHORT'}
Entry:        ${entry:,.2f}
Exit:         ${close_price:,.2f}
Result:       {result}
P&L:          {'+'if pnl > 0 else '-'}${abs(pnl):,.2f}

Why she closed:
{reason}
    """
    send_email(subject, body)


def alert_signal_skipped(pair, confidence, reason):
    subject = f"{pair} signal skipped — {confidence}% confidence"
    body = f"""
Tess saw a setup but skipped it.

Pair:        {pair}
Confidence:  {confidence}% (needs 80% minimum)

Why she skipped:
{reason}

This is Tess being disciplined.
    """
    send_email(subject, body)


def alert_kill_switch(daily_loss, limit):
    subject = "KILL SWITCH — Tess stopped trading for today"
    body = f"""
Tess hit her daily loss limit and has stopped all trading.

Loss today:  ${daily_loss:,.2f}
Your limit:  ${limit:,.2f}

She will resume tomorrow. Your account is safe.
    """
    send_email(subject, body)


def alert_morning_report(total_pnl, trades_today, wins, losses, watching):
    win_rate = round((wins / trades_today * 100)) if trades_today > 0 else 0
    subject = f"Morning report — {'+'if total_pnl >= 0 else '-'}${abs(total_pnl):,.2f} overnight"
    body = f"""
Good morning. Here is what Tess did while you slept.

Overnight P&L:  {'+'if total_pnl >= 0 else '-'}${abs(total_pnl):,.2f}
Trades taken:   {trades_today}
Wins:           {wins}
Losses:         {losses}
Win rate:       {win_rate}%

Currently watching:
{watching}

Tess is active and scanning ETH and SOL.
    """
    send_email(subject, body)


def test_alerts():
    print("[Tess] Sending test email...")
    result = send_email(
        "Tess is alive and ready",
        "This is a test message.\n\nTess is set up correctly.\nEmail alerts are working.\n\nShe is ready to start scanning ETH and SOL for you."
    )
    if result:
        print("[Tess] Check your inbox now.")
    else:
        print("[Tess] Something went wrong. Check your Gmail and app password.")


test_alerts()

