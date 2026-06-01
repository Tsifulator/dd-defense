"""FastAPI web app: upload a D&D invoice -> audit report + draft letter in the browser.

Thin layer over the existing engine — it calls extract_from_file() and run_audit()
directly and renders with webpreview.render_report_page(). No logic is duplicated.

Design / boundaries:
  * Local-first: binds 127.0.0.1 by default. The Anthropic key stays server-side
    (loaded from .env); it is never sent to the browser.
  * Analyzes & drafts only — the disclaimer is shown on every page.
  * Stateless: each upload is processed in a temp dir that is deleted afterward.
    Nothing is persisted (no DB, no accounts) — that's the chosen v2 scope.

Run:  python3 -m dd_defense.webapp        # prints the local URL
"""
# NOTE: deliberately NO `from __future__ import annotations` here. FastAPI resolves
# endpoint type hints (UploadFile/Form), which are imported locally inside
# create_app(); stringized annotations would fail to resolve against module globals
# and FastAPI would mis-read the file field as a query param.

import json
import os
import tempfile
from typing import Optional

from .audit import run_audit
from .letter import draft_letter
from .schema import Evidence
from .webpreview import _CSS, _esc, render_dashboard, render_report_page

try:
    from .cli import _load_dotenv
except Exception:  # pragma: no cover
    def _load_dotenv(path=".env"):
        pass

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB — invoices are small; guards against abuse
ALLOWED_EXT = (".pdf", ".png", ".jpg", ".jpeg")

# Resolve sample paths relative to the project root (parent of this package).
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_PKG_DIR)
_SAMPLE_INVOICE_JSON = os.path.join(_ROOT, "samples", "sample_parsed_invoice.json")
_SAMPLE_EVIDENCE_JSON = os.path.join(_ROOT, "samples", "sample_evidence.json")

# Case database. Overridable via env (e.g. a mounted volume in deployment).
DB_PATH = os.environ.get("DD_DB_PATH", os.path.join(_ROOT, "data", "cases.db"))


# ---------------------------------------------------------------------------
# upload page
# ---------------------------------------------------------------------------

