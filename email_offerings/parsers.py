"""Turn the owner's existing emails and files into :class:`Offering` records.

Three inputs are supported:

* the **internal cost sheet** the owner writes to the sales team
  (``DELETE RED...FOB: Calhoun, GA...COST FOB: $0.99/sf...Suggested Sell
  Below...BRING BACK ALL FIRM OFFERS...DELETE RED.`` followed by the product
  description, subject ``$0.99/sf Silver Rustic Oak ... (AWR 10/1)``),
* a **JSON offering file** (``Offering.to_dict()`` shape), and
* **buyer lines** such as ``Jane Doe <jane@example.com>`` or
  ``jane@example.com, Jane Doe, Doe Floors, 73127``.

Everything here is pure parsing: no I/O except :func:`load_offering_file`.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

from email_offerings.models import Buyer, Offering, OfferingStatus, Unit, make_offering_id, utcnow
from email_offerings.templates import strip_internal_markup

# --------------------------------------------------------------------------- #
# Regular expressions
# --------------------------------------------------------------------------- #

_AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+"

_PRICE_RE = re.compile(
    rf"""^\s*
        (?:(?:USD|US\$|\$)\s*)?                  # optional currency
        (?P<amount>{_AMOUNT})
        \s*
        (?P<cents>cents?\b|¢)?
        (?P<rest>.*)$""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)
_AMOUNT_ONLY_RE = re.compile(
    rf"^\s*(?P<currency>(?:USD|US\$|\$)\s*)?(?P<amount>{_AMOUNT})\s*(?P<cents>cents?\b|¢)?(?P<tail>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_TAIL_PUNCT_RE = re.compile(r"^[\s.,;:!?)\]]*$")
_UNIT_SEPARATOR_RE = re.compile(r"^\s*(?:/|per\b|a\b|an\b|for\b)\s*", re.IGNORECASE)
_UNIT_PUNCT_RE = re.compile(r"[.,;:!?()\[\]/]")
_SPACES_RE = re.compile(r"\s+")

# Subject handling.
_REPLY_PREFIX_RE = re.compile(r"^\s*(?:re|fwd?|fw)\s*:\s*", re.IGNORECASE)
_PERSONAL_PREFIX_RE = re.compile(r"^\s*[A-Za-z][A-Za-z .'-]{0,40}>>\s*")
_DATE_SUFFIX_RE = re.compile(r"\s*\([^()]*?\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b[^()]*\)\s*$")
_UPDATE_SUFFIX_RE = re.compile(r"\s*[-–—]\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?\s+update\s*$", re.IGNORECASE)
_MAKE_OFFERS_PREFIX_RE = re.compile(r"^\s*MAKE\s+(?:FIRM\s+)?OFFERS?\s*[:\-]?\s*", re.IGNORECASE)

