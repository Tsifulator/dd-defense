"""Outreach bot: prospect -> qualify -> draft personalized email -> queue in Airtable.

This is the autonomous draft pipeline (the same draft->approve->fire pattern proven
elsewhere). It does NOT send anything: every prospect lands in the Airtable Prospects
table with status "Needs Approval" for a human to review, then send. Keeping the send
manual avoids torching sender reputation and stays clear of CAN-SPAM concerns while
volume is low.

Pieces (all pure/testable except the Airtable write + optional LLM):
  fit_score(prospect)        -> 0..100 (forwarders + volume rank higher)
  draft_email(prospect)      -> {subject, body}  (templated; optional LLM polish)
  prospect_to_fields(...)    -> Airtable field dict
  queue_prospects(prospects) -> writes drafts to Airtable (status Needs Approval)

Input prospects are plain dicts (from your scrape / the prospect-tracker xlsx):
  {company, type, contact_name, title, email, phone, url, location,
   containers_per_mo, source, notes}
"""
from __future__ import annotations

import os

from .airtable import client as airtable

# ---------------------------------------------------------------------------
# qualify
# ---------------------------------------------------------------------------

_TYPE_WEIGHT = {"forwarder": 30, "broker": 20, "drayage": 25, "importer": 10, "other": 0}


def fit_score(p):
    """0..100. Forwarders and higher container volume score higher — so you work
    the best leads first. Mirrors the formula in the prospect-tracker spreadsheet."""
    vol = p.get("containers_per_mo") or 0
    try:
        vol = float(vol)
    except (TypeError, ValueError):
        vol = 0
    t = str(p.get("type", "")).strip().lower()
    base = _TYPE_WEIGHT.get(t, 0)
    return int(min(100, round(vol / 3) + base))


# ---------------------------------------------------------------------------
# draft (templated; LLM optional)
# ---------------------------------------------------------------------------

SITE = "dnddefense.com"


def _first_name(p):
    name = (p.get("contact_name") or "").strip()
    if name:
        return name.split()[0]
    return "there"


def _detect_segment(p):
    """Detect vertical segment from type + notes for hook selection."""
    notes = str(p.get("notes") or "").lower()
    ptype = str(p.get("type", "")).strip().lower()
    if ptype == "drayage":
        return "drayage"
    if ptype == "broker":
        return "broker"
    kw = notes + " " + str(p.get("company") or "").lower()
    if any(w in kw for w in ("seafood", "fish", "shrimp", "lobster", "protein", "meat")):
        return "seafood"
    if any(w in kw for w in ("produce", "perishab", "fresh", "fruit", "vegetab", "reefer")):
        return "perishable"
    if any(w in kw for w in ("pharma", "biotech", "cold chain", "medical", "vaccine", "life science")):
        return "pharma"
    if any(w in kw for w in ("amazon", "fba", "ecomm", "e-comm", "dtc", "shopify")):
        return "ecomm"
    if ptype == "forwarder":
        return "forwarder"
    if ptype == "importer":
        return "importer"
    return "general"


