"""Render the dispute letter (and a one-page audit summary) to a branded PDF.

Why a PDF: the importer forwards it to the carrier as-is. The letter PDF keeps the
[BRACKETED] placeholders and the not-legal-advice footer intact. reportlab is
imported lazily so the rest of the package stays stdlib-only.

Public API:
    letter_pdf_bytes(report_dict, letter_text, brand=None) -> bytes
    report_pdf_bytes(report_dict, letter_text, brand=None) -> bytes  (summary + letter)
"""
from __future__ import annotations

import io

DEFAULT_BRAND = "D&D Invoice Defense"


def _money(v, cur="USD"):
    if v is None:
        return "n/a"
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get((cur or "USD").upper(), "")
    return f"{sym}{v:,.2f}" if sym else f"{v:,.2f} {cur}"


def _styles():
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch  # noqa: F401 (kept for callers)
    ss = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle("brand", parent=ss["Title"], fontSize=15, spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=10, leading=14),
        "small": ParagraphStyle("small", parent=ss["Normal"], fontSize=8, leading=11, textColor=_grey()),
        "mono": ParagraphStyle("mono", parent=ss["Normal"], fontName="Helvetica", fontSize=9.5, leading=14),
    }


def _grey():
    from reportlab.lib import colors
    return colors.HexColor("#666666")


def _doc(buf):
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate
    return SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="D&D dispute letter")


def _header(brand, st):
    from reportlab.platypus import Paragraph, Spacer
    return [
        Paragraph(brand or DEFAULT_BRAND, st["brand"]),
        Paragraph("Demurrage &amp; Detention invoice dispute", st["small"]),
        Spacer(1, 10),
    ]


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def letter_pdf_bytes(report, letter_text, brand=None):
    """The dispute letter as a forward-ready PDF."""
    from reportlab.platypus import Paragraph, Spacer
    st = _styles()
    buf = io.BytesIO()
    doc = _doc(buf)
    elems = _header(brand, st)
    # render the letter line-by-line so blank lines become spacing
    for line in (letter_text or "").split("\n"):
        if line.strip() == "":
            elems.append(Spacer(1, 6))
        elif line.strip() == "---":
            elems.append(Spacer(1, 4))
        else:
            elems.append(Paragraph(_esc(line), st["mono"]))
    doc.build(elems)
    return buf.getvalue()


def report_pdf_bytes(report, letter_text, brand=None):
    """A one-page audit summary followed by the dispute letter."""
    from reportlab.lib import colors
    from reportlab.platypus import (
        PageBreak, Paragraph, Spacer, Table, TableStyle)
    st = _styles()
    cur = report.get("currency") or "USD"
    buf = io.BytesIO()
    doc = _doc(buf)
    e = _header(brand, st)

    e.append(Paragraph("Audit summary", st["h2"]))
    meta = [
        ["Invoice", _esc(report.get("invoice_number")), "Carrier", _esc(report.get("issuing_party"))],
        ["Billed party", _esc(report.get("billed_party")), "Total billed", _money(report.get("total_amount_due"), cur)],
    ]
    t = Table(meta, colWidths=[80, 180, 80, 120])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
        ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#222")),
    ]))
    e.append(t)
    e.append(Spacer(1, 8))

    oblig = report.get("amount_obligation_eliminated") or 0
    disp = report.get("amount_disputable") or 0
    e.append(Paragraph(
        f"<b>{_money(oblig, cur)}</b> may have its payment obligation eliminated "
        f"(required-element / timing defects); an additional <b>{_money(disp, cur)}</b> "
        f"is implicated by arithmetic and substantive grounds (indicative — lines may overlap).",
        st["body"]))
    e.append(Spacer(1, 6))

    fails = [f for f in report.get("findings", []) if f.get("status") == "fail"]
    if fails:
        e.append(Paragraph("Grounds found", st["h2"]))
        for f in fails:
            amt = f.get("amount_implicated") or 0
            tag = f" — ~{_money(amt, cur)}" if amt else ""
            e.append(Paragraph(f"• <b>{_esc(f.get('title'))}</b>{tag} "
                               f"<font size=8 color='#666'>({_esc(f.get('citation'))})</font>", st["body"]))

    e.append(Spacer(1, 8))
    e.append(Paragraph(
        "Automated analysis for the importer's review — not legal advice, and it files no "
        "disputes. Verify each ground and citation before relying on it.", st["small"]))

    e.append(PageBreak())
    e.append(Paragraph("Draft dispute letter", st["h2"]))
    e.append(Paragraph("For your review — complete the [BRACKETED] fields and verify before sending.", st["small"]))
    e.append(Spacer(1, 8))
    for line in (letter_text or "").split("\n"):
        if line.strip() == "":
            e.append(Spacer(1, 6))
        elif line.strip() == "---":
            e.append(Spacer(1, 4))
        else:
            e.append(Paragraph(_esc(line), st["mono"]))

    doc.build(e)
    return buf.getvalue()