# Body handling.
_MARKER_WORDS = r"COST|SELL|SUGGESTED|BRING|MAKE|DELETE|FOB|F\.O\.B\.|QTY|QUANTITY|APPROX"
_SEGMENT_SPLIT_RE = re.compile(
    rf"\.{{2,}}|…+|[\r\n]+|;|\s\|\s|\.\s+(?=(?:{_MARKER_WORDS})\b)",
    re.IGNORECASE,
)
_DELETE_RED_SPAN_RE = re.compile(r"DELETE\s+RED\b.*?DELETE\s+RED\b\.?", re.IGNORECASE | re.DOTALL)
_DELETE_RED_TAIL_RE = re.compile(r"DELETE\s+RED\b.*\Z", re.IGNORECASE | re.DOTALL)
_INTERNAL_LINE_RE = re.compile(r"COST\s+FOB|Suggested\s+Sell", re.IGNORECASE)
_FOB_RE = re.compile(
    r"\bF\.?O\.?B\.?\s*(?:point|location|pt\.?)?\s*[:\-]\s*(?P<value>.+?)\.?"
    r"(?=\s*(?:$|COST\b|SELL\b|SUGGESTED\s+SELL\b|BRING\s+BACK\b|MAKE\s+(?:FIRM\s+)?OFFERS\b|DELETE\s+RED\b|QTY\b|QUANTITY\b))",
    re.IGNORECASE,
)
_FOB_LOOSE_RE = re.compile(
    r"\bF\.?O\.?B\.?\s+(?:point\s+|location\s+)?(?P<value>[A-Z][A-Za-z.' ]*?,\s*[A-Z]{2})\b",
)
_COST_PREFIX_RE = re.compile(r"\bCOST\s*$", re.IGNORECASE)
_COST_RE = re.compile(
    r"\bCOST(?:\s+F\.?O\.?B\.?)?(?:\s+price)?\s*[:\-]?\s*(?P<value>(?:USD|US\$|\$)?\s*\.?\d.*)$",
    re.IGNORECASE,
)
_SELL_RE = re.compile(
    r"(?:^\s*SELL(?:ING)?(?:\s+PRICE|\s+AT|\s+FOR)?|\bSUGGESTED\s+SELL(?:ING)?(?:\s+PRICE|\s+AT|\s+FOR)?)"
    r"\s*[:\-]?\s*(?P<value>(?:USD|US\$|\$)?\s*\.?\d.*)$",
    re.IGNORECASE,
)
_MARKER_LINE_RE = re.compile(
    r"^\s*(?:F\.?O\.?B\.?\s*(?:point|location|pt\.?)?\s*[:\-]|SELL(?:ING)?(?:\s+PRICE|\s+AT|\s+FOR)?\s*[:\-]|"
    r"QTY\s*[:\-]|QUANTITY\s*[:\-]|AVAILABLE\s*[:\-]|BRING\s+BACK\s+(?:ALL\s+)?(?:FIRM\s+)?OFFERS\b\.?\s*$|"
    r"MAKE\s+(?:FIRM\s+)?OFFERS\b\.?\s*$)",
    re.IGNORECASE,
)
_SUGGESTED_SELL_RE = re.compile(r"\bSUGGESTED\s+SELL\b", re.IGNORECASE)
_MAKE_OFFERS_RE = re.compile(
    r"\bBRING\s+BACK\s+(?:ALL\s+)?(?:FIRM\s+)?OFFERS\b|\bMAKE\s+(?:FIRM\s+)?OFFERS\b",
    re.IGNORECASE,
)
_QTY_LABEL_RE = re.compile(r"^\s*(?:QTY|QUANTITY|AVAILABLE)\s*[:\-]\s*(?P<value>.+?)\s*\.?\s*$", re.IGNORECASE)
_QTY_RE = re.compile(
    r"(?:\b(?:approx(?:imately)?\.?|about|roughly|around|~)\s*)?"
    r"\b(?:\d+(?:\.\d+)?|a|one|two|three|four|five|six|seven|eight|nine|ten|twelve|several|multiple)\s*\+?\s*"
    r"(?:full\s+)?(?:truck\s*loads?|trucks?|TLs?|loads?|pallets?|plts?|skids?|containers?)\b"
    r"(?!\s*(?:per\b|/|a\b|in\s+a\b|to\s+a\b|on\s+a\b|to\s+the\b))"
    r"(?:\s+(?:available|avail\.?|left|remaining|in\s+stock|on\s+hand|total))?",
    re.IGNORECASE,
)
_UNITS_PER_TRUCKLOAD_RE = re.compile(
    r"(?P<count>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*\+?\s*"
    r"(?P<unit>sf|sq\.?\s*ft\.?|square\s+f(?:ee|oo)t|sy|sq\.?\s*yds?\.?|square\s+yards?|lf|lin\.?\s*ft\.?|"
    r"linear\s+f(?:ee|oo)t|pcs?|pieces?|plts?|pallets?|skids?|ea|each|units?)\s*"
    r"(?:per|/|a|in\s+a|to\s+a|to\s+the|on\s+a)\s+(?:full\s+)?(?:truck\s*load|truck|TL|load|trailer)\b",
    re.IGNORECASE,
)

_EXTRA_UNIT_ALIASES: dict[str, Unit] = {
    "pcs": Unit.EACH,
    "pieces": Unit.EACH,
    "units": Unit.EACH,
    "plts": Unit.PALLET,
    "pallets": Unit.PALLET,
    "skids": Unit.PALLET,
}

