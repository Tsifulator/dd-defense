"""Local web preview of a D&D audit (stdlib only — no Flask, no deps).

Reads the generated artifacts in an output directory (report.json + letter.md)
and serves a clean dashboard at http://127.0.0.1:<port>/. It re-reads the files
on every request, so re-running the audit and refreshing the page shows the
latest result.

Run:  python3 -m dd_defense.webpreview --out out
"""
from __future__ import annotations

import argparse
import html
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _money(v, cur="USD"):
    if v is None:
        return "n/a"
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get((cur or "USD").upper(), "")
    return f"{sym}{v:,.2f}" if sym else f"{v:,.2f} {cur}"


def _esc(s):
    return html.escape("" if s is None else str(s))


_STATUS = {
    "fail": ("DISPUTABLE", "#b3261e", "#fdecea"),
    "review": ("REVIEW", "#9a6700", "#fff8e1"),
    "needs_evidence": ("NEEDS EVIDENCE", "#1f5fb3", "#e8f0fe"),
    "pass": ("OK", "#1a7f37", "#e9f7ee"),
    "not_applicable": ("N/A", "#6b6b6b", "#f0f0f0"),
}

_SEVERITY = {
    "obligation_eliminated": "obligation to pay may be eliminated",
    "disputable": "disputable charge",
    "review": "manual review",
}

# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

_CSS = """
:root{--ink:#1a1a1a;--muted:#6b6b6b;--line:#e4e4e7;--bg:#f6f7f9;--card:#fff;}
*{box-sizing:border-box}
body{margin:0;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}
header{background:#0f172a;color:#fff;padding:22px 28px}
header h1{margin:0;font-size:19px;letter-spacing:.2px}
header .meta{margin-top:6px;color:#cbd5e1;font-size:13px}
.wrap{max-width:920px;margin:0 auto;padding:24px 20px 80px}
.disclaimer{background:#fff8e1;border:1px solid #f0e0a0;color:#7a5c00;border-radius:8px;padding:10px 14px;font-size:13px;margin:18px 0}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:18px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.card .big{font-size:26px;font-weight:700;margin:4px 0}
.card.red .big{color:#b3261e}.card.amber .big{color:#9a6700}
.card .lbl{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
.counts{display:flex;gap:18px;flex-wrap:wrap;margin:6px 0 0;font-size:14px;color:var(--muted)}
nav{position:sticky;top:0;background:rgba(246,247,249,.92);backdrop-filter:blur(6px);border-bottom:1px solid var(--line);padding:10px 0;margin:8px 0 20px;z-index:5}
nav a{color:#0f172a;text-decoration:none;font-size:13px;font-weight:600;margin-right:18px}
nav a:hover{text-decoration:underline}
h2{font-size:16px;margin:30px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.f{background:var(--card);border:1px solid var(--line);border-left-width:4px;border-radius:8px;padding:14px 16px;margin:10px 0}
.f .top{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.badge{font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;letter-spacing:.3px}
.f .title{font-weight:650}
.f .sub{color:var(--muted);font-size:12.5px;margin-top:3px}
.f .body{margin-top:8px;font-size:14px}
.f .amt{margin-left:auto;font-weight:700;color:#b3261e;font-size:14px}
.ev{margin:6px 0 0;padding-left:18px;color:#1f5fb3;font-size:13px}
.passed{color:var(--muted);font-size:13px}
pre.letter{background:#fff;border:1px solid var(--line);border-radius:10px;padding:20px 22px;white-space:pre-wrap;font:13.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;color:#222}
.copybar{display:flex;gap:10px;align-items:center;margin:0 0 8px}
button.copy{font:13px inherit;border:1px solid var(--line);background:#fff;border-radius:7px;padding:6px 12px;cursor:pointer}
button.copy:hover{background:#f0f0f2}
.foot{color:var(--muted);font-size:12px;margin-top:30px;border-top:1px solid var(--line);padding-top:14px}
.empty{color:var(--muted);font-style:italic}
"""


