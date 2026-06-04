# D&D Defense — Agent Brief

> Read this first. It is a self-contained description of the project for an AI agent
> (or a new developer) picking it up cold. No prior context is assumed.

---

## 1. What this is (one paragraph)

**D&D Defense** is a tool that helps importers and freight forwarders stop overpaying
ocean-carrier **demurrage & detention (D&D)** fees. It ingests a D&D invoice (PDF or
image), extracts the structured data, audits it against a configurable ruleset based on
the U.S. FMC rule (46 CFR Part 541 + the § 545.5 incentive principle), flags every
charge that appears disputable, and drafts a dispute letter the customer can review and
send. It also tracks each dispute as a "case" to its outcome, so the operator can prove
how much money was actually recovered.

## 2. Why this exists (the domain in 5 facts)

1. Ocean carriers bill **demurrage** (container sits at the terminal too long) and
   **detention** (container kept out too long) at **$75–$300+/container/day** after a
   "free time" window expires.
2. Since the **FMC Final Rule (46 CFR Part 541, eff. May 28 2024)**, a D&D invoice must
   contain specific required data elements. If a required element is **missing**, or the
   invoice is **issued >30 days** after the charge was last incurred, the billed party's
   **obligation to pay can be eliminated**.
3. Many charges are also substantively improper under the FMC **"incentive principle"
   (46 CFR § 545.5)** — e.g. fees that accrued while the container *couldn't* be moved
   (terminal closures, no appointments, customs holds).
4. Small importers pay these by default because manually checking each invoice against
   the rule is tedious. **That tedium is the automation opportunity.**
5. **The law moves.** Example: in 2025 a court **vacated § 541.4** (which party may be
   billed); it was removed from the CFR. The rest of Part 541 stands. An agent working
   here must treat citations as *maintained config*, not frozen knowledge — a model's
   training data may be out of date.

## 3. CRITICAL CONSTRAINTS — do not violate these

These are non-negotiable. They keep the product legal and trustworthy.

- **The tool ANALYZES and DRAFTS. It does not FILE disputes or give legal advice.** The
  customer reviews the draft and sends it themselves. Never write copy or features that
  imply "we will dispute on your behalf" or "we represent you."
- **Human-in-the-loop, always.** Nothing is sent anywhere automatically. Every output is
  a draft + report for a human to approve and act on.
- **Never hallucinate invoice data.** Distinguish "missing from the invoice" (a real
  dispute ground) from "the extractor was unsure" (a human-review flag). Conflating them
  manufactures false grounds and destroys credibility. This distinction is enforced by
  the schema (see §6) and must be preserved.
- **Every customer-facing output carries the disclaimer:** *"Automated analysis for the
  importer's review. Not legal advice; the importer files any dispute. Verify each ground
  and citation before relying on it."*
- **The ruleset is CONFIGURABLE DATA, not hardcoded logic** — so it can be updated as
  regulations change and extended to non-US rules later.

## 4. The core mental model: TWO LAYERS

This is the single most important design idea. Dispute grounds come in two layers, and a
single invoice file can only *prove* one of them:

| Layer | What it checks | Provable from | Severity |
|-------|----------------|---------------|----------|
| **Facial** | Missing required elements; >30-day late issuance; arithmetic/consistency errors; container check-digit | the invoice **alone** | strongest — can *eliminate the obligation to pay* |
| **Substantive** | Closures, no-appointment days, container availability/holds, rate-vs-tariff (the incentive principle) | the invoice **+ external evidence the importer supplies** | disputable; bigger $ but needs proof |

When substantive evidence is absent, the audit returns `needs_evidence` and lists exactly
what to gather — it never guesses.

## 5. Architecture & data flow

The biggest cost/accuracy lever: **do NOT use an LLM where Python suffices.** ~90% of the
audit is deterministic. The only LLM calls are extraction (one cheap vision call) and an
optional letter polish.

```
invoice file (PDF/PNG/JPG)
  └─ extract.py  → LLM vision (claude-haiku) → ParsedInvoice (JSON, with provenance)
       └─ audit.py  → run ruleset (rules.py, pure Python) → AuditReport
            ├─ report.py   → Markdown / HTML report
            ├─ letter.py   → draft dispute letter (templated; optional LLM polish)
            ├─ pdfout.py   → branded PDF of letter + report
            └─ store.py    → save as a tracked case (SQLite)
```

## 6. The data schema (the non-obvious part)

Every extracted invoice datum is a **`Field`** carrying provenance:

```python
Field(value, present_on_invoice: bool, confidence: float, source_text: str)
```

- `present_on_invoice == False` → element is **MISSING** → a real dispute ground.
- `present_on_invoice == True` but low `confidence` → extractor **UNSURE** → human-verify
  flag, **NOT** a dispute ground.

