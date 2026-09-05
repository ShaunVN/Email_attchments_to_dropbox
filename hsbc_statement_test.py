import imaplib
import os
from email import message_from_bytes
from email.header import decode_header
from email.utils import parseaddr

from dotenv import load_dotenv

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
SEARCH_CRITERIA = os.getenv(
    "SEARCH_CRITERIA",
    'FROM "HSBC@connect.hsbc.com.au" SUBJECT "Your Monthly HSBC Bank Statement" UNSEEN',
)
TARGET_SENDER = "HSBC@connect.hsbc.com.au".lower()
TARGET_SUBJECT = "Your Monthly HSBC Bank Statement".lower()


def connect_gmail():
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise ValueError("Set GMAIL_USER and GMAIL_APP_PASSWORD in your environment or .env file.")

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    mail.select("inbox")
    return mail


def decode_header_value(value):
    if not value:
        return ""

    decoded_parts = decode_header(value)
    text = "".join(
        part.decode(charset or "utf-8", errors="replace") if isinstance(part, bytes) else part
        for part, charset in decoded_parts
    )
    return text


def is_expected_email(msg):
    from_address = parseaddr(msg.get("From", ""))[1].lower()
    subject = decode_header_value(msg.get("Subject", "")).lower()
    return from_address == TARGET_SENDER and subject == TARGET_SUBJECT


def print_match_details(email_id, msg):
    sender = decode_header_value(msg.get("From", ""))
    subject = decode_header_value(msg.get("Subject", ""))
    date = msg.get("Date", "")
    attachments = []

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get("Content-Disposition") is None:
            continue

        filename = part.get_filename()
        if not filename:
            continue

        filename = decode_header_value(filename)
        if filename.lower().endswith(".pdf"):
            attachments.append(filename)

    print("-" * 80)
    print(f"Email ID: {email_id.decode()}")
    print(f"From: {sender}")
    print(f"Date: {date}")
    print(f"Subject: {subject}")
    print(f"PDF attachments: {attachments if attachments else 'None'}")
    print("-" * 80)


def main():
    mail = connect_gmail()
    try:
        status, messages = mail.search(None, SEARCH_CRITERIA)
        if status != "OK":
            raise RuntimeError(f"Gmail search failed: {status}")

        email_ids = messages[0].split()
        if not email_ids:
            print("No HSBC matching emails found.")
            return

        print(f"Found {len(email_ids)} potential HSBC email(s). Safe mode: no attachments will be saved, moved, or marked as read.")

        for email_id in email_ids:
            _, msg_data = mail.fetch(email_id, "(RFC822)")
            for response_part in msg_data:
                if not isinstance(response_part, tuple):
                    continue

                msg = message_from_bytes(response_part[1])
                if not is_expected_email(msg):
                    continue

                print_match_details(email_id, msg)
    finally:
        mail.logout()


if __name__ == "__main__":
    main()
