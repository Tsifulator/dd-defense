// D&D Defense — scheduled agent (Cloudflare Worker, cron-triggered)
//
// Runs on its own (no laptop needed) on a schedule. It is DRAFT-ONLY and
// read-mostly: it never sends email. Each run it:
//   1. Reads the Airtable Prospects table.
//   2. Computes a "queue health" snapshot (how many need approval, drafted, sent…).
//   3. For any prospect rows that were added with a Company but NO draft yet
//      (e.g. imported by hand), it writes a personalized draft + sets status
//      "Needs Approval" — so your queue is always review-ready.
//
// What it deliberately does NOT do:
//   - It does not SEND anything (you approve + send from Airtable).
//   - It does not scrape the web (Workers can't browse; feed prospects via the
//     CLI `outreach` command or by adding rows to Airtable, then this drafts them).
//
// Secrets (set via `wrangler secret put`):  AIRTABLE_API_KEY
// Vars (wrangler.jsonc):                    AIRTABLE_BASE_ID, PROSPECTS_TABLE
// Optional: NOTIFY_WEBHOOK (a Slack/Discord webhook) for the digest.

const AT = "https://api.airtable.com/v0";

async function airtableList(env, table) {
  const out = [];
  let offset;
  do {
    const url = new URL(`${AT}/${env.AIRTABLE_BASE_ID}/${encodeURIComponent(table)}`);
    url.searchParams.set("pageSize", "100");
    if (offset) url.searchParams.set("offset", offset);
    const r = await fetch(url, { headers: { Authorization: `Bearer ${env.AIRTABLE_API_KEY}` } });
    if (!r.ok) throw new Error(`Airtable list ${table}: ${r.status} ${await r.text()}`);
    const j = await r.json();
    out.push(...j.records);
    offset = j.offset;
  } while (offset);
  return out;
}

async function airtableUpdate(env, table, id, fields) {
  const url = `${AT}/${env.AIRTABLE_BASE_ID}/${encodeURIComponent(table)}/${id}`;
  const r = await fetch(url, {
    method: "PATCH",
    headers: { Authorization: `Bearer ${env.AIRTABLE_API_KEY}`, "content-type": "application/json" },
    body: JSON.stringify({ fields, typecast: true }),
  });
  if (!r.ok) throw new Error(`Airtable update ${table}/${id}: ${r.status} ${await r.text()}`);
  return r.json();
}

// ── draft logic (mirrors dd_defense/outreach.py, kept intentionally simple) ──
const TYPE_WEIGHT = { Forwarder: 30, Broker: 20, Importer: 10, Other: 0 };

function fitScore(f) {
  const vol = Number(f["Est. Containers/mo"] || 0) || 0;
  const base = TYPE_WEIGHT[f["Type"]] ?? 0;
  return Math.min(100, Math.round(vol / 3) + base);
}

function firstName(f) {
  const n = (f["Contact Name"] || "").trim();
  return n ? n.split(/\s+/)[0] : "there";
}

function draftEmail(f) {
  const company = (f["Company"] || "your company").trim();
  const fn = firstName(f);
  const isImporter = f["Type"] === "Importer";
  const hook = isImporter
    ? "Since the FMC's 2024 rule, a lot of demurrage & detention invoices are technically disputable — missing required info, billed late, or simple math errors. Most importers pay them anyway because checking each one by hand is a pain."
    : "Since the FMC's 2024 rule, a lot of the demurrage & detention invoices your clients get are technically disputable — missing required fields, billed late, math errors. Most get paid anyway because checking each one by hand is tedious.";
  const ask = isImporter
    ? `Can I audit last month's D&D invoices for ${company} for free and show you what's contestable? No cost, no commitment.`
    : `Can I run a batch of ${company}'s recent D&D invoices for free and show you what's contestable? No cost, no commitment — one account across all your importers' containers.`;
  return {
    subject: "found $ in your carrier D&D invoices (free check)",
    body: `Hi ${fn},\n\nQuick one — ${hook}\n\nI built a tool that audits D&D invoices against the rule and drafts the dispute letter automatically. ${ask}\n\nWorth a 15-min call?\n\n[Your name]\ndnddefense.com · [phone]`,
  };
}

async function notify(env, text) {
  if (!env.NOTIFY_WEBHOOK) return;
  try {
    await fetch(env.NOTIFY_WEBHOOK, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch (_) { /* best-effort */ }
}

async function runAgent(env) {
  const table = env.PROSPECTS_TABLE || "Prospects";
  const recs = await airtableList(env, table);

  // draft any rows that have a Company but no Draft Email yet
  let drafted = 0;
  for (const rec of recs) {
    const f = rec.fields || {};
    if (f["Company"] && !f["Draft Email"]) {
      const d = draftEmail(f);
      await airtableUpdate(env, table, rec.id, {
        "Draft Subject": d.subject,
        "Draft Email": d.body,
        "Fit Score": fitScore(f),
        "Status": f["Status"] || "Needs Approval",
      });
      drafted++;
    }
  }

  // queue-health snapshot
  const by = {};
  for (const r of recs) {
    const s = (r.fields || {})["Status"] || "(none)";
    by[s] = (by[s] || 0) + 1;
  }
  const needsApproval = by["Needs Approval"] || 0;
  const summary =
    `D&D agent run: ${recs.length} prospects · ${needsApproval} need approval · ` +
    `${drafted} newly drafted · breakdown ${JSON.stringify(by)}`;
  await notify(env, summary);
  return summary;
}

export default {
  // cron trigger
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runAgent(env).catch((e) => notify(env, `D&D agent ERROR: ${e.message}`)));
  },
  // manual trigger for testing: GET / runs it once and shows the summary
  async fetch(request, env) {
    if (!env.AIRTABLE_API_KEY || !env.AIRTABLE_BASE_ID) {
      return new Response("Not configured: set AIRTABLE_API_KEY (secret) + AIRTABLE_BASE_ID (var).", { status: 503 });
    }
    try {
      const summary = await runAgent(env);
      return new Response(summary + "\n", { headers: { "content-type": "text/plain" } });
    } catch (e) {
      return new Response("error: " + e.message, { status: 500 });
    }
  },
};