# Buyer lines.
_STATE_RE = re.compile(r"^[A-Za-z]{2}$")
_EMAIL_RE = re.compile(r"^[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[^@\s<>,;\"']+$")
_ANGLE_RE = re.compile(r"^(?P<name>[^<]*)<(?P<email>[^<>]+)>(?P<rest>.*)$")
_COMMENT_RE = re.compile(r"^(?P<email>\S+)\s*\((?P<name>[^()]*)\)\s*$")
_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
_FIELD_SPLIT_RE = re.compile(r"[,;\t]")


# --------------------------------------------------------------------------- #
# Prices and units
# --------------------------------------------------------------------------- #


def _parse_unit_token(token: str, *, allow_plural: bool = False) -> Unit | None:
    """Parse ``"/sf"``, ``"SF."``, ``"sq. ft."``, ``"pcs"`` ... into a :class:`Unit`.

    Plural truckload words (``"truckloads"``, ``"loads"``) are only accepted
    with ``allow_plural`` so that a quantity such as ``"6 truckloads"`` is not
    mistaken for a price.
    """
    normalised = _SPACES_RE.sub(" ", _UNIT_PUNCT_RE.sub(" ", token.lower())).strip()
    if not normalised:
        return None
    normalised = normalised.replace("feet", "foot").replace("yards", "yard")
    if normalised in _EXTRA_UNIT_ALIASES:
        return _EXTRA_UNIT_ALIASES[normalised]
    candidates = [normalised]
    if allow_plural and normalised.endswith("s") and len(normalised) > 2:
        candidates.append(normalised[:-1])
    for candidate in candidates:
        try:
            return Unit.parse(candidate)
        except ValueError:
            continue
    return None


def _unit_from_text(rest: str) -> Unit | None:
    """Find the unit at the start of ``rest`` (``"/sf Silver Oak"`` → ``Unit.SF``)."""
    rest = _UNIT_SEPARATOR_RE.sub("", rest, count=1)
    words = rest.split()
    for width in (3, 2, 1):
        if len(words) >= width:
            unit = _parse_unit_token(" ".join(words[:width]))
            if unit is not None:
                return unit
    return None


def _amount_value(amount: str, cents: str | None) -> float:
    value = float(amount.replace(",", ""))
    return round(value / 100.0, 4) if cents else value


def parse_price_unit(text: str) -> tuple[float, Unit]:
    """Parse a price with its unit.

    Handles ``"$0.99/sf"``, ``"$0.60/SF"``, ``"$8500/TL"``, ``"$8,500/TL"``,
    ``"$8,500 per truckload"``, ``"$11.90/sy"``, ``"0.60/sf"``, ``"$45 each"``
    and ``"99 cents/sf"`` (→ ``0.99``). Text after the unit is ignored, so a
    subject such as ``"$0.99/sf Silver Rustic Oak"`` parses too.

    Args:
        text: The price text.

    Returns:
        ``(price, unit)``.

    Raises:
        ValueError: If no leading amount or no recognisable unit is found.
    """
    match = _PRICE_RE.match(text or "")
    if not match:
        raise ValueError(f"Could not find a price in {text!r}.")
    unit = _unit_from_text(match.group("rest"))
    if unit is None:
        raise ValueError(f"Could not find a pricing unit in {text!r} (expected e.g. '/sf', '/TL', 'each').")
    return _amount_value(match.group("amount"), match.group("cents")), unit


def _parse_price_with_default(text: str, default_unit: Unit | None) -> tuple[float, Unit]:
    """Like :func:`parse_price_unit` but accept a bare amount when a unit is already known."""
    try:
        return parse_price_unit(text)
    except ValueError:
        match = _AMOUNT_ONLY_RE.match(text or "")
        if default_unit is None or not match:
            raise
        # "$8000" or "$8000 delivered" is a bare price; "8000 bananas" is not.
        if not (match.group("currency") or match.group("cents") or _TAIL_PUNCT_RE.match(match.group("tail"))):
            raise
        return _amount_value(match.group("amount"), match.group("cents")), default_unit


