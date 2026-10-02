"""Email rendering for the offerings system.

Every function in this module is pure: it takes models from
``email_offerings.models`` plus ``Settings`` and returns strings or a
``RenderedEmail``. Nothing here reads files, touches the network or looks at
the clock -- callers pass ``when``.

Two families of renderers live here:

* **Customer-facing** (offering email, follow-up, delivered quote, ZIP
  request, "interested" reply, offer acknowledgement). These never read
  ``Offering.cost_price``, ``Offering.suggested_sell_note`` or
  ``Offering.internal_notes`` and always pass ``Offering.description``
  through :func:`strip_internal_markup`, so an internal cost sheet pasted
  into a description can never leak to a buyer.
* **Owner / sales-team facing** (internal cost sheet, offer escalation,
  digest). These may contain internal data and are only ever addressed to
  ``Settings.sales_team_email`` / ``Settings.escalation_email``.

Text bodies are primary. ``RenderedEmail.html`` is a minimal ``<p>``-wrapped
version of the text with every character escaped via :func:`html.escape`.
"""

from __future__ import annotations

import html as _html
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

from email_offerings.config import Settings
from email_offerings.models import (
    ActionKind,
    ActionRecord,
    Buyer,
    Classification,
    DeliveredQuote,
    InboundMessage,
    Offering,
    Unit,
)

__all__ = [
    "MAKE_OFFER_CALL_TO_ACTION",
    "DELIVERED_CALL_TO_ACTION",
    "UNSUBSCRIBE_FOOTER_TEXT",
    "RenderedEmail",
    "blast_subject",
    "format_price",
    "internal_subject",
    "personal_subject",
    "render_delivered_quote_reply",
    "render_digest",
    "render_follow_up",
    "render_interested_reply",
    "render_internal_sheet",
    "render_offer_acknowledgement",
    "render_offer_escalation",
    "render_offering_email",
    "render_zip_request_reply",
    "short_date",
    "signature_block",
    "strip_internal_markup",
    "text_to_html",
    "unsubscribe_footer",
]

#: Footer appended to customer-facing mail when ``Policy.unsubscribe_footer`` is set.
UNSUBSCRIBE_FOOTER_TEXT: str = "Reply with REMOVE to stop receiving these offerings."

#: Call to action used on "make offers" offerings.
MAKE_OFFER_CALL_TO_ACTION: str = (
    "Please make a firm offer - all firm offers will be brought back and considered."
)

#: Call to action closing every delivered-price quote.
DELIVERED_CALL_TO_ACTION: str = (
    "If that works for you, make a firm offer delivered to your location "
    "and I will confirm availability right away."
)

# Internal markup used in the owner's cost sheets. ``DELETE RED ... DELETE RED.``
# wraps the part of the sheet the sales team must delete before forwarding.
_DELETE_RED_SPAN_RE = re.compile(r"DELETE\s+RED\b.*?DELETE\s+RED\b\.?", re.IGNORECASE | re.DOTALL)
_DELETE_RED_TAIL_RE = re.compile(r"DELETE\s+RED\b.*\Z", re.IGNORECASE | re.DOTALL)
_INTERNAL_LINE_RE = re.compile(r"COST\s+FOB|Suggested\s+Sell", re.IGNORECASE)
_SPACE_RUN_RE = re.compile(r"[ \t]{2,}")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #


@dataclass
class RenderedEmail:
    """Subject plus text body (primary) and an optional HTML alternative."""

    subject: str
    text: str
    html: str | None = None


# --------------------------------------------------------------------------- #
# Formatting primitives
# --------------------------------------------------------------------------- #


def format_price(price: float, unit: Unit) -> str:
    """Format a price the way buyers see it in subjects and bodies.

    Per-area / per-piece units keep cents (``"$0.99/sf"``, ``"$11.90/sy"``,
    ``"$45.00/ea"``); truckload prices are whole dollars with thousands
    separators (``"$8,500/TL"``).

    Args:
        price: Price in dollars.
        unit: Pricing unit (a ``Unit`` or any string ``Unit.parse`` accepts).

    Returns:
        The formatted price with its unit suffix.
    """
    if not isinstance(unit, Unit):
        unit = Unit.parse(str(unit))
    if unit is Unit.TRUCKLOAD:
        return f"${price:,.0f}/{unit.value}"
    return f"${price:,.2f}/{unit.value}"


