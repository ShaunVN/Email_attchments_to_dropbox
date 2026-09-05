"""
Generate a Word (.docx) document with a line-by-line explanation of the main script.
This script uses python-docx to produce the file: HSBC_script_documentation.docx
"""
from docx import Document

DESCRIPTION = r"""
Email attachment automation for HSBC monthly statements.

This document contains a top-to-bottom description of the main script, its behavior, and important notes.

(Condensed for readability; full explanation follows in sections.)

1. Purpose: find Gmail messages from HSBC with subject "Your Monthly HSBC Bank Statement", extract PDF attachments, skip duplicates, save locally (to your Dropbox-sync folder) or upload to Dropbox if a token is provided, mark emails read and move them into a "HomeLoans" label/folder, and keep a checkpoint so older messages aren't reprocessed.

2. Key pieces: imaplib for Gmail, email package for parsing, dropbox SDK optional, dotenv for local .env config, processed_statements.json for checkpointing.

3. Notable new features in the updated script:
   - Persistent processed_hashes stored in processed_statements.json to avoid duplicates across runs
   - --dry-run flag to preview actions without writing files, moving emails, or updating state
   - Inline comments added throughout the file for maintainability
   - State file has two keys: last_processed_date (ISO timestamp) and processed_hashes (list of sha256 hex digests)

4. How to use:
  - Install dependencies: python -m pip install -r requirements.txt
  - Prepare .env in repo root with your credentials (do NOT commit it)
  - Run preview: python email_attachments_to_dropbox.py --dry-run
  - Run live: python email_attachments_to_dropbox.py

5. Important caveats & tips
  - Gmail may require an app-specific password and IMAP enabled
  - IMAP label handling varies; if automatic label creation fails, create the "HomeLoans" label manually in Gmail
  - The script stores processed hashes and last processed date in processed_statements.json in the repo root; keep this file private

"""

def make_doc():
    doc = Document()
    doc.add_heading('HSBC Email Attachment Automation - Script Documentation', level=1)
    doc.add_paragraph('Generated documentation for email_attachments_to_dropbox.py')
    for para in DESCRIPTION.split('\n\n'):
        doc.add_paragraph(para.strip())

    # Add a short per-section explanation for quick reference
    doc.add_heading('Quick reference: commands', level=2)
    doc.add_paragraph('Install dependencies: python -m pip install -r requirements.txt')
    doc.add_paragraph('Dry-run (preview): python email_attachments_to_dropbox.py --dry-run')
    doc.add_paragraph('Run live: python email_attachments_to_dropbox.py')

    out_path = 'HSBC_script_documentation.docx'
    doc.save(out_path)
    print(f'Created documentation: {out_path}')

if __name__ == '__main__':
    make_doc()