def _leading_price(text: str) -> tuple[float, Unit, str] | None:
    """Split a leading price token off ``text``: ``"$0.99/sf Oak"`` → ``(0.99, SF, "Oak")``.

    Only tokens that look like money (a ``$``, a decimal point or the word
    ``cents``) are accepted, so ``"6 truckloads Oak"`` or ``"5mm/12mil Oak"`` are
    left alone.
    """
    words = text.split()
    for width in (1, 2, 3):
        if len(words) < width:
            break
        candidate = " ".join(words[:width])
        if not (candidate.startswith("$") or re.match(r"^\d*\.\d+", candidate) or re.search(r"\bcents?\b|¢", candidate, re.IGNORECASE)):
            continue
        try:
            price, unit = parse_price_unit(candidate)
        except ValueError:
            continue
        return price, unit, " ".join(words[width:])
    return None


# --------------------------------------------------------------------------- #
# Internal cost sheet
# --------------------------------------------------------------------------- #


def _clean_subject(subject: str) -> tuple[str, bool]:
    """Strip reply/forward prefixes, personal ``NAME>>`` prefixes and date suffixes.

    Returns ``(remaining text, make_offers flag from a MAKE OFFERS: prefix)``.
    """
    text = subject.strip()
    for _ in range(5):  # a handful of stacked prefixes/suffixes at most
        before = text
        text = _REPLY_PREFIX_RE.sub("", text)
        text = _PERSONAL_PREFIX_RE.sub("", text)
        text = _UPDATE_SUFFIX_RE.sub("", text)
        text = _DATE_SUFFIX_RE.sub("", text)
        if text == before:
            break
    make_offers = False
    stripped = _MAKE_OFFERS_PREFIX_RE.sub("", text)
    if stripped != text:
        make_offers = True
        text = stripped
    return text.strip(), make_offers


def _internal_spans(body: str) -> tuple[list[str], str]:
    """Return the ``DELETE RED ... DELETE RED`` spans and the body with them removed."""
    spans = [m.group(0) for m in _DELETE_RED_SPAN_RE.finditer(body)]
    remainder = _DELETE_RED_SPAN_RE.sub(" ", body)
    tail = _DELETE_RED_TAIL_RE.search(remainder)
    if tail:
        spans.append(tail.group(0))
        remainder = remainder[: tail.start()]
    return spans, remainder


def _segments(body: str) -> list[str]:
    return [seg.strip() for seg in _SEGMENT_SPLIT_RE.split(body) if seg and seg.strip()]


def _find_fob(segments: list[str], body: str) -> str:
    for segment in segments:
        match = _FOB_RE.search(segment)
        if match and not _COST_PREFIX_RE.search(segment[: match.start()]):
            return match.group("value").strip()
    for match in _FOB_LOOSE_RE.finditer(body):
        if not _COST_PREFIX_RE.search(body[: match.start()]):
            return match.group("value").strip()
    return ""


def _find_quantity(segments: list[str], body: str) -> str:
    for segment in segments:
        match = _QTY_LABEL_RE.match(segment)
        if match:
            return match.group("value").strip()
    match = _QTY_RE.search(body)
    return match.group(0).strip() if match else ""


def _find_units_per_truckload(body: str, unit: Unit) -> float | None:
    if unit == Unit.TRUCKLOAD:
        return None
    for match in _UNITS_PER_TRUCKLOAD_RE.finditer(body):
        if _parse_unit_token(match.group("unit"), allow_plural=True) == unit:
            count = float(match.group("count").replace(",", ""))
            if count > 0:
                return count
    return None


