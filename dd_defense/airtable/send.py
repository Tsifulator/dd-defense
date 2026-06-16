"""Send outreach emails via Gmail SMTP + update Airtable status.

Reads "Approved" prospects from Airtable, sends each via Gmail SMTP, flips
status to "Sent". Paced at 3s between sends to protect reputation.

Requires GMAIL_USER + GMAIL_APP_PASSWORD in .env.

Run:  python3 -m dd_defense.cli send-outreach [--limit 10] [--dry-run]
"""
from __future__ import annotations

import email.message
import os
import smtplib
import time

from .client import (
    TABLE_PROSPECTS,
    list_records,
    update_record,
)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _gmail_creds():
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not pw:
        raise RuntimeError("GMAIL_USER and GMAIL_APP_PASSWORD not set (add them to .env).")
    return user, pw


def send_email(to, subject, body, from_addr=None, reply_to=None):
    """Send one plain-text email via Gmail SMTP. Returns None on success."""
    user, pw = _gmail_creds()

    msg = email.message.EmailMessage()
    msg["From"] = from_addr or user
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(user, pw)
        server.send_message(msg)
    return None


def send_approved(limit=10, dry_run=False, from_addr=None, reply_to=None, pace=3.0):
    """Send outreach emails to Approved prospects. Returns summary dict."""
    records = list_records(TABLE_PROSPECTS, formula="{Status}='Approved'")
    # Sort by Fit Score descending — best leads first
    records.sort(key=lambda r: -(r.get("fields", {}).get("Fit Score") or 0))

    if limit:
        records = records[:limit]

    if not records:
        print("No Approved prospects to send.")
        return {"sent": 0, "failed": 0, "total": 0}

    print(f"{'DRY RUN — ' if dry_run else ''}Sending to {len(records)} prospect(s)...\n")

    # Verify creds before starting (fail fast)
    if not dry_run:
        _gmail_creds()

    sent = failed = 0
    for i, rec in enumerate(records):
        f = rec.get("fields", {})
        company = f.get("Company", "?")
        to = f.get("Email", "")
        subject = f.get("Draft Subject", "")
        body = f.get("Draft Email", "")

        if not to or not subject or not body:
            print(f"  ⊘ {company} — missing email/subject/body, skipped")
            continue

        if dry_run:
            print(f"  → {company} <{to}>")
            print(f"    Subject: {subject}")
            sent += 1
            continue

        try:
            send_email(to, subject, body, from_addr=from_addr, reply_to=reply_to)
            update_record(TABLE_PROSPECTS, rec["id"], {"Status": "Sent"})
            print(f"  ✓ {company} <{to}>")
            sent += 1
        except Exception as ex:
            print(f"  ✗ {company} <{to}> — {ex}")
            failed += 1

        # Pace sends to protect reputation
        if not dry_run and i < len(records) - 1:
            time.sleep(pace)

    print(f"\nDone. sent={sent} failed={failed}")
    return {"sent": sent, "failed": failed, "total": len(records)}
