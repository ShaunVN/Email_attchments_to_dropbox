"""
Email attachment automation for HSBC monthly statements.
Features added:
- Persistent processed hashes to avoid duplicates across runs
- --dry-run flag to preview actions without modifying emails or files
- Inline comments and clearer structure for maintainability
- State stored in processed_statements.json with keys: last_processed_date, processed_hashes
"""

import argparse
import hashlib
import json
import imaplib
import os
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header
from email.utils import parsedate_to_datetime, parseaddr
from io import BytesIO

from dotenv import load_dotenv

# Dropbox SDK is optional; script works in local-only mode when missing
try:
    import dropbox
    from dropbox.exceptions import ApiError
except ImportError:  # pragma: no cover
    dropbox = None
    ApiError = None

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:  # pragma: no cover
    PdfReader = None
    PdfWriter = None

load_dotenv()

# ---------- Configuration (loaded from environment or .env) ----------
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
DROPBOX_ACCESS_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN")
# LOCAL_DEST_FOLDER is the local Dropbox-synced path where attachments will be saved when not uploading
LOCAL_DEST_FOLDER = os.getenv(
    "LOCAL_DEST_FOLDER",
    r"C:\path\to\your\Dropbox\folder\HSBC\Loan statements",
)
DROPBOX_DEST_FOLDER = os.getenv("DROPBOX_DEST_FOLDER", "/Email_Attachments")
GMAIL_HOMELOANS_FOLDER = os.getenv("GMAIL_HOMELOANS_FOLDER", "HomeLoans")
# LAST_PROCESSED_DATE is optional override; persisted state will take precedence
LAST_PROCESSED_DATE = os.getenv("LAST_PROCESSED_DATE", "")
# STATE_FILE stores JSON with 'last_processed_date' and 'processed_hashes' list
STATE_FILE = os.getenv("STATE_FILE", "processed_statements.json")
# By default uploads are disabled to avoid sandboxed App-folder behavior; set UPLOAD_TO_DROPBOX=true to enable uploading
UPLOAD_TO_DROPBOX = os.getenv("UPLOAD_TO_DROPBOX", "false").lower() in ("1", "true", "yes")
# Optional PDF password used to decrypt encrypted statements before saving/opening them.
PDF_PASSWORD = os.getenv("PDF_PASSWORD", "")
PDF_DECRYPT_ENABLED = os.getenv("PDF_DECRYPT_ENABLED", "false").lower() in ("1", "true", "yes")
LOG_FILE = os.getenv("LOG_FILE", "hsbc_statement_log.txt")
SEARCH_CRITERIA = os.getenv(
    "SEARCH_CRITERIA",
    'FROM "HSBC@connect.hsbc.com.au" SUBJECT "Your Monthly HSBC Bank Statement" UNSEEN',
)
TARGET_SENDER = "HSBC@connect.hsbc.com.au".lower()
TARGET_SUBJECT = "Your Monthly HSBC Bank Statement".lower()


# ---------- Helpers: IMAP connection and header decoding ----------
def connect_gmail():
    """Connect to Gmail via IMAP using credentials from env/.env."""
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise ValueError("Set GMAIL_USER and GMAIL_APP_PASSWORD in your environment or .env file.")

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    # Select the inbox mailbox; adjust if you need a different mailbox
    mail.select("inbox")
    return mail


def decode_header_value(value):
    """Decode an RFC-2047 encoded header into a unicode string."""
    if not value:
        return ""

    decoded_parts = decode_header(value)
    text = "".join(
        part.decode(charset or "utf-8", errors="replace") if isinstance(part, bytes) else part
        for part, charset in decoded_parts
    )
    return text