def parse_internal_sheet(subject: str, body: str, *, now: datetime) -> Offering:
    """Parse the owner's internal cost sheet email into a draft :class:`Offering`.

    Subject forms: ``"$0.99/sf {title} (AWR 10/1)"``, ``"MAKE OFFERS: {title}
    (New 10/1)"``, optionally prefixed with ``Re:``/``Fwd:`` or suffixed with
    ``"- 10/1 update"``. The leading price sets ``cost_price`` when the body
    mentions ``COST FOB`` and ``sell_price`` otherwise.

    Body markers (case-insensitive, separated by ``...`` or newlines):
    ``FOB: Calhoun, GA``, ``COST FOB: $0.99/sf``, ``SELL: $1.19/sf`` /
    ``Suggested Sell: $1.19/sf``, ``Suggested Sell Below``, ``BRING BACK ALL
    FIRM OFFERS`` / ``MAKE OFFERS``, ``Approx 6 truckloads available``, and
    ``24,000 sf per truckload``. The customer-facing ``description`` is the
    body with :func:`email_offerings.templates.strip_internal_markup` applied
    and lines that are nothing but a captured marker (``FOB:``, ``SELL:``,
    ``QTY:``, ``BRING BACK ALL FIRM OFFERS``) dropped; the removed internal
    text (``DELETE RED`` spans, ``COST FOB`` / ``SELL`` lines) is kept in
    ``internal_notes``.

    Args:
        subject: Email subject.
        body: Plain-text email body.
        now: Timestamp used for ``created_at`` and the offering id.

    Returns:
        A ``DRAFT`` offering with ``id == make_offering_id(title, now)``.

    Raises:
        ValueError: If no title, no FOB location or no price can be found.
    """
    body = body or ""
    title, make_offers = _clean_subject(subject or "")

    sell_price: float | None = None
    sell_unit: Unit | None = None
    cost_price: float | None = None
    cost_unit: Unit | None = None

    headline: tuple[float, Unit] | None = None
    leading = _leading_price(title)
    if leading:
        price, unit, title = leading
        headline = (price, unit)
        if re.search(r"\bCOST\s+F\.?O\.?B\b", body, re.IGNORECASE):
            cost_price, cost_unit = price, unit
        else:
            sell_price, sell_unit = price, unit
    if not title:
        raise ValueError(f"Could not determine an offering title from subject {subject!r}.")

    spans, _remainder = _internal_spans(body)
    segments = _segments(body)
    default_unit = sell_unit or cost_unit
    notes: list[str] = []

    for segment in segments:
        cost_match = _COST_RE.search(segment)
        if cost_match:
            try:
                cost_price, cost_unit = _parse_price_with_default(cost_match.group("value"), default_unit)
                default_unit = default_unit or cost_unit
            except ValueError:
                pass
        if _SUGGESTED_SELL_RE.search(segment):
            notes.append(segment.rstrip("."))
        sell_match = _SELL_RE.search(segment)
        if sell_match:
            try:
                sell_price, sell_unit = _parse_price_with_default(sell_match.group("value"), default_unit)
                default_unit = default_unit or sell_unit
            except ValueError:
                pass

    if _MAKE_OFFERS_RE.search(body):
        make_offers = True

    # "$8,500/TL ... Suggested Sell: $9,000/TL": the headline was the cost after all.
    if headline and cost_price is None and sell_price is not None and sell_price != headline[0]:
        cost_price, cost_unit = headline

    if sell_price is None:
        if cost_price is None:
            raise ValueError(
                f"No price found for {title!r}: expected a leading price in the subject "
                "or a 'COST FOB:' / 'SELL:' marker in the body."
            )
        sell_price, sell_unit = cost_price, cost_unit
    unit = sell_unit or cost_unit or Unit.EACH

    fob_location = _find_fob(segments, body)
    if not fob_location:
        raise ValueError(f"No FOB location found for {title!r} (expected 'FOB: City, ST' in the body).")

    internal_lines = [
        line.strip()
        for line in _remainder.splitlines()
        if line.strip() and (_INTERNAL_LINE_RE.search(line) or _SELL_RE.search(line))
    ]
    internal_notes = "\n".join(span.strip() for span in [*spans, *internal_lines])
    description = "\n".join(
        line for line in strip_internal_markup(body).splitlines() if not _MARKER_LINE_RE.match(line)
    ).strip()

    return Offering(
        id=make_offering_id(title, now),
        title=title,
        description=description,
        fob_location=fob_location,
        unit=unit,
        sell_price=sell_price,
        quantity_available=_find_quantity(segments, body),
        units_per_truckload=_find_units_per_truckload(body, unit),
        make_offers=make_offers,
        status=OfferingStatus.DRAFT,
        created_at=now,
        cost_price=cost_price,
        suggested_sell_note="\n".join(notes),
        internal_notes=internal_notes,
    )


