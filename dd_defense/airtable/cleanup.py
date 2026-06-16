"""One-time prospect email cleanup — fix placeholders, fake phones, inconsistent sigs.

Reads all Prospects from Airtable, fixes Draft Email issues, patches them back.
Safe to re-run (idempotent — only patches records that actually changed).

Run:  python3 -m dd_defense.airtable.cleanup [--dry-run]
"""
from __future__ import annotations

import re
import sys

from .client import (
    TABLE_PROSPECTS,
    list_records,
    update_record,
)

# Fake phone patterns to strip
_FAKE_PHONES = [
    "+1-617-555-0100",
    "+1-617-555-0142",
    r"\+1-617-555-\d{4}",
]

# Bracket placeholders and how to resolve them
_BRACKET_PATTERNS = [
    # [Contact Name] / [First Name] / [Name] / [contact name] → use actual name or remove
    (r"\[(?:Contact |First )?[Nn]ame\]", "_contact_name"),
    # [your company] / [your company name] / [COMPANY] / [company] → use Company field
    (r"\[(?:your )?(?:company|company name|COMPANY)\]", "_company"),
    # [clients] → "your clients"
    (r"\[clients\]", "your clients"),
    # [port] / [Port of Houston] etc → use Location field
    (r"\[(?:port|Port of \w+)\]", "_location"),
    # [number] → remove entirely (container count is vague)
    (r"\[number\]", "your"),
    # Any remaining [bracketed text] with "your" in it → remove brackets
    (r"\[your ([^\]]+)\]", r"your \1"),
]

SENDER_NAME = "Nick"
SITE = "dnddefense.com"


def _first_name(contact_name):
    """Extract first name from a contact name, or None."""
    name = (contact_name or "").strip()
    if name:
        return name.split()[0]
    return None


def _fix_greeting(body, contact_name):
    """Fix 'Hi [Contact Name]' or 'Hi [First Name]' → 'Hi {first}' or 'Hi there'."""
    first = _first_name(contact_name)
    if first:
        body = re.sub(r"Hi \[(?:Contact |First )?[Nn]ame\]", f"Hi {first}", body)
    else:
        body = re.sub(r"Hi \[(?:Contact |First )?[Nn]ame\]", "Hi there", body)
    return body


def _fix_brackets(body, fields):
    """Replace remaining [placeholder] brackets with real data."""
    company = fields.get("Company", "your company")
    location = fields.get("Location / Port", "your port")
    contact_name = fields.get("Contact Name", "")

    for pattern, replacement in _BRACKET_PATTERNS:
        if replacement == "_contact_name":
            first = _first_name(contact_name)
            body = re.sub(pattern, first or "there", body)
        elif replacement == "_company":
            body = re.sub(pattern, company, body)
        elif replacement == "_location":
            body = re.sub(pattern, location, body)
        else:
            body = re.sub(pattern, replacement, body)

    # Catch any remaining single-word brackets like [port] [company]
    body = re.sub(r"\[(\w+)\]", r"\1", body)

    return body


def _fix_signature(body):
    """Standardize the email signature to: Nick\\ndnddefense.com"""
    # Remove fake phone patterns
    for pat in _FAKE_PHONES:
        body = re.sub(rf"\s*·?\s*{pat}", "", body)

    # Standardize various signature formats
    # "Nick Tsiflikiotis\ndnddefense.com · phone" → "Nick\ndnddefense.com"
    # "Nick\ndnddefense.com · phone" → "Nick\ndnddefense.com"
    body = re.sub(
        r"(?:Nick(?:\s+Tsiflikiotis)?)\n(?:dnddefense\.com)(?:\s*·\s*\S*)?",
        f"{SENDER_NAME}\n{SITE}",
        body,
    )

    # Also catch "[Your name]\ndnddefense.com"
    body = re.sub(
        r"\[Your name\]\n(?:dnddefense\.com)",
        f"{SENDER_NAME}\n{SITE}",
        body,
    )

    return body


def fix_draft(fields):
    """Fix a single prospect's Draft Email. Returns the fixed body, or None if no change."""
    body = fields.get("Draft Email", "")
    if not body:
        return None

    original = body
    contact_name = fields.get("Contact Name", "")

    body = _fix_greeting(body, contact_name)
    body = _fix_brackets(body, fields)
    body = _fix_signature(body)

    # Clean up double spaces / trailing whitespace
    body = re.sub(r"  +", " ", body)
    body = "\n".join(line.rstrip() for line in body.split("\n"))

    return body if body != original else None


def run(dry_run=False):
    """Fetch all prospects, fix drafts, patch back. Returns summary."""
    records = list_records(TABLE_PROSPECTS)
    print(f"Loaded {len(records)} prospects from Airtable.\n")

    fixed = skipped = unchanged = 0
    for rec in records:
        fields = rec.get("fields", {})
        company = fields.get("Company", "(unnamed)")
        new_body = fix_draft(fields)

        if new_body is None:
            unchanged += 1
            continue

        if dry_run:
            # Show what would change
            old_sig = fields.get("Draft Email", "")[-80:]
            new_sig = new_body[-80:]
            if old_sig != new_sig:
                print(f"  FIX {company}")
                print(f"      sig: ...{new_sig}")
            else:
                print(f"  FIX {company} (bracket/greeting fixes)")
            fixed += 1
        else:
            try:
                update_record(TABLE_PROSPECTS, rec["id"], {"Draft Email": new_body})
                print(f"  ✓ {company}")
                fixed += 1
            except Exception as ex:
                print(f"  ✗ {company}: {ex}")
                skipped += 1

    print(f"\nDone. fixed={fixed} unchanged={unchanged} skipped={skipped}")
    return {"fixed": fixed, "unchanged": unchanged, "skipped": skipped}


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("DRY RUN — no changes will be made.\n")
    run(dry_run=dry_run)


if __name__ == "__main__":
    main()
