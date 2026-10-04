"""
Sends email alerts via Gmail SMTP. Needs three environment variables
in your .env file:

    EMAIL_SENDER=msandersoddd@gmail.com
    EMAIL_APP_PASSWORD=<your 16-character Gmail App Password>
    EMAIL_RECIPIENT=msandersoddd@gmail.com

Get the App Password from your Google Account -> Security ->
2-Step Verification (must be on) -> App Passwords -> generate one for
"Mail". Your regular Gmail password will NOT work here.

Failures here are deliberately non-fatal -- a broken email config
should never crash real trading. Errors just print to the terminal.
"""
import os
import smtplib
from email.mime.text import MIMEText


def send_email_alert(subject: str, body: str) -> bool:
    sender = os.environ.get("EMAIL_SENDER")
    app_password = os.environ.get("EMAIL_APP_PASSWORD")
    recipient = os.environ.get("EMAIL_RECIPIENT")

    if not sender or not app_password or not recipient:
        print("Email alert skipped -- EMAIL_SENDER/EMAIL_APP_PASSWORD/EMAIL_RECIPIENT "
              "not all set in .env.")
        return False

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.starttls()
            server.login(sender, app_password)
            server.send_message(msg)
        print(f"Email alert sent to {recipient}: {subject}")
        return True
    except Exception as e:
        print(f"Email alert failed ({e}) -- continuing, not crashing trading.")
        return False
