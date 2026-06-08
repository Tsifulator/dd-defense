# Airtable Operations Base — setup (≈5 minutes)

This connects D&D Defense to an Airtable base that holds three tables:

| Table | What it's for |
|-------|---------------|
| **Prospects** | Outreach queue — the bot drafts a personalized email per forwarder and drops it here with status **Needs Approval**. You review → send. |
| **Leads** | Inbound free-audit requests from the `dnddefense.com` form. |
| **Cases** | A mirror of the audit/savings tracker (billed / flagged / **recovered $**). |

You do two tiny things (make a base, make a token). A script builds all the tables.

---

## Step 1 — make an empty base

1. Go to **airtable.com**, sign in.
2. Click **+ Create** → **Start from scratch** → name it **"D&D Defense"**.
3. Open it. The URL looks like `https://airtable.com/appXXXXXXXXXXXXXX/...` —
   the **`appXXXXXXXXXXXXXX`** part is your **Base ID**. Copy it.

*(You can delete the default "Table 1" later — the script makes its own tables.)*

## Step 2 — make an API token

1. Go to **airtable.com/create/tokens** (Account → Builder hub → Personal access tokens).
2. **Create token** → name it "D&D Defense".
3. **Scopes** — add these four:
   - `data.records:read`
   - `data.records:write`
   - `schema.bases:read`
   - `schema.bases:write`  *(needed so the script can create tables)*
4. **Access** → add your **"D&D Defense"** base.
5. **Create token** → copy it (starts with `pat...`). You only see it once.

## Step 3 — put both in your `.env`

Open `dd-defense/.env` and add:

```
AIRTABLE_API_KEY=pat...your token...
AIRTABLE_BASE_ID=app...your base id...
```

## Step 4 — build the tables (one command)

```bash
cd ~/tsifulator.ai/dd-defense
pip install -r requirements.txt          # if you haven't already
python3 -m dd_defense.cli airtable-setup
python3 -m dd_defense.cli airtable-ping   # should say "Airtable reachable ✓"
```

That creates Prospects / Leads / Cases with all the right fields. Done.

---

## Using it

**Queue outreach drafts** (draft-only — nothing sends):
```bash
# scrape forwarders into a CSV (see outreach/prospects-sample.csv for the columns)
python3 -m dd_defense.cli outreach my_prospects.csv --sender-name "Nick" --sender-phone "+1..."
# optional: --polish  (LLM-personalizes each draft; needs ANTHROPIC_API_KEY)
```
Then open the **Prospects** table in Airtable (phone or desktop), read each
**Draft Email**, and when happy, send it yourself and set Status → **Sent**.

**Mirror your cases/savings into Airtable:**
```bash
python3 -m dd_defense.cli airtable-sync
```

**Inbound leads from the website:** deploy `site/functions/api/lead.js` (it ships
in the `site/` folder) and set `AIRTABLE_API_KEY` + `AIRTABLE_BASE_ID` in
**Cloudflare Pages → Settings → Environment variables**. The form then writes
straight into the Leads table. Until then, the form falls back to emailing you.

---

### Why sending is manual (for now)
The bot **drafts and queues** but never sends. Auto-blasting cold email from a
fresh domain is the fastest way to get flagged as spam and can brush CAN-SPAM
rules. Review + send yourself while volume is low; we can add a proper sending
setup (dedicated warmed domain) when you're ready to scale.

*Not legal advice. Follow CAN-SPAM: only email businesses with a real reason,
include a way to opt out, and don't mislead.*