def _finding_card(f):
    label, ink, bg = _STATUS.get(f.get("status"), (f.get("status", "").upper(), "#444", "#eee"))
    sev = _SEVERITY.get(f.get("severity"), f.get("severity", ""))
    conts = ", ".join(_esc(c) for c in (f.get("affected_containers") or []))
    sub = f"{_esc(f.get('citation'))} · {_esc(sev)}"
    if conts:
        sub += f" · containers: {conts}"
    amt = f.get("amount_implicated") or 0
    amt_html = f'<span class="amt">~{_esc(_money(amt))}</span>' if amt else ""
    ev = f.get("evidence_needed") or []
    ev_html = ""
    if ev:
        items = "".join(f"<li>{_esc(e)}</li>" for e in ev)
        ev_html = f'<ul class="ev">{items}</ul>'
    body = _esc(f.get("dispute_ground_text"))
    body_html = f'<div class="body">{body}</div>' if body else ""
    return (
        f'<div class="f" style="border-left-color:{ink}">'
        f'<div class="top">'
        f'<span class="badge" style="color:{ink};background:{bg}">{_esc(label)}</span>'
        f'<span class="title">{_esc(f.get("title"))}</span>'
        f'{amt_html}'
        f'</div>'
        f'<div class="sub">{sub}</div>'
        f'{body_html}{ev_html}'
        f'</div>'
    )


def _section(title, items):
    if not items:
        return f"<h2>{_esc(title)}</h2><p class='empty'>None.</p>"
    return f"<h2>{_esc(title)}</h2>" + "".join(_finding_card(f) for f in items)


def render_page_from_dir(out_dir):
    rp = os.path.join(out_dir, "report.json")
    lp = os.path.join(out_dir, "letter.md")
    if not os.path.exists(rp):
        return _empty_page(out_dir)
    with open(rp, encoding="utf-8") as fh:
        r = json.load(fh)
    letter = ""
    if os.path.exists(lp):
        with open(lp, encoding="utf-8") as fh:
            letter = fh.read()
    return render_report_page(r, letter)


def render_report_page(r, letter=""):
    """Render a full audit page from an in-memory report dict + letter string.
    Shared by the static preview server and the FastAPI web app."""
    cur = r.get("currency") or "USD"
    findings = r.get("findings", [])
    fails = [f for f in findings if f.get("status") == "fail"]
    facial = [f for f in fails if f.get("layer") == "facial"]
    substantive = [f for f in fails if f.get("layer") == "substantive"]
    reviews = [f for f in findings if f.get("status") == "review"]
    needs = [f for f in findings if f.get("status") == "needs_evidence"]
    passed = [f for f in findings if f.get("status") in ("pass", "not_applicable")]

    oblig = r.get("amount_obligation_eliminated") or 0
    disp = r.get("amount_disputable") or 0

    oblig_card = ""
    if oblig:
        oblig_card = (
            f'<div class="card red"><div class="lbl">Obligation may be eliminated</div>'
            f'<div class="big">{_esc(_money(oblig, cur))}</div>'
            f'<div class="sub">full invoice — a required-element or timing defect was found</div></div>')
    else:
        oblig_card = (
            f'<div class="card"><div class="lbl">Obligation may be eliminated</div>'
            f'<div class="big">{_esc(_money(0, cur))}</div>'
            f'<div class="sub">no required-element/timing defect found</div></div>')

    passed_html = (", ".join(_esc(f.get("title")) for f in passed)) if passed else "None."

    letter_html = _esc(letter) if letter else "No letter generated."

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>D&D Audit Preview — {_esc(r.get('invoice_number'))}</title><style>{_CSS}</style></head>
<body>
<header>
  <h1>D&amp;D Invoice Defense — Audit Preview</h1>
  <div class="meta">Invoice <b>{_esc(r.get('invoice_number'))}</b> · Carrier {_esc(r.get('issuing_party'))}
   · Billed party {_esc(r.get('billed_party'))} · Total {_esc(_money(r.get('total_amount_due'), cur))}</div>
