# Autonomous invoice intake — setup

Turn the tool hands-off: a customer forwards their carrier D&D invoices to an
address you control, and the pipeline audits each one and drops the case (with
the flagged $ and a draft dispute letter) into Airtable for your review.

```
customer forwards invoice  →  inbox bot reads it  →  audit engine  →  SQLite case
                                                                       →  Airtable
```

> The audit is heavy Python (vision + PDF), so this runs where Python runs — your
> Mac (or a small always-on box later) — NOT in a Cloudflare Worker. Run it on a
> schedule with `cron`/`launchd`, or by hand.

---

## Option A — Watched folder (simplest, no email)

While you operate the done-for-you service, just drop a client's invoices in a
folder and audit them all:

```bash
python3 -m dd_defense.cli ingest folder --folder ./incoming --client "AcmeForwarding" --airtable
```
- Audits every new file, saves a case, pushes to Airtable.
- Re-running is safe — already-audited files are skipped (content fingerprint).

## Option B — Email inbox (most autonomous)

### 1. Make/choose a mailbox
Use a dedicated address (e.g. a Gmail like `dnd.invoices@gmail.com`, or
`invoices@dnddefense.com`). Customers forward invoices there.

### 2. Create an APP PASSWORD (Gmail)
Gmail blocks normal-password IMAP. Make an app password:
1. Turn on 2-Step Verification (myaccount.google.com → Security).
2. Go to **myaccount.google.com/apppasswords** → create one named "D&D".
3. Copy the 16-character password.

### 3. Add to `.env`
```
DD_IMAP_HOST=imap.gmail.com
DD_IMAP_USER=dnd.invoices@gmail.com
DD_IMAP_PASSWORD=your-16-char-app-password
DD_IMAP_FOLDER=INBOX
```
(For other providers: Outlook → `outlook.office365.com`, etc.)

### 4. Run it
```bash
python3 -m dd_defense.cli ingest inbox --airtable
```
Reads unseen emails with PDF/image attachments, audits each, saves the case,
marks the email seen, and pushes to Airtable. The client is inferred from the
sender's domain (override with `--client`).

---

## Stronger disputes: evidence enrichment

The substantive (incentive-principle) grounds — closures, no-appointment days —
need facts the invoice doesn't carry. Scaffold local evidence files, fill them
from terminal notices/tariffs, and pass `--enrich`:

```bash
python3 -m dd_defense.cli evidence-scaffold          # creates evidence_data/*.json
#   edit evidence_data/closures.json, tariffs.json, ... with real data
python3 -m dd_defense.cli ingest inbox --enrich --airtable
```
`--enrich` auto-adds US federal holidays as non-operating days, plus whatever you
put in `evidence_data/`. (There's no single free public closure API, so closures
are operator-maintained from carrier/terminal notices — honest and reliable.)

---

## Make it run on a schedule (your Mac)

A simple cron entry (runs every hour):
```bash
crontab -e
# add (adjust the path):
0 * * * * cd ~/tsifulator.ai/dd-defense && /usr/bin/python3 -m dd_defense.cli ingest inbox --enrich --airtable >> ingest.log 2>&1
```
Only runs while your Mac is awake. For 24/7, move it to a small always-on host
later (the audit needs Python + the extraction libs).

---

### Boundary reminder
Ingestion + audit + drafting are automatic. **Sending the dispute letter is not** —
each case lands in Airtable for you to review and send. The tool analyzes and
drafts; it does not file disputes or give legal advice.