# Segment-specific hooks and subjects
_SEGMENT_HOOKS = {
    "perishable": {
        "subject": "your reefer D&D invoices — there's $ sitting on the table",
        "hook": ("Perishable importers get hit hardest by D&D fees — a stuck reefer container "
                 "racks up $200–$400/day, and carriers know you can't wait around to dispute it. "
                 "But since the FMC's 2024 rule, a lot of those charges are technically invalid — "
                 "missing required fields, billed late, or accrued during terminal closures."),
        "ask": (f"Can I audit a batch of {{company}}'s recent D&D invoices for free? If the "
                f"charges are clean, you've lost nothing. If they're not, I'll show you exactly "
                f"what to dispute and draft the letter."),
    },
    "seafood": {
        "subject": "your seafood D&D charges — most are disputable (free audit)",
        "hook": ("Seafood importers pay some of the steepest demurrage & detention in the game — "
                 "reefer surcharges, port congestion, customs holds on SIMP paperwork. Since the "
                 "FMC's 2024 rule, a huge chunk of those invoices are technically disputable: "
                 "missing required info, billed late, or fees that ran during closures."),
        "ask": (f"Can I run {{company}}'s last month of D&D invoices through our tool for free "
                f"and show you what's contestable? No cost, no strings — if there's nothing "
                f"there, you know your carriers are billing clean."),
    },
    "pharma": {
        "subject": "pharma D&D invoices — the FMC rule makes most disputable",
        "hook": ("Pharma and cold-chain shipments attract premium D&D rates, and carriers know "
                 "you'll pay to avoid disrupting a validated supply chain. But since the FMC's "
                 "2024 rule, invoices missing required data elements or billed more than 30 days "
                 "late can have the obligation to pay eliminated entirely."),
        "ask": (f"Can I audit {{company}}'s recent D&D invoices for free? The tool checks every "
                f"line against the FMC rule and drafts the dispute letter — you just review and "
                f"send. No cost, no commitment."),
    },
    "drayage": {
        "subject": "your per diem & chassis D&D — there's money back on the table",
        "hook": ("Drayage carriers eat D&D costs that aren't even their fault — terminal "
                 "closures, no appointments, chassis shortages. Since the FMC's 2024 rule, "
                 "carriers have to include specific data on every invoice, and a missing field "
                 "or a late bill can void the charge entirely. Most truckers pay anyway because "
                 "checking each invoice is a pain."),
        "ask": (f"Can I run {{company}}'s recent D&D and per diem invoices through our tool for "
                f"free? I'll flag everything that's disputable under the rule and draft the "
                f"letters — you just review and send."),
    },
    "ecomm": {
        "subject": "your container D&D fees — most FBA importers overpay",
        "hook": ("High-volume importers get nickeled and dimed on D&D — $150–$300/container/day "
                 "adds up fast across dozens of containers. Since the FMC's 2024 rule, a lot of "
                 "those charges are technically invalid: missing required fields, billed late, or "
                 "fees that accrued during port congestion you couldn't control."),
        "ask": (f"Can I audit {{company}}'s last month of D&D invoices for free? At your volume, "
                f"even a 20% dispute rate means real money back. No cost, no commitment."),
    },
    "broker": {
        "subject": "free D&D audit tool for your import clients",
        "hook": ("Since the FMC's 2024 rule, a lot of the demurrage & detention invoices your "
                 "clients receive are technically disputable — missing required fields, billed "
                 "late, math errors. You're already the trusted advisor on customs — adding D&D "
                 "dispute support is a natural value-add."),
        "ask": (f"Can I run a sample batch of {{company}}'s client D&D invoices for free and "
                f"show you what's contestable? If there's money there, it's a service you can "
                f"offer your importers at zero effort."),
    },
    "forwarder": {
        "subject": "found $ in your clients' D&D invoices (free check)",
        "hook": ("Since the FMC's 2024 rule, a lot of the demurrage & detention invoices your "
                 "clients get are technically disputable — missing required fields, billed late, "
                 "math errors. Most get paid anyway because checking each one by hand is tedious."),
        "ask": (f"Can I run a batch of {{company}}'s recent D&D invoices for free and show you "
                f"what's contestable? No cost, no commitment — and it's one account across all "
                f"your importers' containers."),
    },
    "importer": {
        "subject": "found $ in your carrier D&D invoices (free check)",
        "hook": ("Since the FMC's 2024 rule, a lot of demurrage & detention invoices are "
                 "technically disputable — missing required info, billed late, or simple math "
                 "errors. Most importers pay them anyway because checking each one by hand is a pain."),
        "ask": (f"Can I audit last month's D&D invoices for {{company}} for free and show you "
                f"what's contestable? No cost, no commitment — if there's nothing there, you've "
                f"lost nothing."),
    },
}


def draft_email(p, sender_name="[Your name]", sender_phone="[phone]"):
    """Return {subject, body}. Personalized by company/type/segment. Templated —
    no LLM needed (so it runs free + offline). `polish_with_llm` can refine later."""
    company = (p.get("company") or "your company").strip()
    fn = _first_name(p)
    segment = _detect_segment(p)
    s = _SEGMENT_HOOKS.get(segment, _SEGMENT_HOOKS["importer"])

    subject = s["subject"]
    hook = s["hook"]
    ask = s["ask"].format(company=company)

    body = (
        f"Hi {fn},\n\n"
        f"Quick one — {hook}\n\n"
        f"I built a tool that audits D&D invoices against the rule and drafts the dispute "
        f"letter automatically. {ask}\n\n"
        f"Worth a 15-min call?\n\n"
        f"{sender_name}\n{SITE} · {sender_phone}"
    )
    return {"subject": subject, "body": body}


