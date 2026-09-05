import imaplib
import os
from email import message_from_bytes
from email.header import decode_header

import dropbox
from dropbox.exceptions import ApiError
from dotenv import load_dotenv

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
DROPBOX_ACCESS_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN")
DROPBOX_DEST_FOLDER = os.getenv("DROPBOX_DEST_FOLDER", "/Email_Attachments")
SEARCH_CRITERIA = os.getenv("SEARCH_CRITERIA", 'UNSEEN SUBJECT "Invoice"')
ALLOWED_EXTENSIONS = {
    ext.strip().lower()
    for ext in os.getenv("ALLOWED_EXTENSIONS", ".pdf,.xlsx,.csv,.png,.jpg").split(",")
    if ext.strip()
}


def connect_gmail():
    """Connect to Gmail via IMAP."""
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise ValueError("Set GMAIL_USER and GMAIL_APP_PASSWORD in your environment or .env file.")

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    mail.select("inbox")
    return mail


def upload_to_dropbox(dbx, file_bytes, dropbox_path):
    """Upload byte content directly to Dropbox."""
    try:
        dbx.files_upload(file_bytes, dropbox_path, mode=dropbox.files.WriteMode.overwrite)
        print(f"Successfully uploaded: {dropbox_path}")
    except ApiError as err:
        print(f"Failed to upload {dropbox_path}: {err}")


def decode_filename(filename):
    if not filename:
        return ""

    decoded_name, encoding = decode_header(filename)[0]
    if isinstance(decoded_name, bytes):
        return decoded_name.decode(encoding or "utf-8", errors="replace")
    return decoded_name


def process_emails():
    if not DROPBOX_ACCESS_TOKEN:
        raise ValueError("Set DROPBOX_ACCESS_TOKEN in your environment or .env file.")

    dbx = dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)
    mail = connect_gmail()

    try:
        status, messages = mail.search(None, SEARCH_CRITERIA)
        if status != "OK":
            raise RuntimeError(f"Gmail search failed: {status}")

        email_ids = messages[0].split()
        if not email_ids:
            print("No matching emails found.")
            return

        print(f"Found {len(email_ids)} email(s) matching criteria.")

        for email_id in email_ids:
            _, msg_data = mail.fetch(email_id, "(RFC822)")
            for response_part in msg_data:
                if not isinstance(response_part, tuple):
                    continue

                msg = message_from_bytes(response_part[1])
                for part in msg.walk():
                    if part.get_content_maintype() == "multipart":
                        continue
                    if part.get("Content-Disposition") is None:
                        continue

                    filename = part.get_filename()
                    if not filename:
                        continue

                    filename = decode_filename(filename)
                    extension = os.path.splitext(filename)[1].lower()

                    if ALLOWED_EXTENSIONS and extension not in ALLOWED_EXTENSIONS:
                        print(f"Skipping {filename} (unsupported extension: {extension})")
                        continue

                    file_bytes = part.get_payload(decode=True)
                    if not file_bytes:
                        continue

                    dropbox_path = f"{DROPBOX_DEST_FOLDER.rstrip('/')}/{filename}"
                    print(f"Processing attachment: {filename}")
                    upload_to_dropbox(dbx, file_bytes, dropbox_path)
    finally:
        mail.logout()


if __name__ == "__main__":
    process_emails()
