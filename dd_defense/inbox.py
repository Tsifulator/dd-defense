"""Email inbox ingestion (stdlib only — imaplib + email).

The front door for autonomous invoice intake: a customer forwards their carrier
D&D invoices to an address you control; this reads the inbox, pulls the PDF/image
attachments, and hands them to the audit pipeline. No third-party deps.

The pure attachment-extraction (`attachments_from_bytes`) is split from the IMAP
network code so it can be unit-tested without a live mailbox.

Config (env, see INBOX_SETUP.md):
  DD_IMAP_HOST       e.g. imap.gmail.com
  DD_IMAP_USER       the mailbox address
  DD_IMAP_PASSWORD   an APP PASSWORD (not your normal password)
  DD_IMAP_FOLDER     default "INBOX"
"""
from __future__ import annotations

import email
import os
from email.header import decode_header, make_header

# attachment types we treat as invoices
_INVOICE_EXTS = (".pdf", ".png", ".jpg", ".jpeg")
_INVOICE_MIMES = ("application/pdf", "image/png", "image/jpeg", "image/jpg")


def _decode(s):
    if not s:
        return ""
    try:
        return str(make_header(decode_header(s)))
    except Exception:
        return str(s)


def _is_invoice_part(filename, content_type):
    fn = (filename or "").lower()
    ct = (content_type or "").lower()
    return fn.endswith(_INVOICE_EXTS) or ct in _INVOICE_MIMES


def attachments_from_bytes(raw_bytes):
    """Parse a raw RFC822 email (bytes) and return a list of attachment dicts:
    {filename, content_type, data(bytes)} for each PDF/image attachment. Pure —
    no network. Also returns the message's from/subject for context."""
    msg = email.message_from_bytes(raw_bytes)
    meta = {
        "from": _decode(msg.get("From")),
        "subject": _decode(msg.get("Subject")),
        "date": msg.get("Date", ""),
        "message_id": msg.get("Message-ID", ""),
    }
    out = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        filename = _decode(part.get_filename())
        ctype = part.get_content_type()
        # treat as attachment if it has a filename or is explicitly an attachment
        if not filename and "attachment" not in disp:
            if not _is_invoice_part(None, ctype):
                continue
        if not _is_invoice_part(filename, ctype):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        out.append({"filename": filename or f"attachment.{_ext_for(ctype)}",
                    "content_type": ctype, "data": payload})
    return {"meta": meta, "attachments": out}


def _ext_for(ctype):
    return {"application/pdf": "pdf", "image/png": "png",
            "image/jpeg": "jpg", "image/jpg": "jpg"}.get((ctype or "").lower(), "bin")


# ---------------------------------------------------------------------------
# IMAP fetching (network — not unit-tested; verified manually)
# ---------------------------------------------------------------------------


def _config(host=None, user=None, password=None, folder=None):
    host = host or os.environ.get("DD_IMAP_HOST")
    user = user or os.environ.get("DD_IMAP_USER")
    password = password or os.environ.get("DD_IMAP_PASSWORD")
    folder = folder or os.environ.get("DD_IMAP_FOLDER", "INBOX")
    missing = [n for n, v in (("DD_IMAP_HOST", host), ("DD_IMAP_USER", user),
                              ("DD_IMAP_PASSWORD", password)) if not v]
    if missing:
        raise RuntimeError("Inbox not configured: set " + ", ".join(missing)
                           + " (see INBOX_SETUP.md).")
    return host, user, password, folder


def fetch_invoice_emails(host=None, user=None, password=None, folder=None,
                         unseen_only=True, mark_seen=True, save_dir=None, limit=None):
    """Connect to the IMAP inbox, find emails with invoice attachments, save the
    attachments to `save_dir`, and return a list of:
      {meta, saved_paths:[...], attachments:[{filename,content_type,path}]}.
    Network function — needs a live mailbox + credentials."""
    import imaplib

    host, user, password, folder = _config(host, user, password, folder)
    save_dir = save_dir or os.path.join("inbox_attachments")
    os.makedirs(save_dir, exist_ok=True)

    results = []
    M = imaplib.IMAP4_SSL(host)
    try:
        M.login(user, password)
        M.select(folder)
        criteria = "UNSEEN" if unseen_only else "ALL"
        typ, data = M.search(None, criteria)
        if typ != "OK":
            return results
        ids = data[0].split()
        if limit:
            ids = ids[-limit:]
        for num in ids:
            # peek so we control the seen flag ourselves
            typ, msg_data = M.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            parsed = attachments_from_bytes(raw)
            if not parsed["attachments"]:
                continue
            saved = []
            for i, att in enumerate(parsed["attachments"]):
                safe = _safe_name(att["filename"], num, i)
                path = os.path.join(save_dir, safe)
                with open(path, "wb") as fh:
                    fh.write(att["data"])
                saved.append({"filename": att["filename"],
                              "content_type": att["content_type"], "path": path})
            results.append({"meta": parsed["meta"], "attachments": saved,
                            "saved_paths": [a["path"] for a in saved]})
            if mark_seen:
                M.store(num, "+FLAGS", "\\Seen")
        return results
    finally:
        try:
            M.logout()
        except Exception:
            pass


def _safe_name(filename, num, idx):
    base = "".join(c for c in (filename or "") if c.isalnum() or c in "._-") or "invoice"
    nid = num.decode() if isinstance(num, bytes) else str(num)
    return f"{nid}_{idx}_{base}"