def short_date(when: datetime | date) -> str:
    """Format a date as the owner writes it in subjects: ``"10/1"`` (no zero padding)."""
    return f"{when.month}/{when.day}"


def _format_number(value: float) -> str:
    """``2.0`` → ``"2"``, ``1.5`` → ``"1.5"``, ``12000`` → ``"12,000"``."""
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _truckloads_phrase(truckloads: float) -> str:
    """``1`` → ``"1 truckload"``, ``2`` → ``"2 truckloads"``."""
    word = "truckload" if truckloads == 1 else "truckloads"
    return f"{_format_number(truckloads)} {word}"


def _subject_core(offering: Offering) -> str:
    """``"MAKE OFFERS: {title}"`` or ``"{price} {title}"`` -- the subject without a date."""
    title = offering.title.strip()
    if offering.make_offers:
        return f"MAKE OFFERS: {title}"
    return f"{format_price(offering.sell_price, offering.unit)} {title}"


# --------------------------------------------------------------------------- #
# Subjects
# --------------------------------------------------------------------------- #


def blast_subject(offering: Offering, when: datetime, *, update: bool = False) -> str:
    """Subject for a blast to the buyer list.

    ``"$0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring (New 10/1)"`` or, for
    make-offers deals, ``"MAKE OFFERS: {title} (New 10/1)"``. A re-send gets the
    ``" - 10/1 update"`` suffix.
    """
    day = short_date(when)
    subject = f"{_subject_core(offering)} (New {day})"
    if update:
        subject = f"{subject} - {day} update"
    return subject


def personal_subject(buyer: Buyer, offering: Offering) -> str:
    """Subject for a one-buyer forward: ``"JORDAN>>$0.99/sf {title}"``.

    The buyer's first name is upper-cased. A buyer without a name gets the
    blast subject without its date suffix.
    """
    core = _subject_core(offering)
    first = buyer.first_name
    if not first:
        return core
    return f"{first.upper()}>>{core}"


def internal_subject(offering: Offering, when: datetime) -> str:
    """Subject of the internal cost sheet: ``"{price} {title} (AWR 10/1)"``.

    The price is ``cost_price`` when set, else ``sell_price``.
    """
    price = offering.cost_price if offering.cost_price is not None else offering.sell_price
    return f"{format_price(price, offering.unit)} {offering.title.strip()} (AWR {short_date(when)})"


# --------------------------------------------------------------------------- #
# Internal markup
# --------------------------------------------------------------------------- #


def strip_internal_markup(text: str) -> str:
    """Remove the owner's internal cost-sheet markup from ``text``.

    * Every ``DELETE RED ... DELETE RED`` span is removed, case-insensitively,
      across newlines, together with an optional trailing period.
    * An unbalanced ``DELETE RED`` marker drops everything from the marker to
      the end of the text (safer than leaving internal data behind).
    * Any remaining line containing ``COST FOB`` or ``Suggested Sell`` is
      dropped (case-insensitive).
    * Runs of spaces left behind by a removed span, and runs of blank lines,
      are collapsed; the result is stripped.

    Args:
        text: Description or email body possibly containing internal markup.

    Returns:
        The customer-safe text.
    """
    if not text:
        return ""
    cleaned = _DELETE_RED_SPAN_RE.sub(" ", text)
    cleaned = _DELETE_RED_TAIL_RE.sub("", cleaned)
    kept = [
        _SPACE_RUN_RE.sub(" ", line).rstrip()
        for line in cleaned.splitlines()
        if not _INTERNAL_LINE_RE.search(line)
    ]
    cleaned = "\n".join(kept)
    cleaned = _BLANK_RUN_RE.sub("\n\n", cleaned)
    return cleaned.strip()


# --------------------------------------------------------------------------- #
# Shared building blocks
# --------------------------------------------------------------------------- #


def unsubscribe_footer(settings: Settings) -> str:
    """The unsubscribe footer, or ``""`` when ``Policy.unsubscribe_footer`` is off."""
    return UNSUBSCRIBE_FOOTER_TEXT if settings.policy.unsubscribe_footer else ""


