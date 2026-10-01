"""Data models shared by every module of the email offerings system.

All timestamps are timezone-aware ``datetime`` objects in UTC. Serialization
helpers (``to_dict`` / ``from_dict``) use ISO-8601 strings so the models can be
stored in SQLite or JSON without a schema library.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utcnow() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _dump_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def make_offering_id(title: str, when: datetime) -> str:
    """Build a stable, URL-safe id such as ``silver-rustic-oak-spc-2026-10-01``."""
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")[:48].strip("-") or "offering"
    return f"{slug}-{when.date().isoformat()}"


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class Unit(str, Enum):
    """Pricing unit. The value is what customers see after the price."""

    SF = "sf"  # square foot
    SY = "sy"  # square yard
    LF = "lf"  # linear foot
    EACH = "ea"
    PALLET = "plt"
    TRUCKLOAD = "TL"

    @classmethod
    def parse(cls, text: str) -> "Unit":
        key = text.strip().lower().lstrip("/").rstrip(".")
        aliases = {
            "sf": cls.SF, "sqft": cls.SF, "sq ft": cls.SF, "sq. ft.": cls.SF, "square foot": cls.SF,
            "sy": cls.SY, "sqyd": cls.SY, "sq yd": cls.SY, "square yard": cls.SY,
            "lf": cls.LF, "lin ft": cls.LF, "linear foot": cls.LF,
            "ea": cls.EACH, "each": cls.EACH, "pc": cls.EACH, "piece": cls.EACH, "unit": cls.EACH,
            "plt": cls.PALLET, "pallet": cls.PALLET, "skid": cls.PALLET,
            "tl": cls.TRUCKLOAD, "truckload": cls.TRUCKLOAD, "truck": cls.TRUCKLOAD, "load": cls.TRUCKLOAD,
        }
        if key not in aliases:
            raise ValueError(f"Unknown unit: {text!r}")
        return aliases[key]


class OfferingStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    SOLD = "sold"
    EXPIRED = "expired"


class BuyerStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    UNSUBSCRIBED = "unsubscribed"
    BOUNCED = "bounced"


class CampaignKind(str, Enum):
    BLAST = "blast"  # To: sender, Bcc: buyer chunk
    PERSONAL = "personal"  # one buyer, "NAME>>Title" subject
    UPDATE = "update"  # re-send of an active offering ("- 10/1 update")
    FOLLOW_UP = "follow_up"
    INTERNAL = "internal"  # cost sheet to the sales team


class CampaignStatus(str, Enum):
    SCHEDULED = "scheduled"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ContactKind(str, Enum):
    """A touch between the system and one buyer about one offering."""

    INITIAL = "initial"
    FOLLOW_UP = "follow_up"
    QUOTE = "quote"
    REPLY = "reply"  # automatic reply other than a quote
    DRAFT = "draft"  # a draft was created for a human to send


class ReplyIntent(str, Enum):
    DELIVERED_PRICE_REQUEST = "delivered_price_request"
    FIRM_OFFER = "firm_offer"
    INTERESTED = "interested"
    QUESTION = "question"
    NOT_INTERESTED = "not_interested"
    UNSUBSCRIBE = "unsubscribe"
    OUT_OF_OFFICE = "out_of_office"
    BOUNCE = "bounce"
    UNKNOWN = "unknown"


class ActionKind(str, Enum):
    BLAST_SENT = "blast_sent"
    PERSONAL_SENT = "personal_sent"
    FOLLOW_UP_SENT = "follow_up_sent"
    QUOTE_SENT = "quote_sent"
    REPLY_SENT = "reply_sent"
    DRAFT_CREATED = "draft_created"
    OFFER_ESCALATED = "offer_escalated"
    BUYER_UNSUBSCRIBED = "buyer_unsubscribed"
    BUYER_BOUNCED = "buyer_bounced"
    BUYER_DECLINED = "buyer_declined"
    IGNORED = "ignored"
    OFFERING_EXPIRED = "offering_expired"
    ERROR = "error"


# --------------------------------------------------------------------------- #
# Core records
# --------------------------------------------------------------------------- #


@dataclass
class Offering:
    """A product deal being offered to buyers.

    Customer-facing fields: ``title``, ``description``, ``fob_location``,
    ``unit``, ``sell_price``, ``quantity_available``, ``make_offers``,
    ``attachments``.

    Internal-only fields that must NEVER be rendered to a customer:
    ``cost_price``, ``suggested_sell_note``, ``internal_notes``.
    """

    id: str
    title: str
    description: str
    fob_location: str
    unit: Unit
    sell_price: float
    quantity_available: str = ""
    units_per_truckload: float | None = None
    make_offers: bool = False
    status: OfferingStatus = OfferingStatus.DRAFT
    created_at: datetime = field(default_factory=utcnow)
    expires_at: datetime | None = None
    cost_price: float | None = None
    suggested_sell_note: str = ""
    internal_notes: str = ""
    tags: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise ValueError("Offering id must not be empty.")
        if not self.title or not self.title.strip():
            raise ValueError("Offering title must not be empty.")
        if not self.fob_location or not self.fob_location.strip():
            raise ValueError("Offering FOB location must not be empty.")
        if self.sell_price < 0:
            raise ValueError("Sell price must be non-negative.")
        if self.cost_price is not None and self.cost_price < 0:
            raise ValueError("Cost price must be non-negative.")
        if self.units_per_truckload is not None and self.units_per_truckload <= 0:
            raise ValueError("Units per truckload must be positive.")
        if isinstance(self.unit, str) and not isinstance(self.unit, Unit):
            self.unit = Unit.parse(self.unit)
        if isinstance(self.status, str) and not isinstance(self.status, OfferingStatus):
            self.status = OfferingStatus(self.status)

    @property
    def is_active(self) -> bool:
        return self.status == OfferingStatus.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["unit"] = self.unit.value
        data["status"] = self.status.value
        data["created_at"] = _dump_dt(self.created_at)
        data["expires_at"] = _dump_dt(self.expires_at)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Offering":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["unit"] = Unit.parse(str(kwargs.get("unit", "ea")))
        kwargs["status"] = OfferingStatus(kwargs.get("status", OfferingStatus.DRAFT.value))
        kwargs["created_at"] = _parse_dt(kwargs.get("created_at")) or utcnow()
        kwargs["expires_at"] = _parse_dt(kwargs.get("expires_at"))
        kwargs["tags"] = list(kwargs.get("tags") or [])
        kwargs["attachments"] = list(kwargs.get("attachments") or [])
        return cls(**kwargs)


@dataclass
class Buyer:
    """A wholesale buyer on the mailing list. ``email`` is the primary key."""

    email: str
    name: str = ""
    company: str = ""
    postal_code: str = ""
    city_state: str = ""
    status: BuyerStatus = BuyerStatus.ACTIVE
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        self.email = self.email.strip().lower()
        if not _EMAIL_RE.match(self.email):
            raise ValueError(f"Invalid buyer email: {self.email!r}")
        if isinstance(self.status, str) and not isinstance(self.status, BuyerStatus):
            self.status = BuyerStatus(self.status)

    @property
    def first_name(self) -> str:
        return self.name.strip().split(" ")[0] if self.name.strip() else ""

    @property
    def destination(self) -> str:
        """Best available destination string for a freight quote ('' if unknown)."""
        if self.postal_code.strip():
            return self.postal_code.strip()
        return self.city_state.strip()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["created_at"] = _dump_dt(self.created_at)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Buyer":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["status"] = BuyerStatus(kwargs.get("status", BuyerStatus.ACTIVE.value))
        kwargs["created_at"] = _parse_dt(kwargs.get("created_at")) or utcnow()
        kwargs["tags"] = list(kwargs.get("tags") or [])
        return cls(**kwargs)


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class Campaign:
    """One scheduled or completed send of an offering to a set of recipients."""

    id: str
    offering_id: str
    kind: CampaignKind
    recipients: list[str]
    subject: str = ""
    personal_note: str = ""
    status: CampaignStatus = CampaignStatus.SCHEDULED
    created_at: datetime = field(default_factory=utcnow)
    sent_at: datetime | None = None
    thread_ids: list[str] = field(default_factory=list)
    error: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.kind, str) and not isinstance(self.kind, CampaignKind):
            self.kind = CampaignKind(self.kind)
        if isinstance(self.status, str) and not isinstance(self.status, CampaignStatus):
            self.status = CampaignStatus(self.status)
        self.recipients = [r.strip().lower() for r in self.recipients if r and r.strip()]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["status"] = self.status.value
        data["created_at"] = _dump_dt(self.created_at)
        data["sent_at"] = _dump_dt(self.sent_at)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Campaign":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["kind"] = CampaignKind(kwargs["kind"])
        kwargs["status"] = CampaignStatus(kwargs.get("status", CampaignStatus.SCHEDULED.value))
        kwargs["created_at"] = _parse_dt(kwargs.get("created_at")) or utcnow()
        kwargs["sent_at"] = _parse_dt(kwargs.get("sent_at"))
        kwargs["recipients"] = list(kwargs.get("recipients") or [])
        kwargs["thread_ids"] = list(kwargs.get("thread_ids") or [])
        return cls(**kwargs)


@dataclass
class Contact:
    """A touch between the system and one buyer about one offering."""

    offering_id: str
    buyer_email: str
    kind: ContactKind
    at: datetime
    thread_id: str = ""
    message_id: str = ""
    campaign_id: str = ""

    def __post_init__(self) -> None:
        self.buyer_email = self.buyer_email.strip().lower()
        if isinstance(self.kind, str) and not isinstance(self.kind, ContactKind):
            self.kind = ContactKind(self.kind)


@dataclass
class ActionRecord:
    """Audit log entry. Everything the engine does is recorded as one of these."""

    kind: ActionKind
    at: datetime
    offering_id: str = ""
    buyer_email: str = ""
    thread_id: str = ""
    message_id: str = ""
    details: str = ""
    id: int | None = None  # assigned by the store

    def __post_init__(self) -> None:
        if isinstance(self.kind, str) and not isinstance(self.kind, ActionKind):
            self.kind = ActionKind(self.kind)
        self.buyer_email = self.buyer_email.strip().lower()


# --------------------------------------------------------------------------- #
# Mail transport records
# --------------------------------------------------------------------------- #


@dataclass
class InboundMessage:
    """A message read from the mailbox, normalised to plain text."""

    message_id: str  # provider id (Gmail message id)
    thread_id: str
    from_email: str
    subject: str
    body_text: str
    date: datetime
    from_name: str = ""
    to: list[str] = field(default_factory=list)
    rfc_message_id: str = ""  # Message-ID header
    in_reply_to: str = ""
    references: str = ""
    headers: dict[str, str] = field(default_factory=dict)  # lower-cased header names
    label_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.from_email = self.from_email.strip().lower()
        self.headers = {k.lower(): v for k, v in self.headers.items()}


@dataclass
class OutboundMessage:
    """A message to send or draft. ``to``/``bcc`` are plain addresses."""

    subject: str
    body_text: str
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    body_html: str | None = None
    reply_to: str = ""
    thread_id: str = ""  # provider thread to attach to (replies)
    in_reply_to: str = ""  # RFC Message-ID being answered
    references: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    attachments: list[str] = field(default_factory=list)  # file paths

    def __post_init__(self) -> None:
        self.to = [a.strip().lower() for a in self.to if a and a.strip()]
        self.cc = [a.strip().lower() for a in self.cc if a and a.strip()]
        self.bcc = [a.strip().lower() for a in self.bcc if a and a.strip()]
        if not (self.to or self.cc or self.bcc):
            raise ValueError("Outbound message needs at least one recipient.")
        if not self.subject.strip():
            raise ValueError("Outbound message needs a subject.")

    @property
    def all_recipients(self) -> list[str]:
        return [*self.to, *self.cc, *self.bcc]


@dataclass
class SendResult:
    message_id: str
    thread_id: str
    draft_id: str = ""
    dry_run: bool = False


# --------------------------------------------------------------------------- #
# Derived records
# --------------------------------------------------------------------------- #


@dataclass
class Classification:
    """Result of classifying one inbound reply."""

    intent: ReplyIntent
    confidence: float = 1.0
    postal_code: str | None = None
    destination: str | None = None  # "Oklahoma City, OK" when no ZIP was given
    truckloads: float | None = None
    quantity_units: float | None = None
    offer_price: float | None = None
    summary: str = ""
    source: str = "rules"  # "rules" | "claude"

    def __post_init__(self) -> None:
        if isinstance(self.intent, str) and not isinstance(self.intent, ReplyIntent):
            self.intent = ReplyIntent(self.intent)
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    @property
    def has_destination(self) -> bool:
        return bool(self.postal_code or self.destination)


@dataclass
class DeliveredQuote:
    """Delivered (FOB + freight) pricing for an offering to one destination."""

    offering_id: str
    origin: str
    destination: str
    distance_miles: float
    rate_per_mile: float
    truckloads: float
    freight_per_truckload: float
    freight_total: float
    unit: Unit
    fob_price_per_unit: float
    delivered_price_per_unit: float
    units_per_truckload: float | None = None
    units_total: float | None = None
    delivered_total: float | None = None
    quoted_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if self.distance_miles < 0:
            raise ValueError("Distance must be non-negative.")
        if self.truckloads <= 0:
            raise ValueError("Truckloads must be positive.")
        if self.freight_per_truckload < 0 or self.freight_total < 0:
            raise ValueError("Freight must be non-negative.")
        if self.delivered_price_per_unit < self.fob_price_per_unit:
            raise ValueError("Delivered price cannot be below FOB price.")


@dataclass
class RunReport:
    """Counts from one ``Engine.run_once`` pass."""

    started_at: datetime
    finished_at: datetime | None = None
    live: bool = False
    campaigns_sent: int = 0
    messages_sent: int = 0
    drafts_created: int = 0
    replies_processed: int = 0
    quotes_sent: int = 0
    offers_escalated: int = 0
    follow_ups_sent: int = 0
    buyers_suppressed: int = 0
    offerings_expired: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        mode = "LIVE" if self.live else "DRY RUN"
        return (
            f"[{mode}] campaigns={self.campaigns_sent} sent={self.messages_sent} "
            f"drafts={self.drafts_created} replies={self.replies_processed} "
            f"quotes={self.quotes_sent} offers={self.offers_escalated} "
            f"follow_ups={self.follow_ups_sent} suppressed={self.buyers_suppressed} "
            f"expired={self.offerings_expired} skipped={self.skipped} errors={len(self.errors)}"
        )