</header>
<div class="wrap">
  <div class="disclaimer">Automated analysis for the importer's review — not legal advice, and it files no disputes.
   Verify each ground and citation before relying on it.</div>

  <nav><a href="#overview">Overview</a><a href="#findings">Findings</a><a href="#letter">Draft letter</a>
   <a href="/report.json" target="_blank">Raw JSON</a></nav>

  <a id="overview"></a>
  <div class="cards">
    {oblig_card}
    <div class="card amber"><div class="lbl">Additional disputable</div>
      <div class="big">{_esc(_money(disp, cur))}</div>
      <div class="sub">arithmetic + substantive grounds (indicative; lines may overlap)</div></div>
  </div>
  <div class="counts">
    <span><b>{len(fails)}</b> disputable</span>
    <span><b>{len(reviews)}</b> to review</span>
    <span><b>{r.get('needs_evidence_count', len(needs))}</b> pending evidence</span>
    <span><b>{len(passed)}</b> checks passed</span>
  </div>

  <a id="findings"></a>
  {_section("Facial defects — strongest grounds (provable from the invoice)", facial)}
  {_section("Substantive grounds (incentive principle)", substantive)}
  {_section("To review (low confidence or possible duplicates)", reviews)}
  {_section("Pending evidence", needs)}
  <h2>Checks passed</h2><p class="passed">{passed_html}</p>

  <a id="letter"></a>
  <h2>Draft dispute letter (for the importer to review &amp; send)</h2>
  <div class="copybar"><button class="copy" onclick="copyLetter()">Copy letter</button>
    <span class="passed">You are the sender — complete the [BRACKETED] fields and verify before sending.</span></div>
  <pre class="letter" id="letter">{letter_html}</pre>

  <div class="foot">{_esc(r.get('note',''))}</div>
</div>
<script>
function copyLetter(){{
  var t=document.getElementById('letter').innerText;
  navigator.clipboard.writeText(t).then(function(){{
    var b=document.querySelector('button.copy');b.textContent='Copied ✓';
    setTimeout(function(){{b.textContent='Copy letter';}},1500);}});
}}
</script>
</body></html>"""


def _empty_page(out_dir):
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>D&D Audit Preview</title>
<style>{_CSS}</style></head><body><header><h1>D&amp;D Invoice Defense — Audit Preview</h1></header>
<div class="wrap"><div class="disclaimer">No audit found in <code>{_esc(out_dir)}</code> yet.</div>
<p>Run an audit first, for example:</p>
<pre class="letter">python3 -m dd_defense.cli audit \\
  --parsed samples/sample_parsed_invoice.json \\
  --evidence samples/sample_evidence.json --out {_esc(out_dir)}</pre>
<p>then refresh this page.</p></div></body></html>"""


# ---------------------------------------------------------------------------
# savings dashboard (case portfolio) — shared renderer
# ---------------------------------------------------------------------------

_STATUS_PILL = {
    "drafted": "#6b6b6b", "sent": "#1f5fb3", "responded": "#9a6700",
    "resolved": "#1a7f37", "rejected": "#b3261e", "withdrawn": "#6b6b6b",
}