def normalize_datetime(value):
    """Parse an email Date header into a datetime or return None."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None


# ---------- Persistent state (checkpoint + processed hashes) ----------
def load_processed_state():
    """Load the processing state from STATE_FILE.

    The state JSON has the shape:
      {
        "last_processed_date": "<iso timestamp>" or "",
        "processed_keys": ["filename_ddmmyyyy|size", ...]
      }
    """
    default = {"last_processed_date": LAST_PROCESSED_DATE, "processed_keys": []}
    if not os.path.exists(STATE_FILE):
        return default

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (json.JSONDecodeError, OSError):
        return default

    # Ensure keys exist
    state.setdefault("last_processed_date", LAST_PROCESSED_DATE)
    state.setdefault("processed_keys", [])
    return state


def save_processed_state(state):
    """Persist the processing state to STATE_FILE."""
    with open(STATE_FILE, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, indent=2)


def file_hash(file_bytes):
    """Return SHA-256 hex digest for bytes — used to detect duplicate attachments."""
    return hashlib.sha256(file_bytes).hexdigest()


def write_log(message):
    """Append a timestamped message to the main runtime log file and print it."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as log_handle:
            log_handle.write(line + "\n")
    except OSError:
        # Keep the main flow running even if the log file cannot be written.
        pass


def decrypt_pdf_bytes(file_bytes, password=None):
    """Return decrypted PDF bytes when the file is encrypted and a password is provided."""
    if not password or not PDF_DECRYPT_ENABLED or PdfReader is None or PdfWriter is None:
        return file_bytes, False, "not-decrypted"

    try:
        reader = PdfReader(BytesIO(file_bytes))
        if not reader.is_encrypted:
            return file_bytes, False, "not-encrypted"

        decrypt_result = reader.decrypt(password)
        if decrypt_result == 0:
            return file_bytes, False, "decrypt-failed"

        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)

        output = BytesIO()
        writer.write(output)
        return output.getvalue(), True, "decrypted"
    except Exception as exc:
        return file_bytes, False, f"decrypt-error:{exc}"


# ---------- Gmail label/folder management helpers ----------
def ensure_gmail_home_loans_folder(mail):
    """Ensure the HomeLoans label/folder exists in the account; create if missing."""
    status, folder_data = mail.list()
    if status != "OK":
        raise RuntimeError(f"Failed to list Gmail folders: {status}")

    folder_name = f'"{GMAIL_HOMELOANS_FOLDER}"'
    for entry in folder_data or []:
        if folder_name.encode("utf-8") in entry or GMAIL_HOMELOANS_FOLDER.encode("utf-8") in entry:
            # Found existing folder/label
            return

    # Create the label (IMAP CREATE). If it fails, we'll continue but print a warning.
    result = mail.create(GMAIL_HOMELOANS_FOLDER)
    if result[0] != "OK":
        print(f"Could not create Gmail label/folder '{GMAIL_HOMELOANS_FOLDER}'")


def move_email_to_home_loans(mail, email_id):
    """Move a message out of Inbox into the HomeLoans label and mark it seen/deleted.

    Implemented as: mark seen -> copy to label -> mark deleted -> expunge
    This is a standard IMAP move pattern.
    """
    # Mark as seen
    mail.store(email_id, "+FLAGS", "(\\Seen)")
    # Copy to HomeLoans label
    mail.copy(email_id, GMAIL_HOMELOANS_FOLDER)
    # Mark original for deletion and expunge to remove from Inbox
    mail.store(email_id, "+FLAGS", "\\Deleted")
    mail.expunge()
    try:
        print(f"Moved email {email_id.decode()} to folder/label '{GMAIL_HOMELOANS_FOLDER}' and marked as read.")
    except Exception:
        # email_id may already be a string in some contexts
        print(f"Moved email {email_id} to folder/label '{GMAIL_HOMELOANS_FOLDER}' and marked as read.")


# ---------- Dropbox/local save helpers ----------
def upload_to_dropbox(dbx, file_bytes, dropbox_path, dry_run=False):
    """Upload byte content directly to Dropbox when configured. In dry-run, only print."""
    if dry_run:
        write_log(f"[dry-run] Would upload to Dropbox: {dropbox_path}")
        return True

    if dropbox is None or ApiError is None:
        write_log("Dropbox SDK not available; skipping upload.")
        return False

    try:
        dbx.files_upload(file_bytes, dropbox_path, mode=dropbox.files.WriteMode.overwrite)
        write_log(f"Successfully uploaded: {dropbox_path}")
        return True
    except ApiError as err:
        write_log(f"Failed to upload {dropbox_path}: {err}")
        return False


