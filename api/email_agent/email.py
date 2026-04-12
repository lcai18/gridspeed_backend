import os
from uuid import uuid4

import httpx
from dotenv import load_dotenv

load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL")
RESEND_API_URL = "https://api.resend.com/emails"


def send_email(to, subject, body, in_reply_to=None, references=None):
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is not configured")
    if not FROM_EMAIL:
        raise RuntimeError("FROM_EMAIL is not configured")

    message_id = f"<{uuid4()}@{FROM_EMAIL.split('@')[1]}>"

    headers = {"Message-ID": message_id}
    if in_reply_to:
        headers["In-Reply-To"] = in_reply_to
    if references:
        headers["References"] = references

    payload = {
        "from": FROM_EMAIL,
        "to": [to],
        "subject": subject,
        "text": body,
        "headers": headers,
    }

    response = httpx.post(
        RESEND_API_URL,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30.0,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"Resend send failed: {response.status_code} {response.text}")

    return message_id