The audit reads `present_on_invoice` for compliance and `confidence` only for advisory
"please verify" notes. **Never collapse these two.**

Key dataclasses (in `dd_defense/schema.py`):
- `ParsedInvoice` — all the invoice fields (each a `Field`) + `line_items[]`
- `LineItem` — container, charge_type, start/end date, days, rate, line_total
- `Evidence` — the optional sidecar for Layer-2 checks (closures, no_appointment_dates,
  containers[], government_holds[], tariff_rates{}, free_time_tolls_holidays)
- `Rule` — id, title, citation, layer, category, severity, dispute_ground template, check()
- `Finding` — one rule's result: status (pass|fail|review|needs_evidence|not_applicable),
  affected_containers, dispute_ground_text, evidence_needed, amount_implicated
- `AuditReport` — the whole result + summary money figures

## 7. The ruleset (`dd_defense/rules.py` — the file you edit most)

Two editable surfaces:
1. **`REQUIRED_ELEMENTS`** — the list of FMC-required invoice elements (§ 541.6). Each
   entry auto-generates a presence check. Add/remove/reword here.
2. **`_EXPLICIT_RULES`** — checks with real logic.

Current rules:
- **Facial:** `REQ_*` (one per required element, § 541.6), `TIMING_30_DAY` (§ 541.7),
  `MATH_LINE` (days×rate≠total), `MATH_TOTAL` (lines≠total), `CHARGE_DURING_FREE_TIME`,
  `DUPLICATE_LINES`, `CONTAINER_CHECK_DIGIT` (ISO 6346).
- **Substantive (§ 545.5, need evidence):** `HOLIDAY_WEEKEND`, `CLOSURE`,
  `NO_APPOINTMENT`, `INCENTIVE_NO_FAULT` (availability/holds), `RATE_VS_TARIFF`.

⚠️ **Citations are a maintained starter** drafted from the published rule. They must be
verified by counsel before commercial reliance. Severity `obligation_eliminated` means a
single failure can void the whole invoice.

## 8. Repo map

```
dd_defense/
  schema.py      ParsedInvoice / Field / Evidence / Rule / Finding / AuditReport
  util.py        parse_date, to_float, fmt_money, date_range (stdlib helpers)
  calendars.py   US federal holidays + weekend logic
  rules.py       THE CONFIGURABLE RULESET  ← edit as regulations change
  audit.py       run_audit(invoice, evidence) -> AuditReport  (LLM-free, deterministic)
  validators.py  ISO 6346 container check-digit (catches extractor misreads)
  report.py      AuditReport -> Markdown
  letter.py      AuditReport -> draft dispute letter (+ optional LLM polish)
  pdfout.py      branded PDF export of letter + report (reportlab, lazy import)
  extract.py     invoice file -> ParsedInvoice (LLM vision; magic-byte validation; retries)
  store.py       SQLite case + savings tracker (lifecycle, portfolio rollup, CSV export)
  batch.py       process a folder of invoices + triage (foundation of a future agent)
  webapp.py      FastAPI: upload→report site, /cases dashboard, auth gate, PDF downloads
  webpreview.py  shared HTML renderers + a stdlib static-preview server
  cli.py         `python -m dd_defense.cli <command>`
samples/         synthetic + real-layout test invoices (real customer invoices are gitignored)
tests/           deterministic test suite (no network) — 81 tests, all green
docs/            cash-flow model, GTM playbook, legal review packet
site/            static landing page for dnddefense.com
```

Third-party deps (`anthropic`, `pypdf`, `pypdfium2`, `pillow`, `fastapi`, `uvicorn`,
`reportlab`) are **lazily imported** so the core audit/report/letter/store path runs on
the Python **stdlib alone**. Extraction + web + PDF need the extras (`requirements.txt`).

## 9. Commands

CLI (`python -m dd_defense.cli ...`):
- `audit --invoice file.pdf [--evidence ev.json] [--pdf] [--save] [--client NAME]` — audit one invoice (LLM)
- `audit --parsed inv.json ...` — audit an already-parsed invoice (no LLM; for dev/tests)
- `batch <folder> [--client NAME] [--db path]` — audit every invoice in a folder + triage summary
- `cases [--client NAME]` — portfolio: total billed / flagged / **recovered**, recovery rate
- `case <id>` — one case detail + event history
- `status <id> <new_status>` — move a case (drafted→sent→responded→resolved/rejected/withdrawn)
- `recover <id> <amount>` — record what the carrier actually waived/credited (closes the case)
- `export [--client NAME] [--out file.csv]` — CSV ledger

