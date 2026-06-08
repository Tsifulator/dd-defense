# D&D scheduled agent (Cloudflare Worker)

Runs on a **cron schedule** in Cloudflare — no laptop needed — alongside your
other Workers (kuroshio, brands-box, etc.). It is **draft-only**: it never sends
email. Each run it reads the Airtable **Prospects** table, writes a personalized
draft for any row that has a Company but no draft yet (so hand-added rows become
review-ready), and emits a queue-health summary.

## What it does NOT do
- **Never sends email** — you approve + send from Airtable.
- **Doesn't scrape the web** — Cloudflare Workers can't browse. Feed prospects by
  running the Python CLI (`python -m dd_defense.cli outreach file.csv`) or by
  adding rows in Airtable; this agent then drafts them on schedule.

## Deploy (≈3 min)

```bash
cd agent-worker
npm install
npx wrangler login                       # if not already
npx wrangler secret put AIRTABLE_API_KEY # paste your Airtable token (stays server-side)
npx wrangler deploy
```

`AIRTABLE_BASE_ID` and the schedule are already in `wrangler.jsonc`
(`appSMyhfk2MbaTjhL`, every Monday 14:00 UTC). Change the cron there — see
crontab.guru. Optional: add a `NOTIFY_WEBHOOK` var (Slack/Discord) to get the
run summary pushed to you.

## Test it without waiting for the cron

After deploy, just visit the Worker's URL (or run `npx wrangler dev` and open it)
— a GET runs the agent once and prints the summary. You can also trigger the cron
manually from the Cloudflare dashboard (Workers → dnd-agent → Triggers).

## Two agents, one system
- **Python CLI** (`dd_defense`) — the heavy lifting: invoice audit, batch, the
  rich outreach drafting, case sync. Run on demand from your Mac.
- **This Worker** — the always-on light agent: keeps the Airtable queue drafted
  and reports health, on a schedule, without your laptop.

Both write to the same Airtable base, so your queue is one source of truth.