# --------------------------------------------------------------------------- #
# Offering files
# --------------------------------------------------------------------------- #


def load_offering_file(path: str | os.PathLike[str]) -> Offering:
    """Load an :class:`Offering` from a JSON file in ``Offering.to_dict()`` shape.

    A missing ``id`` is generated with ``make_offering_id(title, utcnow())``.

    Args:
        path: Path to the JSON file.

    Returns:
        The parsed offering.

    Raises:
        ValueError: If the file is not valid JSON, is not a JSON object, has
            no ``title``, or is missing another required field.
        OSError: If the file cannot be read.
    """
    name = os.fspath(path)
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{name} must contain a JSON object with the offering fields.")
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError(f"{name} is missing the required 'title' field.")
    data = dict(data)
    data["title"] = title
    if not str(data.get("id") or "").strip():
        data["id"] = make_offering_id(title, utcnow())
    data.setdefault("description", "")
    try:
        return Offering.from_dict(data)
    except TypeError as exc:
        raise ValueError(f"{name} is missing a required offering field: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}") from exc


# --------------------------------------------------------------------------- #
# Buyer lines
# --------------------------------------------------------------------------- #


def _location_fields(fields: list[str]) -> tuple[str, str]:
    """Split trailing location fields into ``(postal_code, city_state)``."""
    postal_code = ""
    city_state = ""
    for value in fields:
        if not value:
            continue
        if _ZIP_RE.match(value) and not postal_code:
            postal_code = value
        elif city_state and _STATE_RE.match(value) and "," not in city_state:
            city_state = f"{city_state}, {value.upper()}"
        elif not city_state:
            city_state = value
    return postal_code, city_state


def parse_buyer_line(line: str) -> Buyer | None:
    """Parse one line of a buyer list into a :class:`Buyer`.

    Accepted forms::

        Jane Doe <jane@example.com>
        jane@example.com
        jane@example.com (Jane Doe)
        jane@example.com, Jane Doe, Doe Floors, 73127
        jane@example.com, Jane Doe, Doe Floors, Oklahoma City, OK
        Jane Doe <jane@example.com>, Doe Floors, 73127

    Args:
        line: The raw line (commas, semicolons or tabs separate fields).

    Returns:
        A :class:`Buyer`, or ``None`` for blank lines, ``#`` comments and
        lines without a valid email address.
    """
    text = (line or "").strip()
    if not text or text.startswith("#"):
        return None

    name = company = ""
    extra: list[str] = []
    angle = _ANGLE_RE.match(text)
    comment = _COMMENT_RE.match(text)
    if angle:
        email = angle.group("email").strip()
        name = angle.group("name").strip().strip('"').strip()
        extra = [f.strip().strip('"') for f in _FIELD_SPLIT_RE.split(angle.group("rest")) if f.strip()]
        if extra:
            company, *extra = extra
    elif comment and _EMAIL_RE.match(comment.group("email")):
        email = comment.group("email")
        name = comment.group("name").strip()
    else:
        fields = [f.strip().strip('"') for f in _FIELD_SPLIT_RE.split(text)]
        index = next((i for i, f in enumerate(fields) if _EMAIL_RE.match(f)), None)
        if index is None:
            return None
        email = fields.pop(index)
        name = fields[0] if len(fields) > 0 else ""
        company = fields[1] if len(fields) > 1 else ""
        extra = fields[2:]

    if not _EMAIL_RE.match(email):  # stricter than Buyer's own check, so Buyer() cannot raise
        return None
    postal_code, city_state = _location_fields(extra)
    return Buyer(email=email, name=name, company=company, postal_code=postal_code, city_state=city_state)


__all__ = [
    "load_offering_file",
    "parse_buyer_line",
    "parse_internal_sheet",
    "parse_price_unit",
]
