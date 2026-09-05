import hashlib
import imaplib
import os
from email import message_from_bytes
from email.header import decode_header
from email.utils import parseaddr

from dotenv import load_dotenv

try:
    import dropbox
    from dropbox.exceptions import ApiError
except ImportError:  # pragma: no cover
    dropbox = None
    ApiError = None

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
DROPBOX_ACCESS_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN")
LOCAL_DEST_FOLDER = os.getenv(
    "LOCAL_DEST_FOLDER",
    r"C:\path\to\your\Dropbox\folder\HSBC\Loan statements",
)
DROPBOX_DEST_FOLDER = os.getenv("DROPBOX_DEST_FOLDER", "/Email_Attachments")
SEARCH_CRITERIA = os.getenv(
    "SEARCH_CRITERIA",
    'FROM "HSBC@connect.hsbc.com.au" SUBJECT "Your Monthly HSBC Bank Statement" UNSEEN',
)
TARGET_SENDER = "HSBC@connect.hsbc.com.au".lower()
TARGET_SUBJECT = "Your Monthly HSBC Bank Statement".lower()


def connect_gmail():
    """Connect to Gmail via IMAP."""
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


def file_hash(file_bytes):
    return hashlib.sha256(file_bytes).hexdigest()


def upload_to_dropbox(dbx, file_bytes, dropbox_path):
    """Upload byte content directly to Dropbox when configured."""
    if dropbox is None or ApiError is None:
        return

    try:
        dbx.files_upload(file_bytes, dropbox_path, mode=dropbox.files.WriteMode.overwrite)
        print(f"Successfully uploaded: {dropbox_path}")
    except ApiError as err:
        print(f"Failed to upload {dropbox_path}: {err}")


def save_attachment_locally(file_bytes, filename, target_folder, seen_hashes):
    file_digest = file_hash(file_bytes)
    if file_digest in seen_hashes:
        print(f"Skipping duplicate HSBC statement: {filename}")
        return

    seen_hashes.add(file_digest)
    os.makedirs(target_folder, exist_ok=True)
    destination = os.path.join(target_folder, filename)
    with open(destination, "wb") as output_file:
        output_file.write(file_bytes)
    print(f"Saved attachment: {destination}")


def is_expected_email(msg):
    from_address = parseaddr(msg.get("From", ""))[1].lower()
    subject = decode_header_value(msg.get("Subject", "")).lower()
    return from_address == TARGET_SENDER and subject == TARGET_SUBJECT


def process_emails():
    mail = connect_gmail()
    seen_hashes = set()

    try:
        status, messages = mail.search(None, SEARCH_CRITERIA)
        if status != "OK":
            raise RuntimeError(f"Gmail search failed: {status}")

        email_ids = messages[0].split()
        if not email_ids:
            print("No matching HSBC emails found.")
            return

        print(f"Found {len(email_ids)} HSBC email(s) matching the criteria.")

        if DROPBOX_ACCESS_TOKEN and dropbox is not None:
            dbx = dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)
        else:
            dbx = None
            os.makedirs(LOCAL_DEST_FOLDER, exist_ok=True)
            print(f"Saving HSBC PDF statements to: {LOCAL_DEST_FOLDER}")

        for email_id in email_ids:
            _, msg_data = mail.fetch(email_id, "(RFC822)")
            for response_part in msg_data:
                if not isinstance(response_part, tuple):
                    continue

                msg = message_from_bytes(response_part[1])
                if not is_expected_email(msg):
                    print(f"Skipping email not matching HSBC statement criteria: {msg.get('Subject')}")
                    continue

                for part in msg.walk():
                    if part.get_content_maintype() == "multipart":
                        continue
                    if part.get("Content-Disposition") is None:
                        continue

                    filename = part.get_filename()
                    if not filename:
                        continue

                    filename = decode_header_value(filename)
                    if not filename.lower().endswith(".pdf"):
                        print(f"Skipping {filename} (not a PDF attachment)")
                        continue

                    file_bytes = part.get_payload(decode=True)
                    if file_bytes is None:
                        continue

                    if dbx is not None:
                        file_digest = file_hash(file_bytes)
                        if file_digest in seen_hashes:
                            print(f"Skipping duplicate HSBC statement: {filename}")
                            continue
                        seen_hashes.add(file_digest)
                        dropbox_path = f"{DROPBOX_DEST_FOLDER.rstrip('/')}/{filename}"
                        print(f"Processing HSBC PDF attachment: {filename}")
                        upload_to_dropbox(dbx, file_bytes, dropbox_path)
                    else:
                        save_attachment_locally(file_bytes, filename, LOCAL_DEST_FOLDER, seen_hashes)
    finally:
        mail.logout()


if __name__ == "__main__":
    process_emails()
