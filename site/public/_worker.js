// Pages Advanced Mode: _worker.js handles /api/lead, passes everything else to static assets.

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Only intercept /api/lead — everything else falls through to static assets
    if (url.pathname === "/api/lead") {
      return handleLead(request, env);
    }

    // Pass through to static assets (Pages serves index.html etc.)
    return env.ASSETS.fetch(request);
  },
};

async function handleLead(request, env) {
  const json = (obj, status = 200) =>
    new Response(JSON.stringify(obj), {
      status,
      headers: { "content-type": "application/json", "access-control-allow-origin": "*" },
    });

  if (request.method === "GET") {
    return new Response("POST a lead here.", { status: 405 });
  }
  if (request.method !== "POST") {
    return json({ ok: false, error: "method not allowed" }, 405);
  }

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
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return json({ ok: false, error: "invalid email" }, 400);
  }

  if (!env.AIRTABLE_API_KEY || !env.AIRTABLE_BASE_ID) {
    return json({ ok: false, error: "intake not configured" }, 503);
  }

  const table = encodeURIComponent(env.AIRTABLE_LEADS_TABLE || "Leads");
  const airtableUrl = `https://api.airtable.com/v0/${env.AIRTABLE_BASE_ID}/${table}`;

  const fields = {
    Company: company,
    "Contact Name": name,
    Email: email,
    Message: message,
    Source: "dnddefense.com",
    Status: "New",
  };
  for (const k of Object.keys(fields)) {
    if (fields[k] === "" || fields[k] == null) delete fields[k];
  }

  try {
    const r = await fetch(airtableUrl, {
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
