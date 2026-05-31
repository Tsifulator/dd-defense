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
export ANTHROPIC_API_KEY=sk-...
python -m dd_defense.cli audit --invoice path/to/invoice.pdf --evidence evidence.json
```

Extraction uses a cheap vision-capable model (`claude-haiku-4-5`) so scanned
PDFs/images work. The audit, report, and letter are pure Python — **the only LLM
calls are extraction and the optional `--polish`** of the letter.

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
samples/        synthetic defective invoice + evidence
tests/          deterministic engine tests
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

- No persistence yet (writes files). A thin SQLite layer is a natural next step.
- Single invoice at a time; no batch, auth, or UI.
- Substantive checks are only as good as the evidence supplied.
