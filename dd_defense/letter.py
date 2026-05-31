"""Draft a dispute letter for the IMPORTER to review and send.

Templated (no LLM needed). `polish()` is an optional LLM pass to smooth tone.
The framing is deliberate: the importer is the sender. Nothing here says the tool
disputes "on the customer's behalf." [BRACKETED] items are for the importer to
complete or verify before sending.
"""
from __future__ import annotations

from .util import fmt_money


def draft_letter(report, inv=None):
    c = report.currency or "USD"
    carrier = report.issuing_party or "[CARRIER NAME]"
    importer = report.billed_party or "[YOUR COMPANY NAME]"
    invoice_no = report.invoice_number or "[INVOICE NUMBER]"

    fails = [f for f in report.findings if f.status == "fail"]
    facial = [f for f in fails if f.layer == "facial"]
    substantive = [f for f in fails if f.layer == "substantive"]
    needs = [f for f in report.findings if f.status == "needs_evidence"]
    eliminated = report.amount_obligation_eliminated > 0

    L = []
    L.append("[YOUR COMPANY LETTERHEAD]")
    L.append("[DATE]")
    L.append("")
    L.append(f"To: {carrier} — Billing Disputes")
    L.append("[CARRIER DISPUTE EMAIL / ADDRESS — see the dispute contact on the invoice]")
    L.append("")
    L.append(f"Re: Dispute of demurrage/detention invoice {invoice_no}")
    L.append("")
    L.append("To whom it may concern,")
    L.append("")
    L.append(
        f"{importer} disputes the charges on invoice {invoice_no} and requests their mitigation, "
        f"waiver, or removal. Based on our review, the invoice does not meet the requirements of the "
        f"Federal Maritime Commission's rule on demurrage and detention billing (46 CFR Part 541) "
        f"and/or reflects charges that are not properly due, for the reasons below.")
    L.append("")

    n = 0
    if facial:
        L.append("Billing-requirement and accuracy defects:")
        for f in facial:
            n += 1
            L.append(f"  {n}. {f.dispute_ground_text}")
        L.append("")
    if substantive:
        L.append("Charges that do not appear properly due:")
        for f in substantive:
            n += 1
            L.append(f"  {n}. {f.dispute_ground_text}")
        L.append("")

    if eliminated:
        L.append(
            f"Because the invoice omits required information and/or was not timely issued, we "
            f"understand our obligation to pay may be eliminated under the rule. We therefore request "
            f"that the charge of {fmt_money(report.total_amount_due, c)} be withdrawn, or that a fully "
            f"compliant invoice be reissued for our review.")
    else:
        L.append("We request that the disputed amounts be removed or corrected and a revised invoice "
                 "issued for our review.")
    L.append("")

    if needs:
        L.append("We are also reviewing whether additional charges accrued during periods when the "
                 "container could not be retrieved or returned (e.g., terminal closures or unavailable "
                 "appointments) and may supplement this dispute with supporting records.")
        L.append("")

    L.append("Please confirm receipt and respond within the timeframe for fee mitigation, refund, or "
             "waiver described on the invoice (and as provided by the FMC rule). We are happy to provide "
             "any further information needed to resolve this.")
    L.append("")
    L.append("Sincerely,")
    L.append("")
    L.append("[NAME]")
    L.append(f"[TITLE], {importer}")
    L.append("[PHONE] · [EMAIL]")
    L.append("")
    L.append("---")
    L.append("DRAFT for your review. You are the sender; verify every factual claim and citation, "
             "complete the bracketed fields, and confirm the carrier's dispute contact from the invoice "
             "before sending. This letter is not legal advice.")
    return "\n".join(L)


def polish(letter_text, model="claude-sonnet-4-5", api_key=None):
    """Optional: smooth the draft's tone via the Anthropic API. Returns the input
    unchanged if the SDK or key is unavailable."""
    try:
        import os
        import anthropic
    except ImportError:
        return letter_text
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return letter_text
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model,
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": ("Polish this dispute letter for a professional, firm-but-cooperative tone. Keep "
                        "every factual claim, number, citation, and [BRACKETED] placeholder exactly. Do "
                        "not add claims. Return only the letter.\n\n" + letter_text),
        }],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text") or letter_text
