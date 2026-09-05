import hashlib
import json
import imaplib
import os
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header
from email.utils import parsedate_to_datetime, parseaddr

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
GMAIL_HOMELOANS_FOLDER = os.getenv("GMAIL_HOMELOANS_FOLDER", "HomeLoans")
LAST_PROCESSED_DATE = os.getenv("LAST_PROCESSED_DATE", "")
STATE_FILE = os.getenv("STATE_FILE", "processed_statements.json")
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


def normalize_datetime(value):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None


def load_processed_state():
    if not os.path.exists(STATE_FILE):
        return {"last_processed_date": LAST_PROCESSED_DATE}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (json.JSONDecodeError, OSError):
        return {"last_processed_date": LAST_PROCESSED_DATE}

    if "last_processed_date" not in state and LAST_PROCESSED_DATE:
        state["last_processed_date"] = LAST_PROCESSED_DATE
    return state


def save_processed_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, indent=2)


def file_hash(file_bytes):
    return hashlib.sha256(file_bytes).hexdigest()


def ensure_gmail_home_loans_folder(mail):
    status, folder_data = mail.list()
    if status != "OK":
        raise RuntimeError(f"Failed to list Gmail folders: {status}")

    folder_name = f'"{GMAIL_HOMELOANS_FOLDER}"'
    for entry in folder_data or []:
        if folder_name.encode("utf-8") in entry or GMAIL_HOMELOANS_FOLDER.encode("utf-8") in entry:
            return

    result = mail.create(GMAIL_HOMELOANS_FOLDER)
    if result[0] != "OK":
        print(f"Could not create Gmail label/folder '{GMAIL_HOMELOANS_FOLDER}'")


def move_email_to_home_loans(mail, email_id):
    mail.store(email_id, "+FLAGS", "(\\Seen)")
    mail.copy(email_id, GMAIL_HOMELOANS_FOLDER)
    mail.store(email_id, "+FLAGS", "\\Deleted")
    mail.expunge()
    print(f"Moved email {email_id.decode()} to folder/label '{GMAIL_HOMELOANS_FOLDER}' and marked as read.")


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
        return False

    seen_hashes.add(file_digest)
    os.makedirs(target_folder, exist_ok=True)
    destination = os.path.join(target_folder, filename)
    with open(destination, "wb") as output_file:
        output_file.write(file_bytes)
    print(f"Saved attachment: {destination}")
    return True


def is_expected_email(msg):
    from_address = parseaddr(msg.get("From", ""))[1].lower()
    subject = decode_header_value(msg.get("Subject", "")).lower()
    return from_address == TARGET_SENDER and subject == TARGET_SUBJECT


def should_skip_email(msg, processed_state):
    email_date = normalize_datetime(msg.get("Date"))
    last_processed = processed_state.get("last_processed_date")
    if not email_date or not last_processed:
        return False

    try:
        last_processed_dt = datetime.fromisoformat(last_processed)
    except ValueError:
        return False

    return email_date <= last_processed_dt.astimezone(email_date.tzinfo or timezone.utc)


def process_emails():
    mail = connect_gmail()
    processed_state = load_processed_state()
    seen_hashes = set()

    try:
        ensure_gmail_home_loans_folder(mail)

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

                if should_skip_email(msg, processed_state):
                    print(f"Skipping email older than last processed date: {msg.get('Subject')}")
                    continue

                saved_any = False
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
                        if save_attachment_locally(file_bytes, filename, LOCAL_DEST_FOLDER, seen_hashes):
                            saved_any = True

                if saved_any or dbx is not None:
                    move_email_to_home_loans(mail, email_id)
                    email_datetime = normalize_datetime(msg.get("Date"))
                    if email_datetime:
                        processed_state["last_processed_date"] = email_datetime.isoformat()
                        save_processed_state(processed_state)
    finally:
        mail.logout()


if __name__ == "__main__":
    process_emails()