def build_unique_filename(filename, email_date):
    """Return filename with a date suffix inserted before the extension.

    Example: statement.pdf + 2026-09-05 => statement_05092026.pdf
    """
    stem, extension = os.path.splitext(filename)
    if not email_date:
        return filename

    suffix = email_date.strftime("%d%m%Y")
    return f"{stem}_{suffix}{extension}"


def save_attachment_locally(file_bytes, filename, target_folder, processed_keys, email_date=None, dry_run=False):
    """Save attachment to local folder with a unique date-suffixed filename. Returns True if saved.

    Uses processed_keys (persistent) to avoid saving the same filename+date+size multiple times.
    """
    unique_filename = build_unique_filename(filename, email_date)
    # use size as additional simple discriminator
    key = f"{unique_filename}|{len(file_bytes)}"
    if key in processed_keys:
        write_log(f"SKIPPED -> already-processed file (by filename+date+size): {unique_filename}")
        return False

    destination = os.path.join(target_folder, unique_filename)

    if dry_run:
        write_log(f"[dry-run] SAVE -> {destination}")
        return True

    # Optional password-based decryption for encrypted PDFs if user specifies a password
    if PDF_DECRYPT_ENABLED and PDF_PASSWORD:
        decrypted_bytes, decrypted, decrypt_status = decrypt_pdf_bytes(file_bytes, PDF_PASSWORD)
        if decrypted:
            file_bytes = decrypted_bytes
            write_log(f"DECRYPTED -> {unique_filename} ({decrypt_status})")
        else:
            write_log(f"DECRYPTION-FAILED -> {unique_filename} ({decrypt_status})")
            # If decrypt failed, keep original bytes so we still try to save the file.

    processed_keys.add(key)
    os.makedirs(target_folder, exist_ok=True)
    with open(destination, "wb") as output_file:
        output_file.write(file_bytes)
    write_log(f"SAVED -> {destination}")
    return True


# ---------- Email/message helpers ----------
def is_expected_email(msg):
    """Return True when message sender and subject match the HSBC statement pattern."""
    from_address = parseaddr(msg.get("From", ""))[1].lower()
    subject = decode_header_value(msg.get("Subject", "")).lower()
    return from_address == TARGET_SENDER and subject == TARGET_SUBJECT


def should_skip_email(msg, processed_state):
    """Decide if email is older-or-equal to the last processed date (skip if so)."""
    email_date = normalize_datetime(msg.get("Date"))
    last_processed = processed_state.get("last_processed_date")
    if not email_date or not last_processed:
        return False

    try:
        last_processed_dt = datetime.fromisoformat(last_processed)
    except ValueError:
        return False

    # Compare using the message timezone when available
    return email_date <= last_processed_dt.astimezone(email_date.tzinfo or timezone.utc)


