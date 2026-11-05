import os
import requests
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone
from fastapi import FastAPI, Request

app = FastAPI(title="TradingView Macro Filter Gateway")

# ══════════════════════════════════════════════════════════════════════════════
# ENVIRONMENT VARIABLES & CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
FINNHUB_API_KEY     = os.getenv("FINNHUB_API_KEY", "")

# Email Settings (Optional)
SMTP_SERVER  = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT    = int(os.getenv("SMTP_PORT", "587"))
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "")
SENDER_PASS  = os.getenv("SMTP_APP_PASSWORD", "")
RECV_EMAIL   = os.getenv("RECV_EMAIL", "")

# Twilio SMS Settings (Optional)
TWILIO_SID   = os.getenv("TWILIO_SID", "")
TWILIO_AUTH  = os.getenv("TWILIO_AUTH", "")
TWILIO_FROM  = os.getenv("TWILIO_FROM", "")
TARGET_PHONE = os.getenv("TARGET_PHONE", "")


# ══════════════════════════════════════════════════════════════════════════════
# MACRO & NEWS FILTER
# ══════════════════════════════════════════════════════════════════════════════
def is_macro_safe(window_minutes: int = 30) -> tuple[bool, str]:
    """
    Queries Finnhub's Economic Calendar for high-impact USD events.
    Blocks signals if high-impact news falls within window_minutes.
    """
    if not FINNHUB_API_KEY:
        return True, "Macro check bypassed (FINNHUB_API_KEY missing)"

    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")
    url = f"https://finnhub.io/api/v1/calendar/economic?from={today_str}&to={today_str}&token={FINNHUB_API_KEY}"

    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            events = response.json().get("economicCalendar", [])
            
            for event in events:
                impact = str(event.get("impact", "")).upper()
                country = str(event.get("country", "")).upper()

                # Filter for high-impact US economic events (e.g., CPI, NFP, FOMC)
                if impact in ["HIGH", "3"] and country == "US":
                    event_time = event.get("time")  # Expected HH:MM:SS format
                    if event_time:
                        event_dt_str = f"{today_str} {event_time}"
                        event_dt = datetime.strptime(event_dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        
                        diff_minutes = abs((event_dt - now_utc).total_seconds()) / 60.0
                        
                        if diff_minutes <= window_minutes:
                            event_name = event.get("event", "High-Impact News Event")
                            return False, f"High-impact news '{event_name}' in {int(diff_minutes)} mins"

    except Exception as err:
        print(f"[ERROR] Finnhub API check failed: {err}")

    return True, "No high-impact macro news nearby"


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATION HANDLERS
# ══════════════════════════════════════════════════════════════════════════════
def send_discord_notification(payload: dict, macro_info: str):
    """Formats and dispatches a rich embed notification to Discord."""
    if not DISCORD_WEBHOOK_URL or "http" not in DISCORD_WEBHOOK_URL:
        return

    signal = str(payload.get("signal", "ALERT")).upper()
    is_buy = signal == "BUY"
    color = 5763719 if is_buy else 15548997  # Green for BUY, Red for SELL

    embed = {
        "embeds": [{
            "title": f"🚨 ICT Trade Signal: {signal} ({payload.get('ticker', 'N/A')})",
            "color": color,
            "fields": [
                {"name": "Symbol", "value": str(payload.get("ticker", "N/A")), "inline": True},
                {"name": "Price", "value": str(payload.get("price", "N/A")), "inline": True},
                {"name": "Confluence Score", "value": f"{payload.get('score', 'N/A')}/5", "inline": True},
                {"name": "Timeframe", "value": str(payload.get("tf", "N/A")), "inline": True},
                {"name": "Macro Status", "value": f"✅ {macro_info}", "inline": False}
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "ICT Automated Signal Gateway"}
        }]
    }

    try:
        requests.post(DISCORD_WEBHOOK_URL, json=embed, timeout=5)
    except Exception as err:
        print(f"[ERROR] Discord post failed: {err}")


def send_email_notification(subject: str, message: str):
    """Sends an email alert via SMTP."""
    if not (SENDER_EMAIL and SENDER_PASS and RECV_EMAIL):
        return

    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECV_EMAIL

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASS)
            server.sendmail(SENDER_EMAIL, [RECV_EMAIL], msg.as_string())
    except Exception as err:
        print(f"[ERROR] Email dispatch failed: {err}")


def send_sms_notification(message: str):
    """Dispatches an SMS alert via Twilio."""
    if not (TWILIO_SID and TWILIO_AUTH and TWILIO_FROM and TARGET_PHONE):
        return

    endpoint = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
    data = {"From": TWILIO_FROM, "To": TARGET_PHONE, "Body": message}

    try:
        requests.post(endpoint, data=data, auth=(TWILIO_SID, TWILIO_AUTH), timeout=5)
    except Exception as err:
        print(f"[ERROR] SMS dispatch failed: {err}")


# ══════════════════════════════════════════════════════════════════════════════
# API ROUTE
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/")
def read_root():
    return {"status": "online", "service": "TradingView Webhook Gateway"}


@app.post("/webhook")
async def handle_webhook(request: Request):
    """Receives alerts from TradingView, filters macro risk, and dispatches notifications."""
    try:
        data = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON body"}

    # 1. Run Macro Economic Filter
    safe, macro_reason = is_macro_safe(window_minutes=30)

    if not safe:
        print(f"[BLOCKED] Signal rejected due to macro news: {macro_reason}")
        return {
            "status": "rejected",
            "reason": macro_reason,
            "signal": data.get("signal"),
            "ticker": data.get("ticker")
        }

    # 2. Prepare Alert Text
    summary_text = (
        f"ICT ALERT: {data.get('signal', 'SIGNAL')} on {data.get('ticker', 'N/A')} "
        f"@ {data.get('price', 'N/A')} (Score: {data.get('score', 'N/A')})"
    )

    # 3. Dispatch Alerts to Channels
    send_discord_notification(data, macro_reason)
    send_email_notification(f"Trade Alert: {data.get('signal')} {data.get('ticker')}", summary_text)
    send_sms_notification(summary_text)

    return {"status": "success", "macro_status": macro_reason}