def signature_block(settings: Settings) -> str:
    """``settings.signature`` if set, else ``"-{sender_name}"`` plus the company on the next line.

    Without a sender name the sender email is used so the signature is never a bare dash.
    """
    if settings.signature.strip():
        return settings.signature.strip()
    name = settings.sender_name.strip() or settings.sender_email
    lines = [f"-{name}"]
    if settings.company_name.strip():
        lines.append(settings.company_name.strip())
    return "\n".join(lines)


def text_to_html(text: str) -> str:
    """Minimal HTML version of a text body.

    Blank-line separated blocks become ``<p>`` paragraphs; newlines inside a
    paragraph become ``<br>``. Every character goes through ``html.escape``.
    """
    paragraphs = [block.strip("\n") for block in _PARAGRAPH_SPLIT_RE.split(text.strip()) if block.strip()]
    rendered = []
    for block in paragraphs:
        lines = [_html.escape(line, quote=True) for line in block.split("\n")]
        rendered.append(f"<p>{'<br>'.join(lines)}</p>")
    return "\n".join(rendered)


def _greeting(buyer: Buyer | None) -> str:
    if buyer is not None and buyer.first_name:
        return f"Hi {buyer.first_name},"
    return "Hello,"


def _join_blocks(blocks: list[str]) -> str:
    """Join non-empty blocks with blank lines."""
    return "\n\n".join(block.strip("\n") for block in blocks if block and block.strip())


def _customer_closing(settings: Settings) -> list[str]:
    """Signature followed by the unsubscribe footer (when enabled)."""
    return [signature_block(settings), unsubscribe_footer(settings)]


def _offering_facts(offering: Offering) -> str:
    """FOB / price / quantity lines shared by the customer-facing renderers."""
    lines = [
        f"FOB: {offering.fob_location.strip()}",
        f"Price: {format_price(offering.sell_price, offering.unit)} FOB {offering.fob_location.strip()}",
    ]
    if offering.quantity_available.strip():
        lines.append(f"Quantity: {offering.quantity_available.strip()}")
    return "\n".join(lines)


def _rendered(subject: str, blocks: list[str]) -> RenderedEmail:
    text = _join_blocks(blocks)
    return RenderedEmail(subject=subject, text=text, html=text_to_html(text))


# --------------------------------------------------------------------------- #
# Customer-facing renderers
# --------------------------------------------------------------------------- #


def render_offering_email(
    offering: Offering,
    settings: Settings,
    *,
    when: datetime,
    buyer: Buyer | None = None,
    personal_note: str = "",
    update: bool = False,
) -> RenderedEmail:
    """Render the customer-facing offering email (blast or personal forward).

    With ``buyer`` the subject is :func:`personal_subject`, the body opens
    with a first-name greeting and ``personal_note`` comes first; otherwise
    the subject is :func:`blast_subject`. The body is the stripped
    description, FOB / price / quantity lines, a make-offer call to action
    when ``offering.make_offers`` is set, the signature and the footer.
    """
    if buyer is not None:
        subject = personal_subject(buyer, offering)
    else:
        subject = blast_subject(offering, when, update=update)

    blocks: list[str] = []
    if buyer is not None:
        blocks.append(_greeting(buyer))
    if personal_note.strip():
        blocks.append(personal_note.strip())
    if update:
        blocks.append(f"Update {short_date(when)}: this material is still available.")
    blocks.append(strip_internal_markup(offering.description))
    blocks.append(_offering_facts(offering))
    if offering.make_offers:
        blocks.append(MAKE_OFFER_CALL_TO_ACTION)
    blocks.extend(_customer_closing(settings))
    return _rendered(subject, blocks)