Web app (`python -m dd_defense.webapp`): `/` upload, `/audit`, `/cases` dashboard,
`/cases/{id}`, `/cases/{id}/letter.pdf`, `/cases.csv`, `/demo` (no API call), `/healthz`,
`/login` + `/logout` (gate active only when `DD_APP_PASSWORD` is set).

## 10. The case + savings tracker (why it matters)

`store.py` (SQLite) turns audits into tracked **cases**. The three money columns:
- `amount_billed` — what the carrier charged
- `amount_flagged` — what the tool flagged as in play (the estimate)
- `amount_recovered` — what the carrier **actually** waived/credited ← **the proof of value**

`portfolio_stats()` rolls these up (total recovered, recovery rate, estimated fee). This
ledger is the asset: it's how you prove savings to the next customer. `client` is a tag
for organizing one operator's book — **it is NOT access control / data isolation.**

## 11. Batch + triage (`batch.py`)

`process_folder()` runs many invoices and **triages** each:
- `auto_clear` — clean extraction, high confidence, no integrity flags
- `needs_review` — low confidence, container check-digit failure, high $ value, or no
  grounds found (verify extraction worked)

Triage decides what to **review**, never what to **send** (human-in-the-loop preserved).
This is deliberately the foundation of a future autonomous agent (find→audit→draft→approve→send).

## 12. Config (env vars, all optional; see `.env.example`)

- `ANTHROPIC_API_KEY` — required for extraction + letter polish
- `DD_APP_PASSWORD` — if set, the web app requires login (unset = open, local-dev default)
- `DD_SECRET_KEY` — signs session cookies (set in any real deployment)
- `DD_COOKIE_SECURE` — set to 1 when served over HTTPS
- `DD_DB_PATH` — case database location (default `data/cases.db`)
- `DD_RATE_LIMIT_MAX` / `DD_RATE_LIMIT_WINDOW` — per-IP limit on the paid `/audit` endpoint

`.env` is gitignored. **Never commit secrets. Never print the API key.**

## 13. What is built vs NOT built

**Built & verified:** the full audit engine; LLM extraction (incl. scanned-image path);
report + letter + PDF; SQLite case/savings tracker; batch+triage; FastAPI web app with
password auth + rate limiting; ISO 6346 validation; static landing page; 81 tests green.

**Deliberately NOT built (do not build without a real reason / customer pull):**
- Multi-tenant SaaS: per-user accounts, hard data isolation, signup, Stripe billing.
  (Today it's single-operator: ONE shared password; everyone logged in sees all cases.)
- The autonomous agent (batch is its foundation; wrap with inbox-watch + scheduler later).
- B/L verification, multi-page/odd-layout extraction tuning, per-customer cost tracking.
- Deployment: app is deploy-ready (`dd_defense.webapp:app`) but not hosted yet.

**Known limitations:** extraction can misread identifiers (the check-digit catches
containers; B/L is not yet guarded). The ruleset is not lawyer-blessed. No real *filled*
customer invoice has been processed yet — the highest-value validation is a real one.

## 14. Working norms (how to behave in this repo)

- **Test discipline:** keep the suite green. Run `python -m unittest discover -s tests`
  before committing. Add tests for new logic. Never commit a red suite.
- **Verify by exit codes / tests, not by eye.** Don't claim something works without running it.
- **Secrets:** scan diffs for `sk-ant-` before committing. `.env`, real invoices (`samples/*.pdf|png|jpg`), `out*/`, `data/*.db` are gitignored — keep it that way.
- **Commit style:** one logical change per commit; clear message; the repo is pushed to a
  private GitHub remote after each phase.
- **The strategic truth:** the code is NOT the moat — a competent dev could rebuild it.
  The defensibility is distribution (forwarder relationships), the accumulating outcome
  dataset, regulatory currency, and trust/brand. Bias toward what compounds those, not
  toward gold-plating the code. When unsure whether to build more, the answer is usually
  "a real customer matters more than another feature."

## 15. Business context (so technical choices serve the model)

- **Customer:** sell to small/mid **freight forwarders** first (volume, already in the
  dispute loop, you're "software" to them → cleaner legal footing), then importers.
- **Pricing:** flat monthly subscription ($149 / $399 / $899 by volume). The savings
  dashboard *justifies* the price. Avoid contingency (% of recovered) until counsel signs
  off — it edges toward acting as the customer's agent.
- **Go-to-market:** done-for-you **service first** (operator runs invoices, logs outcomes,
  builds the track record), productize to self-serve only after demand is proven.
- **Margins** are ~98% (each audit costs pennies). The constraint is customer acquisition
  and operator time, never compute cost.

---

*This brief describes intent and current state; it is not legal advice. Verify all
regulatory citations against the published rule before relying on them.*