_UPLOAD_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>D&D Invoice Defense</title><style>__CSS__
.upwrap{max-width:680px;margin:0 auto;padding:30px 20px 80px}
.drop{border:2px dashed #c7ccd4;border-radius:12px;background:#fff;padding:40px 24px;text-align:center;transition:.15s}
.drop.over{border-color:#0f172a;background:#f0f4ff}
.drop p{margin:6px 0;color:#444}
.drop .hint{color:#8a8f98;font-size:13px}
.fname{margin-top:10px;font-weight:600;color:#0f172a}
label.fld{display:block;margin:20px 0 6px;font-weight:600;font-size:14px}
textarea{width:100%;min-height:90px;border:1px solid #d8dbe0;border-radius:8px;padding:10px;font:13px ui-monospace,Menlo,monospace}
details{margin-top:18px}summary{cursor:pointer;font-weight:600;font-size:14px;color:#0f172a}
.row{display:flex;gap:12px;align-items:center;margin-top:22px;flex-wrap:wrap}
button.go{background:#0f172a;color:#fff;border:0;border-radius:9px;padding:12px 22px;font:600 15px inherit;cursor:pointer}
button.go:disabled{opacity:.5;cursor:default}
a.demo{font-size:14px;color:#1f5fb3;text-decoration:none}a.demo:hover{text-decoration:underline}
.spin{display:none;align-items:center;gap:10px;color:#444;font-size:14px;margin-top:18px}
.spin.on{display:flex}
.dot{width:14px;height:14px;border:2px solid #c7ccd4;border-top-color:#0f172a;border-radius:50%;animation:s .8s linear infinite}
@keyframes s{to{transform:rotate(360deg)}}
</style></head>
<body>
<header><h1>D&amp;D Invoice Defense</h1>
  <div class="meta">Upload a demurrage &amp; detention invoice. The tool audits it against the FMC rule
   (46 CFR Part 541) and drafts a dispute letter for the importer to review and send.</div></header>
<div class="upwrap">
  <div class="disclaimer">Automated analysis for the importer's review — <b>not legal advice</b>, and it
   files no disputes. Your invoice is processed on this machine and not stored.</div>

  <form id="f" action="/audit" method="post" enctype="multipart/form-data">
    <div class="drop" id="drop">
      <p><b>Drag &amp; drop</b> an invoice here, or click to choose</p>
      <p class="hint">PDF, PNG, or JPG · scanned invoices are OK</p>
      <input type="file" id="file" name="invoice" accept=".pdf,.png,.jpg,.jpeg" style="display:none">
      <div class="fname" id="fname"></div>
    </div>

    <details>
      <summary>Add supporting evidence (optional — unlocks the substantive grounds)</summary>
      <label class="fld" for="ev">Evidence JSON</label>
      <textarea id="ev" name="evidence" placeholder='{"closures":[{"location":"...","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}], "tariff_rates":{"default":120}}'></textarea>
      <p class="hint">Closures, no-appointment dates, container availability/holds, tariff rates. Leave blank to skip.</p>
    </details>

    <div class="row">
      <button class="go" id="go" type="submit" disabled>Audit invoice</button>
      <a class="demo" href="/demo">or see it on a sample invoice →</a>
    </div>
    <div class="spin" id="spin"><span class="dot"></span> Analyzing the invoice… this takes a few seconds.</div>
  </form>
</div>
<script>
var drop=document.getElementById('drop'),inp=document.getElementById('file'),
    fname=document.getElementById('fname'),go=document.getElementById('go');
drop.onclick=function(){inp.click();};
['dragover','dragenter'].forEach(function(e){drop.addEventListener(e,function(ev){ev.preventDefault();drop.classList.add('over');});});
['dragleave','drop'].forEach(function(e){drop.addEventListener(e,function(ev){ev.preventDefault();drop.classList.remove('over');});});
drop.addEventListener('drop',function(ev){if(ev.dataTransfer.files.length){inp.files=ev.dataTransfer.files;show();}});
inp.addEventListener('change',show);
function show(){if(inp.files.length){fname.textContent='Selected: '+inp.files[0].name;go.disabled=false;}}
document.getElementById('f').addEventListener('submit',function(){
  go.disabled=true;document.getElementById('spin').classList.add('on');});
</script>
</body></html>""".replace("__CSS__", _CSS)


def _error_page(title, detail, hint=""):
    hint_html = f'<p class="hint">{_esc(hint)}</p>' if hint else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>D&D — {_esc(title)}</title>
<style>{_CSS}.upwrap{{max-width:680px;margin:0 auto;padding:30px 20px}}.hint{{color:#666;font-size:14px}}
a.back{{color:#1f5fb3}}</style></head><body>
<header><h1>D&amp;D Invoice Defense</h1></header>
<div class="upwrap">
  <div class="disclaimer" style="background:#fdecea;border-color:#f0b3ad;color:#9a2b22">
   <b>{_esc(title)}</b><br>{_esc(detail)}</div>
  {hint_html}
  <p><a class="back" href="/">← Try another invoice</a></p>
</div></body></html>"""


def _result_with_nav(report_dict, letter, saved_id=None, status=None, recovered=0):
    html = render_report_page(report_dict, letter)
    bits = ['<a href="/" style="color:#1f5fb3;text-decoration:none">← Audit another</a>',
            '<a href="/cases" style="color:#1f5fb3;text-decoration:none">View all cases →</a>']
    if saved_id:
        ref = "C-%04d" % saved_id
        tag = f"saved as {ref}"
        if status:
            tag += f" · status: {_esc(status)}"
        if recovered:
            tag += f" · recovered {recovered:,.2f}"
        bits.insert(0, f'<b>{tag}</b>')
    inject = '<p style="margin:6px 0 0;display:flex;gap:16px;flex-wrap:wrap">' + "".join(
        f'<span>{b}</span>' for b in bits) + '</p>'
    return html.replace('</div>\n\n  <nav>', '</div>' + inject + '\n\n  <nav>', 1)


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------


def create_app():
    from fastapi import FastAPI, Form, UploadFile
    from fastapi.responses import HTMLResponse, JSONResponse

    # Re-export the names the endpoint annotations reference so they resolve at
    # module scope (FastAPI inspects them by name).
    globals().update(Form=Form, UploadFile=UploadFile,
                     HTMLResponse=HTMLResponse, JSONResponse=JSONResponse)

    _load_dotenv()  # pull ANTHROPIC_API_KEY from .env if present
    app = FastAPI(title="D&D Invoice Defense", docs_url=None, redoc_url=None)

    def _run_pipeline(inv, evidence, save=False):
        report = run_audit(inv, evidence)
        letter = draft_letter(report)
        rd = report.to_dict()
        saved_id = None
        if save:
            from . import store
            conn = store.connect(DB_PATH)
            saved_id = store.create_case(conn, rd, letter=letter)
            conn.close()
        return _result_with_nav(rd, letter, saved_id)

    @app.get("/", response_class=HTMLResponse)
    def home():
        return _UPLOAD_PAGE

    @app.get("/cases", response_class=HTMLResponse)
    def cases():
        from . import store
        conn = store.connect(DB_PATH)
        rows = store.list_cases(conn)
        stats = store.portfolio_stats(conn)
        conn.close()
        cur = rows[0]["currency"] if rows else "USD"
        return render_dashboard(stats, rows, currency=cur)

    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    def case_detail(case_id: int):
        from . import store
        conn = store.connect(DB_PATH)
        c = store.get_case(conn, case_id)
        conn.close()
        if not c:
            return HTMLResponse(_error_page("No such case", f"Case {case_id} was not found."),
                                status_code=404)
        report = json.loads(c["report_json"]) if c.get("report_json") else {}
        return _result_with_nav(report, c.get("letter_text") or "", case_id, status=c["status"],
                                recovered=c.get("amount_recovered") or 0)

    @app.get("/healthz", response_class=JSONResponse)
    def healthz():
        has_key = bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())
        return {"status": "ok", "api_key_configured": has_key}

    @app.get("/demo", response_class=HTMLResponse)
    def demo():
        """Run the bundled synthetic invoice + evidence — no upload, no API call."""
        from .extract import load_parsed
        try:
            inv = load_parsed(_SAMPLE_INVOICE_JSON)
            with open(_SAMPLE_EVIDENCE_JSON, encoding="utf-8") as fh:
                evidence = Evidence.from_dict(json.load(fh))
            return _run_pipeline(inv, evidence)
        except Exception as ex:  # pragma: no cover
            return HTMLResponse(_error_page("Demo failed", str(ex)), status_code=500)

    @app.post("/audit", response_class=HTMLResponse)
    async def audit(invoice: UploadFile, evidence: Optional[str] = Form(default=None)):
        # validate extension
        name = (invoice.filename or "").lower()
        if not name.endswith(ALLOWED_EXT):
            return HTMLResponse(_error_page(
                "Unsupported file type", f"‘{invoice.filename}’ is not a PDF or image.",
                "Upload a .pdf, .png, .jpg, or .jpeg invoice."), status_code=400)

        data = await invoice.read()
        if not data:
            return HTMLResponse(_error_page("Empty file", "The uploaded file had no content."), status_code=400)
        if len(data) > MAX_UPLOAD_BYTES:
            return HTMLResponse(_error_page(
                "File too large", f"The file is {len(data)//(1024*1024)} MB; the limit is "
                f"{MAX_UPLOAD_BYTES//(1024*1024)} MB."), status_code=413)

        # parse optional evidence JSON
        ev_obj = None
        evidence = (evidence or "").strip()
        if evidence:
            try:
                ev_obj = Evidence.from_dict(json.loads(evidence))
            except (json.JSONDecodeError, TypeError, ValueError) as ex:
                return HTMLResponse(_error_page(
                    "Evidence JSON is invalid", str(ex),
                    "Fix the JSON (or clear the evidence box) and try again."), status_code=400)

        # check key before spending time
        if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
            return HTMLResponse(_error_page(
                "No API key configured",
                "Extraction needs an Anthropic API key, which the server could not find.",
                "Put ANTHROPIC_API_KEY in dd-defense/.env and restart the server."), status_code=500)

        # extract -> audit in a temp dir
        suffix = os.path.splitext(name)[1] or ".pdf"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            tmp.write(data)
            tmp.flush()
            tmp.close()
            from .extract import extract_from_file
            try:
                inv = extract_from_file(tmp.name)
            except Exception as ex:
                return HTMLResponse(_error_page(
                    "Could not read the invoice", str(ex),
                    "If this is a scanned image, make sure it is legible. You can also try a PDF."),
                    status_code=502)
            return _run_pipeline(inv, ev_obj, save=True)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    return app


def main(argv=None):
    import argparse
    import socket

    import uvicorn

    ap = argparse.ArgumentParser(prog="dd_defense.webapp", description="Run the D&D invoice web app locally.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="0 = auto-pick a free port")
    a = ap.parse_args(argv)

    port = a.port
    if not port:
        s = socket.socket()
        for p in range(8800, 8840):
            try:
                s.bind((a.host, p)); s.close(); port = p; break
            except OSError:
                continue
        else:
            port = 8800
    print(f"WEBAPP_URL=http://{a.host}:{port}/", flush=True)
    uvicorn.run(create_app(), host=a.host, port=port, log_level="warning")


# Importable ASGI app for `uvicorn dd_defense.webapp:app` and for deployment.
try:  # pragma: no cover - only if fastapi is installed
    app = create_app()
except Exception:
    app = None


if __name__ == "__main__":
    main()