def render_delivered_quote_reply(
    offering: Offering,
    quote: DeliveredQuote,
    buyer: Buyer | None,
    settings: Settings,
) -> RenderedEmail:
    """Answer a "how much delivered to ZIP?" question.

    Mentions the freight total, origin, destination, miles, truckload count,
    the delivered price per unit (via :func:`format_price`) and closes with
    "make a firm offer delivered to your location". The engine replaces the
    subject with ``"Re: {original subject}"``; the template returns
    ``"Delivered pricing: {title}"``.
    """
    title = offering.title.strip()
    fob = format_price(quote.fob_price_per_unit, quote.unit)
    delivered = format_price(quote.delivered_price_per_unit, quote.unit)
    loads = _truckloads_phrase(quote.truckloads)

    freight_line = (
        f"The freight is ${quote.freight_total:,.0f} from {quote.origin} to {quote.destination} "
        f"({quote.distance_miles:,.0f} mi) on {loads}"
    )
    if quote.truckloads != 1:
        freight_line += f", ${quote.freight_per_truckload:,.0f} per truckload"
    freight_line += "."

    price_lines = [f"FOB price: {fob}", f"Delivered price: approx. {delivered}"]
    if quote.unit is not Unit.TRUCKLOAD and quote.units_total:
        total = f"{_format_number(quote.units_total)} {quote.unit.value}"
        if quote.delivered_total is not None:
            total += f" for about ${quote.delivered_total:,.0f} delivered"
        price_lines.append(f"Total: {total}")

    blocks = [
        _greeting(buyer),
        f"Thanks for asking about the {title}. {freight_line}",
        "\n".join(price_lines),
        DELIVERED_CALL_TO_ACTION,
        *_customer_closing(settings),
    ]
    return _rendered(f"Delivered pricing: {title}", blocks)


def render_zip_request_reply(offering: Offering, buyer: Buyer | None, settings: Settings) -> RenderedEmail:
    """Ask the buyer for a ZIP code and truckload count so we can quote delivered."""
    title = offering.title.strip()
    blocks = [
        _greeting(buyer),
        (
            f"Happy to quote the {title} delivered. The material is "
            f"{format_price(offering.sell_price, offering.unit)} FOB {offering.fob_location.strip()}. "
            "Please send me the delivery ZIP code and how many truckloads you need and "
            "I will come back with a delivered price."
        ),
        *_customer_closing(settings),
    ]
    return _rendered(f"Delivered pricing: {title}", blocks)


def render_interested_reply(offering: Offering, buyer: Buyer | None, settings: Settings) -> RenderedEmail:
    """Reply to an interested buyer with the details and a request for ZIP / quantity."""
    title = offering.title.strip()
    blocks = [
        _greeting(buyer),
        f"Thanks for your interest in the {title}. Here are the details:",
        strip_internal_markup(offering.description),
        _offering_facts(offering),
        (
            "Send me your delivery ZIP code and how many truckloads you can take and "
            "I will quote it delivered."
        ),
    ]
    if offering.make_offers:
        blocks.append(MAKE_OFFER_CALL_TO_ACTION)
    blocks.extend(_customer_closing(settings))
    return _rendered(f"{_subject_core(offering)}", blocks)


def render_follow_up(offering: Offering, buyer: Buyer | None, settings: Settings, *, when: datetime) -> RenderedEmail:
    """Short "still available" nudge to a buyer who has not replied."""
    title = offering.title.strip()
    price = format_price(offering.sell_price, offering.unit)
    blocks = [
        _greeting(buyer),
        (
            f"Just following up - the {title} is still available as of {short_date(when)} at "
            f"{price} FOB {offering.fob_location.strip()}."
            + (f" {offering.quantity_available.strip()} available." if offering.quantity_available.strip() else "")
        ),
        "If you want delivered pricing, reply with your ZIP code and how many truckloads you need.",
        *_customer_closing(settings),
    ]
    return _rendered(f"Still available: {_subject_core(offering)}", blocks)


def render_offer_acknowledgement(offering: Offering, buyer: Buyer | None, settings: Settings) -> RenderedEmail:
    """Acknowledge a firm offer without accepting it: "Got your offer, will confirm shortly"."""
    title = offering.title.strip()
    blocks = [
        _greeting(buyer),
        f"Got your offer on the {title} - thank you. I will review it and confirm shortly.",
        *_customer_closing(settings),
    ]
    return _rendered(f"Your offer: {title}", blocks)


# --------------------------------------------------------------------------- #
# Owner / sales-team facing renderers
# --------------------------------------------------------------------------- #


