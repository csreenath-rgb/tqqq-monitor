"""
alerts.py  -  pluggable alert channels: email (SMTP) + Telegram.

All credentials come from environment variables (set as GitHub Actions secrets).
Nothing sensitive is ever committed to the repo.

Required secrets (set the ones for channels you want; missing channels are skipped):

  EMAIL
    SMTP_HOST       e.g. smtp.gmail.com
    SMTP_PORT       e.g. 587
    SMTP_USER       the sending account's address
    SMTP_PASS       an APP PASSWORD (not your login password)  <-- important
    EMAIL_FROM      usually same as SMTP_USER
    EMAIL_TO        comma-separated family recipients, e.g. a@x.com,b@y.com

  TELEGRAM
    TELEGRAM_BOT_TOKEN   from @BotFather
    TELEGRAM_CHAT_ID     the family group's chat id (see README to obtain it)

Design choice: email + Telegram are the only channels. Telegram was chosen over
Slack because one bot token + one group chat reaches everyone with no per-user
setup. To swap in Slack, add a send_slack() with the same (subject, body)->bool
shape and register it in send_all().
"""
import os
import smtplib
import json
import urllib.request
import urllib.parse
from email.message import EmailMessage


def _env(*names):
    return all(os.environ.get(n) for n in names)


def send_email(subject, body):
    """Returns True on success, False if not configured / failed."""
    if not _env("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "EMAIL_FROM", "EMAIL_TO"):
        print("  email: not configured (skipped)")
        return False
    recipients = [r.strip() for r in os.environ["EMAIL_TO"].split(",") if r.strip()]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["EMAIL_FROM"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"]), timeout=30) as s:
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.send_message(msg)
        print(f"  email: sent to {len(recipients)} recipient(s)")
        return True
    except Exception as e:
        print(f"  email: FAILED ({e})")
        return False


def send_telegram(subject, body):
    """Returns True on success, False if not configured / failed."""
    if not _env("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        print("  telegram: not configured (skipped)")
        return False
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    text = f"*{subject}*\n\n{body}"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text, "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=30) as resp:
            ok = json.loads(resp.read().decode()).get("ok", False)
        print(f"  telegram: {'sent' if ok else 'API returned not-ok'}")
        return bool(ok)
    except Exception as e:
        print(f"  telegram: FAILED ({e})")
        return False


def send_all(subject, body):
    """Fan out to every configured channel. Returns {channel: success_bool}."""
    return {"email": send_email(subject, body),
            "telegram": send_telegram(subject, body)}
