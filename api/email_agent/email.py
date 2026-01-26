import os
from uuid import uuid4
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Header

sg = SendGridAPIClient(os.getenv("SENDGRID_API_KEY"))
FROM_EMAIL = os.getenv("FROM_EMAIL")

def send_email(to, subject, body, in_reply_to=None, references=None):
    message_id = f"<{uuid4()}@{FROM_EMAIL.split('@')[1]}>"
    
    message = Mail(
        from_email=Email(FROM_EMAIL),
        to_emails=To(to),
        subject=subject,
        plain_text_content=body
    )
    
    message.add_header(Header("Message-ID", message_id))

    if in_reply_to:
        message.add_header(Header("In-Reply-To", in_reply_to))

    if references:
        message.add_header(Header("References", references))

    sg.send(message)
    
    return message_id