_AVAILABLE_RE = re.compile(r"\b(?:available|avail\.?|left|remaining|in\s+stock|on\s+hand)\b", re.IGNORECASE)
_NOTE_SPLIT_RE = re.compile(r"\.{2,}|\u2026|[\r\n]+")
_DELETE_RED_MARKER_RE = re.compile(r"DELETE\s+RED\b\.?", re.IGNORECASE)
_STRUCTURED_NOTE_RE = re.compile(
    r"^(?:(?:COST\s+)?F\.?O\.?B\.?\s*:|SELL\s*:|SUGGESTED\s+SELL\b|(?:QTY|QUANTITY|AVAILABLE)\s*[:\-]"
    r"|BRING\s+BACK\s+ALL\s+(?:FIRM\s+)?OFFERS\b|MAKE\s+(?:FIRM\s+)?OFFERS\b)",
    re.IGNORECASE,
)


def _normalise_segment(text: str) -> str:
    """Whitespace-collapsed, case-folded form used to spot duplicate sheet segments."""
    return re.sub(r"\s+", " ", text.strip().rstrip(".")).strip().casefold()


def _free_form_notes(internal_notes: str, rendered: list[str]) -> list[str]:
    """Segments of ``internal_notes`` that the internal sheet does not already render.

    ``parsers.parse_internal_sheet`` keeps the whole removed ``DELETE RED``
    span in ``internal_notes``. Every marker in such a span (``FOB:``, ``COST
    FOB:``, ``SELL:``, ``Suggested Sell``, quantity, ``BRING BACK ALL FIRM
    OFFERS``) has a structured field that :func:`render_internal_sheet`
    renders itself, so those segments and anything equal to an already
    rendered part are dropped; free-form remarks are kept in order.
    """
    known = {_normalise_segment(part) for part in rendered if part.strip()}
    kept: list[str] = []
    for raw in _NOTE_SPLIT_RE.split(_DELETE_RED_MARKER_RE.sub(" ", internal_notes)):
        segment = raw.strip().strip(".").strip()  # the marker regex may leave a stray dot behind
        key = _normalise_segment(segment)
        if not key or key in known or _STRUCTURED_NOTE_RE.match(segment):
            continue
        known.add(key)
        kept.append(segment)
    return kept


def render_internal_sheet(offering: Offering, settings: Settings, *, when: datetime) -> RenderedEmail:
    """Reproduce the owner's internal cost sheet for the sales team.

    ``"DELETE RED...FOB: {fob}...COST FOB: {cost}...SELL: {sell}...{suggested_sell_note}
    ...BRING BACK ALL FIRM OFFERS...DELETE RED."`` followed by the description.
    ``COST FOB`` appears only when ``cost_price`` is set and ``BRING BACK ALL
    FIRM OFFERS`` only when ``make_offers`` is set, so the sheet round-trips
    through ``parsers.parse_internal_sheet``. ``internal_notes`` may itself be
    a span captured by that parser; only its free-form remarks are embedded
    (markers already rendered from structured fields are dropped), so an
    offering parsed from a sheet re-renders as the same sheet.
    """
    parts = [f"FOB: {offering.fob_location.strip()}"]
    if offering.cost_price is not None:
        parts.append(f"COST FOB: {format_price(offering.cost_price, offering.unit)}")
    parts.append(f"SELL: {format_price(offering.sell_price, offering.unit)}")
    if offering.suggested_sell_note.strip():
        parts.append(offering.suggested_sell_note.strip())
    quantity = offering.quantity_available.strip()
    if quantity:
        parts.append(quantity if _AVAILABLE_RE.search(quantity) else f"{quantity} available")
    parts.extend(_free_form_notes(offering.internal_notes, [*parts, quantity]))
    if offering.make_offers:
        parts.append("BRING BACK ALL FIRM OFFERS")
    header = "...".join(["DELETE RED", *parts, "DELETE RED"]) + "."

    blocks = [
        header,
        strip_internal_markup(offering.description),
        _offering_facts(offering),
        signature_block(settings),
    ]
    return _rendered(internal_subject(offering, when), blocks)


