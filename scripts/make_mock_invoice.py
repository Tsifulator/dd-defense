"""Generate a realistic MOCK ocean-carrier D&D invoice as a PDF, for testing the
extraction pipeline end to end. Synthetic data; planted defects so the audit has
something to find. Requires reportlab (pip install reportlab).

Usage:  python3 scripts/make_mock_invoice.py samples/mock_invoice.pdf
"""
from __future__ import annotations

import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# --- planted, realistic content --------------------------------------------
# Defects on purpose:
#  * Invoice dated 2025-05-02 but charges last incurred 2025-03-22 -> >30 days late.
#  * Container PBLU2233445: 12 x 165 = 1980 billed as 2080 (math error).
#  * Container TGHU9988776 charge starts 2025-03-09, free time ends 2025-03-10
#    (1 day charged within free time).
#  * The "how to request mitigation/refund/waiver" timeframe is intentionally
#    OMITTED from the footer (a missing required element).


def build(path):
    styles = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=styles["Title"], fontSize=18, spaceAfter=2)
    small = ParagraphStyle("s", parent=styles["Normal"], fontSize=8.5, textColor=colors.HexColor("#444"))
    normal = styles["Normal"]
    bold = ParagraphStyle("b", parent=styles["Normal"], fontName="Helvetica-Bold")

    doc = SimpleDocTemplate(path, pagesize=LETTER,
                            leftMargin=0.7 * inch, rightMargin=0.7 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    el = []

    el.append(Paragraph("PACIFIC BLUE LINES", h))
    el.append(Paragraph("Demurrage &amp; Detention Invoice", styles["Heading2"]))
    el.append(Paragraph("Pacific Blue Lines (US) Inc. · 100 Harbor Blvd, Long Beach, CA 90802 · SCAC: PBLU", small))
    el.append(Spacer(1, 12))

    meta = Table([
        ["Invoice No.", "PBLU-DD-2025-04417", "Invoice Date", "2025-05-02"],
        ["Bill of Lading", "PBLUSLA2503187", "Due Date", "2025-05-17"],
        ["Port of Discharge", "Port of Long Beach (USLGB)", "Charge Type", "Demurrage"],
    ], colWidths=[1.1 * inch, 2.4 * inch, 1.0 * inch, 2.0 * inch])
    meta.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
        ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#222")),
    ]))
    el.append(meta)
    el.append(Spacer(1, 10))

    el.append(Paragraph("Billed To:", bold))
    el.append(Paragraph("Sunrise Produce Imports, LLC", normal))
    el.append(Paragraph("4820 Cold Storage Way, Vernon, CA 90058", small))
    el.append(Paragraph("Basis for liability: Consignee / party of record on the bill of lading above.", small))
    el.append(Spacer(1, 12))

    el.append(Paragraph(
        "Free time: 5 calendar days. Free time start 2025-03-06. Free time last day (LFD) 2025-03-10.", small))
    el.append(Paragraph(
        "Rate basis: PBLU US Demurrage Tariff Rule 210, tiered per-diem. Applicable rate: USD 165.00 per container/day.", small))
    el.append(Spacer(1, 12))

    rows = [["Container", "Type", "From", "To", "Days", "Rate/Day", "Amount"]]
    rows.append(["PBLU2233445", "Demurrage", "2025-03-11", "2025-03-22", "12", "165.00", "2,080.00"])  # math error
    rows.append(["TGHU9988776", "Demurrage", "2025-03-09", "2025-03-18", "10", "165.00", "1,650.00"])  # starts in free time
    rows.append(["", "", "", "", "", "Total Due", "3,730.00"])
    t = Table(rows, colWidths=[1.2 * inch, 1.0 * inch, 0.95 * inch, 0.95 * inch,
                               0.5 * inch, 0.9 * inch, 1.0 * inch])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("FONT", (5, -1), (6, -1), "Helvetica-Bold", 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (4, 0), (6, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#0f172a")),
        ("LINEABOVE", (5, -1), (6, -1), 0.5, colors.HexColor("#888")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f4f6f8")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    el.append(t)
    el.append(Spacer(1, 16))

    # Footer: dispute contact present; mitigation-timeframe intentionally omitted.
    el.append(Paragraph(
        "Questions or disputes: billing.disputes@pacificbluelines.example · +1 (562) 555-0148", small))
    el.append(Paragraph(
        "This invoice is issued consistent with applicable Federal Maritime Commission regulations.", small))
    el.append(Spacer(1, 6))
    el.append(Paragraph(
        "Remit payment by the due date. Reference the invoice number on all payments.", small))

    doc.build(el)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "samples/mock_invoice.pdf"
    build(out)
    print("WROTE " + out)
