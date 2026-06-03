"""Deterministic validators for extracted identifiers (no LLM, no network).

The extraction model occasionally misreads a digit in a container or bill-of-lading
number (e.g. HLBU9250073 -> HLBU6952073). A dispute letter citing the wrong
container number is dead on arrival, so we catch these mechanically.

ISO 6346 container numbers carry a self-checking digit: 4 letters (owner code +
category) + 6 digits (serial) + 1 check digit. The check digit is computed from
the first 10 characters, so a single mistyped/misread character almost always
fails the checksum. This lets us flag a likely-misread container number for human
re-check BEFORE it ends up in a letter — for free.
"""
from __future__ import annotations

import re

# ISO 6346 letter values: A=10, then increment, SKIPPING every multiple of 11.
def _letter_values():
    vals, n = {}, 10
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        while n % 11 == 0:
            n += 1
        vals[ch] = n
        n += 1
    return vals


_LETTER_VALUES = _letter_values()
_CONTAINER_RE = re.compile(r"^[A-Z]{4}\d{7}$")


def container_check_digit(code):
    """Return the ISO 6346 check digit (0-9) for an 11-char container code,
    computed from its first 10 characters. Raises ValueError on bad format."""
    code = (code or "").strip().upper()
    if not re.match(r"^[A-Z]{4}\d{6}", code):
        raise ValueError(f"not a container-code shape: {code!r}")
    total = 0
    for i, ch in enumerate(code[:10]):
        val = _LETTER_VALUES[ch] if ch.isalpha() else int(ch)
        total += val * (2 ** i)
    remainder = total % 11
    return 0 if remainder == 10 else remainder


def is_valid_container(code):
    """True if `code` is a well-formed ISO 6346 number with a correct check digit."""
    code = (code or "").strip().upper()
    if not _CONTAINER_RE.match(code):
        return False
    try:
        return container_check_digit(code) == int(code[10])
    except (ValueError, KeyError):
        return False


def validate_containers(codes):
    """Given a list of container numbers, return a list of problems (dicts).
    Empty list == all valid. Each problem: {value, reason, suggestion?}."""
    problems = []
    for raw in codes or []:
        code = (str(raw) or "").strip().upper()
        if not _CONTAINER_RE.match(code):
            problems.append({
                "value": raw,
                "reason": "not a valid container-number format (expect 4 letters + 7 digits)",
            })
            continue
        try:
            expected = container_check_digit(code)
        except (ValueError, KeyError):
            problems.append({"value": raw, "reason": "could not compute check digit"})
            continue
        if expected != int(code[10]):
            # The check digit doesn't match. We can't know WHICH character was
            # misread (could be any of the 11), so we don't fabricate a "correct"
            # number — we just flag it for human re-check against the source.
            problems.append({
                "value": code,
                "reason": (f"fails the ISO 6346 check digit (stated {code[10]}, but the first 10 "
                           f"characters compute to {expected}) — at least one character is wrong, "
                           f"most likely a misread during extraction"),
            })
    return problems