def render_dashboard(stats, cases, currency="USD"):
    """Render the portfolio savings dashboard from store.portfolio_stats() output
    and a list of case rows (dicts). Pure function — no DB access here."""
    def m(v):
        return _money(v, currency)

    rows = []
    for c in cases:
        ref = "C-%04d" % c["id"]
        color = _STATUS_PILL.get(c["status"], "#444")
        rec = c.get("amount_recovered") or 0
        rec_html = f'<b style="color:#1a7f37">{m(rec)}</b>' if rec else m(rec)
        rows.append(
            f'<tr>'
            f'<td><a href="/cases/{c["id"]}">{ref}</a></td>'
            f'<td><span class="pill" style="background:{color}">{_esc(c["status"])}</span></td>'
            f'<td>{_esc(c.get("invoice_number"))}</td>'
            f'<td>{_esc(c.get("carrier"))}</td>'
            f'<td class="num">{m(c.get("amount_billed"))}</td>'
            f'<td class="num">{m(c.get("amount_flagged"))}</td>'
            f'<td class="num">{rec_html}</td>'
            f'</tr>'
        )
    table = "".join(rows) or '<tr><td colspan="7" class="empty">No cases yet.</td></tr>'

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Savings dashboard — D&D Invoice Defense</title><style>{_CSS}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-top:14px}}
th,td{{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line);font-size:14px}}
th{{background:#f0f1f3;font-size:12px;text-transform:uppercase;letter-spacing:.4px;color:#555}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
.pill{{color:#fff;font-size:11px;font-weight:700;padding:2px 9px;border-radius:999px}}
td a{{color:#0f172a;font-weight:600;text-decoration:none}}td a:hover{{text-decoration:underline}}
</style></head><body>
<header><h1>D&amp;D Invoice Defense — Savings dashboard</h1>
  <div class="meta">Tracked dispute cases and what carriers actually waived/credited.</div></header>
<div class="wrap">
  <nav><a href="/">Upload</a><a href="/cases">Cases</a><a href="/demo">Demo</a>
   <a href="/logout" style="float:right;color:#888">Sign out</a></nav>
  <div class="cards" style="grid-template-columns:repeat(3,1fr)">
    <div class="card" style="border-left:4px solid #1a7f37">
      <div class="lbl">Total recovered</div>
      <div class="big" style="color:#1a7f37">{m(stats['total_recovered'])}</div>
      <div class="sub">carrier waived / credited across {stats['closed_cases']} closed case(s)</div></div>
    <div class="card amber"><div class="lbl">Open pipeline (flagged)</div>
      <div class="big">{m(stats['open_flagged_pipeline'])}</div>
      <div class="sub">in play on {stats['open_cases']} open case(s)</div></div>
    <div class="card"><div class="lbl">Recovery rate</div>
      <div class="big">{stats['recovery_rate']*100:.0f}%</div>
      <div class="sub">recovered ÷ flagged on closed cases</div></div>
  </div>
  <div class="counts">
    <span><b>{stats['total_cases']}</b> cases</span>
    <span>total billed <b>{m(stats['total_billed'])}</b></span>
    <span>total flagged <b>{m(stats['total_flagged'])}</b></span>
    <span>est. fee @ {stats['fee_rate']*100:.0f}% <b>{m(stats['estimated_fee'])}</b></span>
  </div>
  <table>
    <thead><tr><th>Case</th><th>Status</th><th>Invoice</th><th>Carrier</th>
      <th class="num">Billed</th><th class="num">Flagged</th><th class="num">Recovered</th></tr></thead>
    <tbody>{table}</tbody>
  </table>
  <div class="foot">“Recovered” is what the carrier actually waived or credited — your proof of savings.
   Automated analysis for the importer's review; not legal advice.</div>
</div></body></html>"""


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------


def make_handler(out_dir):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, ctype="text/html; charset=utf-8", code=200):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                try:
                    self._send(render_page_from_dir(out_dir))
                except Exception as ex:  # never crash the preview
                    self._send(f"<pre>preview error: {_esc(ex)}</pre>", code=500)
            elif path == "/report.json":
                rp = os.path.join(out_dir, "report.json")
                if os.path.exists(rp):
                    with open(rp, "rb") as fh:
                        self._send(fh.read(), "application/json; charset=utf-8")
                else:
                    self._send("{}", "application/json; charset=utf-8", 404)
            elif path == "/healthz":
                self._send("ok", "text/plain; charset=utf-8")
            else:
                self._send("<h1>404</h1>", code=404)

        def log_message(self, *a):  # quiet
            pass

    return Handler


def _free_port(host, start=8765, end=8800):
    for port in range(start, end):
        s = socket.socket()
        try:
            s.bind((host, port))
            s.close()
            return port
        except OSError:
            s.close()
            continue
    return start


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dd_defense.webpreview", description="Serve a local web preview of a D&D audit.")
    ap.add_argument("--out", default="out", help="output directory holding report.json + letter.md")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="0 = auto-pick a free port")
    a = ap.parse_args(argv)

    out_dir = os.path.abspath(a.out)
    port = a.port or _free_port(a.host)
    httpd = ThreadingHTTPServer((a.host, port), make_handler(out_dir))
    url = f"http://{a.host}:{port}/"
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, ".preview_url"), "w") as fh:
            fh.write(url)
    except OSError:
        pass
    print("PREVIEW_URL=" + url, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
