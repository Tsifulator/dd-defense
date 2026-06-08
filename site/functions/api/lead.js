// Cloudflare Pages Function: POST /api/lead
// Receives the landing-page "free audit" form and writes the lead into Airtable.
// The Airtable token lives as a server-side env var (NEVER in the browser).
//
// Set these in Cloudflare Pages > Settings > Environment variables (Production):
//   AIRTABLE_API_KEY   personal access token (scope: data.records:write on the base)
//   AIRTABLE_BASE_ID   appXXXXXXXXXXXXXX
//   AIRTABLE_LEADS_TABLE   (optional) defaults to "Leads"
//
// The form posts JSON {name, company, email, message}. We respond with JSON so the
// page can show a thank-you without leaving.

export async function onRequestPost(context) {
  const { request, env } = context;

  const json = (obj, status = 200) =>
    new Response(JSON.stringify(obj), {
      status,
      headers: { "content-type": "application/json", "access-control-allow-origin": "*" },
    });

  let data;
  try {
    const ct = request.headers.get("content-type") || "";
    data = ct.includes("application/json")
      ? await request.json()
      : Object.fromEntries((await request.formData()).entries());
  } catch {
    return json({ ok: false, error: "bad request" }, 400);
  }

  const name = (data.name || "").toString().trim();
  const company = (data.company || "").toString().trim();
  const email = (data.email || "").toString().trim();
  const message = (data.message || "").toString().trim();

  if (!email || !company) {
    return json({ ok: false, error: "company and email are required" }, 400);
  }
  // light email sanity check
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return json({ ok: false, error: "invalid email" }, 400);
  }

  if (!env.AIRTABLE_API_KEY || !env.AIRTABLE_BASE_ID) {
    // Not configured yet — don't lose the lead; report so the page can fall back to email.
    return json({ ok: false, error: "intake not configured" }, 503);
  }

  const table = encodeURIComponent(env.AIRTABLE_LEADS_TABLE || "Leads");
  const url = `https://api.airtable.com/v0/${env.AIRTABLE_BASE_ID}/${table}`;

  const fields = {
    Company: company,
    "Contact Name": name,
    Email: email,
    "Monthly Invoices": "",
    Message: message,
    Source: "dnddefense.com",
    Status: "New",
  };
  // drop empty values (Airtable can reject some empties)
  for (const k of Object.keys(fields)) {
    if (fields[k] === "" || fields[k] == null) delete fields[k];
  }

  try {
    const r = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.AIRTABLE_API_KEY}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({ records: [{ fields }], typecast: true }),
    });
    if (!r.ok) {
      const detail = await r.text();
      return json({ ok: false, error: `airtable ${r.status}`, detail: detail.slice(0, 300) }, 502);
    }
    return json({ ok: true });
  } catch (e) {
    return json({ ok: false, error: "network" }, 502);
  }
}

// Friendly response for accidental GETs
export async function onRequestGet() {
  return new Response("POST a lead here.", { status: 405 });
}
