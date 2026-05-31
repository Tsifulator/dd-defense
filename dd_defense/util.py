"""Small, dependency-free helpers: date parsing, number coercion, money formatting.

Kept deliberately stdlib-only so the audit engine runs anywhere with just Python.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

# Formats we attempt, in order, when an invoice gives us a date as free text.
_DATE_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d",
    "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y",
    "%d-%b-%Y", "%d %b %Y", "%d %B %Y",
    "%B %d, %Y", "%b %d, %Y",
]


def parse_date(value):
    """Best-effort parse of a date from a string/date. Returns date or None.

    Intentionally forgiving: invoices arrive in many formats. If we cannot parse
    it, we return None and let the caller decide (usually -> needs_evidence),
    rather than guessing.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def to_float(value):
    """Coerce '$1,200.00', '(150)', 1200 -> float. Returns None if not numeric."""
    if value is None:
        return None
    if isinstance(value, bool):  # guard: bool is a subclass of int
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    cleaned = "".join(ch for ch in s if ch.isdigit() or ch in ".-")
    if cleaned in ("", "-", ".", "-.", "--"):
        return None
    try:
        v = float(cleaned)
    except ValueError:
        return None
    return -abs(v) if negative else v


def fmt_money(amount, currency="USD"):
    if amount is None:
        return "n/a"
    symbols = {"USD": "$", "EUR": "€", "GBP": "£"}
    sym = symbols.get((currency or "USD").upper(), "")
    if sym:
        return f"{sym}{amount:,.2f}"
    return f"{amount:,.2f} {currency}"


def days_between(start, end):
    if start is None or end is None:
        return None
    return (end - start).days


def date_range(start, end):
    """Inclusive list of dates start..end (order-tolerant)."""
    if start is None or end is None:
        return []
    if end < start:
        start, end = end, start
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out
