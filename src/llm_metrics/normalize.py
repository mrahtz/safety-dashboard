"""Value normalization (section 4.4 / 6a).

Two jobs, kept mechanical:

- ``strip_decorations``: remove ``%``, footnote marks, qualifiers and whitespace
  from a raw value string, for the OCR-presence check and for display.
- ``to_float``: parse the leading numeric reading (with sign) to a typed float,
  so the structural and VLM values can be compared after rounding to the source's
  stated precision. Returns ``None`` for cells with no single numeric reading
  (e.g. ``11/12``, ``PASS``), which the caller treats as not-auto-comparable.

We normalize BEFORE comparing so the review queue does not fill up with noise
like ``92.3`` vs ``92.30`` (section 4.4). Callers always log the raw forms too.
"""

import re

# Leading signed number: matches +0.2, -10.4, 56.5, 0.810, 32.32 ...
_LEADING_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")
# Footnote marks and stray decoration characters.
_FOOTNOTE_MARKS = "*†‡§¶"


def strip_decorations(value_string: str) -> str:
    """Strip ``%``, footnote marks, parenthetical qualifiers and whitespace."""
    s = re.sub(r"\([^)]*\)", "", value_string)          # drop parentheticals
    s = s.replace("%", "")
    s = "".join(ch for ch in s if ch not in _FOOTNOTE_MARKS)
    return s.strip()


def to_float(value_string: str, precision: int | None = None) -> float | None:
    """Parse the leading signed numeric reading; round to ``precision`` decimals.

    A fraction like ``11/12`` has a leading number ``11`` but is not a single
    reading, so we reject it: if a ``/`` follows the number it is not a scalar.
    """
    m = _LEADING_NUMBER.search(value_string)
    if not m:
        return None
    tail = value_string[m.end():].lstrip()
    if tail.startswith("/"):
        return None
    val = float(m.group())
    return round(val, precision) if precision is not None else val


def precision_of(value_string: str) -> int:
    """Number of decimal places in the leading reading (0 if integer/none)."""
    m = _LEADING_NUMBER.search(value_string)
    if not m or "." not in m.group():
        return 0
    return len(m.group().split(".", 1)[1])