# ---------- Main processing flow ----------
def process_emails(dry_run=False):
    """Main: search, validate, extract PDF attachments, save/upload, and move emails.

    When dry_run=True the function will only print what it would do.
    """
    mail = connect_gmail()
    # Load persistent state (last_processed_date + processed_keys)
    processed_state = load_processed_state()
    # Use a set for quick lookups; start with persisted keys
    processed_keys = set(processed_state.get("processed_keys", []))

    try:
        # Ensure Gmail label exists before moving messages
        ensure_gmail_home_loans_folder(mail)

        status, messages = mail.search(None, SEARCH_CRITERIA)
        if status != "OK":
            raise RuntimeError(f"Gmail search failed: {status}")

        email_ids = messages[0].split()
        if not email_ids:
            print("No matching HSBC emails found.")
            return

        print(f"Found {len(email_ids)} HSBC email(s) matching the criteria.")

        # Initialize Dropbox client only when explicitly enabled via UPLOAD_TO_DROPBOX and when SDK/token are available
        if UPLOAD_TO_DROPBOX and DROPBOX_ACCESS_TOKEN and dropbox is not None:
            dbx = dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)
            print("Dropbox upload enabled — files will be uploaded to Dropbox paths under: ", DROPBOX_DEST_FOLDER)
            # Note: if your Dropbox app uses App folder access, uploads go under Dropbox/Apps/<AppName>/...
        else:
            dbx = None
            os.makedirs(LOCAL_DEST_FOLDER, exist_ok=True)
            print(f"Local-save mode active. Saving HSBC PDF statements to: {LOCAL_DEST_FOLDER}")
            if DROPBOX_ACCESS_TOKEN and not UPLOAD_TO_DROPBOX:
                print("DROPBOX_ACCESS_TOKEN is present but UPLOAD_TO_DROPBOX is false — uploads are disabled by configuration.")

        # Track the most recent processed date seen in this run
        newest_processed_dt = None

        for email_id in email_ids:
            _, msg_data = mail.fetch(email_id, "(RFC822)")
            for response_part in msg_data:
                if not isinstance(response_part, tuple):
                    continue

                msg = message_from_bytes(response_part[1])
                # Second-level validation to avoid false positives from SEARCH
                if not is_expected_email(msg):
                    write_log(f"SKIPPED -> email not matching HSBC criteria: {msg.get('Subject')}")
                    continue

                if should_skip_email(msg, processed_state):
                    write_log(f"SKIPPED -> email older than last processed date: {msg.get('Subject')}")
                    continue

                saved_any = False
                for part in msg.walk():
                    # skip multipart container blocks
                    if part.get_content_maintype() == "multipart":
                        continue
                    # require a Content-Disposition header for attachments
                    if part.get("Content-Disposition") is None:
                        continue

                    filename = part.get_filename()
                    if not filename:
                        continue

                    filename = decode_header_value(filename)
                    # Only keep PDFs
                    if not filename.lower().endswith(".pdf"):
                        write_log(f"SKIPPED -> {filename} (not a PDF attachment)")
                        continue

                    file_bytes = part.get_payload(decode=True)
                    if file_bytes is None:
                        write_log(f"SKIPPED -> {filename} (attachment content empty)")
                        continue

                    email_datetime = normalize_datetime(msg.get("Date"))
                    unique_filename = build_unique_filename(filename, email_datetime)

                    if dbx is not None:
                        # Dropbox mode: use persistent processed_keys (based on filename+date+size) to avoid duplicates
                        dropbox_target = f"{DROPBOX_DEST_FOLDER.rstrip('/')}/{unique_filename}"
                        key = f"{unique_filename}|{len(file_bytes)}"
                        if key in processed_keys:
                            write_log(f"SKIPPED -> already-processed file (by filename+date+size): {unique_filename}")
                            continue

                        # upload (or dry-run announce)
                        success = upload_to_dropbox(dbx, file_bytes, dropbox_target, dry_run=dry_run)
                        if success and not dry_run:
                            processed_keys.add(key)
                            saved_any = True
                        elif success and dry_run:
                            # in dry-run we treat as would-be saved
                            saved_any = True
                    else:
                        # Local save mode uses save_attachment_locally which records processed_keys
                        if save_attachment_locally(file_bytes, filename, LOCAL_DEST_FOLDER, processed_keys, email_date=email_datetime, dry_run=dry_run):
                            saved_any = True

                # If we saved or would have saved any file for this message, move/mark the email (unless dry-run)
                if saved_any:
                    if dry_run:
                        print(f"[dry-run] Would move email {email_id.decode()} to '{GMAIL_HOMELOANS_FOLDER}' and mark as read.")
                    else:
                        move_email_to_home_loans(mail, email_id)

                # Update newest processed date seen (use Date header when available)
                email_datetime = normalize_datetime(msg.get("Date"))
                if email_datetime:
                    if newest_processed_dt is None or email_datetime > newest_processed_dt:
                        newest_processed_dt = email_datetime

        # Persist any new processed keys and last_processed_date
        if newest_processed_dt:
            processed_state["last_processed_date"] = newest_processed_dt.isoformat()

        # Convert processed_keys set back to sorted list for deterministic state file
        processed_state["processed_keys"] = sorted(list(processed_keys))

        # Save state unless in dry-run
        if not dry_run:
            save_processed_state(processed_state)
        else:
            print("[dry-run] Processed state would be updated as follows:")
            print(json.dumps(processed_state, indent=2))

    finally:
        mail.logout()


# ---------- CLI Entrypoint ----------
def main():
    parser = argparse.ArgumentParser(description="Download HSBC PDF statements and save to Dropbox/local folder")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without saving files, moving emails, or updating state")
    args = parser.parse_args()

    process_emails(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
