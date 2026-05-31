# D&D Invoice Defense (v1)

Ingest one ocean-carrier **demurrage & detention** invoice, audit it against a
configurable FMC ruleset, flag every dispute ground, and draft a letter the
**importer** can review and send.

> This tool **analyzes and drafts**. It does not file disputes and is not legal
> advice. Nothing is sent anywhere; the importer reviews the draft and acts.

## Why it works in two layers

| Layer | What it checks | Provable from… |
|-------|----------------|----------------|
| **Facial** | Missing required elements, >30-day late issuance, math/consistency errors | the invoice alone |
| **Substantive** | Closures, no-appointment days, container availability, rate vs. tariff (FMC "incentive principle") | the invoice **+ evidence you supply** |

Facial defects are the strongest grounds — under the FMC rule a missing required
element or a late invoice can **eliminate the obligation to pay**. Substantive
grounds need records the invoice doesn't carry; when that evidence is absent the
audit returns `needs_evidence` and tells you exactly what to gather.

## Run it now (no install, no API key)

The core pipeline is **stdlib-only**. From `dd-defense/`:

```bash
# Audit the bundled synthetic invoice WITH supporting evidence:
python -m dd_defense.cli audit \
  --parsed samples/sample_parsed_invoice.json \
  --evidence samples/sample_evidence.json \
  --out out

# Or without evidence — substantive checks become "needs evidence":
python -m dd_defense.cli audit --parsed samples/sample_parsed_invoice.json --out out

# Run the tests:
python -m unittest discover -s tests
```

Outputs land in `out/`: `report.md`, `letter.md`, `report.json`, `parsed_invoice.json`.

## Audit a real invoice (needs extraction extras + key)

```bash
pip install -r requirements.txt
# put your key in dd-defense/.env  ->  ANTHROPIC_API_KEY=sk-ant-...
python -m dd_defense.cli audit --invoice path/to/invoice.pdf --evidence evidence.json
```

Extraction uses a cheap vision-capable model (`claude-haiku-4-5`) so scanned
PDFs/images work. The audit, report, and letter are pure Python — **the only LLM
calls are extraction and the optional `--polish`** of the letter.

## Web app (upload → report, in the browser)

A thin FastAPI layer over the same engine: drag-drop an invoice, get the report +
draft letter rendered in the browser. Local-first — binds `127.0.0.1`, the API key
stays server-side (loaded from `.env`), and uploads are processed in a temp file
that is deleted immediately (nothing is stored).

```bash
pip install -r requirements.txt
python -m dd_defense.webapp            # prints the local URL, e.g. http://127.0.0.1:8800/
```

- `/`        upload page (drag-drop PDF/PNG/JPG, optional evidence JSON)
- `/demo`    full report on the bundled sample — **no upload, no API call**
- `/healthz` liveness + whether a key is configured

There is also a **static preview** (no web framework) that renders the files an
audit already wrote to a directory:

```bash
python -m dd_defense.webpreview --out out_mock   # serves report.json + letter.md
```

## Project map

```
dd_defense/
  schema.py     ParsedInvoice / Evidence / Rule / Finding (Field carries provenance)
  calendars.py  date parsing + US federal holidays/weekends
  rules.py      the CONFIGURABLE ruleset  <-- edit this as regulations change
  audit.py      the engine: (invoice, evidence) -> AuditReport  (LLM-free)
  report.py     AuditReport -> Markdown
  letter.py     AuditReport -> draft dispute letter (+ optional LLM polish)
  extract.py    invoice file -> ParsedInvoice (lazy LLM/PDF deps) + JSON loader
  cli.py        `python -m dd_defense.cli audit ...`
  webapp.py     FastAPI upload->report site (thin layer over the engine)
  webpreview.py shared HTML renderer + static file-preview server
scripts/
  make_mock_invoice.py  generate a realistic mock invoice PDF (dev/testing)
samples/        synthetic defective invoice + evidence
tests/          deterministic engine tests + web-layer tests
```

## Configuring the ruleset

Everything rule-related lives in `dd_defense/rules.py`:

- **Add/remove/reword a required element** → edit the `REQUIRED_ELEMENTS` list.
- **Change wording, citation, or severity of a check** → edit its `Rule(...)`.
- **Add a new check** → write a small `check(inv, ev, ctx)` returning a
  `CheckResult` and append a `Rule` to `_EXPLICIT_RULES`.

⚠️ **Citations are best-effort** subsection labels for 46 CFR Part 541 / § 545.5
and **must be verified against the published regulation** before you rely on
them. This is the starter ruleset — refine it with your authoritative checklist.

## Status / not yet built

- Web app is **local-first and stateless**: no accounts, no database, no billing,
  no saved history (each upload is processed then discarded). Those are the next
  steps if/when demand is validated.
- Not yet deployed. The app is deploy-ready (importable `dd_defense.webapp:app`),
  but hosting (e.g. Railway, behind a password) hasn't been set up.
- Single invoice at a time; no batch.
- Substantive checks are only as good as the evidence supplied.
