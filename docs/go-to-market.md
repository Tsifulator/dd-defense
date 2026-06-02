# D&D Invoice Defense — Pricing & Go-to-Market

> Working doc. Numbers are **starting anchors to validate**, not final. The goal:
> land the first 3 paying subscribers via a free-pilot-to-paid motion.

---

## 1. What we sell (the one sentence)

**Always-on protection against overpaying demurrage & detention — we catch the
disputable charges on every invoice and hand you a ready-to-send dispute letter.**

We sell *recovered dollars with proof*, delivered as software. We do **not** file
disputes or give legal advice — the client reviews and sends. That boundary is
deliberate and keeps us clean.

## 2. The model: monthly subscription (tiered by volume)

Why subscription, not % - of - savings:
- **Attribution is impossible to enforce** — billing on savings needs the client to
  honestly report what the carrier waived. They won't.
- **% - of - recovered edges toward acting as their agent** — legal exposure we avoid.
- **Subscription = predictable revenue, no attribution, clean positioning.**

The savings dashboard is the **sales tool that justifies the price**, not the
billing basis.

| Tier | Invoices / month | Price (monthly) | Price (annual, ~2 mo free) | For |
|------|------------------|-----------------|-----------------------------|-----|
| **Starter** | up to 50 | $149 | $1,490/yr | a single importer or a tiny forwarder |
| **Growth** | up to 250 | $399 | $3,990/yr | an active mid-size forwarder |
| **Pro** | up to 1,000 | $899 | $8,990/yr | a high-volume forwarder |
| **Managed** (add-on) | — | +$500–1,500/mo | — | we also draft + track the full dispute correspondence |

Everyone gets: unlimited audits within tier, the savings dashboard, PDF
letters/reports, CSV export, regulatory updates as the rule changes.

**Why the margins are excellent:** each audit costs ~pennies (one cheap extraction
call + free deterministic checks). A Growth client running 200 invoices/mo costs a
few dollars in API against $399 revenue ≈ ~98% gross margin.

## 3. Who to target first (and why)

**Small freight forwarders serving perishable-goods importers** — in that order.

- **Forwarders > importers**: one forwarder = dozens of importers' invoices (volume),
  they're *already* in the carrier dispute loop (so we're "software" to them, not
  legal advice), and one sale covers many shippers.
- **Perishables is the sharp wedge**: reefer (refrigerated) containers carry
  **higher per-diem** and the cargo spoils, so importers feel D&D pain acutely and
  move fast. Lead with this vertical.

Ideal first customer: a 5–30 person forwarder, US ports (LA/LB, NY/NJ, Savannah),
handling reefer produce/seafood, drowning in carrier invoices, no one checking them
line by line.

## 4. The sequence that lands it (don't pitch cold)

1. **Find 5–10 forwarders** (LinkedIn, port community groups, referrals).
2. **Offer a free audit of last month's D&D invoices.** No commitment. "Send me last
   month's demurrage invoices and I'll show you what's disputable — free."
3. **Run them through the tool. Show the flagged $ + sample dispute letter.** The
   number sells it: "You were billed $X; $Y looks disputable; here are the grounds."
4. **Convert believers to a paid pilot** at the tier matching their volume.
5. **Log every outcome in the tracker.** The growing "$ recovered" record becomes the
   proof for the next prospect. After 3 wins, you have a case study.

**Key:** lead with the free number, not the price. Value first, price second.

## 5. Cold outreach (copy/paste, then make it yours)

**LinkedIn / email — short version:**

> Subject: quick question on your demurrage invoices
>
> Hi [Name] — I built a tool that checks ocean-carrier demurrage & detention
> invoices against the FMC's billing rules (46 CFR Part 541) and flags the charges
> that are disputable — missing required info, late invoices, math errors, charges
> during closures, etc.
>
> Most of these slip through because nobody has time to check every invoice line by
> line. Happy to run **last month's D&D invoices for you, free**, and show you what's
> disputable + a ready-to-send dispute letter. No commitment.
>
> Worth a look? — [You]

**Follow-up after the free audit:**

> Here's what I found on the [N] invoices you sent: **$[billed] billed, $[flagged]
> looks disputable** across [M] charges. Top grounds: [late issuance / missing
> elements / math]. Attached: the audit + a draft dispute letter for each.
>
> If you want this run on every invoice automatically, it's $[tier]/mo — typically
> pays for itself on the first disputed charge. Want to start a 30-day pilot?

## 6. Objection handling

| They say | You say |
|---|---|
| "We already check invoices." | "Manually? This checks every line against all 13 FMC required fields + the 30-day rule + math in seconds, and drafts the letter. It's the stuff that slips through on a busy week." |
| "What if a month is quiet?" | "That's the protection working — like insurance. The dashboard shows cumulative savings so you see the ROI over time. Annual billing smooths it out." |
| "Is this legal advice?" | "No — the tool analyzes and drafts; you review and send. You stay in control. That's by design." |
| "Carriers will just reject it." | "Some will. But a facially defective or late invoice eliminates the obligation to pay under the rule — those are strong. And we track what actually gets waived so you see the real win rate." |
| "Too expensive." | "What did you pay in D&D last month? If $399 catches even one disputable charge, it's paid for. The free audit shows you the number first." |

## 7. The honest risks (name them, manage them)

- **Quiet-month churn** → annual billing + cumulative-savings framing + position as
  protection, not pay-per-find.
- **Regulation in flux** (§541.4 was vacated by a court in 2025) → our maintained
  ruleset *is* the moat; the math-error & incentive-principle grounds survive even
  if the "missing element" lever weakens. "Catch overbilling" stays valuable.
- **Extraction errors on messy invoices** → human-in-the-loop by design; every field
  carries a confidence flag for review.

## 8. What we are NOT building yet (stay focused)

- Stripe billing — it's a half-day *after* someone says yes. Invoice them manually
  for the first few.
- Per-user logins, self-serve signup — the current shared-password gate is enough to
  share a link with a pilot.
- Public marketing site — the free audit is the funnel, not a website.

**The only bottleneck right now is a paying customer. Everything above serves that.**