def polish_with_llm(draft, prospect, model="claude-haiku-4-5", api_key=None):
    """Optional: lightly personalize the draft with the LLM (e.g. reference the
    prospect's port/commodity). Returns the input unchanged if SDK/key absent.
    Never invents facts — only rephrases using fields we already have."""
    try:
        import anthropic
    except ImportError:
        return draft
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return draft
    ctx = {k: prospect.get(k) for k in ("company", "type", "location", "notes") if prospect.get(k)}
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model, max_tokens=600,
        system=("You lightly personalize a short B2B cold email. Keep it under 130 words, keep "
                "the [bracketed] placeholders, keep the free-audit offer and the dnddefense.com "
                "reference. Use ONLY the provided context facts — do not invent anything. Return "
                "just the email body."),
        messages=[{"role": "user", "content":
                   f"Context: {ctx}\n\nEmail to personalize:\n{draft['body']}"}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    return {"subject": draft["subject"], "body": text or draft["body"]}


# ---------------------------------------------------------------------------
# Airtable shaping + queue
# ---------------------------------------------------------------------------

def prospect_to_fields(p, draft, status="Needs Approval"):
    """Map a prospect + its draft into Airtable Prospects fields."""
    type_map = {"forwarder": "Forwarder", "broker": "Broker",
                "drayage": "Drayage", "importer": "Importer", "other": "Other"}
    return {
        "Company": p.get("company"),
        "Type": type_map.get(str(p.get("type", "")).strip().lower(), "Other"),
        "Contact Name": p.get("contact_name"),
        "Title": p.get("title"),
        "Email": p.get("email"),
        "Phone": p.get("phone"),
        "LinkedIn / URL": p.get("url"),
        "Location / Port": p.get("location"),
        "Est. Containers/mo": p.get("containers_per_mo"),
        "Fit Score": fit_score(p),
        "Source": p.get("source"),
        "Status": status,
        "Draft Subject": draft["subject"],
        "Draft Email": draft["body"],
        "Notes": p.get("notes"),
    }


def _norm_company(name):
    """Normalize a company name for dedupe: lowercase, collapse whitespace, drop
    trailing punctuation. So 'Harbor FF' and 'harbor ff ' match."""
    return " ".join(str(name or "").lower().split()).strip(" .,")


def existing_company_keys(api_key=None, base_id=None):
    """Set of normalized company names already in the Airtable Prospects table."""
    recs = airtable.list_records(airtable.TABLE_PROSPECTS, api_key=api_key, base_id=base_id)
    return {_norm_company(r.get("fields", {}).get("Company")) for r in recs}


def queue_prospects(prospects, sender_name="[Your name]", sender_phone="[phone]",
                    use_llm=False, api_key=None, base_id=None, status="Needs Approval",
                    dedupe=True):
    """Draft + write each prospect to the Airtable Prospects queue. Returns a dict
    {created: [records], skipped: [company names]}. Does NOT send any email.

    With dedupe=True (default), prospects whose Company already exists in the table
    are skipped — so this is safe to re-run or schedule without creating duplicates."""
    seen = existing_company_keys(api_key=api_key, base_id=base_id) if dedupe else set()
    field_dicts, skipped = [], []
    for p in prospects:
        key = _norm_company(p.get("company"))
        if dedupe and key and key in seen:
            skipped.append(p.get("company"))
            continue
        seen.add(key)  # also dedupe within this batch
        draft = draft_email(p, sender_name=sender_name, sender_phone=sender_phone)
        if use_llm:
            draft = polish_with_llm(draft, p, api_key=api_key)
        field_dicts.append(prospect_to_fields(p, draft, status=status))
    created = airtable.create_records(
        airtable.TABLE_PROSPECTS, field_dicts, api_key=api_key, base_id=base_id) if field_dicts else []
    return {"created": created, "skipped": skipped}


def load_prospects_csv(path):
    """Load prospects from a CSV with headers matching the prospect dict keys
    (company,type,contact_name,title,email,phone,url,location,containers_per_mo,
    source,notes). Lets you scrape into a CSV then queue in one command."""
    import csv
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out.append({k.strip(): (v.strip() if isinstance(v, str) else v)
                        for k, v in row.items()})
    return out