def render_offer_escalation(
    offering: Offering | None,
    inbound: InboundMessage,
    classification: Classification,
    settings: Settings,
    *,
    thread_url: str = "",
) -> RenderedEmail:
    """Tell the owner about a firm offer: who, what (price / qty / destination), summary, quoted body, link."""
    title = offering.title.strip() if offering is not None else "unknown offering"
    offering_id = offering.id if offering is not None else ""
    who = f"{inbound.from_name.strip()} <{inbound.from_email}>" if inbound.from_name.strip() else inbound.from_email

    facts = [f"Buyer: {who}"]
    if offering is not None:
        facts.append(f"Offering: {offering_id}")
        facts.append(f"Asking: {format_price(offering.sell_price, offering.unit)} FOB {offering.fob_location.strip()}")
    if classification.offer_price is not None:
        if offering is not None:
            facts.append(f"Offer price: {format_price(classification.offer_price, offering.unit)}")
        else:
            facts.append(f"Offer price: ${classification.offer_price:,.2f}")
    if classification.truckloads is not None:
        facts.append(f"Quantity: {_truckloads_phrase(classification.truckloads)}")
    if classification.quantity_units is not None:
        unit_label = f" {offering.unit.value}" if offering is not None else ""
        facts.append(f"Units: {_format_number(classification.quantity_units)}{unit_label}")
    destination = classification.postal_code or classification.destination
    if destination:
        facts.append(f"Destination: {destination}")
    facts.append(f"Classification: firm offer ({classification.source}, confidence {classification.confidence:.2f})")
    if classification.summary.strip():
        facts.append(f"Summary: {classification.summary.strip()}")
    if thread_url.strip():
        facts.append(f"Thread: {thread_url.strip()}")

    quoted_lines = [f"> {line}" for line in inbound.body_text.strip().splitlines()] or ["> (empty message)"]
    original = (
        f"Original message from {inbound.from_email} on {inbound.date.isoformat()} "
        f"(subject: {inbound.subject}):\n" + "\n".join(quoted_lines)
    )

    blocks = [
        f"Firm offer received for the {title}. It has NOT been accepted - please reply to the buyer.",
        "\n".join(facts),
        original,
    ]
    subject = f"FIRM OFFER: {title} - {inbound.from_email}"
    return _rendered(subject, blocks)


def render_digest(
    actions: list[ActionRecord],
    settings: Settings,
    *,
    since: datetime,
    until: datetime,
    pending_drafts: int = 0,
) -> RenderedEmail:
    """Owner digest: actions grouped by kind, escalated offers, drafts awaiting review and errors."""
    window = f"{since.isoformat(timespec='minutes')} to {until.isoformat(timespec='minutes')}"
    mode = "LIVE" if settings.policy.live else "DRY RUN"
    counts = Counter(action.kind for action in actions)

    blocks = [f"Offerings digest [{mode}] for {window}.\n{len(actions)} action(s) recorded."]

    if counts:
        count_lines = ["Actions by kind:"]
        for kind in ActionKind:
            if counts[kind]:
                count_lines.append(f"  {kind.value}: {counts[kind]}")
        blocks.append("\n".join(count_lines))

    offers = [a for a in actions if a.kind is ActionKind.OFFER_ESCALATED]
    offer_lines = [f"Escalated offers ({len(offers)}):"]
    offer_lines += [_action_line(a) for a in offers] or ["  (none)"]
    blocks.append("\n".join(offer_lines))

    drafts = [a for a in actions if a.kind is ActionKind.DRAFT_CREATED]
    draft_lines = [f"Drafts created in this window ({len(drafts)}):"]
    draft_lines += [_action_line(a) for a in drafts] or ["  (none)"]
    draft_lines.append(f"Drafts still awaiting review: {pending_drafts}")
    blocks.append("\n".join(draft_lines))

    errors = [a for a in actions if a.kind is ActionKind.ERROR]
    if errors:
        blocks.append("\n".join([f"Errors ({len(errors)}):", *[_action_line(a) for a in errors]]))

    subject_day = short_date(until) if until.date() == since.date() else f"{short_date(since)} - {short_date(until)}"
    return _rendered(f"Offerings digest {subject_day}", blocks)


def _action_line(action: ActionRecord) -> str:
    """``"  - buyer@example.com - offering-id: details"`` for digest listings."""
    who = action.buyer_email or "(unknown buyer)"
    what = action.offering_id or "(no offering)"
    line = f"  - {who} - {what}"
    if action.details.strip():
        line += f": {action.details.strip()}"
    return line
